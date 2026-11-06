"""Local-first knowledge cache, document extraction, real-name lookup and contribution views."""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree
from html.parser import HTMLParser

from .errors import Error
from .knowledge_protocol import CHUNK,MAX_FILE,BINARY_DOCUMENTS,attachment_reason,knowledge_id
from .store import atomic,safe_path
from .community import packed,digest,normalized
from .connections import networked


def select_user(users,value):
    exact=[x for x in users if value in (x['id'],x['account'])]
    matches=exact or [x for x in users if x['name'] and normalized(x['name'])==normalized(value)]
    if len(matches)!=1:
        raise Error('USER_NOT_FOUND' if not matches else 'AMBIGUOUS_USER','Use one exact real name, account or stable user ID.',
                    json.dumps([{'id':x['id'],'account':x['account'],'name':x['name']} for x in matches],ensure_ascii=False))
    return matches[0]


class Knowledge:
    def __init__(self,wb):
        self.wb=wb;self.root=wb.base.path('.cosmesh/knowledge');self._uploaded=set();self._known_blobs=None
        exclude=wb.base.path('.git/info/exclude')
        if exclude.is_file() and '.cosmesh/knowledge/' not in exclude.read_text().splitlines():
            atomic(exclude,exclude.read_text()+'\n.cosmesh/knowledge/\n')
        self.root.mkdir(exist_ok=True,mode=0o700)
        self.path=self.root/'snapshot.json'
        stamp=self.path.stat().st_mtime_ns if self.path.is_file() else None
        cached=getattr(wb,'_knowledge_snapshot',None)
        if cached and cached[0]==stamp:
            self.data=cached[1];self.versions=cached[2];return
        self.data=json.loads(self.path.read_text()) if self.path.is_file() else {'users':[],'versions':[],'heads':{},'events':[],'contributions':[],'archives':[]}
        self.versions={(x['doc']['meta']['id'],x['version']):x['doc'] for x in self.data['versions']}
        wb._knowledge_snapshot=(stamp,self.data,self.versions)

    def rpc(self,payload):
        from .spaces import rpc
        connection=self.wb.client.get('connection')
        if not connection:raise Error('HUB_CONNECTION_REQUIRED','Use thread discover with your existing connection first.')
        return rpc(connection,payload)

    def cache_blob(self,raw):
        sha=digest(raw);path=self.root/sha
        if not path.exists() or digest(path.read_bytes())!=sha:
            temp=self.root/('.'+uuid4().hex)
            temp.write_bytes(raw);temp.chmod(0o600);os.replace(temp,path)
        return sha

    @networked
    def download(self,sha,size):
        path=self.root/sha
        if path.is_file():
            raw=path.read_bytes()
            if len(raw)==size and digest(raw)==sha:return raw
        data=bytearray()
        while len(data)<size:
            part=self.rpc({'method':'community_get','sha256':sha,'offset':len(data)})
            if part['sha256']!=sha or part['offset']!=len(data) or part['size']!=size:raise Error('SYNC_PROTOCOL','Document source changed during download.')
            raw=base64.b64decode(part['data'],validate=True)
            if not raw or len(raw)>CHUNK or len(data)+len(raw)>size:raise Error('SYNC_PROTOCOL','Invalid document chunk.')
            data.extend(raw)
        raw=bytes(data)
        if digest(raw)!=sha:raise Error('CHECKSUM_MISMATCH','Downloaded document checksum differs.')
        self.cache_blob(raw);return raw

    @networked
    def refresh(self):
        info=self.rpc({'method':'community_info'})
        if not info.get('format')==1:raise Error('SCHEMA_UNSUPPORTED','Unsupported knowledge service.')
        if self.data.get('sha256')!=info['sha256']:
            raw=self.download(info['sha256'],info['size']);data=json.loads(raw)
            from .knowledge_protocol import validate
            from .thread_protocol import content_version
            if data.get('format')!=1:raise Error('SCHEMA_UNSUPPORTED','Unsupported knowledge snapshot.')
            for record in data['versions']:
                validate(record['doc'])
                if content_version(record['doc'])!=record['version']:raise Error('CHECKSUM_MISMATCH','Knowledge metadata version differs.')
            data.update(sha256=info['sha256'],synced_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())
            atomic(self.path,json.dumps(data,ensure_ascii=False,indent=2)+'\n');os.chmod(self.path,0o600)
            self.data=data;self.versions={(x['doc']['meta']['id'],x['version']):x['doc'] for x in data['versions']}
            self.wb._knowledge_snapshot=(self.path.stat().st_mtime_ns,self.data,self.versions);self._known_blobs=None
        self.wb.client['community_enabled']=True;self.wb.save_client()
        return info

    @networked
    def upload(self,sha):
        if self._known_blobs is None:self._known_blobs={s for d in self.versions.values() for f in d['meta']['files'] for s in (f['sha256'],f['text_sha256']) if s}
        if sha in self._uploaded or sha in self._known_blobs:return
        path=self.root/sha
        if not path.is_file():raise Error('NOT_FOUND','Local document content is missing.')
        raw=path.read_bytes()
        if digest(raw)!=sha:raise Error('CHECKSUM_MISMATCH','Local document changed after registration.')
        for offset in range(0,max(len(raw),1),CHUNK):
            reply=self.rpc({'method':'community_put','sha256':sha,'size':len(raw),'offset':offset,'data':base64.b64encode(raw[offset:offset+CHUNK]).decode()})
            if reply.get('sha256')!=sha or type(reply.get('complete'))is not bool:
                raise Error('SYNC_PROTOCOL','Upload acknowledgement does not match its file.')
            if reply['complete']:self._uploaded.add(sha);break

    @networked
    def apply(self,operation,value,key=None):
        sha=self.cache_blob(packed(value).encode());self.upload(sha)
        return self.rpc({'method':'community_apply','operation':operation,'input':sha,'key':key or str(uuid4())})

    def user(self,value):return select_user(self.data['users'],value)

    def present(self,doc):
        from .protocol import public
        result=public(doc)
        for field in ('author','maintainer'):
            person=next((x for x in self.data['users'] if x['id']==doc['meta'][field+'_id']),None)
            result[field+'_name']=person['name'] if person else None
        return result

    def doc(self,value,version=None):
        if '@' in value:value,version=value.split('@',1)
        candidates=[key for key in self.data['heads'] if key==value or key.startswith(value) or self.versions[(key,self.data['heads'][key])]['meta']['title']==value]
        if len(candidates)!=1:raise Error('NOT_FOUND' if not candidates else 'AMBIGUOUS_ID','Choose one knowledge item ID.')
        key=candidates[0];version=version or self.data['heads'][key]
        if (key,version) not in self.versions:raise Error('REFERENCE_UNAVAILABLE','Knowledge version is not cached; pull first.')
        return self.versions[(key,version)]

    def ref(self,value):
        from .thread_protocol import content_version
        doc=self.doc(value)
        return {'repo':'library','id':doc['meta']['id'],'version':content_version(doc)}

    def backlinks(self, value):
        """Find shared work that directly cites this resource, without copying it."""
        source = self.ref(value)
        exact = '@' in value
        links = self.wb.incoming_links([('library', source['id'])],
                                      version=source['version'] if exact else None)
        threads = {}
        for link in links:
            thread = link['thread']
            key = (thread['source']['repo'], thread['id'])
            group = threads.setdefault(key, {**thread, 'record_ids': []})
            if link['record']['id'] not in group['record_ids']:
                group['record_ids'].append(link['record']['id'])
        current = self.doc(source['id'])
        return {'resource': self.present(self.doc(value)), 'source': source,
                'current_source': self.ref(source['id']), 'current_status': current['meta']['status'],
                'version_scope': 'exact' if exact else 'all',
                'threads': list(threads.values()), 'backlinks': links,
                'coverage': 'cached_mesh', 'sources': self.wb.status()['spaces']}

    def outbox(self):
        path=self.root/'outbox';path.mkdir(exist_ok=True,mode=0o700);return path

    def stage(self,operation,value,key=None):
        key=key or str(uuid4());path=self.outbox()/(digest(key.encode())+'.json')
        if not isinstance(key,str) or len(key)>200:raise Error('INPUT_INVALID','Use an operation key of at most 200 characters.')
        data={'operation':operation,'value':value,'key':key}
        receipt=self.root/('receipt-'+path.name)
        if receipt.exists():
            saved=json.loads(receipt.read_text());old=saved['request']
            if operation=='resource_add':value['id']=old['value']['id']
            if data!=old:raise Error('IDEMPOTENCY_CONFLICT','This completed key identifies different input.')
            return {**saved['result'],'replayed':True,'saved_locally':True}
        if path.exists():
            old=json.loads(path.read_text())
            # A caller-generated new ID is replaced by the original ID on a retry.
            if operation=='resource_add':value['id']=old['value']['id']
            if old!=data:raise Error('IDEMPOTENCY_CONFLICT','This local key already identifies different input.')
        else:atomic(path,packed(data)+'\n');os.chmod(path,0o600)
        return {'id':value['id'],'saved_locally':True,'publication':'pending','key':key}

    @networked
    def synchronize(self,keys=None,refresh=True):
        if refresh:self.refresh()
        results=[];errors=[]
        for path in sorted(self.outbox().glob('*.json')):
            request=json.loads(path.read_text());value=request['value']
            if keys is not None and request['key'] not in keys:continue
            try:
                for file in value.get('files',[]):
                    for sha in (file['sha256'],file['text_sha256']):
                        if sha:self.upload(sha)
                saved=self.apply(request['operation'],value,request['key'])
                receipt=self.root/('receipt-'+path.name)
                atomic(receipt,packed({'request':request,'result':saved})+'\n')
                path.unlink();results.append(saved)
            except Error as exc:errors.append({'id':value['id'],'error':exc.as_dict()})
        self.refresh()
        return {'synchronized':not errors,'results':results,'errors':errors}

    def import_files(self,paths):
        files=[]
        known={(f['sha256'],Path(f['name']).suffix.lower()):f for doc in self.versions.values() for f in doc['meta']['files']}
        for name in paths:
            path=Path(name).expanduser()
            if not path.is_file() or path.stat().st_size>MAX_FILE:raise Error('INPUT_TOO_LARGE','Choose an existing document up to 20 MiB.')
            raw=path.read_bytes()
            if len(raw)>MAX_FILE:raise Error('INPUT_TOO_LARGE','Choose a document up to 20 MiB.')
            reason=attachment_reason(path.name,raw)
            if reason:raise Error('INPUT_INVALID',f'Attachment {path.name} (suffix {path.suffix or "[none]"}) rejected: {reason}. Accepts UTF-8 text/source, PDF, Office and figures.')
            sha=self.cache_blob(raw)
            previous=known.get((sha,path.suffix.lower()))
            if previous:
                files.append({**previous,'name':path.name});continue
            # Parsing runs in a separate process with a bounded lifetime; originals survive extraction failures.
            try:
                result=subprocess.run([sys.executable,'-m','cosmesh.knowledge','extract',str(path.resolve())],capture_output=True,text=True,timeout=30)
                extracted=json.loads(result.stdout) if result.returncode==0 else {'status':'failed','text':'','tool':'extractor_failed'}
            except (OSError,ValueError,subprocess.TimeoutExpired):extracted={'status':'failed','text':'','tool':'extractor_timeout'}
            text=extracted['text'].encode()
            if len(text)>MAX_FILE:extracted['status']='truncated';text=text[:MAX_FILE].decode('utf-8',errors='ignore').encode()
            files.append({'name':path.name,'sha256':sha,'size':len(raw),'text_sha256':self.cache_blob(text) if text else None,
                          'text_status':extracted['status'],'extractor':extracted['tool']})
        return files

    def text(self,value,offline=False):
        doc=self.doc(value);parts=[]
        for f in doc['meta']['files']:
            sha=f['text_sha256']
            if not sha:parts.append({'file':f['name'],'status':f['text_status'],'text':''});continue
            path=self.root/sha
            if path.is_file() and digest(path.read_bytes())!=sha:
                if offline:raise Error('CHECKSUM_MISMATCH','Cached extracted text was modified.')
                path.unlink()
            if not path.is_file():
                if offline:raise Error('CONTENT_NOT_CACHED','Fetch this item text while connected before using it offline.')
                # Size is requested separately to keep catalog records small.
                first=self.rpc({'method':'community_get','sha256':sha,'offset':0});self.download(sha,first['size'])
            parts.append({'file':f['name'],'status':f['text_status'],'text':path.read_text()})
        return {'id':doc['meta']['id'],'parts':parts}

    @networked
    def search(self,query,full_text=False,offline=False):
        from .protocol import public
        matches=[]
        if full_text and not offline:
            offset=0
            while True:
                page=self.rpc({'method':'community_search','query':query,'offset':offset})
                for hit in page['hits']:
                    if (hit['id'],hit['version']) not in self.versions:self.refresh()
                    matches.append({**self.present(self.doc(hit['id'],hit['version'])),'snippet':hit['snippet']})
                if page['next_offset'] is None:break
                offset=page['next_offset']
            return matches
        for key,version in self.data['heads'].items():
            doc=self.versions[(key,version)]
            if doc['meta']['status']!='published':continue
            haystack=doc['meta']['title']+'\n'+doc['body']+'\n'+packed(doc['meta']['bibliography'])
            if full_text:haystack+='\n'+'\n'.join(x['text'] for x in self.text(key,offline)['parts'])
            if query.casefold() in haystack.casefold():matches.append(self.present(doc))
        return matches

    def overview(self,value):
        user=self.user(value);account=user['account'];uid=user['id']
        threads=[];records=[]
        for item in self.wb.catalog.values():
            if item['repo']=='local':continue
            doc=item['doc']
            if doc['meta']['author']==account:
                (threads if doc['meta']['kind']=='thread' else records).append(self.wb.present(item))
        contributed={(x['object'],x['payload'].get('version')) for x in self.data['events'] if x['person']==uid}
        documents=[{'id':x['doc']['meta']['id'],'version':x['version'],'title':x['doc']['meta']['title'],
                    'maintainer':x['doc']['meta']['maintainer'],'kind':x['doc']['meta']['resource_kind']}
                   for x in self.data['versions'] if x['doc']['meta']['author_id']==uid or (x['doc']['meta']['id'],x['version']) in contributed]
        events=[x for x in self.data['events'] if x['person']==uid]
        evidence=[x for x in self.data['contributions'] if x['person']==uid]
        pending=[{'request':self.wb.present(x),**self.wb.request_state(x)} for x in self.wb.catalog.values()
                 if x['doc']['meta'].get('type')=='request' and x['doc']['meta']['to']==account]
        return {'user':user,'threads':threads,'records':records,'requests':pending,'knowledge_versions':documents,
                'contributions':evidence,'activity':events,'as_of':self.data.get('synced_at'),
                'summary':{'active_threads':sum(t['lifecycle']=='active' for t in threads),
                           'archived_threads':sum(t['lifecycle']=='archived' for t in threads),
                           'pending_requests':sum(q['state'] in {'pending','accepted','partial'} for q in pending),
                           'knowledge_versions':len(documents),'contribution_records':len(evidence)}}


