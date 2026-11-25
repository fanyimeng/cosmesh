"""Owned Git stores with per-store locks and a recoverable write journal."""

from . import _fcntl as fcntl
from . import _platform
import hashlib
import io
import json
import os
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .errors import Error
from .protocol import HEX, decode


def check_windows_path(path):
    """Reject DOS device names and components that Win32 silently rewrites."""
    drive = path.drive
    if drive.startswith('\\\\.\\') or (drive.startswith('\\\\?\\') and
            not (drive[4:].upper().startswith('UNC\\') or (len(drive) == 6 and drive[5] == ':'))):
        raise Error("PATH_INVALID", "Select a filesystem path on a drive or a share.")
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        if part == '..':
            continue
        device = part.partition('.')[0].rstrip(' ').upper()
        if (part.endswith(('.', ' ')) or any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                or device in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
                or (device[:3] in {'COM', 'LPT'} and len(device) == 4 and device[3] in '123456789¹²³')):
            raise Error("PATH_INVALID", "Select an ordinary Windows file or directory name.")


def safe_path(path):
    path = Path(path).expanduser()
    if os.name == 'nt':
        check_windows_path(path)
    path = Path(os.path.abspath(path))
    for component in (path,) + tuple(path.parents):
        if os.name == 'nt':
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise Error("PATH_INVALID", "Managed paths cannot traverse Windows reparse points.")
        elif component.is_symlink():
            raise Error("PATH_INVALID", "Store and managed paths cannot contain symlinks.")
    return path


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            if _platform.WINDOWS:
                from ._win_private import trusted_fd
                if not trusted_fd(handle.fileno()):
                    raise Error("AUTH_DENIED", "Write managed data in a private Windows directory.")
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _platform.replace_managed_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# Only an enclosing Store.locked context can lend its still-open handle to a
# nested call on this thread. Every fresh context acquires an OS lock.
_store_scopes = threading.local()


@contextmanager
def _store_lock(path):
    scopes = getattr(_store_scopes, "active", None)
    if scopes is None:
        scopes = _store_scopes.active = set()
    key = (os.getpid(), path)
    if key in scopes:
        yield
        return
    with path.open("a+") as handle:
        deadline = time.monotonic() + _platform.LOCK_DEADLINE
        while True:
            try:
                fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Error("STORE_BUSY", "Another process is using this store; retry later.")
                time.sleep(.05)
        scopes.add(key)
        try:
            yield
        finally:
            scopes.remove(key)
            fcntl.lockf(handle, fcntl.LOCK_UN)


