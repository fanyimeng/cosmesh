# Thread 工作串

## 工作台和共享

0.2 的 thread 命令在 cosmesh/cosbase 中相同，默认工作台为 ~/.local/share/cosmesh/work。thread init 只建立新空间；已存在时直接沿用。旧 schema 1 的 Project/Task/Update 空间、Profile 和原有历史保持原位。

新建工作默认私人。thread share 导出该 Thread 当前正式概况和记录，保持 Thread/记录 ID，按已核验的本人远端账号建立署名；旧私人 Git 历史和本机执行映射不发布。共享主位置为发起人的 Thread 空间；所有已核验用户可追加自己的记录，创建者维护概况和状态。

## 从一个或多个会话初始化

对个人 Agent 说“用 CosBase 初始化这些工作，先给我 Thread 划分”。Agent 根据当前可见内容、用户指定的可读材料和已有工作，按可独立推进的事项拟定一个或多个 Thread。先讨论标题、范围、当前进展和复用关系，用户确认后才用已有 create/note/result/bind 建立或补充。工作台缺失时也在确认后建立。

同一件事跨会话优先复用，一次会话涉及数件事可对应数条 Thread；初始化会再次检查已有工作。背景、结果、下一步和未决问题留在私人 Base，实际目录/主机/Agent/Session 是私人定位。Session ID 缺失时如实省略；其他会话材料只读取用户指定且有权访问的范围。已共享工作的私人补充另存 Base 并保留来源引用。部分成功保留 ID 和稳定幂等键，重试从回执继续。

例如：会话 A 同时讨论分析与会议，会话 B 补充分析。可以讨论后建立“分析”“会议”两条 Thread，A 关联两条，B 关联“分析”；已有“分析”时优先补充原 Thread。分享始终是后续的明确操作。

## 新 Session 与生命周期

0.3.7 增加 locate/split/merge/delete 与生命周期查询。新会话先 thread locate --workdir ACTUAL_PATH [--session-id ID]，结合实际事项读取已有 continue/context；明确续接按当前授权追加增量，新的独立事项才确认新建。定位只读使用本人的私人映射，无会话数据库扫描或后台模型。

split A --title TEXT --file FILE [--ref ID@VERSION] 在同一范围新建分支，保留原工作和固定来源；来源只存一份，去向从回链查看。merge B --into A 保留本人同库原件并标记 merged，continue B 跟随 A；其他作者或空间只引用并保留原状态。共同变更在一次 Git 提交，重试用原幂等键。

delete ID 按实际目标范围处理：Base 为 trash，Mesh 为 withdrawn。原记录、旧 ID、引用和私人会话绑定保留；list 默认只展示 active，--lifecycle 或 --all 可查历史。restore 从回收/撤回/归并恢复先前收纳状态，普通归档恢复 active，工作结果保持。归并和撤回停止普通新增工作，已存在请求的回应仍依原权限记录在原 Thread。公开内容撤回不收回他人已缓存的历史。

同库归并用来源到后续工作的单向引用及回链表示，避免制造私人来源互相依赖。以后分享沿来源顺序明确执行；部分来源尚私人时保持 PRIVATE_REFERENCE，操作不自动公开关联工作。

新生命周期值要求客户端和 Hub 0.3.7+，旧数据无需改写。固定安装器为新包标记 thread_lifecycle_version=2；回滚到不支持该能力的版本需先保全数据并确认兼容恢复方案。

## 创建和继续

thread create --title TEXT --file FILE 创建工作。thread list --query TEXT 定位，thread context ID 读取背景、过程、结果来源、请求状态和更正。thread note/result ID --file FILE 追加内容，thread bind ID --agent NAME --workdir PATH --session-id ID 保存私人定位。thread continue ID 提供接续上下文，实际恢复旧会话由宿主支持和环境可访问性决定。

thread edit ID --expect VERSION 修改本人的标题和概况。thread state ID --to active|done|archived 追加有因果前提的状态记录。thread correct RECORD_ID --file FILE 追加本人的记录更正，原文和历史引用继续可读。

0.3 将工作结果和收纳分别显示：state --to active|done|cancelled 设置工作结果，archive/restore 设置收纳状态。共享工作结束或显式归档后，由 Hub 对已接受版本生成知识库档案；私人工作留在本机。thread list 默认隐藏归档工作，--all 可查历史。新旧归档版本和重开流程见 guide show knowledge。

