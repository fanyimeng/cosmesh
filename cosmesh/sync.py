"""Bounded explicit synchronization through a pinned, restricted SSH transport."""
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .errors import Error
from .hub import REPO, view
from .store import Store, atomic, safe_path
from .connections import networked, control_options


def transport(value):
    value = dict(value)
    if value.get('kind') == 'system':
        from .access import discovery
        if set(value) != {'kind', 'discovery', 'repo'} or not REPO.fullmatch(value['repo']):
            raise Error('INPUT_INVALID', 'Invalid local service connection.')
        value['discovery'] = discovery(value['discovery'])['path']
        return value
    if value.get('kind') == 'ssh-system':
        if set(value) != {'kind', 'host', 'discovery', 'repo'} or not REPO.fullmatch(value['repo']):
            raise Error('INPUT_INVALID', 'Invalid SSH system connection.')
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+', value['host']) or value['host'].startswith('-'):
            raise Error('INPUT_INVALID', 'Invalid existing SSH host alias.')
        if not value['discovery'].startswith('/'):
            raise Error('INPUT_INVALID', 'Use an absolute remote service discovery path.')
        return value
    if set(value) != {"host", "port", "login", "repo", "identity", "known_hosts", "host_key_alias"}:
        raise Error("INPUT_INVALID", "Incomplete SSH transport settings.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value["host"]):
        raise Error("INPUT_INVALID", "Use a DNS hostname or IPv4 address.")
    for key in ("login", "host_key_alias"):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value[key]):
            raise Error("INPUT_INVALID", "Invalid SSH " + key)
    if not REPO.fullmatch(value["repo"]) or not 1 <= int(value["port"]) <= 65535:
        raise Error("INPUT_INVALID", "Invalid repository or port.")
    value["port"] = int(value["port"])
    for key in ("identity", "known_hosts"):
        path = safe_path(value[key])
        if not path.is_file() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise Error("PATH_INVALID", "SSH identity and known_hosts must be private files owned by this user.")
        value[key] = str(path)
    return value


def ssh_args(config):
    arguments = ["ssh", "-F", "/dev/null", "-T", "-i", config["identity"], "-p", str(config["port"]),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + config["known_hosts"], "-o", "HostKeyAlias=" + config["host_key_alias"],
            "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=8",
            "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2"]
    return [arguments[0], *control_options(arguments+['-l', config['login'], config['host']]), *arguments[1:]]


def remote_status(config):
    if config.get('kind') == 'system':
        from .gateway import status
        try:
            return status(config)
        except (OSError, TimeoutError):
            raise Error('SYNC_NETWORK', 'Local gateway is unavailable; local commits are preserved.')
    if config.get('kind') == 'ssh-system':
        from .connections import request
        return request(config, {'protocol': 1, 'repo': config['repo'], 'operation': 'cosmesh-status'})
    else:
        command = ssh_args(config) + ['-l', config['login'], config['host'], 'cosmesh-status', config['repo']]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise Error("SYNC_NETWORK", "Cannot contact the pinned SSH Hub; local commits are preserved.")
    if result.returncode:
        code = re.search(r"\[COSMESH:([A-Z_]+)\]", result.stderr)
        raise Error(code[1] if code else "SYNC_NETWORK", "Hub handshake failed: " + result.stderr.strip())
    try:
        value = json.loads(result.stdout)
        if value["protocol"] != 1 or value["repo"] != config["repo"] or not isinstance(value["actor"], str):
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError):
        raise Error("SYNC_PROTOCOL", "Invalid Hub identity response.")


def git_transport(config):
    if config.get('kind') in {'system', 'ssh-system'}:
        import sys
        command = [sys.executable, '-m', 'cosmesh', '_transport', '--service', config['discovery']]
        if config['kind'] == 'ssh-system':
            command += ['--ssh', config['host']]
        return {'GIT_SSH_COMMAND': shlex.join(command + ['--']), 'GIT_SSH_VARIANT': 'simple'}
    return {"GIT_SSH_COMMAND": shlex.join(ssh_args(config)), "GIT_SSH_VARIANT": "ssh"}


def url(config):
    if config.get('kind') in {'system', 'ssh-system'}:
        return 'cosmesh:' + config['repo'] + '.git'
    return "ssh://%s@%s:%s/%s.git" % (config["login"], config["host"], config["port"], config["repo"])


def system_ssh(host):
    arguments = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=8',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2', host]
    return [arguments[0], *control_options(arguments), *arguments[1:]]


