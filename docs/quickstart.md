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

## Maintenance requests / 系统维护请求

```sh
cosmesh thread maintenance --json
cosmesh thread request --maintenance --body 'Please clarify this behavior.' --sync --json
```

The Hub maintainer registers one ordinary shared Thread. Requests, replies, assignments, results, delivery and requester adoption reuse the same Thread workflow. Legacy feedback history and pending queues remain available.

维护者登记一个普通共享 Thread。用户直接向它发普通请求，后续回应、分配、结果交付和确认采用均沿用 Thread 流程；旧反馈历史和未发送项保留。

For a portable installer hosted outside the default deployment root, pass `--source /srv/team/agent`; discovery follows `/srv/team/service.json`, or use `--service` to select another file. Existing installations can use `cosmesh client check --json` and `cosmesh client update --json` when the maintainer has configured a channel.

通用安装器支持自定义服务器目录，并记住匹配的发现文件；已配置更新渠道的安装可用 client check/update 检查或更新。安装不会自动发布私人 Base。

## DeepSeek Harness / DSH

Install the same two Skills with `cosmesh skills install --agent dsh --only both --json`. Use `cosmesh doctor --agent dsh --json` and `cosmesh guide show dsh --json` for diagnostics and guidance. With user authorization, Full access lets DSH use the same private workbench across project directories; it relaxes the session file-write sandbox while Base/Mesh sharing rules remain in effect.

DSH 共用两份 Skill 和现有工作台。按用户授权推荐完全访问，使不同项目目录能使用同一份个人工作资料；该模式放开会话文件写限制，Base 默认私有、Mesh 明确分享的规则继续保留。

## Help a friend install / 给朋友安装

`cosbase invite prompt --mode server --agent dsh` generates a copyable installation prompt from public Hub entry fields. The recipient uses their own SSH account. `--mode github` instead installs a fixed source tag through the existing package builder and installer, starting with private Base. Neither mode exports the inviter's Profile or credentials.

`profile import --file cv.pdf --dry-run --json` previews local CV/intro fields; a personal Agent can propose unstructured fields with `--fields-file`. Save confirmed changes with the existing `profile set --expect`. `profile init --interactive` offers terminal questions and a final preview/confirmation. Read `guide show onboarding` for both installation paths and identity setup.

已安装用户可以生成一段安装 prompt 转发给朋友，区分有账号接入 Mesh、无账号先用私人 Base。CV/简介导入先预览确认；问答填写可跳过后补。原件和私人 Profile 保持本地。
