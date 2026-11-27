# CosMesh 0.7.1

每项工作用一个 Thread 保存记录和文件。从这个 Thread 发消息时，可让指定收件人持续只读查看整项工作的最新进展，也可只发送本次消息。收件人可回到发送时的版本。

## 请 Agent 安装或升级

在实际工作的设备上，把下面任一段贴给 Codex、OpenCode 或 DSH 即可。

> 请在这台设备上为我安装 CosMesh 和托管 Skill。先检查已有安装。使用我提供的 Hub 邀请或离线包；仅在本机使用时，从 https://github.com/fanyimeng/cosmesh 的稳定版本安装。请核验实际版本和 Skill。

> 请沿用现有安装和软件源，把 CosMesh 和托管 Skill 升级到正式稳定版。核验版本，告诉我哪些已绑定项目已补上接续说明，哪些需要处理。

macOS / Linux 客户端通过维护者提供的安装脚本或匹配平台的固定包安装，直接在 Linux 服务器运行的 Agent 使用该机的软件源引导程序。Windows 11 x64 从维护者软件源的 `stable-windows.json` 取得 ZIP 和校验值，用 64 位 CPython 3.12、Git for Windows 安装，以后通过已安装的 `cosmesh.cmd` 从新解压的包升级。仅使用本机时也可从已发布的 GitHub tag 构建应用。[安装与升级教程](cosmesh/manual/install.md)

0.7.1 起，绑定本机项目目录会在其 `AGENTS.md` 中补上接续约定。安装和跨版本升级也会检查这台设备已有的绑定。Agent 开工先读对应 Thread，有阶段成果时记下结论、依据和下一步。一个目录绑定多个 Thread 时，按当前任务选择，有歧义再问。项目已有的约定继续生效。[新版使用说明](cosmesh/manual/upgrade.md)

## 常用命令

```sh
cosmesh thread find 项目名
cosmesh thread continue THREAD_ID --ready
cosmesh thread note THREAD_ID --body "完成了第一轮检查"
cosmesh thread send THREAD_ID --to ACCOUNT --body "请看最新进展"
cosmesh thread inbox
cosmesh thread mail open MESSAGE_ID --records
cosmesh thread mail open MESSAGE_ID --at-send --files
cosmesh collect status
```

[快速上手](cosmesh/manual/upgrade.md) · [工作区共享和撤权](cosmesh/manual/sharing.md) · [托管 Skill](cosmesh/skills/cosmesh/SKILL.md) · [可选组件](components/README.md)
