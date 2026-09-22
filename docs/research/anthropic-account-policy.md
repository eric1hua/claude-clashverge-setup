# Claude 账号政策与网络配置边界

- 核验日期：2026-09-22
- 证据范围：Anthropic 官方政策、帮助中心与 Claude Code 文档；不把论坛个案或代理商宣传作为封禁原因证据。
- 项目目标：在符合服务地区与账号政策的前提下，提高 Clash Verge 配置的安全性、稳定性和可验证性。配置不能保证账号不被暂停或封禁。

## 适用范围

网络错误、匿名 HTTP 403 或一次出口变化不足以确定账号封禁原因。官方支持地区列表应在使用前核对；代理出口不等于使用者的地区资格。本项目只维护路由与 DNS，不判断使用者位置、不修改时区或客户端指纹、不提供地区停用或风控规避功能。

## 证据与实施边界

| 主题 | 官方事实 | 不能据此确定的事项 | 工程可做的边界 |
| --- | --- | --- | --- |
| 暂停与封禁 | 官方列举重复违反使用政策、从不支持地区创建账号、违反服务条款等原因；受影响组织也可能因异常活动暂停。[1] | 尚不能确定具体账号事件属于哪一项，更不能把某次换 IP 直接判定为原因。 | 保存脱敏时间线、错误类型与配置版本，便于申诉；不能通过连通测试证明账号安全。 |
| 支持地区 | 当前 Claude.ai 与 API 支持列表包括美国，不包括中国大陆。[2] | 本项目不验证实际所在地或账号调查结果。 | 地区使用决策应依据实际情况；不得把境外代理出口当作政策资格证明。 |
| VPN 与代理 | 登录故障排查建议停用 VPN；企业账号 IP 白名单又明确支持组织批准的 VPN 出口。[3][4] | 官方没有在这些页面规定所有 VPN 使用都违规。 | 可排查代理导致的登录故障；不能写成“开代理必封”或“某代理免封”。 |
| 固定或住宅 IP | 本轮官方资料未发现消费者必须使用固定 IP、住宅 IP，或因此免于封禁的规定。 | 未验证固定 IP 能降低账号的封禁风险；节点标签不证明线路或出口属性。 | 如选择稳定出口，只将其记为路由管理决策，不宣称已获得风控收益。 |
| 凭据与官方客户端 | 消费者条款限制账号共享及未获允许的自动访问；Claude Code 允许本人通过官方流程登录未经修改的官方程序。第三方应用不得收集或中介 Claude.ai 凭据、代用户复用订阅凭据。[5][6] | 应按自己的环境核对调用方及凭据用途。 | 使用官方认证流程；自建产品或服务按官方要求采用 API key 或支持的云平台；不导出、复制或代理转售订阅凭据。 |
| 网络完整性 | Claude Code 提供 HTTP(S) 代理配置，不支持 SOCKS 代理。[7] | 域名规则存在不代表本机运行时已命中；HTTP 状态码不等于登录或模型调用成功。 | 检查生效规则、实际连接、TLS 与认证路径，分别报告静态检查、网络测试和真实应用验证。 |

## Clash Verge 配置含义

1. 若本地 mixed/http 监听端口用于 Claude Code，代理地址使用其实际协议，例如 `http://127.0.0.1:端口`；目标网站为 HTTPS 不意味着本地代理监听器支持 HTTPS。
2. Claude 的认证、API、内容与更新并非只访问 `claude.ai`。当前官方清单还包括 `claude.com`、`platform.claude.com`、`api.anthropic.com`、`mcp-proxy.anthropic.com`、`downloads.claude.ai`、`bridge.claudeusercontent.com` 等；其中 `platform.claude.com` 参与 OAuth 交换、刷新和吊销。Web/Desktop 还依赖内容与 CDN 域名。[7]
3. 按实际产品和安装方式维护域名规则；npm、Google Storage、GitHub 等通用依赖不宜全部视为 Claude 专用认证流量。复核文档后再更新规则，不依赖永久不变的域名清单。
4. 保持证书验证，限制本机代理及控制接口的暴露，避免凭据进入配置仓库、日志或备份索引。可设计代理不可用时停止相关流量，但必须验证覆盖范围，不能把规则级阻断称为整机断网保障。
5. 不采用客户端指纹伪装、位置伪装或反检测方案；DNS、路由与出口管理的验收指标是可观察的网络行为，不是推测的封号概率。

## 账号受限时的处理

通过 `claude.ai` 登录受限账号，使用限制页面提供的官方申诉入口；说明真实使用地点、发生时间和错误信息，等待 Safeguards 团队复核。[1] 提交前删除日志中的 token、Cookie、订阅地址及其他秘密。本文不代用户提交申诉，也不把更换网络或重新注册作为恢复账号的验证办法。

## 官方来源

| 编号 | 来源 | 时间说明 |
| --- | --- | --- |
| [1] | [Safeguards warnings and appeals](https://support.claude.com/en/articles/8241253-safeguards-warnings-and-appeals) | 页面日期 2026-07-09；列示原因并非穷尽清单。 |
| [2] | [Supported Regions Policy](https://www.anthropic.com/supported-countries) | 动态列表，以核验日内容为准。 |
| [3] | [Troubleshoot Claude error messages](https://support.claude.com/en/articles/12466728-troubleshoot-claude-error-messages) | 登录故障排查建议，不应扩写为统一封禁规则。 |
| [4] | [Restrict access to Claude with IP allowlisting](https://support.claude.com/en/articles/13200993-restrict-access-to-claude-with-ip-allowlisting) | Enterprise 功能，不代表个人订阅必须固定 IP。 |
| [5] | [Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms) | 本轮页面显示生效日 2025-10-08，返回韩语正文；实际适用条款以账号所在地及官方页面为准。 |
| [6] | [Claude Code: Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use) | 动态文档；应区分官方 CLI 的本人登录与第三方截取或中介凭据。 |
| [7] | [Claude Code: Enterprise network configuration](https://code.claude.com/docs/en/network-config#network-access-requirements) | 动态文档，能力与域名清单须结合本机版本复核。 |

维护要求：政策变化、Claude 产品或安装方式变化，以及出现新的认证或网络故障时复核本文；保留旧结论的日期与修订记录，不把未知原因补写成已证实结论。
