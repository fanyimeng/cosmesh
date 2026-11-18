"""Private delivery receipts pointing to ordinary, owner-authored Thread records."""
import json
import sqlite3
from contextlib import contextmanager

from .errors import Error
from .personal_protocol import digest,packed,private_dir,read_json,save_json
from .protocol import decode
from .thread_protocol import content_version,identifier
from .thread_access import Access,read_manifest
from .thread_mail import cursor
from .threads import now

FORMAT='thread.messages.v1'
KINDS={'message','request','reply','result'}


class Messages:
    def __init__(self,hub,account):
        self.hub=hub;self.access=Access(hub,account);self.person=self.access.person
        self.root=private_dir(hub.root/'thread-messages-v1')
        self._catalog=None;self._people=None;self._sources={}

    @contextmanager
    def db(self):
        with self.hub.locked('thread-access-v1'):
            path=self.root/'messages.sqlite3'
            db=sqlite3.connect(str(path),timeout=15);db.row_factory=sqlite3.Row;path.chmod(0o600)
            db.executescript('''PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,thread TEXT,owner TEXT,
              snapshot TEXT,record TEXT,version TEXT,sender TEXT,recipients TEXT,kind TEXT,reply TEXT,created TEXT,historical INTEGER);
            CREATE TABLE IF NOT EXISTS seen(person TEXT,message TEXT,value INTEGER,at TEXT,PRIMARY KEY(person,message));
            CREATE TABLE IF NOT EXISTS operations(person TEXT,key TEXT,fingerprint TEXT,message TEXT,PRIMARY KEY(person,key));
            CREATE TABLE IF NOT EXISTS trackers(person TEXT,peer TEXT,thread TEXT,PRIMARY KEY(person,peer));
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);''')
            try:
                db.execute('BEGIN IMMEDIATE');yield db;db.commit()
            except Exception:db.rollback();raise
            finally:db.close()

    def catalog(self):
        if self._catalog is None:self._catalog=read_json(self.hub.root/'thread-access-v1/catalog.json',{'threads':{}})['threads']
        return self._catalog

    def people(self):
        if self._people is None:
            with self.access.community.db() as db:self._people={r['id']:dict(r) for r in db.execute('SELECT * FROM users')}
        return self._people

    def readable(self,row,person):
        return bool(row and (row['owner_id']==person or row['visibility']=='public' or row['visibility']=='restricted' and person in row['readers']))

    def visible(self,event,who=None):
        who=who or self.person['id']
        return who in [event['sender'],*json.loads(event['recipients'])] and self.readable(self.catalog().get(event['thread']),who)

    def event(self,db,mid):
        row=db.execute('SELECT * FROM events WHERE id=?',(mid,)).fetchone()
        if not row or not self.visible(row):raise Error('NOT_FOUND','This message is unavailable in your mailbox.')
        return row

    def preflight(self,db,thread,recipients,reply):
        if reply is not None and not isinstance(reply,str):raise Error('INPUT_INVALID','Use a message ID for replies.')
        row=self.catalog().get(identifier(thread,'W'))
        if not row or row['owner_id']!=self.person['id']:raise Error('PRIVATE_THREAD_NOT_SENT','尚未发送：请选择本人 Thread，并明确设置接收者的查看权限。')
        if not isinstance(recipients,list) or not 1<=len(recipients)<=100 or any(not isinstance(x,str) for x in recipients):raise Error('INPUT_INVALID','Choose 1–100 recipients.')
        with self.access.community.db() as users:
            targets=[self.access.community.resolve_user(users,v) for v in recipients]
        targets={t['id']:t for t in targets if t['id']!=self.person['id']}
        if not targets:raise Error('INPUT_INVALID','Choose another user as recipient.')
        if any(t['status']!='active' or not self.readable(row,pid) for pid,t in targets.items()):
            raise Error('PRIVATE_THREAD_NOT_SENT','尚未发送：部分接收者无权查看来源 Thread；先明确授权，消息不会自动公开。')
        if reply:
            parent=self.event(db,reply)
            participants={parent['sender'],*json.loads(parent['recipients'])}
            if not set(targets)<=participants:raise Error('AUTH_DENIED','A reply stays within the original participants; send separately to a new audience.')
            if not all(self.readable(self.catalog().get(parent['thread']),pid) for pid in targets):
                raise Error('REFERENCE_UNAVAILABLE','A reply recipient no longer has access to its original source.')
        return row,sorted(targets)

    def source(self,event):
        key=(event['owner'],event['snapshot'])
        if key not in self._sources:
            if len(self._sources)>=2:self._sources.pop(next(iter(self._sources)))
            self._sources[key]=read_manifest(self.hub,{'owner_account':event['owner'],'sha':event['snapshot'],'history':[event['snapshot']]})[0]
        value=self._sources[key]
        text=value['history'].get(event['record']+'@'+event['version'])
        if text is None:raise Error('SOURCE_PENDING','The pinned message record is unavailable.')
        return decode(text)

    def present(self,db,event,body=False,offset=0,chars=8000):
        doc=self.source(event);users=self.people();targets=json.loads(event['recipients']);text=doc['body']
        seen={r['person']:bool(r['value']) for r in db.execute('SELECT * FROM seen WHERE message=?',(event['id'],))}
        own=event['sender']==self.person['id']
        result={'id':event['id'],'sequence':event['seq'],'thread':event['thread'],'sender':event['sender'],
                'sender_name':users.get(event['sender'],{}).get('name'),'sender_account':users.get(event['sender'],{}).get('account'),'recipients':targets,
                'recipient_names':[users.get(t,{}).get('name') for t in targets],
                'kind':event['kind'],'reply_to':event['reply'],'created_at':event['created'],
                'title':doc['meta']['title'][:300],'excerpt':text[:400],
                'source':{'thread':event['thread'],'record':event['record'],'version':event['version'],'snapshot':event['snapshot']},
                'accepted':True,'delivery_state':'accepted','historical':bool(event['historical']),
                'unread':False if own else not seen.get(self.person['id'],bool(event['historical']))}
        if own:result['read_by']={t:seen.get(t,None if event['historical'] else False) for t in targets}
        if body:result.update(body=text[offset:offset+chars],next_offset=offset+chars if offset+chars<len(text) else None)
        return result

    def dispatch(self,p):
        method=p.get('method');schemas={
            'thread_message_preflight':{'method','thread','recipients','reply'},
            'thread_message_search':{'method','query','thread','peer','offset','limit'},
            'thread_message_tracker':{'method','message','preferred','title','create'},
            'thread_message_send':{'method','thread','recipients','reply','snapshot','record','version','kind','key'},
            'thread_message_list':{'method','folder','limit','before','unread','peer','thread','kind'},
            'thread_message_read':{'method','id','offset','chars'},
            'thread_message_mark':{'method','ids','seen'}}
        if method not in schemas or set(p)!=schemas[method]:raise Error('INPUT_INVALID','Invalid message request.')
        with self.db() as db:
            self.import_legacy(db)
            if method=='thread_message_search':return self.search(db,p)
            if method=='thread_message_tracker':return self.tracker(db,p)
            if method=='thread_message_preflight':
                _,targets=self.preflight(db,p['thread'],p['recipients'],p['reply'])
                return {'allowed':True,'recipients':targets,'sender':self.person['id']}
            if method=='thread_message_send':
                if not isinstance(p['key'],str) or not 1<=len(p['key'])<=200 or not isinstance(p['kind'],str) or p['kind'] not in KINDS:raise Error('INPUT_INVALID','Use a stable operation key and supported message kind.')
                fingerprint=digest(packed({k:v for k,v in p.items() if k not in {'method','key'}}).encode())
                prior=db.execute('SELECT * FROM operations WHERE person=? AND key=?',(self.person['id'],p['key'])).fetchone()
                if prior:
                    if prior['fingerprint']!=fingerprint:raise Error('IDEMPOTENCY_CONFLICT','This send key belongs to different content.')
                    return {'id':prior['message'],'accepted':True,'sent':True,'replayed':True}
                row,targets=self.preflight(db,p['thread'],p['recipients'],p['reply'])
                from .personal_relay import Relay
                relay=Relay(self.hub,self.person['account'])
                relay.dispatch({'method':'personal_thread_head','thread':p['thread']})
                with self.hub.locked('personal-'+relay.owner[:32]):
                    history=sqlite3.connect(str(relay.root/'relay.sqlite3'))
                    try:accepted=history.execute('SELECT 1 FROM thread_history WHERE thread=? AND sha=?',(p['thread'],p['snapshot'])).fetchone()
                    finally:history.close()
                if not accepted:raise Error('STATE_CONFLICT','Send a previously accepted personal Thread snapshot.')
                from .personal_protocol import check_id,HASH
                check_id(p['version'],HASH);check_id(p['snapshot'],HASH)
                event=dict(id='M-'+digest((self.person['id']+':'+p['key']).encode()),thread=p['thread'],owner=self.person['account'],
                           snapshot=p['snapshot'],record=identifier(p['record']),version=p['version'],sender=self.person['id'],
                           recipients=packed(targets),kind=p['kind'],reply=p['reply'],created=now(),historical=0)
                doc=self.source(event)
                if doc['meta']['kind']!='entry' or doc['meta']['thread']!=p['thread']:raise Error('POLICY_DENIED','Send an ordinary record belonging to the selected Thread.')
                self.reserve_message(row,p['thread'],doc)
                db.execute('INSERT INTO events(id,thread,owner,snapshot,record,version,sender,recipients,kind,reply,created,historical) VALUES(:id,:thread,:owner,:snapshot,:record,:version,:sender,:recipients,:kind,:reply,:created,:historical)',event)
                db.execute('INSERT INTO operations VALUES(?,?,?,?)',(self.person['id'],p['key'],fingerprint,event['id']))
                return {'id':event['id'],'accepted':True,'sent':True,'replayed':False,'recipients':targets,'source_record':p['record']}
            if method=='thread_message_mark':
                if type(p['seen'])is not bool or not isinstance(p['ids'],list) or not 1<=len(p['ids'])<=100:raise Error('INPUT_INVALID','Mark 1–100 messages explicitly.')
                rows=[self.event(db,mid) for mid in p['ids']]
                if any(self.person['id'] not in json.loads(e['recipients']) for e in rows):raise Error('AUTH_DENIED','Only recipients mark their own received messages.')
                for e in rows:db.execute('INSERT OR REPLACE INTO seen VALUES(?,?,?,?)',(self.person['id'],e['id'],int(p['seen']),now()))
                return {'marked':p['ids'],'seen':p['seen']}
            if method=='thread_message_read':
                if type(p['offset'])is not int or p['offset']<0 or type(p['chars'])is not int or not 1<=p['chars']<=32000:raise Error('INPUT_INVALID','Use bounded text pagination.')
                return self.present(db,self.event(db,p['id']),True,p['offset'],p['chars'])
            return self.list(db,p)

    def reserve_message(self,row,tid,doc):
        from .protocol import encode
        from .thread_access import LIMITS
        key='message:'+content_version(doc)
        if key in row['objects']:return
        size=len(encode(doc).encode())
        with self.access.community.db() as db:current,retained=self.access.community.usage(db,tid)
        direct=row.get('direct_current',row['usage']['current'])
        usage=dict(current=direct+size+sum(current.values()),
                   retained=sum({**row['objects'],key:size,**retained}.values()))
        if usage['current']>LIMITS['current'] or usage['retained']>LIMITS['retained']:
            raise Error('STORAGE_QUOTA','Message records also count toward the source Thread capacity; existing data is retained.')
        row['objects'][key]=size;row['direct_current']=direct+size;row['usage']=usage
        path=self.hub.root/'thread-access-v1/catalog.json';data=read_json(path);data['threads'][tid]=row
        # Conservative reservation survives interruption before the receipt transaction commits.
        save_json(path,data)

    def search(self,db,p):
        from .thread_search import Index
        peer=None
        if p['peer']:
            with self.access.community.db() as users:peer=self.access.community.resolve_user(users,p['peer'])['id']
        allowed={};events={};index=Index(self.hub);updated=0
        with index.open() as (search,fts):
            for e in db.execute('SELECT * FROM events ORDER BY seq'):
                if not self.visible(e) or p['thread'] and p['thread']!=e['thread']:continue
                if peer and peer not in [e['sender'],*json.loads(e['recipients'])]:continue
                scope='message:'+e['id'];events[scope]=e;allowed[scope]=e['snapshot']
                updated+=index.refresh(search,fts,scope,e['thread'],e['snapshot'],lambda e=e:[self.source(e)])
            result=index.search(search,fts,p['query'],allowed,p['offset'],p['limit'])
            for hit in result['hits']:
                e=events[hit['scope']];hit.update(message=e['id'],sender=e['sender'],kind=e['kind'],reply_to=e['reply'])
            result['indexed_changes']=updated
            return result

    def tracker(self,db,p):
        from uuid import UUID
        from .protocol import encode
        from .personal_relay import Relay
        from .thread_personal_protocol import empty
        from .thread_migration import put
        from .thread_tracking import MARKER
        event=self.event(db,p['message']);peer=event['sender'];owner=self.person['id']
        if owner not in json.loads(event['recipients']):raise Error('AUTH_DENIED','Only received messages create a private person Thread.')
        if type(p['create'])is not bool:raise Error('INPUT_INVALID','Invalid tracking action.')
        existing=db.execute('SELECT thread FROM trackers WHERE person=? AND peer=?',(owner,peer)).fetchone()
        if existing:return {'thread':existing['thread'],'created':False}
        if not p['create']:return {'thread':None,'created':False}
        if p['preferred']:
            tid=identifier(p['preferred'],'W')
        else:
            if not isinstance(p['title'],str) or not p['title'].strip() or len(p['title'])>200:raise Error('INPUT_INVALID','Use a complete person Thread title.')
            # Stable owner/person key prevents duplicate IDs even after an interrupted registry write.
            hub_id=read_json(self.hub.root/'thread-access-v1/catalog.json')['hub_id']
            tid='W-'+str(UUID(hex=digest((hub_id+':'+owner+':'+peer).encode())[:32],version=4))
            relay=Relay(self.hub,self.person['account']);head=relay.dispatch({'method':'personal_thread_head','thread':tid})['sha']
            if not head:
                person=self.people()[peer];timestamp=now();rid='E-'+str(UUID(hex=digest((tid+':tracking').encode())[:32],version=4))
                base=dict(schema_version=2,author=self.person['account'],created_at=timestamp)
                docs=[{'meta':dict(base,kind='thread',id=tid,title=p['title']),'body':'接收此人的消息，追踪对方定向可见的工作；我的笔记保存在这里。'},
                      {'meta':dict(base,kind='entry',id=rid,thread=tid,type='note',title='Tracking connection'),
                       'body':MARKER+packed(dict(hub_id=hub_id,person_id=peer,account=person['account']))+' -->'}]
                value=empty(tid)
                for doc in docs:
                    text=encode(doc);value['documents']['documents/'+doc['meta']['id']+'.md']=text
                    value['history'][doc['meta']['id']+'@'+content_version(doc)]=text
                sha=put(relay,packed(value).encode())
                relay.dispatch({'method':'personal_thread_commit','thread':tid,'sha':sha,'expect':None})
        db.execute('INSERT INTO trackers VALUES(?,?,?)',(owner,peer,tid))
        return {'thread':tid,'created':not bool(p['preferred'])}

    def import_legacy(self,db):
        if db.execute("SELECT 1 FROM metadata WHERE key='legacy_import'").fetchone():return
        from .thread_mail import digest as old_digest
        records={};sources={}
        for tid,row in self.catalog().items():
            if not row.get('migrated'):continue
            sha=row['history'][0];value,_=read_manifest(self.hub,row,sha)
            for text in value['documents'].values():
                doc=decode(text);records[doc['meta']['id']]=doc;sources[doc['meta']['id']]=(row,sha)
        def targets(doc,seen=None):
            seen=set() if seen is None else seen;m=doc['meta']
            if m['id'] in seen:return set()
            seen.add(m['id'])
            if m.get('type')=='request':return {m['to']}-{m['author']}
            if m.get('request'):
                q=records.get(m['request']['id'])
                return {q['meta']['author'],q['meta'].get('to')}-{m['author'],None} if q else set()
            if m.get('supersedes'):
                q=records.get(m['supersedes']['id'])
                return (targets(q,seen)|{q['meta']['author']})-{m['author']} if q else set()
            return set()
        candidates={}
        for rid,doc in records.items():
            if doc['meta']['kind']!='entry':continue
            recipients=targets(doc)
            if not recipients:continue
            row,sha=sources[rid];source={'repo':row['migrated']['repo'],'id':rid,'version':content_version(doc)}
            candidates['M-'+old_digest(source)]={'source':source,'sender':doc['meta']['author'],'recipients':sorted(recipients),'created_at':doc['meta']['created_at']}
        legacy=self.hub.root/'mail/deliveries.json'
        if legacy.is_file():candidates.update(json.loads(legacy.read_text()).get('events',{}))
        count=0
        for mid,event in sorted(candidates.items(),key=lambda item:(item[1].get('created_at',''),item[0])):
            ref=event.get('source',{});rid=ref.get('id')
            if rid not in sources:continue
            row,sha=sources[rid];value,_=read_manifest(self.hub,row,sha)
            text=value['history'].get(rid+'@'+ref.get('version',''))
            if text is None:continue
            doc=decode(text);m=doc['meta']
            with self.access.community.db() as users:
                sender=self.access.community.resolve_user(users,event['sender'])['id']
                recipients={account:self.access.community.resolve_user(users,account)['id'] for account in event['recipients'] if account!=event['sender']}
            if not recipients:continue
            parent=m.get('request') or m.get('supersedes')
            kind='request' if m['type']=='request' else 'result' if m['type'] in {'result','deliver'} else 'reply'
            db.execute('INSERT OR IGNORE INTO events(id,thread,owner,snapshot,record,version,sender,recipients,kind,reply,created,historical) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)',
                       (mid,m['thread'],row['owner_account'],sha,rid,ref['version'],sender,packed(sorted(recipients.values())),kind,'M-'+old_digest(parent) if parent else None,event['created_at']))
            for account,pid in recipients.items():
                seen=self.hub.root/('mail/seen-'+old_digest(account)+'.json')
                saved=json.loads(seen.read_text()).get('states',{}) if seen.is_file() else {}
                if mid in saved:db.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?,?)',(pid,mid,int(saved[mid]),now()))
            count+=1
        db.execute("INSERT INTO metadata VALUES('legacy_import',?)",(str(count),))

    def list(self,db,p):
        import base64
        folder=p['folder'];limit=p['limit']
        if folder not in {'inbox','outbox','chat'} or type(limit)is not int or not 1<=limit<=100 or type(p['unread'])is not bool or p['kind'] not in (None,*KINDS):raise Error('INPUT_INVALID','Invalid mailbox filters.')
        peer=None
        if p['peer']:
            with self.access.community.db() as users:peer=self.access.community.resolve_user(users,p['peer'])['id']
        query=digest(packed([self.person['id'],folder,peer,p['thread'],p['unread'],p['kind']]).encode())
        token=cursor(p['before']) if p['before'] else None
        if token and token['query']!=query:raise Error('VIEW_EXPIRED','Continue the same mailbox and filters.')
        ceiling=token['ceiling'] if token else db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        bound=token['before'] if token else ceiling+1;selected=[];more=False
        for e in db.execute('SELECT * FROM events WHERE seq<=? AND seq<? ORDER BY seq DESC',(ceiling,bound)):
            own=e['sender']==self.person['id']
            if not self.visible(e) or folder=='inbox' and own or folder=='outbox' and not own:continue
            if peer and peer not in [e['sender'],*json.loads(e['recipients'])]:continue
            if p['thread'] and e['thread']!=p['thread'] or p['kind'] and e['kind']!=p['kind']:continue
            result=self.present(db,e)
            if p['unread'] and not result['unread']:continue
            if len(selected)==limit:more=True;break
            selected.append(result)
        nxt=base64.urlsafe_b64encode(packed({'query':query,'ceiling':ceiling,'before':selected[-1]['sequence']}).encode()).decode().rstrip('=') if more else None
        return {'format':FORMAT,'messages':selected,'next_cursor':nxt,'server_current':True,'marks_read':False,'folder':folder}
