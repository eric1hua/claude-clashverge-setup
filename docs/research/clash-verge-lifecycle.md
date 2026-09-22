# Clash Verge 配置生命周期与实现依据

- 核查日期：2026-09-22
- 目标版本：Clash Verge Rev 2.5.2；Mihomo 1.19.29
- 研究范围：官方文档与对应版本源码。运行时验收结果另存于 `diagnostics/`，本文不代替本机验证。
- 项目决策：对明确选择的订阅使用同一份项目源码生成**订阅扩展脚本**；保留 App 控制面与 TUN；保留主订阅通用 DNS 基线，并为 Claude 的七个专属后缀增加独立解析策略。

## 1. 输入、生成物与合并顺序

Verge 的订阅、Merge 和 Script 是持久输入；`clash-verge.yaml` 是生成物。每次增强配置时，扩展链重新读取 `profiles/` 下相应文件。因此持久改动应落在已关联的扩展文件，不能只修改生成物。[扩展读取实现][chain]

2.5.2 的执行顺序为：

1. 读取当前订阅，应用订阅规则、节点及组的顺序扩展。
2. 合并 App 基础设置，执行内置处理、TUN 与 DNS 设置。
3. 全局 Merge → 全局 Script → 订阅 Merge → 订阅 Script。
4. 恢复 App 权威控制字段，随后进行清理、排序等最终处理。

权威字段包括控制 API、Unix socket、secret、监听端口、mode、allow-lan、log-level、ipv6、unified-delay。扩展不是这些选项的持久配置入口。[2.5.2 增强实现][enhance]

当前在线文档还描述了 TUN 字段的 GUI 优先级，但 2.5.2 源码中的 `CONTROL_PLANE_KEYS` 不含 `tun`。文档会随版本更新，涉及 TUN 时必须检查实际生成配置，不能直接套用最新说明。[在线扩展文档][extend]

本项目选择订阅扩展脚本，因为它位于全局扩展和订阅 Merge 之后，避免后续已有订阅脚本再次覆盖项目规则。所选订阅分别关联部署目标，由同一份受管理源码生成；备份应保留原文件和关联关系。未来新导入的订阅需单独纳入管理，不能声称会自动受保护。

DNS 调整通过所选订阅各自的 Merge 持久保存，保留该订阅有效 DNS 配置；在此基础上，仅对 `anthropic.com`、`claude.ai`、`claude.com`、`clau.de`、`claudemcpclient.com`、`claudemcpcontent.com`、`claudeusercontent.com` 的域及子域增加 `nameserver-policy`。它们使用现有解析服务商的 IP 形式 DoH 地址 `https://1.1.1.1/dns-query#Claude` 和 `https://8.8.8.8/dns-query#Claude`，使这些查询明确经过 Claude 专用组。

`nameserver-policy` 优先于通用 nameserver/fallback；`#Claude` 是 DNS 连接指定代理的官方语法。保留现有 bootstrap 与 `proxy-server-nameserver`，避免为解析代理自身地址再次依赖该代理。七个后缀之外的域名仍遵循基线策略；这不是对全部应用 DNS、IPv6 或未进入 Mihomo 的流量作捕获保证。[DNS 官方配置][dns-doc]

## 2. 固定节点与失败行为

Mihomo `select` 选择的是节点名称；若已保存名称不在当前组中，它会取组内第一个节点。名称固定也不保证代理供应商的实际出口 IP 固定。[Selector 实现][selector]

本项目的 Claude 专用组只允许一个**准确匹配、已确认的现有节点**。该节点缺失时，脚本仍应生成合法配置，将组成员设为 `REJECT` 并保留 Claude 域名优先规则。不得静默选择另一个同国家节点，不得加入 `DIRECT`、自动测速、故障转移或负载均衡备用。

不要用 JavaScript `throw` 代替上述阻断行为。Verge 对扩展脚本异常仅记录错误，保留脚本执行前的配置并继续处理；抛错可能令项目规则整体缺席。[异常处理实现][enhance]

该阻断边界仅适用于规则模式下、实际进入 Mihomo 且命中所维护域名规则的流量。关闭代理、切换全局直连、应用绕过代理、规则未覆盖的新域名，以及上游改变节点出口，均不受单节点组约束。

## 3. 正式重载与持久性验收

App 的 `enhance_profiles` 命令和重新激活订阅都进入官方配置生成、校验与应用流程。订阅更新也会重新生成当前配置。[配置命令][profile-command]、[订阅更新实现][profile-feature]

在 2.5.2 中，编辑器保存扩展后是否立即应用，取决于该项是否被判定影响当前配置。因此外部写文件后，应显式通过 App 重新生成，而不能仅凭文件保存成功认定生效。[保存实现][save-profile]

本机的 `127.0.0.1:33331` 是 Verge 单实例/PAC 服务。源码只注册 `/commands/visible`、`/commands/pac`、`/commands/scheme`，没有重载配置接口。`clash:` / `clash-verge:` 深链只处理订阅导入，也不能替代重载。[本地服务器][embedded-server]、[深链实现][scheme]

GUI 不可用时，可正常退出后重新打开 App，让官方启动流程重新读取持久输入。应验证生成文件、内核 `/rules` 与 `/proxies`，再执行实际网络请求。重启会暂时中断代理连接，应记录发生时间和验收结果。

Mihomo 的 `PUT /configs?force=true` 支持读取 `path` 或 `payload`，只重新加载现成的内核配置，**不执行 Verge 的 Merge/Script 管线**。它不能证明扩展能在 App 重启或订阅更新后保留。指定文件路径还受 Mihomo 安全路径约束。[重载 API 源码][configs-api]

