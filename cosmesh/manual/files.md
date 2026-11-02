# 组件与文件职责

## 0.3 知识库与用户目录

connections.py 管理按需建立、命令内复用的控制通道和专用 OpenSSH 控制 socket；空闲两分钟关闭 SSH 主连接，不编辑用户 SSH 配置。gateway.py 保留 Git 二进制通道，连续 RPC 使用独立有界帧；受保护 relay v2 对每条请求核验 UID/账号及 endpoint，v1 保持兼容。

community.py 是 Hub 所有者运行的确定性服务，使用独立 SQLite 与内容寻址 blobs，保存稳定用户、本人实名、不可变资料版本、历史工作档案、贡献和操作链。检索文字与当前目录是派生结果。knowledge_protocol.py 定义 schema 3 的 K- 资料、引用与集中限额。

knowledge.py 管理本地元数据缓存、原文件缓存、离线待提交队列、格式提取、按需正文读取与实名解析；community_cli.py 提供 library/user 命令，并复用同一工作台的连接。resources/community_rpc.py 是标准库 SSH 适配器，在既有账号下校验网关和内核身份，传输有界文件分块，无新增账号、密钥或后台模型。

schema 1 旧文档、schema 2 Thread、schema 3 知识资料分别验证；library 的原始文件独立于 Git 源空间。Thread 的 generation/author_id 和版本化操作回执保留贡献来源，服务器核验稳定用户 ID。

## 软件、发布、数据三种目录

下面的源文件路径用于理解实现。日常使用发布版不需要下载开发源码。发布程序位于 ~/.local/opt/cosmesh/versions/VERSION/runtime，实际 Python 模块路径由 doctor 的 python_executable 和程序导入位置确认。用户数据与程序版本分别保存。

## cli.py 与程序入口

cosmesh/__init__.py 保存客户端版本；__main__.py 支持 python -m cosmesh。pyproject.toml 声明 Python 基线、依赖、cosmesh/cosbase 入口和包资源。

cosmesh/cli.py 的 parser 构造命令和参数，execute 路由业务、Profile、说明与反馈，capabilities 返回真实能力，main 输出稳定 JSON、错误码与来源状态；base_main 绑定 CosBase 规则。传输和 hook 分支输出各自协议流。

## protocol.py 文档格式

StrictLoader 安全解析 YAML，拒绝对象构造、别名、重复键与深层嵌套。identifier/new_id 处理 UUID，timestamp/day 处理时间和日期，validate 检查元数据，encode/decode 读写 Markdown，version 用内容摘要标识版本，public 生成 CLI 文档对象。当前业务 schema_version=1。

## service.py 业务与视图

initialize 建源配置。Service.run 在固定 Git 快照中完成读取、幂等检查、版本前提、变更和提交。fresh/create 建文档，mutate 分派修改与生命周期，reduce 从 Update 控制链重建任务，check_graph 核验引用和规则，read 生成列表、正文、context、Inbox、Agenda、历史与验证结果。

CosBase 的转换、归档、回收、恢复与结束规则和 CosMesh 的邀请、状态、日期、责任规则在此分别处理。未来独立 reducer/views 模块尚未拆出。

## store.py 版本与事务

safe_path 防止路径越界和符号链接，atomic 使用临时文件与替换。Store.git 通过参数数组运行 Git；locked 检查专用仓库和干净工作树、持有文件锁并执行 recover。snapshot 固定 revision 读取受控文本，documents 解码文档，commit 保存操作材料并提交显式变更，recover 处理未完成事务。client/save_client 维护不入源历史的连接与同步元数据。

## sync.py 同步和传输

transport 校验位置与传输参数；system_ssh/system_command 复用既有 SSH 登录；remote_status 核验来源身份与产品。git_transport/url 生成固定 Git 调用。configure 连接空间，clone 建副本，merge 保留双方并处理冲突，synchronize 执行有限重试的 fetch/merge/push；publish=False 用于只拉取。state 提供已核验版本和截至时间。

## access.py 与 gateway.py 身份入口

