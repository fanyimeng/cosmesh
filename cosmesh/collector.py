"""One local collector for explicitly selected work directories and host summaries."""

from copy import deepcopy
from . import _fcntl as fcntl
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import time

from .errors import Error
from .personal import initialize
from .personal_protocol import digest, fingerprint, packed, read_json, save_json
from .store import safe_path
from .thread_files import portable_path
from .thread_personal import ThreadPersonal
from .threads import Workbench, workspace_root, now
from .workspace_progress import policy as workspace_policy

IGNORE = {
    ".git",
    ".hg",
    ".svn",
    ".ssh",
    ".aws",
    ".codex",
    ".agents",
    ".cosmesh",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".DS_Store",
    ".cosmesh-dsh",
}


def commands(group, command):
    verbs = command(group, "collect").add_subparsers(dest="collector", required=True)
    add = command(verbs, "add", "collect.add")
    add.add_argument("id")
    add.add_argument("directory")
    add.add_argument("--as", dest="prefix", default="")
    add.add_argument("--personal", action="store_true")
    add.add_argument(
        "--context-file",
        help="Explicit UTF-8 handoff summary maintained by the host inside this directory",
    )
    remove = command(verbs, "remove", "collect.remove")
    remove.add_argument("id")
    remove.add_argument("directory")
    summary = command(verbs, "summary", "collect.summary")
    summary.add_argument("id")
    summary.add_argument("--body", required=True)
    summary.add_argument("--event", required=True)
    command(verbs, "status", "collect.status")
    command(verbs, "once", "collect.once")
    watch = command(verbs, "watch", "collect.watch")
    watch.add_argument("--interval", type=int, default=60)


def inventory(root):
    root = safe_path(root)
    if not root.is_dir():
        raise Error("PATH_INVALID", "Collection directory is unavailable.")
    if (root / ".git").exists():
        run = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-cz", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=30,
        )
        if run.returncode:
            raise Error("GIT_FAILED", "Cannot enumerate project files.")
        paths = (root / os.fsdecode(n) for n in run.stdout.split(b"\0") if n)
    else:

        def paths_in_tree():
            def failed(exc):
                raise exc

            for parent, dirs, files in os.walk(root, onerror=failed):
                dirs[:] = [d for d in dirs if d not in IGNORE and not (Path(parent) / d).is_symlink()]
                for name in files:
                    yield Path(parent) / name

        paths = paths_in_tree()
    found = {}
    checked_parents = set()
    for p in paths:
        rel = p.relative_to(root)
        if any(k in IGNORE for k in rel.parts) or p.name.lower().startswith(
            ("credentials", "id_rsa", "id_ed25519")
        ):
            continue
        try:
            name = portable_path(rel.as_posix())
            if p.parent not in checked_parents:
                safe_path(p.parent)
                checked_parents.add(p.parent)
            info = p.lstat()
        except FileNotFoundError:
            continue
        except Error as exc:
            if exc.code == "PATH_INVALID":
                continue
            raise
        if stat.S_ISREG(info.st_mode) and not p.name.lower().endswith((".key", ".pem")):
            found[name] = fingerprint(info)
    return found


def owned(wb, tid):
    item = wb.find(tid, "thread")
    if item["repo"] != "local" or item["doc"]["meta"]["author"] != wb.author_for(item):
        raise Error("AUTH_DENIED", "Choose a local Thread you own.")
    return item["doc"]["meta"]["id"]


def policy_dir(wb):
    return wb.base.path(".git/cosmesh-personal-policy")


def enable_sync(wb, tid, enabled):
    path = policy_dir(wb) / tid
    save_json(
        path,
        {
            "enabled": enabled,
            "head": wb.base.head if enabled else None,
            "bindings": binding_stamp(wb, tid),
        },
    )


def binding_stamp(wb, tid):
    rows = [
        row
        for k in ("bindings", "project_bindings")
        for row in wb.client.get(k, [])
        if row.get("thread") == tid
    ]
    return digest(packed(rows).encode())


