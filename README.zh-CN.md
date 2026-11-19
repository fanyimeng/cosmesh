# CosMesh

**以 AI Agent 为入口的本地优先协作系统，连接工作上下文、知识资料与可追溯的成果。**

[English](README.md) · [快速上手](docs/quickstart.md) · [系统架构](docs/architecture.md)

CosMesh 让人与自己的 Agent 在不同会话中接续工作。**Thread（工作串）** 保存一件工作的背景、记录、请求、决定和结果。

- **私人 Thread**：个人工作，可独立离线使用。
- **指定可见 Thread**：所有者授予一个或多个指定用户查看权限。
- **全员可见 Thread**：同一 Hub 的已核验用户可读，所有者保持唯一。
- **知识库**：保存文档版本，通过引用和回链找到使用过它的工作与成果。

用户自己的 Agent 负责交互与理解，程序负责 Markdown 记录、Git 历史、确定性操作和显式同步。

## 可以做什么

- 默认快速模式：直接读取分级状态页与总览，按需展开原文。
- 用中英文短名查工作，改名后旧名仍可检索。
- 将 Thread 关联 Codex、DSH、OpenCode 的项目／工作区，跨会话继续。
- 本人跨设备保留 Project、目录和 Session，可单独批准供读者查看的 Project 显示名称。
- 通过可解释证据和明确确认为旧 Thread 补充关联，重复确认保持幂等。
- 分裂、合并工作，保留原作者、记录和来源。
- 发出请求、交付结果，沿引用核验依据。
- 按选定范围提供完整 Thread 记录与历史，文件明确选择后传输，固定版本按需打开。
- 统一搜索工作、记录、知识资料及旧版工作库。
- 离线读写，恢复连接后按需同步共享变更。
- 私人工作删除进回收站，共享工作撤回后保留历史与引用。

## 本地试用

当前正式版本 **0.4.9**；下一候选 **0.5.0**（尚未发布）。需要 Python 3.9+ 和 Git，面向 macOS / Linux。

已有服务器渠道安装的用户可对 Agent 说“升级 CosMesh”。GitHub 提供源码和版本标签；可用 `tools/build_release.py` 构建匹配本机的固定包，或使用维护者分发的包。详见[安装与升级](docs/quickstart.md#installation-and-rollback--安装与回退)。

源码试用：

```sh
git clone https://github.com/fanyimeng/cosmesh.git
cd cosmesh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

cosmesh thread init --json
cosmesh thread create --title '我的第一条工作' --body '目标、背景和下一步。' --json
cosmesh thread overview --mine --compact --json
```

私人 Thread 无需服务器即可使用。需要协作时，用本人已有的 SSH 别名连接已配置的兼容 Hub：

```sh
cosmesh thread discover --ssh your-hub --json
```

个人同步让同一 Thread 跨设备接续。查看范围为私有／指定可见／全员可见；消息写在发送者自己的 Thread 中，发送前核验接收者权限。只有目标消息获服务器接受才显示已发送，已读需明确标记。文件仅明确选择才传输。

## 交给自己的 Agent

为 Codex 或 OpenCode 安装统一 CosMesh Skill：

```sh
cosmesh skills install --agent codex --only cosmesh --json
# OpenCode 使用 --agent opencode；DeepSeek Harness 使用 --agent dsh。
```

然后说：“用 CosMesh 整理当前工作，先检查已有 Thread，再建议续接或新建。”

GitHub 与服务器渠道提供独立版本化安装包，见[快速上手](docs/quickstart.md)。系统提供 CLI、统一 Skill 与独立的内存 TUI 演示。已支持同一 Thread 跨设备恢复、按人收信、全文检索和版本增量；原生 Windows 与后台通知留后续。

## 可选宿主组件

[DSH Connector](<components/README.md>) 是独立版本的可选 Host 组件，提供原生项目创建／复用、新会话创建、默认初始化确认后等待。当前 **0.1.1 随源码独立发布**，未发布到 npm，与 Python 安装包分开发行。安装 CLI 或标准 Skill 不会自动安装／启用连接器；核验兼容后，明确通过 DSH 插件管理器安装。普通 CLI／Hub 使用不依赖 DSH。

0.4.8 提供统一 `component list/status/install/update/rollback` 入口；独立兼容清单和固定包支持按需更新，旧版及资料保留。详见[组件管理](cosmesh/manual/components.md)。

## 开发

```sh
python -m unittest discover -s tests -v
python demos/tui_demo.py --snapshot
```

测试使用临时空间与合成样例。运行数据、凭证和具体部署记录保留在公开源码之外。
