"""Read raw execution events from the current workbench and accepted owner sidecars.

This reader is deliberately offline and side-effect free: it never initializes
personal sync, contacts a Hub, probes project directories or rewrites bindings.
"""
import socket

from .personal_protocol import digest, packed, read_json
from .source_paths import manifest_path
from .thread_protocol import identifier


def personal_state(wb):
    """Return the existing personal state, or an empty read-only fallback."""
    return read_json(wb.base.path('.cosmesh/personal-v1/state.json'), {}) or {}


def raw_locations(wb, tid):
    """Collect the current local rows and retained remote rows exactly once."""
    tid = identifier(tid, 'W')
    state = personal_state(wb)
    root = wb.base.path('.cosmesh/personal-v1')
    rows = {}
    for directory in ('threads', 'executions'):
        value = read_json(root / directory / (tid + '.json'), {}) or {}
        rows.update(value.get('bindings', {}))
    return binding_rows(wb,tid,state,rows)


def binding_rows(wb,tid,state,known):
    rows=dict(known)
    groups = dict(state.get('bindings', {}))
    replica = state.get('replica')
    groups[replica] = {
        'device': state.get('device'),
        'bindings': wb.client.get('bindings', []),
        'project_bindings': wb.client.get('project_bindings', []),
    }
    for replica, group in groups.items():
        for kind in ('bindings', 'project_bindings'):
            for binding in group.get(kind, []):
                if binding.get('thread') != tid:
                    continue
                row = {'device': group.get('device'), 'replica': replica,
                       'kind': kind, 'binding': binding}
                workdir = binding.get('workdir')
                wire = manifest_path(workdir)
                if wire != workdir:
                    # Never replace an accepted event or change its content-derived ID.
                    if digest(packed(row).encode()) in known:
                        continue
                    row['binding'] = dict(binding, workdir=wire)
                rows[digest(packed(row).encode())] = row
    return rows


def owner_execution(wb, tid):
    """Build the canonical owner view from the same rows used for recovery."""
    from .thread_execution_projection import owner_projection
    return owner_projection(raw_locations(wb, tid), current_device=personal_state(wb).get('device'), current_host=socket.gethostname())


def execution_stamp(wb):
    """Cheap sidecar versions for derived-cache invalidation, without content reads."""
    root = wb.base.path('.cosmesh/personal-v1')
    result = {}
    paths = [root/'state.json']
    for directory in ('threads', 'executions'):
        parent = root/directory
        if parent.is_dir():
            paths.extend(sorted(parent.glob('W-*.json')))
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        info = path.stat()
        result[str(path.relative_to(root))] = [info.st_mtime_ns, info.st_size]
    return result


def cache_shared(wb, hub_id, rows):
    """Cache only server-supplied shared projections from one authenticated Hub."""
    updated = False
    for row in rows:
        metadata = row.get('execution_metadata')
        if not metadata:
            if wb.client.get('shared_execution', {}).pop(row['thread'], None) is not None:
                updated = True
            continue
        from .errors import Error
        from .thread_execution_projection import validate_shared_projection
        try:
            validate_shared_projection(metadata)
        except ValueError:
            raise Error('SYNC_PROTOCOL', 'A shared Thread response contains invalid execution metadata.')
        entry = {'hub_id': hub_id, 'content_revision': row['content_revision'],
                 'execution_metadata': metadata}
        target = wb.client.setdefault('shared_execution', {})
        if target.get(row['thread']) != entry:
            target[row['thread']] = entry
            updated = True
    if updated:
        wb.save_client()
    return updated


def shared_execution(wb, tid):
    """Return a cached shared projection and its explicit cache provenance."""
    entry = wb.client.get('shared_execution', {}).get(tid)
    if not entry:
        return None
    from .thread_execution_projection import validate_shared_projection
    try:
        return validate_shared_projection(entry['execution_metadata'])
    except (ValueError, KeyError):
        return None


def shared_execution_cache(wb, tid):
    entry = wb.client.get('shared_execution', {}).get(tid)
    if not entry:
        return None
    return {'hub_id': entry['hub_id'], 'content_revision': entry['content_revision'],
            'state': 'cached', 'server_current': False}