def system_command(config, operation):
    arguments = shlex.join(['_remote', '--service', config['discovery'], '--repo', config['repo'], '--operation', operation])
    return 'exec "$HOME/.local/opt/cosmesh/bin/cosmesh" ' + arguments


@networked
def configure(store, product, config):
    config = transport(config)
    with store.locked():
        status = remote_status(config)
        local = store.client()
        _, files = store.snapshot()
        source = json.loads(files[".cosmesh/config.json"])
        if status["product"] != product or source["product"] != product:
            raise Error("PRODUCT_MISMATCH", "Hub and client products differ.")
        if local.get("user", source["user"]) != status["actor"]:
            raise Error("AUTH_DENIED", "Existing local identity differs from the registered SSH key; preserve drafts and clone separately.")
        local.update(remote=config, user=status["actor"])
        store.save_client(local)
        return {"remote": config, "actor": status["actor"], "configured": True}, store.head


@networked
def clone(root, product, config, publish=True, remote_hint=None):
    config = transport(config)
    root = safe_path(root)
    if root.exists():
        raise Error("STORE_EXISTS", "Clone requires a new directory.")
    status = remote_hint or remote_status(config)
    if status["product"] != product:
        raise Error("PRODUCT_MISMATCH", "Hub and client products differ.")
    if not status["revision"]:
        raise Error("REMOTE_EMPTY", "Initialize and push the first store before cloning.")
    root.mkdir(parents=True, mode=0o700)
    store = Store(root)
    store.git("init", "-q")
    store.git("symbolic-ref", "HEAD", "refs/heads/main")
    store.git("fetch", "--no-tags", url(config), "refs/heads/main", extra_env=git_transport(config))
    fetched = store.git('rev-parse', 'FETCH_HEAD')
    view(store, store.snapshot(fetched)[1], product)
    store.git("merge", "--ff-only", "FETCH_HEAD")
    store.save_client({"user": status["actor"], "remote": config, 'remote_revision': fetched})
    return synchronize(store, product, publish=publish, remote_hint={**status, 'revision': fetched})


