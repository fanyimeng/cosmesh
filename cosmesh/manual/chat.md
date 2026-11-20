## 分享、收件和归属

从普通消息或工作发起。`thread send [ID] --to ACCOUNT --body TEXT`，可重复 --record RECORD 和 --attach FILE，仅发送实际选中内容。Agent 可以先整理文字。使用 --dry-run 检查收件人、正文、附件；用 --expect PREVIEW 固定预览后发送，相同操作键用于网络重试。自记开发意见使用 thread note ID --body TEXT。各收件人分开选择内容时分别投递。

`thread inbox` 获取最新来信和本人工作关联建议；`thread chat --with ACCOUNT` 看按人往来；`thread activity ID` 看该工作相关投递。`thread mail show MID` 看原文。根据用户选择运行 `thread mail file MID --thread ID` 或 `--defer`；保留原信。明确回复用 `thread reply MID --body TEXT`。针对原信的回复沿既有归属，普通来信不自动建工作。附件按需 `thread mail download MID --name FILE --output NEW_PATH`。发送人可以 `thread mail revoke MID` 撤销后续在线查看。读列表不标已读，按用户意愿用 mail mark。
