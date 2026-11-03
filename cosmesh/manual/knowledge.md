# 知识库、文献与历史工作档案

## 内容和位置

0.3.6 的本地读取按 Git 完整版本批量取对象，Thread 操作和状态回执复用同一工作台。当前命令内可复用未变化模型，版本变化时重新校验；每次加载继续检查路径。接受状态匹配已核验来源，旧版本引用仍沿历史查找。此缓存随命令退出释放，无新后台进程或持久索引文件。

知识库是独立的组内共享空间。文献和资料使用 K- ID，可包含 Markdown、TXT、PDF、DOC/DOCX、PPT/PPTX、HTML、BibTeX、JSON/CSV/TSV 和简单图像。大型科研数据继续留在分析环境。每项资料可以有多个文件和多个固定版本，Thread 与 Base 引用同一个资料 ID 和版本。

服务器由维护者 hub community-init --hub-root HUB --library-root LIBRARY 明确建立专用目录。catalog.sqlite3 保存用户、不可变资料版本、活动、来源和派生索引；blobs/ 保存按 SHA-256 定位的原文件和提取文字。目录 700、文件 600，客户端通过已核验的 Hub 身份访问。共享用户默认可读；正式写入前填写本人真实姓名。

本机缓存位于工作台 .cosmesh/knowledge/，不进入业务 Git，也不附私人 Profile。普通缓存读取和待提交资料离线可用。library sync 或 --sync 才发布。读取最新内容使用 --require-fresh，只拉取；回执未确认时仅称已保存本地。

0.3.4 的网络操作按需复用连接：一轮命令中的目录、状态和资料分块共用连续 RPC 通道，Git 使用独立通道并复用底层 SSH。控制 socket 位于执行主机本地的私有临时目录，空闲两分钟后 SSH 主连接关闭，不修改用户 SSH 配置，不自动同步或后台检查业务。批量版本只决定抓取范围，写入后仍核验实际接受结果。断线保留本地输入，未知结果的写入使用原幂等键重试；旧服务端可退化到单次控制连接。

## 收录、阅读和检索

library add --title TEXT --file PATH --kind document|reference 建立本地待提交资料，--file 可重复。--body 保存简短用途，长篇文字作为文件。--doi、--arxiv、--authors 保存文献信息，原文作者与上传者分别保留；可以只收录文献信息而暂不附全文。--ref ID@VERSION 引用已共享的工作或知识资料。

library list/search/show/text/history 查看资料。默认列表和搜索突出 published，list --all 查看 draft/withdrawn；list --archives 查看历史工作档案。search QUERY --full-text 使用服务器的派生正文索引，按页取得命中项，不下载整库。离线全文检索只使用已缓存文字，缺失时明确返回 CONTENT_NOT_CACHED。

library fetch ID@VERSION --name FILENAME --output NEW_PATH 取回指定原文件，拒绝覆盖已有输出，核对文件 SHA-256。library text ID 按需取回提取文字；history 和 ID@VERSION 可查看旧版。

PDF 使用固定版本 pypdf 提取并保留页标记；DOCX 提取正文段落，PPTX 保留幻灯片序号，HTML 仅提取文字。原文件始终保留。扫描 PDF 标记 needs_ocr，旧 DOC/PPT 和图片保留原文件并标记 not_extracted；本版未配置 OCR 或旧 Office 转换。复杂表格、图片和公式以原文件核查。解析有时间和输入大小限制，提取失败不丢弃原件。

单附件 20 MiB、每项最多 32 个不同名称文件，合计 100 MiB。单项摘要保持简短，内部归档包和目录快照上限 100 MiB。限额在 knowledge_protocol.py 集中定义。

## 从资料查找已有工作和结果

```sh
cosmesh library backlinks K-ID --require-fresh --json
cosmesh library backlinks K-ID@VERSION --offline --json
cosmesh thread context RESULT_ID --json
```

backlinks 从现有引用重建，列出直接引用这份资料的共享工作和记录。threads 按工作去重并附 record_ids；backlinks 保留完整引用者 record、所属 thread、关系字段 relation 和固定版本 target，record.type 区分结果、笔记等。library show 同时提供这些关系。程序不复制结果正文、不增加源记录或作者。

仅传资料 ID 时 version_scope=all，包含已缓存工作对该资料各版本的引用；ID@VERSION 时为 exact，只返回引用该版本的记录。source 表示本次选定资料，current_source/current_status 表示最新缓存版本及状态；旧引用保持原版本，资料修订或撤回后仍能查阅历史使用。