启用用户目录后，request --to 接受本人填写的完整实名、账号或稳定用户 ID；重名明确返回候选。展示优先使用 author_name/to_name。共享写入前本人需 user set-name，私人工作不受该条件约束。知识资料使用 K-ID@VERSION 作为 --ref，正文按需 library text 获取。

## 工作总览与私人接续定位

同时需要总览和继续一个已明确目标时，可用 `thread overview --mine --compact --continue ID_OR_EXACT_TITLE --json`。一次本地调用、一次 Workbench.load 返回两份视图，避免 Agent 读完总览后再次发起相同续接读取。目标名称/前缀有歧义时返回候选；--mine 和 --scope 同时限定总览与目标，目标可以已归并或不在列表上限内。仅在明确恢复或执行时按需核验宿主与目录。

组合格式为 thread.overview.continue.v1，continuation 保留原 continue 的工作、全部记录、引用、更正、请求、回链、归并去向和私人绑定；其 source 字段也使用同一 source_index。所选工作在总览中的 purpose.from、进展的 text_from 是回执内 JSON Pointer，指向 continuation 中已提供的原文。完整正文仅一份，作者、固定版本和接受状态保持；来源的同步状态复用共同 sync.spaces，不再重复返回顶层 sources。原始 continue/context 接口不变，缺失来源和后续变化仍需按需核验。

组合读取包含私人执行定位，只允许 --compact 及 all/base 本人视图，--scope mesh 在加载前明确拒绝。单独共享总览继续用 overview --scope mesh --compact。旧的已知目标单独续接、普通总览、详细接口保持原有行为；用户尚未选定目标时也保持先总览、后按选择读取。

0.3.18 起，两份 Skill 默认用 `thread overview --mine --compact --json` 一次本地快览。紧凑视图每条提供目的短句、一条近期非空原文摘录、状态、一个私人接续入口与绑定数量；不探测绑定目录，也不查询宿主。Agent 先显示所需字段，缺项标为仅登记／缺失，概括结论或下一步时再按需读原文和更正。原有默认详细 JSON 与 --details 保持，--compact 和 --details 互斥。

紧凑回执 format=thread.overview.compact.v1，行及记录的 source 是顶层 source_index 的整数下标；固定 repo/id/version 只存一次，作者与 accepted 分别保留。顶层统一解释证据状态，避免逐行重复来源和通用说明。execution 的 agent/host、session、project、directory 及 binding_count 保留私人定位；缺绑定时 status=missing。显式 --host-file 可提供已核验元数据，host_observation 给出来源/时间；project.verified_absence=true 表示宿主已核验该会话未归属项目，目录始终只登记。scope mesh 同样排除全部私人执行定位。

用户明确继续某条工作时，Agent 复用本轮已知 ID 直接读取 continue/context，只有目标未知才 locate；需要恢复旧会话或进入目录执行时再定点核验该工作所需会话与目录。宿主只提供列表 API 时批量一次，在工具侧筛出目标；宿主不可用则从 Thread 上下文继续，不为填满总览而扫描所有会话。一般查询的首次入口、版本和能力已知时直接使用。

对个人 Agent 说“我的工作总览”或“继续哪个工作”。Agent 主动显示工作目的、进展、执行 Agent、实际所属项目、可接续会话及关联目录，缺失项也明确列出。默认简短展示，每条优先一个接续入口，更多会话和目录按需展开。生成内容的工具单独记为 generation_tools，执行 Agent 来自私人绑定与宿主核验。

`thread overview --mine --json` 离线读取自己的工作与私人定位，按最近记录排序，默认最多 20 条，total/truncated 提示是否还有更多。`--limit 1..100`、`--query`、`--all` 和 `--lifecycle` 沿用列表筛选。`thread overview ID --details --json` 展开最多 100 个绑定并标明截断；同一 Thread 可关联多个会话，同一会话可关联多个 Thread。旧 list 契约保持。

`--scope base` 仅私人工作；`--scope mesh` 仅共享正文与进展，整个回执均不含私人执行定位、绑定数量或工作台路径，也不接受 --host-file。默认 scope all 是本人的私人总览，可包含本人对共享工作的私人绑定，不能直接复制进共享结果。普通读取不联网；--require-fresh 只显式拉取共享内容，Base 总览保持离线。

