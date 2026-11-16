"""Versioned owner-only transfer policy; independent of public attachments."""
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .errors import Error
from .store import safe_path, atomic

MIB = 1024 ** 2
GIB = 1024 ** 3
CHUNK = 256 * 1024
MAX_METADATA = 32 * MIB
DEFAULTS = dict(auto_file=256*MIB, batch_bytes=2*GIB, batch_files=1000,
                daily_bytes=10*GIB, server_bytes=50*GIB, cache_bytes=10*GIB,
                explicit_file=2*GIB)
HASH = re.compile(r'^[0-9a-f]{64}$')
ID = re.compile(r'^[0-9a-f]{32}$')
SCIENCE = ('.fits', '.fit', '.fts', '.npy', '.h5', '.hdf5', '.parquet', '.nc')
ARCHIVES = ('.zip', '.gz', '.tgz', '.tar', '.bz2', '.xz', '.npz', '.7z', '.zst', '.rar')
TEXT = {'.md','.txt','.tex','.bib','.csv','.tsv','.json','.yaml','.yml','.toml',
        '.py','.sh','.c','.h','.cpp','.js','.css','.html','.htm','.svg','.rst','.ini'}


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def check_id(value, pattern=ID):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Error('INPUT_INVALID', 'Invalid personal object identifier.')
    return value


def integer(value, maximum, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Error('INPUT_INVALID', 'Invalid transfer size, offset or quota.')
    return value


def private_dir(path):
    path = safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise Error('AUTH_DENIED', 'Personal storage must be owned by this account and mode 700.')
    return path


def read_json(path, default=None):
    path = safe_path(path)
    if not path.exists(): return default
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise Error('AUTH_DENIED', 'Personal metadata must be a private owned regular file.')
    if info.st_size > MAX_METADATA:
        raise Error('INPUT_TOO_LARGE', 'Personal metadata exceeds its protocol limit.')
    return json.loads(path.read_text())


def save_json(path, value):
    path = safe_path(path)
    atomic(path, packed(value)+'\n')
    path.chmod(0o600)


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''): h.update(block)
    return h.hexdigest()


def fingerprint(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def file_reason(name, head, science=False):
    lower = name.casefold()
    if lower.endswith(ARCHIVES) or head.startswith((b'PK\x03\x04',b'\x1f\x8b',b'7z\xbc\xaf',b'Rar!',b'BZh',b'\xfd7zXZ')):
        return 'archive_requires_separate_policy'
    if head.startswith((b'\x7fELF',b'MZ',b'\xfe\xed\xfa',b'\xcf\xfa\xed\xfe',b'\xca\xfe\xba\xbe',b'\x00asm')):
        return 'executable_content'
    scientific = lower.endswith(SCIENCE) or head.startswith((b'SIMPLE  =',b'\x93NUMPY',b'\x89HDF',b'PAR1',b'CDF'))
    if scientific:
        return None if science else 'science_rule_required'
    if head.startswith((b'%PDF-',b'\x89PNG\r\n',b'\xff\xd8\xff',b'GIF87a',b'GIF89a')): return None
    try: text = head.decode('utf-8')
    except UnicodeDecodeError as exc:
        # A bounded prefix may end in the middle of one UTF-8 character.
        try: text = head[:exc.start].decode('utf-8') if exc.reason=='unexpected end of data' else None
        except UnicodeDecodeError: text = None
    if text is None or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text): return 'unknown_binary'
    if '-----BEGIN ' in text and 'PRIVATE KEY-----' in text: return 'credential_content'
    return None if Path(lower).suffix in TEXT or not Path(lower).suffix else 'unrecognized_type'


def content_reason(path,name,science=False):
    with Path(path).open('rb') as stream: head=stream.read(65536)
    if Path(name).suffix.lower() in {'.docx','.pptx','.xlsx'} and head.startswith(b'PK\x03\x04'):
        import zipfile
        try:
            with zipfile.ZipFile(path) as archive:
                entries=archive.infolist()
                names={r.filename for r in entries}
                if '[Content_Types].xml' not in names: return 'invalid_office_container'
                if len(entries)>1000 or sum(r.file_size for r in entries)>256*MIB: return 'expanded_container_limit'
                if any(r.flag_bits&1 or r.filename.startswith(('/','\\')) or '..' in Path(r.filename).parts or
                       '\\' in r.filename or r.filename.lower().endswith(('.bin','.exe','.dll')) for r in entries):
                    return 'unsafe_office_container'
                return None
        except (ValueError,OSError,zipfile.BadZipFile): return 'invalid_office_container'
    return file_reason(name,head,science)


