# CosMesh DSH connector

可选、独立安装的 Host bundle：`@cosmesh/dsh-connector`，版本 **0.1.1**。此组件随 CosMesh 源码独立发布，**未在 npm 发布**；与 CosMesh CLI 的版本独立。

**0.1.2 更新：新增统一 execution_revision 与 owner 执行元数据回执，保持旧参数、旧回执、journal 和初始化语义；修复已初始化操作以 initialize:false 重试时状态回退的问题。绝对目录与 Session 仍只在本机回执和日志中，查看者可读所有者批准的 Agent／Project 显示摘要。升级保留原项目、会话、资料和回退包。**

## 能力与边界

`cosmesh_open_work` 将已有、活跃、本地私人 Thread 接入当前 Host 的原生 Workspace 与新 Session；不创建 Thread、不改变可见范围。复用已有项目且保持其标题，通过 CosMesh CLI 私人 `bind-project`、`bind` 和 `update --local-only` 登记接入。

默认 `initialize:true` 发起**一次真实模型请求**：确认接续后等待。仅该 Session 的初始化轮全部工具由 Host guard 拒绝，不自动开始研究，不创建子 Agent/目标/定时任务。不发布、不同步 Thread 或上传项目文件；但是 **handoff 会作为初始化 prompt 发给当前配置的模型服务**，因此不应放入凭据或不允许提供给模型的资料。`initialize:false` 只建空白 Session，不调用模型；DSH 未选中空白 Session 时可能隐藏它。

成功回执 `initialized_waiting` 仅说明看到匹配输入、助手非空显示文本（只取 `data.message.content` 的 text block）和 `turn/end.reason.kind=completed`，不验证回复语义或 GUI 视觉；`accepted` 绝不等于 `completed`。回执和本组件 journal 仅保留布尔状态、有限序号及最多 512 字符的确认文本，不返回/存储完整模型 stream、reasoning、provider 元数据或 replay。DSH 自己的原生 Session 日志仍按宿主规则保留模型事件。

0.1.2 在全部旧字段之外新增 `receiptVersion: 2`、`executionRevision` 和 `executionMetadata`。后者是 CLI 返回的 owner 执行元数据，含本机工作目录与 Session，只保存到本机 journal 和本工具回执；其他 Thread 查看者使用 shared 投影，不获得这些字段。旧 CLI 未返回 execution_metadata 时两个新增值均为 null，不伪造 v2。partial 同样保留已产生的 revision，不能解释为无副作用。

## 安装与配置

需要已有 CosMesh CLI 和 DSH **0.2.2-rc.3** 的 Host 服务；包严格声明已核对的 DSH 版本，不承诺其他版本兼容。Node 测试要求 22+，打包测试另需 npm、tar。无 npm 依赖、安装脚本、构建步骤或自带 orchestrator 副本。

用户明确授权安装后，在 DSH Plugins → Add plugin 选择本目录绝对路径，或让 Agent 通过 `plugin_manager.install_bundle` 安装本地目录/本地固定包。无需 npm registry，不应执行 `npm install @cosmesh/dsh-connector` 假装它已发布。使用已构建、校验的新版本目录升级，保留原目录用于回退。

**迁移前先让已接受的初始化结束。旧原型与本组件注册相同工具，不可同时启用。** 保留原包与配置用于回退；在用户授权的切换中停用旧 bundle、启用本 bundle。不要删除项目中的旧 journal，也不要改 operation key 来绕过失败。

插件配置（不是模型工具参数）：

```yaml
namespace: desktop
cliPath: /opt/cosmesh/bin/cosmesh
```

`cliPath` 可省略，默认 `path.join(os.homedir(), '.local/opt/cosmesh/bin/cosmesh')`，即 `~/.local/opt/cosmesh/bin/cosmesh`。显式值必须是无控制字符的绝对可执行文件路径，`~` 字符串不展开。只通过参数数组 `execFile` 调用，不经过 shell；不可从工具参数传入命令、CLI 路径或任意参数。路径指向哪个程序由用户配置负责，组件不是 executable 签名验证器。配置由激活入口严格校验（不依赖 profile 中可解析的 schema 包）；用户通过 DSH 配置编辑机制修改，不直接改写真实 profile。

`namespace` 默认且迁移时必须保持 `desktop`；旧用户自定义值也必须保持原值。owner 精确保持 `{host: hostname(), namespace}` 的属性顺序与含义，不加入包名/profile/版本，否则旧 key 会落入不同目录。不要因安装位置变更而改 namespace。

## 工具参数兼容

保留旧工具名及全部参数，不增加自动研究模式或独立 status 工具。