证据状态：verified（已核验）附来源和时间，宿主会话以列出时刻为准，尚未执行恢复；registered（仅登记）来自既有记录/绑定；missing（缺失）说明原因。宿主元数据由当前个人 Agent 报告，不代替 Hub 身份校验。宿主未提供、列表未覆盖或失联时保留仅登记，不推断会话已删除。归档会话标明需在宿主恢复。详细视图可检查本机目录存在性，紧凑视图只登记；远端目录不自动 SSH。绑定目录表示工作关联，数据/产物用途未登记时留空。

### 宿主元数据

有受支持的会话/项目 API 时，个人 Agent 批量读取元数据，按以下格式生成临时私人 JSON，传给 `thread overview --mine --host-file FILE --json`。CLI 不扫描 Agent 数据库，也不持久保存这份快照。仅取必要元数据，不附聊天正文、摘要、Profile 或凭证；临时文件设为 600，使用后删除。

```json
{"format":1,"source":"supported_host_api","observed_at":"2026-01-02T12:00:00+00:00",
 "sessions":[{"agent":"codex","host":"actual-host","id":"actual-session-id",
 "title":"宿主原生会话标题","project_id":null,"cwd":"/work/example","status":"idle",
 "updated_at":"2026-01-02T11:00:00+00:00"}],
 "projects":[{"agent":"codex","host":"actual-host","id":"actual-project-id","name":"示例项目","path":"/work/example"}]}
```

format=1；source 与带时区的 observed_at 必填。sessions 最多 1000 条，必填 agent/host/id/project_id/status；可选 title/cwd/updated_at/url。status 为 active/idle/notLoaded/archived/unavailable。projects 最多 200 条，必填 agent/host/id/name，可选 path。单字符串最多 2000 字符，文件受既有 1 MiB 限额约束；未知字段拒绝，空列表允许。url 只填宿主实际返回的地址，不拼接猜测协议链接。

Codex 宿主可用 list_projects/list_threads：projectId/label/path 映射到项目 id/name/path；会话 id/title/projectId/cwd/status/updatedAt 映射到上表。hostId=local 仅对实际本机映射为本机 hostname，其他宿主需已知映射。updatedAt 按接口实际秒/毫秒换成带时区 ISO 时间，不把数字原样传入。只规范化所需绑定及项目；查询不到时不遍历私人数据库。缺少单个会话可按需用受支持的定点查询补核验，仍失败则说明缺口。

会话真实 project_id 决定所属项目。明确为 null 时，显示“未归属项目”；即使 cwd 匹配已登记项目目录，也只返回 project_candidates 候选。会话 ID 按 agent+host 匹配，目录匹配不会自动建立会话绑定。本次观察不改绑定；新增绑定沿用既有 bind 和用户授权。进展摘录保留原文、固定来源与接受状态，Agent 需要概括下一步时按需读取 context，核验更正和适用条件。

## 接入和同步

首次共享用 thread discover --ssh EXISTING_ALIAS；服务器本地可省略 SSH 或用 --service 指定服务发现。Hub 必须支持 0.2 的专用 Thread 空间。复用已有账号，不需要新的注册或密钥。thread status 显示本地/远端账号、缓存空间和各来源截至版本。

thread sync 拉取并推送；thread sync --pull 与读取的 --require-fresh 只拉取，不发布本地草稿。普通读写离线可用。同步逐空间报告结果，来源未接受时保留待提交内容，只有 accepted=true 或确认的同步回执才说明已同步。

## 请求和交付

thread request THREAD_ID --to USER --file FILE 先向人提出需求。thread inbox 查询本人收到的请求，--sent 查询自己发出的，--all 包含已结束请求。

接收人选择自己的工作，明确 share 后，用 thread assign REQUEST_ID --work WORK1 --work WORK2 回应承接。结果用 thread result WORK_ID --file FILE 生成记录，取得 RECORD_ID，再用 thread deliver REQUEST_ID --result RECORD1 --result RECORD2 交付引用；--partial 表示部分交付。需要同步时加 --sync，程序先确认来源再发布交付回复。

thread reply/decline REQUEST_ID 回应或拒绝。请求人用 thread adopt/cancel REQUEST_ID 采用或取消。结果正文保留在来源工作串，TA 内的回应引用它；context 内直接提供可用来源与更正信息。

## 由个人 Agent 协调

默认由请求发起人的 Agent 按需协调。用户让 B 协调时，向 B 发一条普通请求，说明需要综合哪些工作和交付什么；B 在自己的 Thread 形成结果并交付。协调沿用原有作者、请求动作和引用，不增加协调人角色，也无需常驻公共模型。

