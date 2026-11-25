"""Publish complete, owner-approved work updates once for all its named readers."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import subprocess
import sys
import tempfile
import time

from . import _fcntl as fcntl
from .connections import networked
from .errors import Error
from .personal import Personal, initialize
from .personal_protocol import digest, fingerprint, packed, read_json, save_json
from .store import safe_path
from .thread_access_cli import Online
from .thread_personal import ThreadPersonal
from .thread_protocol import identifier
from .threads import Workbench


def directory(wb):
    return wb.base.path('.git/cosmesh-workspace-progress')


def policy(wb, tid):
    return directory(wb) / (identifier(tid, 'W') + '.json')


def check_identity(online, expected=None):
    if online.info.get('workspace_protocol') != 'workspace.v1':
        raise Error('FEATURE_UNAVAILABLE', 'The Hub must support recipient workspace access.')
    identity = {'hub': online.info.get('hub_id'), 'actor': online.info.get('actor')}
    if not all(identity.values()) or expected and any(identity[k] != expected[k] for k in identity):
        raise Error('AUTH_DENIED', 'Workspace progress belongs to its original Hub and account.')
    return identity


def files_state(personal, tid):
    state = personal.state
    return {aid: [row, state.get('asset_copies', {}).get(aid), state.get('asset_paths', {}).get(aid),
                  state.get('removed_assets', {}).get(aid)]
            for aid, row in state['assets'].items() if row['thread'] == tid}


def file_stamp(personal, tid, verified=False):
    rows = []
    for aid, (row, copy, relative, removed) in sorted(files_state(personal, tid).items()):
        if removed:
            continue
        try:
            path = personal.source_path(aid)
            stamp = (copy or {}).get('fingerprint') if verified else fingerprint(safe_path(path).stat())
        except (Error, OSError) as exc:
            path, stamp = None, getattr(exc, 'code', 'file_unavailable')
        rows.append([aid, row['blob'], row['name'], relative, row['science'], path, stamp])
    return digest(packed(rows).encode())


def cursor(wb, personal, tid, verified=False):
    from .collector import binding_stamp
    return {'head': wb.base.head, 'bindings': binding_stamp(wb, tid),
            'files': file_stamp(personal, tid, verified)}


def changed(wb, tid, previous, current, history):
    if not previous or any(previous[k] != current[k] for k in ('bindings', 'files')):
        return True
    before, head = previous['head'], current['head']
    if before == head:
        return False
    # Inspect the touched paths (including edit-then-revert), once per revision range.
    key = (before, head)
    if key not in history:
        if wb.base.git('merge-base', '--is-ancestor', before, head, check=False).returncode:
            return True
        paths = wb.base.git('log', '--format=', '--name-only', before + '..' + head, '--', 'documents')
        wb.load()
        history[key] = {p.stem for p in map(Path, paths.splitlines()) if p.suffix == '.md'}
    return any(doc == tid or wb.catalog.get(doc, {}).get('doc', {}).get('meta', {}).get('thread') == tid
               for doc in history[key])


def prepare_workspace(wb, online, tid):
    """Freeze the accepted personal snapshot and a durable cursor for the eventual grant."""
    from .collector import owned
    identity = check_identity(online)
    with wb.base.locked():
        wb.load()
        tid = owned(wb, tid)
        initialize(wb)
        p = ThreadPersonal(wb)
        if not p.state['connection']:
            p.connect()
        if p.state['connection'] != online.config or p.state['actor'] != identity['actor']:
            raise Error('STATE_CONFLICT', 'Use the same personal relay and message Hub for this work.')
        head = p.rpc({'method': 'personal_thread_head', 'thread': tid})['sha']
        existing = read_json(policy(wb, tid), {})
        if (existing.get('enabled') and existing.get('snapshot') == head and head
                and all(existing.get(k) == value for k, value in identity.items())):
            observed = cursor(wb, p, tid)
            if not changed(wb, tid, existing['cursor'], observed, {}):
                return dict(identity, thread=tid, snapshot=head, cursor=observed)
        original = deepcopy(files_state(p, tid))
        remote = p.load_manifest(head, tid) if head else {}

    confirmed = {r['blob']: r['size'] for r in remote.get('assets', {}).values() if r['blob']}
    ids = []
    for aid, (row, copy, relative, removed) in original.items():
        if removed:
            continue
        if row['device'] == p.state['device'] or copy and copy['device'] == p.state['device']:
            ids.append(aid)
        elif not row['blob'] or confirmed.get(row['blob']) != row['size']:
            raise Error('WORKSPACE_INCOMPLETE', 'A registered file still needs its source device.', row['name'])
    # Other writers can commit progress while large files are staged and transferred.
    with tempfile.TemporaryDirectory(dir=p.staging) as staging:
        p.staging = Path(staging)
        transfers = p.push(ids, persist=False, confirmed=confirmed)['files'] if ids else []
    with wb.base.locked():
        wb.load()
        owned(wb, tid)
        current = ThreadPersonal(wb)
        if (current.state['connection'] != online.config or current.state['actor'] != identity['actor']
                or files_state(current, tid) != original):
            raise Error('STATE_CONFLICT', 'The local file registrations or Hub changed during sync. Retry this work.')
        problems = []
        for row in transfers:
            aid = row['id']
            if row['state'] != 'server_available':
                problems.append({'file': row['name'], 'reason': row['reason']})
                continue
            current.state['assets'][aid] = p.state['assets'][aid]
            current.state.setdefault('asset_copies', {})[aid] = p.state['asset_copies'][aid]
        if transfers:
            current.save()
        if problems:
            raise Error('WORKSPACE_INCOMPLETE', 'Some registered files need attention before sharing.', problems[:10])
        receipt = current.synchronize(tid, compact=True)
        return dict(identity, thread=tid, snapshot=receipt['accepted_snapshot'],
                    cursor=cursor(wb, current, tid, verified=True))


def arm_workspace(wb, ticket):
    tid = ticket['thread']
    with wb.base.locked():
        path = policy(wb, tid)
        previous = read_json(path, {})
        if previous and any(previous[k] != ticket[k] for k in ('hub', 'actor')):
            raise Error('STATE_CONFLICT', 'This work is already bound to a different Hub or owner.')
        # A delayed replay may have an earlier cursor; retain the live checkpoint.
        if previous.get('enabled'):
            save_json(path, dict(previous, activation=uuid4().hex))
        else:
            save_json(path, dict(ticket, enabled=True, activation=uuid4().hex, attempts=0, retry_at=0))
    schedule(wb)


def pause(wb, tid):
    path = policy(wb, tid)
    state = read_json(path)
    if state:
        save_json(path, dict(state, enabled=False, paused=True))


def after_personal(wb, personal, tid, verb):
    """Resume/discover existing grants for the same owner after a normal personal sync."""
    path = policy(wb, tid)
    state = read_json(path)
    if state and state.get('paused'):
        if verb == 'personal':
            save_json(path, dict(state, enabled=True, paused=False, retry_at=0))
        return
    if state and state.get('enabled'):
        return
    if getattr(personal, 'server_info', {}).get('workspace_protocol') != 'workspace.v1':
        return
    online = Online(wb)
    identity = check_identity(online, state)
    status = online.call('delivery_workspace_status', work=tid, to=[])
    if status.get('active_count', 0) > 0:
        # First pass validates current records and available files before advancing.
        save_json(path, dict(identity, thread=tid, cursor=None, snapshot=None,
                             enabled=True, attempts=0, retry_at=0))


def inspect_state(wb, state, history):
    p = Personal(wb)
    observed = cursor(wb, p, state['thread'])
    pending = changed(wb, state['thread'], state['cursor'], observed, history)
    return observed, pending


def report(wb, offline=False, status=False, force=False):
    """Advance source checkpoints only after a verified publish acknowledgement."""
    paths = sorted(directory(wb).glob('W-*.json'))
    reports = []
    history = {}
    for path in paths:
        result = {'thread': path.stem}
        state = None
        try:
            with wb.base.locked():
                state = read_json(path)
                if not state['enabled']:
                    reports.append(dict(result, enabled=False, pending=False))
                    continue
                wb.client = wb.read_client()
                observed, pending = inspect_state(wb, state, history)
                result.update(enabled=True, pending=pending, revision=state.get('revision'),
                              snapshot=state['snapshot'], retry_at=state.get('retry_at', 0))
                if state.get('error') and pending:
                    result['error'] = state['error']
                    if state.get('issues'):
                        result['issues'] = state['issues']
                if status or offline or not pending or (not force and time.time() < result['retry_at']):
                    if not status and not pending and state['cursor'] != observed:
                        save_json(path, dict(state, cursor=observed))
                    reports.append(result)
                    continue

            online = Online(wb)
            check_identity(online, state)
            grants = online.call('delivery_workspace_status', work=path.stem, to=[])
            if grants.get('thread') != path.stem or type(grants.get('active_count')) is not int:
                raise Error('SYNC_PROTOCOL', 'The Hub has not confirmed the current workspace grants.')
            if grants['active_count'] == 0:
                with wb.base.locked():
                    if read_json(path) == state:
                        save_json(path, dict(state, enabled=False, attempts=0, retry_at=0))
                        still_active = False
                    else:
                        still_active = bool(read_json(path)['enabled'])
                reports.append(dict(thread=path.stem, enabled=still_active, pending=still_active))
                continue
            ticket = prepare_workspace(wb, online, path.stem)
            with wb.base.locked():
                latest = read_json(path)
                if not latest['enabled'] or state != latest:
                    reports.append(dict(result, pending=latest['enabled'], enabled=latest['enabled']))
                    continue
                reply = online.call('delivery_workspace_publish', work=path.stem, snapshot=ticket['snapshot'])
                count = reply.get('active_count')
                if (reply.get('accepted') is not True or reply.get('thread') != path.stem
                        or type(count) is not int or count < 0 or (count and not reply.get('revision'))):
                    raise Error('SYNC_PROTOCOL', 'The Hub has not confirmed this workspace update.')
                save_json(path, dict(ticket, revision=reply.get('revision'), enabled=count > 0,
                                     activation=state.get('activation'), attempts=0, retry_at=0))
                # Files can change outside the store lock. A later write must
                # remain observable even if the Hub accepted the frozen bytes.
                newer = inspect_state(wb, ticket, history)[1] if count else False
                reports.append(dict(thread=path.stem, enabled=count > 0, pending=newer,
                                    snapshot=ticket['snapshot'], revision=reply.get('revision')))
        except (Error, OSError) as exc:
            result.update(pending=True, error=getattr(exc, 'code', 'IO_ERROR'))
            if isinstance(getattr(exc, 'hint', None), list):
                result['issues'] = exc.hint
            if not offline and not status and state and result['error'] != 'STORE_BUSY':
                with wb.base.locked():
                    latest = read_json(path)
                    if latest and latest['enabled'] and latest == state:
                        attempts = latest.get('attempts', 0) + 1
                        retry_at = time.time() + min(300, 5 * 2 ** min(attempts - 1, 6))
                        save_json(path, dict(latest, attempts=attempts, retry_at=retry_at,
                                             error=result['error'], issues=result.get('issues')))
                        result['retry_at'] = retry_at
            reports.append(result)
    return reports


# Producers and the worker use the short gate only when updating wake/launch state.
# Network calls and the collection delay happen outside it. A crashed worker releases
# its OS lock; the next CLI action or an explicit collector pass recovers durable work.
def _busy(lock):
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    fcntl.flock(lock, fcntl.LOCK_UN)
    return False


def schedule(wb, offline=False, recovery_only=False):
    root = directory(wb)
    if not root.is_dir():
        return
    try:
        with (root / 'gate.lock').open('a+') as gate:
            fcntl.flock(gate, fcntl.LOCK_EX)
            wake = root / 'wake.json'
            if recovery_only:
                retry = read_json(root / 'retry.json', {})
                if not wake.exists() and not (retry and retry['at'] <= time.time()):
                    return
            save_json(wake, {'at': time.time_ns()})
            if offline:
                return
            with (root / 'worker.lock').open('a+') as lock:
                if _busy(lock) or read_json(root / 'launch.json', {}).get('until', 0) > time.time():
                    return
            save_json(root / 'launch.json', {'until': time.time() + 5})
            code = ('import sys;sys.path.insert(0,sys.argv[1]);'
                    'from cosmesh.workspace_progress import worker;worker(sys.argv[2])')
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
            child = subprocess.Popen([sys.executable, '-I', '-c', code, str(Path(__file__).resolve().parent.parent),
                                      str(wb.base.root)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, close_fds=True, **options)
            from threading import Thread
            Thread(target=child.wait, daemon=True).start()
    except (Error, OSError):
        # The durable cursor still records the last acknowledged revision.
        return


@networked
def worker(path):
    wb = Workbench(path)
    root = directory(wb)
    with (root / 'worker.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        (root / 'launch.json').unlink(missing_ok=True)
        while True:
            time.sleep(2)
            wake = read_json(root / 'wake.json', {})
            updates = report(wb)
            due = [r.get('retry_at') or time.time() + 5 for r in updates if r.get('pending')]
            with (root / 'gate.lock').open('a+') as gate:
                fcntl.flock(gate, fcntl.LOCK_EX)
                retry = root / 'retry.json'
                if due:
                    save_json(retry, {'at': min(due)})
                else:
                    retry.unlink(missing_ok=True)
                if wake == read_json(root / 'wake.json', {}):
                    (root / 'wake.json').unlink(missing_ok=True)
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    return


def after_command(args):
    from .threads import workspace_root
    writes = {'thread.note', 'thread.result', 'thread.summary', 'thread.correct', 'thread.link',
              'thread.state', 'thread.archive', 'thread.restore', 'thread.edit', 'thread.bind',
              'thread.bind-project', 'thread.associate', 'thread.update', 'thread.name', 'thread.split',
              'thread.merge', 'thread.delete', 'thread.fetch', 'thread.personal', 'thread.sync',
              'thread.task', 'collect.summary'}
    write = args.action in writes or (args.action == 'personal.asset' and getattr(args, 'asset_command', None) == 'add')
    try:
        root = workspace_root(getattr(args, 'root', None))
        if not (root / '.git/cosmesh-workspace-progress').is_dir():
            return
        schedule(Workbench(root), offline=getattr(args, 'offline', False) or getattr(args, 'local_only', False),
                 recovery_only=not write)
    except (OSError, Error):
        return
