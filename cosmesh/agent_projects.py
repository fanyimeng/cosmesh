"""Private Thread-to-agent-project/workspace links, independent of sessions."""
from collections import Counter
import os
import socket
from pathlib import Path

from .errors import Error
from .threads import now


def agent_name(value):
    value = value.strip().casefold()
    return {'deepseek harness':'dsh','deepseek-harness':'dsh','open code':'opencode'}.get(value, value)


def local_path(value):
    path = Path(value)
    if not path.is_absolute():
        return None
    normalized = Path(os.path.normpath(path))
    if '..' in path.parts:
        # A link followed by '..' can reach a different directory from the
        # lexical path. Never index one directory and modify the other.
        try:
            if normalized != path.resolve():
                return None
        except (OSError, RuntimeError):
            return None
    return normalized


def identity(row):
    if row.get('project_id'):
        key = ('native', row['project_id'])
    elif row['kind'] == 'directory' and row.get('workdir'):
        key = ('directory', row['workdir'])
    else:
        key = ('named', row['name'], row.get('workdir'))
    return (row['thread'], row['agent'], row['host'].casefold(), row['kind'], key)


def active(wb, thread=None):
    rows = wb.client.get('project_bindings', [])
    if not isinstance(rows, list): raise Error('SCHEMA_INVALID', 'Invalid private project bindings.')
    seen, result = set(), []
    for row in reversed(rows):
        if not isinstance(row, dict) or not {'thread','agent','host','kind','name'} <= set(row):
            raise Error('SCHEMA_INVALID', 'Invalid private project binding.')
        if thread is not None and row['thread'] != thread: continue
        key = identity(row)
        if key in seen: continue
        seen.add(key)
        if not row.get('removed'): result.append(dict(row))
    return result


def bind(wb, thread, agent, name=None, project_id=None, kind='project', host=None,
         workdir=None, observation=None, remove=False, dry_run=False, guidance=True):
    if kind not in {'project','workspace','directory'}:raise Error('INPUT_INVALID','Choose project, workspace or directory.')
    thread = wb.find(thread, 'thread')['doc']['meta']['id']
    agent = agent_name(agent);host = host or socket.gethostname()
    for value, limit in ((agent,80),(host,500),(name,500),(project_id,1000),(workdir,4096)):
        if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value or '\n' in value):
            raise Error('INPUT_INVALID', 'Use bounded project/workspace metadata without control characters.')
    if kind == 'directory' and workdir and not name:
        name = Path(workdir).expanduser().absolute().name or '/'
    if not name and not (remove and project_id):
        raise Error('INPUT_INVALID', 'Provide the actual project/workspace name; use a native ID when available.')
    if kind == 'directory' and not workdir and not remove:
        raise Error('INPUT_INVALID', 'A directory workspace needs its explicit working directory.')
    prior = active(wb, thread)
    if remove and not name:
        match = [r for r in prior if r['agent']==agent and r['host']==host and r['kind']==kind and r.get('project_id')==project_id]
        name = match[0]['name'] if match else project_id
    row = {'thread':thread,'agent':agent,'host':host,'kind':kind,'name':name.strip(),
           'project_id':project_id,'workdir':str(Path(workdir).expanduser().absolute()) if workdir else None,
           'status':'registered','removed':remove}
    if observation is not None:
        from .thread_overview import host_snapshot
        snapshot = host_snapshot(observation)
        matches = [r for r in snapshot.get('projects',[]) if agent_name(r['agent'])==agent and r['host']==host and
                   (r['id']==project_id if project_id else r['name']==name)]
        if len(matches)!=1:
            raise Error('AMBIGUOUS_ID' if matches else 'NOT_FOUND','Choose one actual agent project/workspace from the host metadata.')
        project = matches[0]
        if project['name'] != name: raise Error('STATE_CONFLICT','Project name differs from the observed native ID; use the current name.')
        if workdir and project.get('path') and row['host'].casefold() == socket.gethostname().casefold():
            observed = Path(project['path'])
            if local_path(observed) is None or local_path(row['workdir']) != local_path(observed):
                raise Error('STATE_CONFLICT', 'The selected directory differs from the observed local project root.')
        row.update(project_id=project['id'], status='verified', observed_at=snapshot['observed_at'],
                   source=snapshot['source'], verification='personal_agent_reported')
        if project.get('path'): row['workdir']=project['path']
    latest = next((r for r in reversed(wb.client.get('project_bindings',[])) if identity(r)==identity(row)),None)
    unchanged = latest is not None and {k:v for k,v in latest.items() if k!='at'} == row
    if not dry_run and not unchanged:
        wb.client.setdefault('project_bindings',[]).append({**row,'at':now()})
        wb.save_client()
    result = {'project':row,'private':True,'saved_locally':not dry_run,'unchanged':unchanged}
    if guidance and not remove:
        result['project_guidance'] = for_registration(wb, row, dry_run=dry_run)
    return result


