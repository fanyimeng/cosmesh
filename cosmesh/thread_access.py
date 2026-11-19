"""Single-owner Thread snapshots with authenticated, bounded online reading.

These snapshots stay outside public Git and the legacy global Library. Old
gateways cannot discover them. Existing public-Git IDs require a separate
migration before access can be narrowed; no false revocation is reported.
"""
import json
from contextlib import contextmanager
from uuid import uuid4

from .errors import Error
from .personal_protocol import HASH, MAX_METADATA, MIB, GIB, check_id, digest, integer, packed, private_dir, read_json, save_json
from .personal_relay import Relay
from .protocol import decode
from .store import Store, safe_path
from .thread_personal_protocol import validate_manifest, protect_history
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

    def execution(self, row, value=None):
        """Return only the shared projection; raw paths and Sessions stay private."""
        cached = row.get('execution_shared')
        if value is None and cached and row.get('execution_cache_version') == 1:
            return cached
        if value is None:
            value, _ = self.manifest(row)
        from .thread_execution_projection import shared_projection
        metadata = shared_projection(value.get('bindings', {}))
        row['execution_shared'] = metadata
        row['execution_revision'] = metadata['execution_revision']
        row['execution_cache_version'] = 1
        return metadata

    def ensure_execution(self, row):
        before = row.get('execution_cache_version') == 1 and 'execution_shared' in row
        self.execution(row)
        return not before

    def brief(self, tid, row):
        # Labels are current; identity is the stable person ID, never the label.
        with self.community.db() as db:
            person = self.community.resolve_user(db, row['owner_id'])
        result = dict(thread=tid, owner_id=row['owner_id'], owner_account=row['owner_account'],
                      owner_name=person['name'] or person['account'], title=row['title'][:300], title_complete=len(row['title'])<=300,
                      visibility=row['visibility'], acl_revision=row['acl_revision'],
                      content_revision=row['sha'], writable=row['owner_id']==self.person['id'],
                      usage=row['usage'], migrated=bool(row.get('migrated')))
        if 'execution_shared' in row:
            result['execution_metadata'] = row['execution_shared']
        return result

    def manifest(self, row, sha=None):
        return read_manifest(self.hub,row,sha)

    def legacy_check(self, tid):
        policies = read_json(self.hub.root / 'policy/repos.json', {})
        for repo, policy in policies.items():
            if policy.get('thread_format'):
                found = self.hub.repo(repo).git('cat-file', '-e', 'refs/heads/main:documents/'+tid+'.md', check=False)
                if not found.returncode:
                    raise Error('LEGACY_PUBLIC_MIGRATION_REQUIRED',
                                'This ID already exists in legacy public Git. Its old read paths must be migrated before changing visibility; existing content is retained.')

    def publish(self, data, p):
        tid = identifier(p['thread'], 'W'); mode = p['visibility']
        if mode not in MODES: raise Error('INPUT_INVALID', 'Use private, restricted or public.')
        self.community.check(self.account, write=mode!='private')
        readers = self.people(p['readers'])
        if mode != 'restricted' and readers: raise Error('INPUT_INVALID', 'Reader lists apply only to restricted Threads.')
        if mode == 'restricted' and not readers: mode = 'private'
        old = data['threads'].get(tid)
        if old and old['owner_id'] != self.person['id']:
            raise Error('NOT_FOUND', 'This Thread is unavailable to this account.')
        sha = check_id(p['sha'], HASH)
        expect = integer(p['expect'], 2**63-1)
        if old and (old['sha'], old['visibility'], old['readers']) == (sha, mode, readers):
            if self.ensure_execution(old): save_json(self.root / 'catalog.json', data)
            return dict(self.brief(tid, old), accepted=True, replayed=True)
        if expect != (old['acl_revision'] if old else 0):
            raise Error('STATE_CONFLICT', 'Thread access changed; review the current access before retrying.')
        if mode == 'private':
            if not old:
                raise Error('NOT_FOUND', 'This Thread is already private; use personal sync for owner-only content.')
            self.ensure_execution(old)
            old.update(visibility='private', readers=[], acl_revision=expect+1)
            save_json(self.root / 'catalog.json', data)
            return dict(self.brief(tid, old), accepted=True, replayed=False)
        # Legacy Git is a separate namespace. New ACL IDs cannot be published there.
        if not old or not old.get('migrated'):self.legacy_check(tid)
        relay = Relay(self.hub, self.account)
        head = relay.dispatch({'method': 'personal_thread_head', 'thread': tid})['sha']
        if head != sha and (not old or old['sha']!=sha): raise Error('STATE_CONFLICT', 'Publish the accepted current personal Thread snapshot.')
        candidate = {'sha': sha, 'owner_account': self.account, 'history': [sha]}
        value, relay = self.manifest(candidate)
        if value['thread'] != tid: raise Error('POLICY_DENIED', 'The source belongs to another Thread.')
        if old:
            before, _ = self.manifest(old)
            protect_history(before, value)
        # Raw coordinates and receipts stay private; only the whitelist projection is public.
        objects = {sha: len(packed(value).encode()), **{k:v for k,v in (old['objects'] if old else {}).items() if k.startswith('message:')}}
        for asset in value['assets'].values():
            if asset['blob']:
                if mode != 'private' and asset['size'] > LIMITS['file']:
                    raise Error('FILE_QUOTA', 'A published file exceeds the configured file limit; retain it privately and choose a smaller source or index.')
                objects[asset['blob']] = asset['size']
        retained = dict(old['objects']) if old else {}
        new_files = sum(a['size'] for blob,a in {a['blob']:a for a in value['assets'].values() if a['blob']}.items() if blob not in retained)
        if new_files > LIMITS['batch']:
            raise Error('TRANSFER_BUDGET', 'New published files exceed the configured batch limit.')
        retained.update(objects)
        usage = {'current': sum(objects.values()), 'retained': sum(retained.values())}
        if self.hub.settings().get('format')==2:
            with self.community.db() as db:library_current,library_retained=self.community.usage(db,tid)
            usage={'current':sum({**objects,**library_current}.values()),'retained':sum({**retained,**library_retained}.values())}
        if mode != 'private' and (usage['current'] > LIMITS['current'] or usage['retained'] > LIMITS['retained']):
            raise Error('STORAGE_QUOTA', 'Thread current or retained capacity exceeds the public limit; accepted content is retained.')
        doc = decode(value['documents']['documents/'+tid+'.md'])
        execution = self.execution({}, value)
        row = dict(owner_id=self.person['id'], owner_account=self.account, visibility=mode, readers=readers,
                   acl_revision=expect+1, sha=sha, title=doc['meta']['title'], usage=usage, objects=retained,
                   direct_current=sum(objects.values()), history=list(dict.fromkeys([*(old['history'] if old else []), sha])),
                   execution_shared=execution, execution_revision=execution['execution_revision'], execution_cache_version=1)
        if old and old.get('migrated'):
            row['migrated']=old['migrated']
            row['migration_review']=old.get('migration_review',False) and sha==old['sha']
        data['threads'][tid] = row
        save_json(self.root / 'catalog.json', data)
        return dict(self.brief(tid, row), accepted=True, replayed=False)

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
        if method not in allowed or set(p) != allowed[method]:
            raise Error('INPUT_INVALID', 'Invalid Thread access request; caller identity is server-derived.')
        with self.state() as data:
            if method == 'thread_access_info':
                return {'protocol': FORMAT, 'hub_id': data['hub_id'], 'person': {k:self.person[k] for k in ('id','account','name')}, 'limits': LIMITS, 'unified':self.hub.settings().get('format')==2, 'messages_protocol':'thread.messages.v1' if self.hub.settings().get('format')==2 else None, 'execution_protocol': 'thread.execution.v2'}
            if method == 'thread_access_set': return self.publish(data, p)
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
                changed = [self.ensure_execution(row) for _, row in selected]
                if any(changed): save_json(self.root / 'catalog.json', data)
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
                if self.ensure_execution(row): save_json(self.root / 'catalog.json', data)
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
                            execution_metadata=self.execution({}, value),
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
