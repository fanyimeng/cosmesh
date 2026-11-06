"""Hub-owned real-name directory, knowledge versions and append-only contribution records."""
import base64
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .errors import Error
from .store import atomic, safe_path
from .knowledge_protocol import CHUNK, MAX_FILE, MAX_TOTAL, DIGEST, knowledge_id
from .threads import now


def packed(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(raw): return hashlib.sha256(raw).hexdigest()
def normalized(name): return unicodedata.normalize('NFKC',name).strip().casefold()


class Community:
    def __init__(self, root, admin):
        self.root=safe_path(root); self.admin=admin
        if self.root.is_dir() and (self.root.stat().st_uid != os.geteuid() or self.root.stat().st_mode & 0o077):
            raise Error('AUTH_DENIED','Knowledge storage must be private and owned by its Hub.')

    def initialize(self):
        if self.root.exists() and any(self.root.iterdir()) and not (self.root/'catalog.sqlite3').is_file():
            raise Error('STORE_EXISTS','Choose an empty dedicated knowledge directory; existing data is preserved.')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(self.root,0o700)
        for part in ('blobs','uploads'): (self.root/part).mkdir(exist_ok=True,mode=0o700)
        with self.db() as db:self.person(db,self.admin)
        return {'root':str(self.root),'requires_real_name':True,'format':1}

    @contextmanager
    def db(self):
        path=safe_path(self.root/'catalog.sqlite3')
        db=sqlite3.connect(str(path),timeout=15); db.row_factory=sqlite3.Row
        db.executescript('''PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,account TEXT UNIQUE,name TEXT,role TEXT,status TEXT,version TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,object TEXT,actor TEXT,person TEXT,action TEXT,at TEXT,payload TEXT,previous TEXT,hash TEXT);
        CREATE TABLE IF NOT EXISTS versions(id TEXT,version TEXT,doc TEXT,PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS heads(id TEXT PRIMARY KEY,version TEXT);
        CREATE TABLE IF NOT EXISTS operations(actor TEXT,key TEXT,fingerprint TEXT,result TEXT,PRIMARY KEY(actor,key));
        CREATE TABLE IF NOT EXISTS contributions(repo TEXT,id TEXT,version TEXT,person TEXT,account TEXT,kind TEXT,title TEXT,at TEXT,refs TEXT,PRIMARY KEY(repo,id,version));
        CREATE TABLE IF NOT EXISTS archives(repo TEXT,thread TEXT,closing TEXT,asset TEXT,version TEXT,PRIMARY KEY(repo,thread,closing));
        CREATE TABLE IF NOT EXISTS checkpoints(repo TEXT PRIMARY KEY,head TEXT);
        CREATE TABLE IF NOT EXISTS archive_errors(repo TEXT,thread TEXT,error TEXT,PRIMARY KEY(repo,thread));
        CREATE TABLE IF NOT EXISTS search_text(id TEXT PRIMARY KEY,version TEXT,content TEXT,status TEXT);
        ''')
        os.chmod(path,0o600)
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()

    def event(self,db,obj,user,action,payload):
        old=db.execute('SELECT hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        record={'id':'V-'+str(uuid4()),'object':obj,'actor':user['account'],'person':user['id'],
                'action':action,'at':now(),'payload':payload,'previous':old['hash'] if old else None}
        sha=digest(packed(record).encode())
        db.execute('INSERT INTO events(id,object,actor,person,action,at,payload,previous,hash) VALUES(?,?,?,?,?,?,?,?,?)',
                   (record['id'],obj,user['account'],user['id'],action,record['at'],packed(payload),record['previous'],sha))
        return sha

    def person(self,db,account):
        row=db.execute('SELECT * FROM users WHERE account=?',(account,)).fetchone()
        if row:return dict(row)
        user={'id':'person-'+str(uuid4()),'account':account,'name':None,'role':'admin' if account==self.admin else 'member',
              'status':'active','version':uuid4().hex,'created':now()}
        db.execute('INSERT INTO users VALUES(:id,:account,:name,:role,:status,:version,:created)',user)
        self.event(db,'user:'+user['id'],user,'user_registered',user)
        return user

    def require(self,db,account,write=False,admin=False):
        user=self.person(db,account)
        if user['status']!='active':raise Error('USER_DISABLED','This account is disabled in this Hub.')
        if admin and user['role']!='admin':raise Error('AUTH_DENIED','This operation requires a system maintainer.')
        if write and not user['name']:raise Error('REAL_NAME_REQUIRED','Fill in your own real name with user set-name before publishing shared work.')
        return user

    def check(self,account,write=False):
        with self.db() as db:return self.require(db,account,write)

    def resolve_user(self,db,value):
        rows=[dict(x) for x in db.execute('SELECT * FROM users ORDER BY account')]
        exact=[x for x in rows if value in (x['id'],x['account'])]
        matches=exact or [x for x in rows if x['name'] and normalized(x['name'])==normalized(value)]
        if len(matches)!=1:
            raise Error('USER_NOT_FOUND' if not matches else 'AMBIGUOUS_USER','Use one exact real name, account or stable user ID.',
                        json.dumps([{'id':x['id'],'account':x['account'],'name':x['name']} for x in matches],ensure_ascii=False))
        return matches[0]

    def blob(self,sha):
        if not isinstance(sha,str) or not DIGEST.fullmatch(sha):raise Error('INPUT_INVALID','Invalid content checksum.')
        return safe_path(self.root/'blobs'/sha[:2]/sha)

    def save_blob(self,raw):
        if len(raw)>MAX_TOTAL:raise Error('INPUT_TOO_LARGE','Internal archive or catalog payload exceeds 100 MiB.')
        sha=digest(raw);path=self.blob(sha)
        if not path.exists():
            path.parent.mkdir(exist_ok=True,mode=0o700)
            temporary=path.with_name('.'+uuid4().hex)
            with temporary.open('wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
            temporary.chmod(0o600);os.replace(temporary,path)
        return sha

    def snapshot(self,account):
        with self.db() as db:
            user=self.require(db,account)
            users=[dict(x) for x in db.execute('SELECT * FROM users ORDER BY account')]
            versions=[{'version':x['version'],'doc':json.loads(x['doc'])} for x in db.execute('SELECT * FROM versions ORDER BY id,version')]
            heads={x['id']:x['version'] for x in db.execute('SELECT * FROM heads')}
            events=[{**dict(x),'payload':json.loads(x['payload'])} for x in db.execute('SELECT * FROM events ORDER BY seq')]
            contributions=[{**dict(x),'refs':json.loads(x['refs'])} for x in db.execute('SELECT * FROM contributions ORDER BY at,id')]
            archives=[dict(x) for x in db.execute('SELECT * FROM archives ORDER BY repo,thread,closing')]
            archive_errors=[dict(x) for x in db.execute('SELECT * FROM archive_errors')]
            value={'format':1,'users':users,'versions':versions,'heads':heads,'events':events,'contributions':contributions,'archives':archives,'archive_errors':archive_errors}
        raw=packed(value).encode();sha=self.save_blob(raw)
        return {'sha256':sha,'size':len(raw),'user':user,'format':1,'requires_real_name':True}

    def resource(self,ref):
        with sqlite3.connect(str(self.root/'catalog.sqlite3')) as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT doc FROM versions WHERE id=? AND version=?',(ref['id'],ref['version'])).fetchone()
        if not row:raise Error('REFERENCE_UNAVAILABLE','Knowledge version has not been accepted.')
        return json.loads(row['doc'])

    def _store_resource(self,db,doc,user,action):
        from .protocol import encode,version
        raw=encode(doc);sha=version(raw);meta=doc['meta']
        db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?)',(meta['id'],sha,packed(doc)))
        db.execute('INSERT OR REPLACE INTO heads VALUES(?,?)',(meta['id'],sha))
        self.index_resource(db,doc,sha)
        self.event(db,meta['id'],user,action,{'version':sha,'previous':meta['previous'],'refs':meta['refs'],'generation':meta['generation']})
        return {'id':meta['id'],'version':sha,'accepted':True}

    def index_resource(self,db,doc,sha):
        meta=doc['meta'];searchable=meta['title']+'\n'+doc['body']+'\n'+packed(meta['bibliography'])
        for file in meta['files']:
            if file['text_sha256']:searchable+='\n'+self.blob(file['text_sha256']).read_text(encoding='utf-8',errors='replace')
        db.execute('INSERT OR REPLACE INTO search_text VALUES(?,?,?,?)',(meta['id'],sha,searchable.casefold(),meta['status']))

    def apply(self,account,operation,value,key,hub):
        if not isinstance(key,str) or not key or len(key)>200:raise Error('INPUT_INVALID','Provide a stable operation key.')
        fingerprint=digest(packed({'operation':operation,'value':value}).encode())
        with self.db() as db:
            user=self.require(db,account)
            old=db.execute('SELECT * FROM operations WHERE actor=? AND key=?',(user['id'],key)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise Error('IDEMPOTENCY_CONFLICT','This key already identifies different input.')
                return {**json.loads(old['result']),'replayed':True}
            if operation=='set_name':
                if set(value)-{'name','expect'}:raise Error('INPUT_INVALID','Only a real name and expected version may be changed.')
                name=unicodedata.normalize('NFKC',value.get('name','')).strip()
                if not 2<=len(name)<=80 or any(unicodedata.category(c).startswith('C') or c in '/\\<>@' for c in name):
                    raise Error('INPUT_INVALID','Provide your own real name, 2–80 characters.')
                if user['name'] and value.get('expect')!=user['version']:raise Error('STATE_CONFLICT','Read user me and pass --expect for a name change.')
                user.update(name=name,version=uuid4().hex)
                db.execute('UPDATE users SET name=?,version=? WHERE id=?',(name,user['version'],user['id']))
                self.event(db,'user:'+user['id'],user,'real_name_set',{'name':name,'version':user['version'],'identity_verification':'server_account','name_verification':'self_declared'})
                result=user
            elif operation=='manage_user':
                self.require(db,account,admin=True)
                target=self.resolve_user(db,value['user'])
                if value.get('expect')!=target['version']:raise Error('STATE_CONFLICT','User directory changed; refresh before changing it.')
                status=value.get('status',target['status']);role=value.get('role',target['role'])
                if status not in {'active','disabled'} or role not in {'member','admin'}:raise Error('INPUT_INVALID','Invalid user status or role.')
                if target['role']=='admin' and (status!='active' or role!='admin'):
                    count=db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0]
                    if count<=1:raise Error('POLICY_DENIED','Keep at least one active system maintainer.')
                target.update(status=status,role=role,version=uuid4().hex)
                db.execute('UPDATE users SET status=?,role=?,version=? WHERE id=?',(status,role,target['version'],target['id']))
                self.event(db,'user:'+target['id'],user,'user_managed',target);result=target
            elif operation in {'resource_add','resource_revise','resource_transfer'}:
                self.require(db,account,write=True)
                result=self._resource_action(db,user,operation,value,hub)
            else:raise Error('COMMAND_DENIED','Unknown community operation.')
            db.execute('INSERT INTO operations VALUES(?,?,?,?)',(user['id'],key,fingerprint,packed(result)))
            return result

    def _resource_action(self,db,user,operation,v,hub):
        from .knowledge_protocol import validate,attachment_reason,MAX_FILE
        from .thread_protocol import reference
        allowed={'id','expect','title','body','kind','status','files','bibliography','refs','generation','maintainer'}
        if set(v)-allowed:raise Error('INPUT_INVALID','Unknown knowledge fields.')
        knowledge_id(v['id']);previous=None;old=None
        if operation!='resource_add':
            row=db.execute('SELECT v.doc,h.version FROM heads h JOIN versions v ON h.id=v.id AND h.version=v.version WHERE h.id=?',(v['id'],)).fetchone()
            if not row:raise Error('NOT_FOUND','Knowledge record not found.')
            old=json.loads(row['doc']);previous=row['version']
            if old['meta']['resource_kind']=='archive':raise Error('HISTORY_PROTECTED','Thread archives are immutable system snapshots.')
            if old['meta']['maintainer_id']!=user['id'] and user['role']!='admin':raise Error('AUTH_DENIED','Only its maintainer can revise this resource.')
            if v.get('expect')!=previous:raise Error('STATE_CONFLICT','Knowledge version changed; preserve your input and refresh.')
        elif db.execute('SELECT 1 FROM heads WHERE id=?',(v['id'],)).fetchone():raise Error('ID_CONFLICT','Knowledge ID already exists.')
        if operation=='resource_transfer':
            doc=json.loads(packed(old));target=self.resolve_user(db,v['maintainer'])
            if target['status']!='active' or not target['name']:raise Error('REAL_NAME_REQUIRED','Choose an active maintainer with a real name.')
            doc['meta'].update(maintainer=target['account'],maintainer_id=target['id'],previous=previous)
            doc['meta']['generation']={'tool':'cosmesh.transfer','initiated_by':user['id']}
        else:
            files=v.get('files',old['meta']['files'] if old else [])
            for f in files:
                path=self.blob(f['sha256'])
                if not path.is_file() or path.stat().st_size!=f['size']:raise Error('SOURCE_PENDING','Upload all file content before publishing metadata.')
                if path.stat().st_size>MAX_FILE:raise Error('INPUT_TOO_LARGE','Choose a document up to 20 MiB.')
                reason=attachment_reason(f['name'],path.read_bytes())
                if reason:raise Error('INPUT_INVALID',f'Attachment {f["name"]} rejected: {reason}. Accepts UTF-8 text/source, PDF, Office and figures.')
                if f['text_sha256'] and not self.blob(f['text_sha256']).is_file():raise Error('SOURCE_PENDING','Extracted text is missing.')
            refs=v.get('refs',old['meta']['refs'] if old else [])
            for ref in refs:
                if ref.get('repo')=='local':raise Error('PRIVATE_REFERENCE','Shared knowledge can only cite accepted shared sources.')
                reference(ref);hub.resolve_shared_reference(ref)
            if v.get('kind',old['meta']['resource_kind'] if old else 'document') not in {'document','reference'}:raise Error('INPUT_INVALID','Use document or reference; archives are generated by the server.')
            generation=v.get('generation',{})
            if not isinstance(generation,dict) or set(generation)-{'tool','model','instruction','verification'} or any(not isinstance(x,str) or len(x)>8000 for x in generation.values()):
                raise Error('INPUT_INVALID','Generation accepts bounded tool, model and instruction text.')
            generation={**generation,'verification':'client_reported' if generation.get('tool') not in (None,'unspecified') else 'not_provided'}
            doc={'meta':{'schema_version':3,'kind':'resource','id':v['id'],'author':old['meta']['author'] if old else user['account'],
                'author_id':old['meta']['author_id'] if old else user['id'],'created_at':old['meta']['created_at'] if old else now(),
                'title':v.get('title',old['meta']['title'] if old else ''),'resource_kind':v.get('kind',old['meta']['resource_kind'] if old else 'document'),
                'maintainer':old['meta']['maintainer'] if old else user['account'],'maintainer_id':old['meta']['maintainer_id'] if old else user['id'],
                'previous':previous,'status':v.get('status',old['meta']['status'] if old else 'published'),'files':files,
                'bibliography':v.get('bibliography',old['meta']['bibliography'] if old else {}),'refs':refs,
                'generation':generation,'archive':None},'body':v.get('body',old['body'] if old else '')}
        validate(doc)
        return self._store_resource(db,doc,user,operation)

    def rpc(self,payload,account,hub):
        method=payload['method']
        if method=='community_info':
            self.check(account)
            self.reconcile(hub)
            return self.snapshot(account)
        if method=='community_get':
            self.check(account);path=self.blob(payload['sha256'])
            if not path.is_file():raise Error('NOT_FOUND','Document content is unavailable.')
            offset=payload.get('offset',0)
            if type(offset)is not int or offset<0:raise Error('INPUT_INVALID','Invalid content offset.')
            with path.open('rb') as handle:handle.seek(offset);raw=handle.read(CHUNK)
            return {'sha256':payload['sha256'],'size':path.stat().st_size,'offset':offset,'data':base64.b64encode(raw).decode()}
        if method=='community_search_batch':
            from .search import query_groups, score, excerpt
            queries=payload.get('queries');groups=query_groups(queries);limit=payload.get('limit',10)
            if type(limit)is not int or not 1<=limit<=50:raise Error('INPUT_INVALID','Use a search limit from 1 to 50.')
            clauses=['('+' AND '.join('instr(content,?)>0' for _ in terms)+')' for terms in groups]
            where="status='published' AND ("+' OR '.join(clauses)+')'
            parameters=[term for terms in groups for term in terms]
            # Group count ranks the bounded shortlist; the personal Agent reviews the sources.
            ranking='+'.join('CASE WHEN '+clause+' THEN 1 ELSE 0 END' for clause in clauses)
            with self.db() as db:
                self.require(db,account)
                total=db.execute('SELECT COUNT(*) FROM search_text WHERE '+where,parameters).fetchone()[0]
                rows=db.execute('SELECT id,version,content FROM search_text WHERE '+where+' ORDER BY ('+ranking+') DESC,id LIMIT ?',
                                parameters+parameters+[limit]).fetchall()
                hits=[]
                for row in rows:
                    _,matches=score([('text',row['content'],4)],groups)
                    part=excerpt(row['content'],[t for i in matches for t in groups[i]])
                    hits.append({'id':row['id'],'version':row['version'],'snippet':part['text'],'matched_queries':matches})
            return {'hits':hits,'total':total,'has_more':total>len(hits),'candidate_limit':limit}
        if method=='community_search':
            query=payload.get('query','').strip().casefold();offset=payload.get('offset',0)
            if not query or len(query)>500 or type(offset)is not int or offset<0:raise Error('INPUT_INVALID','Provide a short search query and valid offset.')
            with self.db() as db:
                self.require(db,account)
                total=db.execute("SELECT COUNT(*) FROM search_text WHERE status='published' AND instr(content,?)>0",(query,)).fetchone()[0]
                rows=db.execute("SELECT * FROM search_text WHERE status='published' AND instr(content,?)>0 ORDER BY id LIMIT 5 OFFSET ?",(query,offset)).fetchall()
                hits=[]
                for row in rows:
                    pos=row['content'].find(query)
                    hits.append({'id':row['id'],'version':row['version'],'snippet':row['content'][max(0,pos-30):pos+90]})
            return {'hits':hits,'total':total,'next_offset':offset+len(hits) if offset+len(hits)<total else None}
        if method=='community_reindex':
            with self.db() as db:
                user=self.require(db,account,admin=True)
                rows=db.execute('SELECT v.doc,v.version FROM heads h JOIN versions v ON v.id=h.id AND v.version=h.version').fetchall()
                db.execute('DELETE FROM search_text')
                for row in rows:self.index_resource(db,json.loads(row['doc']),row['version'])
                self.event(db,'library',user,'search_index_rebuilt',{'items':len(rows)})
            return {'rebuilt':len(rows),'source_versions_unchanged':True}
        if method=='community_put':
            self.check(account,write=True);sha=payload['sha256'];path=self.blob(sha)
            size=payload['size'];offset=payload['offset']
            try:raw=base64.b64decode(payload['data'],validate=True)
            except (ValueError,TypeError):raise Error('INPUT_INVALID','Invalid upload chunk.')
            if type(size)is not int or not 0<=size<=MAX_FILE or type(offset)is not int or not 0<=offset<=size or len(raw)>CHUNK or offset+len(raw)>size:
                raise Error('INPUT_TOO_LARGE','Invalid document upload bounds.')
            if path.exists():return {'complete':True,'sha256':sha}
            # Per-account temporary paths; final blobs are immutable content-addressed files.
            temp=safe_path(self.root/'uploads'/(digest(account.encode())+'-'+sha))
            import fcntl
            with temp.open('a+b') as handle:
                os.chmod(temp,0o600);fcntl.lockf(handle,fcntl.LOCK_EX)
                handle.seek(0,2);length=handle.tell()
                if offset>length:raise Error('UPLOAD_INCOMPLETE','Upload chunks in order; retry the original chunk.')
                if offset<length:
                    handle.seek(offset)
                    if handle.read(len(raw))!=raw:raise Error('IDEMPOTENCY_CONFLICT','A retried chunk has different content.')
                else:handle.write(raw);handle.flush();os.fsync(handle.fileno())
                complete=temp.stat().st_size==size
                if complete:
                    handle.seek(0)
                    if digest(handle.read())!=sha:raise Error('CHECKSUM_MISMATCH','Uploaded document checksum does not match.')
                    path.parent.mkdir(exist_ok=True,mode=0o700);os.replace(temp,path)
            return {'complete':complete,'sha256':sha}
        if method=='community_apply':
            path=self.blob(payload['input'])
            if not path.is_file() or path.stat().st_size>1024*1024:raise Error('INPUT_TOO_LARGE','Missing or oversized operation input.')
            value=json.loads(path.read_text())
            return self.apply(account,payload['operation'],value,payload['key'],hub)
        # Bootstrap name registration can happen before document uploads are permitted.
        if method=='community_name':return self.apply(account,'set_name',{'name':payload['name'],'expect':payload.get('expect')},payload['key'],hub)
        raise Error('COMMAND_DENIED','Unsupported knowledge operation.')

    def reconcile(self,hub):
        """Index every newly accepted version, then create immutable closing snapshots."""
        from .store import Store
        from .thread_protocol import content_version,references
        policies=json.loads((hub.root/'policy/repos.json').read_text())
        for repo,policy in sorted(policies.items()):
            if not policy.get('thread_format'):continue
            store=Store(hub.repo(repo).root)
            head=store.git('rev-parse','--verify','refs/heads/main',check=False)
            if head.returncode:continue
            revision=head.stdout.strip()
            with self.db() as db:
                checkpoint=db.execute('SELECT head FROM checkpoints WHERE repo=?',(repo,)).fetchone()
                old=checkpoint['head'] if checkpoint else None
                if old==revision and not db.execute('SELECT 1 FROM archive_errors WHERE repo=?',(repo,)).fetchone():continue
                commits=store.git('rev-list','--reverse',revision,*(['^'+old] if old else [])).splitlines()
                for commit in commits:
                    _,snapshot=store.snapshot(commit)
                    for doc in store.documents(snapshot).values():
                        m=doc['meta'];user=self.person(db,m['author']);sha=content_version(doc)
                        if m.get('author_id') and m['author_id']!=user['id']:
                            raise Error('AUTH_DENIED','Accepted record identity differs from the user directory.')
                        inserted=db.execute('INSERT OR IGNORE INTO contributions VALUES(?,?,?,?,?,?,?,?,?)',
                            (repo,m['id'],sha,user['id'],m['author'],m.get('type','thread'),m['title'],m['created_at'],packed(references(m)))).rowcount
                        if inserted:self.event(db,m['id'],user,'shared_record_observed',{'repo':repo,'version':sha,'type':m.get('type','thread'),'generation':m.get('generation',{})})
                    for path,text in snapshot.items():
                        if not path.startswith('.cosmesh/operations/'):continue
                        obj='operation:'+repo+'/'+path
                        if db.execute('SELECT 1 FROM events WHERE object=?',(obj,)).fetchone():continue
                        receipt=json.loads(text);user=self.person(db,receipt['actor'])
                        self.event(db,obj,user,'operation_recorded',{'result':receipt['result'],'operation':receipt.get('operation','legacy'),'generation':receipt.get('generation',{'verification':'not_provided'}),'source_revision':commit})
                db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?)',(repo,revision))
            _,files=store.snapshot(revision);docs=store.documents(files)
            for thread in [d for d in docs.values() if d['meta']['kind']=='thread']:
                records=sorted([d for d in docs.values() if d['meta'].get('thread')==thread['meta']['id']],key=lambda d:(d['meta']['created_at'],d['meta']['id']))
                def terminal(kind):
                    stream={d['meta'].get('previous'):d for d in records if d['meta']['type']==kind}
                    current=None;last=None
                    while current in stream:last=stream[current];current=last['meta']['id']
                    return last
                state=terminal('state');lifecycle=terminal('lifecycle')
                from .thread_protocol import thread_state
                placement = thread_state([d['meta'] for d in records])['lifecycle']
                close=lifecycle if lifecycle and placement in {'archived','withdrawn','merged'} else state
                closed=placement in {'archived','withdrawn','merged'}
                if not closed:continue
                refs=[{'repo':repo,'id':d['meta']['id'],'version':content_version(d)} for d in [thread]+records]
                signature=digest(packed(refs).encode());tid=thread['meta']['id']
                try:
                    with self.db() as db:
                        if db.execute('SELECT 1 FROM archives WHERE repo=? AND thread=? AND closing=?',(repo,tid,signature)).fetchone():continue
                        evidence=[]
                        for d in records:
                            for ref in references(d['meta']):
                                try:
                                    from .protocol import encode
                                    source=hub.resolve_shared_reference(ref)
                                    evidence.append({'source':ref,'document':source,'source_text':encode(source)})
                                except Error as exc:evidence.append({'source':ref,'error':exc.as_dict()})
                        archive={'format':1,'thread':thread,'records':records,'citations':evidence,'source_revision':revision,'closing':close['meta']['id'],
                                 'source_texts':{d['meta']['id']:files['documents/'+d['meta']['id']+'.md'] for d in [thread]+records},
                                 'contributors':[self.person(db,name) for name in sorted({d['meta']['author'] for d in [thread]+records})]}
                        raw=packed(archive).encode();blob=self.save_blob(raw)
                        prior=db.execute('SELECT asset FROM archives WHERE repo=? AND thread=? LIMIT 1',(repo,tid)).fetchone()
                        kid=prior['asset'] if prior else 'K-'+str(uuid4())
                        previous=db.execute('SELECT version FROM heads WHERE id=?',(kid,)).fetchone()
                        user=self.person(db,close['meta']['author']);owner=self.person(db,thread['meta']['author'])
                        doc={'meta':{'schema_version':3,'kind':'resource','id':kid,'author':owner['account'],'author_id':owner['id'],
                            'created_at':now(),'title':thread['meta']['title'],'resource_kind':'archive','maintainer':owner['account'],
                            'maintainer_id':owner['id'],'previous':previous['version'] if previous else None,'status':'published',
                            'files':[{'name':'thread-archive.json','sha256':blob,'size':len(raw),'text_sha256':blob,'text_status':'extracted','extractor':'cosmesh.archive'}],
                            'bibliography':{},'refs':refs,'generation':{'tool':'cosmesh.archive','initiated_by':user['id']},
                            'archive':{'repo':repo,'thread':tid,'closing':close['meta']['id'],'state':state['meta']['state'] if state else 'active','source_revision':revision}},'body':thread['body'][:8000]}
                        saved=self._store_resource(db,doc,user,'thread_archived')
                        db.execute('INSERT INTO archives VALUES(?,?,?,?,?)',(repo,tid,signature,kid,saved['version']))
                        db.execute('DELETE FROM archive_errors WHERE repo=? AND thread=?',(repo,tid))
                except Error as exc:
                    with self.db() as db:db.execute('INSERT OR REPLACE INTO archive_errors VALUES(?,?,?)',(repo,tid,packed(exc.as_dict())))
