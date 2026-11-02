#!/usr/bin/env python3
"""Standalone offline release installer. Uses only Python's standard library."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
NAMES = ("cosmesh", "cosbase")
MARKER = {"product": "cosmesh", "installation_format": 1}


class Failure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for component in (path,) + tuple(path.parents):
        if component.is_symlink() and str(component) not in ("/tmp", "/var"):
            raise Failure("PATH_CONFLICT", "路径包含符号链接，请选择真实目录：" + str(component))
    return path.resolve()


def atomic(path, data, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise Failure("PATH_CONFLICT", "保留已有符号链接：" + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cosmesh-write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


def read_bytes(path):
    path = Path(path)
    if path.is_symlink():
        raise Failure("PATH_CONFLICT", "保留已有符号链接：" + str(path))
    return path.read_bytes() if path.exists() else None


def clean_env():
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        env.pop(name, None)
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1")
    return env


def checked(command, env=None):
    result = subprocess.run([str(x) for x in command], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env or clean_env())
    if result.returncode:
        raise Failure("INSTALL_FAILED", (result.stderr or result.stdout).strip()[-4000:])
    return result.stdout.strip()


def base_interpreter():
    candidates = [Path(sys.base_prefix) / "bin" / ("python%d.%d" % sys.version_info[:2]),
                  Path(sys.base_prefix) / "bin/python3", Path(sys.executable).resolve()]
    for candidate in candidates:
        if candidate.is_file():
            probe = checked([candidate, "-I", "-c", "import sys; print(sys.prefix == sys.base_prefix)"])
            if probe == "True":
                return candidate.resolve()
    raise Failure("INTERPRETER_UNAVAILABLE", "请使用系统 Python 执行安装器，避免依赖开发虚拟环境。")


def bundle(path):
    root = canonical(path)
    raw = (root / "release.json").read_bytes()
    meta = json.loads(raw)
    if not isinstance(meta, dict) or meta.get("format") != 1 or meta.get("product") != "cosmesh" or not VERSION.fullmatch(meta.get("version", "")):
        raise Failure("INVALID_RELEASE", "发布元数据无效。")
    paths = meta.get("files", {})
    if not isinstance(paths, dict) or not paths:
        raise Failure("INVALID_RELEASE", "发布文件清单为空。")
    for name, checksum in paths.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or (root / relative).is_symlink():
            raise Failure("INVALID_RELEASE", "发布文件路径无效。")
        location = root / relative
        if not location.resolve().is_relative_to(root) or digest(location.read_bytes()) != checksum:
            raise Failure("CHECKSUM_MISMATCH", "发布文件校验失败：" + name)
    if "install.py" not in paths or meta.get("wheel") not in paths:
        raise Failure("INVALID_RELEASE", "缺少安装器或客户端 wheel。")
    wheels = meta.get("wheels")
    if not isinstance(wheels, list) or meta["wheel"] not in wheels or any(p not in paths or not p.endswith(".whl") for p in wheels):
        raise Failure("INVALID_RELEASE", "wheel 清单无效。")
    if meta.get("python") != "%d.%d" % sys.version_info[:2] or meta.get("platform") != sys.platform:
        raise Failure("PLATFORM_MISMATCH", "此发布包需要 %s / Python %s。" % (meta.get("platform"), meta.get("python")))
    import platform
    if meta.get("machine") != platform.machine():
        raise Failure("PLATFORM_MISMATCH", "此发布包需要架构 " + str(meta.get("machine")))
    expected_id = "cosmesh-%s-%s-%s-py%d%d" % (meta["version"], sys.platform, platform.machine(), *sys.version_info[:2])
    if meta.get("release_id") != expected_id or meta.get("schema_write") not in ([1], [1, 2], [1, 2, 3]):
        raise Failure("INVALID_RELEASE", "发布标识或数据格式声明无效。")
    if not shutil.which("git"):
        raise Failure("DEPENDENCY_MISSING", "请先安装 Git。")
    meta["manifest_sha256"] = digest(raw)
    return root, meta


def load_state(prefix, required=False):
    path = prefix / "state.json"
    if not path.exists():
        if required:
            raise Failure("NOT_INSTALLED", "安装尚未完成，请重试原发布包的 install 命令。")
        return None
    state = json.loads(read_bytes(path))
    if not isinstance(state, dict) or state.get("format") != 1 or not isinstance(state.get("versions"), dict):
        raise Failure("INVALID_INSTALL", "安装状态无效。")
    if any(not VERSION.fullmatch(v) for v in state["versions"]):
        raise Failure("INVALID_INSTALL", "安装版本无效。")
    if state.get("current") not in state["versions"] or not isinstance(state.get("skills", {}), dict):
        raise Failure("INVALID_INSTALL", "当前版本或 Skill 状态无效。")
    return state


@contextmanager
def locked(prefix, create=False, wait_seconds=0):
    marker = prefix / "owner.json"
    if not prefix.exists():
        if not create:
            raise Failure("NOT_INSTALLED", "尚未安装：" + str(prefix))
        prefix.mkdir(parents=True, mode=0o700)
    if not marker.exists():
        if not create or any(prefix.iterdir()):
            raise Failure("PREFIX_CONFLICT", "安装目录已有其他内容，请选择空目录。")
        atomic(marker, json_bytes(MARKER))
    if json.loads(read_bytes(marker)) != MARKER:
        raise Failure("PREFIX_CONFLICT", "此目录不属于 CosMesh 安装器。")
    lock_path = prefix / ".lock"
    if lock_path.is_symlink():
        raise Failure("PATH_CONFLICT", "安装锁是符号链接。")
    with lock_path.open("a+") as handle:
        import time
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Failure("INSTALL_BUSY", "另一个安装 / 升级进程正在使用此目录。")
                time.sleep(.025)
        try:
            recover(prefix)
            yield
        finally:
            fcntl.lockf(handle, fcntl.LOCK_UN)


def recover(prefix):
    pending = prefix / "pending.json"
    if not pending.exists():
        return
    journal = json.loads(read_bytes(pending))
    after_state = journal["after"].get(str(prefix / "state.json"))
    committed = after_state is not None and read_bytes(prefix / "state.json") == base64.b64decode(after_state)
    for path, after in journal["after"].items():
        current = read_bytes(path)
        before = journal["before"][path]
        allowed = [base64.b64decode(x) if x is not None else None for x in (before, after)]
        if current not in allowed:
            raise Failure("RECOVERY_REQUIRED", "保留升级期间的额外修改，需检查：" + path)
        if committed and current != allowed[1]:
            raise Failure("RECOVERY_REQUIRED", "版本已切换但安装文件不一致，需检查：" + path)
    if not committed:
        for path, before in journal["before"].items():
            if before is None:
                Path(path).unlink(missing_ok=True)
            else:
                atomic(Path(path), base64.b64decode(before), journal["modes"][path])
    pending.unlink()


def switch_files(prefix, changes, state):
    # state.json is written last: this is the activation point.
    targets = {str(path): (content, mode) for path, content, mode in changes}
    targets[str(prefix / "state.json")] = (json_bytes(state), 0o600)
    before = {path: read_bytes(path) for path in targets}
    journal = {"before": {p: base64.b64encode(b).decode() if b is not None else None for p, b in before.items()},
               "after": {p: base64.b64encode(value[0]).decode() for p, value in targets.items()},
               "modes": {p: (Path(p).stat().st_mode & 0o777) if Path(p).exists() else value[1] for p, value in targets.items()}}
    atomic(prefix / "pending.json", json_bytes(journal))
    try:
        for path, (content, mode) in targets.items():
            atomic(Path(path), content, mode)
        (prefix / "pending.json").unlink()
    except BaseException:
        recover(prefix)
        raise


def skill_changes(prefix, state, version, names=None, skills_dir=None):
    existing = state.get("skills", {}) if state else {}
    selected = names if names is not None else list(existing)
    destination = canonical(skills_dir or Path.home() / ".agents/skills") if names else None
    result, records = [], dict(existing)
    for name in selected:
        target = canonical(destination / name / "SKILL.md") if names is not None else Path(existing[name]["path"])
        if target.parent.is_symlink():
            raise Failure("SKILL_CONFLICT", "保留已有 Skill 链接：" + str(target.parent))
        content = (prefix / "versions" / version / "skills" / name / "SKILL.md").read_bytes()
        local_entry = ("\n\n## 本机用户安装入口\n\n本文命令中的 `%s` 使用以下绝对路径调用：\n\n`%s`\n\n"
                       "该入口由发布安装器管理，自动选择已安装版本；无需进入源码目录或激活开发虚拟环境。\n" %
                       (name, str(prefix / "bin" / name)))
        content += local_entry.encode()
        current = read_bytes(target)
        owned = existing.get(name)
        if current is not None and current != content and (not owned or str(target) != owned["path"] or digest(current) != owned["sha256"]):
            raise Failure("SKILL_CONFLICT", "Skill 已有自定义内容，升级已停止并保留文件：" + str(target))
        result.append((target, content, 0o600))
        records[name] = {"path": str(target), "sha256": digest(content), "version": version}
    return result, records


def preflight_skills(state, names=None, skills_dir=None):
    existing = state.get("skills", {}) if state else {}
    selected = names if names is not None else list(existing)
    target_root = canonical(skills_dir or Path.home() / ".agents/skills") if names else None
    for name in selected:
        target = canonical(target_root / name / "SKILL.md") if names is not None else canonical(existing[name]["path"])
        current = read_bytes(target)
        owned = existing.get(name)
        if owned and names is not None and owned["path"] != str(target):
            raise Failure("SKILL_CONFLICT", "已管理 Skill 的目录固定；请继续使用原目录：" + owned["path"])
        if current is not None and (not owned or owned["path"] != str(target) or digest(current) != owned["sha256"]):
            raise Failure("SKILL_CONFLICT", "已有 Skill 未由本安装管理或已被修改，请保留并检查：" + str(target))


def prepare(prefix, release, meta):
    version = meta["version"]
    home = prefix / "versions" / version
    if home.is_symlink():
        raise Failure("PATH_CONFLICT", "版本目录是符号链接。")
    if home.exists():
        saved = json.loads(read_bytes(home / "release.json"))
        if saved.get("manifest_sha256") != meta["manifest_sha256"]:
            raise Failure("IMMUTABLE_RELEASE", "相同版本号对应不同发布内容，请使用新版本号。")
        if (home / "ready.json").exists():
            return
        # Only this installer's failed, unactivated version is rebuilt.
        shutil.rmtree(home)
    home.mkdir(parents=True)
    atomic(home / "release.json", json_bytes(meta))
    with zipfile.ZipFile(release / meta["wheel"]) as wheel:
        for name in NAMES:
            atomic(home / "skills" / name / "SKILL.md", wheel.read("cosmesh/skills/" + name + "/SKILL.md"))
    runtime = home / "runtime"
    checked([base_interpreter(), "-m", "venv", runtime])
    python = runtime / "bin/python"
    checked([python, "-I", "-m", "pip", "--isolated", "install", "--prefix", runtime,
             "--no-index", "--no-deps", "--no-compile",
             *[release / item for item in meta["wheels"]]])
    actual = checked([python, "-I", "-c", "import cosmesh; print(cosmesh.__version__)"])
    if actual != version:
        raise Failure("VERSION_MISMATCH", "运行环境中的版本与发布清单不符。")
    checked([runtime / "bin/cosmesh", "capabilities", "--json"])
    atomic(home / "ready.json", json_bytes({"version": version, "manifest_sha256": meta["manifest_sha256"]}))


def wrappers(prefix, interpreter, manager):
    changes = [(prefix / "manager.py", manager, 0o700)]
    for name in NAMES:
        script = "#!/bin/sh\nexec %s %s launch %s \"$@\"\n" % (
            shlex.quote(str(interpreter)), shlex.quote(str(prefix / "manager.py")), name)
        changes.append((prefix / "bin" / name, script.encode(), 0o700))
    return changes


def apply_release(prefix, release_path, action, names=None, skills_dir=None, dry_run=False, verbose=False):
    release, meta = bundle(release_path)
    state = load_state(prefix) if prefix.exists() else None
    if action == "install" and state:
        raise Failure("ALREADY_INSTALLED", "已安装，请使用 upgrade 或 self status。")
    if action == "upgrade" and not state:
        raise Failure("NOT_INSTALLED", "尚未安装，请先执行 install。")
    if state and tuple(map(int, meta["version"].split("."))) < tuple(map(int, state["current"].split("."))):
        raise Failure("USE_ROLLBACK", "回到旧版本请使用 rollback。")
    if state:
        old = state["versions"][state["current"]]
        if old["schema_write"] != meta["schema_write"] and not ((old["schema_write"] == [1] and meta["schema_write"] == [1, 2]) or (old["schema_write"] in ([1],[1,2]) and meta["schema_write"] == [1,2,3])):
            raise Failure("MIGRATION_REQUIRED", "此版本涉及数据格式变化，需先实现并验证迁移。")
        if meta["version"] in state["versions"] and state["versions"][meta["version"]]["manifest_sha256"] != meta["manifest_sha256"]:
            raise Failure("IMMUTABLE_RELEASE", "相同版本号对应不同发布包。")
    plan = {"action": action, "from": state["current"] if state else None, "to": meta["version"],
            "prefix": str(prefix), "release": str(release), "data_unchanged": True, "dry_run": dry_run,
            "skills": names if names is not None else list(state.get("skills", {})) if state else []}
    preflight_skills(state, names, skills_dir)
    if dry_run:
        return plan
    if verbose:
        print("发布包校验通过。正在准备独立运行环境 %s…" % meta["version"], file=sys.stderr)
    with locked(prefix, create=action == "install"):
        latest = load_state(prefix)
        if latest != state:
            raise Failure("INSTALL_CONFLICT", "安装状态已变化，请重新读取后重试。")
        prepare(prefix, release, meta)
        if verbose:
            print("运行环境检查通过。正在切换程序和选装 Skill…", file=sys.stderr)
        changes, skill_records = skill_changes(prefix, state, meta["version"], names, skills_dir)
        next_state = json.loads(json.dumps(state)) if state else {"format": 1, "versions": {}, "skills": {}}
        if not state or state["current"] != meta["version"]:
            next_state["previous"] = state["current"] if state else None
        next_state.update(current=meta["version"], skills=skill_records)
        next_state["versions"][meta["version"]] = {"manifest_sha256": meta["manifest_sha256"], "schema_write": meta["schema_write"]}
        interpreter = base_interpreter()
        switch_files(prefix, wrappers(prefix, interpreter, (release / "install.py").read_bytes()) + changes, next_state)
    plan["commands"] = {name: str(prefix / "bin" / name) for name in NAMES}
    return plan


def rollback(prefix, version=None, dry_run=False):
    with locked(prefix):
        state = load_state(prefix, required=True)
        version = version or state.get("previous")
        if version not in state["versions"]:
            raise Failure("VERSION_UNAVAILABLE", "没有可回滚的已安装版本。")
        if (state["versions"][version]["schema_write"] != state["versions"][state["current"]]["schema_write"] and
                not (state["versions"][version]["schema_write"] == [1] and state["versions"][state["current"]]["schema_write"] == [1, 2])):
            raise Failure("MIGRATION_REQUIRED", "数据格式不兼容，回滚已停止。")
        current = json.loads(read_bytes(prefix / 'versions' / state['current'] / 'release.json'))
        target = json.loads(read_bytes(prefix / 'versions' / version / 'release.json'))
        if current.get('thread_lifecycle_version', 1) > target.get('thread_lifecycle_version', 1):
            raise Failure('MIGRATION_REQUIRED', '旧程序不支持新的 Thread 生命周期记录；先保全数据并核验兼容恢复方案。')
        preflight_skills(state)
        changes, records = skill_changes(prefix, state, version)
        result = {"action": "rollback", "from": state["current"], "to": version, "data_unchanged": True, "dry_run": dry_run}
        if not dry_run:
            state.update(current=version, previous=state["current"], skills=records)
            switch_files(prefix, changes, state)
        return result


def manage_skills(prefix, names, skills_dir=None, dry_run=False):
    with locked(prefix):
        state = load_state(prefix, required=True)
        preflight_skills(state, names, skills_dir)
        changes, records = skill_changes(prefix, state, state["current"], names, skills_dir)
        if not dry_run:
            state["skills"] = records
            switch_files(prefix, changes, state)
        return {"version": state["current"], "managed": True, "dry_run": dry_run,
                "skills": [{"name": name, "status": "planned" if dry_run else "installed", **records[name]} for name in names],
                "host_discovery": "not_verified", "natural_language_trigger": "not_verified"}


def status(prefix):
    with locked(prefix):
        state = load_state(prefix, required=True)
        return {"mode": "managed_release", "prefix": str(prefix), "version": state["current"],
                "previous": state.get("previous"), "installed_versions": list(state["versions"]),
                "skills": state["skills"], "data_location": "Independent document stores; unchanged by install, upgrade, rollback or uninstall."}


def uninstall(prefix, dry_run=False):
    with locked(prefix):
        state = load_state(prefix, required=True)
        removed, preserved = [], []
        for record in state["skills"].values():
            path = Path(record["path"])
            content = read_bytes(path)
            if content is not None and digest(content) == record["sha256"]:
                removed.append(str(path))
            elif content is not None:
                preserved.append(str(path))
        result = {"action": "uninstall", "prefix": str(prefix), "data_unchanged": True,
                  "removed_skills": removed, "preserved_modified_skills": preserved, "dry_run": dry_run}
        if dry_run:
            return result
        for path in removed:
            Path(path).unlink()
            try:
                Path(path).parent.rmdir()
            except OSError:
                pass
        for version in state["versions"]:
            shutil.rmtree(prefix / "versions" / version)
        for path in (prefix / "bin/cosmesh", prefix / "bin/cosbase", prefix / "manager.py", prefix / "state.json", prefix / "client.json"):
            path.unlink(missing_ok=True)
        # Unknown files in the prefix are left in place.
        for directory in (prefix / "bin", prefix / "versions"):
            try:
                directory.rmdir()
            except OSError:
                pass
    for name in (".lock", "owner.json"):
        (prefix / name).unlink(missing_ok=True)
    try:
        prefix.rmdir()
    except OSError:
        pass
    return result


def launch(prefix, name, arguments):
    if arguments and arguments[0] == "self":
        return main(arguments[1:], fixed_prefix=prefix)
    if arguments[:2] == ["skills", "install"]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--agent", choices=("codex", "opencode"), required=True)
        parser.add_argument("--only", choices=(*NAMES, "both"), default=name)
        parser.add_argument("--dest")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true")
        args = parser.parse_args(arguments[2:])
        try:
            result = manage_skills(prefix, list(NAMES) if args.only == "both" else [args.only], args.dest, args.dry_run)
            emit(result, None, args.json)
            return 0
        except Failure as exc:
            emit(None, exc, args.json)
            return 2
    with locked(prefix, wait_seconds=3):
        state = load_state(prefix, required=True)
        executable = prefix / "versions" / state["current"] / "runtime/bin" / name
    env = clean_env()
    env["COSMESH_INSTALL_PREFIX"] = str(prefix)
    os.execve(str(executable), [str(executable), *arguments], env)


def emit(data, error, machine):
    if machine:
        print(json.dumps({"ok": error is None, "data": data, "sync": None, "warnings": [],
                          "error": {"code": error.code, "message": error.message} if error else None}, ensure_ascii=False))
    elif error:
        print("操作未完成 [%s]：%s" % (error.code, error.message), file=sys.stderr)
    elif data.get("action") in ("install", "upgrade", "rollback"):
        verb = {"install": "安装", "upgrade": "升级", "rollback": "回滚"}[data["action"]]
        print(("预览：" if data.get("dry_run") else "已完成：") + verb + " CosMesh / CosBase " + data["to"])
        if data.get("from"):
            print("版本变化：%s → %s" % (data["from"], data["to"]))
        if data.get("commands"):
            print("程序入口：" + data["commands"]["cosmesh"])
            print("查看安装：" + data["commands"]["cosmesh"] + " self status")
        print("用户文档独立保留。")
    elif data.get("mode") == "managed_release":
        print("CosMesh / CosBase " + data["version"] + " · 用户发布版")
        print("安装目录：" + data["prefix"])
        print("可用版本：" + ", ".join(data["installed_versions"]))
        print("上一版本：" + str(data.get("previous") or "无"))
        print("已管理 Skill：" + (", ".join(data["skills"]) or "无"))
    elif data.get("action") == "uninstall":
        print("卸载预览。" if data.get("dry_run") else "程序已卸载；用户文档保留。")
        for path in data.get("preserved_modified_skills", []):
            print("保留自定义 Skill：" + path)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv=None, fixed_prefix=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "launch":
        prefix = Path(__file__).resolve().parent
        try:
            return launch(prefix, argv[1], argv[2:])
        except (Failure, OSError, ValueError) as exc:
            error = exc if isinstance(exc, Failure) else Failure("INSTALL_IO_ERROR", str(exc))
            emit(None, error, "--json" in argv)
            return 2
    parser = argparse.ArgumentParser(description="CosMesh 用户版安装 / 升级管理，不需要源码或开发虚拟环境。")
    parser.add_argument("action", choices=("install", "upgrade", "status", "rollback", "uninstall", "skills"))
    parser.add_argument("--prefix", default=str(fixed_prefix or Path.home() / ".local/opt/cosmesh"))
    parser.add_argument("--release", help="已解压的发布包目录")
    parser.add_argument("--skills", choices=(*NAMES, "both", "none"))
    parser.add_argument("--skills-dir")
    parser.add_argument("--agent", choices=("codex", "opencode"))
    parser.add_argument("--version", help="回滚到已安装版本")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        prefix = canonical(args.prefix)
        if args.action in ("install", "upgrade"):
            release = args.release or (Path(__file__).resolve().parent if (Path(__file__).resolve().parent / "release.json").exists() else None)
            if release is None:
                raise Failure("RELEASE_REQUIRED", "请通过 --release 指定已解压的发布包目录。")
            names = None if args.skills is None else [] if args.skills == "none" else list(NAMES) if args.skills == "both" else [args.skills]
            result = apply_release(prefix, release, args.action, names, args.skills_dir, args.dry_run, verbose=not args.json)
        elif args.action == "status":
            result = status(prefix)
        elif args.action == "rollback":
            result = rollback(prefix, args.version, args.dry_run)
        elif args.action == "skills":
            if not args.skills:
                raise Failure("INPUT_INVALID", "请通过 --skills 选择 Skill。")
            names = [] if args.skills == "none" else list(NAMES) if args.skills == "both" else [args.skills]
            result = manage_skills(prefix, names, args.skills_dir, args.dry_run)
            result["agent"] = args.agent
        else:
            result = uninstall(prefix, args.dry_run)
        emit(result, None, args.json)
        return 0
    except (Failure, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        error = exc if isinstance(exc, Failure) else Failure("INSTALL_IO_ERROR", str(exc))
        emit(None, error, args.json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