def ancestor(store, older, newer):
    return store.git("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def merge(store, product, remote):
    local = store.head
    if ancestor(store, remote, local):
        return
    view(store, store.snapshot(remote)[1], product)
    if ancestor(store, local, remote):
        store.git("merge", "--ff-only", remote)
        return
    base = store.git("merge-base", local, remote)
    original = store.snapshot(base)[1]
    left, right = store.snapshot(local)[1], store.snapshot(remote)[1]
    for path in set(left) & set(right):
        if path.startswith("documents/") and left[path] != right[path] and left[path] != original.get(path) and right[path] != original.get(path):
            atomic(store.path(".cosmesh/conflict.json"), json.dumps({"state": "conflict", "path": path,
                   "local_revision": local, "remote_revision": remote}) + "\n")
            raise Error("SYNC_CONFLICT", "Both sides revised the same document; preserve and review both committed versions.")
    diagnostic = {"state": "merging", "local_revision": local, "remote_revision": remote}
    path = store.path(".cosmesh/conflict.json")
    atomic(path, json.dumps(diagnostic) + "\n")
    try:
        result = store.git("merge", "--no-commit", "--no-ff", remote, check=False)
        if result.returncode:
            raise Error("SYNC_CONFLICT", "Concurrent definition edits need review; both committed versions are preserved.")
        view(store, store.snapshot(store.git("write-tree"), allow_tree=True)[1], product)
        store.git("commit", "-q", "-m", "Merge accepted CosMesh history")
        path.unlink(missing_ok=True)
    except Exception as exc:
        if store.git("rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0:
            store.git("merge", "--abort")
        diagnostic.update(state="conflict", error=getattr(exc, "code", "SYNC_CONFLICT"))
        atomic(path, json.dumps(diagnostic) + "\n")
        if isinstance(exc, Error):
            raise Error("SYNC_CONFLICT", exc.message, "Local commits and remote tracking history are retained; review .cosmesh/conflict.json.")
        raise


@networked
def synchronize(store, product, publish=True, remote_hint=None):
    with store.locked():
        local = store.client()
        if not local.get("remote"):
            raise Error("FRESHNESS_UNAVAILABLE", "No controlled Hub is configured for this store.", "Use repo connect or repo clone.")
        candidate = local['remote']
        # Upgrade existing server-side SSH connections using the already logged-in account.
        if not candidate.get('kind'):
            from .access import DEFAULT_DISCOVERY, discovery, system_user
            if Path(os.environ.get('COSMESH_SERVICE', DEFAULT_DISCOVERY)).is_file():
                service = discovery(os.environ.get('COSMESH_SERVICE', DEFAULT_DISCOVERY))
                if candidate['host'] in service.get('legacy_hosts', []) and candidate['login'] == system_user()['user']:
                    proposed = transport({'kind': 'system', 'discovery': service['path'], 'repo': candidate['repo']})
                    confirmed = remote_status(proposed)
                    if confirmed['actor'] != local['user'] or confirmed['product'] != product:
                        raise Error('AUTH_DENIED', 'System identity differs from the existing document identity.')
                    candidate = proposed
                    local['remote'] = proposed
                    store.save_client(local)
        config = transport(candidate)
        env = git_transport(config)
        try:
            for attempt in range(3):
                status = remote_hint if attempt == 0 and remote_hint is not None else remote_status(config)
                if status.get('protocol') != 1 or status.get('repo') != config['repo']:
                    raise Error('SYNC_PROTOCOL', 'Batch version belongs to another source.')
                if status["actor"] != local["user"] or status["product"] != product:
                    raise Error("AUTH_DENIED", "Authenticated Hub identity or product changed.")
                observed = status['revision']
                if observed:
                    known = observed == local.get('remote_revision') and store.git('cat-file', '-e', observed+'^{commit}', check=False).returncode == 0
                    if not known:
                        store.git("fetch", "--no-tags", url(config), "refs/heads/main:refs/remotes/cosmesh-hub/main", extra_env=env)
                        observed = store.git("rev-parse", "refs/remotes/cosmesh-hub/main")
                    merge(store, product, observed)
                if not publish:
                    local.update(remote_revision=observed, actor=status['actor'],
                                 last_sync_at=datetime.now(timezone.utc).isoformat(), last_error=None)
                    store.save_client(local)
                    return {'refreshed': True, 'actor': status['actor']}, store.head
                pushed = store.head
                result = store.git("push", "--porcelain", url(config), "HEAD:refs/heads/main", extra_env=env, check=False)
                # Check the accepted ref even when the push response was lost.
                accepted = remote_status(config)
                if accepted["revision"] != pushed and accepted["revision"]:
                    store.git("fetch", "--no-tags", url(config), "refs/heads/main:refs/remotes/cosmesh-hub/main", extra_env=env)
                if accepted["revision"] and ancestor(store, pushed, accepted["revision"]):
                    if accepted['revision'] != store.head:
                        merge(store, product, accepted['revision'])
                    local.update(remote_revision=accepted["revision"], actor=accepted["actor"],
                                 last_sync_at=datetime.now(timezone.utc).isoformat(), last_error=None)
                    store.save_client(local)
                    store.path(".cosmesh/conflict.json").unlink(missing_ok=True)
                    return {"synchronized": True, "actor": accepted["actor"], "attempts": attempt + 1}, store.head
                if result.returncode and not any(term in result.stderr for term in ("non-fast-forward", "fetch first", "failed to lock")):
                    raise Error("SYNC_REJECTED", "Hub rejected the push: " + result.stderr.strip(), "Local commits are preserved.")
            raise Error("SYNC_RETRY_LIMIT", "The Hub kept advancing; retry later. Local commits are preserved.")
        except Error as exc:
            local["last_error"] = exc.code
            store.save_client(local)
            raise


def state(store, revision=None):
    result = {"state": "local_only", "source_revision": revision, "local_revision": revision,
              "remote_revision": None, "last_sync_at": None, "identity_verified": False, "unpushed_count": None}
    if not store.path(".cosmesh/config.json").is_file():
        return result
    local = store.client()
    head = store.head
    result["local_revision"] = head
    if not local.get("remote"):
        return result
    remote = local.get("remote_revision")
    result.update(remote_revision=remote, last_sync_at=local.get("last_sync_at"), actor=local.get("user"),
                  state="needs_sync")
    if remote:
        verified = ancestor(store, revision or head, remote)
        result.update(identity_verified=verified, state="synced" if head == remote else "needs_sync")
        count = store.git("rev-list", "--count", remote + ".." + head, check=False)
        if not count.returncode:
            result["unpushed_count"] = int(count.stdout.strip())
    if local.get("last_error"):
        result["last_error"] = local["last_error"]
        result["state"] = "offline" if local["last_error"] == "SYNC_NETWORK" else "sync_failed"
    return result
