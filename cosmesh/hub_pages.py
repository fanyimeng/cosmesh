"""Prepare public conversation documents on writes; RPC reads files only."""
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from .errors import Error
from .store import Store, atomic, safe_path
from .threads import Workbench, now


def root(hub):
    path=hub.root/'views';path.mkdir(mode=0o700,exist_ok=True)
    if path.is_symlink() or path.stat().st_uid!=os.geteuid() or path.stat().st_mode&0o077:
        raise Error('AUTH_DENIED','Prepared Hub views require a private owned directory.')
    return path


def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(value):return hashlib.sha256(packed(value).encode()).hexdigest()
def pair(actor,peer,progress=False):return sha([actor,peer,progress])


def schedule(hub):
    """Best-effort finite program, never a public Agent or polling service."""
    try:
        path=root(hub);atomic(path/'dirty.json',packed({'generation':now()}))
        if os.environ.get('COSMESH_PAGE_BACKGROUND')=='0':return
        code='import sys;sys.path.insert(0,%r);from cosmesh.hub_pages import worker;worker(%r)'%(
            str(Path(__file__).resolve().parent.parent),str(hub.root))
        child=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
        from threading import Thread
        Thread(target=child.wait,daemon=True).start()
    except (Error,OSError):pass


def public_workbench(hub):
    """Read fixed accepted bare snapshots without clones or personal metadata."""
    from .hub import BareStore
    from .thread_protocol import Snapshot, reference_edges
    from .status_pages import FORMAT
    class Accepted(BareStore):
        def client(self):return {'remote_revision':self.revision}
    class Public(Workbench):
        def status(self):return {'state':'synced','identity_verified':True,'last_sync':self.client['last_sync']}
    community=hub.community()
    if not community:raise Error('FEATURE_UNAVAILABLE','Prepared conversations require the existing user directory.')
    info=community.snapshot(community.admin)
    library=json.loads(community.blob(info['sha256']).read_text())
    parent=safe_path(os.environ.get('COSMESH_HUB_PAGE_CACHE',str(Path(tempfile.gettempdir()).resolve()/('cosmesh-hub-views-'+str(os.geteuid())))))
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    directory=parent/sha(str(hub.root));directory.mkdir(mode=0o700,exist_ok=True)
    for p in (directory.parent,directory):
        if p.stat().st_uid!=os.geteuid() or p.stat().st_mode&0o077:raise Error('AUTH_DENIED','Hub indexes stay host-local and private.')
    wb=Public.__new__(Public);wb.base=Store(directory/'source')
    kb=wb.base.path('.cosmesh/knowledge');kb.mkdir(mode=0o700,parents=True,exist_ok=True)
    atomic(kb/'snapshot.json',packed(library))
    wb.user=community.admin;wb.config={'user':wb.user,'timezone':'Asia/Shanghai'}
    wb.client={'actor':wb.user,'spaces':{},'bindings':[],'project_bindings':[],
               'last_sync':{'at':now(),'complete':True,'publish':False,'errors':[]}}
    wb.catalog={};wb.stores={};wb._models={};wb._revisions={};wb._accepted_models={};wb._resolved={}
    policies=json.loads((hub.root/'policy/repos.json').read_text())
    for name,policy in policies.items():
        if not policy.get('thread_format'):continue
        store=Accepted(hub.repo(name).root)
        head=store.git('rev-parse','--verify','refs/heads/main',check=False)
        if head.returncode:continue
        store.revision=head.stdout.strip();files=store.snapshot(store.revision)[1];model=Snapshot(store,files)
        wb.client['spaces'][name]={'owner':policy['owner']};wb.stores[name]=store
        wb._models[name]=(store.revision,files,model);wb._revisions[name]=store.revision
        for identifier,doc in model.docs.items():
            if identifier in wb.catalog:raise Error('ID_CONFLICT','Duplicate authoritative public record.')
            wb.catalog[identifier]={'doc':doc,'repo':name,'store':store}
    wb._incoming={};wb._records={}
    for item in wb.catalog.values():
        meta=item['doc']['meta']
        if meta.get('thread'):wb._records.setdefault(meta['thread'],[]).append(item)
        for relation,ref in reference_edges(meta):wb._incoming.setdefault((ref['repo'],ref['id']),[]).append((item,relation,ref))
    for values in wb._records.values():values.sort(key=lambda x:(x['doc']['meta']['created_at'],x['doc']['meta']['id']))
    from .thread_names import rebuild
    rebuild(wb)
    token=sha([FORMAT,wb._revisions,info['sha256']]);wb.public_tokens={k:token for k in ('all','base','mesh')}
    return wb,library['users']


def build(hub):
    from .status_pages import rebuild,cache_root,database,meta
    from .contact_chat import read
    from .thread_chat import document
    path=root(hub);generation=json.loads((path/'dirty.json').read_text())['generation'] if (path/'dirty.json').is_file() else now()
    wb,users=public_workbench(hub);index=cache_root(wb);rebuild(wb,index)
    pairs=set()
    for item in wb.catalog.values():
        m=item['doc']['meta']
        if m.get('type')=='request' and m['author']!=m['to']:
            pairs.update(((m['author'],m['to']),(m['to'],m['author'])))
    manifest={'format':1,'generation':generation,'generated_at':now(),'frames':{},'users':users}
    with database(index) as con:
        for actor,peer in sorted(pairs):
            for progress in (False,True):
                args=SimpleNamespace(contact=peer,rounds=5,before=None,date=None,include_progress=progress)
                identity={'actor':actor,'local':actor,'timezone':'Asia/Shanghai'}
                freshness={'generated_at':manifest['generated_at'],'remote_checked_at':manifest['generated_at'],
                           'local_sources_current':True,'source_token':wb.public_tokens['mesh']}
                packet=read(con,args,identity,users,freshness)
                window=con.execute('SELECT body FROM pages WHERE key=?',(packet['view'],)).fetchone()[0]
                name=packet['view'].split('contact-',1)[1]
                atomic(path/('window-'+name+'.json'),window)
                key=pair(actor,peer,progress);frame='frame-'+key+'-'+name+'.json'
                atomic(path/frame,packed(packet));atomic(path/(frame+'.md'),document(packet))
                manifest['frames'][key]={'frame':frame,'window':'window-'+name+'.json'}
    atomic(path/'index.json',packed(manifest))
    return manifest


