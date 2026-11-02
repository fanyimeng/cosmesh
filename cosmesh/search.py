"""Bounded lexical retrieval for a personal Agent's query expansion and source review."""
from datetime import datetime, timedelta, timezone
import re

from .errors import Error
from .connections import networked


def query_groups(queries):
    if not isinstance(queries, list) or not 1 <= len(queries) <= 8:
        raise Error('INPUT_INVALID', 'Provide 1–8 query variants in one batch.')
    groups = []
    for value in queries:
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise Error('INPUT_INVALID', 'Each query must contain 1–500 characters.')
        terms = list(dict.fromkeys(value.casefold().split()))
        if len(terms) > 12: raise Error('INPUT_INVALID', 'Use at most 12 terms per query variant.')
        if terms not in groups: groups.append(terms)
    return groups


def score(fields, groups):
    """All terms within a variant; any variant may match. Scores are ranks, not probabilities."""
    folded = [(name, text.casefold(), weight) for name, text, weight in fields]
    matches, best = [], 0
    for index, terms in enumerate(groups):
        weights = [max((weight for _, text, weight in folded if term in text), default=0) for term in terms]
        if all(weights):
            matches.append(index)
            phrase = ' '.join(terms)
            best = max(best, sum(weights) + max((2*weight for _, text, weight in folded if phrase in text), default=0))
    return best + min(3, len(matches)) if matches else 0, matches


def excerpt(text, terms, length=280):
    folded = text.casefold()
    positions = [folded.find(term) for term in terms if term in folded]
    point = min(positions) if positions else 0
    if len(folded) != len(text):
        # Map the folded location back to the untouched source, e.g. German ß.
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo+hi+1)//2
            if len(text[:mid].casefold()) <= point: lo = mid
            else: hi = mid-1
        point = lo
    start = max(0, point-50)
    return {'text': text[start:start+length], 'offset': start,
            'truncated': start > 0 or start+length < len(text)}


def add_commands(commands, command):
    group = command(commands, 'search').add_subparsers(dest='search_command', required=True)
    for verb in ('query', 'read'):
        p = command(group, verb, 'search.'+verb)
        if verb=='query':p.add_argument('query')
        else:p.add_argument('id',nargs='+')
        p.add_argument('--scope', choices=('all','base','mesh'), default='all')
        p.add_argument('--legacy-root', action='append', default=[], help='Select old read-only stores instead of automatic legacy discovery.')
        p.add_argument('--no-legacy', action='store_true', help='Only query the Thread workbench and knowledge cache.')
        p.add_argument('--offset', type=int, default=0)
        if verb == 'query':
            p.add_argument('--query', dest='variants', action='append', default=[])
            p.add_argument('--full-text', action='store_true')
            p.add_argument('--kind', choices=('thread','record','knowledge'), action='append')
            p.add_argument('--author');p.add_argument('--after');p.add_argument('--before')
            p.add_argument('--include-inactive', action='store_true')
            p.add_argument('--limit', type=int, default=10)
        else:
            p.add_argument('--repo');p.add_argument('--file')
            p.add_argument('--chars', type=int, default=8000)
            p.add_argument('--links-limit', type=int, default=10)
            p.add_argument('--parent-chars', type=int, default=600)


