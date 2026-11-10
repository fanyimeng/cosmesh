# CosMesh

**以 AI Agent 为入口的本地优先协作系统，连接工作上下文、知识资料与可追溯的成果。**

[English](README.md) · [快速上手](docs/quickstart.md) · [系统架构](docs/architecture.md)

CosMesh 让人与自己的 Agent 在不同会话中接续工作。**Thread（工作串）** 保存一件工作的背景、记录、请求、决定和结果。

- **私人 Thread**：个人工作，可独立离线使用。
- **公开 Thread**：公开科研写作与协作，同一 Hub 的已核验用户可读。
- **知识库**：保存文档版本，通过引用和回链找到使用过它的工作与成果。

用户自己的 Agent 负责交互与理解，程序负责 Markdown 记录、Git 历史、确定性操作和显式同步。

## 可以做什么

- 默认快速模式：直接读取分级状态页与总览，按需展开原文。
- 用中英文短名查工作，改名后旧名仍可检索。
- 将 Thread 关联 Codex、DSH、OpenCode 的项目／工作区，跨会话继续。
- 分裂、合并工作，保留原作者、记录和来源。
- 发出请求、交付结果，沿引用核验依据。
- 默认共享工作简报；明确允许后附带说明、报告与小型结果，以固定版本引用打开。
- 统一搜索工作、记录、知识资料及旧版工作库。
- 离线读写，恢复连接后按需同步共享变更。
- 私人工作删除进回收站，共享工作撤回后保留历史与引用。

## 本地试用

当前版本 **0.4.0**。需要 Python 3.9+ 和 Git，面向 macOS / Linux。

普通用户可直接下载 [GitHub 安装包](https://github.com/fanyimeng/cosmesh/releases/tag/v0.4.0)，无需自行构建。ZIP 包支持 Apple Silicon／Python 3.9 和 Linux x86-64／Python 3.12；解压后使用匹配的 Python 运行 `python3 install.py install --skills cosmesh`。已有服务器渠道安装的用户直接对 Agent 说“升级 CosMesh”。详见[安装与升级](docs/quickstart.md#independent-user-packages--独立用户安装包)。

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

私人工作留在本地。对外请求和回复默认尝试发送；关联私人 Thread 时显著显示 **⚠️ 尚未发送**，引导确认公开当前 Thread 后再发送。公开范围为当前 Hub 用户，附件仍需明确选择。只有目标消息获服务器接受才显示已发送。

## 交给自己的 Agent

为 Codex 或 OpenCode 安装统一 CosMesh Skill：

```sh
cosmesh skills install --agent codex --only cosmesh --json
# OpenCode 使用 --agent opencode；DeepSeek Harness 使用 --agent dsh。
```

然后说：“用 CosMesh 整理当前工作，先检查已有 Thread，再建议续接或新建。”

GitHub 与服务器渠道提供独立版本化安装包，见[快速上手](docs/quickstart.md)。系统提供 CLI、统一 Skill 与独立的内存 TUI 演示。原生 Windows、私人资料跨设备同步、后台通知列入后续规划。

## 开发

```sh
python -m unittest discover -s tests -v
python demos/tui_demo.py --snapshot
```

测试使用临时空间与合成样例。运行数据、凭证和具体部署记录保留在公开源码之外。
