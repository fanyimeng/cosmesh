"""Thread schema 2: one work object, immutable authored records and pinned references."""
import hashlib
import json
import re
from datetime import datetime
from uuid import UUID, uuid4

from .errors import Error
from .protocol import USER, encode, version

REPO = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
TYPES = {'note', 'result', 'request', 'reply', 'assign', 'decline', 'delivery',
         'adopt', 'cancel', 'link', 'correction', 'state', 'lifecycle', 'summary'}
CONTROL = {'assign', 'decline', 'delivery', 'adopt', 'cancel'}
PLACEMENTS = {'active', 'archived', 'trash', 'withdrawn', 'merged'}


def thread_state(records):
    """Replay independent work-result and placement streams from source records."""
    def ordered(kind):
        stream = {m.get('previous'): m for m in records if m['type'] == kind}
        cursor = None
        while cursor in stream:
            entry = stream[cursor]
            yield entry
            cursor = entry['id']
    state, state_id = 'active', None
    for entry in ordered('state'):
        state, state_id = entry['state'], entry['id']
    placement = 'active' if state == 'active' else 'archived'
    destination, previous_placements = None, []
    for entry in ordered('lifecycle'):
        value = entry['lifecycle']
        if value in {'trash', 'withdrawn', 'merged'}:
            target = entry['refs'][0] if value == 'merged' else None
            if previous_placements and previous_placements[-1] == (value, target):
                previous_placements.pop()
            else:
                previous_placements.append((placement, destination))
            placement = value
            destination = target
        elif entry.get('basis', {}).get('id') == state_id or placement in {'trash', 'withdrawn', 'merged'}:
            placement = value if entry.get('basis', {}).get('id') == state_id else ('active' if state == 'active' else 'archived')
            destination = None
            if previous_placements and previous_placements[-1][0] == value:
                previous_placements.pop()
            else:
                previous_placements.clear()
    prior = previous_placements[-1] if previous_placements else ('active', None)
    return {'work_state': 'done' if state == 'archived' else state, 'lifecycle': placement,
            'merged_into': destination, 'restore_to': prior[0], 'restore_ref': prior[1], 'state_id': state_id}


def identifier(value, prefix=None):
    try:
        head, raw = value.split('-', 1)
        parsed = UUID(raw)
        if head not in ({prefix} if prefix else {'W', 'E'}) or parsed.version != 4 or str(parsed) != raw:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise Error('INPUT_INVALID', 'Expected a complete W- or E- UUIDv4 identifier.')
    return value


def new_id(kind):
    return ('W-' if kind == 'thread' else 'E-') + str(uuid4())


def reference(ref):
    if not isinstance(ref, dict) or set(ref) != {'repo', 'id', 'version'}:
        raise Error('REFERENCE_INVALID', 'References require repo, id and content version.')
    if not isinstance(ref['repo'], str) or not REPO.fullmatch(ref['repo']):
        raise Error('REFERENCE_INVALID', 'Invalid reference repository.')
    if ref['repo'] == 'library':
        from .knowledge_protocol import knowledge_id
        knowledge_id(ref['id'])
    else:
        identifier(ref['id'])
    if not isinstance(ref['version'], str) or not re.fullmatch('[0-9a-f]{64}', ref['version']):
        raise Error('REFERENCE_INVALID', 'References pin a SHA-256 content version.')
    return ref


def reference_edges(meta):
    """Name each forward edge so reverse views preserve its actual meaning."""
    edges = [(field, ref) for field in ('refs', 'work') for ref in meta.get(field, [])]
    edges += [(field, meta[field]) for field in ('request', 'supersedes', 'basis') if meta.get(field)]
    return edges


def references(meta):
    return [ref for _, ref in reference_edges(meta)]