| 参数 | 说明 |
| --- | --- |
| `thread_id` | 已有本地私人 `W-UUID` |
| `workdir` | 当前 Host 上绝对项目目录 |
| `project_title` | 新注册项目标题，已有标题不变 |
| `session_title` | 新 Session 标题 |
| `operation_key` | 同一逻辑请求的稳定键；不同键表示有意另建 Session |
| `handoff` | 可选私人背景，最多 12000 字符 |
| `initialize` | 默认 true；可在同一创建请求上从 false 变为 true |
| `create_directory` | 默认 false；必须另有用户明确授权才设 true |

每次调用需要 Agent Session 与 Full access；工具不提升权限，调用前仍需明确用户授权。默认不建缺失目录。重试使用原 key、原创建参数（标题、handoff、目录创建许可均不可改）；已完成步骤不重复执行。当前状态可在同 key 重试的回执中查看；**该重试不是只读 status**，未完成步骤可能继续写绑定、补回执或发起初始化，不应冒充无副作用查询。

## 分层

- [work-link.js](lib/work-link.js)：仅一个宿主无关流程、参数归一化、串行队列、journal/lock、逐步续跑、有限回执；不导入 DSH，也不内置初始化 prompt。adapter 提供 `initializationText` 与 `handoffText`。
- [cosmesh.js](lib/cosmesh.js)：有界 CLI 调用、Thread 校验、私人绑定与不含执行坐标的幂等本地记录。STORE_BUSY 最多尝试四次，其他失败不盲目循环。
- [dsh.js](lib/dsh.js)：原生 Workspace/Session、持久化核验、初始化 prompt、事件证据与工具 guard。它提供 adapter，而不复制流程。
- [index.js](index.js)：配置校验、Full access 检查及工具注册。

## 幂等与崩溃限制

继续使用项目内 `.cosmesh-dsh/<sha256(owner)[0:20]>/<sha256([thread_id, operation_key])>.json`，format=1，fingerprint 继续对旧字段顺序与规范化真实目录计算；`initialize` 不进入 fingerprint。Session ID 在创建前预留，初始化 requestId 在提交前保存，Host admission 对同 requestId 去重。旧 handoff 内容与路径保持，初始化正文由 adapter 生成但已存正文原样复用。

重试校验真实 Host 元数据；项目/Session 丢失、归档、目录变化或身份不符时拒绝自动替换。旧 journal 中仍在 100000 字节上限内的完整模型回执会在同输入重试时清理为有限字段；超限/损坏/符号链接 journal 拒绝读取，**不会自动迁移或新建替代对象**。需人工保全证据并对账，不能通过换 key 处理。

锁采用排他创建，journal 以临时文件写入后原子 rename。崩溃可能留下 lock 或临时文件；不自动抢锁/删文件。手工操作前核验所有者进程、确切路径与已存在 Host 对象。没有跨 CLI、Host、文件系统的事务，也不承诺突然断电时目录项已经 fsync 或 NFS 多机互斥验收。

guard 是当前插件进程内的保护：超时、取消、丢失已接受回执时仍保留，直到匹配输入被消费后的真实 turn/end；不取消已接受模型请求，也不把接受误报完成。证据读取失败保守保留保护。**进程崩溃、重启或插件卸载会失去 guard**，没有自动扫描全部 journal 或持久化恢复钩子，不能声称跨崩溃的工具禁用保证。不要在初始化未结束时卸载/升级；崩溃后先人工核验目标 Session，避免无保护恢复待处理输入，再显式重试原 key。本组件不自动开始研究。

## 验证

在本目录运行：

```sh
node --test test/*.test.mjs
# 或 npm test
```

迁入原 61 项回归并更新 imports，补充配置/CLI 参数隔离、旧 owner/journal/fingerprint、有限回执与旧 replay 清理、取消 guard 和实际 `npm pack --offline --ignore-scripts` 自包含验证。打包测试只把已打包的资源解到新建合成临时目录，从外部运行 Node 验证根/locale/icon exports，无真实 profile、Session、Thread 或业务资料。测试目录、VERIFICATION、临时缓存不进入 package.files。

npm 发布、跨设备/NFS/断电和 GUI 验收分别验证；沿用原型图标与中英文 locale，并在包内核验其存在和可解析性。


## 旧记录与公开范围

本地 Thread 的私人副本可以同时有对外可见的服务器快照。`--local-only` 只限制本次写入；后续公开同步仍会包含普通记录与历史。因此本组件的普通记录不再写入项目名称、目录、Session、Workspace 或初始化请求标识，详细信息保留在私人绑定与 `.cosmesh-dsh` 日志。

0.1.0 及旧原型可能已经把这些信息写进普通记录。升级保留原资料，不自动改写或删除历史。公开或扩大旧 Thread 的同步范围前，应检查这些旧记录及其历史；仅编辑当前正文不能消除旧历史中的信息。本人接续可继续使用 `thread personal ID`，保持当前公开快照不变。

旧 operation_key、journal 和初始化 requestId 保持。新版简短记录使用 v2 内部回执键，避免旧版已写入但日志步骤未落盘时，同键不同正文发生冲突；已完成步骤仍直接复用。
