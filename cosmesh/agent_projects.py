"""Private Thread-to-agent-project/workspace links, independent of sessions."""
import socket
from pathlib import Path

from .errors import Error
from .threads import now


def agent_name(value):
    value = value.strip().casefold()
    return {'deepseek harness':'dsh','deepseek-harness':'dsh','open code':'opencode'}.get(value, value)


def identity(row):
    return (row['thread'], row['agent'], row['host'], row['kind'], row.get('project_id') or row['name'])


def active(wb, thread):
    rows = wb.client.get('project_bindings', [])
    if not isinstance(rows, list): raise Error('SCHEMA_INVALID', 'Invalid private project bindings.')
    seen, result = set(), []
    for row in reversed(rows):
        if not isinstance(row, dict) or not {'thread','agent','host','kind','name'} <= set(row):
            raise Error('SCHEMA_INVALID', 'Invalid private project binding.')
        if row['thread'] != thread: continue
        key = identity(row)
        if key in seen: continue
        seen.add(key)
        if not row.get('removed'): result.append(dict(row))
    return result


def bind(wb, thread, agent, name=None, project_id=None, kind='project', host=None,
         workdir=None, observation=None, remove=False, dry_run=False):
    if kind not in {'project','workspace','directory'}:raise Error('INPUT_INVALID','Choose project, workspace or directory.')
    thread = wb.find(thread, 'thread')['doc']['meta']['id']
    agent = agent_name(agent);host = host or socket.gethostname()
    for value, limit in ((agent,80),(host,500),(name,500),(project_id,1000),(workdir,4096)):
        if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value or '\n' in value):
            raise Error('INPUT_INVALID', 'Use bounded project/workspace metadata without control characters.')
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
        row.update(project_id=project['id'], status='verified', observed_at=snapshot['observed_at'],
                   source=snapshot['source'], verification='personal_agent_reported')
        if project.get('path'): row['workdir']=project['path']
    latest = next((r for r in reversed(wb.client.get('project_bindings',[])) if identity(r)==identity(row)),None)
    unchanged = latest is not None and {k:v for k,v in latest.items() if k!='at'} == row
    if not dry_run and not unchanged:
        wb.client.setdefault('project_bindings',[]).append({**row,'at':now()})
        wb.save_client()
    return {'project':row,'private':True,'saved_locally':not dry_run,'unchanged':unchanged}
