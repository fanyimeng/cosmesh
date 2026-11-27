# CosMesh 0.7.1

Keep records and registered files together in a Thread. When sending a message you can give named recipients ongoing read access to that work. Recipients can browse the latest progress or open the version you sent.

## Install or update with your agent

Paste one of these requests into Codex, OpenCode or DSH on the device where you work.

> Install CosMesh and its managed skill for my account. Use my Hub invitation or the offline package I provide. For local-only use, get a stable version from https://github.com/fanyimeng/cosmesh. Check this device's platform and any existing installation, then verify the CLI version and skill.

> Update CosMesh and its managed skill to the stable release using my existing installation and software source. Verify the version, check which bound projects received the new instructions, and tell me if any project needs attention.

On macOS and Linux, use the Hub maintainer’s install script or a matching fixed package. An agent already running on the Hub’s Linux server uses that server’s bootstrap program. On Windows 11 x64, get the ZIP named in the maintainer’s `stable-windows.json`, verify the archive hash and use 64-bit CPython 3.12 and Git for Windows. The installed Windows launcher updates from an unpacked package. Local-only users can build from a published GitHub tag. [Installation tutorial](cosmesh/manual/install.md)

Starting with 0.7.1, binding a local project directory adds instructions to its `AGENTS.md`. Installation and version upgrades also check existing bindings on this device. Agents read the relevant Thread before starting work and append material progress. In a directory linked to several Threads, the agent selects the one for the task or asks which one to use. Existing project rules continue to apply. [Quick start](cosmesh/manual/upgrade.md)

## Working with a Thread

```sh
cosmesh thread find Agenda
cosmesh thread continue THREAD_ID --ready
cosmesh thread note THREAD_ID --body "First check complete"
cosmesh thread send THREAD_ID --to ACCOUNT --body "Please review the latest progress"
cosmesh thread inbox
cosmesh thread mail open MESSAGE_ID --records
cosmesh thread mail open MESSAGE_ID --at-send --files
cosmesh collect status
```

[中文说明](README.zh-CN.md) · [Quick start](cosmesh/manual/upgrade.md) · [Sharing](cosmesh/manual/sharing.md) · [Agent guide](cosmesh/skills/cosmesh/SKILL.md) · [Components](components/README.md)
