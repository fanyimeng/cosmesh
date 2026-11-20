"""Single-owner Thread snapshots with authenticated, bounded online reading.

These snapshots stay outside public Git and the legacy global Library. Old
gateways cannot discover them. Existing public-Git IDs require a separate
migration before access can be narrowed; no false revocation is reported.
"""
import json
from contextlib import contextmanager
from uuid import uuid4

from .errors import Error
from .personal_protocol import HASH, MAX_METADATA, check_id, digest, integer, private_dir, read_json, save_json
from .personal_relay import Relay
from .protocol import decode
from .store import Store, safe_path
from .thread_personal_protocol import validate_manifest
from .thread_protocol import content_version, identifier

FORMAT = 'thread.access.v1'
MODES = ('private', 'restricted', 'public')
from .transfer_policy import PUBLIC as LIMITS


class Access:
    def __init__(self, hub, account):
        self.hub = hub
        self.community = hub.community()
        if not self.community:
            raise Error('FEATURE_UNAVAILABLE', 'Thread access requires the Hub stable user directory.')
        self.person = self.community.check(account)
        self.account = account
        self.root = private_dir(hub.root / 'thread-access-v1')

    @contextmanager
    def state(self):
        # One atomic catalog transaction covers ACL, content heads and accounting.
        with self.hub.locked('thread-access-v1'):
            path = self.root / 'catalog.json'
            data = read_json(path)
            if data is None:
                data = {'format': FORMAT, 'hub_id': 'hub-'+uuid4().hex, 'threads': {}}
                save_json(path, data)
            # Older Hubs replace Thread rows on publish, but preserve catalog extensions.
            self.project_approvals = data.setdefault('project_approvals', {})
            yield data

    def readable(self, row):
        return row['owner_id'] == self.person['id'] or row['visibility'] == 'public' or (
            row['visibility'] == 'restricted' and self.person['id'] in row['readers'])

    def lookup(self, data, tid):
        row = data['threads'].get(identifier(tid, 'W'))
        if not row or not self.readable(row):
            raise Error('NOT_FOUND', 'This Thread is unavailable to this account.')
        return row

    def people(self, values):
        if not isinstance(values, list) or len(values) > 100 or any(not isinstance(v, str) for v in values):
            raise Error('INPUT_INVALID', 'Select at most 100 existing users.')
        with self.community.db() as db:
            rows = [self.community.resolve_user(db, value) for value in values]
        if any(r['status'] != 'active' for r in rows):
            raise Error('USER_DISABLED', 'A selected account is disabled.')
        return sorted({r['id'] for r in rows if r['id'] != self.person['id']})

    def execution(self, row, revision=None):
        """A pinned snapshot inherits only approvals at or before its publication."""
        approvals = self.project_approvals
        if not approvals:
            return None
        history = row['history']
        end = history.index(revision) if revision else len(history) - 1
        return next((approvals[history[i]] for i in range(end, -1, -1) if history[i] in approvals), None)

    def brief(self, tid, row):
        # Labels are current; identity is the stable person ID, never the label.
        with self.community.db() as db:
            person = self.community.resolve_user(db, row['owner_id'])
        result = dict(thread=tid, owner_id=row['owner_id'], owner_account=row['owner_account'],
                      owner_name=person['name'] or person['account'], title=row['title'][:300], title_complete=len(row['title'])<=300,
                      visibility=row['visibility'], acl_revision=row['acl_revision'],
                      content_revision=row['sha'], writable=row['owner_id']==self.person['id'],
                      usage=row['usage'], migrated=bool(row.get('migrated')))
        metadata = self.execution(row)
        if metadata is not None:
            result['execution_metadata'] = metadata
        return result

    def manifest(self, row, sha=None):
        return read_manifest(self.hub,row,sha)

    def withdraw(self, data, request):
        if request['visibility']!='private' or request['readers'] or 'approve_projects' in request:
            raise Error('COMMAND_RETIRED','Use selected-content delivery for new shares.')
        tid=identifier(request['thread'],'W')
        row=self.lookup(data,tid)
        if row['owner_id']!=self.person['id']:
            raise Error('AUTH_DENIED','Only the owner may withdraw an archived share.')
        if row['visibility']=='private':
            return dict(self.brief(tid,row),accepted=True,replayed=True)
        if request['expect']!=row['acl_revision'] or request['sha']!=row['sha']:
            raise Error('STATE_CONFLICT','The archived share changed; read its current revision.')
        row.update(visibility='private',readers=[],acl_revision=row['acl_revision']+1)
        save_json(self.root/'catalog.json',data)
        return dict(self.brief(tid,row),accepted=True,replayed=False)

    def dispatch(self, p):
        method = p.get('method')
        allowed = {
            'thread_access_info': {'method'},
            'thread_access_search':{'method','query','thread','offset','limit'},
            'thread_access_changes':{'method','thread','since','revision','offset','limit'},
            'thread_access_set': {'method','thread','sha','visibility','readers','expect'},
            'thread_access_status': {'method','thread'},
            'thread_access_list': {'method','direct','owner','offset','limit'},
            'thread_access_read': {'method','thread','revision','record','version','offset','chars'},
            'thread_access_files': {'method','thread','revision','offset','limit'},
            'thread_access_records': {'method','thread','revision','record','offset','limit'},
            'thread_access_file': {'method','thread','revision','asset','offset'},
        }
        fields = set(p)
        if method == 'thread_access_set': fields.discard('approve_projects')
        if method not in allowed or fields != allowed[method]:
            raise Error('INPUT_INVALID', 'Invalid Thread access request; caller identity is server-derived.')
        with self.state() as data:
            if method == 'thread_access_info':
                return {'protocol': FORMAT, 'hub_id': data['hub_id'], 'person': {k:self.person[k] for k in ('id','account','name')}, 'limits': LIMITS, 'unified':self.hub.settings().get('format')==2, 'delivery_protocol':'delivery.v1','messages_protocol':'thread.messages.v1' if self.hub.settings().get('format')==2 else None, 'project_summary_protocol': 'thread.projects.v1'}
            if method == 'thread_access_set': return self.withdraw(data, p)
            if method == 'thread_access_list':
                if type(p['direct']) is not bool: raise Error('INPUT_INVALID', 'direct must be boolean.')
                offset=integer(p['offset'], 2**31-1); limit=integer(p['limit'], 100, 1)
                owner = None
                if p['owner']:
                    with self.community.db() as db: owner=self.community.resolve_user(db,p['owner'])['id']
                rows = [(tid,row) for tid,row in sorted(data['threads'].items()) if self.readable(row)
                        and (not owner or row['owner_id']==owner)
                        and (not p['direct'] or (row['owner_id']!=self.person['id'] and row['visibility']=='restricted' and self.person['id'] in row['readers']))]
                selected = rows[offset:offset+limit]
                return {'threads':[self.brief(tid,row) for tid,row in selected],
                        'next_offset':offset+limit if offset+limit<len(rows) else None, 'hub_id':data['hub_id'], 'server_current':True}
            if method=='thread_access_search':
                from .thread_search import Index
                if p['thread']:self.lookup(data,p['thread'])
                index=Index(self.hub)
                with index.open() as (db,fts):
                    allowed={};updated=0
                    for tid,row in data['threads'].items():
                        if not self.readable(row) or p['thread'] and tid!=p['thread']:continue
                        scope='thread:'+tid;allowed[scope]=row['sha']
                        def docs(row=row):return (decode(text) for text in self.manifest(row)[0]['documents'].values())
                        updated+=index.refresh(db,fts,scope,tid,row['sha'],docs)
                    result=index.search(db,fts,p['query'],allowed,p['offset'],p['limit'])
                    result['indexed_changes']=updated
                    return result
            row = self.lookup(data, p['thread'])
            if method == 'thread_access_status':
                result=self.brief(p['thread'],row)
                if row['owner_id']==self.person['id']:
                    result['readers']=row['readers'];result['migration_review']=row.get('migration_review',False)
                return result
            if method=='thread_access_changes':
                from .thread_search import changes
                return changes(self,row,p)
            value, relay = self.manifest(row, p['revision'])
            if method == 'thread_access_records':
                offset=integer(p['offset'],2**31-1); limit=integer(p['limit'],100,1)
                if p['record']: identifier(p['record'])
                docs=[decode(t) for t in (value['history'] if p['record'] else value['documents']).values()]
                docs=sorted((d for d in docs if not p['record'] or d['meta']['id']==p['record']),key=lambda d:(d['meta']['created_at'],d['meta']['id'],content_version(d)))
                return {'records':[dict(id=d['meta']['id'],title=d['meta']['title'],author=d['meta']['author'],version=content_version(d)) for d in docs[offset:offset+limit]],
                        'next_offset':offset+limit if offset+limit<len(docs) else None,'read_only':True}
            if method == 'thread_access_read':
                chars=integer(p['chars'], 32000, 1); offset=integer(p['offset'], 2**31-1)
                rid=p['record'] or p['thread']; identifier(rid)
                text = value['history'].get(rid+'@'+p['version']) if p['version'] else value['documents'].get('documents/'+rid+'.md')
                if text is None: raise Error('NOT_FOUND', 'This record is unavailable in the selected Thread.')
                doc=decode(text); body=doc['body']
                return dict(thread=p['thread'], owner_id=row['owner_id'], record=rid, title=doc['meta']['title'],
                            author=doc['meta']['author'], version=content_version(doc), content_revision=p['revision'] or row['sha'],
                            body=body[offset:offset+chars], next_offset=offset+chars if offset+chars<len(body) else None,
                            **({'execution_metadata': metadata} if (metadata := self.execution(row, p['revision'])) is not None else {}),
                            read_only=True, acl_revision=row['acl_revision'],
                            records=[{'id':decode(t)['meta']['id'],'title':decode(t)['meta']['title']} for t in list(value['documents'].values())[:100]] if rid==p['thread'] and offset==0 else [])
            if method == 'thread_access_files':
                offset=integer(p['offset'], 2**31-1); limit=integer(p['limit'], 100, 1)
                assets=sorted(value['assets'].items())
                return {'files':[dict(id=aid, name=a['name'], size=a['size'], sha=a['blob'], relative_path=a.get('relative_path',a['name']),
                                     state='available' if a['blob'] else 'index_only') for aid,a in assets[offset:offset+limit]],
                        'next_offset':offset+limit if offset+limit<len(assets) else None, 'read_only':True}
            asset=value['assets'].get(p['asset'])
            if not asset or not asset['blob']: raise Error('NOT_FOUND', 'This file is unavailable in the selected Thread.')
            # ACL is held through every bounded chunk read; revocation gates the next chunk.
            result=relay.dispatch({'method':'personal_get','sha':asset['blob'],'offset':p['offset']})
            return dict(result, name=asset['name'], read_only=True)


def public_asset(asset):
    return {key: asset.get(key) for key in ('name', 'size', 'blob', 'relative_path')} if asset else None


def public_content(value):
    return (value['documents'], value['history'],
            {aid: public_asset(asset) for aid, asset in value['assets'].items()})


def read_manifest(hub, row, sha=None):
    sha = sha or row['sha']
    check_id(sha, HASH)
    if sha not in row['history']:
        raise Error('NOT_FOUND', 'This published version is unavailable.')
    relay = Relay(hub, row['owner_account'])
    path = safe_path(relay.blobs / sha)
    if not path.is_file() or path.stat().st_size > MAX_METADATA:
        raise Error('SOURCE_PENDING', 'The accepted source is unavailable.')
    raw = path.read_bytes()
    if digest(raw) != sha: raise Error('CHECKSUM_MISMATCH', 'Accepted Thread content differs.')
    value = json.loads(raw); validate_manifest(value, Store(hub.root / 'thread-access-v1/validation'))
    return value, relay



def protected_ids(hub):
    data=read_json(hub.root/'thread-access-v1/catalog.json', {'threads':{}})
    return set(data['threads'])
