#!/usr/bin/env python3
"""Server-side per-account setup from a maintainer-owned release channel. Stdlib only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys


class Failure(Exception):
    pass


def read(path, owner, private=False, limit=2 * 1024 * 1024):
    path = Path(path)
    if path.is_symlink():
        raise Failure('Preserve symlinked installation or release paths.')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or
                info.st_mode & (0o077 if private else 0o022) or info.st_size > limit):
            raise Failure('Unsafe channel or installation file permissions.')
        data = handle.read(limit + 1)
        if len(data) > limit:
            raise Failure('Channel or installation metadata too large.')
    return data


def release(root):
    root = root.absolute()
    # Protected sticky root-owned ancestors (e.g. a shared mount) cannot replace another owner's directory.
    owner = root.stat().st_uid
    for path in (root, *root.parents):
        info = path.lstat()
        writable = info.st_mode & 0o022
        protected_sticky_parent = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if path.is_symlink() or info.st_uid not in (owner, 0) or (writable and not protected_sticky_parent):
            raise Failure('The release channel must be maintainer-controlled.')
    channel = json.loads(read(root / 'stable-linux.json', owner))
    if not isinstance(channel, dict):
        raise Failure('Unsupported release channel.')
    version = channel.get('version', '')
    if channel.get('format') != 1 or not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise Failure('Unsupported release channel.')
    py = '%d.%d' % sys.version_info[:2]
    if (channel.get('platform'), channel.get('machine'), channel.get('python')) != (sys.platform, platform.machine(), py):
        raise Failure('The server Python/platform does not match the published channel.')
    name = 'cosmesh-%s-%s-%s-py%d%d' % (version, sys.platform, platform.machine(), *sys.version_info[:2])
    if channel.get('release_id') != name:
        raise Failure('Invalid release directory in channel.')
    selected = root / name
    if selected.is_symlink() or selected.resolve() != selected:
        raise Failure('Release paths must be real directories.')
    for file, key in [('install.py', 'installer_sha256'), ('release.json', 'manifest_sha256')]:
        if hashlib.sha256(read(selected / file, owner)).hexdigest() != channel.get(key):
            raise Failure('Release channel checksum mismatch.')
    return selected, version


def installed(prefix):
    if not (prefix / 'state.json').exists():
        return None
    data = json.loads(read(prefix / 'state.json', os.geteuid(), private=True))
    if not isinstance(data, dict):
        raise Failure('Invalid installation state; preserve local material.')
    value = data.get('current', '')
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise Failure('Invalid installed version; preserve installation state.')
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('status', 'ensure'))
    p.add_argument('--prefix', type=Path, default=Path.home() / '.local/opt/cosmesh')
    p.add_argument('--releases-dir', type=Path, default=Path('/srv/cosmesh/releases'))
    p.add_argument('--local-skill-version')
    args = p.parse_args(argv)
    try:
        prefix = args.prefix.expanduser().absolute()
        for path in (prefix, *prefix.parents):
            if path.is_symlink():
                raise Failure('Preserve symlinked installation directories.')
        selected, available = release(args.releases_dir)
        current = installed(prefix)
        number = lambda s: tuple(map(int, s.split('.')))
        action = 'unchanged'
        if args.action == 'ensure' and (current is None or number(current) < number(available)):
            action = 'install' if current is None else 'upgrade'
            command = [sys.executable, str(selected / 'install.py'), action, '--prefix', str(prefix), '--json']
            if current is None:
                command += ['--skills', 'none']
            env = dict(os.environ)
            for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__'):
                env.pop(key, None)
            result = subprocess.run(command, text=True, capture_output=True, env=env)
            reply = json.loads(result.stdout)
            if result.returncode or not reply.get('ok'):
                raise Failure('Server installation was preserved: ' + (reply.get('error') or {}).get('message', 'installer failed'))
            current = installed(prefix)
        data = {'action': action, 'installed_version': current, 'available_version': available,
                'local_skill_version': args.local_skill_version,
                'server_update_available': current is None or number(current) < number(available),
                'skill_update_available': args.local_skill_version is not None and args.local_skill_version != current,
                'prefix': str(prefix), 'data_unchanged': True, 'automatic_updates': False}
        print(json.dumps({'ok': True, 'data': data, 'error': None}, ensure_ascii=False))
        return 0
    except (Failure, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'ok': False, 'data': None, 'error': {'code': 'REMOTE_SETUP_FAILED', 'message': str(exc)}}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
