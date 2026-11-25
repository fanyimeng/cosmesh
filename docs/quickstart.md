# 用 CosMesh 在不同设备接续同一项工作

在自己的客户端配置默认 Hub。服务器上的个人 Agent 也使用自己的账号。第一次使用时执行以下命令，用创建回执中的实际 ID 替换示例里的 THREAD_ID。

```sh
cosmesh thread init
cosmesh thread create --title 'Survey' --body '目标和下一步'
cosmesh thread bind-project THREAD_ID --agent AGENT --kind directory --workdir PROJECT_DIR
cosmesh collect add THREAD_ID PROJECT_DIR --personal
cosmesh collect once
```

`collect once` 检查本机登记的全部收集范围。要持续收集可启动 `cosmesh collect watch`，按 Ctrl-C 停止。只需本机记录可在登记范围时省略 `--personal`。可选的 `--context-file handoff.md` 用于读取项目中由宿主维护的交接摘要。宿主也可在工作停顿时用 `collect summary THREAD_ID --event EVENT_ID --body TEXT` 记录结论。

## 在下一台设备上

```sh
cosmesh thread find Survey --require-fresh
cosmesh thread fetch THREAD_ID --agent AGENT --workdir DIRECTORY --files
cosmesh thread continue THREAD_ID --ready
cosmesh thread locations THREAD_ID
```

查看恢复回执中的文件状态。已有不同内容会保留在目的地。希望持续收集目的目录里的新文件时，使用 `collect add THREAD_ID DIRECTORY --personal` 登记。个人同步默认上限为单文件 5 GB、单批 10 GB、服务端本人总量 30 GB。

## 给合作者发东西

```sh
cosmesh thread send THREAD_ID --to ACCOUNT --record RECORD_ID --attach FILE --dry-run
cosmesh thread send THREAD_ID --to ACCOUNT --record RECORD_ID --attach FILE --expect PREVIEW
cosmesh thread inbox
cosmesh thread mail show MESSAGE_ID
cosmesh thread mail file MESSAGE_ID --thread THREAD_ID
cosmesh thread reply MESSAGE_ID --body '回复内容'
```

PREVIEW 使用预览回执中的摘要。独立消息可省略源 Thread。想暂不归档可用 `thread mail file MESSAGE_ID --defer`。按人查看用 `thread chat --with ACCOUNT`，按工作查看来往用 `thread activity THREAD_ID`。对外默认上限为单文件 2 GB、单批 5 GB、每项工作累计 15 GB。

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
