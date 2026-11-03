"""One local client surface for the shared library, people and contribution history."""
import json
from pathlib import Path
from uuid import uuid4
from .errors import Error
from .knowledge import Knowledge
from .threads import Workbench,workspace_root
from .protocol import public


def add_commands(commands,command):
    users=command(commands,'user').add_subparsers(dest='user_command',required=True)
    command(users,'me','user.me');command(users,'list','user.list')
    for verb in ('show','overview','contributions'):
        p=command(users,verb,'user.'+verb);p.add_argument('person')
    p=command(users,'set-name','user.set_name');p.add_argument('name');p.add_argument('--expect')
    p=command(users,'manage','user.manage');p.add_argument('person');p.add_argument('--expect',required=True)
    p.add_argument('--status',choices=('active','disabled'));p.add_argument('--role',choices=('member','admin'))
    library=command(commands,'library').add_subparsers(dest='library_command',required=True)
    command(library,'sync','library.sync');command(library,'status','library.status')
    command(library,'reindex','library.reindex')
    command(library,'pending','library.pending')
    p=command(library,'abandon','library.abandon');p.add_argument('key');p.add_argument('--reason',required=True)
    p=command(library,'list','library.list');p.add_argument('--archives',action='store_true');p.add_argument('--all',action='store_true')
    p=command(library,'search','library.search');p.add_argument('query');p.add_argument('--full-text',action='store_true')
    for verb in ('show','text','history','archive-show','backlinks'):
        p=command(library,verb,'library.'+verb);p.add_argument('id')
    p=command(library,'fetch','library.fetch');p.add_argument('id');p.add_argument('--name',required=True);p.add_argument('--output',required=True)
    p=command(library,'open','library.open');p.add_argument('reference');p.add_argument('--launch',action='store_true')
    for verb in ('add','revise'):
        p=command(library,verb,'library.'+verb)
        if verb=='revise':p.add_argument('id');p.add_argument('--expect',required=True)
        p.add_argument('--title');p.add_argument('--body',default=None);p.add_argument('--file',action='append',default=[])
        p.add_argument('--kind',choices=('document','reference'));p.add_argument('--status',choices=('draft','published','withdrawn'))
        p.add_argument('--doi');p.add_argument('--arxiv');p.add_argument('--authors');p.add_argument('--ref',action='append',default=[])
        p.add_argument('--generation-file');p.add_argument('--sync',action='store_true')
    p=command(library,'transfer','library.transfer');p.add_argument('id');p.add_argument('--to',required=True);p.add_argument('--expect',required=True);p.add_argument('--sync',action='store_true')


