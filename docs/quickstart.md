# Quick start / 快速上手

Use Python 3.9+ and Git on macOS or Linux. Run commands from an activated development environment after following the README. Ordinary local operations do not contact a Hub.

## Personal work / 个人工作

```sh
cosmesh thread init --json
cosmesh thread create --title 'Example work' --body 'Goal, current facts, next step.' --json
cosmesh thread overview --mine --compact --json
cosmesh thread context THREAD_ID --json
cosmesh thread note THREAD_ID --body 'Progress or a decision.' --json
```

Replace `THREAD_ID` with the ID returned by creation. Both CLI names use the same workbench, defaulting to `~/.local/share/cosmesh/work`; `--root PATH` selects an isolated workbench. Threads start private.

用创建回执中的实际 ID 替换占位符。统一 CLI 使用同一工作台，新 Thread 默认私人；`--root` 可隔离试用数据。

## Continue and organize / 续接与整理

```sh
cosmesh thread locate --workdir "$PWD" --json
cosmesh thread bind THREAD_ID --agent codex --workdir "$PWD" --json
cosmesh thread continue THREAD_ID --json
cosmesh thread split THREAD_ID --title 'Separate work' --body 'New scope.' --json
cosmesh thread merge SOURCE_ID --into TARGET_ID --json
cosmesh thread delete THREAD_ID --json
cosmesh thread list --lifecycle trash --json
cosmesh thread restore THREAD_ID --json
```

Your agent checks existing context before creating work. Session/directory mappings stay private. Split work cites its source; merged work keeps its original records and points to the continuing Thread. Deletion means recycling for private work and withdrawal for public work; history remains available.

由个人 Agent 判断续接还是新建。会话和目录映射保持私人；分裂保留来源，合并给出后续入口。私人 Thread 删除进入回收站，公开 Thread 删除表示撤回，均保留历史。

## Collaboration / 协作

```sh
cosmesh thread discover --ssh your-hub --json
cosmesh user set-name 'Your name' --json
cosmesh thread share THREAD_ID --sync --json
cosmesh thread sync --pull --json
```

Use your own existing SSH alias and fill in your own name before the first shared write. A maintainer must configure a compatible Hub. Generic server discovery defaults to `/srv/cosmesh/service.json`; deployments may select another file through `COSMESH_SERVICE`. Public source defaults contain no specific server account or Python environment.

Mesh is readable by all authenticated users on that Hub; authorship and request rules govern writes. `--require-fresh` and `thread sync --pull` only refresh cached shared sources. `--sync` and `thread sync` publish local shared changes. Private Threads and Profile remain private.

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