thread coordinate THREAD_ID --json 导出只读协调材料。也可以传 REQUEST_ID，程序定位原 Thread，并标明本次关注的请求。材料包含请求状态、回应、已分配工作串的当前记录、固定版本来源及更正链。它只整理材料，由当前个人 Agent 理解、汇总和提出补充请求；命令不调用模型、不改变状态、不发送消息。

换会话或换人时重新读取同一材料即可继续。新鲜度沿用普通读取：--require-fresh 明确只拉取；离线材料按 sources 的截至版本解读。evidence_complete=false 表示有来源无法取得，需要保留诊断并说明缺口。共享工作串的协调包不附私人回链、Session/目录绑定或 Profile。

个人 Agent 应区分“未回应、处理中、部分交付、已交付、已采用”，检查不同数据/代码版本和更正。语义上的结论、缺口和建议由个人 Agent 判断，保留各自作者及来源。承接、交付、采用和取消仍遵循现有请求人/接收人规则。

汇总保存为普通 note 或 result，使用 --ref 引用实际依据。可重复提供多个 --ref，ID@VERSION 固定到协调包 source_index 中实际读过的版本；仅传 ID 使用命令执行时的当前版本：

```sh
cosmesh thread coordinate WORK_ID --require-fresh --json
cosmesh thread result WORK_ID --file synthesis.md \
  --ref RECORD_ID@CONTENT_VERSION --ref OTHER_RECORD_ID@OTHER_VERSION --sync --json
```

所有 ID/版本取实际回执，汇总作者为当前真实用户。受托协调人在自己的 Thread 保存 result，再用 deliver 回原请求。用户再次查看进展时，个人 Agent 主动读收件箱或 coordinate；未运行的个人 Agent 等下次启动继续。后台提醒和自动唤醒尚未提供。

## 沿记录回链复用成果

A 在 TA 的综合结果 TA_rec01 使用 --ref 引用 TB、TC 的具体结果。程序从这些正向引用重建反向索引，TB、TC 自动展示“被 TA_rec01 引用”的关联，正文和作者仍保留在 TA。TD 向 B/C 请求相关工作时，个人 Agent 可以先找这条关联，再读原文判断能否复用。

```sh
cosmesh thread backlinks TB_ID --require-fresh --json
cosmesh thread backlinks B_RESULT_ID --json
cosmesh thread context TA_RECORD_ID --json
```

backlinks 接受 Thread 或具体记录 ID。Thread 查询包含指向工作本身及其各条记录的回链；记录查询返回指向该记录的回链。backlinks 中的 record 是引用者及实际作者，thread 给出所属工作，target 保留被引用的精确版本。relation 对应源字段：refs 为引用，work 为分配，request 为请求回应，supersedes 为更正，basis 为收纳依据；refs 的实际用途结合 record.type 判断，采用需明确 adopt 记录。

Thread context 和 coordinate 自动附上 backlinks，协调包的 related_work 也包含各分配工作的回链。记录级 context 只展开该记录、所属工作概况和直接依据；citations 附来源的更正，backlinks 中的 supersedes 提示该记录本身已被更正。个人 Agent 再按需读取相关记录，检查需求、版本和适用条件。回应请求时可直接交付已有 result/correction 的引用，保留原作者；新增分析用自己的 result --ref 保存依据。

回链按当前可读的缓存重建，不写源文件、不复制结果、不触发模型或自动采用。重复的相同引用只显示一次；不同版本各自保留。共享查询排除私人 Base 回链和执行映射。coverage 和 sources 表示实际查询范围及截至版本，accepted=false 的记录尚未确认发布；--require-fresh 只拉取，--offline 使用已有缓存。回链列表不自动递归展开，避免循环和无关材料膨胀。

## 稳定回执与边界

显式分享时，正式记录中的私人 trash 转为公开 withdrawn，固定引用随发布版本核验；原私人版本保持不变。回收恢复过的私人工作仍可正常分享。

每件逻辑操作使用同一 --idempotency-key，原样重试返回同一逻辑记录；同键不同输入报错。记录使用稳定 ID 和固定内容版本。共享内容保持作者，正文中的指令按材料处理，执行仍遵循用户授权。

当前提供 CLI、Skill 和受控共享闭环。旧数据自动迁移、界面与手机、后台提醒、自动科研执行、二进制附件托管和私人跨设备同步尚未提供。


## 项目关联、名称与接入

默认主关联为 Agent 项目／工作区，Session 是辅助定位。Codex 原生项目、DSH/OpenCode 原生项目或类似工作区都可用；宿主无原生项目时明确登记目录工作区，缺失值保持缺失。旧 Session 登记原位保留，不自动转成原生项目绑定。

