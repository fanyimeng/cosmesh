"""Fixed knowledge-file references and verified private local opening."""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote,unquote,urlsplit

from .errors import Error
from .knowledge_protocol import knowledge_id,DIGEST
from .store import safe_path


def filename(name):
    if not isinstance(name,str) or not name or name in {'.','..'} or len(name)>240 or any(c in name for c in '/\\\n\r\x00'):
        raise Error('PATH_INVALID','Use a plain attachment filename.')
    return name


def link(ref,name):
    return 'cosmesh://library/'+ref['id']+'@'+ref['version']+'/'+quote(filename(name),safe='')


def parse(value):
    url=urlsplit(value)
    if url.scheme!='cosmesh' or url.netloc!='library' or url.query or url.fragment:
        raise Error('REFERENCE_INVALID','Use cosmesh://library/K-ID@VERSION/FILENAME.')
    parts=url.path.lstrip('/').split('/')
    if len(parts)!=2 or '@' not in parts[0]:raise Error('REFERENCE_INVALID','Pin one knowledge version and filename.')
    kid,version=parts[0].split('@',1);knowledge_id(kid)
    if not DIGEST.fullmatch(version):raise Error('REFERENCE_INVALID','Pin the complete knowledge content version.')
    return {'repo':'library','id':kid,'version':version},filename(unquote(parts[1]))


class Dependencies(HTMLParser):
    def __init__(self):super().__init__();self.missing=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);values=[]
        for field in ('src','poster','srcset','data'):
            if attrs.get(field):values.append(attrs[field])
        if tag=='link' and attrs.get('href'):values.append(attrs['href'])
        if tag=='base' and attrs.get('href'):values.append(attrs['href'])
        if tag in {'image','use'}:values.extend(attrs[x] for x in ('href','xlink:href') if attrs.get(x))
        if tag=='a' and attrs.get('href') and urlsplit(attrs['href']).scheme in {'','file'}:values.append(attrs['href'])
        if tag=='meta' and attrs.get('http-equiv','').casefold()=='refresh':values.append('redirect')
        for value in values:
            if not value.lstrip().startswith(('data:','#')):self.missing.append('resource')


def html_ready(raw):
    """No rendering, URL fetching or script execution during publication."""
    try:
        text=raw.decode('utf-8');parser=Dependencies();parser.feed(text)
    except (UnicodeError,ValueError):return False
    css=re.findall(r'url\(\s*[\'"]?([^\)\'"\s]+)',text,re.I)
    return not parser.missing and all(x.startswith(('data:','#')) for x in css) and not re.search(r'@import\s',text,re.I)


def relative_links(raw):
    try:text=raw.decode('utf-8')
    except UnicodeError:return []
    links=[]
    for value in re.findall(r'!?\[[^\]]*\]\(([^\s)]+)',text):
        parsed=urlsplit(value.strip('<>'))
        if parsed.scheme or parsed.netloc or not parsed.path:continue
        path=unquote(parsed.path)
        if path not in links:links.append(path)
    return links


