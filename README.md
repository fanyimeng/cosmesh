# CosMesh

**An agent-first, local-first collaboration system connecting work context, knowledge, and traceable results.**

[简体中文](README.zh-CN.md) · [Quick start](docs/quickstart.md) · [Architecture](docs/architecture.md)

CosMesh gives people and their AI agents a durable workspace beyond a single chat. A **Thread** keeps a piece of work together: context, notes, requests, decisions, and results.

- **Private Threads** — personal work, available offline.
- **Restricted Threads** — owner-controlled access for selected Hub users.
- **Public Threads** — owner-controlled access for all authenticated users on the same Hub.
- **Knowledge library** — versioned documents with citations and backlinks to the work that used them.

Your own agent provides the conversation. CosMesh provides Markdown records, Git history, deterministic commands, and explicit synchronization.

## What you can do

- Continue existing Threads across agent sessions and working directories.
- Keep device, directory and Session associations private across devices. Owners can approve Project labels for Thread readers.
- Backfill missing associations through evidence-based, explicitly confirmed candidates.
- Split or merge work while keeping its authorship and sources.
- Send requests, deliver results, and follow references back to their evidence.
- Search Threads, records, knowledge, and legacy workspaces through one interface.
- Read and write locally; synchronize shared changes when connected.
- Recover deleted private work or withdraw shared work while preserving history.

## Try it locally

Current release: **0.4.9**. Next candidate: **0.5.0** (not yet released). Requires Python 3.9+ and Git; targets macOS and Linux.

Use your configured Hub channel to update an existing installation. GitHub publishes source and version tags; build a matching fixed package locally with `tools/build_release.py`, or use a maintainer-provided package. See [installation and upgrades](docs/quickstart.md#installation-and-rollback--安装与回退).

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

A compatible Hub must already be configured. Personal synchronization preserves the same Thread ID across devices. Choose private, restricted or public visibility explicitly. Messages live in the sender’s own Thread and require recipient access; accepted delivery and explicit read marks remain separate. Files transfer only when selected.

## Use with your agent

Install the single CosMesh Skill for Codex or OpenCode:

```sh
cosmesh skills install --agent codex --only cosmesh --json
# For OpenCode, use --agent opencode; for DeepSeek Harness, use --agent dsh.
```

Then ask: “Use CosMesh to organize this work. Check existing Threads first and suggest what to continue or create.”

For an independent user installation or upgrade, use a release package from GitHub or your Hub maintainer; see the [quick start](docs/quickstart.md). This repository provides the CLI, one standard Skill, build tools, and an optional in-memory TUI demo. Private cross-device sync, on-demand viewing, person Threads, message search and version-based change lists are available. Native Windows and background notifications remain future work.

## Optional host components

The [DSH Connector](<components/README.md>) is a separately versioned, optional Host bundle for native Project/Session creation and one initialization-only acknowledgement. It is an **independent release**, distributed separately from the Python CLI and not published to npm. Installing the CLI or standard Skill does not install or enable it. Install explicitly through DSH's plugin manager after checking compatibility; ordinary CLI/Hub use remains independent of DSH.

CosMesh 0.4.8 provides unified `component list/status/install/update/rollback` commands, independent pinned releases and resumable rollback. See the [component guide](cosmesh/manual/components.md).

## Development

```sh
python -m unittest discover -s tests -v
python demos/tui_demo.py --snapshot
```

Tests use temporary stores and synthetic fixtures. Runtime data, credentials, and deployment-specific records are kept outside the public source tree.
