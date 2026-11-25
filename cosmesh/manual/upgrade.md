# CosMesh 0.7.0｜从一封来信查看整项工作的进展

本版新增 Windows 客户端。发信时可让指定收件人持续只读查看来源工作的定义、记录、历史和已登记文件。工作区共享需要收发端和 Hub 都支持 0.7。

1. **开始工作。** 首次运行 `cosmesh thread init`，再用 `cosmesh thread create --title "项目名"` 建一项工作。把下文的 ID 换成返回的 W-ID。已有工作用 `cosmesh thread find "项目名"` 查找，再用 `cosmesh thread continue ID --ready` 接续。
2. **连接和记录。** 离线安装后，使用维护者给的连接信息运行 `cosmesh thread discover --ssh SSH别名 --service 服务文件路径`；首次在 Hub 上发言用 `cosmesh user set-name "姓名"` 登记。用 `cosmesh thread note ID --body "完成了什么，下一步做什么"` 记进展。用 `cosmesh thread personal ID --files` 同步到本人 Hub，另一台设备可用 `cosmesh thread fetch ID --workdir 目录 --agent codex --files` 接续。
3. **发送与授权。** `cosmesh thread send ID --to 账号 --body "请查看进展"` 会在终端提示共享工作区、仅发消息或取消。已授权账号可以复用权限。脚本和 Agent 经确认后用 `--share-workspace` 或 `--message-only`。授权只涉及所选工作；设备和会话绑定归本人。
4. **同步进展。** 共享后，本机写入的记录会合并同步。持续收集项目目录先用 `cosmesh collect add ID 目录 --personal` 登记，在该机运行 `cosmesh collect watch` 发现外部文件变化。`cosmesh collect status` 查看积压，`cosmesh collect once` 在恢复联网后立即补同步。
5. **读取和撤权。** `cosmesh thread inbox` 列出消息；把消息 ID 代入 `cosmesh thread mail open MID --records` 看最新进展，加 `--at-send` 看发送时版本。用 `--files` 列文件，或用 `--download 文件ID --output 新路径` 下载。发件人可用 `cosmesh thread mail unshare ID --from 账号` 撤回在线访问。

重看本说明用 `cosmesh guide show upgrade`，详细用法见 `cosmesh guide show usage`。使用托管 Skill 的用户请新开 Agent 会话。已配置稳定渠道的客户端用 `cosmesh client update` 更新；Windows 用户解压新离线包后运行 `cosmesh client update --release 包目录`。
