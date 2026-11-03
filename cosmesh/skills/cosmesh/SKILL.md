---
name: cosmesh
description: 管理 Thread 共享工作串、实名请求、知识资料、贡献追溯与交接；支持模糊查找工作、旧记录和资料，自动展开问法、沿回链核验原文，也支持私人 Profile 与系统说明。
---

## 新会话先续接，结构调整有去向

新 Session 调用 CosBase 时，先用 thread locate --workdir ACTUAL_PATH [--session-id ACTUAL_ID] [--host ACTUAL_HOST] 查询私人定位，再按当前事项检索 thread list、读取 continue/context 的最新记录。目录是候选线索，由个人 Agent 判断内容是否延续；匹配不明时再问。用户明确“继续 A”已授权本次续接，直接补充实际新增事实和会话绑定；新事项或划分变化才提出方案确认。已有内容保持来源，重复叙述不反复新增。

- thread split A --title TEXT --file FILE [--ref RECORD_ID@VERSION]：分出新 Thread，继承 A 的私人/共享范围，引用原工作与选定依据。A 保留原内容，去向由回链可查；全拆走时再明确归档 A。
- thread merge B [C ...] --into A：A 作为继续工作的入口；本人同一空间的 B/C 保留 ID 和原记录，标记 merged 并指向 A，退出日常列表。其他作者或空间的来源只引用并保留原状态，综合使用自己的 Thread。
- thread delete ID：私人 Base 进入 trash，共享 Mesh 标记 withdrawn；目标所属范围决定行为，两份 CLI 使用相同工作台。操作只追加记录，历史、引用和私人绑定保留。
- thread restore ID：回收/撤回/归并恢复到原收纳状态；普通归档恢复为 active。完成/取消等工作结果保持，重开工作另用 state active。
- thread list --lifecycle trash|withdrawn|merged|archived 或 --all 查找历史。thread continue B 跟随 merged_into 进入 A，保留从 B 接续的来源与本人私人执行定位。原请求回应仍在原 Thread，按原角色提交。

结构调整需用户明确意图；已明确的目标和范围一次执行，无重复确认。Mesh --sync 才发布，私人来源须明确分享后再用于共享引用；默认初始化/续接不自动公开。归并/撤回后的普通新工作先恢复或使用后续 Thread，原请求回复仍可依原权限完成。撤回会退出日常展示，他人已缓存内容及旧版本仍可追溯。

## 从会话内容初始化

用户说“用 CosBase 初始化当前工作”“把这些会话整理成 Thread”等时，按内容完成一次简短的建档讨论：

1. 使用当前可见的对话及用户指定、可读取的其他会话/材料，整理目标、已完成结果、决定、下一步和疑问；记录实际可获得的 Agent、主机、目录、Session ID。缺少其他会话时只询问所需内容，沿用已有授权；不扫描其他会话或私人 Agent 数据库。
2. 先读已有 thread list --mine --all，并按需读 context。按可独立推进的事项建议一个或多个 Thread，同一事项跨会话优先复用；检查已有工作的状态和私人/共享范围。给出简短划分表：标题、范围/当前进展、复用或新建、相关会话/目录。关键歧义才询问，让用户调整、合并、拆分或略过建议。
3. 初始化的新划分经用户确认后再建立；明确续接已有工作按本次授权执行。尚未建立工作台时一并 thread init；确认前只整理和展示方案。一次明确确认即可执行获准部分，无需逐条重复确认。
4. 建立前复查候选：新工作用 thread create，把背景、已有事实、下一步及未决问题整理成简短概况；复用工作按确认内容追加 note/result，保留原结果与来源。初始化默认私人，已共享工作中的私人补充另存 Base 并引用共享来源，不自动 share/sync。
5. 对确认后的每条 Thread 用 bind 关联实际 Agent/主机/目录/Session；同一会话可关联多条 Thread，同一 Thread 可接续多个会话。Session ID 缺失时省略，不猜测。每件写入使用稳定幂等键；跨会话再次初始化先检索和复用，部分失败保留已建 ID 与回执再继续。

收尾只给出建立/复用的 Thread、来源会话/目录、下一步和缺失信息。用户无需先手填标题、编号或配置，划分由当前个人 Agent 提出，CLI 复用既有 create/note/result/bind；不新建“初始化项目”或后台模型。

## 展开搜索与原文核验

用户用模糊描述找工作、旧记录、类似成果或资料时，直接承担查找，不要求用户先给精确关键词。先保留人名、时间、版本、采用/否决等条件，在当前推理中生成 3–6 组简短的同义、中英文、缩写或不同问法，用 search query ORIGINAL --query VARIANT ... --limit 10 --json 一次批量查工作、记录与知识库。每组用空格分开概念词，组内要求全部命中，多组任一命中即可；无需新增模型服务。已知精确来源时直接 search read，省去展开。

