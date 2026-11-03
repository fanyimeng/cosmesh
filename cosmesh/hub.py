"""Git-over-SSH Hub; protected key mappings identify application users."""
import base64
import fcntl
import hashlib
import json
import os
import pwd
import re
import shlex
import socket
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .errors import Error
from .service import Service, initialize
from .protocol import USER
from .store import Store, atomic, safe_path

REPO = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ZERO = "0" * 40


def actor():
    return pwd.getpwuid(os.getuid()).pw_name


def receive_hook(entrypoint, hub_root, name):
    entrypoint = Path(entrypoint)
    manager = entrypoint.parent.parent / 'manager.py'
    launcher = (['python3', '-I', '-B', str(manager), 'launch', entrypoint.name]
                if entrypoint.name in {'cosmesh', 'cosbase'} and manager.is_file() else [str(entrypoint)])
    return '#!/bin/sh\nexec ' + shlex.join(launcher + ['hub', 'validate', '--hub-root', str(hub_root), '--repo', name]) + '\n'


def git_env():
    retained = {key: os.environ[key] for key in (
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH") if key in os.environ}
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(retained, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return env


class BareStore(Store):
    def git(self, *args, **kwargs):
        supplied = dict(kwargs.pop("extra_env", {}) or {})
        supplied.update({key: value for key, value in git_env().items() if key.startswith("GIT_")})
        return super().git(*args, extra_env=supplied, **kwargs)


def view(store, files, product):
    config = json.loads(files[".cosmesh/config.json"])
    if config.get("schema_version") == 2:
        from .thread_protocol import Snapshot
        if product != "cosmesh":
            raise Error("PRODUCT_MISMATCH", "Thread workspaces use CosMesh storage.")
        return Snapshot(store, files)
    if config.get("schema_version") != 1 or config.get("product") != product or config.get("mode") != "local_unverified":
        raise Error("SCHEMA_INVALID", "Unsupported source configuration.")
    if set(config) != {"schema_version", "product", "user", "timezone", "mode"}:
        raise Error("SCHEMA_INVALID", "Unexpected source configuration fields.")
    model = Service(store, product)
    model.docs = store.documents(files)
    model.config = config
    model.check_graph()
    return model


def validate_change(store, before, after, product, user):
    if json.loads(after[".cosmesh/config.json"]).get("schema_version") == 2:
        from .thread_protocol import validate_change as validate_threads
        if before and json.loads(before[".cosmesh/config.json"]).get("schema_version") != 2:
            raise Error("SCHEMA_UNSUPPORTED", "Legacy stores require an explicit migration.")
        return validate_threads(store, before, after, user)
    model = view(store, after, product)
    previous = view(store, before, product) if before else None
    if before and before[".cosmesh/config.json"] != after.get(".cosmesh/config.json"):
        raise Error("POLICY_DENIED", "Published repository settings cannot be rewritten.")
    if not before and model.config["user"] != user:
        raise Error("AUTH_DENIED", "The initial local identity must match the authenticated SSH account.")
    for path, old in before.items():
        if path not in after:
            raise Error("HISTORY_PROTECTED", "Published files cannot be deleted: " + path)
        if path.startswith(".cosmesh/operations/") and old != after[path]:
            raise Error("HISTORY_PROTECTED", "Operation receipts are immutable.")
    total = 0
    for path, text in after.items():
        size = len(text.encode())
        if size > (4 * 1024 * 1024 if path.startswith(".cosmesh/operations/") else 1024 * 1024):
            raise Error("INPUT_TOO_LARGE", "Source or receipt exceeds its size limit.")
        if text != before.get(path):
            total += size
        if path.startswith(".cosmesh/operations/") and path not in before:
            receipt = json.loads(text)
            operation = receipt["result"]["operation_id"]
            expected = ".cosmesh/operations/" + hashlib.sha256((user + ":" + operation).encode()).hexdigest() + ".json"
            if path != expected:
                raise Error("AUTH_DENIED", "Operation receipt is outside this authenticated user's namespace.")
    if total > 100 * 1024 * 1024:
        raise Error("INPUT_TOO_LARGE", "One commit changes more than 100 MiB.")
    for doc_id, doc in model.docs.items():
        meta = doc["meta"]
        old = previous.docs.get(doc_id) if previous else None
        if old == doc:
            continue
        kind = meta["kind"]
        if old is None:
            if meta["author"] != user:
                raise Error("AUTH_DENIED", "New document author does not match the authenticated SSH account.")
            if product == "cosmesh" and kind in {"project", "task"} and meta["lead"] != user:
                raise Error("AUTH_DENIED", "New definitions must name their authenticated creator as Lead.")
            if product == "cosmesh" and kind == "task":
                project = model.docs[meta["project"]]["meta"]
                if user not in [project["lead"]] + project["members"]:
                    raise Error("AUTH_DENIED", "Task creator is outside the project.")
            if product == "cosmesh" and kind == "update":
                task = model.docs.get(meta.get("task"))
                if task:
                    baseline_task = previous.docs.get(task["meta"]["id"]) if previous else None
                    baseline = previous.reduce(baseline_task) if baseline_task else model.reduce(task)
                    update_type = meta["type"]
                    if update_type in {"invite", "status", "schedule"}:
                        if task["meta"]["lead"] != user:
                            raise Error("AUTH_DENIED", "Only the Task Lead can change task control state.")
                    elif update_type in {"accept", "decline"}:
                        invitation = baseline["invitations"].get(meta["action"]["invitation"])
                        if not invitation or invitation["to"] != user or invitation["status"] != "pending":
                            raise Error("STATE_CONFLICT", "Invitation is not pending for this authenticated user.")
                    elif user != task["meta"]["lead"] and baseline["participants"].get(user) != "accepted":
                        raise Error("AUTH_DENIED", "Progress requires a Lead or accepted participant.")
                else:
                    project = model.docs[meta["project"]]["meta"]
                    if user not in [project["lead"]] + project["members"]:
                        raise Error("AUTH_DENIED", "Update author is outside the project.")
        else:
            old_meta = old["meta"]
            if product == "cosmesh" and kind == "update":
                raise Error("HISTORY_PROTECTED", "Published Updates are immutable; append a correction.")
            protected = {"id", "kind", "author", "created_at"}
            if product == "cosmesh":
                protected |= {"lead"}
                if kind == "task":
                    protected |= {"project", "members", "initial_status", "due", "followup"}
                if old_meta.get("lead") != user:
                    raise Error("AUTH_DENIED", "Only the Lead can revise definitions.")
            if any(meta.get(field) != old_meta.get(field) for field in protected):
                raise Error("HISTORY_PROTECTED", "Protected definition fields changed.")
    return model


class Hub:
    def __init__(self, root):
        self.root = safe_path(root)

    def settings(self):
        path = self.root / "hub.json"
        if not path.is_file():
            raise Error("HUB_NOT_INITIALIZED", "No Hub at " + str(self.root))
        value = json.loads(path.read_text())
        if value.get("format") != 1 or value.get("host") != socket.gethostname():
            raise Error("HUB_WRONG_NODE", "The authoritative Hub runs on " + str(value.get("host")))
        if value.get("admin_uid") != os.getuid():
            raise Error("AUTH_DENIED", "Hub administration requires the owning Unix account.")
        return value

    def initialize(self):
        if self.root.exists() and any(self.root.iterdir()):
            raise Error("HUB_EXISTS", "Hub initialization requires an empty directory.")
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        for directory in ("repos", "audit", "locks", "policy"):
            (self.root / directory).mkdir(mode=0o700)
        settings = {"format": 1, "host": socket.gethostname(), "admin_uid": os.getuid(),
                    "admin": actor(), "mode": "key_mapped_ssh_beta"}
        atomic(self.root / "hub.json", json.dumps(settings, indent=2) + "\n")
        atomic(self.root / "policy/repos.json", "{}\n")
        atomic(self.root / "policy/keys.json", "{}\n")
        return settings

    def register_key(self, user, public_key, entrypoint):
        if not USER.fullmatch(user):
            raise Error("INPUT_INVALID", "Invalid application user ID.")
        parts = public_key.strip().split()
        if len(parts) < 2 or parts[0] != "ssh-ed25519":
            raise Error("INPUT_INVALID", "Beta enrollment accepts an Ed25519 public key.")
        try:
            raw = base64.b64decode(parts[1], validate=True)
        except ValueError:
            raise Error("INPUT_INVALID", "Malformed public key.")
        if len(raw) != 51 or raw[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
            raise Error("INPUT_INVALID", "Malformed Ed25519 public key.")
        key = parts[0] + " " + parts[1]
        with self.locked("administration"):
            keys = json.loads((self.root / "policy/keys.json").read_text())
            existing = next(((k, v) for k, v in keys.items() if v["public_key"] == key), None)
            if existing and existing[1]["user"] != user:
                raise Error("AUTH_DENIED", "This key already belongs to another application user.")
            key_id = existing[0] if existing else uuid4().hex
            keys[key_id] = {"user": user, "public_key": key, "enabled": True}
            atomic(self.root / "policy/keys.json", json.dumps(keys, indent=2) + "\n")
            command = shlex.join([str(safe_path(entrypoint)), "hub", "serve", "--hub-root", str(self.root), "--key-id", key_id])
            quoted = command.replace("\\", "\\\\").replace('"', '\\"')
            return {"key_id": key_id, "user": user,
                    "authorized_key": 'restrict,command="' + quoted + '" ' + key + " cosmesh:" + key_id}

    def identity(self, key_id):
        self.settings()
        keys = json.loads((self.root / "policy/keys.json").read_text())
        key = keys.get(key_id)
        if not key or not key.get("enabled"):
            raise Error("AUTH_DENIED", "Unknown or revoked Hub key.")
        return key["user"]

    def authorize_key(self, registration):
        """Admin-only enrollment; preserve existing login keys and a private backup."""
        with self.locked("administration"):
            directory = safe_path(Path.home() / ".ssh")
            directory.mkdir(mode=0o700, exist_ok=True)
            path = safe_path(directory / "authorized_keys")
            if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o022:
                raise Error("PATH_INVALID", "Unsafe SSH directory ownership or write permissions.")
            if path.exists() and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022):
                raise Error("PATH_INVALID", "Unsafe authorized_keys ownership or write permissions.")
            previous = path.read_text() if path.exists() else ""
            line = registration["authorized_key"]
            if line not in previous.splitlines():
                public = line.split('" ')[-1].split()[:2]
                if any(" ".join(public) in existing for existing in previous.splitlines()):
                    raise Error("KEY_ALREADY_AUTHORIZED", "Use a dedicated key that has no existing login authorization.")
                atomic(self.root / "policy" / ("authorized_keys.before-" + uuid4().hex), previous)
                atomic(path, previous.rstrip() + "\n" + line + "\n")
            return {"key_id": registration["key_id"], "user": registration["user"], "authorized": True}

    def revoke_key(self, key_id):
        with self.locked("administration"):
            path = self.root / "policy/keys.json"
            keys = json.loads(path.read_text())
            if key_id not in keys:
                raise Error("NOT_FOUND", "Unknown key ID.")
            keys[key_id]["enabled"] = False
            atomic(path, json.dumps(keys, indent=2) + "\n")
            return {"key_id": key_id, "enabled": False}

    def grant(self, name, user, access):
        if not USER.fullmatch(user) or access not in {"read", "write", "none"}:
            raise Error("INPUT_INVALID", "Invalid repository grant.")
        with self.locked("administration"):
            policies = json.loads((self.root / "policy/repos.json").read_text())
            if name not in policies:
                raise Error("NOT_FOUND", "Unknown repository.")
            item = policies[name]
            if item["product"] == "cosbase" and user != item["owner"] and access != "none":
                raise Error("POLICY_DENIED", "CosBase repositories have a single private owner.")
            if item.get('system_feedback') or item.get('thread_format'):
                blocked = set(item.get('blocked', []))
                blocked.add(user) if access == 'none' else blocked.discard(user)
                item['blocked'] = sorted(blocked)
            for role in ("readers", "writers"):
                item[role] = [u for u in item[role] if u != user]
            if access != "none":
                item["readers"].append(user)
            if access == "write":
                item["writers"].append(user)
            atomic(self.root / "policy/repos.json", json.dumps(policies, indent=2) + "\n")
            return {"repo": name, "user": user, "access": access}

    @contextmanager
    def locked(self, name):
        self.settings()
        if not REPO.fullmatch(name):
            raise Error("INPUT_INVALID", "Invalid repository identifier.")
        path = self.root / "locks" / (name + ".lock")
        if path.is_symlink():
            raise Error("PATH_INVALID", "Hub lock cannot be a symlink.")
        with path.open("a+") as handle:
            fcntl.lockf(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.lockf(handle, fcntl.LOCK_UN)

    def policy(self, name, user, write=False):
        self.settings()
        community = self.community()
        if community:
            community.check(user)
        policy = json.loads((self.root / 'policy/repos.json').read_text()).get(name)
        if not policy:
            raise Error('AUTH_DENIED', 'Repository access denied.')
        if write and policy.get('thread_format') and community:
            community.check(user,write=True)
        # Kernel/SSH callers are already authenticated. Keep legacy registered IDs compatible.
        try:
            pwd.getpwnam(user)
            known = True
        except KeyError:
            keys = json.loads((self.root / 'policy/keys.json').read_text())
            known = any(k['user'] == user and k.get('enabled') for k in keys.values())
        known = known or user in policy['readers']
        if policy.get('thread_format'):
            allowed = known and (not write or user not in policy.get('blocked', []))
        elif policy['product'] == 'cosmesh' and not policy.get('system_feedback'):
            allowed = known if not write else user in policy['writers']
            if write and known and not allowed and user not in policy['readers']:
                store = self.repo(name)
                revision = store.git('rev-parse', '--verify', 'refs/heads/main', check=False)
                if revision.returncode == 0:
                    docs = store.documents(store.snapshot(revision.stdout.strip())[1])
                    allowed = any(d['meta']['kind'] == 'project' and
                                  user in [d['meta']['lead']] + d['meta']['members'] for d in docs.values())
        else:
            implicit = policy.get('system_feedback') and known and user not in set(policy['readers']) | set(policy['writers'])
            allowed = user not in policy.get('blocked', []) and (implicit or user in policy['writers' if write else 'readers'])
        if not allowed:
            raise Error('AUTH_DENIED', 'Repository access denied.')
        return policy

    def repo(self, name):
        if not REPO.fullmatch(name):
            raise Error("INPUT_INVALID", "Invalid repository name.")
        return BareStore(self.root / "repos" / (name + ".git"))

    def create_repo(self, name, product, entrypoint, owner=None, system_feedback=False, title=None, seed=False, thread_format=False):
        if product not in {"cosmesh", "cosbase"}:
            raise Error("INPUT_INVALID", "Unknown repository product.")
        if system_feedback and product != 'cosmesh':
            raise Error('POLICY_DENIED', 'System feedback uses shared document rules.')
        if thread_format and (product != 'cosmesh' or system_feedback):
            raise Error('INPUT_INVALID', 'Thread spaces use ordinary Mesh storage.')
        owner = owner or actor()
        if not USER.fullmatch(owner):
            raise Error("INPUT_INVALID", "Invalid repository owner.")
        with self.locked("administration"):
            policies = json.loads((self.root / "policy/repos.json").read_text())
            store = self.repo(name)
            if name in policies or store.root.exists():
                raise Error("REPO_EXISTS", "Repository already exists.")
            store.root.mkdir(mode=0o700)
            store.git("init", "--bare", "-q")
            store.git("symbolic-ref", "HEAD", "refs/heads/main")
            store.git("config", "receive.denyNonFastForwards", "true")
            store.git("config", "receive.denyDeletes", "true")
            # A fixed entrypoint never interpolates client-provided shell fragments.
            hook = receive_hook(entrypoint, self.root, name)
            atomic(store.root / "hooks/pre-receive", hook)
            os.chmod(store.root / "hooks/pre-receive", 0o700)
            policies[name] = {"product": product, "owner": owner, "readers": [owner], "writers": [owner]}
            if thread_format:
                if product != 'cosmesh' or system_feedback:
                    raise Error('INPUT_INVALID', 'Thread spaces use ordinary Mesh storage.')
                policies[name].update(thread_format=True, blocked=[])
            if title is not None:
                policies[name]['title'] = title
            if seed:
                with tempfile.TemporaryDirectory(prefix='.space-', dir=self.root) as folder:
                    work = Store(Path(folder) / 'seed')
                    if thread_format:
                        work.initialize({'schema_version': 2, 'product': 'cosmesh', 'user': owner,
                                         'timezone': 'Asia/Shanghai', 'mode': 'local_unverified', 'scope': 'mesh'})
                    else:
                        initialize(work, 'cosmesh', owner, 'Asia/Shanghai')
                    store.git('fetch', '--no-tags', str(work.root), 'HEAD:refs/heads/main')
            if system_feedback:
                policies[name].update(system_feedback=True, blocked=[])
            atomic(self.root / "policy/repos.json", json.dumps(policies, indent=2) + "\n")
            return {"name": name, "path": str(store.root), **policies[name]}

    def spaces(self, payload, user):
        """One public Mesh space per authenticated server account."""
        if isinstance(payload.get('method'),str) and payload['method'].startswith('community_'):
            community=self.community()
            if not community:raise Error('FEATURE_UNAVAILABLE','This Hub has not enabled its knowledge library and user directory.')
            return community.rpc(payload,user,self)
        if self.community():self.community().check(user)
        if payload in ({'method': 'list_threads'}, {'method': 'ensure_threads'}):
            return self.thread_spaces(payload, user)
        if payload not in ({'method': 'list'}, {'method': 'ensure'}):
            raise Error('INPUT_INVALID', 'Use space list or discover.')
        if payload['method'] == 'ensure':
            name = 'u-' + hashlib.sha256(user.encode()).hexdigest()[:24]
            with self.locked(name):
                policies = json.loads((self.root / 'policy/repos.json').read_text())
                if name not in policies:
                    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
                    entry = Path(prefix) / 'bin/cosmesh' if prefix else Path(sys.executable).with_name('cosmesh')
                    self.create_repo(name, 'cosmesh', entry, user, title=user, seed=True)
                    self.audit(name, 'space_initialized', actor=user)
                elif policies[name]['owner'] != user or policies[name]['product'] != 'cosmesh':
                    raise Error('POLICY_DENIED', 'Personal Mesh space identity conflicts with existing storage.')
        self.settings()
        policies = json.loads((self.root / 'policy/repos.json').read_text())
        result = []
        for name, item in sorted(policies.items()):
            if item['product'] != 'cosmesh' or item.get('system_feedback') or item.get('thread_format'):
                continue
            store = self.repo(name)
            revision = store.git('rev-parse', '--verify', 'refs/heads/main', check=False)
            result.append({'id': name, 'title': item.get('title', name), 'owner': item['owner'],
                           'personal': name == 'u-' + hashlib.sha256(item['owner'].encode()).hexdigest()[:24],
                           'public_read': True, 'initialized': revision.returncode == 0})
        return {'spaces': result, 'own_id': 'u-' + hashlib.sha256(user.encode()).hexdigest()[:24]}

    def _maintenance_entry(self, target):
        if target is None:
            return None
        from .thread_protocol import identifier, content_version, thread_state
        if not isinstance(target, dict) or set(target) != {'repo', 'id'}:
            raise Error('INPUT_INVALID', 'Maintenance entry requires a shared repo and Thread ID.')
        identifier(target['id'], 'W')
        if not isinstance(target['repo'], str) or not REPO.fullmatch(target['repo']):
            raise Error('INPUT_INVALID', 'Invalid maintenance repository.')
        policy = json.loads((self.root / 'policy/repos.json').read_text()).get(target['repo'])
        if not policy or not policy.get('thread_format'):
            raise Error('POLICY_DENIED', 'Maintenance must point to a shared Thread space.')
        store = self.repo(target['repo'])
        revision = store.git('rev-parse', 'refs/heads/main')
        model = view(store, store.snapshot(revision)[1], 'cosmesh')
        doc = model.docs.get(target['id'])
        if not doc or doc['meta']['kind'] != 'thread':
            raise Error('NOT_FOUND', 'Maintenance Thread is not published in that space.')
        records = [d['meta'] for d in model.docs.values() if d['meta'].get('thread') == target['id']]
        if thread_state(records)['lifecycle'] != 'active':
            raise Error('STATE_CONFLICT', 'Maintenance Thread must be active.')
        return {'source': {**target, 'version': content_version(doc)},
                'maintainer': doc['meta']['author'], 'title': doc['meta']['title']}

    def maintenance(self, repo=None, thread=None, clear=False):
        with self.locked('maintenance-entry'):
            settings = self.settings()
            if clear and (repo or thread):
                raise Error('INPUT_INVALID', 'Choose a target or --clear.')
            if (repo is None) != (thread is None):
                raise Error('INPUT_INVALID', 'Provide both --repo and --thread.')
            target = {'repo': repo, 'id': thread} if repo is not None else settings.get('maintenance_thread')
            if clear: target = None
            entry = self._maintenance_entry(target)
            if clear or repo is not None:
                if target is None: settings.pop('maintenance_thread', None)
                else: settings['maintenance_thread'] = target
                atomic(self.root / 'hub.json', json.dumps(settings, indent=2) + '\n')
            return {'configured': entry is not None, 'maintenance': entry}

    def thread_spaces(self, payload, user):
        name = 'w-' + hashlib.sha256(user.encode()).hexdigest()[:24]
        if payload['method'] == 'ensure_threads':
            with self.locked(name):
                policies = json.loads((self.root / 'policy/repos.json').read_text())
                if name not in policies:
                    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
                    entry = Path(prefix) / 'bin/cosmesh' if prefix else Path(sys.executable).with_name('cosmesh')
                    self.create_repo(name, 'cosmesh', entry, user, title=user, seed=True, thread_format=True)
                elif policies[name]['owner'] != user or not policies[name].get('thread_format'):
                    raise Error('POLICY_DENIED', 'Thread space conflicts with existing storage.')
        policies = json.loads((self.root / 'policy/repos.json').read_text())
        spaces = [{'id': k, 'owner': v['owner'], 'public_read': True, 'public_append': True}
                  for k, v in sorted(policies.items()) if v.get('thread_format')]
        versions = {}
        for space in spaces:
            with self.locked(space['id']): versions[space['id']] = self.status(space['id'], user)
        result = {'community_enabled':bool(self.community()), 'own_id':name, 'spaces':spaces, 'versions':versions,
                  'maintenance':None}
        try: result['maintenance'] = self._maintenance_entry(self.settings().get('maintenance_thread'))
        except Error as exc: result['maintenance_error'] = exc.code
        return result

    def validate_thread_references(self, name, model, before, after):
        from .thread_protocol import check_record, content_version
        from .protocol import decode
        def resolve(ref):
            if ref['repo']=='library':return self.resolve_shared_reference(ref)
            source_name = ref['repo']
            if source_name == name:
                candidates = (after, before)
                for files in candidates:
                    text = files.get('documents/' + ref['id'] + '.md')
                    if text:
                        doc = decode(text)
                        if content_version(doc) == ref['version']:
                            return doc
            policy = json.loads((self.root / 'policy/repos.json').read_text()).get(source_name)
            if not policy or not policy.get('thread_format'):
                raise Error('REFERENCE_UNAVAILABLE', 'A shared source must belong to a Thread space.')
            # Only the receiving repo may see Git quarantine objects. Other sources
            # are read from their accepted object databases without inherited GIT_* state.
            source = Store(self.repo(source_name).root)
            revision = source.git('rev-parse', '--verify', 'refs/heads/main', check=False)
            if not revision.returncode:
                path = 'documents/' + ref['id'] + '.md'
                commits = source.git('log', '-1000', '--format=%H', revision.stdout.strip(), '--', path).splitlines()
                for commit in commits:
                    value = source.git('show', commit + ':' + path, check=False)
                    if not value.returncode:
                        doc = decode(value.stdout)
                        if content_version(doc) == ref['version']:
                            return doc
            raise Error('REFERENCE_UNAVAILABLE', 'Referenced source version has not been accepted.')
        for key, doc in model.docs.items():
            if doc['meta']['kind'] == 'entry' and after.get('documents/' + key + '.md') != before.get('documents/' + key + '.md'):
                if doc['meta'].get('lifecycle') == 'merged' and doc['meta']['refs'][0]['repo'] != name:
                    raise Error('PRIVATE_REFERENCE', 'A continuing Thread must stay in the same authoritative space.')
                check_record(doc, model.docs[doc['meta']['thread']], resolve)

    def community(self):
        path=self.root/'community.json'
        if not path.is_file():return None
        if path.is_symlink() or path.stat().st_uid!=os.geteuid() or path.stat().st_mode & 0o077:
            raise Error('AUTH_DENIED','Unsafe community configuration.')
        from .community import Community
        return Community(json.loads(path.read_text())['root'],self.settings()['admin'])

    def initialize_community(self,root):
        self.settings()
        from .community import Community
        root=safe_path(root)
        existing=self.community()
        if existing and existing.root!=root:raise Error('STORE_EXISTS','Existing knowledge storage is preserved.')
        value=Community(root,self.settings()['admin']).initialize()
        atomic(self.root/'community.json',json.dumps({'format':1,'root':str(root)})+'\n')
        os.chmod(self.root/'community.json',0o600)
        return value

    def resolve_shared_reference(self,ref):
        from .thread_protocol import reference,content_version
        from .protocol import decode
        reference(ref)
        if ref['repo']=='library':
            community=self.community()
            if community:return community.resource(ref)
        policy=json.loads((self.root/'policy/repos.json').read_text()).get(ref['repo'])
        if not policy or not policy.get('thread_format'):raise Error('REFERENCE_UNAVAILABLE','Shared source space is unavailable.')
        source=Store(self.repo(ref['repo']).root)
        revision=source.git('rev-parse','--verify','refs/heads/main',check=False)
        if not revision.returncode:
            path='documents/'+ref['id']+'.md'
            for commit in source.git('log','-1000','--format=%H',revision.stdout.strip(),'--',path).splitlines():
                value=source.git('show',commit+':'+path,check=False)
                if not value.returncode:
                    doc=decode(value.stdout)
                    if content_version(doc)==ref['version']:return doc
        raise Error('REFERENCE_UNAVAILABLE','Source version has not been accepted.')

    def audit(self, name, phase, **data):
        record = {"at": datetime.now(timezone.utc).isoformat(), "phase": phase, **data}
        with (self.root / "audit" / (name + ".jsonl")).open("a") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def status(self, name, user):
        policy = self.policy(name, user)
        store = self.repo(name)
        revision = store.git("rev-parse", "--verify", "refs/heads/main", check=False)
        return {"protocol": 1, "repo": name, "product": policy["product"], "actor": user,
                "host": self.settings()["host"], "revision": revision.stdout.strip() if revision.returncode == 0 else None,
                'system_feedback': bool(policy.get('system_feedback')),
                'thread_format': bool(policy.get('thread_format')),
                "mode": "key_mapped_ssh_beta"}

    def validate_push(self, name, user, updates):
        policy = self.policy(name, user, write=True)
        if len(updates) != 1 or updates[0][2] != "refs/heads/main":
            raise Error("REF_DENIED", "Only a single refs/heads/main update is accepted.")
        old, new, _ = updates[0]
        if new == ZERO:
            raise Error("REF_DENIED", "Branch deletion is disabled.")
        store = self.repo(name)
        if old != ZERO and store.git("merge-base", "--is-ancestor", old, new, check=False).returncode:
            raise Error("HISTORY_PROTECTED", "Non-fast-forward pushes are disabled.")
        commits = store.git("rev-list", "--reverse", new, *(["^" + old] if old != ZERO else [])).splitlines()
        if len(commits) > 1000:
            raise Error("INPUT_TOO_LARGE", "A push cannot introduce more than 1000 commits.")
        for commit in commits:
            _, after = store.snapshot(commit)
            parents = store.git("rev-list", "--parents", "-n", "1", commit).split()[1:]
            before = store.snapshot(parents[0])[1] if parents else {}
            # Accepted history arriving through another merge parent is trusted by object identity.
            if len(parents) > 1:
                for parent in parents[1:]:
                    if old != ZERO and store.git("merge-base", "--is-ancestor", parent, old, check=False).returncode == 0:
                        for path, text in store.snapshot(parent)[1].items():
                            if after.get(path) == text:
                                before[path] = text
            model = validate_change(store, before, after, policy["product"], user)
            if policy.get('thread_format'):
                self.validate_thread_references(name, model, before, after)
                community=self.community()
                if community:
                    person=community.check(user,write=True)
                    for key,doc in model.docs.items():
                        if after.get('documents/'+key+'.md')!=before.get('documents/'+key+'.md') and doc['meta'].get('author_id') and doc['meta']['author_id']!=person['id']:
                            raise Error('AUTH_DENIED','Stable author ID must match the authenticated account.')
            if policy.get('system_feedback'):
                from .feedback import validate_snapshot
                validate_snapshot(model)
                if any(path.startswith('documents/') and text != after.get(path) for path, text in before.items()):
                    raise Error('HISTORY_PROTECTED', 'Accepted system feedback is append-only.')
        # Check merged control streams against the actual accepted main as well.
        final = view(store, store.snapshot(new)[1], policy["product"])
        if policy.get("thread_format"):
            if final.config.get("schema_version") != 2 or final.config.get("user") != policy["owner"]:
                raise Error("SCHEMA_INVALID", "Thread repository identity differs from its policy.")
        elif final.config.get("schema_version") == 2:
            raise Error("SCHEMA_UNSUPPORTED", "Thread format requires a dedicated Thread space.")
        elif policy["product"] == "cosmesh":
            for doc in final.docs.values():
                meta = doc["meta"]
                if meta["kind"] in {"project", "task"}:
                    members = list(meta["members"])
                    if any(not self.policy(name, member) for member in members):
                        raise Error("NEEDS_PROJECT_ACCESS", "A named member lacks repository read access.")
                if meta["kind"] == "update" and meta.get("type") == "invite":
                    project = final.docs[meta["project"]]["meta"]
                    invited = meta["action"]["to"]
                    if invited not in [project["lead"]] + project["members"]:
                        raise Error("NEEDS_PROJECT_ACCESS", "Invitation requires repository and project membership.")
        return {"commits_checked": len(commits), "revision": new}

    def serve(self, key_id):
        if not os.environ.get("SSH_CONNECTION"):
            raise Error("AUTH_DENIED", "This entrypoint requires an authenticated SSH connection.")
        user = self.identity(key_id)
        try:
            parts = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
        except ValueError:
            raise Error("INPUT_INVALID", "Malformed SSH request.")
        if parts in (['cosmesh-relay', 'v1'], ['cosmesh-relay', 'v2']):
            keys = json.loads((self.root / 'policy/keys.json').read_text())
            if not keys[key_id].get('relay') or user != self.settings()['admin']:
                raise Error('AUTH_DENIED', 'This key is not a trusted service relay.')
            from .gateway import relay_entry
            return relay_entry(self, key_id, stream=parts[1] == 'v2')
        if len(parts) != 2:
            raise Error("COMMAND_DENIED", "Only fixed Git operations and cosmesh-status are accepted.")
        operation, target = parts
        if operation == "cosmesh-status":
            name = target
        elif operation in {"git-upload-pack", "git-receive-pack"}:
            match = re.fullmatch(r"/?([a-z0-9][a-z0-9-]{0,63})\.git", target)
            if not match:
                raise Error("PATH_INVALID", "Only logical repository names are accepted.")
            name = match.group(1)
        else:
            raise Error("COMMAND_DENIED", "Arbitrary shell commands are disabled for this key.")
        return self.dispatch(name, operation, user, key_id=key_id)

    def dispatch(self, name, operation, user, key_id=None, system_uid=None, channel=None, endpoint=None):
        if operation not in {'cosmesh-status', 'git-upload-pack', 'git-receive-pack'}:
            raise Error('COMMAND_DENIED', 'Unsupported Hub operation.')
        self.policy(name, user, operation == 'git-receive-pack')
        with self.locked(name):
            if operation == "cosmesh-status":
                value = self.status(name, user)
                if system_uid is not None:
                    value.update(uid=system_uid, mode='system_identity')
                if channel is None:
                    print(json.dumps(value))
                else:
                    from .gateway import send
                    send(channel, {'ok': True, 'endpoint': endpoint, 'data': value})
                return 0
            request = str(uuid4())
            env = git_env()
            env.pop('COSMESH_RECEIVE_SYSTEM_UID', None)
            env.update(COSMESH_HUB_ROOT=str(self.root), COSMESH_RECEIVE_ACTOR=user,
                       COSMESH_RECEIVE_REPO=name, COSMESH_RECEIVE_REQUEST=request,
                       COSMESH_RECEIVE_KEY=key_id or '')
            if system_uid is not None:
                env['COSMESH_RECEIVE_SYSTEM_UID'] = str(system_uid)
            if channel is not None:
                from .gateway import send
                send(channel, {'ok': True, 'endpoint': endpoint, 'actor': user, 'uid': system_uid})
            self.audit(name, "begin", request=request, actor=user, operation=operation)
            store = self.repo(name)
            kwargs = {'stdin': channel, 'stdout': channel, 'stderr': subprocess.PIPE} if channel is not None else {}
            try:
                process = subprocess.run(["git", "-c", "core.hooksPath=" + str(store.root / "hooks"),
                                          operation.removeprefix("git-"), str(store.root)], env=env, timeout=90, **kwargs)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
            current = self.status(name, user)["revision"]
            self.audit(name, "complete", request=request, actor=user, operation=operation,
                       exit_code=code, revision=current)
            if code==0 and operation=='git-receive-pack' and self.community():
                try:self.community().reconcile(self)
                except (Error,sqlite3.Error) as exc:
                    self.audit(name,'archive_pending',error=getattr(exc,'code','CATALOG_UNAVAILABLE'))
            return code