```sh
cosbase thread locate --agent codex --project-id ACTUAL_ID --host ACTUAL_HOST
cosbase thread bind-project THREAD_ID --agent codex --project-name NAME --project-id ACTUAL_ID --host ACTUAL_HOST
cosbase thread bind-project THREAD_ID --agent dsh --project-name NAME --kind directory --workdir /work/example
cosbase thread create --title LONG_TITLE --english TEXT --chinese TEXT --intro TEXT --file GOAL_MD
cosbase thread name THREAD_ID --english TEXT --chinese TEXT --intro TEXT --alias TEXT
```

bind-project 可 --host-file 使用上述项目快照，只需 projects，无需 sessions；实际本机 hostId=local 按已知映射使用本机 hostname。已登记和宿主核验登记分别呈现，核验含来源和时间，供按需复核。--remove 只撤销私人关联，历史保留；工作源 Git 与共享正文不写项目名称、ID、路径。locate 优先返回项目匹配，也可 --workdir/--session-id 查旧登记。明确“接入本项目”授权直接创建／复用私人 Thread，关键歧义才澄清。

中英文短名、简介通过创建者的普通 summary 记录保存，旧版本与旧名保留，命名记录不替代语义归纳。可用短名、旧名或稳定 ID 查找；重名显示候选 ID。详细回执 naming 包含 source/version/aliases/conflicted；修改可 --expect VERSION，保留并行命名记录，冲突时提示确认。英文 1–2 词、中文 2–6 字、一句简介为写作建议。未登记的旧工作继续显示原长标题／整体介绍，不自动批量改名。

## 快速模式

默认开启，个人 Agent 尽量少读、少转述、少做额外操作，直接复用成品与成功回执。client mode normal 关闭，client mode fast 开启，client mode 查看；--no-fast/--fast 只覆盖当前调用。两份 CLI 共用私人偏好，不改 Profile、模型或宿主配置；升级保留已保存偏好。每份 JSON 回执 fast_mode 表示本次偏好，无需另查一次。明确详细要求与必要核验仍优先遵循。


## 共享配套资料

普通共享默认只发Thread文本简报，不读取或上传关联文件；用户明确允许附带资料后，个人Agent准备必要说明、报告与小型结果的最小清单，thread share ID --attach FILE（可重复）--sync 或 --materials PRIVATE_JSON 完成先入库、后固定引用及Thread发布。代码可用--git/--commit，数据与压缩文件默认跳过，单文件20 MiB／整次100 MiB。显式--summary-only保留概况分享，缺资料保持partial；固定引用用library open取回打开，详见guide show sharing。

## Project capture and messages (0.3.27)

用户在项目中说“CosBase 更新”，个人Agent复用已知Thread或一次locate定位，按当前工作整理增量；必要时有界读取近期记录去重。thread update ID --body TEXT --interaction-summary TEXT --idempotency-key KEY --ready --json追加普通note，--kind result用于独立成果。私人目标留本地，共享目标默认只同步本次记录；--local-only只保存。精确重复复用记录，语义去重由Agent负责；同步失败原输入重试，UNRELATED_PENDING保留其他草稿并阻止捎带发布。--basis可使用上次update回执的记录集合签名检测并发变化。私人检查点保存在不入Git的本机目录，正文中私人路径在共享前省略，不自动上传附件或通知。

thread inbox --view messages --ready --json列与本人相关的请求、回应、承接、交付和更正；thread outbox --ready --json列本人发出的互动。首次历史标历史，之后新接受的互动进入新消息；时间顺序依据服务器索引，作者时间保留。--unread筛新消息，--limit限制数量，--before接续next_cursor固定窗口，--with实名与--thread ID筛选；最新同次--require-fresh。普通查询读本机缓存，最新一次RPC读预存通知索引，不先Git全量拉取。首次准备／更新中不能据空缓存断言无新消息。

thread mail show M-ID --json查看消息，发出的加--sent。原文使用返回source的thread read ID@VERSION --repo REPO。mail mark M-ID ... --seen或--unseen显式改变本人的查看状态，跨设备保存；列表／同步不标已查看。请求状态独立。thread inbox默认及--view requests保留旧请求JSON，--sent仍查询本人发出的请求。旧Hub无消息能力时明确报告，不用空请求列表代替新消息结果。所有私有收件／查看状态由认证身份决定，不接受代查他人参数。