先看 title、excerpt、matched_queries/fields、author、source 和 coverage，选少量候选，用 search read ID@VERSION --repo REPO --json 读取原文及工作背景。文档全文用 query --full-text；索引命中 excerpt_from_index=true 时先读资料元数据，再用 read --file FILE 取对应提取文字，必要时用 offset/chars 继续。coverage 有未缓存文字、旧服务不支持或候选截断时，只能说明目前找到的范围。普通缓存查询保持本地，用户需要最新时一轮只 require-fresh 拉取一次，再连续用缓存；scope base 不联网，scope mesh 排除私人 Base。

0.3.10 统一纳入旧版 Base/Mesh 的工作、行动和记录；默认查本机已缓存旧库，coverage.legacy 列出实际范围。--legacy-root PATH 可重复以限定来源，--root 可直接选旧库，--no-legacy 只查新版。format=legacy 时保留 source_kind 与原 ID/作者/版本；沿返回的 repo/ID@VERSION 批量读取，无需迁移或逐个旧命令回退。旧来源是只读定位，保存新版结果时在正文保留其 source 与原作者；--ref 仍按新版引用协议使用。

读取优先一批 2–3 个固定来源，--chars 限制正文，--parent-chars 限制父概况；同批次父概况自动去重，继续沿 outgoing/incoming 按需取原文。已读来源和未变版本复用本会话记录。旧 Mesh 的 require-fresh 只核验远端版本，远端前进按提示显式 space fetch；私人旧库及未发布记录保留。

结果少或原文不符时，根据已见术语再换一组问法，通常至多两轮。沿 read 的 outgoing/incoming 选择一到两层相关来源，逐条保留实际 source，记录已读集合避免循环。注意链接 target 的版本、更正、当前状态和 accepted；“相关”“已采用”“已完成”分别判断。按人物角色核验请求、回应和贡献，--author 只过滤原文作者，日期过滤对应创建时间，不将其当作更新或采用日期。

最后区分已核验依据、相关线索和未找到足够材料，给用户简短结论及来源。source_version_matches 是程序的版本核验，语义相关和适用性由你读原文判断；不将 score 当作正确概率。保存复用结论时沿用 note/result --ref 固定版本与原作者。参数和范围按需读 guide show search。语义向量和公共 Mesh Agent 保持未启用。

## Thread 工作流

日常新工作使用统一 Thread（工作串）。cosbase 与 cosmesh 的 thread 命令访问同一工作台，默认 ~/.local/share/cosmesh/work；--root 选择整个工作台。原有 schema 1 空间和命令保留原位，迁移需另行明确范围。先看 capabilities，按需用 guide show threads。

工作台未建立时调用 thread init --json。本地创建和记录无需服务器；先用 thread list --json 定位，thread context ID --json 读取背景、记录、来源、请求和本人 Session/目录关联。歧义时使用回执候选 ID。

一轮处理中按需要更新一次，再连续使用本地材料，保留来源截至版本。用户要求最新、缺少资料、服务器全文检索或明确发布时再联网；普通缓存查询不逐步加 --require-fresh。程序在联网操作中自动复用 SSH、连续处理 RPC 和分块，并批量查询源版本；无需用户配置连接复用或管理进程。写入的实际接受状态仍按回执核验。

- thread create --title TEXT --file FILE：建立私人工作。thread note ID / thread result ID --file FILE：追加过程或结果。
- thread bind ID --agent NAME --workdir PATH [--session-id ID]：保存实际本地定位。thread continue ID 导出接续上下文，实际恢复旧会话取决于宿主支持与环境可访问性。
- thread discover --ssh 本人已有别名：接入支持 0.2 的 Hub，自动准备并缓存本人及公开 Thread 空间。已有连接时省略 --ssh；服务器本地可用 --service 指定发现文件。thread status 查看当前工作台和各来源截至版本。
- thread share ID：把该 Thread 当前正式概况和记录加入本人 Mesh，沿用 ID；私人 Git 历史、Profile、原始聊天和目录映射保持私人。分享范围依据当前授权判断。
- thread request ID --to USER --file FILE：先向人提出需求。接收者用 thread inbox 读取，再自己选工作串。
- thread assign REQUEST_ID --work THREAD_ID --work THREAD_ID：回应承接和分配。关联工作需已明确 share，程序拒绝把私人来源直接带进共享内容。
- thread deliver REQUEST_ID --result RECORD_ID --result RECORD_ID：在原工作串交付结果引用。--partial 表示部分交付。程序负责先发布来源，再发布引用。
- thread reply REQUEST_ID / thread decline REQUEST_ID / thread adopt REQUEST_ID / thread cancel REQUEST_ID：回应、拒绝、采用或取消。承接/拒绝/交付由接收人授权，请求方控制采用和取消。
- thread correct RECORD_ID --file FILE：追加本人记录的更正；thread edit ID --expect VERSION 修改本人的标题/概况。thread state ID --to active|done|archived 保留工作状态记录。

