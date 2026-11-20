---
name: cosmesh
description: 查找与接续本人的 Thread、收集项目变化、向指定联系人投递资料并处理收件。
---

## 快速接续

已知 Thread ID 或本机唯一名称，直接 `cosmesh thread continue ID --ready --json`。刚从另一设备回来、需要最新进展时，已开启个人同步的 Thread 加上 `--require-fresh`。这一次读取给出目标、交接摘要、最近进展和本机登记位置。能据此工作就开始。需要精确原文时按回执中的来源用 `thread read ID@VERSION --repo REPO`；需要其他设备或完整位置时用 `thread locations ID`。

只知道当前目录或真实项目 ID 时用 `thread locate --workdir DIR` 或 `--project-id ID`。只知道工作名、或新设备还没有这项工作，用 `thread find 名称`；明确只找本机可加 `--offline`，要求 Hub 最新可加 `--require-fresh`。恢复用 `thread fetch ID --workdir DIR --agent AGENT --files`，沿用原 ID。回执分别给出记录、文件与位置的结果。项目绑定需要真实原生 ID；目录登记用 `--kind directory`。DSH 是否已创建会话以宿主回执为准。

## 工作中

本人选择同步用 `thread personal ID --files`。要持续收集新的项目文件，在对应设备上登记 `collect add ID DIR --personal`，多个目录使用不同的 `--as PREFIX`。`collect once` 对本机所有登记范围检查一次，`collect watch` 可持续运行。在任务停顿、换人或换设备时，用 `collect summary ID --event STABLE_ID --body TEXT` 留下决定、已完成事项和下一步。宿主维护交接文件时，登记时可加 `--context-file RELATIVE_PATH`。磁盘扫描只覆盖已登记的文件。同一阶段的多次保存合并处理。用 `thread personal ID --off` 暂停个人同步。

## 给人发、收和归档

`thread send [ID] --to ACCOUNT --body TEXT` 可重复指定 `--record ID` 和 `--attach FILE`。按用户要求选取内容和收件人。需要审阅时使用 `--dry-run`，随后带 `--expect PREVIEW` 发送；网络重试复用同一操作键或预览。成功回执已确认投递。

收消息用 `thread inbox --json`，只看未读可加 `--unread`。列表已有摘录和关联建议，确需全文时用 `thread mail show MID`，多个已读标记可以一次执行 `thread mail mark MID1 MID2 --seen`。用户选定后用 `thread mail file MID --thread ID` 或 `--defer`。`thread reply MID --body TEXT` 沿原往来回复，按人查询用 `thread chat --with ACCOUNT`，按工作查询用 `thread activity ID`。附件按需下载，发送人可撤回后续在线读取。