class Search:
    def __init__(self, wb=None, legacy=None):
        from .knowledge import Knowledge
        from .legacy_search import LegacySources
        self.wb, self.kb = wb, Knowledge(wb) if wb else None
        self.legacy = legacy if legacy is not None else LegacySources()
        self.people = {x['account']:x for x in self.kb.data['users']} if self.kb else {}
        self.parents = {}

    def identity(self, account, repo=None):
        actor=self.wb.client.get('actor') if self.wb else None
        if repo=='local' and self.wb and account==self.wb.config['user'] and isinstance(actor,str) and actor:return actor
        old=self.legacy.models.get(repo)
        if old and old['scope']=='base' and account==old['config']['user']:
            return old['client'].get('actor') or account
        return account

    def person(self, account, repo=None):
        identity=self.identity(account,repo);user = self.people.get(identity, {})
        return {'account': account, 'identity_account':identity,'name': user.get('name'), 'id': user.get('id')}

    def text_path(self, sha):
        from .knowledge_protocol import DIGEST
        from .store import safe_path
        if not isinstance(sha,str) or not DIGEST.fullmatch(sha):raise Error('SCHEMA_INVALID','Invalid extracted text hash.')
        return safe_path(self.kb.root/sha)

    def parent(self, item):
        identifier = item['doc']['meta'].get('thread', item['doc']['meta']['id'])
        if identifier not in self.parents:
            parent = self.wb.find(identifier, 'thread')
            self.parents[identifier] = (parent, self.wb.present(parent))
        return self.parents[identifier]

    def allowed(self, repo, scope):
        old=self.legacy.models.get(repo)
        return scope == 'all' or (old['scope']==scope if old else (scope == 'base') == (repo == 'local'))

    def coverage(self, scope='all'):
        old=self.legacy.coverage(scope)
        return {'sources': self.wb.status()['spaces'] if self.wb and scope!='base' else [],
                'base_revision': self.wb._revisions['local'] if self.wb and scope!='mesh' else None,
                'knowledge_as_of': self.kb.data.get('synced_at') if self.kb and scope!='base' else None, 'scope':scope,
                'legacy':old, 'thread_workbench':bool(self.wb),
                'original_text_required_for_confirmation': True}

    def author(self, value, scope='all'):
        if not value: return None
        from .knowledge import select_user
        try:return select_user(self.kb.data['users'] if self.kb else [], value)['account']
        except Error as exc:
            if exc.code!='USER_NOT_FOUND':raise
        known = {x['doc']['meta']['author'] for x in self.wb.catalog.values() if self.allowed(x['repo'],scope)} if self.wb else set()
        known.update(d['meta']['author'] for m in self.legacy.models.values() if self.allowed(m['repo'],scope) for d in m['docs'].values())
        if value in known:return self.identity(value,'local' if self.wb and value==self.wb.config['user'] else None)
        raise Error('USER_NOT_FOUND','Choose an exact real name, account or stable user ID.')

    def date_bound(self, value, upper=False):
        if not value: return None
        try:
            result = datetime.fromisoformat(value)
            if result.tzinfo is None:
                from zoneinfo import ZoneInfo
                config=self.wb.config if self.wb else next((m['config'] for m in self.legacy.models.values()),{'timezone':'Asia/Shanghai'})
                result = result.replace(tzinfo=ZoneInfo(config['timezone']))
            if upper and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value): result += timedelta(days=1)
            return result.astimezone(timezone.utc)
        except (ValueError, KeyError): raise Error('INPUT_INVALID', 'Use an ISO date or timestamp.')

    def query(self, queries, scope='all', kinds=None, limit=10, offset=0, full_text=False,
              offline=False, include_inactive=False, author=None, after=None, before=None):
        groups = query_groups(queries)
        if not 1 <= limit <= 50 or offset < 0: raise Error('INPUT_INVALID', 'Use limit 1–50 and a nonnegative offset.')
        kinds = set(kinds or ('thread','record','knowledge'))
        author = self.author(author,scope);after = self.date_bound(after);before = self.date_bound(before, True)
        if after and before and after >= before: raise Error('INPUT_INVALID', 'The date range is empty.')
        candidates, missing, diagnostics = {}, [], []
        def eligible(meta,repo=None):
            when = datetime.fromisoformat(meta['created_at'])
            return (not author or self.identity(meta['author'],repo)==author) and (not after or when>=after) and (not before or when<before)
        def collect(source, kind, doc, fields, extra, location=None, indexed=False, forced_matches=None):
            rank, matches = score(fields, groups)
            if forced_matches is not None:
                matches=forced_matches
                rank=max((4*len(groups[i]) for i in matches),default=0)+min(3,len(matches))
            if not rank: return
            key = tuple(source[k] for k in ('repo','id'))
            best_field = max(fields, key=lambda f:score([f],groups)[0])
            # Variants can match across title/background/body; the excerpt is always real text.
            terms = [term for i in matches for term in groups[i]]
            fragment = excerpt(best_field[1], terms)
            result = {'source':source,'kind':kind,'title':doc['meta']['title'],
                      'author':self.person(doc['meta']['author'],source['repo']),'created_at':doc['meta']['created_at'],
                      'score':rank,'matched_queries':matches,'excerpt':fragment['text'],
                      'matched_fields':list(dict.fromkeys(name for name,text,_ in fields if any(t in text.casefold() for t in terms))),
                      'location':location or {'field':best_field[0],'offset':fragment['offset']},
                      'excerpt_from_index':indexed,'requires_source_review':True, **extra}
            old = candidates.get(key)
            if old is None or old['score'] < rank: candidates[key] = result
        for item in self.wb.catalog.values() if self.wb else []:
            doc, repo = item['doc'], item['repo'];meta=doc['meta']
            kind = 'thread' if meta['kind']=='thread' else 'record'
            if kind not in kinds or not self.allowed(repo,scope) or not eligible(meta,repo): continue
            parent, state = self.parent(item)
            if not include_inactive and state['lifecycle'] in {'trash','withdrawn','merged'}: continue
            fields=[('title',meta['title'],8),('body',doc['body'],4)]
            person=self.person(meta['author'],repo)
            fields.append(('author',meta['author']+' '+(person['name'] or ''),6))
            if meta.get('to'):
                person=self.person(meta['to']);fields.append(('recipient',meta['to']+' '+(person['name'] or ''),4))
            if kind=='record':fields.append(('thread',parent['doc']['meta']['title']+'\n'+parent['doc']['body'],2))
            collect({'repo':repo,'id':meta['id'],'version':None},kind,doc,fields,
                    {'_item':item,'thread':{'source':state['source'],'title':parent['doc']['meta']['title']},
                     'record_type':meta.get('type'),'lifecycle':state['lifecycle'],'work_state':state['work_state'],
                     'accepted':False})
        for model,doc,parent in self.legacy.items(include_inactive):
            meta=doc['meta'];repo=model['repo'];kind='record' if meta['kind']=='update' else 'thread'
            if kind not in kinds or not self.allowed(repo,scope) or not eligible(meta,repo): continue
            fields=[('title',meta['title'],8),('body',doc['body'],4)]
            person=self.person(meta['author'],repo)
            fields.append(('author',meta['author']+' '+(person['name'] or ''),6))
            for field in ('next_action','waiting'):
                if meta.get(field):fields.append((field,meta[field],4))
            if parent:fields.append(('thread',parent['meta']['title']+'\n'+parent['body'],2))
            state=self.legacy.state(model,doc)
            collect(self.legacy.ref(model,doc),kind,doc,fields,
                    {'format':'legacy','source_kind':meta['kind'],'source_revision':model['revision'],
                     '_legacy':(model,doc),'thread':{'source':self.legacy.ref(model,parent),'title':parent['meta']['title']} if parent else None,
                     'parent_lifecycle':parent['meta']['lifecycle'] if parent else None,
                     'record_type':meta.get('type'),**state,'accepted':False})
        if self.kb and 'knowledge' in kinds and scope!='base':
            from .community import packed,digest
            for identifier, version in self.kb.data['heads'].items():
                doc=self.kb.doc(identifier);meta=doc['meta']
                if not eligible(meta) or (not include_inactive and meta['status']!='published'):continue
                source={'repo':'library','id':identifier,'version':version}
                extra={'status':meta['status'],'resource_kind':meta['resource_kind'],'accepted':True}
                fields=[('title',meta['title'],8),('body',doc['body'],4),('bibliography',packed(meta['bibliography']),5)]
                for role in ('author','maintainer'):
                    person=self.person(meta[role]);fields.append((role,meta[role]+' '+(person['name'] or ''),5))
                collect(source,'knowledge',doc,fields,extra)
                if full_text:
                    for file in meta['files']:
                        sha=file['text_sha256'];path=self.text_path(sha) if sha else None
                        if not sha or not path.is_file():missing.append({'id':identifier,'file':file['name'],'reason':'not_cached' if sha else file['text_status']});continue
                        raw=path.read_bytes()
                        if digest(raw)!=sha:missing.append({'id':identifier,'file':file['name'],'reason':'checksum_mismatch'});continue
                        text=raw.decode('utf-8')
                        rank,matches=score([('text',text,4)],groups)
                        if rank:
                            fragment=excerpt(text,[t for i in matches for t in groups[i]])
                            collect(source,'knowledge',doc,[('text',text,4)],extra,
                                    {'field':'text','file':file['name'],'sha256':sha,'offset':fragment['offset']})
            if full_text and not offline and self.wb.client.get('connection') and not include_inactive:
                try:
                    page=self.kb.rpc({'method':'community_search_batch','queries':queries,'limit':50})
                    unknown=[h for h in page['hits'] if (h['id'],h['version']) not in self.kb.versions]
                    if unknown:
                        self.kb.refresh();self.people={x['account']:x for x in self.kb.data['users']}
                        candidates={k:v for k,v in candidates.items() if k[0]!='library' or self.kb.data['heads'].get(k[1])==v['source']['version']}
                    for hit in page['hits']:
                        doc=self.kb.doc(hit['id'],hit['version'])
                        if not eligible(doc['meta']):continue
                        collect({'repo':'library','id':hit['id'],'version':hit['version']},'knowledge',doc,
                                [('index',hit['snippet'],4)],{'status':doc['meta']['status'],'resource_kind':doc['meta']['resource_kind'],'accepted':True},
                                {'field':'index','requires_file_read':True},True,hit['matched_queries'])
                    diagnostics.append({'server_full_text':True,'total':page['total'],'returned':len(page['hits']),'has_more':page['has_more']})
                except Error as exc:
                    diagnostics.append({'server_full_text':False,'error':exc.as_dict()})
        # The same immutable value in duplicate old clones is one candidate per privacy scope.
        unique={}
        for hit in candidates.values():
            old=hit.get('_legacy')
            key=(old[0]['scope'],hit['source']['id'],hit['source']['version']) if old else (hit['source']['repo'],hit['source']['id'])
            if key not in unique or unique[key]['score']<hit['score']:unique[key]=hit
        results=sorted(unique.values(),key=lambda x:(-x['score'],x['source']['repo'],x['source']['id']))
        selected=results[offset:offset+limit]
        for hit in selected:
            item=hit.pop('_item',None)
            if item is not None:
                hit['source']=self.wb.ref(item)
                hit['accepted']=self.wb.accepted(item) if item['repo']!='local' else False
            old=hit.pop('_legacy',None)
            if old:hit['accepted']=self.legacy.accepted(*old)
        remote_complete=any(x.get('server_full_text') and not x.get('has_more') for x in diagnostics)
        incomplete = bool(self.legacy.errors) or (bool(missing) and not remote_complete) or any(not x.get('server_full_text',False) or x.get('has_more',False) for x in diagnostics)
        return {'queries':[' '.join(g) for g in groups],'original_queries':queries,'query_groups':groups,'method':'agent_expanded_lexical','semantic_vectors':False,
                'scope':scope,'hits':selected,'total_candidates':len(results),
                'next_offset':offset+limit if offset+limit<len(results) else None,
                'coverage':{**self.coverage(scope),'knowledge_text_requested':full_text,'missing_texts':missing[:10],
                            'missing_text_count':len(missing),'server':diagnostics,'limited':incomplete},
                'next_step':'Read selected sources, compare versions and follow bounded references; relevance needs the personal Agent.'}

    def read(self, value, repo=None, scope='all', file=None, offset=0, chars=8000, links_limit=10, offline=False, parent_chars=600):
        from .protocol import public
        from .thread_protocol import content_version, references
        if offset<0 or not 1<=chars<=32000 or not 0<=links_limit<=50 or not 0<=parent_chars<=1000:raise Error('INPUT_INVALID','Use nonnegative offset, chars 1–32000, links-limit 0–50 and parent-chars 0–1000.')
        identifier, _, version = value.partition('@')
        if '@' in value and not version:raise Error('INPUT_INVALID','A source version must follow @.')
        if identifier.startswith(('P-','T-','U-')) or repo in self.legacy.models:
            if file:raise Error('INPUT_INVALID','--file selects extracted knowledge text.')
            if repo is None:repo=self.legacy.find(identifier)[0]['repo']
            result=self.legacy.read(value,repo,scope,offset,chars,links_limit,parent_chars,
                                    lambda account:self.person(account,repo))
            return {**result,'coverage':self.coverage(scope),'next_step':'Review the original text, state and corrections before confirming relevance or reuse.'}
        if identifier.startswith('K-') or repo=='library':
            if not self.kb:raise Error('REFERENCE_UNAVAILABLE','A knowledge cache is not selected.')
            repo='library';doc=self.kb.doc(identifier,version or None);identifier=doc['meta']['id']
            current=self.kb.doc(identifier);source={'repo':repo,'id':identifier,'version':version or self.kb.data['heads'][identifier]}
            if content_version(doc)!=source['version']:raise Error('CHECKSUM_MISMATCH','Cached source does not match the requested version.')
            state={'status':current['meta']['status'],'resource_kind':doc['meta']['resource_kind']};accepted=True
        else:
            if not self.wb:raise Error('REFERENCE_UNAVAILABLE','A Thread workbench is not selected.')
            item=self.wb.find(identifier);identifier=item['doc']['meta']['id']
            if repo is not None and repo!=item['repo']:raise Error('REFERENCE_UNAVAILABLE','This record belongs to another source space.')
            repo=item['repo'];current=item['doc'];source=self.wb.ref(item)
            if version:source=dict(source,version=version)
            doc=self.wb.resolve(source);accepted=self.wb._ref_accepted(source) if repo!='local' else False
            _,parent_state=self.parent(item);state={k:parent_state[k] for k in ('work_state','lifecycle')}
        if not self.allowed(repo,scope):raise Error('PRIVATE_REFERENCE','This source is outside the requested scope.')
        text=doc['body'];location={'field':'body'}
        if file:
            if repo!='library':raise Error('INPUT_INVALID','--file selects extracted knowledge text.')
            entry=next((f for f in doc['meta']['files'] if f['name']==file),None)
            if not entry or not entry['text_sha256']:raise Error('CONTENT_NOT_CACHED','This file has no extracted text.')
            from .community import digest
            path=self.text_path(entry['text_sha256'])
            if path.is_file() and digest(path.read_bytes())!=entry['text_sha256']:raise Error('CHECKSUM_MISMATCH','Cached extracted text was changed.')
            if not path.is_file():
                if offline:raise Error('CONTENT_NOT_CACHED','Fetch this source text while connected.')
                first=self.kb.rpc({'method':'community_get','sha256':entry['text_sha256'],'offset':0})
                self.kb.download(entry['text_sha256'],first['size'])
            text=path.read_text();location={'field':'text','file':file,'sha256':entry['text_sha256']}
        incoming=[]
        for item,relation,target in self.wb._incoming.get((repo,identifier),[]):
            if item['repo']=='local' and repo!='local':continue
            if not self.allowed(item['repo'],scope):continue
            incoming.append({'source':self.wb.ref(item),'title':item['doc']['meta']['title'],'relation':relation,
                             'record_type':item['doc']['meta'].get('type'),'author':self.person(item['doc']['meta']['author'],item['repo']),
                             'target':target,'target_version_matches':target['version']==source['version']})
        incoming.sort(key=lambda x:(x['relation']!='supersedes',not x['target_version_matches'],x['source']['id']))
        outgoing=[ref for ref in references(doc['meta']) if self.allowed(ref['repo'],scope) and not (repo!='local' and ref['repo']=='local')]
        metadata=public(doc);metadata.pop('body',None)
        omitted=[key for key in ('refs','work') if key in metadata]
        for key in omitted:metadata.pop(key)
        current_source={'repo':repo,'id':identifier,'version':content_version(current)}
        parent_info=None
        if repo!='library':
            parent,_=self.parent(self.wb.find(identifier))
            if parent['doc']['meta']['id']!=identifier:
                parent_info={'source':self.wb.ref(parent),'title':parent['doc']['meta']['title'],
                             'overview':parent['doc']['body'][:parent_chars],'overview_truncated':len(parent['doc']['body'])>parent_chars}
        return {'source':source,'current_source':current_source,'is_current':source==current_source,'accepted':accepted,
                'state':state,'document':metadata,'link_fields_in_outgoing':omitted,'thread':parent_info,'author':self.person(doc['meta']['author'],repo),
                'text':text[offset:offset+chars],'location':{**location,'offset':offset},
                'truncated':offset+chars<len(text),'next_offset':offset+chars if offset+chars<len(text) else None,
                'outgoing':outgoing[:links_limit],'incoming':incoming[:links_limit],
                'links_total':{'outgoing':len(outgoing),'incoming':len(incoming)},'links_version_scope':'all_with_explicit_targets',
                'source_version_matches':content_version(doc)==source['version'],'coverage':self.coverage(scope),
                'next_step':'The personal Agent must review the original text, state and corrections before confirming relevance or reuse.'}


