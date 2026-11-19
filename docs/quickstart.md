# Quick start / 快速上手

This guide describes CosMesh 0.4.7. Programs and existing user data remain separate. Use Python 3.9+ and Git on macOS or Linux; use an isolated `--root` and install prefix when trying a candidate.

## Personal work / 本人工作

```sh
cosmesh thread init --json
cosmesh thread create --title 'Example work' --body 'Goal and next step.' --json
cosmesh thread page --ready --json
cosmesh thread update THREAD_ID --body 'New progress.' --json
cosmesh thread bind-project THREAD_ID --agent codex --kind directory --workdir "$PWD" --json
```

Reuse known Thread IDs. A Thread has one owner. Owners can preview Agent and Project display labels with `thread associate ID` and approve them with `thread access ID --visibility public --share-projects` (or restricted visibility with named readers). Approval is pinned to the publication. Device identifiers, hostnames, native Project IDs, workdirs and Sessions stay in the owner view. Ordinary local work is available offline. The default data root stays `~/.local/share/cosmesh/work` for compatibility.

## Same Thread on another device / 跨设备接续

Configure your own default Hub using `client hub --service SERVICE_FILE`. Reuse the deployment's existing authentication; the software distribution source, Hub service and personal client are separate roles.

```sh
cosmesh thread personal THREAD_ID --json
cosmesh personal asset add THREAD_ID FILE --as RELATIVE_PATH --json
cosmesh thread sync THREAD_ID --files --json
cosmesh thread fetch THREAD_ID --workdir DIRECTORY --agent AGENT --files --json
cosmesh thread locations THREAD_ID --json
```

Only selected files transfer. Recovery retains the original Thread ID and previous associations. Existing different files are preserved and reported as conflicts.

## Visibility and messages / 查看权限与消息

```sh
cosmesh thread access THREAD_ID --visibility restricted --to USER --json
cosmesh thread access THREAD_ID --visibility public --json
cosmesh thread browse THREAD_ID --records --json
cosmesh thread send OWN_THREAD --to USER --body 'Message text.' --json
cosmesh thread reply MESSAGE_ID --thread OWN_THREAD --body 'Reply text.' --json
cosmesh thread inbox --json
cosmesh thread mail show MESSAGE_ID --json
cosmesh thread mail mark MESSAGE_ID --seen --json
cosmesh thread track --json
```

Private / restricted / public determines who can view. Readers browse online and download only on request. Messages require source visibility and live in the sender's own Thread. A new received message creates a private person Thread only when no registration or name collision exists; another device restores the same ID. A matching name alone never merges two identities. Lists and searches do not mark messages read.

## Search and changes / 索引与增量

```sh
cosmesh thread search 'search text' --json
cosmesh thread search 'search text' --messages --with USER --json
cosmesh thread browse THREAD_ID --changes PREVIOUS_REVISION --json
cosmesh thread browse THREAD_ID --snapshot REVISION --record RECORD_ID --version VERSION --json
cosmesh library add --title 'Reusable material' --file FILE --thread THREAD_ID --sync --json
```

Search checks current access and returns fixed sources. Changes list only changed records/files; pin the returned revision on later pages. Library inherits current source permissions across all versions. Personal limits are 5 GB/file, 10 GB/batch, 30 GB/account; outward limits are 2 GB/file, 5 GB/batch, 15 GB/Thread including history. GB is decimal. Credential/path filters remain; science data and archives are permitted as stored originals.

## Installation and rollback / 安装与回退

Use a fixed released package supplied by the maintainer. The source checkout does not itself deploy a Hub or an update channel. From a compatible extracted package:

```sh
python3 install.py install --skills cosmesh
python3 install.py upgrade
cosmesh self status --json
cosmesh self rollback --version PREVIOUS_VERSION --json
```

Use `--prefix` and `--skills-dir` for an isolated preview. Installed programs, managed Skills and user data remain separate. Upgrades replace managed guidance and retain customized files under the existing conflict policy. Open a new Agent session after upgrading.

Administrators migrate old public Threads only after stopping every gateway, reviewing `hub migrate-threads --hub-root PATH`, then explicitly using `--apply --gateways-stopped`. Original data and a separate backup remain. Format-1 Hub runtimes refuse migrated data; 0.4.6 preserves ACLs but lacks the new message/search interfaces. Returning to 0.4.7 resumes them. See `guide show operations` and `guide show upgrade`.
