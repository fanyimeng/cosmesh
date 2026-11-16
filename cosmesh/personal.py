"""Local references and opt-in personal workbench replicas; old stores stay schema 2."""
import base64
import hashlib
import json
import os
import socket
import tempfile
from pathlib import Path
from uuid import uuid4

from .errors import Error
from .store import Store, atomic, safe_path
from .threads import Workbench
from .personal_protocol import (CHUNK, DEFAULTS, HASH, MAX_METADATA, check_id, digest,
    fingerprint, inspect_file, integer, merge_maps, packed, private_dir, read_json,
    save_json, sha_file, validate_snapshot, content_reason)


def initialize(wb, device=None):
    """Caller holds the original store lock. Only an ignored sidecar is added."""
    directory=wb.base.path('.cosmesh/personal-v1')
    existing=read_json(directory/'state.json')
    if existing: return {'enabled':True,'workspace':existing['workspace'],'unchanged':True}
    if device is None:
        # The installation-independent data directory is shared by local Agents.
        parent=Path(os.environ.get('COSMESH_DATA_DIR',str(Path.home()/'.local/share/cosmesh')))
        devices=private_dir(parent/'personal-device-v1')
        import fcntl
        with (devices/'lock').open('a+') as lock:
            fcntl.lockf(lock,fcntl.LOCK_EX)
            value=read_json(devices/'identity.json')
            if value is None:
                value={'id':uuid4().hex,'host':socket.gethostname()};save_json(devices/'identity.json',value)
            device=value['id']
    check_id(device)
    exclude=wb.base.path('.git/info/exclude')
    if '.cosmesh/personal-v1/' not in exclude.read_text().splitlines():
        atomic(exclude,exclude.read_text()+'\n.cosmesh/personal-v1/\n')
    private_dir(directory);private_dir(directory/'cache');private_dir(directory/'staging')
    state={'format':1,'workspace':uuid4().hex,'replica':uuid4().hex,'device':device,
           'connection':None,'actor':None,'base':None,'assets':{},'bindings':{},'policy':dict(DEFAULTS)}
    save_json(directory/'state.json',state)
    return {'enabled':True,'workspace':state['workspace'],'device':device,'distribution':'personal',
            'audience':'owner','network_enabled':False}


