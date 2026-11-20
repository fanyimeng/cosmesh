"""Explicit component installation with immutable bundles and resumable host changes."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import shlex
from pathlib import Path
from uuid import uuid4

from . import component_artifacts as artifacts
from . import component_dsh as dsh
from .errors import Error
from .store import atomic, safe_path


def storage(explicit=None):
    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
    return safe_path(explicit or (Path(prefix) / 'components' if prefix else
                                 Path.home() / '.local/share/cosmesh/components'))


def state_path(root, profile):
    identity = str(dsh.profile_path(profile))
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return safe_path(root / 'profiles' / key / 'state.json')


def read_state(path):
    if not path.exists():
        return {'schema': 1, 'components': {}}
    state = json.loads(path.read_text())
    if state.get('schema') != 1 or not isinstance(state.get('components'), dict):
        raise Error('COMPONENT_STATE', 'Unknown component state; preserve it before repair.')
    return state


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = safe_path(root / '.lock')
    with path.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error('STORE_BUSY', 'Another component operation is running; retry after it finishes.') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def same(left, right):
    if left is None or right is None:
        return left is right
    return all(left.get(key) == right.get(key) for key in ('version', 'path'))


def release_info(args, root, item):
    cached = safe_path(root / 'catalog.json')
    if cached.is_file():
        item = artifacts.merge_catalog(item, json.loads(cached.read_text()))
    if (args.component_command in ('install', 'update') and not getattr(args, 'offline', False)
            and not getattr(args, 'artifact', None) and not getattr(args, 'dry_run', False)):
        from .client import config, fetch
        _, cfg = config()
        raw = fetch(cfg, 'components/catalog.json')
        if len(raw) > 1024 * 1024:
            raise Error('COMPONENT_CATALOG', 'Component catalog exceeds the size limit.')
        item = artifacts.merge_catalog(item, json.loads(raw))
        atomic(cached, json.dumps({'schema': 'cosmesh.components.v1', 'components': [item]}, indent=2) + '\n')
    return item


def status(root, profile, item, explicit_root=None):
    saved = read_state(state_path(root, profile))['components'].get(item['id'], {})
    issue = None
    try:
        actual = dsh.installed(profile, item['package'])
    except Error as exc:
        actual, issue = None, exc.as_dict()
    action = None
    if not issue and not saved.get('pending') and (not saved.get('current') or same(actual, saved['current'])):
        if actual is None:
            action = 'install'
        elif actual['version'] in item['releases'] and (
                tuple(map(int, actual['version'].split('.'))) < tuple(map(int, item['version'].split('.')))
        ):
            action = 'update'
    command = ['cosmesh', 'component', action, 'dsh', '--profile', profile, '--host-stopped'] if action else []
    if command and explicit_root:
        command += ['--component-root', str(root)]
    return {'component': item['id'], 'profile': profile, 'recommended_version': item['version'],
            'next_command': shlex.join(command) if command else None,
            'installed': actual, 'managed': saved, 'host_issue': issue,
            'matches_managed': None if issue else same(actual, saved.get('current')),
            'activation': 'unknown', 'read_only': True,
            'hint': 'Installation metadata does not prove runtime activation; verify through DSH after restart.'}


def verified_existing(root, item, actual, offline):
    if actual is None:
        return
    version = actual['version']
    if version not in item['releases']:
        raise Error('COMPONENT_CONFLICT', 'Installed version is outside the compatibility catalog; preserve and manage it through DSH.')
    _, files = artifacts.prepare(root, item, version, offline=offline)
    artifacts.verify_tree(Path(actual['path']), files)


def change(args, root, item):
    profile = args.profile
    path = state_path(root, profile)
    verb = args.component_command
    cli = dsh.executable(args.dsh_cli)
    state = read_state(path)
    saved = state['components'].get(item['id'], {})
    actual = dsh.installed(profile, item['package'])
    pending = saved.get('pending')
    if verb == 'rollback':
        if pending:
            target = pending['target'] if pending['action'] == 'rollback' else pending['before']
        elif 'previous' in saved:
            target = saved['previous']
        else:
            raise Error('COMPONENT_NO_ROLLBACK', 'No previous component installation is recorded.')
        version = target['version'] if target else None
    else:
        version = args.version or item['version']
        if version not in item['releases']:
            raise Error('COMPONENT_VERSION', 'Version is absent from the installed compatibility catalog.')
        target = {'version': version, 'path': str(root / 'versions' / (item['id'] + '-' + version) / 'bundle')}
        if pending and (pending['action'] != verb or not same(target, pending['target'])):
            raise Error('COMPONENT_PENDING', 'Resume the same operation or use component rollback before choosing another version.')
    checked_version = version or (actual['version'] if actual else
                                 pending['target']['version'] if pending and pending.get('target') else item['version'])
    detected = dsh.compatibility(cli, item, checked_version)
    plan = {'component': item['id'], 'action': verb, 'profile': profile, 'target': target,
            'before': actual, 'dsh_version': detected, 'host_stopped_required': True,
            'activation': 'restart_required', 'thread_data_changed': False}
    if getattr(args, 'dry_run', False):
        return dict(plan, dry_run=True, changed=False)
    if not args.host_stopped:
        raise Error('HOST_MUST_BE_STOPPED', 'Finish initialization turns and stop the target DSH host, then repeat with --host-stopped.')
    offline = getattr(args, 'offline', False)
    # Cache the requested release first so an identical external installation can be adopted offline.
    if target:
        _, files = artifacts.prepare(root, item, version, getattr(args, 'artifact', None), offline)
        artifacts.verify_tree(Path(target['path']), files)
    if pending:
        if not same(actual, pending['before']) and not same(actual, pending['target']):
            raise Error('COMPONENT_DRIFT', 'DSH changed outside the pending operation; preserve its journal and reconcile through DSH.')
    elif saved.get('current') and not same(actual, saved['current']):
        raise Error('COMPONENT_DRIFT', 'The DSH installation differs from the managed record; inspect it before changing versions.')
    verified_existing(root, item, actual, offline)
    if not pending and same(actual, target):
        return dict(plan, changed=False, activation='unknown', hint='Version already installed; runtime activation remains host-owned.')
    if not pending:
        backup = path.parent / 'backups' / uuid4().hex
        dsh.backup(profile, backup)
        pending = {'action': verb, 'target': target, 'before': actual, 'backup': str(backup)}
        saved = dict(saved, pending=pending)
        state['components'][item['id']] = saved
        atomic(path, json.dumps(state, indent=2) + '\n')
    # A rollback of a failed install restores the previous host selection via the same official API.
    cancelling = verb == 'rollback' and pending['action'] != 'rollback'
    if not same(actual, target):
        dsh.apply(cli, profile, Path(target['path']) if target else None, item['package'], offline)
    after = dsh.installed(profile, item['package'])
    if not same(after, target):
        raise Error('COMPONENT_HOST_FAILED', 'DSH did not select the requested bundle; keep the pending journal and use rollback or retry.')
    if after:
        artifacts.verify_tree(Path(after['path']), files)
    state['components'][item['id']] = {
        'current': after, 'previous': saved.get('previous') if cancelling else pending['before'],
        'last_backup': pending['backup'], 'activation': 'restart_required'}
    atomic(path, json.dumps(state, indent=2) + '\n')
    return dict(plan, changed=True, installed=after, previous=state['components'][item['id']]['previous'],
                backup=pending['backup'], recovered=pending['action'] != verb or same(actual, target),
                hint='Start DSH and verify the component is running. Original bundles, profile backups and Thread data are retained.')


def execute(args):
    if any(getattr(args, name, None) for name in ('root', 'revision', 'expect_revision', 'idempotency_key', 'require_fresh')):
        raise Error('INPUT_INVALID', 'Component commands use --component-root and their own recovery journal, independently of Thread options.')
    if args.component_command == 'list':
        return {'components': artifacts.catalog(), 'network_used': False}, None
    root = storage(args.component_root)
    item = artifacts.select(args.component)
    if args.component_command == 'status':
        item = release_info(args, root, item)
        profiles = [args.profile] if args.profile else dsh.profiles()
        rows = [status(root, profile, item, args.component_root) for profile in profiles]
        if len(rows) == 1:
            return rows[0], None
        return {'component': item['id'], 'recommended_version': item['version'], 'profiles': rows,
                'read_only': True, 'activation': 'unknown',
                'hint': 'Choose a profile explicitly for changes.' if rows else
                        'Initialize the intended profile through DSH first.'}, None
    # A plan must not create even a lock or cache directory.
    if getattr(args, 'dry_run', False):
        item = release_info(args, root, item)
        return change(args, root, item), None
    with locked(root):
        item = release_info(args, root, item)
        return change(args, root, item), None
