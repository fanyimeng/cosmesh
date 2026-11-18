"""Explicit, restartable offline migration. Original Git and Library are retained."""
import base64
import json
import os
from pathlib import Path
import re
import sqlite3
import tarfile
import time
from contextlib import ExitStack
from uuid import uuid4

from .errors import Error
from .personal_protocol import CHUNK, digest, packed, private_dir, read_json, save_json
from .personal_relay import Relay, initialize
from .protocol import decode
from .thread_protocol import content_version
from .thread_personal_protocol import empty, belongs, validate_manifest
from .store import Store


def put(relay, raw):
    sha=digest(raw)
    relay.dispatch({'method':'personal_begin','sha':sha,'size':len(raw)})
    for offset in range(0,max(1,len(raw)),CHUNK):
        relay.dispatch({'method':'personal_put','sha':sha,'offset':offset,'data':base64.b64encode(raw[offset:offset+CHUNK]).decode()})
    return sha


def sources(hub):
    policies=read_json(hub.root/'policy/repos.json',{})
    result=[]
    for repo,policy in sorted(policies.items()):
        if not policy.get('thread_format'):continue
        store=hub.repo(repo)
        revision=store.git('rev-parse','--verify','refs/heads/main',check=False)
        if revision.returncode:continue
        head=revision.stdout.strip();files=store.snapshot(head)[1]
        docs={p:decode(t) for p,t in files.items() if p.startswith('documents/') and p.endswith('.md')}
        for path,doc in docs.items():
            if doc['meta']['kind']=='thread':result.append((repo,store,head,files,docs,doc))
    return result


