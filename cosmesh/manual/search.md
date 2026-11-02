# 由个人 Agent 展开问题的统一搜索

## 查询与范围

search query QUESTION --query VARIANT 可重复 --query，一次加载后查工作、所有类型记录与知识库。每组问法用空格分开概念词，组内各词都要命中，多个问法任一组命中即可。忽略大小写，词序和间隔不必相同；程序不调用模型、不内置同义词表或向量索引。问法由当前个人 Agent 生成。

默认返回 10 个去重候选，--limit 最大 50，--offset 按本轮候选分页。标题、原文和工作背景参与确定性排序，score 只用于排序。返回 matched_queries、matched_fields、原文片段、作者、固定 source、所属工作及接受/收纳状态。工作默认含归档，排除回收站、撤回和合并来源；--include-inactive 才纳入。资料默认查当前已发布版。

--scope all 为私人 Base 与缓存 Mesh/知识库，base 仅查私人工作，mesh 排除 Base。--kind thread/record/knowledge 可重复。--author 可使用实名、账号或稳定用户 ID，重名返回候选；--after/--before 使用 ISO 日期或时间，纯日期按工作台时区解释，before 纯日期包含当日。搜索不包含 Profile 或本地执行映射。

本机和 Hub 账号不同的本人私人记录，按保存的本人 Hub 账号关联姓名和身份筛选；原 author/account 及来源版本保持，identity_account 单独说明映射。共享记录按其实际账号查目录，固定目录身份优先，避免另一位成员恰与本机账号同名时混人。显示关联不改变权限。

## 旧版 Base 和 Mesh

默认同时读取 COSMESH_DATA_DIR（通常 ~/.local/share/cosmesh）下 cosbase、cosmesh、spaces 中的已有旧库，以及当前安装保存的活动 Mesh 缓存。仅检查这些应用目录，不扫描科研目录、Profile、聊天库或他人 home。--root 可以直接选择旧库，无需初始化 Thread 工作台；有 Thread 工作台时，旧库与新工作、记录、知识资料合并检索。

--legacy-root PATH 可重复，显式选择旧库时替代默认发现；--no-legacy 只查新版。--scope base/mesh 同时约束旧库。--kind thread 包含旧 Project/Task，record 包含旧 Update；format=legacy 和 source_kind 保留实际类型，原文元数据、原 ID、署名及内容版本不变。next_action/waiting 参与检索。归档工作可搜，trash 默认隐藏；父项目已删除时子行动保留并展示父状态。

旧来源的 repo 是本机目录的稳定读取标识，不是新的共享仓库；请沿命中返回的 repo/ID@VERSION 原样读取。同范围、同 ID、同版本的重复旧副本合并候选，不同版本无明确 repo 时返回歧义。coverage.legacy 列出实际读取的库、文档数、固定 Git revision 与失败来源；只覆盖本机已缓存空间，未抓取的空间仍需 space list/fetch。scope mesh 不附私人旧库的路径、版本、候选或反向关联。

每次命令每份旧库读取一次固定 Git 快照，批量查多组问法和读多份原文复用已解析模型、内容版本和关联索引。按明确 repo 读取时只加载所需旧库，单查 knowledge 时跳过旧文档加载。旧库不加写锁、不执行恢复、不改变文件或提交；未提交手工改动不属于这份固定快照。旧来源的 source 用于只读查证；跨格式保存结果时，在正文保留该来源和原作者，Thread --ref 继续按其原协议接受新版来源。

## 文字与网络

默认查本地工作原文、资料标题/说明/文献信息。--full-text 纳入已缓存的文档提取文字；可联网时额外用一次批量 RPC 查询共享知识库正文，服务端最多返回 50 个候选。--offline 不联网。无法提取、未缓存、校验失败、旧 Hub 不支持批量请求及超过候选上限，都在 coverage 中说明，不能据此宣称全库不存在。

--require-fresh 先只拉取一次新版共享来源和知识库，保留未发布工作与资料。旧 Mesh 只核验远端版本，复用连续 RPC；版本超前时返回 FRESHNESS_UNAVAILABLE，并提示显式抓取对应空间，旧库保持原状。旧 Base 始终本地读取。普通缓存读取无此开销。Base 内容及执行绑定不上传；服务器索引只覆盖共享知识资料。

## 原文和回链

search read ID@VERSION --repo REPO 读取固定版本原文，--chars 默认 8000、最多 32000，--offset 按字符继续读取。返回当前版本、原文版本核验、作者、接受状态、当前收纳状态、所属工作背景及上下游引用。原文有截断时带 next_offset。

同次 read 可给最多 5 个来源，跨空间用 REPO/ID@VERSION。一批只加载一次，正文总预算最多 32000 字符；混合成功与失败逐项报告，complete=false 时不能视为全部核验。--file 单次只读一个来源。

父工作概况默认最多 600 字符，--parent-chars 可设 0–1000；同批次重复父概况标为 already_in_batch，工作本身不重复附自身正文。旧库只附必要且有界的元数据，metadata_omitted 列出省略字段；next_offset 可继续读正文。旧版 project/task/reply_to/supersedes/sources/converted_to 解析为同库关联，target 版本属于对应固定快照，当前更正仍在 incoming 中。归档、完成与采用分别按原文核对。

资料命中来自文档提取文字时，用 search read K-ID@VERSION --repo library --file FILE 读取对应原文；只取所需文件，缓存缺失时按当前连接下载并检查摘要。--offline 缺少文字时明确报错。索引片段仅作线索，不能替代这一步。

上下游链接默认每方向 10 条，--links-limit 最多 50。每个链接保留固定版本 target；来自旧版本的使用关系标明 target_version_matches。共享原文不附私人回链或 Session/目录；私人原文的链接仍按所选 scope 限定。用户 Agent 维护已读 source 集合，按需走一到两层，避免循环和重复展开。

## Agent 的查找顺序

保留用户的人名、日期、版本、完成/否决等条件，展开 3–6 组同义、中英文或不同说法，一次 query。先读最相关的少量候选。命中不足时再换一组更贴近已见原文的术语，或开启全文；通常至多两轮检索。

核对原文、current_source、state、accepted、incoming 的更正关系，再给结论。区分已核验依据、仅相关线索和未找到足够材料；找不到时说明缓存与全文覆盖范围。引用实际读过的固定 source，保持原作者。程序检查内容版本和范围，语义相关性与是否复用由个人 Agent 判断。
