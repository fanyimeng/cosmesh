"""Opt-in, kernel-identity-scoped private object relay. No public Git exposure."""
import base64
import json
import os
import sqlite3
import time

from .errors import Error
from .store import Store
from .personal_protocol import (CHUNK, DEFAULTS, HASH, MAX_METADATA, check_id, digest,
    integer, packed, private_dir, read_json, save_json, sha_file, validate_snapshot, content_reason)


def initialize(hub, limits=None):
    directory=private_dir(hub.root/'personal-v1')
    path=directory/'policy.json'
    existing=read_json(path)
    if existing is not None: return {'enabled':True,'limits':existing,'unchanged':True}
    policy=dict(DEFAULTS)
    if limits:
        if set(limits)-set(DEFAULTS): raise Error('INPUT_INVALID','Unknown personal transfer limit.')
        for key,value in limits.items(): policy[key]=integer(value,2**63-1,1)
    save_json(path,policy)
    if limits:save_json(directory/'policy-overrides.json',sorted(limits))
    return {'enabled':True,'limits':policy,'public':False}


class Relay:
    def __init__(self, hub, actor):
        self.hub=hub
        self.actor=actor
        self.policy=read_json(hub.root/'personal-v1/policy.json')
        if self.policy is None: raise Error('FEATURE_UNAVAILABLE','This Hub has not enabled personal relay v1.')
        from .transfer_policy import effective
        self.policy=effective(self.policy,read_json(hub.root/'personal-v1/policy-overrides.json',[]))
        self.owner=digest(actor.encode())
        self.root=private_dir(hub.root/'personal-v1'/self.owner)
        self.blobs=private_dir(self.root/'blobs')
        self.parts=private_dir(self.root/'parts')

    def dispatch(self, payload):
        allowed={
            'personal_info':{'method'}, 'personal_list':{'method'},
            'personal_head':{'method','workspace'},
            'personal_begin':{'method','sha','size'},
            'personal_put':{'method','sha','offset','data'},
            'personal_get':{'method','sha','offset'},
            'personal_commit':{'method','workspace','sha','expect'},
            'personal_thread_list':{'method'}, 'personal_work_list':{'method'},
            'personal_thread_head':{'method','thread'},
            'personal_thread_commit':{'method','thread','sha','expect'},
            'personal_thread_delta':{'method','thread','sha','expect','changes'},
            'personal_execution_list':{'method'},
            'personal_execution_head':{'method','thread'},
            'personal_execution_commit':{'method','thread','sha','expect'},
        }
        from .task_queue import FIELDS
        allowed.update(FIELDS)
        method=payload.get('method')
        if method not in allowed or set(payload)!=allowed[method]:
            raise Error('INPUT_INVALID','Invalid personal relay request; caller identity is server-derived.')
        with self.hub.locked('personal-'+self.owner[:32]):
            path=self.root/'relay.sqlite3'
            # All private paths are checked before opening; same-account applications share this trust boundary.
            from .store import safe_path
            safe_path(path)
            db=sqlite3.connect(str(path),timeout=15)
            path.chmod(0o600)
            db.row_factory=sqlite3.Row
            try:
                db.executescript('''PRAGMA synchronous=FULL;
                    CREATE TABLE IF NOT EXISTS blobs(sha TEXT PRIMARY KEY,size INTEGER,received INTEGER,complete INTEGER);
                    CREATE TABLE IF NOT EXISTS heads(workspace TEXT PRIMARY KEY,sha TEXT);
                    CREATE TABLE IF NOT EXISTS history(workspace TEXT,sha TEXT,at REAL,UNIQUE(workspace,sha));
                    CREATE TABLE IF NOT EXISTS traffic(at REAL,bytes INTEGER);
                    CREATE TABLE IF NOT EXISTS thread_heads(thread TEXT PRIMARY KEY,sha TEXT,title TEXT);
                    CREATE TABLE IF NOT EXISTS work_names(thread TEXT PRIMARY KEY,card TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS thread_history(thread TEXT,sha TEXT,at REAL,UNIQUE(thread,sha));
                    CREATE TABLE IF NOT EXISTS execution_heads(thread TEXT PRIMARY KEY,sha TEXT,title TEXT);
                    CREATE TABLE IF NOT EXISTS execution_history(thread TEXT,sha TEXT,at REAL,UNIQUE(thread,sha));
                    CREATE INDEX IF NOT EXISTS traffic_time ON traffic(at);''')
                with db:
                    db.execute('DELETE FROM traffic WHERE at < ?', (time.time()-86400,))
                    return self.run(db,method,payload)
            finally: db.close()

    def budget(self, db, amount):
        used=db.execute('SELECT COALESCE(SUM(bytes),0) FROM traffic').fetchone()[0]
        if used+amount>self.policy['daily_bytes']:
            raise Error('TRANSFER_BUDGET','Owner rolling 24-hour transfer budget is exhausted; retry later.')
        if amount: db.execute('INSERT INTO traffic VALUES(?,?)',(time.time(),amount))

    def run(self, db, method, p):
        if method.startswith('personal_task_'):
            from .task_queue import dispatch
            return dispatch(self, db, method[len('personal_task_'):], p)
        if method=='personal_info':
            return {'protocol':'personal.v1','thread_protocol':'thread.personal.v1','delta_protocol':'thread.delta.v1','task_protocol':'thread.task.v1','execution_protocol':'thread.execution.v1','limits':self.policy,
                    'access_protocol':'thread.access.v1' if self.hub.community() else None,
                    'workspace_protocol':'workspace.v1' if self.hub.community() else None,
                    'unified':self.hub.settings().get('format')==2,
                    'stored_bytes':db.execute('SELECT COALESCE(SUM(size),0) FROM blobs').fetchone()[0],
                    'traffic_bytes':db.execute('SELECT COALESCE(SUM(bytes),0) FROM traffic').fetchone()[0]}
        if method=='personal_list':
            return {'workspaces':[dict(r) for r in db.execute('SELECT * FROM heads ORDER BY workspace')]}
        if method=='personal_work_list':
            from .protocol import decode
            from .work_directory import names_from_docs
            result = []
            for row in db.execute('SELECT h.thread,h.sha,n.card FROM thread_heads h LEFT JOIN work_names n USING(thread) ORDER BY h.thread'):
                if row['card']:
                    card = json.loads(row['card'])
                else:
                    snapshot = json.loads((self.blobs/row['sha']).read_bytes())
                    docs = [decode(raw) for raw in snapshot['documents'].values()]
                    card = names_from_docs(row['thread'], {d['meta']['id']:d for d in docs})
                    db.execute('INSERT INTO work_names VALUES(?,?)', (row['thread'],packed(card)))
                result.append(dict(card,sha=row['sha']))
            return {'threads':result,'audience':'owner'}
        if method in {'personal_thread_list','personal_execution_list'}:
            table='execution_heads' if method=='personal_execution_list' else 'thread_heads'
            return {'threads':[dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY thread')], 'audience':'owner'}
        if method in {'personal_thread_head','personal_thread_commit','personal_thread_delta','personal_execution_head','personal_execution_commit'}:
            from .thread_protocol import identifier
            tid=identifier(p['thread'],'W')
            table='execution_heads' if method.startswith('personal_execution_') else 'thread_heads'
            current=db.execute('SELECT sha FROM '+table+' WHERE thread=?',(tid,)).fetchone()
            head=current['sha'] if current else None
            if method.endswith('_head'): return {'thread':tid,'sha':head}
            if method == 'personal_thread_delta':
                return self.commit_delta(db, p, head)
        if method in {'personal_head','personal_commit'}:
            workspace=check_id(p['workspace'])
            current=db.execute('SELECT sha FROM heads WHERE workspace=?',(workspace,)).fetchone()
            head=current['sha'] if current else None
            if method=='personal_head': return {'workspace':workspace,'sha':head}
        sha=check_id(p['sha'],HASH)
        row=db.execute('SELECT * FROM blobs WHERE sha=?',(sha,)).fetchone()
        final=self.blobs/sha; part=self.parts/sha
        if method=='personal_begin':
            size=integer(p['size'],self.policy['explicit_file'])
            if row and row['size']!=size: raise Error('STATE_CONFLICT','Object size differs from the registered object.')
            if not row:
                used=db.execute('SELECT COALESCE(SUM(size),0) FROM blobs').fetchone()[0]
                if used+size>self.policy['server_bytes']: raise Error('STORAGE_QUOTA','Private relay storage quota reached; originals are retained.')
                db.execute('INSERT INTO blobs VALUES(?,?,0,0)',(sha,size))
                row={'size':size,'received':0,'complete':0}
            if not row['complete']:
                from .store import safe_path
                safe_path(part)
                if not part.exists():
                    with part.open('xb'): pass
                    part.chmod(0o600)
                with part.open('r+b') as out: out.truncate(row['received'])
                if size==0:
                    if sha!=digest(b''): raise Error('CHECKSUM_MISMATCH','Invalid empty object hash.')
                    os.replace(part,final);db.execute('UPDATE blobs SET complete=1 WHERE sha=?',(sha,))
                    return {'offset':0,'complete':True}
            return {'offset':row['received'],'complete':bool(row['complete'])}
        if row is None: raise Error('NOT_FOUND','Personal object is unavailable for this account.')
        if method=='personal_put':
            offset=integer(p['offset'],row['size'])
            if not isinstance(p['data'],str) or len(p['data'])>4*((CHUNK+2)//3):
                raise Error('INPUT_TOO_LARGE','Transfer chunk exceeds the protocol limit.')
            try: raw=base64.b64decode(p['data'],validate=True)
            except ValueError: raise Error('INPUT_INVALID','Invalid transfer chunk.')
            if not raw or len(raw)>CHUNK or offset+len(raw)>row['size']:
                raise Error('INPUT_INVALID','Invalid transfer chunk length.')
            self.budget(db,len(raw))
            from .store import safe_path
            source=safe_path(final if row['complete'] else part)
            if offset<row['received'] or row['complete']:
                with source.open('rb') as stream: stream.seek(offset);known=stream.read(len(raw))
                if known!=raw: raise Error('STATE_CONFLICT','Retried bytes differ from the accepted chunk.')
                return {'offset':row['received'],'complete':bool(row['complete'])}
            if offset!=row['received']: raise Error('STATE_CONFLICT','Resume from the server-confirmed offset.')
            with source.open('r+b') as stream:
                stream.truncate(offset);stream.seek(offset);stream.write(raw);stream.flush();os.fsync(stream.fileno())
            received=offset+len(raw); complete=received==row['size']
            if complete:
                if sha_file(part)!=sha: raise Error('CHECKSUM_MISMATCH','Uploaded bytes differ from their digest.')
                # Keep the resumable part until commit, so a crash before SQLite commit is recoverable.
                if not final.exists(): os.link(part,final)
            db.execute('UPDATE blobs SET received=?,complete=? WHERE sha=?',(received,int(complete),sha))
            return {'offset':received,'complete':complete}
        if not row['complete']: raise Error('SOURCE_PENDING','Personal object upload is incomplete.')
        if method=='personal_get':
            offset=integer(p['offset'],row['size'])
            count=min(CHUNK,row['size']-offset); self.budget(db,count)
            from .store import safe_path
            with safe_path(final).open('rb') as stream: stream.seek(offset);raw=stream.read(count)
            if len(raw)!=count: raise Error('CHECKSUM_MISMATCH','Stored object size differs; retain local sources.')
            return {'sha':sha,'size':row['size'],'offset':offset,'data':base64.b64encode(raw).decode()}
        if method=='personal_thread_commit':
            return self.commit_thread(db,p,head,row,final)
        if method=='personal_execution_commit':
            return self.commit_execution(db,p,head,row,final)
        if p['expect'] is not None: check_id(p['expect'],HASH)
        if head==sha: return {'workspace':workspace,'sha':sha,'accepted':True,'replayed':True}
        if head!=p['expect']: raise Error('PERSONAL_CONFLICT','Personal workspace advanced; pull and merge before retrying.')
        if row['size']>MAX_METADATA: raise Error('INPUT_TOO_LARGE','Personal workspace metadata exceeds its limit.')
        raw=final.read_bytes()
        if digest(raw)!=sha: raise Error('CHECKSUM_MISMATCH','Personal snapshot checksum differs.')
        value=json.loads(raw);validate_snapshot(value,Store(self.root/'validation'))
        if head:
            old=json.loads((self.blobs/head).read_bytes())
            if old['files']['.cosmesh/config.json']!=value['files']['.cosmesh/config.json']:
                raise Error('HISTORY_PROTECTED','Personal workbench identity is immutable.')
            if set(old['files'])-set(value['files']): raise Error('HISTORY_PROTECTED','Keep source files and use lifecycle records to remove work.')
        for asset in value['assets'].values():
            if asset['blob']:
                found=db.execute('SELECT size,complete FROM blobs WHERE sha=?',(asset['blob'],)).fetchone()
                if not found or not found['complete'] or found['size']!=asset['size']:
                    raise Error('SOURCE_PENDING','Referenced file must be accepted in this owner space first.')
                reason=content_reason(self.blobs/asset['blob'],asset['name'],asset['science'])
                if reason:raise Error('TRANSFER_FILTERED','Personal manifest file rejected: '+reason)
        db.execute('INSERT OR REPLACE INTO heads VALUES(?,?)',(workspace,sha))
        db.execute('INSERT OR IGNORE INTO history VALUES(?,?,?)',(workspace,sha,time.time()))
        return {'workspace':workspace,'sha':sha,'accepted':True,'replayed':False}

    def commit_delta(self, db, p, head):
        from .store import atomic, safe_path
        from .thread_personal_protocol import MAPS, MAX_DELTA
        sha = check_id(p['sha'], HASH)
        expected = check_id(p['expect'], HASH)
        if sha == head:
            return {'thread': p['thread'], 'sha': head, 'accepted': True, 'replayed': True}
        if head != expected:
            raise Error('PERSONAL_CONFLICT', 'Thread advanced; synchronize this Thread again.')
        changes = p['changes']
        if not isinstance(changes, dict) or set(changes) - set(MAPS) or not all(
                isinstance(entries, dict) for entries in changes.values()):
            raise Error('INPUT_INVALID', 'Invalid incremental Thread metadata.')
        wire_size = len(packed(changes).encode())
        if wire_size > MAX_DELTA:
            raise Error('INPUT_TOO_LARGE', 'Incremental Thread metadata exceeds the single-request limit.')
        base = json.loads(safe_path(self.blobs / expected).read_bytes())
        for field, entries in changes.items():
            base[field].update(entries)
        raw = packed(base).encode()
        size = len(raw)
        if size > min(MAX_METADATA, self.policy['explicit_file']):
            raise Error('INPUT_TOO_LARGE', 'The reconstructed Thread exceeds the metadata limit.')
        if digest(raw) != sha:
            raise Error('CHECKSUM_MISMATCH', 'The incremental update differs from the proposed snapshot.')
        row = db.execute('SELECT size FROM blobs WHERE sha=?', (sha,)).fetchone()
        if row is None:
            used = db.execute('SELECT coalesce(sum(size),0) FROM blobs').fetchone()[0]
            if used + size > self.policy['server_bytes']:
                raise Error('STORAGE_QUOTA', 'Private relay storage quota reached.')
        elif row['size'] != size:
            raise Error('STATE_CONFLICT', 'Object size differs from the registered object.')
        self.budget(db, wire_size)
        final = safe_path(self.blobs / sha)
        receipt = self.commit_thread(db, p, head, {'size': size}, final, raw=raw)
        # Publish the immutable bytes before committing the head and blob ledger together.
        # Replacing also repairs an orphaned object from a previously interrupted transaction.
        atomic(final, raw.decode())
        db.execute('INSERT OR REPLACE INTO blobs VALUES(?,?,?,1)', (sha, size, size))
        return receipt

    def commit_thread(self,db,p,head,row,final,raw=None):
        from .thread_personal_protocol import validate_manifest,protect_history
        if p['expect'] is not None: check_id(p['expect'],HASH)
        if head==p['sha']: return {'thread':p['thread'],'sha':head,'accepted':True,'replayed':True}
        if head!=p['expect']: raise Error('PERSONAL_CONFLICT','Thread advanced; synchronize this Thread again.')
        if row['size']>MAX_METADATA: raise Error('INPUT_TOO_LARGE','Thread manifest exceeds its limit.')
        if raw is None: raw=final.read_bytes()
        if digest(raw)!=p['sha']: raise Error('CHECKSUM_MISMATCH','Thread manifest checksum differs.')
        value=json.loads(raw)
        model=validate_manifest(value,Store(self.root/'validation'))
        if value['thread']!=p['thread']: raise Error('POLICY_DENIED','Selected Thread differs from the manifest.')
        previous = json.loads((self.blobs/head).read_bytes()) if head else {}
        if head: protect_history(previous, value)
        approved = previous.get('assets', {})
        for aid, asset in value['assets'].items():
            if asset['blob']:
                found=db.execute('SELECT size,complete FROM blobs WHERE sha=?',(asset['blob'],)).fetchone()
                if not found or not found['complete'] or found['size']!=asset['size']:
                    raise Error('SOURCE_PENDING','Selected file must be accepted in this owner space first.')
                path = self.blobs / asset['blob']
                if not path.is_file() or path.stat().st_size != asset['size']:
                    raise Error('SOURCE_PENDING','The stored object is missing or its size has changed.')
                if approved.get(aid) == asset:
                    continue
                reason=content_reason(path,asset['name'],asset['science'])
                if reason: raise Error('TRANSFER_FILTERED','Selected file rejected: '+reason)
        from .work_directory import names_from_docs
        card = names_from_docs(p['thread'],model.docs)
        db.execute('INSERT OR REPLACE INTO work_names VALUES(?,?)',(p['thread'],packed(card)))
        title=model.docs[p['thread']]['meta']['title']
        db.execute('INSERT OR REPLACE INTO thread_heads VALUES(?,?,?)',(p['thread'],p['sha'],title))
        db.execute('INSERT OR IGNORE INTO thread_history VALUES(?,?,?)',(p['thread'],p['sha'],time.time()))
        return {'thread':p['thread'],'sha':p['sha'],'accepted':True,'replayed':False}

    def commit_execution(self,db,p,head,row,final):
        from .thread_execution_protocol import validate,protect
        from .protocol import decode
        if p['expect'] is not None: check_id(p['expect'],HASH)
        if head==p['sha']: return {'thread':p['thread'],'sha':head,'accepted':True,'replayed':True}
        if head!=p['expect']: raise Error('PERSONAL_CONFLICT','Private execution information advanced; retry this Thread.')
        if row['size']>MAX_METADATA: raise Error('INPUT_TOO_LARGE','Private execution metadata exceeds its limit.')
        raw=final.read_bytes()
        if digest(raw)!=p['sha']: raise Error('CHECKSUM_MISMATCH','Execution manifest checksum differs.')
        value=json.loads(raw);validate(value)
        if value['thread']!=p['thread']: raise Error('POLICY_DENIED','Selected Thread differs from the execution manifest.')
        if head: protect(json.loads((self.blobs/head).read_bytes()),value)
        source=value['source'];policy=self.hub.policy(source['repo'],self.actor)
        if not policy.get('thread_format') or policy['owner']!=source['owner']:
            raise Error('REFERENCE_INVALID','The public Thread repository differs from its declared source.')
        doc=self.hub.repo(source['repo']).git('show','refs/heads/main:documents/'+p['thread']+'.md',check=False)
        if doc.returncode: raise Error('REFERENCE_UNAVAILABLE','Publish the Thread before syncing private execution information.')
        doc=decode(doc.stdout)
        if doc['meta']['kind']!='thread' or doc['meta']['id']!=p['thread']:
            raise Error('REFERENCE_INVALID','The source is not the selected public Thread.')
        db.execute('INSERT OR REPLACE INTO execution_heads VALUES(?,?,?)',(p['thread'],p['sha'],doc['meta']['title']))
        db.execute('INSERT OR IGNORE INTO execution_history VALUES(?,?,?)',(p['thread'],p['sha'],time.time()))
        return {'thread':p['thread'],'sha':p['sha'],'accepted':True,'replayed':False}