access.py 的 system_user/peer_identity 取得系统账号；discovery 校验公共服务发现，connect 校验 socket 目录、所有者和服务进程凭据。

gateway.py 的 receive/send 读写有界握手；request 限定客户端字段与操作；status/open_request/git_client 连接入口；pump 保持 Git 双向字节流。Handler/Server 按连接派生进程。run 持有每主机入口锁。forward 和 relay_entry/relay_dispatch 负责维护者的可信跨节点转交。

## hub.py 服务端接受

Hub.initialize 创建受保护目录，create_repo 建 bare repo 和 hook，grant 管理仓库权限，policy/status 查询授权。serve 解析受限 SSH 请求，dispatch 取得仓库锁并运行 Git，validate_push 检查逐提交历史，audit 记录接受意向与完成。validate_change 负责源变更的作者、动作和历史规则；BareStore/view 以固定快照校验。

key-add/key-revoke 等兼容原公钥入口，普通用户沿用既有系统身份。system_feedback 是指定仓库的共享策略，blocked 与显式只读覆盖默认授权。

## profile.py 私人资料

PersonalProfile 定位共同配置，read/init/set/export/default_timezone 提供读取、补建、版本更新、字段选择与默认时区。encode/decode/validate 处理独立 Profile Markdown/YAML。私有目录、所有者、文件类型、硬链接、原子写和 lockf 构成边界。--root 不切换 Profile。

PersonalProfile.crypt 提供 contact 的加密保存、临时 reveal 与明确解除加密；set 保持已加密字段的更新方式。field_crypto.py 提供带版本信封、随机盐、scrypt 密钥派生和 Fernet 加解密；口令由 cli.profile_password 从隐藏终端或临时管道读取，保持在进程内。

## guide.py 与 manual 文件

guide.py 的 listing/show/search 提供主题、全文/段落和词项检索，document 从资源生成标题、行号和 SHA-256。manual/overview.md 解释定位与边界，usage.md 讲日常命令，architecture.md 讲数据和信任路径，files.md 就是此文件，operations.md 讲部署维护，profile.md 讲个人资料，feedback.md 讲反馈流转。资源随版本固定。

## feedback.py 反馈留存

Feedback.queue 先把用户输入保存到私人 outbox 并核验幂等键。infer/connect/ensure_cache 选择既有 Hub、核对专用反馈标志并建隔离 clone。pull 只刷新共享反馈，sync 按回复依赖顺序把待提交材料变为 Update、同步并保存回执。read 合并已接受快照和本地待提交项；discard 只取消尚未转成提交的本地草稿，保留本地记录。

FeedbackService 复用 Service 的事务、幂等、引用与格式，给作者补建反馈上下文并保存普通 feedback 类型 Update。validate_record/validate_snapshot 限定专用仓库的记录形态。共享写入身份仍由 Hub 核验。

## skills.py、Skill 文件与 errors.py

skills.py 分发标准 Skill，检查宿主路径、托管摘要与冲突，保持既有 Agent 配置。skills/cosbase/SKILL.md 和 skills/cosmesh/SKILL.md 包含业务、资料问答、系统说明及反馈的薄使用指引。它们共用接口，不保存权限或凭证。errors.py 的 Error 提供稳定 code/message/hint。

remote_skills.py 从同一份 Skill 正文生成 SSH 执行入口与成对安装脚本。resources/remote-run.sh 传送引用后的参数、stdin 与退出码，提供按需检查/更新；remote-install.sh 通过已有 SSH 设置服务器 CLI 并取得导出；remote-payload.sh 核验托管摘要、保留自定义内容并切换两份 Skill。tools/remote_bootstrap.py 只用服务器标准库，校验稳定发布目录，调用现有固定包安装器。tests/test_remote_skills.py 覆盖无本地运行时、参数边界、输入流、失败、冲突、回退和更新。

## 开发工具、示例与测试

tools/build_release.py 构建离线候选包或原样提升；release_installer.py 管理安装、升级、回滚、卸载、托管 Skill 和版本环境；hub_gateway_control.py 是维护者部署的 per-host tmux 控制；inspect_hub.py 提供服务器检查。工具职责以各自 --help 和源码为准。

