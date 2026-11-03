"""Publish selected small documents before their Thread, using existing records."""
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from .errors import Error
from .knowledge_protocol import EXTENSIONS,MAX_FILE,MAX_TOTAL
from .material_links import filename,html_ready,link,parse,relative_links
from .store import atomic
from .thread_protocol import new_id
from .threads import now

SKIP_SUFFIXES=('.fits','.fit','.fts','.fits.gz','.h5','.hdf5','.hdf','.npy','.npz','.ms','.zarr','.parquet','.nc',
               '.zip','.tar','.tar.gz','.tgz','.7z','.gz','.bz2','.xz')
PRIVATE_PATH=re.compile(r'(?<![\w:/])(?:/(?:Users|home|private|tmp|srv|mnt|Volumes|data\d+)(?:/[^\s`<>\[\]()，。；；]+)+|[A-Za-z]:\\[^\s`<>\[\]()]+)')
FILE_TOKEN=re.compile(r'(?<![\w:/])(?:[\w.\-]+/)*[\w.\-]+\.(?:md|txt|pdf|docx?|pptx?|html?|json|csv|tsv|png|jpe?g|svg|bib|py|fits(?:\.gz)?|h5|hdf5|npy|npz|zip|tar\.gz)\b',re.I)


def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(packed(value).encode()).hexdigest()


def code_reference(value):
    if value is None:return None
    from urllib.parse import urlsplit
    if not isinstance(value,dict) or set(value)-{'url','commit','paths'} or not {'url','commit'}<=set(value):
        raise Error('INPUT_INVALID','Code uses a repository url, fixed commit and optional relative paths.')
    if not isinstance(value['url'],str) or len(value['url'])>2000 or not isinstance(value['commit'],str):
        raise Error('INPUT_INVALID','Provide a bounded repository URL and fixed commit.')
    url=urlsplit(value['url'])
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise Error('INPUT_INVALID','Share an HTTPS repository URL without embedded credentials or private SSH mappings.')
    if not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}',value['commit']):
        raise Error('INPUT_INVALID','Pin code to its complete Git commit, not a mutable branch.')
    paths=value.get('paths',[])
    if not isinstance(paths,list) or len(paths)>32 or any(not isinstance(p,str) or len(p)>1000 or p.startswith(('/','\\')) or '..' in p.split('/') or '\\' in p or '\n' in p for p in paths):
        raise Error('PATH_INVALID','Code paths must be bounded paths relative to the Git repository.')
    return {'url':value['url'],'commit':value['commit'].lower(),'paths':paths,'access':'not_verified_for_other_users'}


def selection(args):
    files=[{'path':p,'required':True} for p in args.attach];code=None
    if args.materials:
        path=Path(args.materials).expanduser()
        if path.stat().st_size>1024*1024:raise Error('INPUT_TOO_LARGE','Keep the private file selection below 1 MiB.')
        value=json.loads(path.read_text())
        if not isinstance(value,dict) or set(value)-{'format','files','code'} or value.get('format')!=1 or not isinstance(value.get('files',[]),list):
            raise Error('INPUT_INVALID','Use format=1, files and optional code in the private material selection.')
        for f in value.get('files',[]):
            if isinstance(f,str):f={'path':f}
            if not isinstance(f,dict) or set(f)-{'path','name','required'} or not isinstance(f.get('path'),str) or type(f.get('required',True))is not bool:
                raise Error('INPUT_INVALID','Each selected file uses path, optional name and required boolean.')
            f=dict(f);p=Path(f['path']).expanduser();f['path']=str(p if p.is_absolute() else path.absolute().parent/p);files.append(f)
        code=value.get('code')
    if args.git or args.commit or args.code_path:
        if code:raise Error('INPUT_INVALID','Provide code once, through flags or the material selection.')
        code={'url':args.git,'commit':args.commit,'paths':args.code_path}
    if len(files)>256:raise Error('INPUT_TOO_LARGE','Select at most 256 explicit files; directories are not scanned.')
    return files,code_reference(code)


