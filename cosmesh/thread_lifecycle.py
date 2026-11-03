"""Small, source-record based Thread operations; one store/commit per change."""
import socket
from pathlib import Path
from .errors import Error
from .protocol import encode, public
from .thread_protocol import new_id, content_version, check_record, references, thread_state
from .threads import now

REMOVED = {'trash', 'withdrawn', 'merged'}


def state(wb, item):
    return thread_state([x['doc']['meta'] for x in wb.records(item['doc']['meta']['id'])])


def owner(wb, item):
    actor = wb.config['user'] if item['repo'] == 'local' else wb.user
    if item['doc']['meta']['author'] != actor:
        raise Error('AUTH_DENIED', 'Only the creator can reorganize or remove this Thread.')
    return actor


def entry(wb, parent, kind, body='', **fields):
    meta = {'schema_version': 2, 'kind': 'entry', 'id': new_id('entry'), 'author': owner(wb, parent),
            'created_at': now(), 'title': body.strip().splitlines()[0][:100] if body.strip() else kind,
            'thread': parent['doc']['meta']['id'], 'type': kind, **fields}
    for name in ('author_id',):
        if name in parent['doc']['meta']:meta[name] = parent['doc']['meta'][name]
    if getattr(wb, 'generation', None):meta['generation'] = wb.generation
    if kind == 'lifecycle':
        stream = {x['doc']['meta'].get('previous'): x['doc']['meta']['id']
                  for x in wb.records(meta['thread']) if x['doc']['meta']['type'] == 'lifecycle'}
        cursor = None
        while cursor in stream:cursor = stream[cursor]
        meta['previous'] = cursor
        state_id = state(wb, parent)['state_id']
        if state_id:meta['basis'] = wb.ref(wb.find(state_id))
    return {'meta': meta, 'body': body}


def checked(wb, docs, repo):
    overlay = {doc['meta']['id']: doc for doc in docs}
    def resolve(ref):
        doc = overlay.get(ref['id']) if ref['repo'] == repo else None
        return doc if doc and content_version(doc) == ref['version'] else wb.resolve(ref)
    for doc in docs:
        if doc['meta']['kind'] != 'entry':continue
        if repo != 'local' and any(r['repo'] == 'local' for r in references(doc['meta'])):
            raise Error('PRIVATE_REFERENCE', 'Share private sources explicitly before reorganizing public work.')
        parent = overlay.get(doc['meta']['thread']) or wb.find(doc['meta']['thread'], 'thread')['doc']
        check_record(doc, parent, resolve)
    return {'documents/' + doc['meta']['id'] + '.md': encode(doc) for doc in docs}


def placement(wb, value, action, body='', key=None, dry_run=False):
    item = wb.find(value, 'thread')
    def build():
        owner(wb, item)
        current = state(wb, item)
        if action == 'delete':
            target = 'trash' if item['repo'] == 'local' else 'withdrawn'
            if current['lifecycle'] in {'trash', 'withdrawn'}:
                return {}, {'id': item['doc']['meta']['id'], 'lifecycle': current['lifecycle'], 'unchanged': True}
        elif action == 'restore':
            target = current['restore_to'] if current['lifecycle'] in REMOVED else 'active'
        else:
            if current['lifecycle'] in REMOVED:
                raise Error('STATE_CONFLICT', 'Restore this Thread before archiving it.')
            target = 'archived'
        fields = {'refs': [current['restore_ref']]} if target == 'merged' else {}
        record = entry(wb, item, 'lifecycle', body, lifecycle=target, **fields)
        return checked(wb, [record], item['repo']), {'id': item['doc']['meta']['id'], 'record': public(record),
                                                   'lifecycle': target, 'shared': item['repo'] != 'local'}
    return wb._commit(item['store'], action, {'id': item['doc']['meta']['id'], 'body': body}, build, key, dry_run)


def split(wb, value, title, body='', refs=None, key=None, dry_run=False):
    source = wb.find(value, 'thread')
    payload = {'id': source['doc']['meta']['id'], 'title': title, 'body': body, 'refs': refs or []}
    def build():
        actor = owner(wb, source)
        if state(wb, source)['lifecycle'] in REMOVED:
            raise Error('STATE_CONFLICT', 'Restore the source before splitting it.')
        child = {'meta': {'schema_version': 2, 'kind': 'thread', 'id': new_id('thread'),
                         'author': actor, 'created_at': now(), 'title': title}, 'body': body}
        if 'author_id' in source['doc']['meta']:child['meta']['author_id'] = source['doc']['meta']['author_id']
        if getattr(wb, 'generation', None):child['meta']['generation'] = wb.generation
        child_item = {**source, 'doc': child}
        derived = entry(wb, child_item, 'link', '分裂来源：' + source['doc']['meta']['title'], refs=[wb.ref(source), *(refs or [])])
        records = [child, derived]
        return checked(wb, records, source['repo']), {'thread': public(child), 'source': wb.ref(source),
                        'records': [public(derived)], 'shared': source['repo'] != 'local'}
    return wb._commit(source['store'], 'split', payload, build, key, dry_run)


