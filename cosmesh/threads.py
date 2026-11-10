"""A private workbench with shared Thread replicas; no model or background execution."""
import copy
import hashlib
import json
import os
import socket
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .errors import Error
from .protocol import encode, decode, public, USER
from .store import Store, atomic, safe_path
from .thread_protocol import (Snapshot, new_id, content_version, references, reference_edges, check_record, CONTROL, thread_state)
from . import sync
from .connections import networked


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def workspace_root(explicit=None):
    parent = Path(os.environ.get('COSMESH_DATA_DIR', str(Path.home() / '.local/share/cosmesh')))
    return safe_path(explicit or parent / 'work')


def initialize(root, user, tz='Asia/Shanghai', dry_run=False):
    from .service import timezone as zone
    if not isinstance(user, str) or not USER.fullmatch(user):
        raise Error('INPUT_INVALID', 'Invalid local user.')
    zone(tz)
    store = Store(root)
    result, revision = store.initialize({'schema_version': 2, 'product': 'cosmesh', 'user': user,
        'timezone': tz, 'mode': 'local_unverified', 'scope': 'base'}, dry_run)
    if not dry_run:
        exclude = store.path('.git/info/exclude')
        atomic(exclude, exclude.read_text() + '\n.cosmesh/thread-client.json\n.cosmesh/mesh/\n.cosmesh/knowledge/\n')
    return result, revision