def preflight(files):
    accepted=[];skipped=[];total=0;seen=set()
    for f in files:
        path=Path(f['path']).expanduser().absolute();name=filename(f.get('name',path.name));required=f.get('required',True)
        key=(str(path),name)
        if key in seen:continue
        seen.add(key);reason=None;size=None
        if any(path.name.casefold().endswith(s) for s in SKIP_SUFFIXES):reason='data_or_archive_suffix'
        elif path.suffix.casefold() not in EXTENSIONS:reason='unsupported_document_type'
        elif path.is_symlink():reason='symbolic_link'
        elif not path.is_file():reason='file_unavailable'
        else:
            size=path.stat().st_size
            if size>MAX_FILE:reason='file_exceeds_20_MiB'
            elif total+size>MAX_TOTAL:reason='share_exceeds_100_MiB'
            elif len(accepted)>=32:reason='more_than_32_files'
            elif name in {x['name'] for x in accepted}:raise Error('INPUT_INVALID','Choose distinct public filenames for selected documents.')
            elif path.suffix.lower() in {'.html','.htm','.svg'} and not html_ready(path.read_bytes()):reason='html_needs_self_contained_resources'
        if reason:skipped.append({'name':name,'reason':reason,'bytes':size,'required':required});continue
        accepted.append({'path':str(path),'input':f['path'],'name':name,'bytes':size,'required':required});total+=size
    return accepted,skipped,total


def unresolved(text,files,code):
    known={x.get('name') or Path(x['path']).name for x in files}|{Path(x['path']).name for x in files}
    result=[]
    for match in FILE_TOKEN.finditer(text):
        token=match.group();name=token.rsplit('/',1)[-1]
        if name in known or token.startswith(('http:','https:')):continue
        if code and (name.endswith('.py') or any(token.startswith(p.rstrip('/')+'/') or token==p for p in code['paths'])):continue
        if name not in result:result.append(name)
    return result[:64]


def rewrite(text,mapping):
    # Replace explicit selected paths once; retain private originals unchanged.
    if mapping:
        alternatives='|'.join(re.escape(p) for p in sorted(mapping,key=len,reverse=True))
        pattern=re.compile(r'(?<![\w/])`?(?:[\w.\-]+/)*(?:'+alternatives+r')`?(?![\w/])')
        def replace(m):
            name=m.group().strip('`');uri,label=mapping.get(name,mapping.get(name.rsplit('/',1)[-1]))
            if m.start()>0 and text[m.start()-1]=='(' and m.end()<len(text) and text[m.end()]==')':return uri
            return '['+label.replace('[','').replace(']','')+']('+uri+')'
        text=pattern.sub(replace,text)
    text=re.sub(r'(?ms)^##[ \t]+(?:执行环境|私人定位|本机执行)[^\n]*\n.*?(?=^##[ \t]|\Z)','',text)
    return PRIVATE_PATH.sub('〔私人定位省略〕',text)


def index_body(files,skipped,missing,code):
    complete=not missing and not any(f['required'] for f in skipped)
    lines=['## 共享资料','', '资料状态：'+('已入库，可按固定引用打开。' if complete else '资料待补；已入库部分可打开，以下列明缺项。')]
    for f in files:lines.append('- ['+f['name'].replace('[','').replace(']','')+']('+f['reference']+')')
    if not files:lines.append('- 本次没有新增文档原件。')
    if code:
        lines+=['','代码仓库：'+code['url'],'固定 commit：'+code['commit'],
                '仓库访问状态：未代其他用户核验。']+['- '+p for p in code['paths']]
    if skipped:lines+=['','跳过文件：']+['- '+f['name']+'：'+f['reason'] for f in skipped]
    if missing:lines+=['','原文路径尚无共享原件：']+['- '+x for x in missing]
    return '\n'.join(lines)+'\n',complete


def ensure_private_state(wb):
    exclude=wb.base.path('.git/info/exclude');entry='.cosmesh/share-materials/'
    if entry not in exclude.read_text().splitlines():atomic(exclude,exclude.read_text()+'\n'+entry+'\n')
    path=wb.base.path(entry);path.mkdir(mode=0o700,exist_ok=True)
    if path.stat().st_mode&0o077 or path.stat().st_uid!=os.geteuid():raise Error('AUTH_DENIED','Share continuation receipts must be private.')
    return path


def check_pending(wb,tid):
    repo=wb.client.get('own_repo')
    if repo not in wb.stores:raise Error('HUB_CONNECTION_REQUIRED','Discover your Mesh space before publishing.')
    own=wb.stores[repo];accepted=own.client().get('remote_revision')
    if accepted and accepted!=own.head:
        changed=own.git('diff','--name-only',accepted,own.head,'--','documents/').splitlines()
        allowed={tid,*[r['doc']['meta']['id'] for r in wb.records(tid)]}
        if any(Path(path).stem not in allowed for path in changed):
            raise Error('UNRELATED_PENDING','This Mesh space contains unrelated unpublished work. Finish its intended synchronization before sharing this Thread.')