@networked
def execute(args):
    import json
    from contextlib import nullcontext
    from .legacy_search import LegacySources, default_roots, source_repo
    from .store import Store
    from .threads import Workbench, workspace_root
    if any(getattr(args,k,None) for k in ('dry_run','revision','expect_revision')):
        raise Error('INPUT_INVALID','Search is read-only; source versions belong on search read ID@VERSION.')
    offline=getattr(args,'offline',False);fresh=getattr(args,'require_fresh',False)
    if offline and fresh:raise Error('INPUT_INVALID','Offline search cannot request fresh sources.')
    if args.scope=='base' and fresh:raise Error('INPUT_INVALID','Private Base search is local; omit --require-fresh.')
    explicit=getattr(args,'root',None);root=workspace_root(explicit);store=Store(root)
    config_path=store.path('.cosmesh/config.json')
    schema=json.loads(config_path.read_text()).get('schema_version') if config_path.is_file() else None
    if explicit and schema is None:raise Error('NOT_INITIALIZED','Select an existing Thread workbench or old Base/Mesh store.')
    roots=list(args.legacy_root)
    if args.no_legacy and (roots or schema==1):raise Error('INPUT_INVALID','Do not combine --no-legacy with an old store.')
    if not args.no_legacy:
        roots=([root]+roots) if schema==1 else (roots or default_roots())
    if args.search_command=='query' and args.kind and not {'thread','record'}.intersection(args.kind):
        roots=[]
    if args.search_command=='read':
        old=[]
        for value in args.id:
            repo,key=value.split('/',1) if '/' in value else (args.repo,value)
            if key.startswith(('P-','T-','U-')) or (repo and repo.startswith('legacy-')):old.append(repo)
        if not old:roots=[]
        elif all(old):roots=[p for p in roots if source_repo(p) in old]
    wb=Workbench(root) if schema==2 else None
    if schema not in (None,1,2):raise Error('SCHEMA_UNSUPPORTED','Unsupported search source schema.')
    with wb.base.locked() if wb else nullcontext():
        if wb:wb.load()
        legacy=LegacySources(roots,args.scope)
        if schema==1 and legacy.errors:raise Error('SOURCE_UNAVAILABLE','The selected old source could not be read.',str(legacy.coverage(args.scope)['errors']))
        if fresh:
            if wb:
                result=wb.synchronize(publish=False)
                if result['errors']:raise Error('FRESHNESS_UNAVAILABLE','Some shared sources could not be refreshed; cached content is retained.')
            legacy.verify_fresh()
            if legacy.errors:raise Error('FRESHNESS_UNAVAILABLE','An old source is unavailable.')
        search=Search(wb,legacy)
        if args.search_command=='read':
            if len(args.id)>5 or (len(args.id)>1 and args.file):raise Error('INPUT_INVALID','Read at most five sources per batch; --file requires one source.')
            values=[];seen_parents=set()
            for value in args.id:
                repo,identifier=value.split('/',1) if '/' in value else (args.repo,value)
                try:
                    value=search.read(identifier,repo,args.scope,args.file,args.offset,min(args.chars,32000//len(args.id)),args.links_limit,offline,args.parent_chars)
                    parent=value.get('thread')
                    if parent:
                        key=tuple(parent['source'].values())
                        if key in seen_parents:
                            parent.update(overview='',overview_omitted='already_in_batch')
                        seen_parents.add(key)
                    values.append(value)
                except Error as exc:
                    if len(args.id)==1:raise
                    values.append({'requested':value,'error':exc.as_dict()})
            result=values[0] if len(values)==1 else {'sources':values,'complete':all('error' not in x for x in values),'coverage':search.coverage(args.scope)}
        else:
            result=search.query([args.query,*args.variants],args.scope,args.kind,args.limit,args.offset,
                                args.full_text,offline,args.include_inactive,args.author,args.after,args.before)
        return result,None
