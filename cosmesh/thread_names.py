"""Versioned naming cards carried by existing authored summary records."""
import hashlib
import json
import unicodedata

from .errors import Error
from .thread_protocol import new_id

MARKER = '<!-- cosmesh.thread-names.v1 '


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def validate(value):
    if not isinstance(value,dict) or set(value)!={'english','chinese','intro','aliases'}:
        raise Error('INPUT_INVALID','Naming metadata uses english, chinese, intro and aliases.')
    for key,limit in (('english',120),('chinese',120),('intro',1200)):
        text=value[key]
        if text is not None and (not isinstance(text,str) or len(text)>limit or any(c in text for c in '\n\r\x00')):
            raise Error('INPUT_INVALID','Use bounded single-line work names and introduction.')
    if not isinstance(value['aliases'],list) or len(value['aliases'])>100 or any(not isinstance(x,str) or not x.strip() or len(x)>500 or any(c in x for c in '\n\r\x00') for x in value['aliases']):
        raise Error('INPUT_INVALID','Use at most 100 explicit naming aliases.')
    return value


def parse(doc):
    if doc['meta'].get('type')!='summary': return None
    line=doc['body'].partition('\n')[0]
    if not line.startswith(MARKER) or not line.endswith(' -->') or len(line)>65536:return None
    try:return validate(json.loads(line[len(MARKER):-4]))
    except (ValueError,Error):return None


def body(value):
    validate(value)
    payload=json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e')
    title=' · '.join(x for x in (value['english'],value['chinese']) if x) or '工作名称与别名'
    return MARKER+payload+' -->\n\n# '+title+'\n\n'+(value['intro'] or '')+'\n'


def rebuild(wb):
    wb._thread_names={};wb._name_index={}
    for tid,item in wb.catalog.items():
        if item['doc']['meta']['kind']!='thread':continue
        records=[r for r in wb.records(tid) if r['repo']==item['repo'] and r['doc']['meta']['author']==item['doc']['meta']['author'] and parse(r['doc']) is not None]
        aliases={item['doc']['meta']['title']}
        for r in records:
            value=parse(r['doc'])
            aliases.update(x for x in (value['english'],value['chinese'],' · '.join(x for x in (value['english'],value['chinese']) if x)) if x)
            aliases.update(value['aliases'])
        refs={ref['id'] for r in records for ref in r['doc']['meta'].get('refs',[]) if ref['repo']==item['repo']}
        heads=[r for r in records if r['doc']['meta']['id'] not in refs]
        chosen=heads[-1] if heads else None
        value=parse(chosen['doc']) if chosen else {'english':None,'chinese':None,'intro':None,'aliases':[]}
        sources=[wb.ref(r) for r in heads]
        version=sources[0]['version'] if len(sources)==1 else hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest() if sources else 'missing'
        wb._thread_names[tid]={**value,'aliases':sorted(aliases),'source':wb.ref(chosen) if chosen else None,
                               'heads':sources,'version':version,'conflicted':len(heads)>1}
        for alias in aliases:wb._name_index.setdefault(normalized(alias),set()).add(tid)


def view(wb, thread):
    return wb._thread_names[thread]


def matches(wb, item, value):
    return item['doc']['meta']['id'] in wb._name_index.get(normalized(value),set())


def record(wb, doc, value, refs=()):
    from .threads import now
    meta={'schema_version':2,'kind':'entry','id':new_id('entry'),'author':doc['meta']['author'],
          'created_at':now(),'title':'工作名称登记','thread':doc['meta']['id'],'type':'summary','refs':list(refs)}
    if 'author_id' in doc['meta']:meta['author_id']=doc['meta']['author_id']
    if getattr(wb,'generation',None):meta['generation']=wb.generation
    return {'meta':meta,'body':body(value)}


def set_names(wb, value, english=None, chinese=None, intro=None, aliases=(), expect=None, key=None, dry_run=False):
    item=wb.find(value,'thread');old=view(wb,item['doc']['meta']['id'])
    if expect is not None and expect!=old['version']:raise Error('STATE_CONFLICT','The naming card changed; read the current names before retrying.')
    if all(x is None for x in (english,chinese,intro)) and not aliases:raise Error('INPUT_INVALID','Provide a short name, introduction or explicit alias.')
    data={k:old[k] for k in ('english','chinese','intro')}
    for field,text in (('english',english),('chinese',chinese),('intro',intro)):
        if text is not None:data[field]=text
    data['aliases']=list(dict.fromkeys([item['doc']['meta']['title'],*aliases]))
    return wb.append(item['doc']['meta']['id'],body(data),'summary',refs=old['heads'],key=key,dry_run=dry_run)