def publish(wb,args,key=None,dry_run=False):
    from .knowledge import Knowledge
    item=wb.find(args.id,'thread');tid=item['doc']['meta']['id']
    if getattr(wb,'generation',None):wb.generation={k:PRIVATE_PATH.sub('〔私人定位省略〕',v) for k,v in wb.generation.items()}
    if item['doc']['meta']['author'] not in {wb.config['user'],wb.user}:raise Error('AUTH_DENIED','The creator shares the Thread and its supporting materials.')
    requested=bool(args.attach or args.materials or args.resource or args.git or args.commit or args.code_path)
    if args.summary_only and requested:raise Error('INPUT_INVALID','Choose a text-only brief or explicitly selected related files/code.')
    if not requested:
        material={'state':'not_requested','files':[],'skipped':[],'bytes':0,'upload_policy':'explicit_only',
                  'hint':'本次只共享工作简报，未新增上传关联文件；可在明确要求后补传，已有共享资料保持。'}
        if dry_run:return {'thread':tid,'materials':material,'dry_run':True,'saved_locally':False}
        if args.sync:check_pending(wb,tid)
        result=wb.share(tid,key,rewrite=lambda value:rewrite(value,{}),materials='summary_only_v1',
                        overview_notice='> 共享范围：工作简报。关联文件按需另行共享；已有知识库引用可继续使用。')
        result={**result,'materials':material}
        if args.sync:
            wb.load();return {'result':result,'publication':wb.synchronize(knowledge_keys=[],publish_repos=[wb.client['own_repo']])}
        return result
    source_files,code=selection(args);chosen,skipped,total=preflight(source_files)
    text='\n'.join(x['doc']['body'] for x in [item,*wb.records(tid)])
    missing=unresolved(text,[*source_files,*[{'path':parse(r)[1]} for r in args.resource]],code)
    preview={'files':[{'name':x['name'],'bytes':x['bytes']} for x in chosen],'skipped':skipped,'unresolved':missing,
             'bytes':total,'limits':{'file':MAX_FILE,'share':MAX_TOTAL},'code':code}
    known={x['name'] for x in chosen}
    for f in chosen:
        if Path(f['path']).suffix.lower()=='.md':
            for dependency in relative_links(Path(f['path']).read_bytes()):
                if Path(dependency).is_absolute() or '..' in Path(dependency).parts or Path(dependency).name not in known:
                    if Path(dependency).name not in missing:missing.append(Path(dependency).name)
    if dry_run:return {'thread':tid,'materials':preview,'dry_run':True,'saved_locally':False}
    if not args.sync:raise Error('SYNC_REQUIRED','Use --sync to publish selected knowledge files before the Thread, or --dry-run to inspect locally.')
    kb=Knowledge(wb)
    if not wb.client.get('own_repo') or not wb.client.get('community_enabled'):
        raise Error('HUB_CONNECTION_REQUIRED','Discover the Hub knowledge service before sharing documents.')
    check_pending(wb,tid)
    # Freeze bytes before the first upload. Neither selected directories nor data assets are traversed.
    imported=kb.import_files([f['path'] for f in chosen])
    for f,source in zip(imported,chosen):
        if f['size']!=source['bytes']:raise Error('STATE_CONFLICT','A selected file changed during preparation; repeat the preview.')
        f['name']=source['name']
        if Path(source['path']).suffix.lower() in {'.html','.htm'} and not html_ready((kb.root/f['sha256']).read_bytes()):
            raise Error('STATE_CONFLICT','HTML changed after the preview; prepare a self-contained document before sharing.')
    identity={'thread':tid,'files':[{k:f[k] for k in ('name','sha256','size')} for f in imported],
              'resources':args.resource,'code':code,'skipped':skipped,'missing':missing,'summary_only':args.summary_only,'generation':getattr(wb,'generation',None)}
    fingerprint=digest(identity);token=digest(key) if key else fingerprint
    directory=ensure_private_state(wb);receipt=directory/(token+'.json')
    prior=json.loads(receipt.read_text()) if receipt.exists() else {}
    if prior and prior['fingerprint']!=fingerprint:raise Error('IDEMPOTENCY_CONFLICT','This share key identifies a different material selection.')
    if prior.get('result'):return {**prior['result'],'replayed':True}
    kb.refresh();refs=[];published=[]
    for value in args.resource:
        ref,name=parse(value);doc=kb.doc(ref['id'],ref['version'])
        f=next((x for x in doc['meta']['files'] if x['name']==name),None)
        if f is None:raise Error('REFERENCE_UNAVAILABLE','A selected fixed knowledge file is absent.')
        refs.append(ref);published.append({'name':name,'reference':value,'sha256':f['sha256']})
    reused=False
    if imported:
        signatures=sorted((f['name'],f['sha256'],f['size']) for f in imported)
        existing=next((kb.versions[(kid,v)] for kid,v in kb.data['heads'].items()
                       if kb.versions[(kid,v)]['meta']['status']=='published' and kb.versions[(kid,v)]['meta']['resource_kind']=='document' and
                       sorted((f['name'],f['sha256'],f['size']) for f in kb.versions[(kid,v)]['meta']['files'])==signatures),None)
        if existing:kid=existing['meta']['id'];reused=True
        else:
            operation_key='share-materials-'+token
            value={'id':prior.get('knowledge_id') or 'K-'+str(uuid4()),'title':(item['doc']['meta']['title']+' · 共享资料')[:200],
                   'body':'为工作 '+tid+' 提供可按固定版本打开的说明、报告与小型结果。','kind':'document','status':'published','files':imported,
                   'generation':getattr(wb,'generation',{'tool':'unspecified','verification':'not_provided'})}
            operation='resource_add'
            latest=directory/(tid+'.json')
            if prior.get('operation_value'):
                value=prior['operation_value'];operation=prior['operation']
            elif latest.exists():
                old=kb.doc(json.loads(latest.read_text())['id'])
                if old['meta']['maintainer']==wb.user:
                    value.update(id=old['meta']['id'],expect=kb.ref(old['meta']['id'])['version']);operation='resource_revise'
            state={'fingerprint':fingerprint,'knowledge_id':value['id'],'operation':operation,'operation_value':value}
            prior.update(state);atomic(receipt,packed(prior)+'\n');receipt.chmod(0o600)
            kid=kb.stage(operation,value,operation_key)['id']
            uploaded=kb.synchronize([operation_key],refresh=False)
            if not uploaded['synchronized']:raise Error('MATERIAL_UPLOAD_PENDING','Selected files remain staged; retry with the same key. No Thread has been newly shared.',packed(uploaded['errors']))
        atomic(directory/(tid+'.json'),packed({'id':kid})+'\n')
        ref=kb.ref(kid);refs.append(ref)
        published.extend({'name':f['name'],'reference':link(ref,f['name']),'sha256':f['sha256']} for f in imported)
    body,complete=index_body(published,skipped,missing,code)
    by_name={f['name']:f for f in published};mapping={}
    for f in chosen:
        public=by_name[f['name']]
        for name in (f['path'],f['input'],Path(f['path']).name,f['name']):mapping[name]=(public['reference'],public['name'])
    for f in published:mapping.setdefault(f['name'],(f['reference'],f['name']))
    transform=lambda value:rewrite(value,mapping)
    operation='share-'+token
    index_id=prior.get('index_id') or new_id('entry')
    prior.update(fingerprint=fingerprint,index_id=index_id)
    if imported:prior['knowledge_id']=kid
    atomic(receipt,packed(prior)+'\n');receipt.chmod(0o600)
    if index_id in wb.catalog:
        result={'thread':tid,'repo':item['repo'],'materials_index':index_id,'replayed':True}
    elif item['repo']=='local':
        extra={'meta':{'schema_version':2,'kind':'entry','id':index_id,'author':wb.config['user'],'created_at':now(),
                       'title':'共享资料索引','thread':tid,'type':'note','refs':list({packed(r):r for r in refs}.values())},'body':body}
        if getattr(wb,'generation',None):extra['meta']['generation']=wb.generation
        result=wb.share(tid,operation,extra=extra,rewrite=transform,materials=fingerprint)
    else:
        changed=transform(item['doc']['body'])
        if changed!=item['doc']['body']:wb.edit(tid,wb.ref(item)['version'],body=changed,key=operation+'-overview');wb.load()
        note=wb.append(tid,body,'note',refs=list({packed(r):r for r in refs}.values()),key=operation+'-index')
        result={'thread':tid,'repo':item['repo'],'materials_index':note['id']}
    if item['repo']!='local' and 'materials_index' in result:
        prior['index_id']=result['materials_index'];atomic(receipt,packed(prior)+'\n')
    wb.load()
    material={'state':'ready' if complete else 'partial','files':published,'skipped':skipped,'unresolved':missing,
              'bytes':total,'reused_knowledge':reused,'code':code,'open_hint':'library open FIXED_REFERENCE --launch'}
    result={**result,'materials':material}
    publication=wb.synchronize(knowledge_keys=[],publish_repos=[wb.client['own_repo']])
    if not publication['synchronized']:raise Error('MATERIAL_SHARE_PENDING','Documents are accepted; Thread publication needs a retry with the same key.',packed(publication.get('errors',[])))
    result={'result':result,'publication':publication}
    atomic(receipt,packed({'fingerprint':fingerprint,'result':result})+'\n');receipt.chmod(0o600)
    return result
