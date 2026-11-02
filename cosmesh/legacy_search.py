"""Read-only schema 1 snapshots used by the unified search reader."""
import hashlib
import json
import os
import re
from pathlib import Path

from .errors import Error
from .protocol import decode, encode, identifier, version
from .store import Store


def default_roots():
    """Only known application locations; never scan projects or other homes."""
    from .spaces import settings
    data = Path(os.environ.get('COSMESH_DATA_DIR', str(Path.home()/'.local/share/cosmesh')))
    roots = [data/'cosbase', data/'cosmesh']
    if (data/'spaces').is_dir():
        roots.extend(sorted((data/'spaces').iterdir()))
    active = settings().get('mesh_root')
    if active and not os.environ.get('COSMESH_DATA_DIR'): roots.append(Path(active))
    return [p for p in roots if (p/'.cosmesh/config.json').is_file()]


def source_repo(root):
    root = str(Path(root).expanduser().absolute())
    return 'legacy-'+hashlib.sha256(root.encode()).hexdigest()[:24]


class LegacySources:
    def __init__(self, roots=(), scope='all'):
        self.models, self.errors = {}, []
        self._snapshots, self._resolved = {}, {}
        for root in dict.fromkeys(str(Path(p).expanduser().absolute()) for p in roots):
            store = None
            try:
                store = Store(root)
                if store.root.stat().st_uid != os.geteuid():
                    raise Error('AUTH_DENIED', 'Use an owned local Base or Mesh replica.')
                git = store.path('.git')
                if not git.is_dir() or git.stat().st_uid != os.geteuid():
                    raise Error('PATH_INVALID', 'Use an owned managed Git directory.')
                revision = store.head
                config = json.loads(store.git('show', revision+':.cosmesh/config.json'))
                product = config.get('product')
                if config.get('schema_version') != 1 or product not in {'cosbase','cosmesh'}:
                    raise Error('SCHEMA_UNSUPPORTED', 'Legacy search requires a schema 1 Base or Mesh.')
                source_scope = 'base' if product == 'cosbase' else 'mesh'
                if scope != 'all' and scope != source_scope: continue
                model = self.snapshot(store, revision, product)
                model.update(repo=source_repo(root),
                             scope=source_scope, client=store.client(), fresh=False)
                self.models[model['repo']] = model
            except (Error, OSError, ValueError) as exc:
                # Scope mesh does not reveal private paths, even in diagnostics.
                detail = exc.as_dict() if isinstance(exc, Error) else {'code':'IO_ERROR','message':str(exc)}
                self.errors.append({'error':detail} if scope=='mesh' else {'root':root,'error':detail})

    def snapshot(self, store, revision, product):
        key = (str(store.root), revision)
        if key not in self._snapshots:
            from .hub import view
            files = store.snapshot(revision)[1]
            service = view(store, files, product)
            self._snapshots[key] = {'store':store, 'revision':revision, 'config':service.config,
                                    'docs':service.docs, 'service':service, 'states':{}, 'refs':{}}
        return self._snapshots[key].copy()

    def ref(self, model, doc):
        key=doc['meta']['id']
        if key not in model['refs']:
            model['refs'][key]={'repo':model['repo'], 'id':key, 'version':version(encode(doc))}
        return model['refs'][key].copy()

    def coverage(self, scope='all'):
        return {'discovery':'owned_cached_sources', 'sources':[
            {'repo':m['repo'], 'root':str(m['store'].root), 'scope':m['scope'],
             'schema_version':1, 'source_revision':m['revision'], 'documents':len(m['docs']),
             'remote_revision':m['client'].get('remote_revision') if m['scope']=='mesh' else None,
             'last_sync_at':m['client'].get('last_sync_at') if m['scope']=='mesh' else None,
             'remote_verified':m['fresh']} for m in self.models.values() if scope=='all' or m['scope']==scope],
                'errors':([{'error':{'code':x['error']['code'],'message':'An old source is unavailable.'}} for x in self.errors] if scope=='mesh' else self.errors)}

    def verify_fresh(self):
        """Verify shared refs without locking, recovering, fetching or changing old stores."""
        from .sync import remote_status
        checked = {}
        for m in self.models.values():
            if m['scope']=='base': continue
            remote = m['client'].get('remote')
            if not remote: raise Error('FRESHNESS_UNAVAILABLE', 'An old Mesh source has no Hub connection.')
            key = json.dumps(remote, sort_keys=True)
            if key not in checked: checked[key] = remote_status(remote)
            status = checked[key]
            if status.get('revision') != m['client'].get('remote_revision'):
                raise Error('FRESHNESS_UNAVAILABLE', 'An old Mesh cache is behind the Hub; explicitly fetch that space and retry.')
            m['fresh'] = True

    def parent(self, model, doc):
        meta = doc['meta']
        return model['docs'].get(meta.get('task') or meta.get('project'))

    def state(self, model, doc):
        meta = doc['meta'];key = meta['id']
        if key not in model['states']:
            state = {'lifecycle':meta['lifecycle'], 'work_state':meta.get('status')}
            if model['scope']=='mesh' and meta['kind']=='task':
                state['work_state'] = model['service'].reduce(doc)['status']
            model['states'][key] = state
        return model['states'][key]

    def items(self, include_inactive=False):
        for model in self.models.values():
            for doc in model['docs'].values():
                parent = self.parent(model, doc)
                if not include_inactive and doc['meta']['lifecycle']=='trash': continue
                yield model, doc, parent

    def accepted(self, model, doc):
        revision = model['client'].get('remote_revision')
        if model['scope']=='base' or not revision: return False
        if revision==model['revision']: return True
        accepted = self.snapshot(model['store'], revision, model['config']['product'])['docs'].get(doc['meta']['id'])
        return accepted is not None and encode(accepted)==encode(doc)

    def find(self, value, repo=None):
        matches = [(m,m['docs'][value]) for m in self.models.values()
                   if (repo is None or repo==m['repo']) and value in m['docs']]
        # Identical cached copies represent one source value; differing versions remain ambiguous.
        if repo is None:
            unique = {}
            for m,d in matches: unique.setdefault((m['scope'],version(encode(d))), (m,d))
            matches = list(unique.values())
        if len(matches)!=1:
            raise Error('NOT_FOUND' if not matches else 'AMBIGUOUS_ID',
                        'Choose one old source using the repo/ID@VERSION returned by search.')
        return matches[0]

    def resolve(self, model, identifier, wanted):
        current = model['docs'][identifier]
        if wanted is None or self.ref(model,current)['version']==wanted: return model, current
        if not isinstance(wanted,str) or not re.fullmatch(r'[0-9a-f]{64}',wanted):
            raise Error('INPUT_INVALID','Use a complete source content version.')
        key = (model['repo'], identifier, wanted)
        if key not in self._resolved:
            path = 'documents/'+identifier+'.md'
            for commit in model['store'].git('log','-1000','--format=%H',model['revision'],'--',path).splitlines():
                raw = model['store'].git('show',commit+':'+path,check=False)
                if raw.returncode: continue
                doc = decode(raw.stdout, identifier+'.md')
                if version(encode(doc))==wanted:
                    old = self.snapshot(model['store'],commit,model['config']['product'])
                    self._resolved[key] = ({**model,**old}, doc);break
            else: raise Error('REFERENCE_UNAVAILABLE','The old source version is not available in the bounded history.')
        return self._resolved[key]

    def edges(self, model, doc):
        meta = doc['meta'];edges = []
        for relation in ('project','task','reply_to','supersedes','sources','converted_to'):
            values = meta.get(relation)
            if not values: continue
            for value in values if isinstance(values,list) else [values]:
                if value in model['docs']: edges.append((relation,self.ref(model,model['docs'][value])))
        return edges

    def read(self, value, repo, scope, offset, chars, links_limit, parent_chars, person):
        key, _, wanted = value.partition('@');identifier(key)
        current_model,current = self.find(key,repo)
        if scope!='all' and current_model['scope']!=scope:
            raise Error('PRIVATE_REFERENCE','This old source is outside the requested scope.')
        model,doc = self.resolve(current_model,key,wanted or None)
        source = self.ref(model,doc);parent = self.parent(model,doc)
        outgoing = [{'relation':kind,**target} for kind,target in self.edges(model,doc)]
        if 'incoming' not in current_model:
            current_model['incoming']={}
            for other in current_model['docs'].values():
                for relation,target in self.edges(current_model,other):
                    current_model['incoming'].setdefault(target['id'],[]).append((other,relation,target))
        incoming = [{'source':self.ref(current_model,other),'title':other['meta']['title'][:500],
                     'relation':relation,'author':person(other['meta']['author']),
                     'target':target,'target_version_matches':target['version']==source['version']}
                    for other,relation,target in current_model['incoming'].get(key,[])]
        incoming.sort(key=lambda x:(x['relation']!='supersedes',x['source']['id']))
        fields = ('schema_version','id','kind','title','author','created_at','updated_at','lifecycle',
                  'status','type','project','task','due','followup','next_action','waiting')
        metadata = {k:(v[:1000] if isinstance(v,str) else v) for k,v in doc['meta'].items() if k in fields}
        return {'source':source,'current_source':self.ref(current_model,current),
                'is_current':source==self.ref(current_model,current),'source_revision':model['revision'],
                'accepted':self.accepted(current_model,doc),'format':'legacy','source_kind':doc['meta']['kind'],
                'state':self.state(current_model,current),'document':metadata,
                'metadata_omitted':sorted(set(doc['meta'])-set(metadata)),
                'author':person(doc['meta']['author']), 'text':doc['body'][offset:offset+chars],
                'location':{'field':'body','offset':offset},'truncated':offset+chars<len(doc['body']),
                'next_offset':offset+chars if offset+chars<len(doc['body']) else None,
                'thread':{'source':self.ref(model,parent),'title':parent['meta']['title'][:500],
                          'overview':parent['body'][:parent_chars], 'overview_truncated':len(parent['body'])>parent_chars} if parent else None,
                'outgoing':outgoing[:links_limit], 'incoming':incoming[:links_limit],
                'links_total':{'outgoing':len(outgoing),'incoming':len(incoming)},
                'links_version_scope':'legacy_snapshot; incoming_from_current',
                'source_version_matches':self.ref(model,doc)['version']==source['version']}
