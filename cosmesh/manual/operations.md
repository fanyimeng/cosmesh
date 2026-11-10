# 安装、升级、部署与排查

## 本地源码与用户安装

源码试用需要 Python 3.9+ 与 Git：在虚拟环境执行 python -m pip install -e .，用 thread init 初始化私人工作台。统一 Skill 可通过 skills install --agent codex --only cosmesh 安装；OpenCode 使用 --agent opencode，DeepSeek Harness 使用 --agent dsh。DSH 的完全访问与核验流程见 dsh 主题。以宿主实际发现和调用结果判断是否可用。

维护者用 tools/build_release.py 构建当前平台的固定包。用户解压后运行 python3 install.py install --skills cosmesh，默认程序位于 ~/.local/opt/cosmesh，统一 Skill 位于 ~/.agents/skills。--prefix 和 --skills-dir 可选择独立预览位置。

新固定包使用 install.py upgrade 升级；self status 查询安装状态，self rollback 选择兼容旧版本，self uninstall 卸载程序并保留用户数据。用户修改过的 Skill 保留并报告冲突。程序版本、用户文档、Profile 和反馈队列分别保存。

## 自托管 Hub

Hub 由维护者配置兼容程序、受控身份入口、服务发现和共享存储。用户用自己已有的 SSH 别名执行 thread discover --ssh ALIAS；首次共享写入前本人填写姓名。默认服务发现位置为 /srv/cosmesh/service.json；COSMESH_SERVICE 可选择其他路径。

私人 Thread 可独立离线运行；已缓存的公开 Thread 可离线读写。request/reply/deliver 等发送动作默认尝试提交，--local-only 保存草稿；其他变更用 --sync 或 thread sync 发布，thread sync --pull 和 --require-fresh 只拉取。Mesh 对该 Hub 的所有已核验用户公开读取，原作者和请求动作权限继续约束写入。

发布工具的 --root、网关工具的 --base 和安装脚本的 --source/--bootstrap 可配置部署目录。远端引导程序使用服务器 PATH 中的 python3，其版本应匹配所发布的 Linux 包。发布目录可读，Hub 数据与维护控制入口由维护者管理；共享 home 的节点共用同一用户程序与数据，并通过 POSIX lockf 协调写入。

## 按需更新

配置了维护者发布渠道后，client check 查询可用版本，client update 在明确要求时更新程序及未修改的托管 Skill。渠道、安装物和 Hub 独立维护；源码仓库本身不提供已部署的 Hub。日常业务不检查更新，无后台自动升级。

## 故障排查

先读 capabilities、self status、whoami、doctor 及原错误 code。系统说明离线可用。网络失败时保留本地提交，核对已有 SSH 连接、服务发现配置与 Hub 运行状态。

AUTH_DENIED 检查已核验账号和权限。STATE_CONFLICT 刷新 context 并保留原输入。STORE_DIRTY 保留手工修改与恢复日志。IDEMPOTENCY_CONFLICT 要核对同键输入，避免换键重复写入。

系统意见使用 thread request --maintenance 保存普通请求，默认尝试提交到维护 Thread；维护者登记方法见 feedback 主题。旧 feedback 队列保留兼容。未核验远端接受时，只说明已存本地。具体命令以当前 --help 和 guide 为准。

## 备份与恢复

备份覆盖 Hub Git、身份策略、服务发现、受控附件、用户源仓库及私人配置，并保留一致快照。恢复前保全备份以后接受的新记录。生命周期或格式升级需要兼容程序；版本回滚与数据恢复分别核验。固定发布物按版本保存，不覆盖重发。

## 0.4 统一入口升级

0.4 只安装 cosmesh Skill，原 cosbase 命令保留兼容。跨版本升级使用正常 `client update --json` 或新包的 `python3 install.py upgrade --prefix PREFIX`；0.3 自带的 self upgrade 安装器不具备 Skill 退役能力，应使用新包安装器。开发候选不进入 stable 渠道。

托管 cosbase Skill 自动备份并停用；有自定义内容时备份完整资源，在回执报告位置，按需合并。用户文档、私人 Thread、Profile 与加密字段、项目／Session 绑定不搬迁。回退恢复旧 Skill，升级中断自动恢复原入口。未托管或符号链接副本保留并报告；新开 Agent 会话加载统一 Skill。
