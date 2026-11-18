"""Materialize explicitly registered Thread files without replacing local work."""
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from .errors import Error
from .personal_protocol import sha_file
from .store import safe_path


def portable_path(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or '\\' in value or ':' in value or any(ord(c) < 32 for c in value):
        raise Error('PATH_INVALID', 'Use a relative file path inside the destination directory.')
    path = PurePosixPath(value)
    parts = value.split('/')
    if path.is_absolute() or any(p in {'', '.', '..'} for p in parts):
        raise Error('PATH_INVALID', 'Portable file paths must stay inside the selected directory.')
    if any(p.casefold() in {'.git', '.ssh', '.aws', '.codex', '.agents', '.cosmesh', '.venv', 'node_modules', '__pycache__'} or
           p.casefold() == '.env' or p.casefold().startswith('.env.') for p in parts):
        raise Error('PATH_INVALID', 'Credential, application and cache directories cannot be restore destinations.')
    return str(path)


def availability(personal, aid, row):
    """Report local existence separately from server availability; no network or hashing."""
    copy = personal.state.get('asset_copies', {}).get(aid)
    local = None
    if copy and copy['device'] == personal.state['device']:
        local = copy['path']
    elif row['device'] == personal.state['device']:
        local = row['path']
    present = bool(local and Path(local).exists())
    cached = bool(row['blob'] and (personal.cache / row['blob']).is_file())
    return {'server': 'available' if row['blob'] else 'index_only',
            'local': 'present_unverified' if present else 'cached_unverified' if cached else 'missing',
            'local_path': local if present else None,
            'local_base_sha': copy['blob'] if copy and present else None}


def materialize(personal, value, destination):
    """Only manifest files are considered; directory binding never triggers a scan."""
    destination = safe_path(destination)
    policy = personal.limits()
    rows = []; targets = {}; invalid = {}; total = 0; count = 0
    for aid, asset in sorted(value['assets'].items()):
        try: name = portable_path(asset.get('relative_path', asset['name']))
        except Error as exc: invalid[aid] = exc.code; continue
        key = name.casefold()
        targets.setdefault(key, []).append((aid, asset['blob']))
    for aid, asset in sorted(value['assets'].items()):
        if aid in invalid:
            rows.append(dict(id=aid,state='skipped',reason=invalid[aid]));continue
        name = portable_path(asset.get('relative_path', asset['name']))
        row = dict(id=aid, relative_path=name, bytes=asset['size'], state='skipped', reason=None)
        rows.append(row)
        if asset['size'] > policy['auto_file']:
            row['reason'] = 'file_limit'; continue
        if not asset['blob']:
            row.update(state='missing', reason='source_not_uploaded'); continue
        if len(targets[name.casefold()]) > 1:
            row['reason'] = 'destination_collision'; continue
        if count >= policy['batch_files'] or total + asset['size'] > policy['batch_bytes']:
            row['reason'] = 'batch_limit'; continue
        count += 1; total += asset['size']
        temporary = None
        try:
            target = safe_path(destination / name)
            if target.exists():
                if not target.is_file() or target.stat().st_size != asset['size'] or sha_file(target) != asset['blob']:
                    row.update(state='conflict', reason='existing_destination'); continue
                row.update(state='available', reason=None, path=str(target), unchanged=True)
            else:
                source = personal.download(asset['blob'], asset['size'])
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                safe_path(target)
                fd, temporary = tempfile.mkstemp(prefix='.cosmesh-copy-', dir=str(target.parent))
                with os.fdopen(fd, 'wb') as out, source.open('rb') as incoming:
                    shutil.copyfileobj(incoming, out, 256 * 1024); out.flush(); os.fsync(out.fileno())
                if sha_file(temporary) != asset['blob']:
                    raise Error('CHECKSUM_MISMATCH', 'Materialized content differs from its fixed version.')
                # Atomic no-replace publication: a concurrently created local file wins.
                os.link(temporary, target)
                row.update(state='available', reason=None, path=str(target), unchanged=False)
            personal.state.setdefault('asset_copies', {})[aid] = dict(device=personal.state['device'], path=str(target), blob=asset['blob'])
            personal.save()
        except (Error, OSError) as exc:
            row.update(state='failed', reason=getattr(exc, 'code', 'destination_unavailable'))
        finally:
            if temporary: Path(temporary).unlink(missing_ok=True)
    return {'requested': True, 'state': 'complete' if all(r['state'] == 'available' for r in rows) else 'partial',
            'files': rows, 'available': sum(r['state'] == 'available' for r in rows),
            'project_files_overwritten': False}
