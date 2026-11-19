# 同一 Thread 跨设备接续

个人同步保持原 Thread ID，在不同设备上恢复本人的完整执行关联。查看者在线读取已发布内容及所有者批准的 Project 摘要。

```sh
cosmesh thread personal THREAD_ID --json
cosmesh personal asset add THREAD_ID FILE --as RELATIVE_PATH --json
cosmesh thread sync THREAD_ID --files --json
cosmesh thread list --personal --remote --json
cosmesh thread fetch THREAD_ID --workdir DIRECTORY --agent AGENT --files --json
cosmesh thread locations THREAD_ID --json
```

真实原生项目可加 --project-id/--project-name；目录与会话按实际位置登记。目录登记不授权递归扫描，文件仅明确选择。正文恢复、文件恢复、入口登记与实际执行分别报告。bind、fetch、sync 和 locations 回执中的 execution_metadata 使用同一 revision；registered_only 不表示宿主已创建或环境可执行。同设备共享原位引用，不应用网络传输配额。

默认单文件5 GB、每批10 GB/10000份、服务端本人总容量30 GB（含保留对象与历史）。GB为10⁹字节。每日传输1 TiB、本地缓存100 GiB独立设置。personal policy 查看或设置本地额度，与 Hub 取较小值。旧未改变默认值按新额度解释，自定义不同值保留，旧配置文件可回退。科学数据、压缩包及未知后缀可传输，无需 --science；保留该标志作兼容。凭证、特殊路径、目录和非普通文件继续过滤；超限在哈希前跳过。

流式传输、摘要校验、失败续传、三方合并和历史保留沿用原机制；已有不同内容报告冲突并保留。旧公开 Thread 迁移后也使用本人完整内容流，公共来源与私人资产仍分别控制；已有私人流的原始版本和关联保留，新公开视图只包含原获准公开内容。迁移发现原私人流时，普通 sync 保持原公共版本并提示 review_required；审阅私人历史与文件后，显式 thread access 才授权新的完整内容范围。首次恢复遇到本地历史冲突可选新 --root，本机原资料保留。

迁移后本人 fetch 将旧公共缓存退出当前活动目录，磁盘缓存保留用于离线历史和回退。旧 whole-store personal init/connect/sync/join 与 asset push 已退役，不降级整库上传。旧公开执行关联实现仅用于未迁移 Hub 的历史兼容；新 Hub 统一按 Thread 内容流。

thread access 管理查看权限，thread browse 在线读，thread track 按人追踪。Library 来源权限与配额见 guide show knowledge，管理员迁移与版本回退见 guide show operations。中转没有端到端加密。