def execute(args):
    if getattr(args,'revision',None) or getattr(args,'expect_revision',None) or getattr(args,'dry_run',False):
        raise Error('INPUT_INVALID','Knowledge uses explicit content versions; preview files before adding them.')
    from .cli import read_input
    offline=getattr(args,'offline',False);fresh=getattr(args,'require_fresh',False)
    network=args.action in {'library.sync','library.reindex','user.set_name','user.manage'} or getattr(args,'sync',False) or fresh
    if offline and network:raise Error('INPUT_INVALID','Offline reads and local drafts cannot request server operations.')
    wb=Workbench(workspace_root(getattr(args,'root',None)))
    with wb.base.locked():
        wb.load();kb=Knowledge(wb)
        if fresh:
            result=wb.synchronize(publish=False)
            if result['errors']:raise Error('FRESHNESS_UNAVAILABLE','Some shared sources could not be refreshed.')
            kb=Knowledge(wb)
        action=args.action;key=getattr(args,'idempotency_key',None)
        if action=='user.set_name':
            result=kb.rpc({'method':'community_name','name':args.name,'expect':args.expect,'key':key or str(uuid4())});kb.refresh();return result,None
        if action in {'user.me','user.list','user.show','user.overview','user.contributions','user.manage'}:
            if action=='user.manage':
                value={'user':args.person,'expect':args.expect}
                for field in ('status','role'):
                    if getattr(args,field) is not None:value[field]=getattr(args,field)
                result=kb.apply('manage_user',value,key);kb.refresh();return result,None
            if not kb.data['users']:
                if offline:raise Error('DIRECTORY_NOT_CACHED','Pull the user directory once while connected.')
                kb.refresh()
            if action=='user.list':return kb.data['users'],None
            person=wb.user if action=='user.me' else args.person
            if action in {'user.overview','user.contributions'}:return kb.overview(person),None
            result=kb.user(person)
            return {**result,'needs_real_name':not bool(result['name'])},None
        if action=='library.sync':return kb.synchronize(),None
        if action=='library.reindex':
            result=kb.rpc({'method':'community_reindex'});kb.refresh();return result,None
        if action=='library.pending':return [json.loads(x.read_text()) for x in sorted(kb.outbox().glob('*.json'))],None
        if action=='library.abandon':
            from .community import digest,packed
            from .store import atomic
            from .threads import now
            path=kb.outbox()/(digest(args.key.encode())+'.json')
            if not path.exists():raise Error('NOT_FOUND','No pending operation with this key.')
            request=json.loads(path.read_text());saved=kb.root/('abandoned-'+path.name)
            atomic(saved,packed({'request':request,'reason':args.reason,'at':now()})+'\n');path.unlink()
            return {'key':args.key,'abandoned':True,'record_preserved':True},None
        if action=='library.status':return {'items':len(kb.data['heads']),'pending':len(list(kb.outbox().glob('*.json'))),'as_of':kb.data.get('synced_at'),'archive_errors':kb.data.get('archive_errors',[]),'independent_server_storage':True},None
        if action in {'library.add','library.revise','library.transfer'}:
            value={'id':'K-'+str(uuid4())} if action=='library.add' else {'id':kb.doc(args.id)['meta']['id'],'expect':args.expect}
            operation={'library.add':'resource_add','library.revise':'resource_revise','library.transfer':'resource_transfer'}[action]
            if action=='library.transfer':value['maintainer']=kb.user(args.to)['id']
            else:
                if action=='library.add' and not args.title:raise Error('INPUT_INVALID','A knowledge title is required.')
                for field in ('title','body','kind','status'):
                    if getattr(args,field) is not None:value[field]=getattr(args,field)
                if args.file:value['files']=kb.import_files(args.file)
                if args.ref:
                    value['refs']=[wb.citation(x) for x in args.ref]
                    if any(x['repo']=='local' for x in value['refs']):raise Error('PRIVATE_REFERENCE','Share source work explicitly before citing it in the shared library.')
                bibliography={k:getattr(args,k) for k in ('doi','arxiv','authors') if getattr(args,k) is not None}
                if bibliography:value['bibliography']=bibliography
                if args.generation_file:
                    value['generation']=json.loads(read_input(args.generation_file,65536))
                else:value['generation']={'tool':'unspecified','verification':'not_provided'}
            result=kb.stage(operation,value,key)
            if getattr(args,'sync',False):result={'result':result,'publication':kb.synchronize()}
            return result,None
        if action=='library.search':return kb.search(args.query,args.full_text,offline),None
        if action=='library.open':
            from .material_links import open_file
            return open_file(kb,args.reference,offline,args.launch),None
        if action=='library.list':
            items=[kb.present(kb.versions[(k,v)]) for k,v in kb.data['heads'].items()]
            if args.archives:items=[x for x in items if x['resource_kind']=='archive']
            if not args.all:items=[x for x in items if x['status']=='published']
            for item in items:
                item['author_name']=next((u['name'] for u in kb.data['users'] if u['id']==item['author_id']),None)
                if item['archive']:
                    try:item['current_thread']=wb.present(wb.find(item['archive']['thread']))
                    except Error:item['current_thread']=None
            return items,None
        if action=='library.history':
            item=kb.doc(args.id);return [kb.present(d) for (k,v),d in kb.versions.items() if k==item['meta']['id']],None
        if action=='library.backlinks':return kb.backlinks(args.id),None
        if action=='library.text':return kb.text(args.id,offline),None
        if action=='library.archive-show':
            doc=kb.doc(args.id)
            if doc['meta']['resource_kind']!='archive':raise Error('INPUT_INVALID','Choose a Thread archive item.')
            f=doc['meta']['files'][0];path=kb.root/f['sha256']
            if not path.exists() and offline:raise Error('CONTENT_NOT_CACHED','Fetch this archive once while connected.')
            raw=path.read_bytes() if path.exists() else kb.download(f['sha256'],f['size'])
            return json.loads(raw),None
        if action=='library.fetch':
            doc=kb.doc(args.id);file=next((x for x in doc['meta']['files'] if x['name']==args.name),None)
            if not file:raise Error('NOT_FOUND','No file with this name in this version.')
            output=Path(args.output).expanduser()
            if output.exists() or output.is_symlink():raise Error('PATH_CONFLICT','Choose a new output file; existing content is preserved.')
            if offline and not (kb.root/file['sha256']).exists():raise Error('CONTENT_NOT_CACHED','This file is not cached locally.')
            raw=kb.download(file['sha256'],file['size'])
            with output.open('xb') as handle:handle.write(raw)
            return {'path':str(output.absolute()),'sha256':file['sha256'],'bytes':len(raw)},None
        if action=='library.show':
            usage=kb.backlinks(args.id)
            return {**usage,'events':[x for x in kb.data['events'] if x['object']==usage['source']['id']]},None
        raise Error('INPUT_INVALID','Unknown knowledge command.')