Download a fixed package from [GitHub Releases](https://github.com/fanyimeng/cosmesh/releases/tag/v0.4.0). The ZIP packages include dependency wheels: Apple Silicon needs Python 3.9; Linux x86-64 needs Python 3.12. Git is required. Check the archive against the attached SHA256SUMS. The portable archive is for the configured Hub installer and downloads matching dependencies; it is not a standalone ZIP installer.

从 GitHub 下载适配的 ZIP 包，无需自行构建；使用与包匹配的 Python。通用 portable 包供 Hub 安装渠道使用。

Maintainers can also build a fixed package for their current platform:

```sh
python tools/build_release.py --output dist/candidates
```

From the extracted package, install or upgrade outside the source checkout:

```sh
python3 install.py install --skills cosmesh
# Later, from a new compatible package:
python3 install.py upgrade
```

The default prefix is `~/.local/opt/cosmesh`. Use `--prefix` and `--skills-dir` for isolated previews. Versioned programs and managed Skills are separate from workbench/Profile data; compatible upgrades and uninstallation retain user data. Dependency wheels are bundled per platform. Public source publication does not set up a hosted Hub or a release-update channel.

默认程序目录与用户数据分开。维护者按平台构建、验证后再分发固定包；发布源码本身不创建托管 Hub 或更新渠道。

For an existing Hub-channel installation, ask your agent to update CosMesh, or run `~/.local/opt/cosmesh/bin/cosmesh client update --json`. A manual GitHub installation upgrades using `install.py upgrade` from the next downloaded package. GitHub-only installations do not poll Releases automatically. The 0.4 update backs up and retires the managed CosBase Skill, keeps user data, and returns the important update notice. Open a new agent session afterward; reopen the notice with `cosmesh guide show upgrade`.

服务器渠道用户通过现有 Skill 请求“升级 CosMesh”；手动 GitHub 安装则下载新包并运行新包的升级命令。0.4 会备份并停用托管 CosBase Skill，保留数据，显示重要更新说明；完成后新开 Agent 会话。

## Maintenance requests / 系统维护请求

```sh
cosmesh thread maintenance --json
cosmesh thread request --maintenance --body 'Please clarify this behavior.' --sync --json
```

The Hub maintainer registers one ordinary shared Thread. Requests, replies, assignments, results, delivery and requester adoption reuse the same Thread workflow. Legacy feedback history and pending queues remain available.

维护者登记一个普通共享 Thread。用户直接向它发普通请求，后续回应、分配、结果交付和确认采用均沿用 Thread 流程；旧反馈历史和未发送项保留。

For a portable installer hosted outside the default deployment root, pass `--source /srv/team/agent`; discovery follows `/srv/team/service.json`, or use `--service` to select another file. Existing installations can use `cosmesh client check --json` and `cosmesh client update --json` when the maintainer has configured a channel.

通用安装器支持自定义服务器目录，并记住匹配的发现文件；已配置更新渠道的安装可用 client check/update 检查或更新。安装不会自动发布私人 Thread。

## DeepSeek Harness / DSH

Install the unified Skill with `cosmesh skills install --agent dsh --only cosmesh --json`. Use `cosmesh doctor --agent dsh --json` and `cosmesh guide show dsh --json` for diagnostics and guidance. With user authorization, Full access lets DSH use the same private workbench across project directories; it relaxes the session file-write sandbox while private/public Thread sharing rules remain in effect.

DSH 共用统一 Skill 和现有工作台。按用户授权推荐完全访问，使不同项目目录能使用同一份个人工作资料；该模式放开会话文件写限制，私人 Thread 默认留本地、公开需明确同意的规则继续保留。

## Help a friend install / 给朋友安装

`cosmesh invite prompt --mode server --agent dsh` generates a copyable installation prompt from public Hub entry fields. The recipient uses their own SSH account. `--mode github` instead installs a fixed source tag through the existing package builder and installer, starting with private Threads. Neither mode exports the inviter's Profile or credentials.

`profile import --file cv.pdf --dry-run --json` previews local CV/intro fields; a personal Agent can propose unstructured fields with `--fields-file`. Save confirmed changes with the existing `profile set --expect`. `profile init --interactive` offers terminal questions and a final preview/confirmation. Read `guide show onboarding` for both installation paths and identity setup.

已安装用户可以生成一段安装 prompt 转发给朋友，区分有账号接入 Mesh、无账号先用私人 Thread。CV/简介导入先预览确认；问答填写可跳过后补。原件和私人 Profile 保持本地。


Prepared local pages (`thread page --ready --json`) provide a single overview table, saved short names and introductions, progress and private agent-project/workspace associations. `--continue ID_OR_NAME` appends selected status in the same call. Markdown/SQLite derivatives are rebuildable; source records and privacy boundaries remain authoritative. Fast mode is on by default; `client mode normal` turns it off, `client mode fast` restores it. Per-call `--no-fast`/`--fast` does not change the saved preference.

`thread create --title TEXT --english TEXT --chinese TEXT --intro TEXT` saves names with the new work; `thread name ID` updates them through ordinary creator-authored records. Old aliases remain searchable, names can be ambiguous and IDs stay stable. Existing work is not renamed in bulk. `thread bind-project` stores private Codex/DSH/OpenCode project or workspace links independently of sessions; `--kind directory` explicitly represents a directory workspace when no native project exists. Explicit project onboarding directly creates or reuses suitable private work; sharing still follows the user's authorization.


Thread sharing defaults to a text brief and leaves associated files local. Only after explicit user authorization, attach selected supporting documents: `thread share ID --attach FILE --sync` (repeat `--attach`), or use a private `--materials` selection. Existing documents can be reused through fixed `cosmesh://library/K-ID@VERSION/FILENAME` references. `library open REFERENCE` verifies and materializes the file locally; `--launch` explicitly opens the system viewer. Data/archive suffixes are skipped by default, with 20 MiB per file and 100 MiB per share. Incomplete materials remain visible as partial. Pin code through `--git HTTPS_URL --commit FULL_COMMIT`; private originals, execution bindings, historical versions and unrelated knowledge drafts stay preserved. See `guide show sharing` for the bounded workflow and retry behavior.

## Fast interaction summaries

Start with prepared products; expand only when needed:

```sh
cosmesh thread page --ready --json
cosmesh thread chat --with REAL_NAME --json
cosmesh thread chat --with REAL_NAME --require-fresh --json
cosmesh thread chat --with REAL_NAME --tui
```

The conversation defaults to five rounds, with the other participant on the left and you on the right. Follow `next_cursor` using `--before` to read earlier rounds without repeating the current page. Submitting personal Agents write concise interaction summaries using `--interaction-summary`; summaries remain in the original versioned records. Prepared documents update after content changes. A latest-contact read uses one prepared-document RPC, while ordinary reads use the private local cache. Original content and attachments are read on request. See `cosmesh guide show chat`.

0.4 升级与旧 Skill 退役见 `cosmesh guide show operations`，完整更新说明见 `cosmesh guide show upgrade`。私人 Thread 发送会醒目提示尚未发送，默认引导公开当前 Thread 后重试。公开请求与回复默认尝试发送，只有 `sent=true` 才能报告已发送。
