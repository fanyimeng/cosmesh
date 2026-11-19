# Optional host components / 可选宿主组件

CosMesh owns the stable Thread and its private execution-location records. A host connector performs native application operations through that host's supported APIs. Installing the ordinary Python CLI or Skill does **not** install a host connector, change a host profile, or start a model.

CosMesh 保存工作身份与私人执行关联；连接器通过宿主接口操作原生项目和会话。组件是可选项，独立版本化。DSH Connector 0.1.2 随源码独立发布；未发布到 npm，不随 `client update` 自动启用。

| Component | Implementation | Compatibility | State |
|---|---|---|---|
| DSH Connector | [Installable bundle](<dsh-connector/README.md>) | CosMesh CLI 0.4.7+; DSH 0.2.2-rc.3 | Released 0.1.2; explicit installation |

## Build and install / 构建与安装

CosMesh 0.4.8 provides `cosmesh component list/status/install/update/rollback` as the unified entry point. See the [component guide](<../cosmesh/manual/components.md>). Installation remains explicit and uses DSH's official CLI after the target host has stopped. The installed CLI does not carry or automatically activate Node bundles.

The packaged [catalog](<../cosmesh/resources/components.json>) is the canonical compatibility baseline. A software release source can publish the same format at `components/catalog.json` with new pinned versions. Existing version digests and compatibility requirements remain immutable. This permits connector updates independently of a Python CLI release. Fixed ZIPs live at `components/dsh-connector-VERSION/bundle.zip` on that source.

From the source checkout, with Python 3.9+:

```sh
python3 tools/build_component.py --list
python3 tools/build_component.py dsh-connector
```

The builder creates a new versioned directory under `dist/components/` containing:

- `bundle/`: the self-contained DSH plugin directory;
- `bundle.zip`: the same resources, deterministic archive ordering/timestamps;
- `manifest.json`: resource sizes and SHA-256 checksums.

It reads only the selected component's declared package resources; it does not download dependencies, execute package scripts, read Thread data, install plugins or publish. Existing candidate directories are never overwritten. A failed partial build is preserved for inspection; use a different output directory for a new build attempt.

在 DSH 的 **Plugins → Add plugin** 中选择构建回执的 `install_directory`；或者明确授权 DSH Agent 使用官方 `plugin_manager` 安装该绝对目录。安装影响当前 profile 的所有会话。检查管理器回执：只有 `application: applied` 才表示本次已应用；若要求重启，按回执处理，不能仅凭源码更新宣称生效。不要手工改 DSH profile、数据库或认证信息。

From inside DSH, an authorized Agent can install it with:

```text
plugin_manager({action: "install_bundle", target: "<absolute install_directory>"})
```

`@cosmesh/dsh-connector` is the package name, **not a claim that it is available from a public registry**. Directory installation is supported; unzip a candidate first rather than assuming DSH accepts the ZIP file itself.

## Architecture / 分层

- [Catalog](<../cosmesh/resources/components.json>): the source component list, not a background service or remote registry.
- [Host-independent work-link module](<dsh-connector/lib/work-link.js>): host-independent input validation, idempotency journal and step orchestration. The adapter supplies native operations, records and initialization content; no DSH imports belong here.
- [CosMesh adapter](<dsh-connector/lib/cosmesh.js>): bounded CLI calls, private Thread validation and existing binding/update commands. No new Thread data format or Hub endpoint.
- [DSH adapter](<dsh-connector/lib/dsh.js>): native Workspace/Session operations, durability, initialization admission and target-scoped tool guards.
- [Tool registration](<dsh-connector/index.js>): supported Agent tool and permission gate.

This is a small reusable boundary inside a self-contained component, not a new general plugin platform. Python CLI and Hub remain independent of Node/DSH. Other host connectors require their own supported APIs and verification; no Codex/OpenCode automatic project-creation support is implied.

仓库目前未指定开源许可证；本组件不另行附加 MIT 等许可。构建不联网，不代表 DSH 的整个 profile 安装操作在任何环境都能离线完成。

## Existing local prototype / 旧原型迁移

If `@local/cosmesh-dsh-bridge` is installed, finish any initialization turn first. Keep its source directory and all project journals for rollback. Through the official DSH plugin manager, remove the old bundle before installing this connector: both register `cosmesh_open_work`, so they must not be enabled together.

Install each version into its own fixed directory. A linked installation reads that directory directly: preserve the previous bundle for rollback and never modify an active bundle in place. Version 0.1.1 keeps operation journals, existing Projects, Sessions and private bindings. Its ordinary Thread receipts contain only a concise result; execution coordinates remain in private bindings and the local journal.

Keep the old `namespace` (default `desktop`), host, target workdir, `operation_key` and original creation arguments. The connector retains the legacy `.cosmesh-dsh` journal location and creation fingerprint. A same-key retry resumes that operation rather than creating a replacement Session. Do not change keys to bypass partial results or stale locks.

To roll back, remove the connector and reinstall the retained previous bundle using DSH's manager. Removing either software bundle does not delete Projects, Sessions, Thread bindings or journals. If initialization is running, wait until it ends before replacing the plugin: runtime guards are not a durable security boundary across unload/crash.

## Semantics and limits / 行为边界

- Explicitly requested new work: create/reuse the native Project for a canonical directory, create one Session per operation key, bind to the same Thread, then initialize and wait by default.
- Same operation key: recover the same creation operation; this is **not** an arbitrary old-conversation resume command.
- `initialize:false`: create only; DSH hides unselected blank Sessions. Change only this flag to true, keeping the original creation arguments, to initialize later.
- “Continue this Thread” alone does not authorize opening another Session. Inspect existing bindings and clarify ambiguous targets. Project-only attachment and arbitrary existing-Session resume remain native-host/Agent operations, not additional connector modes in this first version.
- No implicit remote execution, SSH, synchronization, access changes, file upload or research. Initialization is a real model request: the selected private background text enters the configured model's context.
- Acceptance is not completion. Report partial/timeout/failure truthfully; retries reuse the same request ID. Crashes can require manual recovery and must not be advertised as exactly-once completion.

## Development checks

```sh
node --test components/dsh-connector/test/*.test.mjs
PYTHONPATH=tests .venv/bin/python -m unittest test_components -v
```

Use the Node bundled with the target DSH for compatibility verification where available. The packaging test compares every resource with source and the archive, checks immutable outputs and rejects unsafe paths/symlinks/lifecycle scripts. Runtime tests use synthetic temporary work, not real Thread records. Host/UI verification remains a separate step; unit tests and plugin installation alone do not prove sidebar rendering.