def merge(wb, values, into, body='', key=None, dry_run=False):
    target = wb.find(into, 'thread')
    sources = [wb.find(value, 'thread') for value in values]
    ids = [s['doc']['meta']['id'] for s in sources]
    if len(set(ids)) != len(ids) or target['doc']['meta']['id'] in ids:
        raise Error('INPUT_INVALID', 'Choose distinct sources and a different continuing Thread.')
    def build():
        owner(wb, target)
        if state(wb, target)['lifecycle'] in REMOVED:
            raise Error('STATE_CONFLICT', 'Choose a continuing Thread that is not deleted, withdrawn or merged.')
        refs = [wb.ref(s) for s in sources]
        # Same-store predecessors point to the continuing Thread; its incoming index
        # supplies the reverse view. Keep private provenance acyclic for later sharing.
        retained_refs = [wb.ref(s) for s in sources if s['store'].root != target['store'].root]
        records = [entry(wb, target, 'link', body or '合并工作；原记录与作者沿来源回链保留。', refs=retained_refs)]
        moved, retained = [], []
        for source in sources:
            same_store = source['store'].root == target['store'].root
            if not same_store:
                retained.append({'source': wb.ref(source), 'reason': 'Original work remains in its own space/ownership.'})
                continue
            owner(wb, source)
            if state(wb, source)['lifecycle'] in REMOVED:
                raise Error('STATE_CONFLICT', 'Restore a removed source before merging it.')
            record = entry(wb, source, 'lifecycle', '并入工作：' + target['doc']['meta']['title'],
                           lifecycle='merged', refs=[wb.ref(target)])
            records.append(record);moved.append(source['doc']['meta']['id'])
        return checked(wb, records, target['repo']), {'id': target['doc']['meta']['id'], 'merged': moved,
                   'source_refs': refs, 'retained_sources': retained, 'records': [public(d) for d in records]}
    return wb._commit(target['store'], 'merge', {'sources': ids, 'into': target['doc']['meta']['id'], 'body': body}, build, key, dry_run)


def continuation(wb, value):
    focus = wb.find(value)
    first = focus if focus['doc']['meta']['kind'] == 'thread' else wb.find(focus['doc']['meta']['thread'], 'thread')
    item, trail, seen = first, [], set()
    while state(wb, item)['merged_into']:
        if item['doc']['meta']['id'] in seen:
            raise Error('REFERENCE_INVALID', 'Thread continuation contains a cycle.')
        seen.add(item['doc']['meta']['id']);trail.append(wb.ref(item))
        destination = state(wb, item)['merged_into']
        wb.resolve(destination)
        item = wb.find(destination['id'], 'thread')
    packet = wb.context(item['doc']['meta']['id'] if trail else value)
    packet['continued_from'] = trail
    for ref in trail:
        for binding in wb.client['bindings']:
            if binding['thread'] == ref['id'] and binding not in packet['local_execution']:
                packet['local_execution'].append(binding)
    return packet


def locate(wb, workdir=None, session_id=None, host=None, agent=None, project_id=None, project_name=None):
    """Suggest existing work from private mappings; the Agent decides semantic fit."""
    host = host or socket.gethostname()
    path = str(Path(workdir).expanduser().absolute()) if workdir else None
    found = {}
    from .agent_projects import active,agent_name
    projects=[p for item in wb.catalog.values() if item['doc']['meta']['kind']=='thread' for p in active(wb,item['doc']['meta']['id'])]
    for binding in [*projects,*wb.client['bindings']]:
        if binding['host'] != host:continue
        if agent and agent_name(binding['agent'])!=agent_name(agent):continue
        by_project=bool('name' in binding and ((project_id and binding.get('project_id')==project_id) or
                        (not project_id and project_name and binding['name']==project_name)))
        by_session = bool(session_id and binding.get('session_id') == session_id)
        by_directory = bool(path and binding.get('workdir') == path)
        if not (by_project or by_session or by_directory):continue
        item = wb.find(binding['thread'], 'thread')
        trail, seen = [], set()
        while state(wb, item)['merged_into']:
            if item['doc']['meta']['id'] in seen:raise Error('REFERENCE_INVALID', 'Merge destination cycle.')
            seen.add(item['doc']['meta']['id']);trail.append(wb.ref(item))
            item = wb.find(state(wb, item)['merged_into']['id'], 'thread')
        shown = wb.present(item)
        if shown['lifecycle'] in {'trash', 'withdrawn'}:continue
        result = found.setdefault(shown['id'], {'thread': shown, 'bindings': [], 'continued_from': [], 'session_match': False,'project_match':False})
        result['project_match'] |= by_project
        result['session_match'] |= by_session
        result['bindings'].append(binding)
        for ref in trail:
            if ref not in result['continued_from']:result['continued_from'].append(ref)
    candidates = sorted(found.values(), key=lambda x:(not x['project_match'],not x['session_match'], x['thread']['lifecycle']!='active', x['thread']['id']))
    return {'private': True, 'candidates': candidates, 'basis': 'local_project_session_and_directory_bindings'}