def migrate(hub, apply=False, gateways_stopped=False):
    settings=hub.settings();state=read_json(hub.root/'thread-migration.json',{})
    if state.get('state')=='complete':
        if settings.get('thread_access_state')!='ready' and apply:
            settings.update(format=2,thread_access_state='ready');save_json(hub.root/'hub.json',settings)
        return dict(state,replayed=True)
    if not apply:
        items=sources(hub)
        return {'apply':False,'requires_stopped_gateways':True,'threads':[{'thread':d['meta']['id'],'owner':d['meta']['author'],'repo':r} for r,_,_,_,_,d in items],
                'old_programs_after_migration':'refused','source_data_retained':True}
    if not gateways_stopped:raise Error('MAINTENANCE_REQUIRED','Stop all gateways before offline migration; pass --gateways-stopped after doing so.')
    with ExitStack() as stack:
        policies=read_json(hub.root/'policy/repos.json',{})
        for name in sorted(policies):stack.enter_context(hub.locked(name))
        stack.enter_context(hub.locked('thread-access-v1'))
        if not state:
            backup=private_dir(hub.root.parent/(hub.root.name+'-before-access-'+uuid4().hex))
            community=hub.community()
            if not community:raise Error('FEATURE_UNAVAILABLE','Initialize the stable user directory before migration.')
            archive=backup/'sources.tar'
            with tarfile.open(archive,'w') as out:
                out.add(hub.root,arcname='hub')
                out.add(community.root,arcname='library')
            archive.chmod(0o600)
            state={'state':'migrating','backup':str(backup),'format':1,'completed':[]}
            save_json(hub.root/'thread-migration.json',state)
        settings.update(format=2,thread_access_state='migrating')
        save_json(hub.root/'hub.json',settings)
        # A format fence makes every previous Hub runtime refuse this directory.
        initialize(hub)
        community=hub.community();items=sources(hub)
        from .thread_access import FORMAT
        catalog=read_json(hub.root/'thread-access-v1/catalog.json',{'format':FORMAT,'hub_id':'hub-'+uuid4().hex,'threads':{}})
        private_dir(hub.root/'thread-access-v1')
        record_threads={}
        material_sources={}
        for repo,store,head,files,docs,root in items:
            tid=root['meta']['id'];owner=root['meta']['author']
            for d in docs.values():
                if belongs(d,tid):record_threads[(repo,d['meta']['id'])]=tid
            for d in docs.values():
                if belongs(d,tid) and d['meta'].get('title')=='共享资料索引':
                    for ref in d['meta'].get('refs',[]):
                        if ref['repo']=='library':material_sources.setdefault(ref['id'],set()).add(tid)
            if tid in catalog['threads']:
                row=catalog['threads'][tid]
                if row.get('migrated',{}).get('repo')!=repo:
                    raise Error('ID_CONFLICT','A legacy Thread ID collides with another access-managed source; originals and backup are retained.')
                continue
            value=empty(tid)
            value['documents']={p:files[p] for p,d in docs.items() if belongs(d,tid)}
            # All historical paths, including no-longer-current records, are preserved.
            for commit in store.git('rev-list',head).splitlines():
                oldfiles=store.snapshot(commit)[1]
                for path,text in oldfiles.items():
                    if path.startswith('documents/') and path.endswith('.md'):
                        d=decode(text)
                        if belongs(d,tid):value['history'][d['meta']['id']+'@'+content_version(d)]=text
            validate_manifest(value,Store(hub.root/'thread-access-v1/validation'))
            relay=Relay(hub,owner);prior=relay.dispatch({'method':'personal_thread_head','thread':tid})['sha']
            sha=put(relay,packed(value).encode())
            # Preserve earlier private versions and bindings in the owner's stream;
            # only the accepted legacy public snapshot enters the access catalog.
            if tid not in state.setdefault('prior_heads',{}):
                state['prior_heads'][tid]=prior;save_json(hub.root/'thread-migration.json',state)
            execution=relay.dispatch({'method':'personal_execution_head','thread':tid})['sha']
            owner_value={**value,**{key:dict(value[key]) for key in ('documents','history','operations','bindings','assets')}}
            if prior:
                previous=json.loads((relay.blobs/prior).read_text())
                owner_value={**value,**{key:{**previous[key],**value[key]} for key in ('documents','history','operations','bindings','assets')}}
            if execution:
                owner_value['bindings'].update(json.loads((relay.blobs/execution).read_text())['bindings'])
            validate_manifest(owner_value,Store(hub.root/'thread-access-v1/validation'))
            owner_sha=put(relay,packed(owner_value).encode())
            if prior:
                with hub.locked('personal-'+relay.owner[:32]):
                    with sqlite3.connect(str(relay.root/'relay.sqlite3')) as db:
                        current=db.execute('SELECT sha FROM thread_heads WHERE thread=?',(tid,)).fetchone()
                        if not current or current[0]!=prior:raise Error('STATE_CONFLICT','Owner stream changed during offline migration.')
                        db.execute('UPDATE thread_heads SET sha=?,title=? WHERE thread=?',(owner_sha,root['meta']['title'],tid))
                        db.execute('INSERT OR IGNORE INTO thread_history VALUES(?,?,?)',(tid,owner_sha,time.time()))
            else:
                relay.dispatch({'method':'personal_thread_commit','thread':tid,'sha':owner_sha,'expect':None})
            person=community.check(owner)
            catalog['threads'][tid]=dict(owner_id=person['id'],owner_account=owner,visibility='public',readers=[],
                acl_revision=1,sha=sha,title=root['meta']['title'],usage={'current':len(packed(value).encode()),'retained':len(packed(value).encode())},
                objects={sha:len(packed(value).encode())},direct_current=len(packed(value).encode()),history=[sha],
                migration_review=bool(state['prior_heads'][tid]), migrated={'repo':repo,'git_head':head,'prior_private_head':state['prior_heads'][tid]})
            save_json(hub.root/'thread-access-v1/catalog.json',catalog)
            state['completed'].append(tid);save_json(hub.root/'thread-migration.json',state)
        # Resolve Library provenance over every retained version, not only latest.
        with community.db() as db:
            for row in db.execute('SELECT id,doc FROM versions').fetchall():
                doc=json.loads(row['doc']);deps=set(material_sources.get(row['id'],set()))
                refs=list(doc['meta'].get('refs',[]))
                if doc['meta']['resource_kind']=='archive':
                    # Old archives embed cited documents, so their provenance must
                    # include those sources as well as their enclosing Thread.
                    for f in doc['meta']['files']:
                        archive=json.loads(community.blob(f['sha256']).read_text())
                        refs.extend(c['source'] for c in archive.get('citations',[]) if c.get('source'))
                for ref in refs:
                    if ref['repo']=='library':db.execute('INSERT OR IGNORE INTO resource_links VALUES(?,?)',(row['id'],ref['id']))
                    tid=record_threads.get((ref['repo'],ref['id']))
                    if tid:deps.add(tid)
                arc=doc['meta'].get('archive') or {}
                if arc.get('thread') in catalog['threads']:deps.add(arc['thread'])
                if deps:
                    existing=db.execute('SELECT sources FROM resource_scopes WHERE id=?',(row['id'],)).fetchone()
                    if existing:deps.update(json.loads(existing['sources']))
                    db.execute('INSERT OR REPLACE INTO resource_scopes VALUES(?,?,0)',(row['id'],packed(sorted(deps))))
        save_json(hub.root/'thread-access-v1/legacy-records.json',{'records':{r+'@'+i:t for (r,i),t in record_threads.items()}})
        state.update(state='complete',thread_count=len(items),library_inheritance=True)
        save_json(hub.root/'thread-migration.json',state)
        settings['thread_access_state']='ready';save_json(hub.root/'hub.json',settings)
        return state
