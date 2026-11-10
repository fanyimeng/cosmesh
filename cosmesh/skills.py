"""Agent-neutral Skill distribution. No host configuration is rewritten."""

import os
import shutil
import json
import subprocess
import sys
import hashlib
import tempfile
from pathlib import Path

from .errors import Error
from .store import safe_path

AGENTS = ("codex", "opencode", "dsh")


def agent_executable(agent):
    found = shutil.which(agent)
    if found or agent != 'dsh' or sys.platform != 'darwin':
        return found
    # Desktop includes the ordinary DSH CLI even when its shell link is absent.
    for root in (Path('/Applications'), Path.home() / 'Applications'):
        entry = root / 'DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh'
        if entry.is_file() and os.access(entry, os.X_OK):
            return str(entry)
    return None


def source(name):
    return Path(__file__).parent / "skills" / name


def destination(dest=None):
    return safe_path(dest or Path.home() / ".agents" / "skills")


def inspect(agent, names, dest=None):
    prefix = os.environ.get("COSMESH_INSTALL_PREFIX")
    owned = {}
    if prefix and (Path(prefix) / "state.json").is_file():
        owned = json.loads((Path(prefix) / "state.json").read_text()).get("skills", {})
    target = destination(dest) if dest or not owned else None
    records = []
    for name in names:
        record = owned.get(name)
        path = safe_path(Path(record["path"]).parent if record and not dest else (target or destination()) / name)
        managed = record and str(path / "SKILL.md") == record["path"]
        if not path.exists():
            status = "install_required"
        elif managed and (path / "SKILL.md").is_file() and not (path / "SKILL.md").is_symlink() and (
                hashlib.sha256((path / "SKILL.md").read_bytes()).hexdigest() == record["sha256"]):
            status = "installed_managed"
        elif path.is_dir() and (path / "SKILL.md").is_file() and not (path / "SKILL.md").is_symlink() and (
                (path / "SKILL.md").read_bytes() == (source(name) / "SKILL.md").read_bytes()):
            status = "installed_identical"
        else:
            status = "conflict"
        records.append({"name": name, "path": str(path), "status": status,
                        "version": record["version"] if managed else None})
    return {"agent": agent, "destination": str(target) if target else "managed_installation", "skills": records,
            "agent_binary": agent_executable(agent), "host_discovery": "not_verified",
            "natural_language_trigger": "not_verified"}


def install(agent, names, dest=None, dry_run=False):
    names = ["cosmesh"] if names else []
    prefix = os.environ.get("COSMESH_INSTALL_PREFIX")
    if prefix:
        command = [sys.executable, str(Path(prefix) / "manager.py"), "skills", "--prefix", prefix,
                   "--agent", agent, "--skills", "both" if len(names) == 2 else names[0], "--json"]
        if dest:
            command += ["--skills-dir", str(dest)]
        if dry_run:
            command.append("--dry-run")
        result = subprocess.run(command, capture_output=True, text=True)
        reply = json.loads(result.stdout)
        if not reply["ok"]:
            raise Error(reply["error"]["code"], reply["error"]["message"])
        return reply["data"]
    plan = inspect(agent, names, dest)
    plan["dry_run"] = dry_run
    if dry_run:
        return plan
    if any(row["status"] == "conflict" for row in plan["skills"]):
        raise Error("OVERWRITE_REFUSED", "An existing Skill differs; preserve and review it before installing.")
    target = destination(dest)
    target.mkdir(parents=True, exist_ok=True)
    for record in plan["skills"]:
        if record["status"] != "install_required":
            continue
        with tempfile.TemporaryDirectory(prefix=".cosmesh-install-", dir=str(target)) as temporary:
            staged = Path(temporary) / record["name"]
            shutil.copytree(source(record["name"]), staged)
            try:
                os.rename(staged, safe_path(record["path"]))
            except OSError:
                raise Error("INSTALL_CONFLICT", "The Skill destination changed during installation; inspect it before retrying.")
        record["status"] = "installed"
    return plan
