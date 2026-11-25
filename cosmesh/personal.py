"""Account transport and local file references, independent of Thread synchronization."""
import base64
import hashlib
import os
import socket
from pathlib import Path
from uuid import uuid4

from .errors import Error
from .source_paths import manifest_path
from .store import atomic, safe_path
from .transfer_policy import effective
from .personal_protocol import (CHUNK, DEFAULTS, HASH, MAX_METADATA, check_id,
    fingerprint, inspect_file, integer, private_dir, read_json,
    save_json, sha_file, content_reason)


def initialize(wb, device=None):
    """Caller holds the original store lock. Only an ignored sidecar is added."""
    directory=wb.base.path('.cosmesh/personal-v1')
    existing=read_json(directory/'state.json')
    if existing: return {'enabled':True,'workspace':existing['workspace'],'unchanged':True}
    if device is None:
        # The installation-independent data directory is shared by local Agents.
        parent=Path(os.environ.get('COSMESH_DATA_DIR',str(Path.home()/'.local/share/cosmesh')))
        devices=private_dir(parent/'personal-device-v1')
        from . import _fcntl as fcntl
        with (devices/'lock').open('a+') as lock:
            fcntl.lockf(lock,fcntl.LOCK_EX)
            value=read_json(devices/'identity.json')
            if value is None:
                value={'id':uuid4().hex,'host':socket.gethostname()};save_json(devices/'identity.json',value)
            device=value['id']
    check_id(device)
    exclude=wb.base.path('.git/info/exclude')
    if '.cosmesh/personal-v1/' not in exclude.read_text(encoding="utf-8").splitlines():
        atomic(exclude,exclude.read_text(encoding="utf-8")+'\n.cosmesh/personal-v1/\n')
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
        if not self.state: raise Error('PERSONAL_NOT_ENABLED','Select a Thread with thread personal ID, or register a file with personal asset add ID FILE.')
        if self.state.get('format')!=1: raise Error('SCHEMA_UNSUPPORTED','Unsupported personal sidecar; original workbench is retained.')
        self.cache=private_dir(self.root/'cache');self.staging=private_dir(self.root/'staging')
        if (self.root/'apply.json').exists():
            raise Error('LEGACY_RECOVERY_REQUIRED','An old whole-store restore is unfinished. Complete it with the retained old version before using Thread sync; its journal is preserved.')

    def save(self): save_json(self.root/'state.json',self.state)

    def cache_room(self,additional):
        from .transfer_policy import effective
        policy=effective(self.state['policy'],self.state.get('policy_overrides',[]))
        used=sum(p.stat().st_size for p in self.cache.iterdir() if p.is_file())
        if used+additional>policy['cache_bytes']:
            raise Error('STORAGE_QUOTA','Local personal cache quota reached; original files are retained.')

    def rpc(self,payload):
        from .spaces import rpc
        config=self.state['connection']
        if not config: raise Error('HUB_CONNECTION_REQUIRED','Use thread personal ID with your existing Hub connection.')
        if self.wb.client.get('actor') not in (None,self.state.get('actor')):
            raise Error('AUTH_DENIED','The selected local account differs from the private relay.')
        if self.wb.client.get('connection') and self.wb.client['connection']!=config:
            raise Error('STATE_CONFLICT','The work store and personal relay use different connections.')
        reply=rpc(config,payload)
        if not self.state.get('actor') or reply.get('actor')!=self.state['actor']:
            raise Error('AUTH_DENIED','Personal relay identity changed; retain all local sources.')
        return reply

    def connect(self,ssh=None,service=None):
        from .spaces import connection,rpc
        config=connection(self.wb.base.root,ssh,service) if ssh or service else self.wb.client.get('connection') or connection(self.wb.base.root)
        try: reply=rpc(config,{'method':'personal_info'})
        except Error as exc:
            if exc.code in {'INPUT_INVALID','COMMAND_DENIED'}:
                raise Error('FEATURE_UNAVAILABLE','This server predates personal relay v1; local sources are unchanged.')
            raise
        if reply.get('protocol')!='personal.v1' or not isinstance(reply.get('actor'),str):
            raise Error('FEATURE_UNAVAILABLE','Server does not support personal relay v1.')
        if self.wb.client.get('actor') and self.wb.client['actor']!=reply['actor']:
            raise Error('AUTH_DENIED','Personal relay identity differs from the existing Thread client.')
        if self.state['connection'] and (config!=self.state['connection'] or reply['actor']!=self.state['actor']):
            raise Error('STATE_CONFLICT','Another Hub or identity needs a separate local data directory.')
        self.state.update(connection=config,actor=reply['actor']);self.save()
        return {'connected':True,'actor':reply['actor'],'limits':reply['limits'],'audience':'owner'}

    def limits(self):
        remote=self.rpc({'method':'personal_info'})['limits']
        from .transfer_policy import effective
        return {k:min(v,remote[k]) for k,v in effective(self.state['policy'],self.state.get('policy_overrides',[])).items()}

    def add(self,tid,path,science=False,relative_path=None,persist=True):
        item=self.wb.find(tid,'thread')
        tid=item['doc']['meta']['id']
        if item['repo']!='local': raise Error('POLICY_DENIED','Register files on a private Thread; public attachments use thread share.')
        if relative_path is not None:
            from .thread_files import portable_path
            relative_path=portable_path(relative_path)
        path=Path(path).expanduser().absolute()
        info=path.stat()
        location = manifest_path(str(path))
        existing=next((key for key,row in self.state['assets'].items() if row['thread']==tid and
                       row['device']==self.state['device'] and manifest_path(row['path'])==location),None)
        aid=existing or uuid4().hex
        if existing and self.state['assets'][aid]['science']!=science:
            self.state['assets'][aid]['science']=science
        elif not existing:
            self.state['assets'][aid]={'name':path.name,'thread':tid,'device':self.state['device'],
                'path':str(path),'science':science,'blob':None,'size':info.st_size}
        if relative_path is not None:self.state.setdefault('asset_paths',{})[aid]=relative_path
        if persist:self.save()
        return {'id':aid,'state':'local_reference','path':str(path),'copied_bytes':0,'transfer_limits_applied':False,
                'relative_path':self.state.get('asset_paths',{}).get(aid,path.name)}

    def remember_copy(self, aid, path, blob, stamp):
        self.state.setdefault('asset_copies', {})[aid] = {
            'device': self.state['device'], 'path': str(path), 'blob': blob,
            'verified_host': socket.gethostname(), 'fingerprint': stamp,
        }

    def plan(self,ids=None,explicit=False,confirmed=None):
        from .transfer_policy import effective
        limits=effective(self.state['policy'],self.state.get('policy_overrides',[])); rows=[];total=0;count=0
        selected=list(self.state['assets']) if ids is None else ids
        for aid in selected:
            row=self.asset(aid);reason=None
            try:
                path=self.source_path(aid)
                copy = self.state.get('asset_copies', {}).get(aid, {})
                stamp = None
                if (confirmed is not None and row['blob'] and copy.get('blob') == row['blob']
                        and copy.get('device') == self.state['device'] and copy.get('path') == path
                        and copy.get('verified_host') == socket.gethostname()):
                    stamp = copy.get('fingerprint')
                result=inspect_file(path,limits['explicit_file' if explicit else 'auto_file'],row['science'],stamp)
            except (OSError,Error) as exc: result={'reason':getattr(exc,'code','file_unavailable'),'bytes':row['size']}
            reason=result.get('reason')
            if result.get('unchanged') and result['bytes'] == row['size']:
                # A current manifest confirms many objects in one authenticated read.
                # Outside a Thread sync, ask the same owner relay about this object.
                present = confirmed.get(row['blob']) == row['size']
                if not present:
                    reply = self.rpc({'method':'personal_begin','sha':row['blob'],'size':row['size']})
                    present = reply.get('complete') is True and reply.get('offset') == row['size']
                if present:
                    rows.append(dict(id=aid, name=row['name'], state='server_available', unchanged=True,
                                     sha=row['blob'], reason=None, bytes=row['size']))
                    continue
            if not reason:
                if count>=limits['batch_files'] or total+result['bytes']>limits['batch_bytes']: reason='batch_limit'
                else: total+=result['bytes'];count+=1
            rows.append({'id':aid,'name':row['name'],'state':'skipped' if reason else 'eligible',
                         'reason':reason,'bytes':result.get('bytes')})
        return {'files':rows,'bytes':total,'files_eligible':count,'explicit':explicit}

    def source_path(self,aid):
        row=self.asset(aid);copy=self.state.get('asset_copies',{}).get(aid)
        if copy and copy['device']==self.state['device']:
            if copy['blob']!=row['blob']:
                raise Error('LOCAL_FILE_DIVERGED','The server file advanced beyond this local copy; preserve and reconcile it before upload.')
            return copy['path']
        if row['device']==self.state['device']:return row['path']
        raise Error('other_source_device','This device has no registered source file.')

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

    def push(self,ids=None,explicit=False,persist=True,confirmed=None):
        # Server hard limits are combined locally without changing saved user preferences.
        local_policy=self.state['policy'];self.state['policy']=self.limits()
        try: plan=self.plan(ids,explicit,confirmed or {})
        finally: self.state['policy']=local_policy
        for entry in plan['files']:
            if entry['reason'] or entry.get('unchanged'): continue
            row=self.asset(entry['id']);path=safe_path(self.source_path(entry['id']));before=path.stat()
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
                if row['blob'] == sha:
                    entry['unchanged'] = True
                row.update(blob=sha,size=size,device=self.state['device'],path=str(path),name=path.name)
                self.remember_copy(entry['id'], path, sha, fingerprint(after))
                entry.update(state='server_available',sha=sha)
            finally: staged.unlink(missing_ok=True)
        # Other files may have taken time to upload since we inspected these sources.
        for entry in plan['files']:
            if entry.get('unchanged'):
                proof = self.state['asset_copies'][entry['id']]
                if fingerprint(safe_path(proof['path']).stat()) != proof['fingerprint']:
                    raise Error('STATE_CONFLICT', 'Source changed during synchronization; retry after writing finishes.')
        if persist and plan['files_eligible']:
            self.save()
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
                observed=integer(reply.get('size'),MAX_METADATA if metadata else effective(self.state['policy'],self.state.get('policy_overrides',[]))['explicit_file'])
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
        copy=self.state.get('asset_copies',{}).get(aid)
        if copy and copy['device']==self.state['device']:
            path=safe_path(copy['path'])
            if path.is_file() and path.stat().st_size==row['size'] and sha_file(path)==row['blob']:
                return {'id':aid,'state':'local_available','path':str(path),'sha':row['blob'],'bytes':row['size'],'project_files_overwritten':False}
            if path.is_file():
                return {'id':aid,'state':'local_reference','path':str(path),'sha':None,'version':'mutable_source',
                        'base_sha':copy['blob'],'server_sha':row['blob'],'copied_bytes':0}
        if row['device']==self.state['device'] and Path(row['path']).exists():
            return {'id':aid,'state':'local_reference','path':row['path'],'copied_bytes':0,
                    'version':'mutable_source','sha':None}
        if not row['blob']: raise Error('SOURCE_PENDING','Source file has not been uploaded; its location is retained.')
        target=self.cache/row['blob']
        if offline and not target.exists(): raise Error('OFFLINE','Fixed file is not cached on this device.')
        path=self.download(row['blob'],row['size'])
        return {'id':aid,'state':'local_available','path':str(path),'sha':row['blob'],'bytes':row['size'],
                'project_files_overwritten':False}
