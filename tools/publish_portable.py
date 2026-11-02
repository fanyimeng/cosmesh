#!/usr/bin/env python3
"""Publish a fixed portable client; keep the server release channel independent."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

import release_installer as manager


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('/srv/cosmesh'))
    p.add_argument('--archive', type=Path, required=True)
    args = p.parse_args()
    root = args.root.resolve()
    with tarfile.open(args.archive) as tar:
        info = json.load(tar.extractfile('local-first/portable.json'))
        script = tar.extractfile('local-first/local-install.sh').read()
    version = info['version']
    if not manager.VERSION.fullmatch(version):
        raise ValueError('Invalid release version.')
    raw = args.archive.read_bytes()
    target = root / 'releases' / ('cosmesh-' + version + '-portable.tar.gz')
    if target.exists() and target.read_bytes() != raw:
        raise ValueError('Published releases are immutable; increment the version.')
    if not target.exists():manager.atomic(target, raw, mode=0o644)
    agent = root / 'agent'
    previous = agent / 'install.sh'
    if previous.is_file() and not (agent / 'stable-local.json').exists():
        manager.atomic(agent / 'install-ssh.sh', previous.read_bytes(), mode=0o644)
    manager.atomic(agent / 'local-first.tar.gz', raw, mode=0o644)
    manager.atomic(agent / 'install.sh', script, mode=0o644)
    channel = {'format': 1, 'version': version, 'execution': 'local',
               'archive': str(target), 'sha256': hashlib.sha256(raw).hexdigest()}
    manager.atomic(agent / 'stable-local.json', manager.json_bytes(channel), mode=0o644)
    print(json.dumps({'published': True, **channel}))


if __name__ == '__main__':main()