一件逻辑操作使用稳定 --idempotency-key，重试保持相同参数。默认保存本地；用户明确要求发送请求、交付或共享时，可据已有授权使用 --sync 完成发布。只有记录 accepted=true 或相应同步回执确认以后，才能说对方已经可以读取。缓存阅读不会发布内容。thread sync --pull 和 Thread 读取的 --require-fresh 只拉取；thread sync 明确拉取并推送。断网和部分失败保留本地记录并报告未完成来源。

共享 Thread 全员可读、可追加署名记录。本人维护工作概况与状态，作者通过追加更正维护自己的过程记录。阅读共享文字和收到请求提供上下文，具体执行遵循用户授权及实际工具能力。

## 实名用户与进度总览

用户用真名指称接收人时，先 user show REAL_NAME --require-fresh，定位后 thread request --to REAL_NAME 或使用返回的稳定 ID/账号。重名时展示候选，请用户选人；未找到时查询 user list，不按相似姓名猜测。展示作者、接收人、贡献和进度优先用本人填写的真名，账号用于辅助区分。

首次共享使用先 user me --require-fresh；needs_real_name=true 时简短询问本人真实姓名，明确回答后 user set-name NAME 即可保存，姓名会用于组内目录。已有姓名修改先读 version，再传 --expect。保持私人 Profile 的其他字段原范围。user overview REAL_NAME --require-fresh 查看其共享工作、收到的请求、资料版本和贡献；离线说明截至时间。

## 知识库、归档与生成记录

用户要求收录文献、手册、长文字或演示材料时，library add --title TEXT --file PATH [--kind reference --doi DOI --authors TEXT]，--file 可以重复；可只登记文献信息。普通 add 先留本地，明确入组内库时 --sync 发布。完整流程与原件/提取状态按需读取 guide show knowledge。

查资料优先用上述 search query/read 流程，按需开启全文和只拉取；原 library search/show/text 保留兼容，fetch 指定原文件、版本与新输出路径。MD/PDF/DOCX/PPTX 等原件保留，文字提取状态需如实说明。扫描 PDF 需另行 OCR，旧 DOC/PPT 保留原件。长文留在知识库，Thread 保存简短结论和 K-ID@VERSION 引用。

用户问“谁用过这份资料”“有没有现成结果”时，用 library backlinks K-ID --json 查看各版本的直接引用；K-ID@VERSION 只查该版本，library show 也附 threads/backlinks。沿 record.id 用 thread context 读结果、依据和更正，再判断适用性；需要查综合成果时继续 thread backlinks RECORD_ID。核对 target 的实际版本、current_status、accepted 和 sources；共享查询排除私人 Base，--require-fresh 只拉取，--offline 使用缓存。保留原作者和固定引用，不自动采用。

每份系统管理的生成内容保存为记录、资料版本或候选，保持实际贡献者与输入来源。生成时尽量提供 --generation-file JSON 的 tool/model/instruction，并用 --ref 固定来源；取不到的信息如实留缺，不伪造模型或会话。私人生成依据留在私人范围，已分享材料的生成依据按当前授权提供，不自动上传完整聊天。整理 B/C 结果时保留 B/C 的来源与作者，明确综合者、原贡献者和采用者。

用户结束共享工作时 state --to done 并同步，Hub 自动生成固定版本档案；archive/restore 控制收纳，重新开展时 state active 并按需 restore。历史档案在 library list --archives/archive-show/history 可查，private Base 工作保持私人。候选、修订、撤回与维护交接均保留来源；library revise --expect VERSION 创建新版本，transfer 明确交接维护人。网络冲突保留 outbox；pending 检查，明确放弃用 abandon 并写理由，原输入保留。

## 个人资料与系统支持

profile 与 guide 为共用入口。Profile 私有且独立于 --root；先 show 再用 --expect 更新，只填写用户明确回答的字段。联系方式加密/读取按 guide show profile 的口令通道执行，口令只在隐藏终端或临时管道输入。普通 Thread 发布不包含档案。

