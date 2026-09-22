# Claude × Clash Verge

为已有 Clash Verge Rev 订阅增加 Claude 专用路由、DNS 策略、可审阅的部署计划和可恢复备份。提供一个本地安装入口，以及可复制给 Agent 的安装提示词。

当前适配范围是 **macOS + Clash Verge Rev 2.5.2 + Mihomo 1.19.29**。其他版本和 Windows/Linux 未经验证，安装器会检查版本。项目不安装 Clash Verge、不提供代理订阅，也不需要提交订阅地址、节点密码或 Claude 凭据。

## 配置如何工作

```text
Claude 域名请求 → Claude 专用代理组 → 你已有订阅中的指定物理节点
```

- Claude 组引用现有节点的**准确名称**，与通用 JMS 组之后的选择分开。
- 同名节点的连接参数随订阅更新；节点改名或删除后，Claude 组只保留 `REJECT`，不会自动选择新出口。
- 路由依据目标域名。Desktop、Claude Code/TUI、网页请求只要进入 Mihomo、处于规则模式且命中规则，就使用 Claude 组；外部 MCP 或第三方网站仍按各自规则处理。
- 以当前有效 DNS 为基线，叠加各目标订阅自己的 Merge DNS，加入 Claude 专用 DoH 策略。`respect-rules` 等其他字段保留；备用订阅没有显式覆盖的 DNS 字段会继承该基线。保留其他业务规则；已有 tailnet IPv4 直连规则补上 `no-resolve`。
- 安装器管理所选订阅的 Merge/Script 扩展，经 App 正常重载生成配置；不直接修改生成的运行配置，不改变 TUN、系统代理、时区或节点传输参数。

节点名称固定不代表供应商出口 IP 固定。本项目不承诺降低封号概率，也不能判断账号资格或封禁原因。参见[官方政策研究](docs/research/anthropic-account-policy.md)。

## 手动一键配置

准备好正在运行的上述版本 Clash Verge Rev，导入并启用你的订阅，先在 `JMS` 组选择要沿用的物理节点。保持规则模式，DNS 需已启用并已配置 `proxy-server-nameserver`，App 所需 geosite 数据需已下载。安装器不会替你猜测节点地址的解析方式。需要本机已有 **Python 3.9+、Node.js 18+、Git**。

先下载并查看源码：

```sh
git clone https://github.com/eric1hua/claude-clashverge-setup.git
cd claude-clashverge-setup
```

查看 [install.sh](install.sh) 和[操作手册](docs/operations/runbook.md) 后执行：

```sh
./install.sh
```

脚本在项目的私有目录创建 Python 虚拟环境并安装固定版本 `PyYAML==6.0.3`，然后发现当前订阅和当前节点、生成计划、执行核心校验。确认计划后，备份并部署扩展，正常退出并重开 Clash Verge，再检查生效状态和匿名网络传输。**重载会短暂中断经过 Clash 的连接。** 不需要 `sudo`，不会自动安装 Homebrew 或系统依赖。

常用选项：

| 命令 | 用途 |
| --- | --- |
| `./install.sh --dry-run` | 生成私有候选并做核心校验；不部署、不重载。首次运行仍会准备私有依赖环境。 |
| `./install.sh --yes` | 已明确授权时跳过最后的交互确认，仍执行校验、备份、部署和验收。 |
| `./install.sh --profile '订阅名称'` | 指定现有订阅；可重复传入以管理多个订阅。 |
| `./install.sh --source-group '代理组名称'` | 首次选节点时使用的来源组，默认 `JMS`。 |
| `./install.sh --node '准确节点名称'` | 显式指定各目标订阅都已存在的物理节点。 |
| `./install.sh --adopt-existing-script` | 审阅后允许替换所选订阅的未知自定义脚本；原脚本会备份。 |

默认仅管理当前活动订阅；显式指定多个订阅时必须包含当前活动订阅。新导入的订阅不会自动加入。重复运行会沿用已保存的节点选择；切换通用 JMS 节点不等于切换 Claude 节点。未知自定义脚本会阻止默认安装，请先审阅它承担的功能，不能把 `--adopt-existing-script` 当成通用报错修复选项。

## 让 Agent 安装

复制 [Agent 安装提示词](docs/agent-install-prompt.md) 的完整代码块给有本机终端访问权限的 Agent。提示词要求先审查现有环境，再部署、重载、验收，并区分配置检查、匿名连通和真实账号使用。

## 项目文件

| 位置 | 用途 |
| --- | --- |
| [install.sh](install.sh) | 手动及 Agent 共用入口 |
| [config/overrides/](config/overrides/) | 不含账号信息的路由源码；DNS 策略由部署工具生成 |
| [scripts/](scripts/) / [tests/](tests/) | 部署、验证、回滚及测试 |
| [操作手册](docs/operations/runbook.md) | 日常使用、订阅更新、问题处理与回滚 |
| [研究资料](docs/research/) | 官方政策、指定版本生命周期及边界 |
| [决策记录](docs/decisions.md) / [变更记录](docs/changelog.md) | 设计依据与版本变更 |
| [PROGRESS.md](PROGRESS.md) | 发布验收范围与待验证事项 |
| [备份说明](backups/README.md) | 本地快照及恢复流程 |
| `.local/`、`backups/` 中的快照与索引 | 本机私有内容，不纳入 Git |

源码测试和核心语法通过不能证明 Desktop/TUI/Web 已登录，也不代表 DNS、IPv6、UDP 全链路已审计。用户自己的真实使用验收须在安装后完成；匿名 `401` 或 Cloudflare `403` 不等于账号被封。维护和贡献请先阅读 [AGENTS.md](AGENTS.md)。