def worker(hub_root):
    from .hub import Hub
    hub=Hub(hub_root);path=root(hub)
    with (path/'worker.lock').open('a+') as lock:
        try:fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            code='import sys;sys.path.insert(0,%r);from cosmesh.hub import Hub;from cosmesh.hub_pages import build;build(Hub(%r))'%(
                str(Path(__file__).resolve().parent.parent),str(hub.root))
            try:subprocess.run([sys.executable,'-c',code],timeout=max(.1,deadline-time.monotonic()),check=True,
                               stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except (subprocess.SubprocessError,OSError):return
            manifest=json.loads((path/'index.json').read_text())
            if json.loads((path/'dirty.json').read_text())['generation']==manifest['generation']:return
            if time.monotonic()>=deadline:return


def fetch(hub,payload,actor):
    """No Git, workbench construction or attachment access on this RPC path."""
    from .knowledge import select_user
    from .contact_chat import packet,decode_cursor
    from .thread_chat import document
    if not hub.community():raise Error('FEATURE_UNAVAILABLE','This Hub has no shared user directory.')
    if set(payload)-{'method','peer','rounds','before','date','include_progress','timezone'}:
        raise Error('INPUT_INVALID','Unsupported prepared-view options.')
    count=payload.get('rounds',5)
    if type(count)is not int or not 1<=count<=20:raise Error('INPUT_INVALID','Use 1 to 20 rounds.')
    from datetime import datetime
    from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
    try:zone=ZoneInfo(payload.get('timezone','Asia/Shanghai'))
    except (ValueError,TypeError,ZoneInfoNotFoundError):raise Error('INPUT_INVALID','Use an available IANA timezone.')
    path=root(hub)
    if not (path/'index.json').is_file():
        schedule(hub);return {'ready':False,'reason':'preparing'}
    index=json.loads((path/'index.json').read_text());peer=select_user(index['users'],payload.get('peer'))
    if peer['account']==actor:raise Error('INPUT_INVALID','Choose another user.')
    progress=payload.get('include_progress',False)
    if type(progress)is not bool:raise Error('INPUT_INVALID','Use a boolean shared-progress flag.')
    saved=index['frames'].get(pair(actor,peer['account'],progress))
    if not saved:
        current=not (path/'dirty.json').is_file() or json.loads((path/'dirty.json').read_text())['generation']==index['generation']
        return {'ready':True,'data':{'format':'contact.chat.v1','title':'与'+(peer['name'] or peer['account'])+'的交流',
                'overall':'暂无双方交流成品。' if current else '交流成品正在更新。','peer':peer,'messages':[],'rounds':[],'total_rounds':0,
                'source_index':[],'private':False,'next_cursor':None,'view':None,'date':payload.get('date'),'truncated':False,
                'freshness':{'generated_at':index['generated_at'],'remote_checked_at':index['generated_at'],
                             'server_current':current,'updating':not current},'transport':'prepared_hub_document'}}
    if payload.get('before') or payload.get('date') or count!=5:
        token=decode_cursor(payload['before']) if payload.get('before') else None
        filename='window-'+token['view'].split('contact-',1)[1]+'.json' if token else saved['window']
        source=path/filename
        if not source.is_file():raise Error('VIEW_EXPIRED','Reopen the latest conversation.')
        view=json.loads(source.read_text())
        if view['actor']!=actor or view['peer']['account']!=peer['account'] or view['include_progress']!=progress:
            raise Error('AUTH_DENIED','This prepared window belongs to another conversation.')
        if token and view['date']!=payload.get('date'):
            raise Error('INPUT_INVALID','Continue the same date window with this cursor.')
        if payload.get('date'):
            from datetime import date
            try:date.fromisoformat(payload['date'])
            except (ValueError,TypeError):raise Error('INPUT_INVALID','Use YYYY-MM-DD.')
            selected=[]
            for round_ in view['rounds']:
                turns=[[r for r in turn if datetime.fromisoformat(r['created_at']).astimezone(zone).date().isoformat()==payload['date']] for turn in round_['turns']]
                turns=[turn for turn in turns if turn]
                if turns:selected.append({'turns':turns,'open':len(turns)==1})
            view={**view,'rounds':selected,'date':payload['date'],'timezone':zone.key}
            name=sha([view['actor'],view['peer'],view['date'],progress,view['rounds'],view['timezone']])
            filename='window-'+name+'.json';atomic(path/filename,packed(view))
        key='mesh/contact-'+filename[len('window-'):-len('.json')]
        result=packet(view,key,token['offset'] if token else 0,count)
    else:result=json.loads((path/saved['frame']).read_text())
    for message in result['messages']:
        message['local_time']=datetime.fromisoformat(message['created_at']).astimezone(zone).strftime('%Y-%m-%d %H:%M')
    for repo in {ref['repo'] for ref in result['source_index'] if ref['repo']!='library'}:hub.policy(repo,actor)
    current=not (path/'dirty.json').is_file() or json.loads((path/'dirty.json').read_text())['generation']==index['generation']
    result['freshness'].update(server_current=current,updating=not current)
    result['transport']='prepared_hub_document'
    return {'ready':True,'data':result}