def sync_records(wb, offline):
    """Compare local revisions once per pass; unrelated writes advance only the cursor."""
    policies = policy_dir(wb)
    if not policies.is_dir():
        return []
    reports = []
    with wb.base.locked():
        head = wb.base.head
        wb.client = wb.read_client()
        diffs = {}
        for path in sorted(policies.iterdir()):
            if not path.name.startswith("W-"):
                continue
            if read_json(workspace_policy(wb, path.name), {}).get('enabled'):
                continue
            state = read_json(path)
            if not state["enabled"]:
                continue
            tid = path.name
            try:
                before = state["head"]
                stamp = binding_stamp(wb, tid)
                if before == head and state["bindings"] == stamp:
                    continue
                if before not in diffs:
                    diff = wb.base.git("diff", "--name-only", before, head, "--", "documents")
                    diffs[before] = diff.splitlines()
                wb.load()
                changed = any(
                    (wb.catalog.get(Path(name).stem) or {}).get("doc", {}).get("meta", {}).get("thread")
                    == tid
                    or Path(name).stem == tid
                    for name in diffs[before]
                )
                if changed or stamp != state["bindings"]:
                    if offline:
                        reports.append({"thread": tid, "pending": True})
                        continue
                    p = ThreadPersonal(wb)
                    receipt = p.synchronize(tid)
                    reports.append({"thread": tid, "snapshot": receipt["accepted_snapshot"]})
                enable_sync(wb, tid, True)
            except (Error, OSError) as exc:
                reports.append(
                    {"thread": tid, "pending": True, "error": getattr(exc, "code", "IO_ERROR")}
                )
    return reports