个人 Agent 沿 record.id 用 thread context 读取结果、依据和更正，根据当前需求、版本和适用条件判断复用。再用 thread backlinks RECORD_ID 可发现引用该结果的综合成果。只读查询不会自动采用，归档工作的原始记录仍可被发现。

coverage=cached_mesh 和 sources 表明实际查询范围及截至版本；accepted=false 的引用者尚未确认发布。默认查询排除私人 Base、Session 和目录绑定。--require-fresh 只拉取，不发布工作草稿或资料 outbox；--offline 完全使用已有缓存。反向关系按需展开，无模型、后台扫描或新数据格式。

## 修订、维护人与候选内容

library revise ID --expect CONTENT_VERSION --file PATH --sync 创建新版本；省略 --file 保留附件，提供 --file 列表时替换该版本的附件清单。旧文件、旧文字和旧说明都可按旧版本取回。--status draft|published|withdrawn 分别表达候选、正式采用和撤回；生成过的版本保持，不覆盖已发布内容。

资料维护人可以修订，作者归属保持。library transfer ID --to REAL_NAME --expect VERSION --sync 明确交接维护责任；历史作者和交接活动保留。系统维护者可处理交接，操作同样记录。

每件逻辑写入使用稳定 --idempotency-key。输入先保存在 outbox，网络重试不重复创建。library pending 显示未发布操作。冲突时保留原输入，刷新并准备修订；明确放弃待提交操作可用 library abandon KEY --reason TEXT，原请求和原因仍留存本地，已接受版本用新版本更正。

## Thread 归档和重开

已被 Hub 接受的共享 Thread 结束为 done/cancelled，或显式 thread archive ID 后，程序生成独立的只读归档版本。归档含完整 Thread/记录、原始文本、来源版本、必要引用内容和结束记录；私人执行绑定、原始聊天与 Profile 不包含在内。library archive-show ID@VERSION 查看完整快照。

归档沿用原 Thread 身份映射，多次快照使用同一个知识资料 ID 的不同版本。后续更正或再次结束生成新版本，旧档案保留。工作结果与收纳状态分开：thread state --to active|done|cancelled 管工作结果，thread archive/restore 管收纳。旧 state archived 仍可读。重新开展时设置 state active，并按需要 restore；旧档案通过当前 Thread 状态显示已经重开。

thread list 默认隐藏已归档工作，--all 包含历史工作。私人 Base 工作仅在本机保留 Git 历史，不自动进入公共知识库。共享 Thread 的权威源继续留在 Hub，知识库快照独立可读；本次不改写 Git 历史以回收空间。

成功接收共享写入后触发归档；断点由下次目录拉取对账补齐，无需公共 Mesh Agent 或大模型。library status 显示 archive_errors，源记录保留，可在恢复条件后重新拉取重建。快照以单仓库接受版本为准，跨工作引用固定到明确版本。

## 贡献与生成依据

Thread 生成内容可用 --generation-file JSON 提供 tool、model、instruction，系统标记 client_reported；拿不到的信息标记 not_provided。资料 add/revise 同样支持 --generation-file。用户只提供适合当前内容范围的生成依据；私人指令和草稿留在私人范围。

每次生成、导入、修订、交接和归档都有版本化产物或操作记录。服务器核验账号与稳定 person ID，客户端提供的模型信息单独标明来源。工作正文、原文件、输入引用、候选状态、采用与更正各自可追溯。贡献归属根据内容来源继承，综合者、原贡献者、上传者和原文作者分别展示。

user overview/contributions REAL_NAME 查看共享工作、请求状态、资料版本与活动。记录链包含前序摘要，可校验关联完整性；系统维护者仍承担备份与恢复职责。数据库和 blobs 必须一起备份，检索索引可以重建。


## 与 Thread 配套共享和固定打开

默认share只共享文本简报；明确要求附件后，thread share ID --attach FILE --sync 将选定必要资料入库后发布普通资料索引与固定引用；--materials 可给私人选择清单，--resource 复用已有资料，--git/--commit 定位代码。后缀默认跳过数据／压缩文件，单文件20 MiB、整次新原件100 MiB；缺项明确为partial。详见 guide show sharing。

固定文件引用为 cosmesh://library/K-ID@VERSION/FILENAME。library open FIXED_REFERENCE 校验并取回本机私有缓存，--launch 明确启动系统查看器；回执提供本人可打开的path，共享继续使用固定reference。Markdown配套相对引用按需准备同项资料中的已选附件，缺失依赖如实返回。JSON/CSV/TSV作为有界UTF-8文字保留原件和可检索文本，不新增分析引擎。
