# CosMesh

**An agent-first, local-first collaboration system connecting work context, knowledge, and traceable results.**

[简体中文](README.zh-CN.md) · [Quick start](docs/quickstart.md) · [Architecture](docs/architecture.md)

CosMesh gives people and their AI agents a durable workspace beyond a single chat. A **Thread** keeps a piece of work together: context, notes, requests, decisions, and results.

- **Private Threads** — personal work, available offline.
- **Public Threads** — collaborative work, readable by authenticated users on the same Hub.
- **Knowledge library** — versioned documents with citations and backlinks to the work that used them.

Your own agent provides the conversation. CosMesh provides Markdown records, Git history, deterministic commands, and explicit synchronization.

## What you can do

- Continue existing Threads across agent sessions and working directories.
- Split or merge work while keeping its authorship and sources.
- Send requests, deliver results, and follow references back to their evidence.
- Search Threads, records, knowledge, and legacy workspaces through one interface.
- Read and write locally; synchronize shared changes when connected.
- Recover deleted private work or withdraw shared work while preserving history.

## Try it locally

Current release: **0.4.0**. Requires Python 3.9+ and Git; targets macOS and Linux.

Download a ready-made [release package](https://github.com/fanyimeng/cosmesh/releases/tag/v0.4.0) for Apple Silicon / Python 3.9 or Linux x86-64 / Python 3.12. Extract it and run `python3 install.py install --skills cosmesh` with the matching Python. Existing Hub-channel users can ask their agent to “update CosMesh”; see [installation and upgrades](docs/quickstart.md#independent-user-packages--独立用户安装包).

For a source checkout:

```sh
git clone https://github.com/fanyimeng/cosmesh.git
cd cosmesh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

cosmesh thread init --json
cosmesh thread create --title "My first Thread" --body "Goal, context, and next step." --json
cosmesh thread overview --mine --compact --json
```

Private Threads work immediately without a Hub. For collaboration, connect using your existing SSH alias:

```sh
cosmesh thread discover --ssh your-hub --json
```

A compatible Hub must already be configured. Private work stays local. Outbound requests and replies attempt delivery automatically; a private Thread produces a prominent **NOT SENT** warning and guidance to publish it first. Publication requires your approval and makes the formal text readable to authenticated Hub users. Attachments remain opt-in.

## Use with your agent

Install the single CosMesh Skill for Codex or OpenCode:

```sh
cosmesh skills install --agent codex --only cosmesh --json
# For OpenCode, use --agent opencode; for DeepSeek Harness, use --agent dsh.
```

Then ask: “Use CosMesh to organize this work. Check existing Threads first and suggest what to continue or create.”

For an independent user installation or upgrade, use a release package from GitHub or your Hub maintainer; see the [quick start](docs/quickstart.md). This repository provides the CLI, one standard Skill, build tools, and an optional in-memory TUI demo. Native Windows, private cross-device sync, and background notifications are future work.

## Development

```sh
python -m unittest discover -s tests -v
python demos/tui_demo.py --snapshot
```

Tests use temporary stores and synthetic fixtures. Runtime data, credentials, and deployment-specific records are kept outside the public source tree.