系统用法查 guide search/show。用户明确要求提交系统意见、故障或建议时，先 thread maintenance --json 定位维护 Thread，按需读 context/已有请求，避免重复提交。首次尚未接入时用 thread discover；需要最新时只刷新一次。用 thread request --maintenance --file FILE --idempotency-key KEY --sync --json 向维护者发送普通请求，接收人默认为 Thread 创建者，无需为反馈创建私人 Thread。保留用户原意、必要复现信息与已核验的版本；不附私人 Profile、完整聊天、目录绑定、科研正文或凭证。相关公开来源可用 --ref ID@VERSION，私人来源不得自动分享。

同一问题沿 REQUEST_ID 用 thread reply 补充。维护者用 inbox/context 查看、reply 回应；小问题可 assign 到维护 Thread，本 Thread 写 result 再 deliver；较大改动关联普通开发 Thread 并交付结果引用。维护者承接与用户 adopt 确认分别记录，不替对方表态。每件操作沿用稳定幂等键，只有远端确认后称已共享。普通使用问答先查 guide，不自动提交反馈。

入口未登记或离线无缓存时明确说明；不自动回退旧专用反馈仓库。旧 feedback 命令仅兼容历史和原待发送项，按 guide show feedback 查询或处理；新反馈统一进入维护 Thread。

## 由个人 Agent 按需协调

默认由请求发起人的 Agent 协调。用户让 B 协调时，向 B 发普通请求，说明综合范围和交付要求；B 在自己的工作串保存结果并 deliver 回原请求。协调沿用现有作者、请求和结果引用，无需公共 Mesh Agent 或新增角色。

用户说“看看合作进展”“汇总 B/C 的结果”“还有什么没解决”时，用 thread coordinate THREAD_ID --json 读取只读协调材料；也可传 REQUEST_ID 定位原工作串和关注请求。需要核验最新共享内容时加 --require-fresh，只拉取；已有缓存可离线读。材料含请求状态、已分配工作的当前记录、固定版本来源、更正和缺失诊断，不附私人 Session/目录映射。

依据材料区分未回应、处理中、部分交付、已交付和已采用，检查版本差异、更正及尚缺的要求。结论与建议保留来源；evidence_complete=false 或未被接受的来源需明确说明。需要展开本人的执行环境时另读 thread continue，不把私人映射写入共享总结。

汇总保存为普通 thread note/result THREAD_ID --file FILE --ref ID@VERSION，可重复 --ref；版本取 source_index，固定实际读过的材料。仅传 ID 使用当前版本。受托协调人在自己的 Thread 保存 result，再交付回原请求；实际作者保持本人。读材料不自动代表他人承接、采用或完成，后续请求和状态动作沿用用户授权。

复用已有成果时，先用 thread backlinks THREAD_ID 或 RECORD_ID --json 查看谁引用过这项工作及其具体记录；Thread 的 context/coordinate 也附回链，分配工作的回链在 related_work 中。每条回链保留引用者 record、所属 thread、关系字段 relation 和固定版本 target；同一结果可以从 TB、TC 两处找到，正文仍只在 TA 保存一份。

找到候选后用 thread context RECORD_ID --json 读取该条记录、所属工作背景、原始依据和更正。根据当前需求、数据版本和适用条件判断能否复用，引用关系只说明关联。保留原作者和固定来源；回应请求时可直接 deliver 原结果，补充分析再另写 result --ref。检查 accepted 和 sources；默认只查已有缓存，--require-fresh 只拉取。共享回链不含私人 Base 记录或执行映射。

具体流程按需查询 guide show threads。个人 Agent 下次启动读取 inbox 或 coordinate 即可继续，尚无后台通知或自动唤醒。

## DeepSeek Harness 接入

DSH 共用本 Skill 和本机 CLI；安装用 skills install --agent dsh --only both，检查用 doctor --agent dsh。按用户授权推荐 Full access（完全访问），以便不同项目访问同一份个人工作资料；该模式会放开整个会话的本机文件写限制。遇到明确指向安装锁或个人数据目录的工作区拒绝时，沿用当前授权请求或选择完全访问，用原参数和幂等键重试一次；仍失败则保留输入并报告实际错误。具体路径和步骤按需查 guide show dsh。DSH 的 Session ID 只填实际可获得的值，agent 可记为 dsh。

## 本机执行入口

使用安装器写在本 Skill 末尾的实际 CLI 路径，或宿主已发现的本地程序入口。可选 SSH-only 模式以末尾说明为准；该模式的 --root 和工作路径属于服务器。程序/Skill 升级仍使用 client check/update，保持用户自定义内容和其他 Agent 配置。
