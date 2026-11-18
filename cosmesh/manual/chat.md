# 消息、按人收录与检索

消息正文是发送者本人 Thread 中的普通版本化记录。Hub 保存固定记录引用、投递回执与各接收者的查看状态；回应写入回应者自己的 Thread。发送前校验接收者对来源 Thread 的当前查看权限，消息不会自动改权限。

```sh
cosmesh thread send OWN_THREAD --to USER --body '内容' --json
cosmesh thread request OWN_THREAD --to USER --body '请求' --json
cosmesh thread reply MESSAGE_ID --thread OWN_THREAD --body '回应' --json
cosmesh thread inbox --json
cosmesh thread outbox --json
cosmesh thread chat --with USER --plain
cosmesh thread mail show MESSAGE_ID --offset 0 --chars 8000 --json
cosmesh thread mail mark MESSAGE_ID --seen --json
cosmesh thread search '关键词' --messages --with USER --json
```

send 支持重复 --to、--record RECORD_ID 选已有记录、--kind result 交付成果说明。仅选定记录进入投递，不顺带公开其他私人草稿。收件箱默认消息；--view requests 只筛请求，--unread 只筛未读，--before 使用返回的固定窗口游标。列表、搜索和展开原文都不标已读，明确 mark --seen/--unseen 修改本人标记。已接受与已查看分开；对方撤权后不再提供正文或索引命中。

首次读取新的有效来信时，自动复用按 Hub／用户 ID 登记的本人私人 Thread；没有登记且名称无冲突时，以发信人实名建立。第二设备恢复同一 ID。已有同名但未确认身份的 Thread 时提示选择，不自动合并，可用 thread track USER --use ID 明确关联。新入口只恢复本人收信 Thread，不下载发信人的完整 Thread。收录只追加消息固定引用，个人笔记保留；按人工作清单继续排除对方全员可见 Thread。全员可见 Thread 中明确发给本人的消息仍进收件箱。

--local-only 或 --offline 保存草稿。随后以相同输入发送，复用同一记录；不确定是否投递成功时同输入重试，保持操作键，Hub 不重复投递。确需再次发送相同内容时提供新的 --idempotency-key。离线回复可用 send OWN_THREAD --to USER --reply-to MESSAGE_ID --local-only。当前收件箱和搜索需要联网核验权限，离线可读本人已保存资料。私人物品和草稿不因收信自动公开。

thread search QUERY 搜索当前有权查看的 Thread 内容；加 --messages 搜索自己的收发消息。结果带 Thread、记录、固定版本、快照及摘要位置。消息用 mail show 读取；Thread 用 browse THREAD --snapshot REVISION --record RECORD --version VERSION --offset OFFSET 按需展开。全文索引首次查询按需建立，后续只更新变化记录，未变化版本复用；当前权限始终先于命中结果。

thread browse THREAD --changes PREVIOUS_REVISION 返回变化的记录、文件清单和新 revision。后续页同时传 --snapshot REVISION 与 next_offset，固定同一增量窗口；完成后再保存新的 revision。无变化返回 unchanged，不重复传正文或文件。正文按固定版本读取，原件明确选择才下载。个人同步沿用内容摘要去重、三方合并和历史保护；元数据快照仍整体提交，文件分块传输并复用已接受对象。

历史消息迁入时保留旧消息 ID、固定来源与显式查看标记，不把旧消息当新提醒或批量建人名 Thread。未曾明确查看的历史消息保持未知查看状态。旧多人追加、分配、采用等投递入口退役；普通结果记录和历史来源保留。软件回退不删除新消息账本与索引。
