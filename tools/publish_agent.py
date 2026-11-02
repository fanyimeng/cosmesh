#!/usr/bin/env python3
"""Publish an already verified server release as the on-demand SSH Skill channel."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import release_installer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/srv/cosmesh'))
    parser.add_argument('--release', type=Path, required=True)
    args = parser.parse_args()
    root = release_installer.canonical(args.root)
    if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o022:
        raise ValueError('The shared root must be controlled by this maintainer.')
    release, meta = release_installer.bundle(args.release)
    if release.parent != root / 'releases':
        raise ValueError('Promote the fixed release into the shared releases directory first.')
    # Shared servers may build with umask 002; published files must be maintainer-write only.
    for location in [release, *release.rglob('*')]:
        os.chmod(location, 0o755 if location.is_dir() else 0o644)
    agent = root / 'agent'
    if agent.is_symlink():
        raise ValueError('Preserve symlinked agent directories.')
    agent.mkdir(exist_ok=True, mode=0o755)
    if agent.stat().st_uid != os.geteuid() or agent.stat().st_mode & 0o022:
        raise ValueError('The agent directory must be maintainer-controlled.')
    channel = root / 'releases/stable-linux.json'
    previous = {}
    for path in (agent / 'install.sh', agent / 'bootstrap.py', channel):
        raw = release_installer.read_bytes(path)
        if raw is not None:
            previous[path.name] = raw
    if previous:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        backup = agent / ('.previous-' + stamp)
        backup.mkdir(mode=0o700)
        for name, raw in previous.items():
            release_installer.atomic(backup / name, raw)
    for name in ('install.sh', 'bootstrap.py'):
        destination = 'install-ssh.sh' if name == 'install.sh' and (agent / 'stable-local.json').exists() else name
        release_installer.atomic(agent / destination, (release / 'agent' / name).read_bytes(), mode=0o644)
    archive = release.parent / (meta['release_id'] + '.zip')
    os.chmod(archive, 0o644)
    data = {k: meta[k] for k in ('version', 'release_id', 'platform', 'machine', 'python')}
    data.update(format=1, installer_sha256=hashlib.sha256((release / 'install.py').read_bytes()).hexdigest(),
                manifest_sha256=hashlib.sha256((release / 'release.json').read_bytes()).hexdigest(),
                archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    release_installer.atomic(channel, release_installer.json_bytes(data), mode=0o644)
    print(json.dumps({'published': True, 'channel': str(channel), 'agent': str(agent), 'release': data}))


if __name__ == '__main__':
    main()
