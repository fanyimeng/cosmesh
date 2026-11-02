# 系统架构

## 默认的本地优先接入

用户电脑的 Agent 读取两份标准 Skill，通过本机 CLI 操作本地 Base、Mesh Git 工作副本、Profile 和反馈队列。Base 不依赖 Hub，Mesh 已缓存内容离线可读写，变更保留为本地提交；显式 sync 时才通过已有 SSH 登录服务器并由 Hub 核验身份、权限与候选历史。

通用安装包包含同一应用 wheel、独立安装器和固定依赖摘要锁。local-install.sh 自动选择本机 Python，缺少时在专用目录准备运行时。local_bootstrap.py 取得匹配平台的固定依赖后复用现有版本化安装器。client.py 仅在显式 client check/update 时访问稳定渠道，日常操作不检查网络或更新。

服务器账号下的 CLI 仅为现有 SSH 传输提供身份入口，由安装脚本自动准备；共享 Hub 与本地客户端独立升级。旧 SSH Skill 可选，服务器原私人资料保持原位。Mac 与服务器之间没有自动私人资料迁移。

## 从交互到持久化的调用路径

用户 → 自己的 Agent → 标准 Skill → CLI/JSON → 确定性服务 → 文档仓库。共享操作再经身份入口和 Git 传输到 Hub。Skill 教 Agent 如何使用接口；权限、版本、幂等、历史保护由程序强制执行。

cli.py 解析命令与稳定信封。service.py 管理业务规则、状态重建与视图。protocol.py 处理 Markdown/YAML 和内容版本。store.py 提供 Git 快照、显式文件提交、原子替换、锁与恢复。sync.py 管理有界同步与合并。hub.py 接受并核验候选历史。gateway.py/access.py 取得可信系统身份和传递 Git 字节流。

说明 guide.py 读取安装包内 manual/*.md；Profile 在 profile.py 的私人文件中；系统反馈 feedback.py 组合私人 outbox、专用共享缓存与现有 Update 存储。这些适配复用同一 CLI、发布和身份设施。

## 源记录、状态与快照

每个业务空间是专用 Git 仓库。schema 1 当前把文档放在 documents/ID.md，操作回执位于 .cosmesh/operations。定义与有效 Update 是事实来源，来源 revision 固定一次读取。任务参与关系、状态、日期、Inbox 与 Agenda 从记录重建。

新的本地事务先保存恢复材料，以原子写和显式路径列表生成提交。幂等键在用户和仓库内标识同一操作，版本前提阻止覆盖已变化状态。时间用于呈现；任务控制使用因果链。清除派生结果后仍能从源记录恢复。

共享 Update 被接受后不可修改；回复和更正追加新 Update。两端独立追加可以合并，双方修改同一定义或互斥状态时保留现场。预览 --dry-run 不写入实际变更。

## 系统身份如何核验

用户已通过服务器原有方式登录。Unix domain socket 使用 Linux SO_PEERCRED 或 macOS getpeereid 取得真实进程 UID，再经系统账号库解析。客户端核对服务进程的内核身份、受保护的发现配置和入口标识。环境 USER、YAML author、Git author 或客户端 --user 不能替代核验。

共享 home 的多个节点可作为一台逻辑机器，使用同一用户程序与工作副本；权威 Hub 写入在其数据所在节点执行。跨节点转交通过维护者配置的受控通道核验身份。服务器外客户端复用自己的 SSH 别名，登录现有服务器账号后调用同一身份入口。

旧专用公钥入口保持兼容并承担内部维护转交；只有显式 relay 维护密钥允许转交 UID。普通请求只传仓库与白名单操作。用户无需额外 CosMesh 注册或公钥登记。

## 仓库授权、业务责任与反馈范围

Base 私有，CosMesh 仓库及历史对全部已核验用户公开读取。原 writer 与已声明的项目协作者可按业务规则写入，Lead/Member、作者和任务接受仍由 hook 核验。私人 CosBase 仓库只有单一所有者。显示名与偏好不改变身份。

系统反馈专用仓库显式启用 system_feedback 策略，同一 Hub 已核验系统账号默认可读写；维护者可单独设只读或禁用。这个策略只作用于指定反馈仓库，普通项目和私人资料继续按原 ACL。每位作者的反馈上下文只承载自己的提交，回复通过 reply_to 跨上下文关联。

## Hub 接受与恢复

Hub 是维护者私有的 bare Git remote。受控入口只提供状态和指定 Git 操作。pre-receive 检查 main、fast-forward、全部新提交、作者、动作权限、路径、引用、格式和历史；反馈仓库另检查专用元数据与完全追加规则。

每仓库用 POSIX lockf 串行化接受。网关按连接 fork 子进程，保持进程级锁有效。接受前意向与完成日志、实际 ref 用于崩溃后对账。派生结果可重建。网络失败保留本地材料，反馈提交前先持久排队。

## 每账号自动空间

spaces.py 复用既有 SSH/gateway；Hub 根据内核核验的账号幂等准备一个个人 Mesh 仓库，初始化空的 schema 1 源配置。空间控制只有 ensure/list；不提供用户可选的隐私或空间成员机制。客户端缓存本人空间并记住活动 root，其他公开空间按需 fetch。业务普通读写不主动连接网络。
