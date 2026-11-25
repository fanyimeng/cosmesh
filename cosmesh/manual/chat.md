## 发消息时选择对方能看什么

从自己的项目 Thread 发信时，可选「共享工作区并发送」「仅发送消息」或取消。前者给本次指定的账号持续只读权限，覆盖这一项工作的定义、当前记录、可用历史和已登记的可共享文件。先核对工作名和收件人。另有新收件人时单独确认。私人设备、会话和本机路径属于本人；写进工作正文的内容随正文共享。仅发消息时，收件人能读正文、选定记录和随信附件。

```sh
cosmesh thread mail sharing THREAD_ID --to ACCOUNT --json
cosmesh thread send THREAD_ID --to ACCOUNT --body '请看这个项目' --share-workspace --dry-run
cosmesh thread send THREAD_ID --to ACCOUNT --body '请看这个项目' --share-workspace --expect PREVIEW
cosmesh thread send THREAD_ID --to ACCOUNT --body '今晚开会' --message-only
```

将 PREVIEW 换成上一步的预览值。重试同一次投递沿用原预览或操作键。纯文字私信可省略 Thread ID。首次共享需客户端和 Hub 都支持 workspace.v1。直接操作终端可在发送时选择；通过 Agent 发信时由 Agent 与发送人确认。

## 从消息打开对方的工作

```sh
cosmesh thread inbox
cosmesh thread mail show MID
cosmesh thread mail open MID
cosmesh thread mail open MID --at-send
cosmesh thread mail open MID --records
cosmesh thread mail open MID --history RECORD_ID
cosmesh thread mail open MID --record RECORD_ID --version VERSION
cosmesh thread mail open MID --files
cosmesh thread mail open MID --download ASSET_ID --output NEW_PATH
```

默认打开最新已同步的工作定义。用 `--at-send` 阅读发件时的定义、记录或文件。列表有下一页时沿用回执里的 revision 与 next_offset，传入 `--workspace-revision REV --offset N`。文件清单提供 ASSET_ID，下载目标须为新文件。来信仍可用 `thread mail file MID --thread OWN_THREAD` 归入自己的工作。用 `thread reply MID --thread OWN_THREAD --body TEXT` 可从本人工作回信。

发件人用 `thread mail unshare THREAD_ID --from ACCOUNT` 撤回该账号的工作区访问权限。撤权后，该人从任何关联消息打开当前或发送时的工作区、在线取文件都需要重新授权。普通信件和已经下载的副本仍在。单封消息另用 `thread mail revoke MID` 撤回。

中断后，用 `thread mail pending --json` 找到本次待投递的 operation，再用 `thread mail resume OPERATION` 续发。首次共享时若已登记的文件缺失或超过对外额度，可先处理回执里的文件问题。收件端在第一版成功后即可打开工作区。仅停用本机进展同步用 `thread personal THREAD_ID --off`，已有授权可继续读取上次成功同步的版本。
