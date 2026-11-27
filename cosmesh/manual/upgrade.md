# CosMesh 0.7.1｜新会话接着做，阶段成果记回原 Thread

本次更新会为本机已绑定的项目补充 `AGENTS.md` 接续说明，以后新绑定目录也会补上。新开 Agent 会话时，直接说“接着做这个项目”，Agent 会先读相应 Thread，完成有意义的阶段工作后记下结果和下一步。已有项目约定继续生效。同一目录关联多项工作时，Agent 按当前任务选 Thread，有歧义再请你指定。

- **已有工作。** 请 Agent 核对升级回执。用 `cosmesh thread project-guidance --dry-run` 查看本机项目说明，`cosmesh thread project-guidance` 可补做或重试。使用旧离线入口升级后，启动新版 CLI 并用 `self status` 核实安装。
- **第一次使用。** 请 Agent 用 `cosmesh thread init` 初始化，再用 `cosmesh thread create --title "项目名"` 建工作。已有 Thread 可用 `cosmesh thread find "项目名"` 查找；绑定目录后从 `cosmesh thread continue ID --ready` 接续。
- **发送与收件。** 从自己的 Thread 发信时可选择共享整项工作或仅发这条消息。指定后分别使用 `--share-workspace` 或 `--message-only`。`cosmesh thread inbox` 看来信，`cosmesh thread mail open MID --records` 看获准的进展，加 `--at-send` 看发送时版本。

重看本提示用 `cosmesh guide show upgrade`，复制安装或升级请求用 `cosmesh guide show install`。更新托管 Skill 后，新开 Agent 会话加载新版。