class TextHTML(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.hidden+=1
        if tag in {'p','div','h1','h2','h3','li','br'}:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def extract(path):
    suffix=path.suffix.lower();tool='cosmesh.text';status='extracted';text=''
    if suffix in {'.md','.txt','.bib','.json','.csv','.tsv'}:text=path.read_text(encoding='utf-8')
    elif suffix in {'.html','.htm'}:
        parser=TextHTML();parser.feed(path.read_text(encoding='utf-8'));text=''.join(parser.parts)
    elif suffix in {'.docx','.pptx'}:
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist())>MAX_FILE*5:raise Error('INPUT_TOO_LARGE','Expanded office document is too large.')
            if suffix=='.docx':names=['word/document.xml']
            else:names=sorted([n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)],key=lambda n:int(re.search(r'(\d+)\.xml',n)[1]))
            parts=[]
            for index,name in enumerate(names,1):
                root=ElementTree.fromstring(archive.read(name));paragraphs=[]
                for node in root.iter():
                    if node.tag.rsplit('}',1)[-1]=='p':paragraphs.append(''.join(x.text or '' for x in node.iter() if x.tag.rsplit('}',1)[-1]=='t'))
                parts.append(('\n[slide '+str(index)+']\n' if suffix=='.pptx' else '')+'\n'.join(paragraphs))
            text='\n'.join(parts);tool='cosmesh.ooxml'
    elif suffix=='.pdf':
        import pypdf
        reader=pypdf.PdfReader(path)
        if reader.is_encrypted:status='encrypted'
        else:
            text='\n'.join('[page '+str(i)+']\n'+(page.extract_text() or '') for i,page in enumerate(reader.pages,1))
            if not any(page.extract_text() for page in reader.pages):status='needs_ocr'
        tool='pypdf/'+pypdf.__version__
    elif suffix not in BINARY_DOCUMENTS:text=path.read_text(encoding='utf-8')
    else:status='not_extracted';tool='original_only'
    return {'status':status,'text':text,'tool':tool}


if __name__=='__main__':
    try:print(json.dumps(extract(Path(sys.argv[2])),ensure_ascii=False))
    except Exception:print(json.dumps({'status':'failed','text':'','tool':'extraction_failed'}))
