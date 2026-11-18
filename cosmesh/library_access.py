"""Library provenance inherits current Thread access, including all old versions."""
import base64
import json
import os
from pathlib import Path

from .community import Community, packed, digest
from .errors import Error
from .personal_protocol import read_json
from .knowledge_protocol import CHUNK


class ScopedCommunity(Community):
    def __init__(self,root,admin,hub):
        super().__init__(root,admin);self.hub=hub

    def access(self):
        return read_json(self.hub.root/'thread-access-v1/catalog.json',{'threads':{}})['threads']

    def provenance(self,db):
        scopes={r['id']:{'sources':set(json.loads(r['sources'])),'independent':bool(r['independent'])} for r in db.execute('SELECT * FROM resource_scopes')}
        links={}
        for r in db.execute('SELECT * FROM resource_links'):links.setdefault(r['id'],set()).add(r['target'])
        records=read_json(self.hub.root/'thread-access-v1/legacy-records.json',{'records':{}})['records']
        for row in db.execute('SELECT id,doc FROM versions'):
            item=scopes.setdefault(row['id'],{'sources':set(),'independent':False})
            for ref in json.loads(row['doc'])['meta']['refs']:
                if ref['repo']=='library':links.setdefault(row['id'],set()).add(ref['id'])
                else:
                    tid=ref['id'] if ref['id'].startswith('W-') else records.get(ref['repo']+'@'+ref['id'])
                    if tid:item['sources'].add(tid)
        return scopes,links

    def visible(self,db,user):
        threads=self.access();scopes,links=self.provenance(db)
        def readable(tid):
            r=threads.get(tid)
            return bool(r and (r['owner_id']==user['id'] or r['visibility']=='public' or
                              r['visibility']=='restricted' and user['id'] in r['readers']))
        def allowed(kid,seen):
            if kid in seen:return False
            item=scopes.get(kid)
            if item is None:return False
            if item['independent']:return True
            return all(readable(t) for t in item['sources']) and all(allowed(k,seen|{kid}) for k in links.get(kid,[]))
        return {kid for kid in scopes if allowed(kid,set())}

    def source_sets(self,db):
        scopes,links=self.provenance(db)
        def resolve(kid,seen):
            if kid in seen:return set()
            item=scopes.get(kid,{'sources':set(),'independent':False})
            if item['independent']:return set()
            result=set(item['sources'])
            for ref in links.get(kid,[]):result.update(resolve(ref,seen|{kid}))
            return result
        return {kid:resolve(kid,set()) for kid in scopes}

    def usage(self,db,tid):
        sources=self.source_sets(db);current={};retained={}
        heads={r['id']:r['version'] for r in db.execute('SELECT * FROM heads')}
        for r in db.execute('SELECT * FROM versions'):
            if tid not in sources.get(r['id'],set()):continue
            objects={'metadata:'+r['version']:len(r['doc'].encode())}
            for f in json.loads(r['doc'])['meta']['files']:
                objects[f['sha256']]=f['size']
                if f['text_sha256']:objects[f['text_sha256']]=self.blob(f['text_sha256']).stat().st_size
            retained.update(objects)
            if heads.get(r['id'])==r['version']:current.update(objects)
        return current,retained

    def epoch(self,db):
        return digest(packed({'access':self.access(),'scopes':[tuple(r) for r in db.execute('SELECT * FROM resource_scopes ORDER BY id')],
                             'heads':[tuple(r) for r in db.execute('SELECT * FROM heads ORDER BY id')]}).encode())

    def snapshot(self,account):
        with self.db() as db:
            user=self.require(db,account);allowed=self.visible(db,user);epoch=self.epoch(db)
            versions=[{'version':r['version'],'doc':json.loads(r['doc'])} for r in db.execute('SELECT * FROM versions ORDER BY id,version') if r['id'] in allowed]
            threads=self.access()
            def readable_tid(t):
                r=threads.get(t)
                return bool(r and (r['owner_id']==user['id'] or r['visibility']=='public' or r['visibility']=='restricted' and user['id'] in r['readers']))
            mapping=read_json(self.hub.root/'thread-access-v1/legacy-records.json',{'records':{}})['records']
            contributions=[{**dict(r),'refs':json.loads(r['refs'])} for r in db.execute('SELECT * FROM contributions ORDER BY at,id') if readable_tid(mapping.get(r['repo']+'@'+r['id']))]
            value={'format':1,'access_scoped':True,'users':[dict(r) for r in db.execute('SELECT * FROM users ORDER BY account')],
                   'versions':versions,'heads':{r['id']:r['version'] for r in db.execute('SELECT * FROM heads') if r['id'] in allowed},
                   'events':[{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM events ORDER BY seq') if r['object'] in allowed or r['object'].startswith('user:')],
                   'contributions':contributions,'archives':[dict(r) for r in db.execute('SELECT * FROM archives') if r['asset'] in allowed and readable_tid(r['thread'])],
                   'archive_errors':[]}
            origins=self.source_sets(db)
            value['resource_access']={kid:{'source_threads':sorted(origins.get(kid,set())),'policy':'inherited' if origins.get(kid) else 'independent_public'} for kid in allowed}
            raw=packed(value).encode();sha=self.save_blob(raw)
            db.execute('INSERT OR REPLACE INTO scoped_snapshots VALUES(?,?,?)',(account,sha,epoch))
        return {'sha256':sha,'size':len(raw),'user':user,'format':1,'requires_real_name':True,'access_scoped':True}

    def blob_allowed(self,db,user,sha):
        snapshot=db.execute('SELECT epoch FROM scoped_snapshots WHERE account=? AND sha=?',(user['account'],sha)).fetchone()
        if snapshot and snapshot['epoch']==self.epoch(db):return True
        if db.execute('SELECT 1 FROM upload_owners WHERE account=? AND sha=?',(user['account'],sha)).fetchone():return True
        allowed=self.visible(db,user)
        return any(sha in (f['sha256'],f['text_sha256']) for r in db.execute('SELECT id,doc FROM versions') if r['id'] in allowed for f in json.loads(r['doc'])['meta']['files'])

    def rpc(self,payload,account,hub):
        method=payload['method']
        if method=='community_info':return self.snapshot(account)
        if method=='community_get':
            with self.db() as db:
                if not self.blob_allowed(db,self.require(db,account),payload['sha256']):raise Error('NOT_FOUND','This Library object is unavailable to this account.')
            return super().rpc(payload,account,hub)
        if method=='community_put':
            from .knowledge_protocol import MAX_FILE
            from .personal_protocol import sha_file, private_dir
            self.check(account,write=True)
            sha=payload['sha256'];target=self.blob(sha);size=payload['size'];offset=payload['offset']
            try:raw=base64.b64decode(payload['data'],validate=True)
            except (ValueError,TypeError):raise Error('INPUT_INVALID','Invalid upload chunk.')
            if type(size)is not int or not 0<=size<=MAX_FILE or type(offset)is not int or not 0<=offset<=size or len(raw)>CHUNK or offset+len(raw)>size:
                raise Error('INPUT_TOO_LARGE','Invalid upload bounds.')
            folder=private_dir(self.root/'scoped-uploads')
            temp=folder/(digest(account.encode())+'-'+sha)
            with temp.open('a+b') as handle:
                temp.chmod(0o600);handle.seek(0,2);length=handle.tell()
                if offset>length:raise Error('UPLOAD_INCOMPLETE','Retry from the last acknowledged chunk.')
                if offset<length:
                    handle.seek(offset)
                    if handle.read(len(raw))!=raw:raise Error('IDEMPOTENCY_CONFLICT','Upload retry changed content.')
                else:handle.write(raw);handle.flush();os.fsync(handle.fileno())
            complete=temp.stat().st_size==size
            if complete:
                if sha_file(temp)!=sha:raise Error('CHECKSUM_MISMATCH','Uploaded content differs from its checksum.')
                target.parent.mkdir(exist_ok=True,mode=0o700)
                if target.exists():temp.unlink()
                else:os.replace(temp,target)
                with self.db() as db:db.execute('INSERT OR IGNORE INTO upload_owners VALUES(?,?)',(account,sha))
            return {'complete':complete,'sha256':sha}
        if method=='community_apply':
            with self.db() as db:
                if not db.execute('SELECT 1 FROM upload_owners WHERE account=? AND sha=?',(account,payload['input'])).fetchone():
                    raise Error('AUTH_DENIED','Upload your own operation input before applying it.')
            return super().rpc(payload,account,hub)
        if method in {'community_search','community_search_batch'}:
            with self.db() as db:
                allowed=self.visible(db,self.require(db,account))
                if method=='community_search':queries=[payload.get('query','')];offset=payload.get('offset',0);limit=5
                else:queries=payload.get('queries',[]);offset=0;limit=payload.get('limit',20)
                if not isinstance(queries,list) or not 1<=len(queries)<=10 or any(not isinstance(q,str) or not q.strip() or len(q)>500 for q in queries) or type(offset)is not int or offset<0 or type(limit)is not int or not 1<=limit<=100:
                    raise Error('INPUT_INVALID','Use bounded queries and pagination.')
                matches=[]
                for r in db.execute("SELECT * FROM search_text WHERE status='published' ORDER BY id"):
                    if r['id'] not in allowed:continue
                    found=[i for i,q in enumerate(queries) if q.casefold() in r['content']]
                    if found:
                        pos=r['content'].find(queries[found[0]].casefold())
                        matches.append({'id':r['id'],'version':r['version'],'snippet':r['content'][max(0,pos-30):pos+90],'matched_queries':found})
                return {'hits':matches[offset:offset+limit],'total':len(matches),'next_offset':offset+limit if offset+limit<len(matches) else None,'has_more':offset+limit<len(matches),'candidate_limit':limit}
        return super().rpc(payload,account,hub)

    def _resource_action(self,db,user,operation,value,hub):
        v=dict(value);sources=v.pop('source_threads',None);independent=v.pop('independent_public',False)
        if type(independent)is not bool:raise Error('INPUT_INVALID','Invalid independent publication choice.')
        old=db.execute('SELECT * FROM resource_scopes WHERE id=?',(v['id'],)).fetchone()
        deps=set(json.loads(old['sources'])) if old else set()
        if sources is not None:
            if not isinstance(sources,list) or not sources or len(sources)>100 or any(not isinstance(t,str) for t in sources):raise Error('INPUT_INVALID','Choose source Threads.')
            deps.update(sources)
        for ref in v.get('refs',[]):
            if ref['repo']!='library':
                records=read_json(hub.root/'thread-access-v1/legacy-records.json',{'records':{}})['records']
                tid=ref['id'] if ref['id'].startswith('W-') else records.get(ref['repo']+'@'+ref['id'])
                if tid:deps.add(tid)
            elif ref['id'] not in self.visible(db,user):raise Error('NOT_FOUND','The referenced Library source is unavailable.')
        threads=self.access()
        for tid in deps:
            if tid not in threads or threads[tid]['owner_id']!=user['id']:
                raise Error('AUTH_DENIED','Only a source Thread owner can publish or change its Library provenance.')
        if operation=='resource_add' and not deps and not independent and not v.get('refs'):
            raise Error('SOURCE_REQUIRED','Choose --thread for inherited access, or explicitly --public for an independent resource.')
        for f in v.get('files',[]):
            for sha in (f['sha256'],f['text_sha256']):
                if sha and not self.blob_allowed(db,user,sha):raise Error('NOT_FOUND','Upload or obtain authorized access to each source file.')
        if independent:
            expanded=set(deps);known=self.source_sets(db)
            for ref in v.get('refs',[]):
                if ref['repo']=='library':expanded.update(known.get(ref['id'],set()))
            if any(t not in threads or threads[t]['owner_id']!=user['id'] for t in expanded):
                raise Error('AUTH_DENIED','Independent publication requires ownership of every inherited source.')
        result=super()._resource_action(db,user,operation,v,hub)
        # Every version shares current access. Revision cannot silently erase provenance.
        db.execute('INSERT OR REPLACE INTO resource_scopes VALUES(?,?,?)',(v['id'],packed(sorted(deps)),int(independent or bool(old and old['independent'] and sources is None))))
        from .transfer_policy import PUBLIC
        for tid in self.source_sets(db).get(v['id'],set()):
            current,retained=self.usage(db,tid);row=threads[tid]
            # Published manifests already account for direct Thread files.
            if sum(current.values())+row.get('direct_current',row['usage']['current'])>PUBLIC['current'] or sum({**row['objects'],**retained}.values())>PUBLIC['retained']:
                raise Error('STORAGE_QUOTA','Inherited Library files exceed the Thread capacity; the prior version is retained.')
        return result
