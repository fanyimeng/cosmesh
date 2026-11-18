"""Frozen whole-store protocol adapter for old clients and rollback only.

Test-only old client used to check compatibility of the retained server protocol.
"""
import json
from cosmesh.errors import Error
from cosmesh.store import atomic
from cosmesh.personal_protocol import (MAX_METADATA, check_id, digest, merge_maps, packed,
                               read_json, save_json, validate_snapshot)


class LegacyWorkspaceSync:
    def snapshot(self):
        _,files=self.wb.base.snapshot()
        bindings=dict(self.state['bindings'])
        bindings[self.state['replica']]={'device':self.state['device'],
            'bindings':self.wb.client.get('bindings',[]),'project_bindings':self.wb.client.get('project_bindings',[])}
        from cosmesh.thread_protocol import references
        from cosmesh.protocol import encode
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
        from cosmesh.status_pages import schedule
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


from cosmesh.personal import Personal as Transport
from cosmesh.personal_protocol import private_dir


class Personal(LegacyWorkspaceSync, Transport):
    def __init__(self, wb):
        # Reproduce old recovery explicitly in the compatibility fixture.
        self.wb=wb;self.root=wb.base.path('.cosmesh/personal-v1')
        pending=read_json(self.root/'apply.json')
        if pending:
            self.state=read_json(self.root/'state.json')
            self.cache=private_dir(self.root/'cache');self.staging=private_dir(self.root/'staging')
            self._apply(pending)
        super().__init__(wb)
