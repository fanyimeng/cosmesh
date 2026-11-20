# 🚀 CosMesh 0.6.2｜让指定机器上的 DSH 或 Codex 接活

在同一个个人同步 Thread 里说“让服务器上的 DSH 跑验证，把结果写回这里”。先在两端绑定已有会话并开启个人同步，再在目标端开启接收。下例的 THREAD、SESSION、HOST 换成实际值。

- **Codex 接收**　在目标机运行并保持 `cosmesh thread task receive THREAD --agent codex --session SESSION`。
- **DSH 接收**　安装或更新连接器到 0.1.3。在目标 profile 的 `cosmesh-receivers.json` 配置接收列表，例如 `[{"thread":"THREAD","session":"SESSION"}]`。保留已有条目，设为 0600，再重载该 profile。
- **指定目标**　用 `cosmesh thread task targets THREAD` 查看设备和会话，再运行 `cosmesh thread task send THREAD --device HOST --agent dsh --body "跑验证" --idempotency-key check-01`。目标也可选 `codex`；同机同类 Agent 有多个会话时加 `--session SESSION`。重试同一任务沿用原键。

忙时排队，设备回来后继续，结果写回原 Thread。原项目和会话保留。新开 Agent 会话即可加载新版 Skill。