def validate(doc):
    meta = doc['meta']
    common = {'schema_version', 'kind', 'id', 'author', 'created_at', 'title'}
    optional = {'author_id', 'generation'}
    extra = {'thread', 'type', 'refs', 'request', 'to', 'work', 'supersedes', 'previous', 'complete', 'state', 'lifecycle', 'basis', 'agent'}
    if meta.get('schema_version') != 2 or type(meta.get('schema_version')) is not int:
        raise Error('SCHEMA_UNSUPPORTED', 'Thread documents use schema 2.')
    kind = meta.get('kind')
    if kind not in {'thread', 'entry'} or not common <= set(meta) or set(meta) - (common | optional | (extra if kind == 'entry' else set())):
        raise Error('SCHEMA_INVALID', 'Invalid Thread document fields.')
    identifier(meta['id'], 'W' if kind == 'thread' else 'E')
    if not isinstance(meta['author'], str) or not USER.fullmatch(meta['author']):
        raise Error('SCHEMA_INVALID', 'Invalid author.')
    if 'author_id' in meta and (not isinstance(meta['author_id'],str) or not re.fullmatch(r'person-[0-9a-f-]{36}',meta['author_id'])):
        raise Error('SCHEMA_INVALID','Invalid stable person ID.')
    if 'generation' in meta and (not isinstance(meta['generation'],dict) or set(meta['generation'])-{'tool','model','instruction','verification'} or any(not isinstance(x,str) or len(x)>8000 for x in meta['generation'].values())):
        raise Error('SCHEMA_INVALID','Generation details must contain bounded tool, model and instruction text.')
    if not isinstance(meta['title'], str) or not meta['title'].strip():
        raise Error('SCHEMA_INVALID', 'A work title is required.')
    try:
        if datetime.fromisoformat(meta['created_at']).utcoffset() is None:
            raise ValueError()
        json.dumps(meta, allow_nan=False)
    except (TypeError, ValueError):
        raise Error('SCHEMA_INVALID', 'Metadata and timestamp must be valid.')
    if not isinstance(doc.get('body'), str):
        raise Error('SCHEMA_INVALID', 'The body must be text.')
    if kind == 'entry':
        identifier(meta.get('thread'), 'W')
        if meta.get('agent') is not None and meta['agent'] != 'mesh-agent':
            raise Error('SCHEMA_INVALID', 'Unknown generated-record agent label.')
        if meta.get('type') not in TYPES:
            raise Error('SCHEMA_INVALID', 'Unknown work record type.')
        for field in ('refs', 'work'):
            if not isinstance(meta.get(field, []), list):
                raise Error('SCHEMA_INVALID', field + ' must be a list.')
        for ref in references(meta):
            reference(ref)
        if meta['type'] == 'request' and (not isinstance(meta.get('to'), str) or not USER.fullmatch(meta['to'])):
            raise Error('SCHEMA_INVALID', 'A request needs a recipient account.')
        if meta['type'] in CONTROL and not meta.get('request'):
            raise Error('SCHEMA_INVALID', 'This action must refer to a request.')
        if meta.get('previous') is not None:
            identifier(meta['previous'], 'E')
        if meta['type'] == 'delivery' and (not meta.get('refs') or type(meta.get('complete')) is not bool):
            raise Error('SCHEMA_INVALID', 'Delivery needs result references and a completion flag.')
        if meta['type'] == 'assign' and not meta.get('work'):
            raise Error('SCHEMA_INVALID', 'Assignment needs at least one work thread.')
        if meta['type'] == 'correction' and not meta.get('supersedes'):
            raise Error('SCHEMA_INVALID', 'A correction needs its original record.')
        if meta['type'] == 'state' and meta.get('state') not in {'active', 'done', 'cancelled', 'archived'}:
            raise Error('SCHEMA_INVALID', 'Work state must be active, done or archived.')
        if meta['type']=='lifecycle' and meta.get('lifecycle') not in PLACEMENTS:
            raise Error('SCHEMA_UNSUPPORTED','Unsupported Thread lifecycle; use a compatible client.')
        if meta['type']=='lifecycle' and meta.get('lifecycle')=='merged' and len(meta.get('refs', [])) != 1:
            raise Error('SCHEMA_INVALID', 'A merged Thread has one continuing Thread reference.')