def inspect_file(path, limit, science=False):
    """Cheap rejection before hashing or copying. Local references bypass this."""
    path = safe_path(path)
    parts = [p.casefold() for p in path.parts]
    if any(p in {'.ssh','.aws','.git','.codex','.agents','node_modules','__pycache__','.venv'} or
           p=='.env' or p.startswith('.env.') for p in parts) or path.name.casefold().endswith(('.pem','.key')) or path.name.casefold().startswith(('id_rsa','id_ed25519','credentials')):
        return {'reason':'credential_or_cache_path'}
    info = path.stat()
    if not stat.S_ISREG(info.st_mode): return {'reason':'directory_or_special_file'}
    if info.st_size > limit: return {'reason':'file_limit', 'bytes':info.st_size}
    reason = content_reason(path,path.name,science)
    return {'reason':reason,'bytes':info.st_size,'fingerprint':fingerprint(info)}


def validate_snapshot(value, store):
    """Only ordinary schema-2 private source files cross this channel."""
    from .thread_protocol import Snapshot
    if not isinstance(value,dict) or set(value)!={'format','files','assets','bindings','sources'} or value['format']!=1:
        raise Error('SCHEMA_UNSUPPORTED', 'Unsupported personal snapshot.')
    files = value['files']
    if not isinstance(files,dict) or not isinstance(value['assets'],dict) or not isinstance(value['bindings'],dict):
        raise Error('SCHEMA_INVALID', 'Invalid personal snapshot collections.')
    for path, content in files.items():
        if not isinstance(path,str) or not isinstance(content,str) or not (
            path in {'.gitignore','.cosmesh/config.json'} or re.fullmatch(r'documents/[WE]-[0-9a-f-]{36}\.md',path) or
            re.fullmatch(r'\.cosmesh/operations/[0-9a-f]+\.json',path)):
            raise Error('PATH_INVALID','Unexpected file in personal snapshot.')
        store.path(path)
    model = Snapshot(store, files)
    if model.config['scope']!='base': raise Error('POLICY_DENIED','Personal snapshots require a private workbench.')
    from .protocol import decode
    from .thread_protocol import content_version
    if not isinstance(value['sources'],dict):raise Error('SCHEMA_INVALID','Invalid fixed personal sources.')
    for key,text in value['sources'].items():
        if not isinstance(key,str) or not isinstance(text,str) or '@' not in key:
            raise Error('SCHEMA_INVALID','Invalid fixed personal source.')
        identifier,sha=key.split('@',1)
        doc=decode(text,identifier+'.md')
        if doc['meta']['id']!=identifier or content_version(doc)!=sha:
            raise Error('CHECKSUM_MISMATCH','Fixed personal source differs from its identity.')
    for aid, row in value['assets'].items():
        check_id(aid)
        if not isinstance(row,dict) or set(row)!={'name','thread','device','path','science','blob','size'}:
            raise Error('SCHEMA_INVALID','Invalid personal asset.')
        check_id(row['device'])
        if not isinstance(row['name'],str) or not row['name'] or '/' in row['name'] or '\\' in row['name'] or len(row['name'])>240:
            raise Error('PATH_INVALID','Invalid asset filename.')
        if not isinstance(row['path'],str) or not Path(row['path']).is_absolute() or len(row['path'])>4096 or '\x00' in row['path']:
            raise Error('PATH_INVALID','Invalid source location.')
        if type(row['science']) is not bool: raise Error('SCHEMA_INVALID','Invalid asset policy.')
        if row['thread'] not in model.docs or model.docs[row['thread']]['meta']['kind']!='thread':
            raise Error('REFERENCE_INVALID','Asset Thread is missing from the private snapshot.')
        if row['blob'] is not None: check_id(row['blob'],HASH)
        integer(row['size'],2**63-1)
    for key,row in value['bindings'].items():
        check_id(key)
        if not isinstance(row,dict) or set(row)!={'device','bindings','project_bindings'}:
            raise Error('SCHEMA_INVALID','Invalid personal binding group.')
        check_id(row['device'])
        if not isinstance(row['bindings'],list) or not isinstance(row['project_bindings'],list):
            raise Error('SCHEMA_INVALID','Invalid personal binding list.')
    return model


def merge_maps(base, local, remote):
    result = {}
    for key in set(base)|set(local)|set(remote):
        b,l,r=base.get(key),local.get(key),remote.get(key)
        if l==r or r==b: value=l
        elif l==b: value=r
        else: raise Error('PERSONAL_CONFLICT','Both replicas changed '+key+'; both versions are retained.')
        if value is not None: result[key]=value
    return result
