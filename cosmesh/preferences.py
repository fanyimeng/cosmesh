"""Small private interaction preference shared by Base and Mesh."""
import json
import os
from pathlib import Path

from .errors import Error
from .store import atomic, safe_path


def path():
    directory = os.environ.get('COSMESH_PROFILE_DIR', str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config'))) / 'cosmesh'))
    return safe_path(os.environ.get('COSMESH_PREFERENCES_FILE', str(Path(directory)/'preferences.json')))


def read():
    target = path()
    if not target.exists(): return {'schema_version': 1, 'fast_mode': True}
    info = target.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077 or not target.is_file() or info.st_nlink != 1:
        raise Error('PREFERENCES_INVALID', 'Keep the interaction preference in a private owned file.')
    if info.st_size > 4096: raise Error('PREFERENCES_INVALID', 'Interaction preference is too large.')
    value = json.loads(target.read_text())
    if not isinstance(value, dict) or value.get('schema_version') != 1 or type(value.get('fast_mode')) is not bool:
        raise Error('PREFERENCES_INVALID', 'Expected a versioned boolean fast_mode preference.')
    return value


def mode(value=None, dry_run=False):
    current = read()
    if value is not None and not dry_run:
        target = path()
        prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
        if (prefix and Path(prefix).resolve() in target.parents) or any((p/'.git').exists() for p in target.parents):
            raise Error('PATH_INVALID', 'Keep interaction preferences outside application versions and Git stores.')
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = target.parent.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise Error('PREFERENCES_INVALID', 'Keep the preference directory private and owned by you.')
        current['fast_mode'] = value == 'fast'
        atomic(target, json.dumps(current, ensure_ascii=False)+'\n')
    enabled = current['fast_mode'] if value is None else value == 'fast'
    return {'fast_mode': enabled, 'saved': value is not None and not dry_run, 'dry_run': dry_run,
            'instruction': ('快速模式：优先最短必要操作，直接复用成品和回执；少读、少解释、少做额外事情。'
                            if enabled else '普通模式：按当前需要充分展开；仍复用已有成品和依据。')}
