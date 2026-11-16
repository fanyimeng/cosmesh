# 个人共享与私人中转（0.4.2）

本功能需要显式开启。当前完成同机资源引用、本人工作台跨设备同步、私人文件中转和按需拉取。后台监视、自动执行、逐 Thread 元数据筛选与缓存自动回收仍为后续阶段。正式安装与公开发布流程独立。

## 同机多 Agent

先在已有私人 Thread 工作台执行 `personal init --root WORKBENCH`。同电脑各 Agent 使用同一工作台和 CLI。

`personal asset add THREAD_ID /absolute/file --root WORKBENCH` 登记原位资源；目录也可登记。本机引用没有大小、类型或数量配额，不复制、不上传、不计算完整内容哈希。`personal asset list` 查清单，`personal asset open ASSET_ID --offline` 返回当前可访问的原路径。同机原件是可变资源，回执标记 mutable_source；固定版本下载缓存另有 SHA-256。

私人延续原本机访问规则；个人共享增加主动登记与跨端接续；公开继续由 thread share 明确发布。个人共享不增加同一系统账号下的进程隔离。

共享 home 的服务器节点访问同一工作台时，使用同一份个人侧车和原文件，沿用工作台的跨进程文件锁。状态页 SQLite 缓存保存在各节点本地临时目录。私人中转数据库只由 Hub 登记的权威节点打开，其他节点经网关访问。实际锁语义取决于共享文件系统配置，上线前应执行双节点隔离验证。

## 跨设备接入

维护者在 Hub 执行 `hub personal-init --hub-root HUB` 开启私人中转服务。未启用的 Hub 不接受新功能；用户仍需分别主动接入自己的工作台。

来源端：

```text
cosmesh personal connect --ssh MY_EXISTING_ALIAS --root WORKBENCH
cosmesh personal sync --root WORKBENCH
```

连接使用现有 SSH／系统身份，收到的 actor 固定到该工作台，不保存新凭证。服务器模式可用 `--service SERVICE_JSON`。首次连接与同步均为明确命令；普通私人读写保持原行为。

`personal sync` 同步该工作台全部私人源记录、操作回执、已登记资产清单及项目／会话绑定。第一次传递当前完整快照，以及当前记录递归引用的本地历史固定版本；此前 Git 的全部历史提交不上传。历史作者、文档 ID、正文版本保持，固定版本通过候选的私人来源档案解析。无引用的更早历史继续保留在来源端。连接配置、Profile、SSH、公开空间缓存和完整聊天不会加入快照。

目标端先创建独立空工作台，再接入：

```text
cosmesh thread init --root NEW_WORKBENCH
cosmesh personal init --root NEW_WORKBENCH
cosmesh personal connect --ssh MY_EXISTING_ALIAS --root NEW_WORKBENCH
cosmesh personal list --root NEW_WORKBENCH
cosmesh personal join WORKSPACE_ID --root NEW_WORKBENCH
cosmesh thread continue THREAD_ID --root NEW_WORKBENCH
```

join 仅用于空工作台；保留来源作者身份，网络授权由本人认证账号核验。不同本地用户名可以接入同一本人空间。以后 `personal sync --pull` 只拉取并合并，`personal sync` 双向同步。两端独立追加可以合并，同定义／状态冲突保留双方历史，先解决差异再重试。绑定来源保存在个人清单中，各端实际执行仍核验本机位置，可用原 bind-project 建立当前端关联。

本阶段以工作台为元数据分发单位。需要仅本机保留的工作使用独立工作台；不要将工作台级同步描述为只上传某个 Thread。

## 文件中转与按需获取

`personal asset plan [ASSET_ID ...]` 只做本地资格检查，不哈希超限文件。`personal asset push [ASSET_ID ...]` 上传合格原件并同步资料清单；省略 ID 处理本工作台全部已登记且来自本设备的资源。`personal sync --files` 将该步骤与同步组合。无 --files 的普通同步只传元数据。

跨设备同步清单后，`personal asset open ASSET_ID` 按需拉取到本机私人缓存；同设备已有原件则直接返回原路径。缓存文件按摘要命名，显示名在清单 name 中。每个工作台各 Agent 共用缓存；不同工作台缓存尚未合并。下载不覆盖项目文件。`--offline` 仅使用原件或已验证缓存。其他来源设备文件未上传时返回 SOURCE_PENDING。

默认自动单文件 256 MiB、每批 2 GiB／1000 个，服务器本人 24 小时已计费收发预算 10 GiB、对象空间 50 GiB，本地工作台下载缓存 10 GiB。`--explicit` 允许已明确选择文件使用 2 GiB 单文件额度，仍受批次、流量和硬配额约束。第一阶段命令触发，不启动常驻后台任务。

`personal policy` 查看本地策略；例如 `personal policy --set auto_file=134217728`。正整数单位为字节，batch_files 为数量；`--dry-run` 预览。daily_bytes 与 server_bytes 由中转服务管理，客户端拒绝修改这两项，避免产生只改显示而未改变服务端配额的假象。其余本地策略仍受服务端硬上限约束。新文件配额预留包括未完成上传，文件读取与成功重试按实际载荷计费；元数据快照同样计费。达到限额暂停，保留源文件；第一阶段缓存无自动淘汰。

科研结果用 `personal asset add THREAD_ID FILE --science` 明确允许 FITS／NPY／HDF5／Parquet 等。普通文本、源码、PDF、图片、经过有界容器检查的 Office 按规则处理。凭证路径、可执行内容、未知二进制、通用压缩包和远端目录传输会给出跳过原因；跨端不跟随符号链接。目录本机引用可用，目录展开／容器例外留后续。原位本机资源不应用这些传输过滤。

文件上传前冻结快照并检查源是否变化；流式分块、服务端偏移和 SHA-256 支持失败后重试。快照与文件通过本人认证命名空间读取，原件不进入业务 Git；公开附件保持原有 20 MiB／100 MiB 与明确选择的规则。

## 状态、兼容与回退

personal status 给出本机保存的 accepted_snapshot；有本地后续修改时它只是上次同步基线。回执中的 server_available 表示服务器已接受原件，local_available 表示本机已校验文件，均不代表 Agent 已执行任务。

新增数据仅在工作台 `.cosmesh/personal-v1/`（Git 排除）、个人数据根的 `personal-device-v1/` 和 Hub 的 `personal-v1/`。旧 Thread 继续使用 schema 2；原正文、Profile、SSH 与公开策略保持。

回退时停止候选入口，切换原 0.4.0 程序／网关。旧程序可继续读写同一工作台普通 Thread；新个人侧车和 Hub 对象原样保留，旧 Hub 不提供个人中转。新设备仅存于私人来源档案的历史版本需恢复候选后解析，来源设备的原 Git 历史始终保留。恢复候选后继续同步，使用内容版本与幂等对象识别重试。

同步应用前持久化恢复日志并保留原 Git 提交，写入失败可以由候选自动对账；发现中断后又产生新修改时提示 RECOVERY_REQUIRED，保留日志与双方记录。回退软件不撤销已经接受的工作记录。需要恢复具体资料时使用经过备份验证的独立恢复副本，避免覆盖回退期间新增工作。

首次启用前查看具体范围。个人服务器访问控制包含服务器维护者可接触存储的边界；本版本不提供端到端加密或长期备份承诺。
