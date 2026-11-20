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
