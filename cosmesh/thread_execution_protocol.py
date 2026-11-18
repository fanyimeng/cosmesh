"""Owner-only execution locations referencing an authoritative public Thread."""
from .errors import Error
from .protocol import USER
from .thread_protocol import REPO, identifier
from .thread_personal_protocol import validate_bindings

FORMAT = 'thread.execution.v1'


def validate(value):
    if not isinstance(value, dict) or set(value) != {'format', 'thread', 'source', 'bindings'} or value['format'] != FORMAT:
        raise Error('SCHEMA_UNSUPPORTED', 'Execution manifests contain only a public source and private locations.')
    tid = identifier(value['thread'], 'W')
    source = value['source']
    if not isinstance(source, dict) or set(source) != {'repo', 'owner'}:
        raise Error('SCHEMA_INVALID', 'A public Thread source needs its repository and owner.')
    if not isinstance(source['repo'], str) or not REPO.fullmatch(source['repo']) or source['repo'] == 'local':
        raise Error('REFERENCE_INVALID', 'Execution metadata must reference a public repository.')
    if not isinstance(source['owner'], str) or not USER.fullmatch(source['owner']):
        raise Error('REFERENCE_INVALID', 'Invalid public repository owner.')
    validate_bindings(value['bindings'], tid)


def protect(old, new):
    if old['thread'] != new['thread'] or old['source'] != new['source']:
        raise Error('HISTORY_PROTECTED', 'Keep the authoritative public Thread source.')
    if any(new['bindings'].get(k) != v for k, v in old['bindings'].items()):
        raise Error('HISTORY_PROTECTED', 'Keep accepted private location events.')
