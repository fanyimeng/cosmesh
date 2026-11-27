# 请 Agent 安装或升级 CosMesh

在你实际用来工作的 Agent（Codex、OpenCode 或 DSH）里粘贴下列一句即可。首次安装时，有 Hub 账号的人可以同时附上维护者给的安装邀请或软件源路径。使用离线包的人可以直接把下载的 ZIP 或解压目录交给 Agent。

**首次安装**

> 请在这台设备上为当前账号安装 CosMesh 和当前 Agent 使用的 cosmesh Skill。先检查平台和已有安装。有 Hub 邀请就使用我自己的连接和邀请里的软件源；我提供了离线包就使用该包；仅在本机使用时可以从 https://github.com/fanyimeng/cosmesh 的稳定版本安装。安装后验证版本和 Skill，告诉我怎样接续第一项工作。

**已有安装**

> 请把这台设备上的 CosMesh 和托管 Skill 升到正式稳定版。沿用现有安装位置、软件源和本人工作数据；如需离线包，请告诉我需要哪个。核实实际版本，并告诉我本机已绑定的项目中哪些补上了接续说明、哪些需要我处理。

## Agent 如何选择来源

| 当前环境 | 取得程序与升级方式 |
| --- | --- |
| macOS / Linux，有 Hub 邀请，Agent 在个人电脑运行 | 使用邀请里的 SSH 别名、软件目录和服务发现文件。用本人账号从软件目录取得 `install.sh`，运行下方的本人客户端安装流程。以后从已配置的稳定渠道升级。 |
| Agent 直接运行在维护者提供软件源的 Linux 服务器 | 使用本人已登录的服务器账号，在该机的软件目录运行 `python3 bootstrap.py ensure`。首次要安装 Skill，再用安装回执所示程序入口执行 `skills install --agent AGENT --only cosmesh`。以后再运行 `ensure` 检查并升级。 |
| Windows 11 x64，有 Hub 邀请 | 在软件目录读取 `stable-windows.json`。用 OpenSSH / SCP 取清单中 `archive` 指定的 ZIP，按 `archive_sha256` 核验，再用下方 PowerShell 流程。需要 64 位 CPython 3.12 和 Git for Windows。 |
| 已取得固定离线包 | 按包内 `release.json` 核对版本、操作系统、CPU 和 Python 主次版本。解压在新目录，使用本次下载包的安装器。Windows、macOS 和 Linux 的依赖 wheels 均随各自的固定包提供。 |
| 只用本机，无 Hub 账号 | 打开 [CosMesh 公开版本标签](https://github.com/fanyimeng/cosmesh/tags)，选择已发布的稳定 tag。若该版本提供匹配的官方发布附件，核验后按离线包安装。也可从固定 tag 的源码按下方步骤构建本机包。 |

### 有 Hub 的 macOS / Linux 客户端

邀请应给出本人的 SSH 别名、可读的软件目录和服务文件路径。以下示例中的 `HOST`、`AGENT_DIR`、`SERVICE_FILE` 分别替换为这些值。先把脚本下载到新的临时目录，然后运行。

```sh
scp HOST:AGENT_DIR/install.sh ./install.sh
sh ./install.sh HOST --source AGENT_DIR --service SERVICE_FILE
```

已有稳定渠道的客户端直接用原安装入口运行 `cosmesh client update --json`。新安装可用回执里的 CLI 路径执行 `cosmesh doctor --agent AGENT --json`，其中 `AGENT` 为 `codex`、`opencode` 或 `dsh`。维护者可用 `cosmesh invite prompt --mode server --agent codex` 生成带本站软件源路径的安装邀请，收件人填写本人的 SSH 别名。

### 固定 ZIP 与源码安装

在 macOS / Linux 的解压目录，用与发布包匹配的 Python 运行 `python3 install.py install --skills cosmesh`。已有安装使用新包的 `python3 install.py upgrade`。自定义程序目录沿用原来的 `--prefix`；自定义 Skill 目录沿用原安装设置。

Windows 首次安装时在解压目录运行 PowerShell。下例采用当前用户的 LocalAppData 作为程序前缀。

```powershell
$prefix = Join-Path $env:LOCALAPPDATA 'CosMesh'
py -3.12 -I -X utf8 .\install.py install --prefix "$prefix" --skills cosmesh
$cm = Join-Path $prefix 'bin\cosmesh.cmd'
& $cm --version
```

Windows 已有安装用原来的 `cosmesh.cmd`，例如在新包解压目录运行 `& $cm client update --release (Get-Location).Path`。同一条命令加 `--dry-run` 可先核对目标版本。0.7.0 的旧离线入口完成切换后，启动已安装的新版 CLI，执行 `self status` 并核对版本，然后用 `thread project-guidance --dry-run` 查看项目说明，仍有待补项时运行 `thread project-guidance`。

从 GitHub 源码安装时，检出选定的固定 tag，按该 tag 的 `requirements-release.txt` 准备专用 Python 构建环境并安装本项目。在该环境运行 `python tools/build_release.py --output dist/candidates`，再按构建回执中的目录执行本机固定包安装流程。Windows 使用 x64 CPython 3.12。以后升级可从新 tag 重复构建，再使用新包安装器。

## 安装后

核对实际 CLI 的 `--version`、`self status` 和当前 Agent 的 `doctor --agent AGENT`。向用户展示首次安装或跨版本升级回执内的 `update_notice.document`。新开 Agent 会话后，可直接说“接着做这个目录关联的工作”。完整安装说明可以用 `cosmesh guide show install` 重看。

0.7.1 起，绑定本机项目目录时会补充项目的 `AGENTS.md`。安装或跨版本升级会检查本机现有绑定，保留项目自己的说明。新会话开工先读相应 Thread，产生有意义的阶段进展时追加结论、依据和下一步。一个目录关联多项工作时，Agent 按任务选择，仍有歧义就请用户指定。可用 `cosmesh thread project-guidance --dry-run` 预览，用 `cosmesh thread project-guidance` 重试。回执显示需要处理的路径时，按各项目已有权限和约定处理。
