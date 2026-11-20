# DeepSeek Harness

## 服务器上的个人客户端

在服务器上运行的 DSH 使用本人 home 下的 Skill、CLI 和本地工作记录，与 PC/Mac 客户端同等。软件发布源负责安装更新；默认 Hub 负责协作与私人中转；个人工作库保存本人的工作。三者可以部署在同一台服务器，配置和职责分别管理。

管理员提供实际服务发现文件后，在本人已安装的 CLI 执行 `client hub --service /actual/path/service.json --json` 保存个人客户端默认 Hub；`client hub --json` 可查看。该命令只保存私人客户端配置，保留发布源、已有工作库连接与 Hub 管理配置，不同步资料。首次工作库接入用 `thread discover`，已有连接的工作库保持原目标；个人共享仍须显式开启。显式 `--ssh`、`--service` 和 `COSMESH_SERVICE` 优先于客户端默认值。

个人 Agent 使用 `thread`、`personal`、`library` 等客户端命令，Hub 管理命令使用独立的 `hub ... --hub-root` 入口。若个人账号同时拥有 Hub 管理目录，同账号进程仍共享操作系统权限；角色分工不构成额外权限隔离。

## 安装与更新

DSH 与 Codex、OpenCode 共用同一套 CosMesh 程序、工作库与一份标准 Skill。Skill 放在 ~/.agents/skills/cosmesh，DSH 的标准预设会自动发现；项目内同名 Skill 的优先级更高。

已安装 CLI 后运行 skills install --agent dsh --only cosmesh --json；doctor --agent dsh --json 检查当前程序和对应 Skill。Mac 桌面版自带的 DSH CLI 即使未加入 PATH，也能被检查识别。程序更新仍使用既有 client update 或服务器安装入口，保留用户修改过的 Skill 和已有资料。

## 可选 DSH Connector（独立组件）

0.4.8 提供统一入口：component list/status/install/update/rollback。安装或更新前完成初始化并退出目标宿主，然后用 component install dsh --host-stopped；启动 DSH 后核对运行版本。详见 guide show components。0.4.7 及更早客户端可沿下述宿主插件流程操作。

标准 Skill 让 Agent 使用 CosMesh CLI；原生项目／新会话自动创建由独立可选组件 `@cosmesh/dsh-connector` 提供。两者不互相替代。组件目前位于源码项目的 `components/dsh-connector/`，独立版本；源码已独立发布，未发布到 npm；使用 component install 显式安装，不随 CLI、pip 或 skills install 自动启用。

适配边界为 DSH 0.2.2-rc.3、CosMesh CLI 0.4.7+。组件复用本人已经安装的 CLI；不自带Python环境，业务仍需CLI可用。默认入口为本人home下 `.local/opt/cosmesh/bin/cosmesh`，自定义安装须由插件配置提供绝对cliPath，不通过模型工具参数接受任意命令。

源码用户执行 `python3 tools/build_component.py dsh-connector` 生成固定候选，回执给出 install_directory。用户明确授权后，在 DSH Plugins → Add plugin 选择该目录，或由 DSH Agent 经官方 plugin_manager 安装。不要手工修改profile、聊天数据库或认证信息。插件在Host进程中运行，影响同profile所有会话；管理器application/warnings决定是否需重启，安装完成不等于界面和业务已验收。完整分层、安装、回退说明见源码 components/README.md。

工具 cosmesh_open_work 连接已有私人Thread与明确目录，复用同目录Project，每个operation_key创建一个Session，默认发送一次初始化消息后等待。初始化会调用配置模型，所选私人接续背景进入请求；不是CosMesh公开／文件上传。工具guard禁止该初始化轮执行任何工具。用户另行明确下达实际任务后才工作。initialize:false只创建空白会话，DSH在未选中时隐藏它；沿原key和创建参数改为true可稍后初始化。新key表示另开会话，不能用于绕过失败。

同key重试恢复同一次创建，不是任意旧会话续接。已有Project不会被自动改名，不自动建Thread、同步、远程SSH或开展研究。回执必须区分accepted和completed；超时保留原ID与文本，不能重复发起。运行时guard在崩溃或卸载后不提供持久隔离，初始化中不要升级／卸载插件。

旧本机原型包名为 `@local/cosmesh-dsh-bridge`，与新组件注册同名工具，不能同时启用。迁移前等待初始化结束，保留原目录和journal；通过DSH管理器移除旧包、安装新包，保持原namespace（默认desktop）、主机、workdir、operation_key及创建参数。旧 `.cosmesh-dsh` journal兼容，不删除项目／会话／关联。回退通过管理器移除新包、重装保留的旧包；不删除资料。安装与升级须明确选择新版本目录，保留旧包回退。

## 完全访问

推荐在用户授权后为 DSH 会话选择 Full access（完全访问），方便在不同项目目录共用个人工作资料。这会放开该会话的本机文件写限制；具体工作继续按用户要求执行，Thread 默认私人，明确分享后公开。

CLI 启动需要读写 ~/.local/opt/cosmesh/.lock；工作库、Git 锁和 Mesh 缓存需要读写 ~/.local/share/cosmesh。私人 Profile 按需使用 ~/.config/cosmesh。工作区模式下，这些目录可能位于允许范围外，所以查询命令也可能返回 INSTALL_IO_ERROR。

遇到明确指向这些路径的工作区拒绝时，说明原因并沿用当前用户的授权，在 DSH 中选择或请求完全访问，再以原参数及幂等键重试一次。若仍失败，保留输入并检查实际错误；不把所有 IO 错误都归为沙箱问题。安装和更新还会写程序及托管 Skill 目录。

## 日常使用

向 DSH 说明“用 CosMesh 整理这项工作，先查已有 Thread”；Agent 读取 Skill 后通过本机绝对路径调用 CLI。新会话先检索和读取已有工作，同一事项继续原 Thread。按实际信息登记 agent=dsh、host、workdir；Session ID 取得到才填写。

自然语言查找由当前个人 Agent 展开问法、沿回链核验原文。用 thread find 查本人工作，thread fetch ID 接续。对外用 thread send 选择内容和收件人；用 thread inbox 查看来信。DSH 可提交交接摘要，收集器只读取已登记的目录和摘要文件。

## 核验范围

安装器或 doctor 确认文件、入口和版本；DSH 的实际发现、加载与执行须在 DSH 内检查。权限设置用于未来会话时，已存在会话仍保留自身模式；恢复旧会话时检查当前模式。验收可在临时工作库记录、查询和跨会话续接，真实 Mesh 只读核验与请求预览保持业务空间整洁。