class Collector:
    def __init__(self, wb):
        self.wb = wb
        host = digest(socket.gethostname().encode())[:24]
        self.directory = wb.base.path(".git/cosmesh-collector/" + host)
        self.path = self.directory / "state.json"

    def save(self, state):
        save_json(self.path, state)

    def once(self, state, scope, offline):
        root = safe_path(scope["directory"])
        files = inventory(root)
        prior = scope["files"]
        changed = sorted(k for k in files if prior.get(k) != files[k])
        removed = sorted(prior.keys() - files.keys())
        wb = self.wb
        tid = scope["thread"]
        report = {
            "thread": tid,
            "directory": str(root),
            "changed": len(changed),
            "removed": len(removed),
            "last_summary": scope.get("summary"),
            "session_context": "reported_only",
        }
        if scope.get("context_file") and scope["context_file"] in changed:
            from .cli import read_input
            from .personal_protocol import inspect_file

            source = root / scope["context_file"]
            checked = inspect_file(source, 16000)
            if checked.get("reason"):
                raise Error(
                    "TRANSFER_FILTERED",
                    "The configured context summary is unavailable.",
                    checked["reason"],
                )
            summary = read_input(source, 16000)
            if summary.strip():
                with wb.base.locked():
                    wb.load()
                    owned(wb, tid)
                    key = "context-" + digest(
                        packed([tid, scope["directory"], scope.get("checkpoint"), summary]).encode()
                    )
                    receipt = wb.append(tid, summary, title="宿主交接摘要", key=key)
                    scope["summary"] = receipt["id"]
                    report["last_summary"] = receipt["id"]
        if changed or removed:
            with wb.base.locked():
                wb.load()
                owned(wb, tid)
                signature = digest(
                    packed([scope["directory"], scope.get("checkpoint"), files]).encode()
                )
                lines = ["Changed files"] + changed + ["Removed files"] + removed
                text = "\n".join(lines)
                result = wb.append(tid, text[:12000], title="项目文件更新", key="collect-" + signature)
                scope.update(checkpoint=result["id"], at=now(), files=files)
                for name in changed + removed:
                    scope.get("excluded", {}).pop(name, None)
                if scope["personal"]:
                    pending = set(scope.get("pending_files", []))
                    scope["pending_files"] = sorted((pending | set(changed)) - set(removed))
                    scope["removed"] = sorted(
                        (set(scope.get("removed", [])) | set(removed)) - set(changed)
                    )
                    scope["pending"] = True
                self.save(state)
        policy = read_json(policy_dir(wb) / tid, {})
        if scope.get("pending") and not offline and policy.get("enabled", True):
            with wb.base.locked():
                wb.load()
                owned(wb, tid)
                initialize(wb)
                personal = ThreadPersonal(wb)
                if not personal.state.get("connection"):
                    personal.connect()
                ids = []
                paths = personal.state.setdefault("asset_paths", {})
                known_paths = {}
                for aid, row in personal.state["assets"].items():
                    if row["thread"] == tid:
                        known_paths.setdefault(paths.get(aid, row["name"]), []).append(aid)
                for name in scope.get("pending_files", []):
                    relative = portable_path("/".join(filter(None, [scope["prefix"], name])))
                    # Reuse the accepted asset ID after restoring on another device.
                    known = known_paths.get(relative, [])
                    if len(known) > 1:
                        raise Error("STATE_CONFLICT", "Ambiguous project file in this Thread.")
                    if known:
                        aid = known[0]
                        local = personal.source_path(aid)
                        if Path(local) != root / name:
                            raise Error("STATE_CONFLICT", "Another source owns this relative path.")
                    else:
                        aid = personal.add(tid, root / name, relative_path=relative, persist=False)[
                            "id"
                        ]
                    personal.state.setdefault("removed_assets", {}).pop(aid, None)
                    ids.append(aid)
                for name in scope.get("removed", []):
                    relative = "/".join(filter(None, [scope["prefix"], name]))
                    for aid in known_paths.get(relative, []):
                        personal.state.setdefault("removed_assets", {})[aid] = True
                personal.save()
                if read_json(workspace_policy(wb, tid), {}).get('enabled'):
                    # Every scope registers changes locally first. The shared source
                    # then prepares all registered files and publishes once per pass.
                    return dict(report, pending=True, workspace_sync=True)
                originals = {
                    aid: (
                        deepcopy(personal.asset(aid)),
                        personal.state.get("asset_copies", {}).get(aid),
                    )
                    for aid in ids
                }
            # Stage and upload immutable bytes outside the work store's writer lock.
            with tempfile.TemporaryDirectory(dir=self.directory) as folder:
                personal.staging = Path(folder)
                transfers = personal.push(ids, persist=False) if ids else {"files": []}
            with wb.base.locked():
                wb.load()
                owned(wb, tid)
                current = ThreadPersonal(wb)
                if not read_json(policy_dir(wb) / tid, {}).get("enabled", True):
                    return dict(report, pending=True)
                for entry in transfers["files"]:
                    if entry["state"] != "server_available":
                        continue
                    aid = entry["id"]
                    if originals[aid] != (
                        current.asset(aid),
                        current.state.get("asset_copies", {}).get(aid),
                    ):
                        raise Error(
                            "STATE_CONFLICT",
                            "The local asset changed while its bytes were being uploaded.",
                        )
                    current.state["assets"][aid] = personal.asset(aid)
                    current.state.setdefault("asset_copies", {})[aid] = personal.state["asset_copies"][
                        aid
                    ]
                current.save()
                receipt = current.synchronize(tid)
                missed = [r for r in transfers["files"] if r["state"] != "server_available"]
                enable_sync(wb, tid, True)
                filtered = {
                    "file_limit",
                    "credential_content",
                    "credential_or_cache_path",
                    "credential_path",
                }
                retry = {r["id"] for r in missed if r.get("reason") not in filtered}
                excluded = scope.setdefault("excluded", {})
                for name in changed + removed:
                    excluded.pop(name, None)
                for entry in missed:
                    if entry.get("reason") in filtered:
                        name = str(Path(personal.source_path(entry["id"])).relative_to(root))
                        excluded[name] = entry["reason"]
                scope.update(
                    pending=bool(retry),
                    pending_files=[
                        name for name, aid in zip(scope.get("pending_files", []), ids) if aid in retry
                    ],
                    removed=[],
                    snapshot=receipt["accepted_snapshot"],
                )
                self.save(state)
                report.update(snapshot=scope["snapshot"], skipped=missed)
        report["pending"] = bool(scope.get("pending"))
        return report

    def run(self, args):
        if not self.wb.base.path(".git").is_dir():
            raise Error("NOT_INITIALIZED", "Initialize the local Thread store first.")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.directory / "lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Error("STORE_BUSY", "The local collector is active.")
            state = read_json(self.path, {"scopes": []})
            scopes = state["scopes"]
            verb = args.collector
            from .workspace_progress import report as workspace_progress
            if verb == "status":
                return {
                    "shared_workspaces": workspace_progress(self.wb, status=True),
                    "scopes": [
                        {k: v for k, v in s.items() if k not in {"files", "pending_files", "removed"}}
                        for s in scopes
                    ]
                }
            if verb in {"add", "remove", "summary"}:
                with self.wb.base.locked():
                    self.wb.load()
                    tid = owned(self.wb, args.id)
                    if verb == "summary":
                        if not args.body.strip() or not args.event.strip():
                            raise Error("INPUT_INVALID", "Provide an event and summary.")
                        result = self.wb.append(
                            tid,
                            args.body,
                            title="Agent 工作摘要",
                            key="summary-" + digest(packed([tid, args.event]).encode()),
                        )
                        for scope in scopes:
                            if scope["thread"] == tid:
                                scope["summary"] = result["id"]
                                if scope["personal"]:
                                    scope["pending"] = True
                        self.save(state)
                        return result
                root = safe_path(args.directory)
                same = next(
                    (s for s in scopes if s["directory"] == str(root) and s["thread"] == tid), None
                )
                if verb == "remove":
                    if same:
                        scopes.remove(same)
                else:
                    if (
                        not root.is_dir()
                        or root == Path.home()
                        or root == Path(root.anchor)
                        or root == self.wb.base.root
                    ):
                        raise Error("PATH_INVALID", "Choose a specific existing project directory.")
                    prefix = portable_path(args.prefix) if args.prefix else ""
                    for s in scopes:
                        if s is same:
                            continue
                        other = Path(s["directory"])
                        if root == other or root in other.parents or other in root.parents:
                            raise Error(
                                "STATE_CONFLICT",
                                "Overlapping scopes need a single active work assignment.",
                            )
                        if s["thread"] == tid and (
                            not prefix
                            or not s["prefix"]
                            or (prefix + "/").startswith(s["prefix"] + "/")
                            or (s["prefix"] + "/").startswith(prefix + "/")
                        ):
                            raise Error(
                                "STATE_CONFLICT",
                                "Use distinct portable prefixes for multiple directories.",
                            )
                    if same and same["prefix"] != prefix:
                        raise Error("STATE_CONFLICT", "Remove this scope before changing its prefix.")
                    context = portable_path(args.context_file) if args.context_file else None
                    if (
                        not same
                        or same["personal"] != args.personal
                        or context != same.get("context_file")
                    ):
                        if same:
                            scopes.remove(same)
                        scopes.append(
                            dict(
                                thread=tid,
                                directory=str(root),
                                prefix=prefix,
                                files={},
                                personal=args.personal,
                                pending=args.personal,
                                context_file=context,
                            )
                        )
                self.save(state)
                if verb == "add" and args.personal:
                    enable_sync(self.wb, tid, True)
                return {"thread": tid, "directory": str(root), "registered": verb == "add"}
            reports = []
            for scope in scopes:
                try:
                    reports.append(self.once(state, scope, getattr(args, "offline", False)))
                except (Error, OSError, subprocess.TimeoutExpired) as exc:
                    reports.append(
                        {
                            "thread": scope["thread"],
                            "directory": scope["directory"],
                            "error": getattr(exc, "code", "IO_ERROR"),
                            "message": str(exc),
                            "pending": bool(scope.get("pending")),
                        }
                    )
            records = sync_records(self.wb, getattr(args, "offline", False))
            shared = workspace_progress(self.wb, offline=getattr(args, "offline", False), force=verb == "once")
            accepted = {r["thread"]: r for r in shared if r.get("enabled") and not r.get("pending") and r.get("snapshot")}
            for item in reports:
                if item.pop("workspace_sync", False) and item["thread"] in accepted:
                    scope = next(s for s in scopes if s["directory"] == item["directory"] and s["thread"] == item["thread"])
                    snapshot = accepted[item["thread"]]["snapshot"]
                    scope.update(pending=False, pending_files=[], removed=[], snapshot=snapshot)
                    item.update(pending=False, snapshot=snapshot)
                    self.save(state)
            return {
                "scopes": reports,
                "records": records,
                "shared_workspaces": shared,
                "complete": all(not r.get("error") and not r.get("pending") for r in reports + records + shared),
            }


def execute(args):
    if getattr(args, "dry_run", False):
        raise Error("INPUT_INVALID", "Use collect status to inspect registered scopes.")
    collector = Collector(Workbench(workspace_root(getattr(args, "root", None))))
    if args.collector != "watch":
        return collector.run(args), None
    if not 5 <= args.interval <= 86400:
        raise Error("INPUT_INVALID", "Choose an interval from 5 to 86400 seconds.")
    if not collector.wb.base.path(".git").is_dir():
        raise Error("NOT_INITIALIZED", "Initialize the work store first.")
    collector.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (collector.directory / "watch.lock").open("a+") as watcher:
        try:
            fcntl.flock(watcher, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error("STORE_BUSY", "A watcher already runs on this node for this work store.")
        while True:
            try:
                print(json.dumps(collector.run(args), ensure_ascii=False), flush=True)
            except Error as exc:
                if exc.code != "STORE_BUSY":
                    raise
            time.sleep(args.interval)