class Snapshot:
    def __init__(self, store, files):
        self.config = json.loads(files['.cosmesh/config.json'])
        if set(self.config) != {'schema_version', 'product', 'user', 'timezone', 'mode', 'scope'} or (
                self.config['schema_version'] != 2 or self.config['product'] != 'cosmesh' or
                self.config['mode'] != 'local_unverified' or self.config['scope'] not in {'base', 'mesh'}):
            raise Error('SCHEMA_UNSUPPORTED', 'Unsupported Thread store configuration.')
        if not USER.fullmatch(self.config['user']):
            raise Error('SCHEMA_INVALID', 'Invalid workspace identity.')
        self.docs = store.documents(files)
        groups = {}
        for doc in self.docs.values():
            validate(doc)
            meta = doc['meta']
            if meta['kind'] == 'entry':
                parent = self.docs.get(meta['thread'])
                if not parent or parent['meta']['kind'] != 'thread':
                    raise Error('REFERENCE_INVALID', 'A record must belong to an existing Thread.')
                if meta['type'] in CONTROL or meta['type'] in {'state','lifecycle'}:
                    group = (meta['request']['repo'], meta['request']['id'], meta['author']) if meta['type'] in CONTROL else (meta['type'], meta['thread'], meta['author'])
                    stream = groups.setdefault(group, {})
                    previous = meta.get('previous')
                    if previous in stream:
                        raise Error('STATE_CONFLICT', 'Concurrent request actions have the same predecessor.')
                    stream[previous] = meta['id']
        for stream in groups.values():
            remaining, current = set(stream.values()), None
            while current in stream:
                current = stream[current]
                if current not in remaining:
                    raise Error('STATE_CONFLICT', 'Cyclic request action stream.')
                remaining.remove(current)
            if remaining:
                raise Error('STATE_CONFLICT', 'A request action predecessor is missing.')
        entries = {}
        for doc in self.docs.values():
            if doc['meta']['kind'] == 'entry':
                meta = doc['meta']
                entries.setdefault(meta['thread'], []).append(meta)
                if meta.get('lifecycle') == 'trash' and self.config['scope'] != 'base':
                    raise Error('POLICY_DENIED', 'Shared Threads are withdrawn; trash belongs to private Base.')
                if meta.get('lifecycle') == 'withdrawn' and self.config['scope'] != 'mesh':
                    raise Error('POLICY_DENIED', 'Private Threads use the Base recycle bin.')
        redirects = {tid: state['merged_into']['id'] for tid, records in entries.items()
                     if (state := thread_state(records))['merged_into']}
        for tid in redirects:
            seen, cursor = set(), tid
            while cursor in redirects:
                if cursor in seen:
                    raise Error('REFERENCE_INVALID', 'Thread merge destinations cannot form a cycle.')
                seen.add(cursor); cursor = redirects[cursor]


def validate_change(store, before, after, actor):
    model = Snapshot(store, after)
    if model.config['scope'] != 'mesh':
        raise Error('POLICY_DENIED', 'Only explicitly shared Thread stores can be published.')
    if before and before.get('.cosmesh/config.json') != after.get('.cosmesh/config.json'):
        raise Error('HISTORY_PROTECTED', 'Accepted store configuration is immutable.')
    old_docs = store.documents(before) if before else {}
    previous_states = {}
    for doc in old_docs.values():
        if doc['meta']['kind'] == 'entry':
            previous_states.setdefault(doc['meta']['thread'], []).append(doc['meta'])
    total = 0
    for path, text in after.items():
        if text != before.get(path):
            total += len(text.encode())
        if len(text.encode()) > (4 * 1024 * 1024 if path.startswith('.cosmesh/operations/') else 1024 * 1024):
            raise Error('INPUT_TOO_LARGE', 'Source or receipt exceeds its size limit.')
        if path.startswith('.cosmesh/operations/') and path not in before:
            receipt = json.loads(text)
            expected = hashlib.sha256((actor + ':' + receipt['key']).encode()).hexdigest()
            if receipt.get('actor') != actor or path != '.cosmesh/operations/' + expected + '.json':
                raise Error('AUTH_DENIED', 'Operation receipt does not belong to this actor.')
    if total > 100 * 1024 * 1024:
        raise Error('INPUT_TOO_LARGE', 'Commit exceeds 100 MiB.')
    for path, text in before.items():
        if path not in after:
            raise Error('HISTORY_PROTECTED', 'Published files cannot be deleted.')
        if path.startswith('.cosmesh/operations/') and after[path] != text:
            raise Error('HISTORY_PROTECTED', 'Operation receipts are immutable.')
    for doc_id, doc in model.docs.items():
        old = old_docs.get(doc_id)
        if old == doc:
            continue
        meta = doc['meta']
        if old:
            if meta['kind'] == 'entry':
                raise Error('HISTORY_PROTECTED', 'Published records are immutable; append a correction.')
            if old['meta']['author'] != actor or {k:v for k,v in meta.items() if k != 'title'} != {k:v for k,v in old['meta'].items() if k != 'title'}:
                raise Error('AUTH_DENIED', 'Only the creator can revise a Thread title and overview.')
        elif meta['author'] != actor:
            raise Error('AUTH_DENIED', 'Record author must match the authenticated account.')
        elif meta['kind'] == 'thread' and actor != model.config['user']:
            raise Error('AUTH_DENIED', 'New Threads belong in their creator\'s own Mesh space.')
        if old is None and meta['kind'] == 'entry':
            prior = thread_state(previous_states.get(meta['thread'], []))
            restoring = meta['type'] == 'lifecycle' and (meta['lifecycle'] in {'active', 'archived'} or (
                meta['lifecycle'] == prior['restore_to'] == 'merged' and meta.get('refs') == [prior['restore_ref']]))
            responding = prior['lifecycle'] in {'withdrawn', 'merged'} and meta['type'] in CONTROL | {'reply'} and meta.get('request')
            withdrawing = meta['type'] == 'lifecycle' and meta['lifecycle'] == 'withdrawn'
            if prior['lifecycle'] in {'trash', 'withdrawn', 'merged'} and not (restoring or responding or withdrawing):
                raise Error('STATE_CONFLICT', 'Restore the withdrawn or merged Thread before adding records.')
    return model