def open_file(kb,value,offline=False,launch=False):
    ref,name=parse(value)
    try:doc=kb.doc(ref['id'],ref['version'])
    except Error as error:
        if offline or error.code not in {'NOT_FOUND','REFERENCE_UNAVAILABLE'}:raise
        kb.refresh();doc=kb.doc(ref['id'],ref['version'])
    file=next((f for f in doc['meta']['files'] if f['name']==name),None)
    if not file:raise Error('NOT_FOUND','This fixed knowledge version has no such attachment.')
    blob=kb.root/file['sha256']
    if offline and not blob.is_file():raise Error('CONTENT_NOT_CACHED','Open this file once while connected before using it offline.')
    if offline and (blob.stat().st_size!=file['size'] or hashlib.sha256(blob.read_bytes()).hexdigest()!=file['sha256']):
        raise Error('CHECKSUM_MISMATCH','The offline cached original differs from its recorded checksum.')
    raw=kb.download(file['sha256'],file['size'])
    if len(raw)!=file['size'] or hashlib.sha256(raw).hexdigest()!=file['sha256']:
        raise Error('CHECKSUM_MISMATCH','The fixed file differs from its recorded size or checksum.')
    root=safe_path(Path(os.environ.get('COSMESH_OPEN_CACHE',str(Path(tempfile.gettempdir()).resolve()/('cosmesh-open-'+str(os.geteuid()))))))
    prefix=os.environ.get('COSMESH_INSTALL_PREFIX')
    if root==kb.wb.base.root or kb.wb.base.root in root.parents or (prefix and Path(prefix).resolve() in [root,*root.parents]):
        raise Error('PATH_INVALID','Keep opened files outside source workbenches and application installations.')
    directory=root/ref['id']/ref['version']
    for p in (root,root/ref['id'],directory):
        p.mkdir(mode=0o700,parents=True,exist_ok=True)
        if p.is_symlink() or p.stat().st_uid!=os.geteuid() or p.stat().st_mode&0o077:
            raise Error('AUTH_DENIED','Opened documents require a private owned cache.')
    path=safe_path(directory/name)
    if path.exists() and (path.stat().st_uid!=os.geteuid() or path.stat().st_mode&0o077 or path.stat().st_nlink!=1):
        raise Error('AUTH_DENIED','The opened copy must be private and owned by this account.')
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=file['sha256']:
        fd,tempname=tempfile.mkstemp(prefix='.open-',dir=str(directory));temp=Path(tempname)
        try:
            with os.fdopen(fd,'wb') as handle:handle.write(raw)
            os.replace(temp,path)
        finally:
            if temp.exists():temp.unlink()
    self_contained=html_ready(raw) if path.suffix.lower() in {'.html','.htm'} else None
    dependencies=[];missing=[];queue=[(name,raw)];seen={name};total=len(raw)
    # Markdown files can open their selected sibling documents and figures using
    # their original relative links. No project directory or external URL is read.
    from .knowledge_protocol import MAX_TOTAL
    while queue:
        logical,content=queue.pop(0)
        if Path(logical).suffix.lower()!='.md':continue
        for relative in relative_links(content):
            parts=(Path(logical).parent/relative).parts
            if Path(relative).is_absolute() or '..' in parts or '\\' in relative:
                missing.append(relative.rsplit('/',1)[-1]);continue
            target='/'.join(parts)
            if target in seen:continue
            sibling=next((f for f in doc['meta']['files'] if f['name']==Path(relative).name),None)
            if not sibling or len(seen)>=32 or total+sibling['size']>MAX_TOTAL:
                missing.append(Path(relative).name);continue
            seen.add(target);total+=sibling['size'];source=kb.root/sibling['sha256']
            if offline and not source.is_file():raise Error('CONTENT_NOT_CACHED','A referenced sibling file is not cached locally.')
            if offline and hashlib.sha256(source.read_bytes()).hexdigest()!=sibling['sha256']:
                raise Error('CHECKSUM_MISMATCH','A referenced offline file differs from its fixed checksum.')
            content=kb.download(sibling['sha256'],sibling['size']);output=safe_path(directory/target)
            output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if output.exists() and (output.stat().st_uid!=os.geteuid() or output.stat().st_mode&0o077 or output.stat().st_nlink!=1):
                raise Error('AUTH_DENIED','Opened sibling files must remain private.')
            if not output.exists() or hashlib.sha256(output.read_bytes()).hexdigest()!=sibling['sha256']:
                fd,tmp=tempfile.mkstemp(prefix='.open-',dir=str(output.parent))
                try:
                    with os.fdopen(fd,'wb') as h:h.write(content)
                    os.replace(tmp,output)
                finally:
                    if os.path.exists(tmp):os.unlink(tmp)
            dependencies.append({'name':sibling['name'],'relative_path':target,'sha256':sibling['sha256']})
            queue.append((target,content))
    if launch:
        if self_contained is False:raise Error('DOCUMENT_INCOMPLETE','This HTML has unresolved resources; fetch it for inspection or request a self-contained report.')
        program=['open',str(path)] if sys.platform=='darwin' else ['xdg-open',str(path)]
        result=subprocess.run(program,capture_output=True,timeout=15)
        if result.returncode:raise Error('OPEN_UNAVAILABLE','The verified file is available locally; the system viewer could not be launched.')
    return {'source':ref,'name':name,'reference':value,'path':str(path),'file_url':path.as_uri(),
            'sha256':file['sha256'],'bytes':len(raw),'verified':True,'launched':launch,'html_self_contained':self_contained,
            'dependencies':dependencies,'unresolved_links':sorted(set(missing)),
            'dependency_check':'static',
            'author':doc['meta']['author'],'author_name':kb.present(doc)['author_name'],
            'read_hint':'向当前用户提供 path 可打开链接；共享时继续引用 reference，保留固定版本和原作者。'}