def local_directories(wb):
    """Use this workbench's live project registrations, never peer copies or Session working directories."""
    from .thread_protocol import thread_state
    host = socket.gethostname().casefold()
    paths = {}
    for row in active(wb):
        if not row.get('workdir') or row['host'].casefold() != host:
            continue
        tid = row['thread']
        item = wb.catalog.get(tid)
        if not item or item['repo'] != 'local' or item['doc']['meta']['author'] != wb.author_for(item):
            continue
        lifecycle = thread_state([r['doc']['meta'] for r in wb.records(tid)])['lifecycle']
        if lifecycle != 'active':
            continue
        path = local_path(row['workdir'])
        if path is not None:
            paths.setdefault(path, set()).add(tid)
    return paths


def directory_guidance(wb, directory, roots, dry_run):
    from .project_guidance import ensure
    original = directory
    directory = local_path(directory)
    if directory is None:
        return dict(directory=str(original), status='skipped', dry_run=dry_run,
                    missing=[], reason='ambiguous_local_path')
    installation = os.environ.get('COSMESH_INSTALL_PREFIX')
    internal = directory.is_relative_to(wb.base.root) or (
        installation and directory.is_relative_to(Path(installation)))
    # A bound build fixture or packaged source inside another project is an
    # acceptance artifact, even when it contains its own Git checkout.
    generated = any(p in roots and directory.relative_to(p).parts[0] in {'build', 'dist'}
                    for p in directory.parents)
    if internal or generated:
        return dict(directory=str(directory), status='skipped', dry_run=dry_run, missing=[],
                    reason='managed_store' if internal else 'project_build_artifact')
    return ensure(directory, dry_run=dry_run)


def for_registration(wb, row, dry_run=False):
    host_matches = row['host'].casefold() == socket.gethostname().casefold()
    if not host_matches or not row.get('workdir'):
        return dict(directory=row.get('workdir'), status='skipped', dry_run=dry_run, missing=[],
                    reason='remote_host' if not host_matches else 'no_workdir')
    item = wb.catalog[row['thread']]
    if item['repo'] != 'local' or item['doc']['meta']['author'] != wb.author_for(item):
        return dict(directory=row['workdir'], status='skipped', dry_run=dry_run, missing=[],
                    reason='not_owner')
    from .thread_protocol import thread_state
    if thread_state([r['doc']['meta'] for r in wb.records(row['thread'])])['lifecycle'] != 'active':
        return dict(directory=row['workdir'], status='skipped', dry_run=dry_run, missing=[],
                    reason='inactive_thread')
    return directory_guidance(wb, Path(row['workdir']), local_directories(wb), dry_run)


def locate_bound(wb, directory):
    path = local_path(Path(directory).expanduser().absolute())
    tids = local_directories(wb).get(path, ())
    return {'candidates': [{'thread': tid, 'title': wb.catalog[tid]['doc']['meta']['title']}
                           for tid in sorted(tids)]}


def backfill(wb, dry_run=False):
    paths = local_directories(wb)
    rows = [dict(directory_guidance(wb, path, paths, dry_run), threads=sorted(paths[path]))
            for path in sorted(paths)]
    counts = dict(Counter(row['status'] for row in rows))
    return dict(complete=not counts.get('pending'), dry_run=dry_run, directories=rows, counts=counts)
