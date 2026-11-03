"""Versioned knowledge records; payload files live outside document Git repositories."""
import re
from uuid import UUID
from .errors import Error

DIGEST = re.compile(r'^[0-9a-f]{64}$')
MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
CHUNK = 256 * 1024
EXTENSIONS = {'.md', '.txt', '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.html', '.htm', '.png', '.jpg', '.jpeg', '.svg', '.bib', '.json', '.csv', '.tsv'}


def knowledge_id(value):
    try:
        prefix, raw = value.split('-', 1)
        parsed = UUID(raw)
        if prefix != 'K' or parsed.version != 4 or str(parsed) != raw: raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise Error('REFERENCE_INVALID', 'Expected a complete K- UUIDv4 knowledge ID.')
    return value


def validate(doc):
    m = doc['meta']
    fields = {'schema_version','kind','id','author','author_id','created_at','title','resource_kind',
              'maintainer','maintainer_id','previous','status','files','bibliography','refs','generation','archive'}
    if set(m) != fields or m['schema_version'] != 3 or m['kind'] != 'resource':
        raise Error('SCHEMA_INVALID', 'Invalid knowledge record.')
    knowledge_id(m['id'])
    if m['resource_kind'] not in {'document','reference','archive'} or m['status'] not in {'draft','published','withdrawn'}:
        raise Error('SCHEMA_INVALID', 'Invalid knowledge kind or lifecycle.')
    if not isinstance(m['title'],str) or not m['title'].strip() or len(m['title']) > 200:
        raise Error('INPUT_INVALID', 'Provide a title of at most 200 characters.')
    if not isinstance(doc.get('body'),str) or len(doc['body']) > 8000:
        raise Error('INPUT_TOO_LARGE', 'Keep the catalog description short; attach long text as a file.')
    if not isinstance(m['files'],list) or len(m['files']) > 32:
        raise Error('INPUT_INVALID', 'A knowledge version supports up to 32 files.')
    if not isinstance(m['bibliography'],dict) or set(m['bibliography'])-{'doi','arxiv','authors'} or any(not isinstance(x,str) or len(x)>4000 for x in m['bibliography'].values()):
        raise Error('INPUT_INVALID','Bibliography accepts DOI, arXiv ID and original-author text.')
    if len({f.get('name') for f in m['files']})!=len(m['files']):raise Error('INPUT_INVALID','Use distinct attachment filenames.')
    from .thread_protocol import reference
    for ref in m['refs']: reference(ref)
    if m['previous'] is not None and not DIGEST.fullmatch(m['previous']):
        raise Error('REFERENCE_INVALID', 'Invalid predecessor version.')
    for f in m['files']:
        if set(f) != {'name','sha256','size','text_sha256','text_status','extractor'}:
            raise Error('SCHEMA_INVALID', 'Invalid file descriptor.')
        if not DIGEST.fullmatch(f['sha256']) or (f['text_sha256'] and not DIGEST.fullmatch(f['text_sha256'])):
            raise Error('SCHEMA_INVALID', 'Invalid file checksum.')
        if type(f['size']) is not int or not 0 <= f['size'] <= (MAX_TOTAL if m['resource_kind']=='archive' else MAX_FILE):
            raise Error('INPUT_TOO_LARGE', 'A document exceeds the file limit.')
        if not isinstance(f['name'],str) or '/' in f['name'] or '\\' in f['name'] or f['name'] in {'.','..'} or len(f['name'])>240:
            raise Error('PATH_INVALID', 'Use a plain filename.')
    if sum(f['size'] for f in m['files']) > MAX_TOTAL:
        raise Error('INPUT_TOO_LARGE', 'A knowledge version exceeds the total file limit.')
