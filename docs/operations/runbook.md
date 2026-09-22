# 安装、维护与恢复

适配 macOS、Clash Verge Rev 2.5.2 / Mihomo 1.19.29。已有订阅必须正常运行并处于规则模式，DNS 已启用且已有 `proxy-server-nameserver`，必需 geosite 数据已由 App 下载；项目不安装代理软件或提供订阅。脚本使用 App 的既有扩展关联，保留正常的订阅刷新流程。

## 首次安装

在仓库目录执行。需要 Python 3.9+、Node.js 18+、Git；首次运行在 `.local/venv/` 创建私有环境，安装 `PyYAML==6.0.3`，需要能够获取该 Python 包。

```sh
./install.sh --dry-run
./install.sh
```

`--dry-run` 可生成私有依赖、候选和计划，但不修改 App 文件、不重载。交互安装会重新生成并检查计划，在部署前确认一次。已授权的 Agent 使用 `./install.sh --yes`。

默认管理当前活动订阅；首次从 `JMS` 组读取实际物理节点。显式指定例子：

```sh
./install.sh --dry-run --profile '主订阅' --profile '备用订阅' --source-group 'JMS' --node '准确节点名称'
./install.sh --profile '主订阅' --profile '备用订阅' --source-group 'JMS' --node '准确节点名称'
```

目标必须已经存在，所选列表必须包含当前活动订阅，准确节点须存在于每个目标订阅。不要填订阅 URL 或密码。先审阅 dry-run，再以相同参数安装；没有必要为相同配置重新导入订阅。

## DNS 变更范围

安装器读取本机当前活动配置的有效 DNS，叠加每个所选订阅自身 Merge 的 DNS 字段，再覆盖七个 Claude 域名后缀的策略，用 `#Claude` 显式指定 DoH 代理组。`respect-rules` 和其余字段沿用上述基线；备用订阅未在自身 Merge 中覆盖的字段会继承当前活动基线。因此，多订阅安装应在计划中检查每个目标的 DNS，不把它理解为完全保留备用订阅独立运行时的历史 DNS。

若 DNS 未启用、某个目标明确关闭 DNS，或缺少有效的 `proxy-server-nameserver`，安装器会停止。先在 Clash Verge 核对实际解析设计，不通过随意添加公共解析器跳过检查。

## 现有脚本保护

默认安装不会静默覆盖未知的订阅脚本。出现该错误时：

1. 在 Clash Verge 或本地私有文件中查看受影响的订阅脚本，识别它管理的规则、节点属性和其他功能。
2. 决定保留、迁移这些功能，或明确接受用本项目脚本替换。
3. 只有接受完整替换后，才在同一组安装参数中增加 `--adopt-existing-script`。原字节会备份，但原逻辑不会自动拼接到新脚本。

不要将这个参数写进无条件重试循环。已有名为 `Claude` 的组或节点也须核对归属和冲突，不通过盲目改名绕过。

## 计划、部署与重载

安装入口调用 `scripts/manage.py` 的准备及部署流程：

- 准备：读取当前版本、订阅、扩展和运行节点；在私有目录生成候选并复制现有 geo 数据，执行 Mihomo 配置校验。缺少必需 geosite 数据时停止，不能在 dry-run 期间向 App 目录自动下载。
- 部署：检查输入与暂存文件是否变化，先保存集中快照，再写入订阅扩展；失败时仅尝试恢复仍属于本次写入的文件。
- 重载：通过 App 正常退出并重开，让官方 Merge/Script 管线重新生成配置，随后验收。

重载会暂时中断经过 Clash 的连接。无法正常退出时脚本停止，不强制杀进程。也可在 GUI 重新激活当前订阅，再运行验收；只调用 Mihomo `PUT /configs` 无法证明 Verge 扩展生命周期生效。

高级用户可分步执行；Python 依赖环境需已准备好。底层订阅参数是复数 `--profiles`，与入口脚本的 `--profile` 不同：

```sh
.local/venv/bin/python scripts/manage.py prepare --profiles '订阅名称'
.local/venv/bin/python scripts/manage.py apply
zsh scripts/reload-app.zsh
.local/venv/bin/python scripts/verify.py
```

重复准备默认保留已保存的节点选择。显式 `--node` 是变更节点意图，应先核对旧选择、目标订阅与新节点；不要删除私有计划来隐式追随通用组。

## 订阅更新与使用范围

Clash Verge 更新订阅并重新加载后，同名节点使用更新的连接参数；Claude 组只引用名称，并未复制一份独立订阅。服务商改名或删节点时，脚本令 Claude 组只含 `REJECT`；新增节点不会被自动选中。订阅名称和节点名称是不同对象。

专用路由按目标域名匹配，须同时满足流量进入 Mihomo 且处于规则模式。它不等于 Claude 应用的全部进程流量隔离；外部 MCP、网页跳转和通用第三方依赖按各自规则处理。新订阅必须显式纳入管理。

本项目保留用户的 TUN、系统代理、监听与 shell 环境。安装成功不代表原来绕过代理的应用已经被接管。Claude Code 使用显式代理时，应根据本机实际 HTTP(S) 监听端口和协议配置；不要套用他人端口或只提供 SOCKS 地址。

## 日常验收

```sh
.local/venv/bin/python scripts/verify.py
.local/venv/bin/python scripts/verify.py --network --output .local/acceptance.json
```

默认检查配置与运行状态。安装入口会在部署后自动运行默认检查及匿名网络检查；上述命令用于之后单独复查。`--network` 额外执行不带 Claude 凭据的网络探测，不登录、不调用模型。报告可包含本机信息，默认保留在私有目录，不直接上传。

将结果分层解释：

| 证据 | 可得出的结论 |
| --- | --- |
| 单元测试 | 源码在所测输入下符合预期 |
| Mihomo `-t` | 候选符合当前核心配置要求 |
| App 重载后运行检查 | 当前活动配置的目标组、规则和策略实际存在 |
| 匿名 HTTP / TLS / 连接记录 | 所测请求的传输行为；不证明账号可用 |
| 用户正常的 Desktop/TUI/Web 会话 | 对应客户端、账号和该次请求的实际体验 |

匿名 `401`、Cloudflare challenge 或 `403` 不能单独确认封号。切换备用订阅、更新远程订阅、升级 App/core 后都应重新验收，尚未实测的步骤明确记录。

## 回滚

查看本地 `backups/manifest.json` 选择快照，使用相对项目根目录的快照路径：

```sh
.local/venv/bin/python scripts/manage.py rollback --snapshot backups/YYYY-MM-DD/clash-verge-HHMMSS-ffffff
zsh scripts/reload-app.zsh
```

回滚前检查快照校验和、恢复目标及文件漂移，拒绝覆盖后续未经审阅的修改。回滚后本项目的新规则检查可能预期失败，应按恢复后的旧配置核验。不要先删除 `.local/` 或备份再尝试恢复。

## 故障处理边界

版本不匹配时复核官方实现；节点缺失时核对订阅与显式选择；文件漂移时重新审阅并准备计划；正式重载失败时检查 App 状态后再处理。不要通过关闭 TLS 校验、添加不认识的配置键、自动换节点或连续重试登录来“修复”验收。

本项目未声称系统级流量全捕获、DNS/IPv6/UDP 全量审计或其他 VPN 的普遍兼容性。网络连通结果也不代表账号政策资格。
