# CosMesh

**以 AI Agent 为入口的本地优先协作系统，连接工作上下文、知识资料与可追溯的成果。**

[English](README.md) · [快速上手](docs/quickstart.md) · [系统架构](docs/architecture.md)

CosMesh 让人与自己的 Agent 在不同会话中接续工作。**Thread（工作串）** 保存一件工作的背景、记录、请求、决定和结果。

- **CosBase**：私人工作台，可独立离线使用。
- **CosMesh**：共享协作，同一 Hub 的已核验用户可以读取公开工作。
- **知识库**：保存文档版本，通过引用和回链找到使用过它的工作与成果。

用户自己的 Agent 负责交互与理解，程序负责 Markdown 记录、Git 历史、确定性操作和显式同步。

## 可以做什么

- 跨 Agent 会话和工作目录续接已有 Thread。
- 分裂、合并工作，保留原作者、记录和来源。
- 发出请求、交付结果，沿引用核验依据。
- 统一搜索工作、记录、知识资料及旧版工作库。
- 离线读写，恢复连接后按需同步共享变更。
- 私人工作删除进回收站，共享工作撤回后保留历史与引用。

## 本地试用

当前版本 **0.3.14**。需要 Python 3.9+ 和 Git，面向 macOS / Linux。

```sh
git clone https://github.com/fanyimeng/cosmesh.git
cd cosmesh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

cosbase thread init --json
cosbase thread create --title '我的第一条工作' --body '目标、背景和下一步。' --json
cosbase thread list --json
```

Base 无需服务器即可使用。需要协作时，用本人已有的 SSH 别名连接已配置的兼容 Hub：

```sh
cosmesh thread discover --ssh your-hub --json
```

默认写入本地，明确使用 `--sync` 或 `thread sync` 才发布。分享后，选定 Thread 对该 Hub 的已核验用户公开。

## 交给自己的 Agent

为 Codex 或 OpenCode 安装两份标准 Skill：

```sh
cosmesh skills install --agent codex --only both --json
# OpenCode 使用 --agent opencode；DeepSeek Harness 使用 --agent dsh。
```

然后说：“用 CosBase 整理当前工作，先检查已有 Thread，再建议续接或新建。”

维护者可构建独立版本化安装包，让用户体验安装和升级，见[快速上手](docs/quickstart.md)。当前公开的是源码预览版，提供 CLI 与独立的内存 TUI 演示。原生 Windows、私人资料跨设备同步、后台通知列入后续规划。

## 开发

```sh
python -m unittest discover -s tests -v
python demos/tui_demo.py --snapshot
```

测试使用临时空间与合成样例。运行数据、凭证和具体部署记录保留在公开源码之外。