def check_record(doc, thread, resolve):
    """Shared semantic validation, with references resolved from trusted source snapshots."""
    meta = doc['meta']
    for ref in references(meta):
        resolve(ref)
    if meta.get('request'):
        request = resolve(meta['request'])
        if request['meta'].get('type') != 'request':
            raise Error('REFERENCE_INVALID', 'Response target must be a request.')
        if meta['thread'] != request['meta']['thread']:
            raise Error('REFERENCE_INVALID', 'Request responses are stored in its original Thread.')
        if meta['type'] in {'assign', 'decline', 'delivery'} and meta['author'] != request['meta']['to']:
            raise Error('AUTH_DENIED', 'Only the recipient can accept, decline or deliver a request.')
        if meta['type'] in {'adopt', 'cancel'} and meta['author'] != request['meta']['author']:
            raise Error('AUTH_DENIED', 'Only the requester can adopt or cancel this request.')
    if meta['type'] == 'assign' and any(resolve(ref)['meta']['kind'] != 'thread' for ref in meta['work']):
        raise Error('REFERENCE_INVALID', 'Assignment targets must be Threads.')
    if meta.get('basis'):
        basis=resolve(meta['basis'])
        if meta['type']!='lifecycle' or basis['meta'].get('type')!='state' or basis['meta']['thread']!=meta['thread']:
            raise Error('REFERENCE_INVALID','Lifecycle basis must identify a state of this Thread.')
    if meta.get('lifecycle') == 'merged':
        target = resolve(meta['refs'][0])
        if target['meta']['kind'] != 'thread' or target['meta']['id'] == thread['meta']['id'] or target['meta']['author'] != thread['meta']['author']:
            raise Error('AUTH_DENIED', 'Merge destinations must be a different Thread by the same creator.')
    if meta['type'] == 'delivery' and any(resolve(ref)['meta'].get('type') not in {'result', 'correction'} for ref in meta['refs']):
        raise Error('REFERENCE_INVALID', 'Delivery must point to result records or corrections.')
    if meta.get('supersedes'):
        old = resolve(meta['supersedes'])
        if old['meta']['kind'] != 'entry' or old['meta']['author'] != meta['author'] or old['meta']['thread'] != meta['thread']:
            raise Error('AUTH_DENIED', 'Authors correct their own records within the same work thread.')
    if meta['type'] in {'state', 'lifecycle', 'summary'} and meta['author'] != thread['meta']['author']:
        raise Error('AUTH_DENIED', 'The creator maintains the work state and overview.')


def content_version(doc):
    return version(encode(doc))
