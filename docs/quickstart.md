# Quick start / 快速上手

Use Python 3.9+ and Git on macOS or Linux. Run commands from an activated development environment after following the README. Ordinary local operations do not contact a Hub.

## Personal work / 个人工作

```sh
cosbase thread init --json
cosbase thread create --title 'Example work' --body 'Goal, current facts, next step.' --json
cosbase thread list --json
cosbase thread context THREAD_ID --json
cosbase thread note THREAD_ID --body 'Progress or a decision.' --json
```

Replace `THREAD_ID` with the ID returned by creation. Both CLI names use the same workbench, defaulting to `~/.local/share/cosmesh/work`; `--root PATH` selects an isolated workbench. Threads start private.

用创建回执中的实际 ID 替换占位符。两份 CLI 使用同一工作台，新 Thread 默认私人；`--root` 可隔离试用数据。

## Continue and organize / 续接与整理

```sh
cosbase thread locate --workdir "$PWD" --json
cosbase thread bind THREAD_ID --agent codex --workdir "$PWD" --json
cosbase thread continue THREAD_ID --json
cosbase thread split THREAD_ID --title 'Separate work' --body 'New scope.' --json
cosbase thread merge SOURCE_ID --into TARGET_ID --json
cosbase thread delete THREAD_ID --json
cosbase thread list --lifecycle trash --json
cosbase thread restore THREAD_ID --json
```

Your agent checks existing context before creating work. Session/directory mappings stay private. Split work cites its source; merged work keeps its original records and points to the continuing Thread. Deletion means recycling in Base and withdrawal in Mesh; history remains available.

由个人 Agent 判断续接还是新建。会话和目录映射保持私人；分裂保留来源，合并给出后续入口。Base 删除进入回收站，Mesh 删除表示撤回，均保留历史。

## Collaboration / 协作

```sh
cosmesh thread discover --ssh your-hub --json
cosmesh user set-name 'Your name' --json
cosmesh thread share THREAD_ID --sync --json
cosmesh thread sync --pull --json
```

Use your own existing SSH alias and fill in your own name before the first shared write. A maintainer must configure a compatible Hub. Generic server discovery defaults to `/srv/cosmesh/service.json`; deployments may select another file through `COSMESH_SERVICE`. Public source defaults contain no specific server account or Python environment.

Mesh is readable by all authenticated users on that Hub; authorship and request rules govern writes. `--require-fresh` and `thread sync --pull` only refresh cached shared sources. `--sync` and `thread sync` publish local shared changes. Base and Profile remain private.

使用自己已有的 SSH 别名；首次共享写入前填写本人姓名。Hub 需先配置，服务发现位置可调整。读取共享内容与写入权限分别核验，私人资料不会随普通同步发布。

## Search and guidance / 检索与说明

```sh
cosmesh search query 'example work' --query 'related result' --json
cosmesh guide show search --json
cosmesh guide show threads --json
cosmesh capabilities --json
```

Your agent selects candidates, reads original sources, and follows pinned references. Offline results reflect the cached revisions. `guide list` lists the bundled manual topics; `--help` gives the installed version's command contract.

## Independent user packages / 独立用户安装包

Maintainers can build a fixed package for their current platform:

```sh
python tools/build_release.py --output dist/candidates
```

From the extracted package, install or upgrade outside the source checkout:

```sh
python3 install.py install --skills both
# Later, from a new compatible package:
python3 install.py upgrade
```

The default prefix is `~/.local/opt/cosmesh`. Use `--prefix` and `--skills-dir` for isolated previews. Versioned programs and managed Skills are separate from workbench/Profile data; compatible upgrades and uninstallation retain user data. Dependency wheels are bundled per platform. Public source publication does not set up a hosted Hub or a release-update channel.

默认程序目录与用户数据分开。维护者按平台构建、验证后再分发固定包；发布源码本身不创建托管 Hub 或更新渠道。