class Workbench:
    def __init__(self, root=None):
        self.base = Store(workspace_root(root))
        self.path = self.base.path('.cosmesh/thread-client.json')
        self.client = self.read_client()
        self.catalog = {}
        self.stores = {}
        self._models = {}

    def read_client(self):
        if not self.path.exists():
            return {'spaces': {}, 'bindings': []}
        if self.path.is_symlink() or self.path.stat().st_uid != os.geteuid() or self.path.stat().st_mode & 0o077:
            raise Error('AUTH_DENIED', 'Workbench execution metadata must be private and owned by this account.')
        value = json.loads(self.path.read_text())
        if not isinstance(value, dict) or not isinstance(value.get('spaces'), dict) or not isinstance(value.get('bindings'), list):
            raise Error('SCHEMA_INVALID', 'Invalid workbench metadata.')
        return value

    def save_client(self):
        atomic(self.path, json.dumps(self.client, ensure_ascii=False, indent=2) + '\n')
        os.chmod(self.path, 0o600)

    def load(self):
        self.client = self.read_client()
        models, revisions = {}, {}
        def model_for(repo, store):
            revision = store.head
            cached = self._models.get(repo)
            if cached is not None and cached[0] == revision:
                # Reuse only within this workbench; recheck managed paths on every load.
                for path in cached[1]: store.path(path)
                value = cached
            else:
                files = store.snapshot(revision)[1]
                value = (revision, files, Snapshot(store, files))
            models[repo] = value; revisions[repo] = revision
            return value[2]
        model = model_for('local', self.base)
        if model.config['scope'] != 'base':
            raise Error('PRODUCT_MISMATCH', '--root must select a Thread workbench; use thread init in a new directory.')
        self.config = model.config
        self.user = self.client.get('actor', self.config['user'])
        self.catalog, self.stores = {}, {'local': self.base}
        self._resolved = {}
        for doc_id, doc in model.docs.items():
            self.catalog[doc_id] = {'doc': doc, 'repo': 'local', 'store': self.base}
        for repo, info in sorted(self.client['spaces'].items()):
            from .thread_protocol import REPO
            if not REPO.fullmatch(repo):
                raise Error('SCHEMA_INVALID', 'Invalid cached repository ID.')
            store = Store(self.base.path('.cosmesh/mesh/' + repo))
            if not store.path('.cosmesh/config.json').exists():
                continue
            shared = model_for(repo, store)
            if shared.config['scope'] != 'mesh' or shared.config['user'] != info['owner']:
                raise Error('SCHEMA_INVALID', 'Cached space differs from its declared owner.')
            self.stores[repo] = store
            for doc_id, doc in shared.docs.items():
                old = self.catalog.get(doc_id)
                if old and old['repo'] != 'local' and old['repo'] != repo:
                    raise Error('ID_CONFLICT', 'One Thread record appears in two authoritative spaces.')
                if old and old['repo'] == 'local' and (repo != self.client.get('own_repo') or doc['meta']['author'] != self.user or old['doc']['meta']['author'] not in {self.config['user'], self.user}):
                    raise Error('ID_CONFLICT', 'Shared identity conflicts with a private record.')
                self.catalog[doc_id] = {'doc': doc, 'repo': repo, 'store': store}
        # Rebuilt from source records: no mirrored content or synthetic authorship.
        self._models, self._revisions = models, revisions
        self._accepted_models = {}
        self._incoming, self._records = {}, {}
        for item in self.catalog.values():
            thread = item['doc']['meta'].get('thread')
            if thread: self._records.setdefault(thread, []).append(item)
            for relation, ref in reference_edges(item['doc']['meta']):
                self._incoming.setdefault((ref['repo'], ref['id']), []).append((item, relation, ref))
        for items in self._records.values():
            items.sort(key=lambda x: (x['doc']['meta']['created_at'], x['doc']['meta']['id']))
        from .thread_names import rebuild
        rebuild(self)

    def find(self, value, kind=None):
        allowed=[x for x in self.catalog.values() if kind is None or x['doc']['meta']['kind']==kind]
        matches=[x for x in allowed if x['doc']['meta']['id']==value]
        if not matches:matches=[x for x in allowed if x['doc']['meta']['id'].startswith(value)]
        if not matches:
            from .thread_names import normalized
            ids=getattr(self,'_name_index',{}).get(normalized(value),set())
            matches=[x for x in allowed if x['doc']['meta']['id'] in ids]
        if not matches:matches=[x for x in allowed if x['doc']['meta']['title']==value]
        if len(matches) != 1:
            raise Error('NOT_FOUND' if not matches else 'AMBIGUOUS_ID', 'Choose one work or record ID.',
                        ', '.join(x['doc']['meta']['id'] for x in matches))
        return matches[0]

    def ref(self, item):
        return {'repo': item['repo'], 'id': item['doc']['meta']['id'], 'version': content_version(item['doc'])}

    def maintenance_entry(self):
        """Resolve the discovered entry through the ordinary local shared snapshot."""
        entry = self.client.get('maintenance')
        if not entry:
            raise Error('MAINTENANCE_UNAVAILABLE', 'No maintenance Thread is cached; run thread discover or ask the Hub maintainer to register one.')
        ref = entry['source']
        self.resolve(ref, accepted=True)
        item = self.find(ref['id'], 'thread')
        if item['repo'] == 'local' or item['repo'] != ref['repo'] or item['doc']['meta']['author'] != entry['maintainer']:
            raise Error('REFERENCE_INVALID', 'The cached maintenance Thread differs from the registered source.')
        if thread_state([x['doc']['meta'] for x in self.records(ref['id'])])['lifecycle'] != 'active':
            raise Error('MAINTENANCE_UNAVAILABLE', 'The maintenance Thread is inactive; refresh discovery or contact its maintainer.')
        result = {**entry, 'source': self.ref(item), 'title': item['doc']['meta']['title']}
        if self.client.get('community_enabled'):
            from .knowledge import Knowledge
            result['maintainer_name'] = Knowledge(self).user(entry['maintainer'])['name']
        return result

    def citation(self, value):
        """Resolve an ID or ID@VERSION supplied by the personal Agent."""
        if value.startswith('K-'):
            from .knowledge import Knowledge
            return Knowledge(self).ref(value)
        doc_id, separator, version = value.partition('@')
        ref = self.ref(self.find(doc_id))
        if separator:
            ref['version'] = version
        self.resolve(ref)
        return ref

    def resolve(self, ref, accepted=False):
        from .thread_protocol import reference
        reference(ref)
        if ref['repo']=='library':
            from .knowledge import Knowledge
            return Knowledge(self).doc(ref['id'],ref['version'])
        cache_key = (ref['repo'], ref['id'], ref['version'], accepted)
        if cache_key in self._resolved:
            return self._resolved[cache_key]
        store = self.stores.get(ref['repo'])
        if not store:
            raise Error('REFERENCE_UNAVAILABLE', 'Source space is not cached; run thread sync or discover.')
        if accepted:
            if ref['repo'] == 'local':
                raise Error('PRIVATE_REFERENCE', 'Publish a source work before sharing its result.')
            revision = store.client().get('remote_revision')
            if not revision:
                raise Error('SOURCE_PENDING', 'The source has not been accepted by the Hub.')
        else:
            revision = self._revisions.get(ref['repo']) or store.head
        if revision == self._revisions.get(ref['repo']):
            model = self._models[ref['repo']][2]
        else:
            key = (ref['repo'], revision)
            if key not in self._accepted_models:
                self._accepted_models[key] = Snapshot(store, store.snapshot(revision)[1])
            model = self._accepted_models[key]
        current = model.docs.get(ref['id'])
        if current is not None and content_version(current) == ref['version']:
            self._resolved[cache_key] = current
            return current
        path = 'documents/' + ref['id'] + '.md'
        commits = store.git('log', '-1000', '--format=%H', revision, '--', path).splitlines()
        for commit in commits:
            value = store.git('show', commit + ':' + path, check=False)
            if not value.returncode:
                doc = decode(value.stdout)
                if content_version(doc) == ref['version']:
                    self._resolved[cache_key] = doc
                    return doc
        raise Error('SOURCE_PENDING' if accepted else 'REFERENCE_UNAVAILABLE', 'Referenced source version is unavailable.')

    def accepted(self, item):
        try:
            self.resolve(self.ref(item), accepted=True)
            return True
        except Error:
            return False

    def records(self, thread):
        return list(self._records.get(thread, []))

    def _commit(self, store, action, payload, build, key=None, dry_run=False):
        key = key or str(uuid4())
        if not isinstance(key, str) or not key or len(key) > 200:
            raise Error('INPUT_INVALID', 'Idempotency key must contain 1–200 characters.')
        identity={'action': action, 'payload': payload, 'actor': self.user}
        if getattr(self,'generation',None):identity['generation']=self.generation
        fingerprint = hashlib.sha256(json.dumps(identity,sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        receipt_path = '.cosmesh/operations/' + hashlib.sha256((self.user + ':' + key).encode()).hexdigest() + '.json'
        def apply():
            files = store.snapshot()[1]
            if receipt_path in files:
                receipt = json.loads(files[receipt_path])
                if receipt['fingerprint'] != fingerprint:
                    raise Error('IDEMPOTENCY_CONFLICT', 'This key already identifies different input.')
                return {**receipt['result'], 'replayed': True, 'saved_locally': True}
            changes, result = build()
            candidate = dict(files, **changes)
            Snapshot(store, candidate)
            if dry_run:
                return {**result, 'dry_run': True, 'saved_locally': False}
            receipt = {'actor': self.user, 'key': key, 'fingerprint': fingerprint, 'result': result,
                       'operation':action,'generation':getattr(self,'generation',{'tool':'unspecified','verification':'not_provided'})}
            changes[receipt_path] = json.dumps(receipt, ensure_ascii=False, sort_keys=True) + '\n'
            store.commit(changes, key)
            return {**result, 'replayed': False, 'saved_locally': True}
        if store is self.base:
            return apply()
        with store.locked():
            return apply()

    def create(self, title, body='', key=None, dry_run=False, naming=None):
        def build():
            doc = {'meta': {'schema_version': 2, 'kind': 'thread', 'id': new_id('thread'),
                           'author': self.config['user'], 'created_at': now(), 'title': title}, 'body': body}
            if getattr(self,'generation',None):doc['meta']['generation']=self.generation
            changes={'documents/' + doc['meta']['id'] + '.md': encode(doc)};result=public(doc)
            if naming is not None:
                from .thread_names import record
                names=record(self,doc,naming);changes['documents/'+names['meta']['id']+'.md']=encode(names)
                result['naming_record']=names['meta']['id']
            return changes,result
        payload={'title':title,'body':body}
        if naming is not None:payload['naming']=naming
        return self._commit(self.base, 'create', payload, build, key, dry_run)

    def append(self, value, body='', record_type='note', refs=None, request=None, work=None,
               recipient=None, supersedes=None, complete=True, state=None, key=None, dry_run=False, agent=None,lifecycle=None,title=None):
        parent = self.find(value, 'thread')
        payload = {'thread': parent['doc']['meta']['id'], 'body': body, 'type': record_type,
                   'refs': refs or [], 'request': request, 'work': work or [], 'to': recipient,
                   'supersedes': supersedes, 'complete': complete, 'state': state, 'agent': agent,'lifecycle':lifecycle}
        if title is not None:
            if not isinstance(title,str) or not title.strip() or len(title)>200 or '\n' in title:
                raise Error('INPUT_INVALID', 'Provide a complete one-line title (at most 200 characters); rewrite rather than truncate.')
            payload['title']=title.strip()
        if lifecycle is None:payload.pop('lifecycle')
        author = self.config['user'] if parent['repo'] == 'local' else self.user
        def build():
            placement = thread_state([x['doc']['meta'] for x in self.records(payload['thread'])])['lifecycle']
            responding = placement in {'withdrawn', 'merged'} and record_type in CONTROL | {'reply'} and request
            if placement in {'trash', 'withdrawn', 'merged'} and not (responding or (record_type == 'lifecycle' and lifecycle in {'active', 'archived'})):
                raise Error('STATE_CONFLICT', 'Restore the removed or merged Thread before appending records.')
            meta = {'schema_version': 2, 'kind': 'entry', 'id': new_id('entry'), 'author': author,
                    'created_at': now(), 'title': record_type,
                    'thread': payload['thread'], 'type': record_type}
            from .thread_chat import summary
            brief = summary(body)
            fallback = {'request':'请求','reply':'回应','delivery':'交付','correction':'更正','note':'记录','result':'成果','summary':'概括'}.get(record_type,record_type)
            first = brief['text'].splitlines()[0] if brief['text'] else ''
            meta['title'] = payload.get('title') or (first if brief['origin'] != 'author' and len(first)<=100 else fallback)
            if getattr(self,'generation',None):meta['generation']=self.generation
            if parent['repo']!='local':
                from .knowledge import Knowledge
                users=Knowledge(self).data['users']
                own=next((x for x in users if x['account']==author),None)
                if own:meta['author_id']=own['id']
            for field in ('refs', 'request', 'work', 'to', 'supersedes', 'state', 'agent','lifecycle'):
                if payload.get(field):
                    meta[field] = payload[field]
            if record_type == 'delivery':
                meta['complete'] = complete
            if record_type=='lifecycle':
                states={x['doc']['meta'].get('previous'):x for x in self.records(meta['thread']) if x['doc']['meta']['type']=='state'}
                cursor=None;last=None
                while cursor in states:last=states[cursor];cursor=last['doc']['meta']['id']
                if last:meta['basis']=self.ref(last)
            if record_type in CONTROL or record_type in {'state','lifecycle'}:
                stream = [x['doc']['meta'] for x in self.records(meta['thread']) if x['doc']['meta']['author'] == author and
                          ((record_type in {'state','lifecycle'} and x['doc']['meta'].get('type') == record_type) or
                           (record_type in CONTROL and x['doc']['meta'].get('type') in CONTROL and x['doc']['meta'].get('request', {}).get('id') == request['id']))]
                previous = {x.get('previous'): x['id'] for x in stream}
                last = None
                while last in previous:
                    last = previous[last]
                meta['previous'] = last
            doc = {'meta': meta, 'body': body}
            if record_type=='summary':
                from .thread_names import parse
                if parse(doc) is not None:meta['title']='工作名称登记'
            if parent['repo'] != 'local' and any(r['repo'] == 'local' for r in references(meta)):
                raise Error('PRIVATE_REFERENCE', 'Shared records can cite only shared sources. Share the source Thread explicitly first.')
            check_record(doc, parent['doc'], self.resolve)
            return {'documents/' + meta['id'] + '.md': encode(doc)}, public(doc)
        return self._commit(parent['store'], 'append', payload, build, key, dry_run)

    def edit(self, value, expect, title=None, body=None, key=None, dry_run=False):
        item = self.find(value, 'thread')
        def build():
            doc = copy.deepcopy(item['doc'])
            allowed = {self.config['user']} if item['repo'] == 'local' else {self.user}
            if doc['meta']['author'] not in allowed:
                raise Error('AUTH_DENIED', 'Only the creator can revise this work overview.')
            if content_version(doc) != expect:
                raise Error('STATE_CONFLICT', 'The work overview changed; keep your input and read it again.')
            if title is not None:
                doc['meta']['title'] = title
            if body is not None:
                doc['body'] = body
            changes={'documents/' + doc['meta']['id'] + '.md': encode(doc)}
            if title is not None and title!=item['doc']['meta']['title']:
                from .thread_names import view,record
                old=view(self,doc['meta']['id']);values={k:old[k] for k in ('english','chinese','intro')}
                values['aliases']=[item['doc']['meta']['title']]
                name=record(self,doc,values,old['heads']);changes['documents/'+name['meta']['id']+'.md']=encode(name)
            return changes, public(doc)
        return self._commit(item['store'], 'edit', {'id': item['doc']['meta']['id'], 'expect': expect, 'title': title, 'body': body}, build, key, dry_run)

    def cache(self, config, space, refresh=True, remote_hint=None):
        repo = space['id']
        remote = dict(config, repo=repo)
        status = remote_hint or sync.remote_status(remote)
        if status.get('repo') != repo or not status.get('thread_format') or status.get('actor') != self.user:
            raise Error('AUTH_DENIED', 'This Hub must verify the saved account and Thread source.')
        store = Store(self.base.path('.cosmesh/mesh/' + repo))
        if store.path('.cosmesh/config.json').exists():
            if store.client().get('remote') != remote:
                raise Error('AUTH_DENIED', 'Cached source belongs to another Hub connection.')
            if refresh:
                sync.synchronize(store, 'cosmesh', publish=False, remote_hint=status)
        else:
            store.root.parent.mkdir(parents=True, exist_ok=True)
            # Older failed clones left an unborn Git directory. Preserve it before retrying.
            if (store.root.is_dir() and not store.root.is_symlink() and store.root.stat().st_uid == os.geteuid()
                    and {p.name for p in store.root.iterdir()} == {'.git'}
                    and not store.path('.git').is_symlink()
                    and store.git('rev-parse', '--verify', 'HEAD', check=False).returncode != 0):
                preserved = store.root.with_name(repo + '.interrupted-' + uuid4().hex)
                store.root.rename(preserved)
                self.client['preserved_clone'] = {'repo':repo, 'path':str(preserved)}
                self.save_client()
            sync.clone(store.root, 'cosmesh', remote, publish=False, remote_hint=status)
        self.client['spaces'][repo] = {'owner': space['owner']}
        self.save_client()
        return store

    @networked
    def discover(self, ssh=None, service=None):
        from .spaces import connection, rpc
        config = self.client.get('connection') if not (ssh or service) else None
        config = config or connection(self.base.root, ssh, service)
        reply = rpc(config, 'ensure_threads')
        actor = reply.get('actor')
        if not isinstance(actor, str) or not USER.fullmatch(actor):
            raise Error('SYNC_PROTOCOL', 'Thread discovery did not return a verified account.')
        if self.client.get('actor') and actor != self.client['actor']:
            raise Error('AUTH_DENIED', 'The configured Hub account changed; use a separate workbench.')
        self.user = actor
        self.client.update(actor=actor, connection=config, own_repo=reply['own_id'])
        self.client['community_enabled']=bool(reply.get('community_enabled'))
        self.client['maintenance']=reply.get('maintenance')
        self.save_client()
        results = []
        for space in reply['spaces']:
            try:
                maintenance_repo = (reply.get('maintenance') or {}).get('source', {}).get('repo')
                self.cache(config, space, refresh=space['id'] == maintenance_repo,
                           remote_hint=reply.get('versions', {}).get(space['id']))
                results.append({'repo': space['id'], 'cached': True})
            except Error as exc:
                results.append({'repo': space['id'], 'cached': False, 'error': exc.as_dict()})
        self.load()
        if self.client.get('community_enabled'):
            from .knowledge import Knowledge
            Knowledge(self).refresh()
        return {'actor': actor, 'own_repo': reply['own_id'], 'spaces': results,
                'ready': reply['own_id'] in self.stores, 'maintenance':self.client.get('maintenance')}

    def share(self, value, key=None, dry_run=False, extra=None, rewrite=None, materials=None, overview_notice=None):
        item = self.find(value, 'thread')
        if item['repo'] != 'local':
            return {**public(item['doc']), 'already_shared': True, 'saved_locally': True}
        repo = self.client.get('own_repo')
        if not repo or repo not in self.stores:
            raise Error('HUB_CONNECTION_REQUIRED', 'Run thread discover with your existing SSH alias first.')
        target = self.stores[repo]
        original = [item] + self.records(item['doc']['meta']['id'])
        originals = {x['doc']['meta']['id']: x['doc'] for x in original}
        if extra is not None:originals[extra['meta']['id']]=extra
        if item['doc']['meta']['author'] not in {self.config['user'], self.user}:
            raise Error('AUTH_DENIED', 'Only your own private work can be published.')
        def build():
            converted, visiting = {}, set()
            def convert(doc_id):
                if doc_id in converted:
                    return converted[doc_id]
                if doc_id in visiting:
                    raise Error('REFERENCE_INVALID', 'Cyclic private references.')
                visiting.add(doc_id)
                doc = copy.deepcopy(originals[doc_id])
                if rewrite is not None:
                    doc['body']=rewrite(doc['body'])
                    if doc['meta'].get('generation',{}).get('instruction'):
                        doc['meta']['generation']['instruction']=rewrite(doc['meta']['generation']['instruction'])
                if overview_notice and doc['meta']['kind']=='thread':doc['body']=doc['body'].rstrip()+'\n\n'+overview_notice+'\n'
                if doc['meta']['author'] not in {self.config['user'], self.user}:
                    raise Error('AUTH_DENIED', 'Private work contains another unverified author.')
                doc['meta']['author'] = self.user
                if doc['meta'].get('lifecycle') == 'trash':
                    doc['meta']['lifecycle'] = 'withdrawn'
                from .knowledge import Knowledge
                own=next((x for x in Knowledge(self).data['users'] if x['account']==self.user),None)
                if own:doc['meta']['author_id']=own['id']
                for ref in references(doc['meta']):
                    if ref['repo'] == 'local':
                        source = self.resolve(ref)
                        if ref['id'] in originals:
                            source = convert(ref['id'])
                            ref.update(repo=repo, version=content_version(source))
                        else:
                            other = self.find(ref['id'])
                            if other['repo'] == 'local':
                                raise Error('PRIVATE_REFERENCE', 'Share related private source Threads explicitly before this work.')
                            ref.update(self.ref(other))
                visiting.remove(doc_id)
                converted[doc_id] = doc
                return doc
            for doc_id in originals:
                convert(doc_id)
            changes = {'documents/' + doc_id + '.md': encode(doc) for doc_id, doc in converted.items()}
            files = target.snapshot()[1]
            if any(path in files and files[path] != text for path, text in changes.items()):
                raise Error('ID_CONFLICT', 'Shared storage already contains different content with this ID.')
            return changes, {'thread': item['doc']['meta']['id'], 'repo': repo, 'records': len(originals) - 1,
                             'publication': 'pending', 'scope': 'hub_authenticated_users', 'history': 'current_formal_records_only'}
        payload={'id': item['doc']['meta']['id']}
        if materials is not None:payload['materials']=materials
        return self._commit(target, 'share', payload, build, key, dry_run)

    @networked
    def synchronize(self, publish=True,knowledge_keys=None,publish_repos=None):
        def prepared_sources():
            knowledge=self.base.path('.cosmesh/knowledge/snapshot.json')
            return (dict(getattr(self,'_revisions',{})),{repo:store.client().get('remote_revision') for repo,store in self.stores.items()},
                    (knowledge.stat().st_mtime_ns,knowledge.stat().st_size) if knowledge.is_file() else None)
        previous_sources=prepared_sources()
        results, errors = [], []
        versions = {}
        if self.client.get('connection'):
            try:
                from .spaces import rpc
                reply = rpc(self.client['connection'], 'list_threads')
                if reply.get('actor') != self.user:
                    raise Error('AUTH_DENIED', 'Hub identity changed.')
                versions = reply.get('versions', {})
                if not isinstance(versions, dict): raise Error('SYNC_PROTOCOL', 'Invalid batch versions.')
                self.client['community_enabled']=bool(reply.get('community_enabled'))
                self.client['maintenance']=reply.get('maintenance')
                self.save_client()
                for space in reply['spaces']:
                    try:
                        self.cache(self.client['connection'], space, refresh=False, remote_hint=versions.get(space['id']))
                    except Error as exc:
                        errors.append({'repo': space['id'], 'error': exc.as_dict()})
            except Error as exc:
                errors.append({'repo': None, 'error': exc.as_dict()})
        self.load()
        if self.client.get('community_enabled'):
            from .knowledge import Knowledge
            try:
                kb=Knowledge(self)
                uploading=publish and knowledge_keys!=[]
                update=kb.synchronize(knowledge_keys) if uploading else kb.refresh()
                if uploading:errors.extend(update['errors'])
            except Error as exc:errors.append({'repo':'library','error':exc.as_dict()})
        for repo, store in self.stores.items():
            if repo == 'local':
                continue
            try:
                sync.synchronize(store, 'cosmesh', publish=False, remote_hint=versions.get(repo))
                results.append({'repo': repo, 'refreshed': True})
            except Error as exc:
                errors.append({'repo': repo, 'error': exc.as_dict()})
        self.load()
        if not self.client.get('spaces') and not errors:
            raise Error('HUB_CONNECTION_REQUIRED', 'Use thread discover before shared synchronization.')
        pending = {repo for repo, store in self.stores.items() if repo != 'local' and sync.state(store).get('unpushed_count')}
        if publish_repos is not None:pending.intersection_update(publish_repos)
        if publish:
            for _ in range(len(pending) + 1):
                progressed = False
                for repo in sorted(list(pending)):
                    store = self.stores[repo]
                    try:
                        docs = store.documents(store.snapshot()[1])
                        for doc in docs.values():
                            for ref in references(doc['meta']):
                                if ref['repo'] != repo:
                                    self.resolve(ref, accepted=True)
                        sync.synchronize(store, 'cosmesh', remote_hint=versions.get(repo))
                        results.append({'repo': repo, 'published': True})
                        pending.remove(repo)
                        progressed = True
                    except Error as exc:
                        if exc.code not in {'SOURCE_PENDING', 'REFERENCE_UNAVAILABLE'}:
                            errors.append({'repo': repo, 'error': exc.as_dict()})
                            pending.remove(repo)
                if not progressed:
                    break
            for repo in sorted(pending):
                errors.append({'repo': repo, 'error': Error('SOURCE_PENDING', 'Referenced source is still pending; local commits are retained.').as_dict()})
        self.client['last_sync'] = {'at': now(), 'complete': not errors, 'publish': publish, 'errors': errors}
        self.save_client()
        self.load()
        if self.client.get('community_enabled'):
            from .knowledge import Knowledge
            try:Knowledge(self).refresh()
            except Error as exc:errors.append({'repo':'library','error':exc.as_dict()})
        self.client['last_sync']={'at':now(),'complete':not errors,'publish':publish,'errors':errors}
        self.save_client()
        if prepared_sources()!=previous_sources:
            from .status_pages import schedule
            schedule(self)
        return {'synchronized': publish and not errors, 'refreshed': not errors, 'results': results, 'errors': errors}

    def request_state(self, item):
        request_id = item['doc']['meta']['id']
        actions = [x for x, relation, _ in self._incoming.get((item['repo'], request_id), [])
                   if relation == 'request' and x['doc']['meta'].get('type') in CONTROL]
        # Causal order within each actor stream, rather than timestamp order.
        terminals = {}
        for x in actions:
            meta = x['doc']['meta']
            terminals.setdefault(meta['author'], {})[meta.get('previous')] = x
        heads = []
        for stream in terminals.values():
            last, current = None, None
            while current in stream:
                last = stream[current]
                current = last['doc']['meta']['id']
            if last:
                heads.append(last)
        kinds = {x['doc']['meta']['type']: x for x in heads}
        state = 'pending'
        for kind, name in [('assign', 'accepted'), ('decline', 'declined'), ('delivery', 'delivered'), ('adopt', 'adopted'), ('cancel', 'cancelled')]:
            if kind in kinds:
                state = 'partial' if kind == 'delivery' and not kinds[kind]['doc']['meta']['complete'] else name
        return {'state': state, 'responses': [self.present(x) for x in actions],
                'confirmed': self.accepted(item) and all(self.accepted(x) for x in heads)}

    def present(self, item):
        extra = {}
        if item['doc']['meta']['kind'] == 'thread':
            state = thread_state([x['doc']['meta'] for x in self.records(item['doc']['meta']['id'])])
            extra = {key: state[key] for key in ('work_state', 'lifecycle', 'merged_into')}
            extra['naming'] = self._thread_names[item['doc']['meta']['id']]
        from .knowledge import Knowledge
        users=Knowledge(self).data['users']
        author=next((x for x in users if x['account']==item['doc']['meta']['author']),None)
        if author:extra.update(author_name=author['name'],person_id=author['id'])
        recipient=next((x for x in users if x['account']==item['doc']['meta'].get('to')),None)
        if recipient:extra['to_name']=recipient['name']
        return {**extra, **public(item['doc']), 'source': self.ref(item), 'shared': item['repo'] != 'local', 'visibility':'private' if item['repo'] == 'local' else 'public',
                'accepted': self.accepted(item) if item['repo'] != 'local' else False}

    def incoming_links(self, targets, include_private=False, version=None):
        """Read source-derived incoming record edges, optionally at one version."""
        links, seen = [], set()
        for target in targets:
            for source, relation, ref in self._incoming.get(target, []):
                if (not include_private and source['repo'] == 'local') or (version and ref['version'] != version):
                    continue
                identity = (source['repo'], source['doc']['meta']['id'], relation, ref['repo'], ref['id'], ref['version'])
                if identity in seen:
                    continue
                seen.add(identity)
                parent = self.find(source['doc']['meta']['thread'], 'thread')
                links.append({'record': self.present(source),
                              'thread': {'id': parent['doc']['meta']['id'], 'title': parent['doc']['meta']['title'],
                                         'source': self.ref(parent)},
                              'relation': relation, 'target': ref})
        links.sort(key=lambda x: (x['record']['created_at'], x['record']['id'], x['target']['id'], x['relation']))
        return links

    def backlinks(self, value):
        """Show incoming edges to a record, or to a Thread and its records."""
        item = self.find(value)
        targets = [item]
        if item['doc']['meta']['kind'] == 'thread':
            targets += self.records(item['doc']['meta']['id'])
        links = self.incoming_links([(x['repo'], x['doc']['meta']['id']) for x in targets],
                                    include_private=item['repo'] == 'local')
        return {'target': self.present(item), 'backlinks': links,
                'coverage': 'local_and_cached_mesh' if item['repo'] == 'local' else 'cached_mesh',
                'sources': self.status()['spaces']}

    def context(self, value):
        focus = self.find(value)
        item = focus if focus['doc']['meta']['kind'] == 'thread' else self.find(focus['doc']['meta']['thread'], 'thread')
        records = self.records(item['doc']['meta']['id']) if focus is item else [focus]
        citations = []
        for record in records:
            for ref in references(record['doc']['meta']):
                try:
                    source = self.resolve(ref)
                    corrected = [self.present(x) for x in self.catalog.values() if x['doc']['meta'].get('supersedes') == ref]
                    citations.append({'source': ref, 'document': public(source), 'accepted': self._ref_accepted(ref), 'corrections': corrected})
                except Error as exc:
                    citations.append({'source': ref, 'error': exc.as_dict()})
        requests = [{'request': self.present(x), **self.request_state(x)} for x in records if x['doc']['meta']['type'] == 'request']
        incoming = self.backlinks(value)
        related = {x['record']['id']: x['record'] for x in incoming['backlinks']
                   if x['target']['id'] == focus['doc']['meta']['id']}
        return {'thread': self.present(item), 'records': [self.present(x) for x in records],
                'focus_record': self.present(focus) if focus is not item else None,
                'requests': requests, 'citations': citations, 'related': list(related.values()),
                'backlinks': incoming['backlinks'], 'backlink_coverage': incoming['coverage'],
                'local_execution': [x for x in self.client['bindings'] if x['thread'] == item['doc']['meta']['id']],
                'sources': self.status()['spaces'], 'resume': 'Use an accessible host session when supported; otherwise start a new session from this context.'}

    def _ref_accepted(self, ref):
        try:
            self.resolve(ref, accepted=True)
            return True
        except Error:
            return False

    def coordination(self, value):
        """Export evidence for the caller's Agent; perform no coordination actions."""
        focus = self.find(value)
        request = None
        if focus['doc']['meta']['kind'] == 'entry':
            if focus['doc']['meta'].get('type') != 'request':
                raise Error('INPUT_INVALID', 'Use a Thread or request ID for coordination context.')
            request = focus
            focus = self.find(request['doc']['meta']['thread'], 'thread')
        thread_id = focus['doc']['meta']['id']
        records = self.records(thread_id)
        requests = [{'request': self.present(x), **self.request_state(x)}
                    for x in records if x['doc']['meta']['type'] == 'request']
        queue = deque([self.ref(focus)] + [self.ref(x) for x in records])
        works, work_ids = [], set()
        for record in records:
            for ref in record['doc']['meta'].get('work', []):
                if ref['id'] in work_ids:
                    continue
                work_ids.add(ref['id'])
                try:
                    item = self.find(ref['id'], 'thread')
                    if item['repo'] != ref['repo']:
                        raise Error('REFERENCE_UNAVAILABLE', 'Assigned work belongs to another source space.')
                    current_records = self.records(ref['id'])
                    works.append({'assigned_source': ref, 'thread': self.present(item),
                                  'records': [self.present(x) for x in current_records],
                                  'backlinks': self.backlinks(ref['id'])['backlinks']})
                    queue.extend([self.ref(item)] + [self.ref(x) for x in current_records])
                except Error as exc:
                    works.append({'assigned_source': ref, 'error': exc.as_dict()})
        corrections = {}
        for item in self.catalog.values():
            ref = item['doc']['meta'].get('supersedes')
            if ref and (focus['repo'] == 'local' or item['repo'] != 'local'):
                corrections.setdefault(tuple(ref[k] for k in ('repo', 'id', 'version')), []).append(item)
        citations, seen = [], set()
        while queue:
            ref = queue.popleft()
            identity = tuple(ref[k] for k in ('repo', 'id', 'version'))
            if identity in seen:
                continue
            seen.add(identity)
            try:
                doc = self.resolve(ref)
                corrected = corrections.get(identity, [])
                citations.append({'source': ref, 'document': public(doc), 'accepted': self._ref_accepted(ref),
                                  'corrections': [self.present(x) for x in corrected]})
                queue.extend(references(doc['meta']))
                queue.extend(self.ref(x) for x in corrected)
            except Error as exc:
                citations.append({'source': ref, 'error': exc.as_dict()})
        return {'purpose': 'personal_agent_coordination', 'thread': self.present(focus),
                'focus_request': self.present(request) if request else None,
                'coordination': {'default_coordinator': (request or focus)['doc']['meta']['author'],
                                 'acting_user': self.user, 'delegation': 'ordinary_request', 'executes_actions': False},
                'records': [self.present(x) for x in records], 'requests': requests, 'related_work': works,
                'backlinks': self.backlinks(thread_id)['backlinks'],
                'citations': citations, 'source_index': [x['source'] for x in citations if 'document' in x],
                'evidence_complete': all('error' not in x for x in citations + works),
                'sources': self.status()['spaces']}

    def bind(self, value, agent, workdir, session_id=None, host=None, dry_run=False):
        thread = self.find(value, 'thread')['doc']['meta']['id']
        host = host or socket.gethostname()
        path = str(Path(workdir).expanduser().absolute())
        if host == socket.gethostname() and not Path(path).is_dir():
            raise Error('PATH_INVALID', 'The current-host work directory does not exist.')
        if not agent.strip():
            raise Error('INPUT_INVALID', 'Specify the actual Agent name.')
        binding = {'thread': thread, 'agent': agent, 'host': host, 'workdir': path, 'session_id': session_id}
        if not dry_run and binding not in self.client['bindings']:
            self.client['bindings'].append(binding)
            self.save_client()
        return {'binding': binding, 'private': True, 'saved_locally': not dry_run, 'sync': None}

    def status(self):
        spaces = []
        for repo, store in self.stores.items():
            if repo != 'local':
                spaces.append({'repo': repo, 'owner': self.client['spaces'][repo]['owner'], **sync.state(store)})
        return {'root': str(self.base.root), 'user': self.user, 'local_user': self.config['user'],
                'thread_count': sum(x['doc']['meta']['kind'] == 'thread' for x in self.catalog.values()),
                'spaces': spaces, 'last_sync': self.client.get('last_sync'),
                'identity_verified': bool(spaces) and all(x.get('identity_verified') for x in spaces),
                'state': 'private' if not spaces else ('synced' if all(x['state'] == 'synced' for x in spaces) else 'needs_sync')}
