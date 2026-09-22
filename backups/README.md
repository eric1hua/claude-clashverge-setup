# 本地备份管理

快照布局：`YYYY-MM-DD/clash-verge-HHMMSS-ffffff/before-change/profiles/原文件名`。

每次实际部署先保存原始字节，在快照 `manifest.json` 中记录恢复目标和修改前后 SHA-256。`backups/manifest.json` 仅作本机索引；历史快照不覆盖。

**只有本说明文件纳入 Git。** 备份和索引可能包含本机路径、节点信息或其他秘密，不要上传、外发或提交。部署工具限制私有文件权限，但共享前仍需另行审查内容。

恢复前保留整个快照及其 manifest。在项目根目录执行：

```sh
.local/venv/bin/python scripts/manage.py rollback --snapshot backups/YYYY-MM-DD/clash-verge-HHMMSS-ffffff
zsh scripts/reload-app.zsh
```

恢复会检查快照校验和、目标路径和目标当前内容。发现后续修改时拒绝覆盖，先审查差异；不要删除保护检查。重载后应按恢复配置重新验证。详细流程见[操作手册](../docs/operations/runbook.md)。

本项目只管理自己创建的快照，不清理 Clash Verge 或其他工具管理的历史备份。恢复完成且确认不再需要之前，不要移除本地备份和私有环境。
