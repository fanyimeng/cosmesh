# DeepSeek Harness

## 服务器上的个人客户端

在服务器上运行的 DSH 使用本人 home 下的 Skill、CLI 和私人工作台，与 PC/Mac 客户端同等。软件发布源负责安装更新；默认 Hub 负责协作与私人中转；个人工作台保存本人的工作。三者可以部署在同一台服务器，配置和职责分别管理。

管理员提供实际服务发现文件后，在本人已安装的 CLI 执行 `client hub --service /actual/path/service.json --json` 保存个人客户端默认 Hub；`client hub --json` 可查看。该命令只保存私人客户端配置，保留发布源、已有工作台连接与 Hub 管理配置，不同步资料。首次工作台接入用 `thread discover`，已有连接的工作台保持原目标；个人共享仍须显式开启。显式 `--ssh`、`--service` 和 `COSMESH_SERVICE` 优先于客户端默认值。

个人 Agent 使用 `thread`、`personal`、`library` 等客户端命令，Hub 管理命令使用独立的 `hub ... --hub-root` 入口。若个人账号同时拥有 Hub 管理目录，同账号进程仍共享操作系统权限；角色分工不构成额外权限隔离。

## 安装与更新

DSH 与 Codex、OpenCode 共用同一套 CosMesh 程序、工作台与一份标准 Skill。Skill 放在 ~/.agents/skills/cosmesh，DSH 的标准预设会自动发现；项目内同名 Skill 的优先级更高。

已安装 CLI 后运行 skills install --agent dsh --only cosmesh --json；doctor --agent dsh --json 检查当前程序和对应 Skill。Mac 桌面版自带的 DSH CLI 即使未加入 PATH，也能被检查识别。程序更新仍使用既有 client update 或服务器安装入口，保留用户修改过的 Skill 和已有资料。

## 完全访问

推荐在用户授权后为 DSH 会话选择 Full access（完全访问），方便在不同项目目录共用个人工作资料。这会放开该会话的本机文件写限制；具体工作继续按用户要求执行，Thread 默认私人，明确分享后公开。

CLI 启动需要读写 ~/.local/opt/cosmesh/.lock；工作台、Git 锁和 Mesh 缓存需要读写 ~/.local/share/cosmesh。私人 Profile 按需使用 ~/.config/cosmesh。工作区模式下，这些目录可能位于允许范围外，所以查询命令也可能返回 INSTALL_IO_ERROR。

遇到明确指向这些路径的工作区拒绝时，说明原因并沿用当前用户的授权，在 DSH 中选择或请求完全访问，再以原参数及幂等键重试一次。若仍失败，保留输入并检查实际错误；不把所有 IO 错误都归为沙箱问题。安装和更新还会写程序及托管 Skill 目录。

## 日常使用

向 DSH 说明“用 CosMesh 整理这项工作，先查已有 Thread”；Agent 读取 Skill 后通过本机绝对路径调用 CLI。新会话先检索和读取已有工作，同一事项继续原 Thread。按实际信息登记 agent=dsh、host、workdir；Session ID 取得到才填写。

自然语言查找由当前个人 Agent 展开问法、沿回链核验原文。共享请求、回应、结果与交付沿用普通 Thread；无需新的公共模型或数据格式。读取最新 Mesh 使用 --require-fresh 或 thread sync --pull，明确发送或交付时才发布。

## 核验范围

安装器或 doctor 确认文件、入口和版本；DSH 的实际发现、加载与执行须在 DSH 内检查。权限设置用于未来会话时，已存在会话仍保留自身模式；恢复旧会话时检查当前模式。验收可在临时工作台记录、查询和跨会话续接，真实 Mesh 只读核验与请求预览保持业务空间整洁。
