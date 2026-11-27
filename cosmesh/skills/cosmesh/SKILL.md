---
name: cosmesh
description: 查找与接续本人的 Thread、收集项目变化、向指定联系人投递资料并处理收件。
---

用户说“安装／升级 CosMesh”时，检查现有程序位置和软件源，按当前平台继续。首次安装、服务器引导和 Windows 离线包的取包方式见 `cosmesh guide show install`，公开源码中的教程在 `cosmesh/manual/install.md`。首次安装或升级成功后，核验已安装的 CLI 和托管 Skill，向用户展示回执中的 `update_notice.document`。配置了稳定渠道的客户端用 `cosmesh client update --json` 升级，Windows 离线包用 `cosmesh client update --release DIR --json`。同版检查直接报告版本。用户随时可用 `cosmesh guide show upgrade` 重看简明说明。

## 快速接续

已知 Thread ID 或本机唯一名称，直接 `cosmesh thread continue ID --ready --json`。刚从另一设备回来、需要最新进展时，已开启个人同步的 Thread 加上 `--require-fresh`。这一次读取给出目标、交接摘要、最近进展和本机登记位置。能据此工作就开始。需要精确原文时按回执中的来源用 `thread read ID@VERSION --repo REPO`；需要其他设备或完整位置时用 `thread locations ID`。

只知道当前目录或真实项目 ID 时用 `thread locate --workdir DIR` 或 `--project-id ID`。只知道工作名、或新设备还没有这项工作，用 `thread find 名称`；明确只找本机可加 `--offline`，要求 Hub 最新可加 `--require-fresh`。恢复用 `thread fetch ID --workdir DIR --agent AGENT --files`，沿用原 ID。回执分别给出记录、文件与位置的结果。项目绑定需要真实原生 ID；目录登记用 `--kind directory`。DSH 是否已创建会话以宿主回执为准。

## 已绑定项目的工作约定

实质性工作开始时，按项目 `AGENTS.md` 读取本次工作的 Thread。用 `cosmesh thread locate --workdir DIR --bound-projects --json` 核对正式绑定，`DIR` 取这份项目指引所在的目录。目录绑定多项工作时按任务和既有项目约定选择；仍有歧义就问用户。出现有意义的进展或收尾时，将结论、证据入口、未决事项和下一步简记到选定 Thread。原有本地文档、资料范围、同步与分享规则一并遵循。安装或升级检查本机的既有绑定，新的本地目录绑定也会补充说明。用 `cosmesh thread project-guidance --dry-run` 检查，`cosmesh thread project-guidance` 重试；向用户报告未处理的路径及原因。

## 工作中

本人选择同步用 `thread personal ID --files --brief --json`。接续时的 fetch、日常的 sync 也可加 `--brief --json`。收到 `ok: true` 且 `complete: true` 就继续工作；部分完成时处理 issues，完整机器回执可省略 `--brief` 取得。要持续收集新的项目文件，在对应设备上登记 `collect add ID DIR --personal`，多个目录使用不同的 `--as PREFIX`。`collect once` 对本机所有登记范围检查一次，`collect watch` 可持续运行。在任务停顿、换人或换设备时，用 `collect summary ID --event STABLE_ID --body TEXT` 留下决定、已完成事项和下一步。宿主维护交接文件时，登记时可加 `--context-file RELATIVE_PATH`。磁盘扫描只覆盖已登记的文件。同一阶段的多次保存合并处理。用 `thread personal ID --off` 暂停个人同步。

## 给自己另一台设备派活

用户指定“让某台机器上的 DSH 或 Codex 做某事”时，在当前个人同步 Thread 用 `thread task send ID --device HOST --agent dsh --body TEXT --idempotency-key KEY --json`。每次明确的新委派生成一个键，网络重试复用原键。已知唯一目标直接发送；有歧义时 `thread task targets ID` 查绑定，再加 `--session NATIVE_ID`。成功返回排队回执就继续手头工作。查询用 `thread task list ID`，完成回执指向原 Thread 的结果记录。只有用户明确打开的接收器会启动原生回合，配置见 `cosmesh guide show dsh`。

## 给人发、收和归档

从自己的 Thread 发消息或回复，先明确收件人和来源工作。用户已明确「共享这项工作的完整内容和后续进展」时，可用 `--share-workspace`；明确仅发消息时用 `--message-only`。仅说「发消息」且有来源 Thread 时，用 `thread mail sharing ID --to ACCOUNT --json` 核对。对新增收件人，用自然语言问「是否让 [收件人] 持续查看 [工作名] 的记录、历史、已登记文件和后续进展？也可以只发这条消息。」等用户选择后再发送。对已有授权的收件人可复用现有权限。缺少来源时，普通消息可直接投递；需要共享则先选本人的工作。选定后使用 `thread send ID --to ACCOUNT --body TEXT` 加上相应选项，`--record ID` 和 `--attach FILE` 可重复。审阅时加 `--dry-run`，发送时保留相同选项并加 `--expect PREVIEW`。重试沿用同一操作键或预览。普通旧 Hub 可仅发消息；用户选择共享时应报告所需能力。

收消息用 `thread inbox --json`，只看未读可加 `--unread`。列表已有摘录和关联建议，确需全文时用 `thread mail show MID`，多个已读标记可以一次执行 `thread mail mark MID1 MID2 --seen`。用户选定后用 `thread mail file MID --thread ID` 或 `--defer`。`thread reply MID --body TEXT` 沿原往来回复，按人查询用 `thread chat --with ACCOUNT`，按工作查询用 `thread activity ID`。附件按需下载，发送人可撤回后续在线读取。

来信带 workspace 时，用 `thread mail open MID` 看最新定义，`--records` 看工作进展，`--at-send` 固定到发件时。文件先 `--files`，用户需要落盘时用 `--download ASSET_ID --output NEW_PATH`。所有者撤权用 `thread mail unshare ID --from ACCOUNT`。共享进展从 `collect status --json` 查状态，`collect once --json` 立即补同步。