## 4. 控制接口与本机监听边界

`allow-lan` 控制代理入站的局域网访问；DNS、控制 API 是各自独立的监听。验收需分别检查代理端口、DNS 端口、TCP 控制端口及监听地址，而不能只检查一个布尔值。[Mihomo 全局配置][general]

Mihomo 1.19.29 的 Unix socket 控制接口不验证 secret，并在创建后将 socket 权限设为 `0666`。TCP 控制接口的 secret 不保护 Unix socket。App 自己使用此 IPC 路径，因此不能随意移除或改路径；应把它记录为上游本地权限边界，而不是声称已通过 secret 消除风险。[控制服务实现][controller-server]

本项目不增加对外监听、不修改 TCP 控制 API、secret 或 App IPC。含订阅 URL、节点凭据或完整配置的备份必须放入不纳入版本控制的专用目录，限制本机文件访问权限；报告只保留必要的脱敏证据。

## 5. macOS TUN、DNS 与 Tailscale

Mihomo 官方 `strict-route` 文档仅具体说明 Linux 与 Windows 效果；1.19.29 依赖的 sing-tun 0.4.21 Darwin 路由实现也未引用 `StrictRoute`。本次证据不足以支持“打开它即可在 macOS 防泄漏”，因此不为制造差异而调整它。[TUN 文档][tun-doc]、[依赖版本][mihomo-dependencies]、[Darwin 路由实现][darwin-tun]

Verge 2.5.2 在 macOS 启用 TUN 且 DNS 为 fake-ip 时，会异步恢复此前保存的 DNS，再设置系统公共 DNS 为 `114.114.114.114`；关闭 TUN 时调用恢复逻辑。这是 App 行为，不等同于最终 DNS 请求已按预期走代理。本项目保留 TUN 与通用 DNS 基线，仅增加前述分域策略；验收仍应检查实际系统 DNS 与请求路径，避免与 Tailscale DNS 相互覆盖。[Verge TUN 实现][verge-tun]

Tailscale 官方列出的地址范围是 `100.64.0.0/10` 与 `fd7a:115c:a1e0::/48`；若使用子网路由，还需保留实际子网。启用 exit node 的行为不同，不能靠排除上述两段就宣布兼容。路由表、MagicDNS 和所需 tailnet 服务都应按本机实际状态验收。[Tailscale 与其他 VPN][tailscale]

## 6. 本项目验收清单

- 所选订阅均使用受管理的订阅脚本；原脚本和关联关系可以恢复。
- 当前准确节点存在时，Claude 组仅含该节点；节点缺失时仅含 `REJECT`。
- 重复生成不重复添加组和规则，不把其他业务自动改到 Claude 节点。
- 正常重启后，生成配置和内核运行规则仍符合预期。
- 七个专属后缀的 DNS 策略指向 Claude 组；验证解析请求使用该组，并确认通用 DNS 和代理地址解析仍可用。
- 静态脚本测试、Mihomo 配置校验、运行时 API 和真实请求分别记录，不互相替代。
- 未经过实际订阅更新验证时，明确记录“生命周期依据已核查，订阅更新实测尚未执行”。
- 备份集中、权限受限、敏感数据不入库；回滚只恢复本次拥有的目标并重新验证。

[chain]: https://github.com/clash-verge-rev/clash-verge-rev/blob/v2.5.2/src-tauri/src/enhance/chain.rs
[enhance]: https://github.com/clash-verge-rev/clash-verge-rev/blob/v2.5.2/src-tauri/src/enhance/mod.rs
[extend]: https://www.clashverge.dev/guide/extend.html
[selector]: https://github.com/MetaCubeX/mihomo/blob/v1.19.29/adapter/outboundgroup/selector.go
[profile-command]: https://github.com/clash-verge-rev/clash-verge-rev/blob/v2.5.2/src-tauri/src/cmd/profile.rs
[profile-feature]: https://github.com/clash-verge-rev/clash-verge-rev/blob/v2.5.2/src-tauri/src/feat/profile.rs
[save-profile]: https://github.com/clash-verge-rev/clash-verge-rev/blob/v2.5.2/src-tauri/src/cmd/save_profile.rs
[embedded-server]: https://github.com/clash-verge-rev/clash-verge-rev/blob/v2.5.2/src-tauri/src/utils/server.rs
[scheme]: https://github.com/clash-verge-rev/clash-verge-rev/blob/v2.5.2/src-tauri/src/utils/resolve/scheme.rs
[configs-api]: https://github.com/MetaCubeX/mihomo/blob/v1.19.29/hub/route/configs.go
[general]: https://wiki.metacubex.one/config/general/
[dns-doc]: https://wiki.metacubex.one/config/dns/
[controller-server]: https://github.com/MetaCubeX/mihomo/blob/v1.19.29/hub/route/server.go
[tun-doc]: https://wiki.metacubex.one/config/inbound/tun/
[mihomo-dependencies]: https://github.com/MetaCubeX/mihomo/blob/v1.19.29/go.mod
[darwin-tun]: https://github.com/MetaCubeX/sing-tun/blob/v0.4.21/tun_darwin.go
[verge-tun]: https://github.com/clash-verge-rev/clash-verge-rev/blob/v2.5.2/src-tauri/src/enhance/tun.rs
[tailscale]: https://tailscale.com/docs/reference/faq/other-vpns