class Store:
    def __init__(self, root):
        self.root = safe_path(root)
        if _platform.WINDOWS and self.root.exists() and not _platform.private_path(self.root):
            raise Error("AUTH_DENIED", "Use a private Windows store owned by your account.")
        self.internal = self.root / ".cosmesh"
        self.pending = self.internal / "pending.json"

    def path(self, relative):
        path = Path(relative)
        if not path.parts or path.anchor or ".." in path.parts:
            raise Error("PATH_INVALID", "Managed paths must stay inside the store.")
        return safe_path(self.root / relative)

    def git(self, *args, input=None, check=True, extra_env=None, binary=False):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", LC_ALL="C")
        env.update(extra_env or {})
        try:
            result = subprocess.run(
                ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false",
                 "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
                 "-c", "user.name=CosMesh Local", "-c", "user.email=local@cosmesh.invalid",
                 "-C", str(self.root), *args], input=input, text=not binary,
                encoding=None if binary else "utf-8",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=60)
        except FileNotFoundError:
            raise Error("DEPENDENCY_MISSING", "Git is required.")
        except subprocess.TimeoutExpired:
            raise Error("GIT_FAILED", "Git timed out; the local write journal has been retained if needed.")
        if check and result.returncode:
            message = result.stderr.decode('utf-8', errors='replace') if binary else result.stderr
            raise Error("GIT_FAILED", message.strip() or "Git command failed.")
        return result.stdout.strip() if check else result

    @property
    def head(self):
        return self.git("rev-parse", "HEAD")

    @contextmanager
    def locked(self):
        if not self.path(".git").is_dir() or not self.path(".cosmesh/config.json").is_file():
            raise Error("NOT_INITIALIZED", "No initialized local store at " + str(self.root), "Run init with an explicit --root.")
        self.client()
        lock_path = self.path(".cosmesh/lock")
        with _store_lock(lock_path):
            self.recover()
            if self.git("status", "--porcelain", "--untracked-files=all"):
                raise Error("STORE_DIRTY", "The managed store has uncommitted changes.",
                            "Preserve and inspect manual changes before retrying; the CLI does not overwrite them.")
            yield self

    def snapshot(self, revision=None, allow_tree=False):
        revision = revision or self.head
        if not isinstance(revision, str) or not HEX.fullmatch(revision):
            raise Error("INPUT_INVALID", "A snapshot requires a complete Git commit hash.")
        if self.git("cat-file", "-t", revision) not in (("commit", "tree") if allow_tree else ("commit",)):
            raise Error("INPUT_INVALID", "Snapshot is not a commit.")
        files = {}
        listing = self.git("ls-tree", "-rz", revision, binary=True, check=False)
        if listing.returncode:
            raise Error('GIT_FAILED', 'Unable to list snapshot files.')
        objects = []
        for line in listing.stdout.split(b'\0'):
            if not line: continue
            mode_type_hash, raw_path = line.split(b'\t', 1)
            mode, kind, sha = mode_type_hash.decode('ascii').split()
            path = raw_path.decode('utf-8')
            if mode != "100644" or kind != "blob":
                raise Error("PATH_INVALID", "Only regular text files are supported in a local store.")
            self.path(path)
            if path != ".gitignore" and path != ".cosmesh/config.json" and not (
                    path.startswith("documents/") and path.endswith(".md")) and not (
                    path.startswith(".cosmesh/operations/") and path.endswith(".json")):
                raise Error("PATH_INVALID", "Unexpected managed file: " + path)
            objects.append((sha, path))
        if objects:
            result = self.git('cat-file', '--batch', input=''.join(sha+'\n' for sha, _ in objects).encode(),
                              binary=True, check=False)
            if result.returncode: raise Error('GIT_FAILED', 'Unable to read snapshot objects.')
            stream = io.BytesIO(result.stdout)
            for expected, path in objects:
                try:
                    sha, kind, size = stream.readline().decode('ascii').split()
                    size = int(size)
                except (ValueError, UnicodeError):
                    raise Error('GIT_FAILED', 'Invalid Git object header.')
                if sha != expected or kind != 'blob' or size < 0:
                    raise Error('GIT_FAILED', 'Snapshot object differs from its tree.')
                raw = stream.read(size)
                if len(raw) != size or stream.read(1) != b'\n':
                    raise Error('GIT_FAILED', 'Incomplete Git snapshot object.')
                digest = hashlib.new('sha1' if len(sha) == 40 else 'sha256',
                                     b'blob '+str(size).encode()+b'\0'+raw).hexdigest()
                if digest != sha: raise Error('CHECKSUM_MISMATCH', 'Git snapshot object checksum differs.')
                # Match text-mode Git reads, including universal newline conversion.
                files[path] = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            if stream.read(): raise Error('GIT_FAILED', 'Unexpected data after Git snapshot objects.')
        return revision, files

    def client(self):
        path = self.path(".cosmesh/client.json")
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise Error("SCHEMA_INVALID", "Invalid local client metadata.")
        return value

    def save_client(self, value):
        value = dict(value)
        exclude = self.path(".git/info/exclude")
        previous = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        additions = [line for line in (".cosmesh/client.json", ".cosmesh/conflict.json") if line not in previous.splitlines()]
        if additions:
            atomic(exclude, previous.rstrip() + "\n" + "\n".join(additions) + "\n")
        atomic(self.path(".cosmesh/client.json"), json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def documents(self, files):
        docs = {}
        for path, text in files.items():
            if path.startswith("documents/"):
                doc = decode(text, Path(path).name)
                if doc["meta"]["id"] in docs:
                    raise Error("SCHEMA_INVALID", "Duplicate document ID.")
                docs[doc["meta"]["id"]] = doc
        return docs

    def recover(self):
        if not self.pending.exists():
            diagnostic = self.path(".cosmesh/conflict.json")
            if diagnostic.exists():
                value = json.loads(diagnostic.read_text(encoding="utf-8"))
                if value.get("state") == "merging":
                    if self.git("rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0:
                        if self.head != value["local_revision"]:
                            raise Error("RECOVERY_REQUIRED", "Interrupted merge advanced unexpectedly; preserve both histories.")
                        self.git("merge", "--abort")
                    value["state"] = "interrupted"
                    atomic(diagnostic, json.dumps(value) + "\n")
            return
        safe_path(self.pending)
        try:
            journal = json.loads(self.pending.read_text(encoding="utf-8"))
            for path in journal["after"]:
                self.path(path)
            head = self.head
            if head != journal["base"]:
                if self.git("log", "-1", "--format=%s") != journal["message"]:
                    raise Error("RECOVERY_REQUIRED", "The store advanced during an incomplete transaction.")
                _, committed = self.snapshot(head)
                if any(committed.get(p) != value for p, value in journal["after"].items()):
                    raise Error("RECOVERY_REQUIRED", "The completed commit does not match its write journal.")
                self.pending.unlink()
                return
            # Roll back only files that still match this transaction, preserving manual edits.
            for path, after in journal["after"].items():
                location = self.path(path)
                actual = location.read_text(encoding="utf-8") if location.exists() else None
                if actual not in (journal["before"][path], after):
                    raise Error("RECOVERY_REQUIRED", "A pending file was manually changed: " + path)
            for path, before in journal["before"].items():
                location = self.path(path)
                if before is None:
                    location.unlink(missing_ok=True)
                else:
                    atomic(location, before)
            self.git("reset", "-q", "HEAD", "--", *journal["after"])
            self.pending.unlink()
        except (OSError, ValueError, KeyError, TypeError):
            raise Error("RECOVERY_REQUIRED", "Cannot safely recover the pending transaction; preserve its journal.")

    def commit(self, changes, operation):
        base, existing = self.snapshot()
        message = "cosmesh local " + operation
        journal = {"base": base, "message": message, "before": {}, "after": changes}
        for path in changes:
            self.path(path)
            journal["before"][path] = existing.get(path)
        atomic(self.pending, json.dumps(journal, ensure_ascii=False))
        try:
            for path, text in changes.items():
                atomic(self.path(path), text)
            self.git("add", "--", *changes)
            self.git("commit", "-q", "-m", message)
            revision = self.head
            self.pending.unlink()
            return revision
        except Exception:
            self.recover()
            raise

    def initialize(self, config, dry_run=False):
        if self.root.exists() and any(self.root.iterdir()):
            raise Error("STORE_EXISTS", "Initialization requires an empty directory.")
        if dry_run:
            return {"root": str(self.root), "config": config, "dry_run": True}, None
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _platform.WINDOWS:
            if not _platform.private_path(self.root):
                raise Error("AUTH_DENIED", "Use a private Windows directory before initializing this store.")
        else:
            os.chmod(self.root, 0o700)
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.internal.mkdir(mode=0o700)
        atomic(self.path(".cosmesh/config.json"), json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        atomic(self.path(".gitignore"), ".cosmesh/lock\n.cosmesh/pending.json\n**/.write-*\n")
        self.git("add", "--", ".cosmesh/config.json", ".gitignore")
        self.git("commit", "-q", "-m", "Initialize local " + config["product"])
        self.save_client({"user": config["user"]})
        return {"root": str(self.root), "config": config}, self.head
