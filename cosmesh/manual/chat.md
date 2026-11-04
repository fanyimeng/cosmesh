# 交互摘要与对话串

请求、回应、承接、进展和交付由提交方个人 Agent 写简洁的交互摘要：说明做了什么、结果如何、还缺什么。两三句话是简洁程度的建议，可以按事情稍长。摘要与正文属于同一条版本化记录，沿用作者、请求关联、贡献信息与服务器同步，不新增 IM 消息实体或公共模型。

## 写入摘要

```sh
cosmesh thread request THREAD_ID --to REAL_NAME --body '必要的详细需求' \
  --interaction-summary '请补齐说明与报告，保留固定版本引用。代码提供仓库和 commit。' --sync --ready
cosmesh thread reply REQUEST_ID --file detail.md \
  --interaction-summary '报告已入库，引用已补。代码仓库仍待补充。' --sync --ready
```

也可 --interaction-summary-file FILE。摘要以普通正文中的“交互摘要”部分保存，详细正文保留在“正文”部分；同一提交方的同一记录保存两者。服务端接受后其他客户端即可读取，旧 Hub 无需新协议。生成工具与模型已知时 --generation-file 如实登记；不附完整 Agent 聊天。旧记录不改写，缺摘要显示“旧记录摘录”，原文与固定版本继续可读。

## 按人查看最近五轮

```sh
cosmesh thread chat --with REAL_NAME --json
cosmesh thread chat --with REAL_NAME --require-fresh --json
cosmesh thread chat --with REAL_NAME --before NEXT_CURSOR --json
cosmesh thread chat --with REAL_NAME --date YYYY-MM-DD --json
```

--with 接受唯一实名、账号或稳定用户 ID，只读取双方在共享 Thread 中实际关联的请求与回应。默认五轮，--rounds 1–20 可调；与五条记录区分。全局按时间排序，同话题、同一人的连续补充合并为一次发言，随后对方接话构成一轮；切换话题另起一轮，单方轮次保留，不制造回应。parts 保留每条原记录、固定版本和更正入口。业务上的待处理、已回应、已承接、已交付和已采用分别显示，单方轮次不代表请求未回应或未交付。

每条显示实名、本人设置时区的时间、Thread 短名和状态。--date 使用该时区的日历日期。公开进展用 --include-progress 明确纳入，并标“共享进展”，不显示成发给自己的私聊。

next_cursor 固定到本次派生视图，向前读取只返回更早轮次，不重复刚才部分；同一日期／进展范围继续使用同一游标。新消息不混入已展开的旧窗口。重新不带游标读取查看最新，--require-fresh 同次拉取一次。缓存失效明确 VIEW_EXPIRED，可重新查看最近五轮。视图和游标只在私人临时缓存，不是新的权威消息或永久会话。

## Thread、终端和聊天界面

```sh
cosmesh thread chat THREAD_ID --json
cosmesh thread chat REQUEST_ID --json
cosmesh thread chat --with REAL_NAME
cosmesh thread chat --with REAL_NAME --tui
cosmesh thread chat --with REAL_NAME --plain
```

指定 Thread 默认最近30条交互，可 --offset/--limit 查历史；指定请求只读该请求与实际回应。原文按消息 ID 通过 thread show/context 展开，材料按固定引用 library open。摘要视图不下载附件或校验原件。

实际终端默认 TUI：🔵 他人在左，🔴 自己在右，中文按显示宽度换行。↑↓滚动，j/k选择消息，Enter读取该条或合并补充的正文，p读更早五轮，r明确刷新，q退出。--plain 输出相同的左右排版，适合日志或聊天窗口内的纯文本块；--json 给 Skill 有界摘要和固定来源，每段文字只返回一份。聊天中直接展示这些摘要、保留作者与顺序，已有概括够用就结束，不重新读全文再写一份。

总览仍用 thread page --ready；合并查看请求及资料登记状态可用 thread check；收件箱用 thread inbox --ready。相关参数见 guide show pages。

## 高频操作的预存与抓取

个人总览的完整回执、按人最近五轮对话在内容更新后准备，查询直接读成品。写入／拉取后由最长45秒的有限后台程序局部更新；请求命中不加载工作台，不读完整记录或附件。更新未完成时可显示上次成品并标“正在更新”，等待用户进一步命令。

启用新版 Hub 后，共享内容更新会触发服务器公共对话成品准备。按人查询 --require-fresh 一次 RPC 读取预存 JSON，无 Git fetch、知识库原件或 Agent 额外往返；客户端私人缓存取回结果，下次普通查看直接读取。服务器只准备已接受的共享内容，私人 Base 总览与项目／会话绑定留在本机。服务器 SQLite 在机器本地，成品 JSON/文本在 Hub 的派生 views 目录，权威来源仍是原有记录和固定版本。

首次缓存、旧 Hub 或显式详细操作保留有界回退。服务器准备中如实返回更新标记，不自动抓完整仓库来补满答案。个人 Agent 收到请求立即选择短入口、展示够用成品，等用户下一步命令；不顺手打开文件、查全量回链或重复验证成功写入。
