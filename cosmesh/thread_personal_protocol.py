"""Selected-Thread private manifests. No repository configuration crosses devices."""
import json
import re
from pathlib import Path

from .errors import Error
from .personal_protocol import check_id, digest, packed, validate_snapshot
from .protocol import decode
from .thread_protocol import content_version, identifier, validate

FORMAT = 'thread.personal.v1'
MAPS = ('documents', 'history', 'operations', 'bindings', 'assets')


def empty(tid):
    return dict(format=FORMAT, thread=identifier(tid, 'W'), **{k: {} for k in MAPS})


def belongs(doc, tid):
    meta = doc['meta']
    return meta['id'] == tid if meta['kind'] == 'thread' else meta['thread'] == tid


def validate_manifest(value, store):
    if not isinstance(value, dict) or set(value) != {'format', 'thread', *MAPS} or value['format'] != FORMAT:
        raise Error('SCHEMA_UNSUPPORTED', 'Unsupported selected-Thread personal manifest.')
    tid = identifier(value['thread'], 'W')
    if any(not isinstance(value[k], dict) for k in MAPS):
        raise Error('SCHEMA_INVALID', 'Invalid Thread manifest collections.')
    documents = value['documents']
    if 'documents/' + tid + '.md' not in documents:
        raise Error('REFERENCE_INVALID', 'The selected Thread definition is required.')
    for path, text in documents.items():
        if not isinstance(path, str) or not re.fullmatch(r'documents/[WE]-[0-9a-f-]{36}\.md', path) or not isinstance(text, str):
            raise Error('PATH_INVALID', 'Only selected Thread documents may be transferred.')
        doc = decode(text, path.split('/')[-1]); validate(doc)
        if not belongs(doc, tid):
            raise Error('POLICY_DENIED', 'The manifest contains another Thread.')
        key = doc['meta']['id'] + '@' + content_version(doc)
        if value['history'].get(key) != text:
            raise Error('HISTORY_PROTECTED', 'Keep the current document in fixed history.')
    for key, text in value['history'].items():
        if not isinstance(key, str) or not isinstance(text, str):
            raise Error('SCHEMA_INVALID', 'Invalid Thread history.')
        doc = decode(text); validate(doc)
        if not belongs(doc, tid) or key != doc['meta']['id'] + '@' + content_version(doc):
            raise Error('POLICY_DENIED', 'History must belong to the selected Thread and fixed version.')
    ids = {path.split('/')[-1][:-3] for path in documents}
    for path, text in value['operations'].items():
        if not isinstance(path, str) or not re.fullmatch(r'\.cosmesh/operations/[0-9a-f]{64}\.json', path):
            raise Error('PATH_INVALID', 'Invalid selected operation receipt path.')
        receipt = json.loads(text)
        if not isinstance(receipt, dict) or receipt.get('result', {}).get('id') not in ids:
            raise Error('POLICY_DENIED', 'Operation receipts must belong to the selected Thread.')
    # Existing schema validation checks control chains, file policy and asset ownership.
    config = dict(schema_version=2, product='cosmesh', user='personal', timezone='UTC', mode='local_unverified', scope='base')
    from .thread_files import portable_path
    assets = {}
    for aid, row in value['assets'].items():
        if not isinstance(row, dict): raise Error('SCHEMA_INVALID', 'Invalid Thread asset.')
        if 'relative_path' in row: portable_path(row['relative_path'])
        if 'removed' in row and row['removed'] is not True: raise Error('SCHEMA_INVALID','Invalid removal marker.')
        assets[aid] = {k: v for k, v in row.items() if k not in ('relative_path','removed')}
    model = validate_snapshot(dict(format=1, files={'.cosmesh/config.json': json.dumps(config), **documents},
                                  assets=assets, bindings={}, sources=value['history']), store)
    validate_bindings(value['bindings'], tid)
    return model


def validate_bindings(bindings, tid):
    if not isinstance(bindings, dict):
        raise Error('SCHEMA_INVALID', 'Invalid private location collection.')
    for key, row in bindings.items():
        if not isinstance(row, dict) or set(row) != {'device', 'replica', 'kind', 'binding'}:
            raise Error('SCHEMA_INVALID', 'Invalid private location record.')
        check_id(row['device']); check_id(row['replica'])
        if key != digest(packed(row).encode()):
            raise Error('CHECKSUM_MISMATCH', 'Location identity differs from its contents.')
        binding = row['binding']
        common = {'thread', 'agent', 'host', 'workdir'}
        allowed = common | ({'session_id'} if row['kind'] == 'bindings' else
                            {'kind', 'name', 'project_id', 'status', 'removed', 'at', 'observed_at', 'source', 'verification'})
        if row['kind'] not in {'bindings', 'project_bindings'} or not isinstance(binding, dict) or not common <= set(binding) or set(binding) - allowed:
            raise Error('SCHEMA_INVALID', 'Invalid selected project/directory binding.')
        if binding['thread'] != tid:
            raise Error('POLICY_DENIED', 'A location belongs to another Thread.')
        for field in ('agent', 'host'):
            if not isinstance(binding[field], str) or not binding[field].strip() or len(binding[field]) > 500:
                raise Error('SCHEMA_INVALID', 'Invalid Agent or host label.')
        path = binding['workdir']
        if path is not None and (not isinstance(path, str) or not Path(path).is_absolute() or '\x00' in path or len(path) > 4096):
            raise Error('PATH_INVALID', 'Invalid destination directory.')
        if row['kind'] == 'project_bindings':
            if binding.get('kind') not in {'project', 'workspace', 'directory'} or not isinstance(binding.get('name'), str) or not binding['name'].strip() or type(binding.get('removed')) is not bool:
                raise Error('SCHEMA_INVALID', 'Invalid native project binding.')
        if len(packed(binding)) > 16384:
            raise Error('INPUT_TOO_LARGE', 'Location metadata exceeds its limit.')


def protect_history(old, new):
    path = 'documents/' + old['thread'] + '.md'
    if decode(old['documents'][path])['meta']['author'] != decode(new['documents'][path])['meta']['author']:
        raise Error('HISTORY_PROTECTED', 'The Thread creator identity is retained across devices.')
    for key in ('history', 'operations', 'bindings'):
        if any(new[key].get(k) != v for k, v in old[key].items()):
            raise Error('HISTORY_PROTECTED', 'Keep accepted Thread history and location events.')
    if set(old['documents']) - set(new['documents']) or set(old['assets']) - set(new['assets']):
        raise Error('HISTORY_PROTECTED', 'Keep accepted Thread records and asset references.')