class Personal:
    def __init__(self, wb):
        self.wb=wb;self.root=wb.base.path('.cosmesh/personal-v1')
        self.state=read_json(self.root/'state.json')
        if not self.state: raise Error('PERSONAL_NOT_ENABLED','Run personal init for this workbench first.')
        if self.state.get('format')!=1: raise Error('SCHEMA_UNSUPPORTED','Unsupported personal sidecar; original workbench is retained.')
        self.cache=private_dir(self.root/'cache');self.staging=private_dir(self.root/'staging')
        pending=read_json(self.root/'apply.json')
        if pending:
            self._apply(pending)

    def save(self): save_json(self.root/'state.json',self.state)

    def cache_room(self,additional):
        used=sum(p.stat().st_size for p in self.cache.iterdir() if p.is_file())
        if used+additional>self.state['policy']['cache_bytes']:
            raise Error('STORAGE_QUOTA','Local personal cache quota reached; original files are retained.')

    def rpc(self,payload):
        from .spaces import rpc
        config=self.state['connection']
        if not config: raise Error('HUB_CONNECTION_REQUIRED','Connect this personal workbench to an enabled relay.')
        reply=rpc(config,payload)
        if reply.get('actor')!=self.state['actor']:
            raise Error('AUTH_DENIED','Personal relay identity changed; retain all local sources.')
        return reply

    def connect(self,ssh=None,service=None):
        from .spaces import connection,rpc
        config=connection(self.wb.base.root,ssh,service)
        try: reply=rpc(config,{'method':'personal_info'})
        except Error as exc:
            if exc.code in {'INPUT_INVALID','COMMAND_DENIED'}:
                raise Error('FEATURE_UNAVAILABLE','This server predates personal relay v1; local sources are unchanged.')
            raise
        if reply.get('protocol')!='personal.v1' or not isinstance(reply.get('actor'),str):
            raise Error('FEATURE_UNAVAILABLE','Server does not support personal relay v1.')
        if self.state['connection'] and (config!=self.state['connection'] or reply['actor']!=self.state['actor']):
            raise Error('STATE_CONFLICT','Use a separate workbench for another relay or identity.')
        self.state.update(connection=config,actor=reply['actor']);self.save()
        return {'connected':True,'actor':reply['actor'],'limits':reply['limits'],'audience':'owner'}

    def limits(self):
        remote=self.rpc({'method':'personal_info'})['limits']
        return {k:min(v,remote[k]) for k,v in self.state['policy'].items()}

    def status(self):
        return {'mode':'personal','audience':'owner','workspace':self.state['workspace'],
                'device':self.state['device'],'replica':self.state['replica'],
                'connected':bool(self.state['connection']),'accepted_snapshot':self.state['base'],
                'asset_count':len(self.state['assets']),'policy':self.state['policy'],
                'automatic_background':False}

    def add(self,tid,path,science=False):
        item=self.wb.find(tid,'thread')
        if item['repo']!='local': raise Error('POLICY_DENIED','This first-stage personal workspace registers private Thread assets.')
        path=Path(path).expanduser().absolute()
        info=path.stat()
        existing=next((key for key,row in self.state['assets'].items() if row['thread']==tid and
                       row['device']==self.state['device'] and row['path']==str(path)),None)
        aid=existing or uuid4().hex
        if existing and self.state['assets'][aid]['science']!=science:
            self.state['assets'][aid]['science']=science
        elif not existing:
            self.state['assets'][aid]={'name':path.name,'thread':tid,'device':self.state['device'],
                'path':str(path),'science':science,'blob':None,'size':info.st_size}
        self.save()
        return {'id':aid,'state':'local_reference','path':str(path),'copied_bytes':0,'transfer_limits_applied':False}

    def plan(self,ids=None,explicit=False):
        limits=self.state['policy']; rows=[];total=0;count=0
        selected=list(self.state['assets']) if ids is None else ids
        for aid in selected:
            row=self.asset(aid);reason=None
            if row['device']!=self.state['device']:
                result={'reason':'other_source_device','bytes':row['size']}
            else:
                try: result=inspect_file(row['path'],limits['explicit_file' if explicit else 'auto_file'],row['science'])
                except (OSError,Error) as exc: result={'reason':getattr(exc,'code','file_unavailable')}
            reason=result.get('reason')
            if not reason:
                if count>=limits['batch_files'] or total+result['bytes']>limits['batch_bytes']: reason='batch_limit'
                else: total+=result['bytes'];count+=1
            rows.append({'id':aid,'name':row['name'],'state':'skipped' if reason else 'eligible',
                         'reason':reason,'bytes':result.get('bytes')})
        return {'files':rows,'bytes':total,'files_eligible':count,'explicit':explicit}

    def asset(self,aid):
        check_id(aid)
        if aid not in self.state['assets']: raise Error('NOT_FOUND','Personal asset is not registered.')
        return self.state['assets'][aid]

    def upload(self,path,sha,size):
        reply=self.rpc({'method':'personal_begin','sha':sha,'size':size})
        offset=integer(reply.get('offset'),size)
        if reply.get('complete'): return
        with Path(path).open('rb') as source:
            source.seek(offset)
            while offset<size:
                raw=source.read(min(CHUNK,size-offset))
                if not raw: raise Error('STATE_CONFLICT','Frozen transfer source changed.')
                result=self.rpc({'method':'personal_put','sha':sha,'offset':offset,'data':base64.b64encode(raw).decode()})
                next_offset=integer(result.get('offset'),size)
                if next_offset<offset+len(raw): raise Error('SYNC_PROTOCOL','Upload did not acknowledge the submitted bytes.')
                offset=next_offset;source.seek(offset)
            if size and not result.get('complete'): raise Error('SOURCE_PENDING','Upload is not confirmed complete.')

    def push(self,ids=None,explicit=False):
        # Server hard limits are combined locally without changing saved user preferences.
        local_policy=self.state['policy'];self.state['policy']=self.limits()
        try: plan=self.plan(ids,explicit)
        finally: self.state['policy']=local_policy
        for entry in plan['files']:
            if entry['reason']: continue
            row=self.asset(entry['id']);path=safe_path(row['path']);before=path.stat()
            staged=self.staging/(entry['id']+'.part')
            h=hashlib.sha256();size=0
            try:
                with path.open('rb') as source, staged.open('wb') as target:
                    staged.chmod(0o600)
                    if fingerprint(os.fstat(source.fileno()))!=fingerprint(before): raise Error('STATE_CONFLICT','Source changed before transfer.')
                    for raw in iter(lambda:source.read(CHUNK),b''):
                        size+=len(raw)
                        if size>entry['bytes']: raise Error('STATE_CONFLICT','Source grew during transfer.')
                        h.update(raw);target.write(raw)
                    target.flush();os.fsync(target.fileno())
                    after=os.fstat(source.fileno())
                if size!=entry['bytes'] or fingerprint(before)!=fingerprint(after) or fingerprint(path.stat())!=fingerprint(before):
                    raise Error('STATE_CONFLICT','Source changed while preparing transfer; retry after writing finishes.')
                reason=content_reason(staged,row['name'],row['science'])
                if reason:raise Error('TRANSFER_FILTERED','Frozen file rejected: '+reason)
                sha=h.hexdigest();self.upload(staged,sha,size)
                row.update(blob=sha,size=size);self.save()
                entry.update(state='server_available',sha=sha)
            finally: staged.unlink(missing_ok=True)
        return plan

    def download(self,sha,expected=None,metadata=False):
        check_id(sha,HASH);target=safe_path(self.cache/sha)
        if target.exists():
            if (expected is None or target.stat().st_size==expected) and sha_file(target)==sha: return target
            raise Error('CHECKSUM_MISMATCH','Local cached object changed; preserve it for inspection.')
        part=safe_path(self.cache/(sha+'.part'))
        offset=part.stat().st_size if part.exists() else 0
        size=expected
        with part.open('ab') as stream:
            part.chmod(0o600)
            while size is None or offset<size:
                reply=self.rpc({'method':'personal_get','sha':sha,'offset':offset})
                observed=integer(reply.get('size'),MAX_METADATA if metadata else self.state['policy']['explicit_file'])
                if size is not None and size!=observed: raise Error('SYNC_PROTOCOL','Download size changed.')
                size=observed
                if reply.get('sha')!=sha or reply.get('offset')!=offset: raise Error('SYNC_PROTOCOL','Download object or offset changed.')
                self.cache_room(size-offset)
                raw=base64.b64decode(reply['data'],validate=True)
                if len(raw)>CHUNK or offset+len(raw)>size or (not raw and offset<size): raise Error('SYNC_PROTOCOL','Invalid download chunk.')
                stream.write(raw);stream.flush();os.fsync(stream.fileno());offset+=len(raw)
        if (size is not None and offset!=size) or sha_file(part)!=sha:
            part.unlink(missing_ok=True)
            raise Error('CHECKSUM_MISMATCH','Downloaded object differs from its fixed reference; partial cache cleared.')
        os.replace(part,target)
        return target

    def open(self,aid,offline=False):
        row=self.asset(aid)
        if row['device']==self.state['device'] and Path(row['path']).exists():
            return {'id':aid,'state':'local_reference','path':row['path'],'copied_bytes':0,
                    'version':'mutable_source','sha':None}
        if not row['blob']: raise Error('SOURCE_PENDING','Source file has not been uploaded; its location is retained.')
        target=self.cache/row['blob']
        if offline and not target.exists(): raise Error('OFFLINE','Fixed file is not cached on this device.')
        path=self.download(row['blob'],row['size'])
        return {'id':aid,'state':'local_available','path':str(path),'sha':row['blob'],'bytes':row['size'],
                'project_files_overwritten':False}

    def snapshot(self):
        _,files=self.wb.base.snapshot()
        bindings=dict(self.state['bindings'])
        bindings[self.state['replica']]={'device':self.state['device'],
            'bindings':self.wb.client.get('bindings',[]),'project_bindings':self.wb.client.get('project_bindings',[])}
        from .thread_protocol import references
        from .protocol import encode
        sources=read_json(self.root/'sources.json',{})
        pending=[ref for doc in self.wb._models['local'][2].docs.values() for ref in references(doc['meta']) if ref['repo']=='local']
        seen=set()
        while pending:
            ref=pending.pop();key=ref['id']+'@'+ref['version']
            if key in seen:continue
            seen.add(key);doc=self.wb.resolve(ref);sources[key]=encode(doc)
            pending.extend(r for r in references(doc['meta']) if r['repo']=='local')
        result={'format':1,'files':files,'assets':self.state['assets'],'bindings':bindings,'sources':sources}
        validate_snapshot(result,self.wb.base)
        return result

    def _apply(self,pending):
        value=pending['snapshot'];validate_snapshot(value,self.wb.base)
        _,current=self.wb.base.snapshot()
        if self.wb.base.head!=pending['before'] and current!=value['files']:
            raise Error('RECOVERY_REQUIRED','Personal apply was interrupted and local work advanced; preserve the recovery journal.')
        changes={k:v for k,v in value['files'].items() if current.get(k)!=v}
        if set(current)-set(value['files']): raise Error('HISTORY_PROTECTED','Personal sync cannot remove source files.')
        if changes: self.wb.base.commit(changes,'personal-sync')
        save_json(self.root/'sources.json',value['sources'])
        self.state.update(assets=value['assets'],bindings=value['bindings'],base=pending['base'])
        self.save();(self.root/'apply.json').unlink(missing_ok=True);self.wb.load()
        from .status_pages import schedule
        schedule(self.wb)

    def sync(self,pull=False,files=False):
        transfers=self.push() if files and not pull else None
        workspace=self.state['workspace']
        head=self.rpc({'method':'personal_head','workspace':workspace})['sha']
        local=self.snapshot()
        remote=json.loads(self.download(head,metadata=True).read_bytes()) if head else None
        if remote: validate_snapshot(remote,self.wb.base)
        previous=self.state['base']
        intent=read_json(self.root/'upload-intent.json')
        if intent and head==intent.get('sha') and workspace==intent.get('workspace'):
            # A reply may be lost after acceptance, followed by more local work.
            # Merge from that exact accepted candidate instead of guessing from time.
            previous=head
        base=json.loads(self.download(previous,metadata=True).read_bytes()) if previous else None
        if previous and not head: raise Error('HISTORY_PROTECTED','Previously accepted personal workspace is missing.')
        merged=local
        if remote:
            if base is None:
                if remote!=local: raise Error('PERSONAL_CONFLICT','Join the existing personal workspace in an empty workbench.')
            else:
                merged={'format':1,**{k:merge_maps(base[k],local[k],remote[k]) for k in ('files','assets','bindings','sources')}}
        validate_snapshot(merged,self.wb.base)
        raw=packed(merged).encode()
        if len(raw)>MAX_METADATA: raise Error('INPUT_TOO_LARGE','Personal snapshot metadata exceeds its limit.')
        accepted=head
        if not pull:
            sha=digest(raw);path=self.cache/sha
            if not path.exists():
                self.cache_room(len(raw));atomic(path,raw.decode());path.chmod(0o600)
            self.upload(path,sha,len(raw))
            save_json(self.root/'upload-intent.json',{'workspace':workspace,'sha':sha,'expect':head})
            self.rpc({'method':'personal_commit','workspace':workspace,'sha':sha,'expect':head})
            accepted=sha
        pending={'before':self.wb.base.head,'snapshot':merged,'base':accepted}
        save_json(self.root/'apply.json',pending);self._apply(pending)
        (self.root/'upload-intent.json').unlink(missing_ok=True)
        return {'workspace':workspace,'accepted_snapshot':accepted,'pulled':bool(head),
                'published':False,'personal_uploaded':not pull,'transfers':transfers,'assets_fetched':0}

    def join(self,workspace):
        check_id(workspace)
        if self.state['base'] or self.state['assets'] or self.wb.catalog:
            raise Error('STORE_EXISTS','Join requires an empty initialized Thread workbench; existing work stays separate.')
        head=self.rpc({'method':'personal_head','workspace':workspace})['sha']
        if not head: raise Error('NOT_FOUND','Personal workspace is unavailable for this account.')
        value=json.loads(self.download(head,metadata=True).read_bytes());validate_snapshot(value,self.wb.base)
        self.state['workspace']=workspace;self.save()
        pending={'before':self.wb.base.head,'snapshot':value,'base':head}
        save_json(self.root/'apply.json',pending);self._apply(pending)
        return {'workspace':workspace,'accepted_snapshot':head,'files_downloaded':0,'joined':True}
