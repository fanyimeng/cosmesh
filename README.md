# CosMesh

**An agent-first, local-first collaboration system connecting work context, knowledge, and traceable results.**

[简体中文](README.zh-CN.md) · [Quick start](docs/quickstart.md) · [Architecture](docs/architecture.md)

CosMesh gives people and their AI agents a durable workspace beyond a single chat. A **Thread** keeps a piece of work together: context, notes, requests, decisions, and results.

- **CosBase** — private personal work, available offline.
- **CosMesh** — shared work, readable by authenticated users on the same Hub.
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

Current release **0.3.26**. Requires Python 3.9+ and Git; targets macOS and Linux.

```sh
git clone https://github.com/fanyimeng/cosmesh.git
cd cosmesh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

cosbase thread init --json
cosbase thread create --title "My first Thread" --body "Goal, context, and next step." --json
cosbase thread overview --mine --compact --json
```

Base works immediately without a Hub. For collaboration, connect using your existing SSH alias:

```sh
cosmesh thread discover --ssh your-hub --json
```

A compatible Hub must already be configured. Writes stay local until you explicitly use `--sync` or `thread sync`. Sharing publishes the selected Thread to the Hub's authenticated users.

## Use with your agent

Install the two standard Skills for Codex or OpenCode:

```sh
cosmesh skills install --agent codex --only both --json
# For OpenCode, use --agent opencode; for DeepSeek Harness, use --agent dsh.
```

Then ask: “Use CosBase to organize this work. Check existing Threads first and suggest what to continue or create.”

For an independent user installation or upgrade, use a verified release package from your Hub maintainer; see the [quick start](docs/quickstart.md). This repository provides the CLI, standard Skills, build tools, and an optional in-memory TUI demo. Native Windows, private cross-device sync, and background notifications are future work.

## Development

```sh
python -m unittest discover -s tests -v
python demos/tui_demo.py --snapshot
```

Tests use temporary stores and synthetic fixtures. Runtime data, credentials, and deployment-specific records are kept outside the public source tree.
