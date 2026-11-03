# 日常使用

## 从 Agent 开始

可以说“记下这件事”“看看我的跟进”“读取这个任务的背景”“把今天进展记下来并同步”“帮我完善个人资料”“解释系统架构”“记录这个系统问题”“查看大家的系统反馈”。两份 Skill 都能使用共同说明和反馈接口。

Agent 先根据意图选择个人事务、共享协作、Profile 或系统支持。文档工作先定位空间和对象，读 context，使用版本与身份校验后的 CLI 操作；系统说明和私人 Profile 无需先初始化业务仓库。系统意见、故障和独立使用问题进入反馈，普通业务进展仍进入所属项目。

## 程序、能力与数据位置

程序默认 ~/.local/opt/cosmesh/bin/cosmesh 与 cosbase，选装 Skill 位于 ~/.agents/skills。capabilities --json 返回版本和真实能力，doctor --agent codex 或 opencode 检查环境与 Skill，whoami 显示本地系统账号和当前文档身份。

默认入口在本地执行。client check --json 按需查更新，client update --json 明确更新本地程序和两份 Skill；更新失败仍可使用本地资料。普通操作不检查服务器。可选的旧 SSH Skill 通过 scripts/run.sh 在服务器执行，需要连接。

默认业务空间为 ~/.local/share/cosmesh/cosmesh 和 cosbase。--root 选择独立业务空间，后续文档命令沿用它。COSMESH_DATA_DIR 可改变共同数据父目录。Profile 和系统反馈有各自独立位置，--root 不切换这两种共同能力。

## CosBase 个人事务

首次使用 cosbase init --json，默认系统账号；新空间时区优先使用显式 --timezone，再用 Profile 的 timezone，最后 Asia/Shanghai。已有空间直接使用。

task create --title TEXT --next TEXT --due DATE --followup DATE --waiting TEXT 按需填入字段。零散 Task 自动归入个人项目，update add --body TEXT 可保存独立想法。inbox 查询待整理，doc list --query TEXT 或 --waiting 筛选，agenda --from DATE --to DATE 查询日期。

修改前用 doc show ID 或 context ID 取 version。doc edit ID --expect VERSION 修改字段；空日期用 --clear-due 或 --clear-followup。doc convert ID --to project|task|update 新建目标、关联来源并归档原文；doc derive 派生新文档并保持原文有效。doc archive / trash / restore ID 管理存放状态。task finish ID --outcome done|cancelled 与 task reopen ID 管理业务结果，均携带 --expect。

## CosMesh 协作与邀请

安装/更新自动 space discover，使用已登录系统账号准备本人 Mesh。space list 列出公开空间，space fetch USER 只拉取该账号内容并返回 root；后续读取传 --root。本人日常命令使用保存的活动 root。Mesh 公开读取，项目与任务角色继续决定写入。

project create --title TEXT --member USER 建共享上下文。task create --project P_ID --title TEXT --invite USER 创建行动；创建者是 Task Lead，邀请初始 pending。受邀者从 inbox/context 取得邀请引用，用 task accept/decline T_ID --invitation REF --expect-revision REV 明确响应。

Task Lead 或已接受成员用 update add --project P_ID --task T_ID --type progress|feedback|decision|correction|session --file FILE 追加记录。--reply-to 关联回复，--supersedes 关联更正。Session 使用实际 --agent、--host、--workdir，按可得信息附 --session-id。

Task Lead 用 task state T_ID --to STATUS --reason TEXT --expect-revision REV 控制整体状态；task schedule 调整共享日期。成员的部分完成写进展。状态路径为 todo → in_progress，执行中可 blocked/review，review → done；取消和重开遵循原因与状态链。项目 Lead 与 Task Lead 相对于各自对象。

## 保存、同步与失败

文档默认写本地，sync 明确 fetch/merge/push。普通文档读取的 --require-fresh 会先完整同步。JSON 检查 ok、error.code、data 和 sync；只有远端接受已核验才说明已同步。

--expect VERSION 检查对象内容，--expect-revision REV 检查控制动作来源，来自最近一次读取。一次逻辑操作使用稳定 --idempotency-key；重试沿用原 key 和完整参数。STATE_CONFLICT 先保留输入再重新读取，IDEMPOTENCY_CONFLICT 表示同 key 的内容不同，STORE_DIRTY 表示手改现场需要保留诊断。

Base init 和日常读写均不需要服务器。Mesh 首次在线克隆缓存后，--offline 明确只用本地，支持查询、创建任务和追加记录；恢复连接后显式 sync。回执提供上次同步时间、remote_revision 和 unpushed_count。网络失败保留提交；同步重试有上限。维护入口用 thread maintenance 查询，新反馈用 thread request --maintenance，--sync 明确发布；详见 feedback 主题。Profile 与说明读取不联系 Hub。