examples/local_roundtrip.py 是隔离业务闭环；release_lifecycle.py 验证用户发布流程及文档/Profile 保留。tests/test_client.py 检查业务与本地持久化，test_hub_sync.py 检查 Hub，test_system_access.py 检查实际 socket 与身份，test_profile.py 检查私人资料，test_release_installer.py 检查安装边界，test_system_support.py 检查说明检索、反馈队列、共享范围、回复、历史保护与重试。

## 业务与共同数据文件

业务空间 documents/ID.md 是源文档；.cosmesh/config.json 是版本化源配置，operations/*.json 是源操作回执。client.json、lock、pending.json、conflict.json 是私人连接、锁、恢复和冲突材料。

~/.config/cosmesh/profile.md 和 profile.lock 属于私人用户配置。~/.local/share/cosmesh/feedback/outbox/*.json 是待提交输入及本地回执，connection.json 是专用反馈连接，repo/ 是共享反馈缓存，network.lock/outbox.lock 分别控制网络过程与短写入。共同元数据不嵌入普通业务仓库。

Hub 的 hub.json、policy/repos.json、policy/keys.json、gateway.json、repos/*.git、locks/ 和 audit/*.jsonl 由维护者管理。公共 service.json 只含发现信息，releases/ 保存不可变发布物；control/ 保存维护脚本与日志。

## 本地优先通用安装与更新

client.py 提供显式 client check/update，读取安装目录 client.json 中的既有 SSH 别名和更新来源。resources/local-install.sh 自动准备本机运行时并取得通用包；tools/build_portable.py 生成应用 wheel 和跨平台固定依赖摘要锁；tools/local_bootstrap.py 组合当前平台的离线 bundle 后调用既有 release_installer.py。普通业务入口不调用更新模块。

spaces.py 管理本人自动空间、公开空间发现及只拉取缓存，复用 sync/gateway。root_for 读取保存的活动 Mesh root，显式 --root 与隔离数据目录优先。gateway 的 cosmesh-space 请求只传 ensure/list，由内核身份决定账号；不接受客户自报空间所有者。

## Thread 0.2 内核与协调器

thread_lifecycle.py 按本机目录和会话定位既有工作，实现同库分裂与归并、Base 回收站、Mesh 撤回及保留来源的续接入口。结构操作保持作者与原 ID，归并和来源通过固定引用追溯；跨空间综合沿普通结果引用处理。

legacy_search.py 只读加载旧版 Base/Mesh 的固定 Git 快照，复用模型、版本和关系索引，提供覆盖清单、有界原文、历史来源及旧 Mesh 远端版本核验；原件、身份和隐私范围保留。

search.py 提供个人 Agent 批量展开问法所用的统一候选检索和固定来源读取，合并 Thread/record/知识资料，保留私人与共享范围、作者、版本、命中依据与有限上下游。community.py 的 community_search_batch 在共享文字上执行有界的多组词查询；Skill 负责问法展开、读原文和语义核验，程序不调用模型。

thread_protocol.py 负责 schema 2 的 Thread/entry 字段、固定版本引用、不可变记录、请求动作与工作状态的因果链，以及共享业务校验。Hub 与客户端复用这套规则，保留实际作者。

threads.py 的 Workbench 组织私人 Base、专用 Mesh 缓存、明确分享、请求关联、来源先于交付的同步、本地 Session/目录映射和引用上下文。thread_cli.py 给两份 CLI 注册同一 thread 命令，旧 schema 1 的 Service 路径保留。

threads.py 的 coordination 只读导出请求、分配工作、来源和更正，供当前个人 Agent 汇总；note/result 的 --ref 保存固定版本依据。协调结果仍是普通工作记录。

mesh_agent.py 保留可选实验公共协调器：私有配置、SQLite 队列、领取租约、去重和有限重试、兼容 HTTP 接口。tools/mesh_agent_control.py 管理 tmux 生命周期；tools/prepare_thread_preview.py 默认只准备独立 Hub 和工作台，显式 --start-agent 才准备并启动公共协调器。
