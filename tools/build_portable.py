#!/usr/bin/env python3
"""One immutable application wheel + locked cross-platform dependency hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.portable-', dir=args.output) as folder:
        stage = Path(folder)
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
                        '--wheel-dir', str(stage), str(ROOT)], check=True)
        wheel = next(stage.glob('cosmesh-*.whl'))
        version = wheel.name.split('-')[1]
        target = args.output / ('cosmesh-' + version + '-portable.tar.gz')
        if target.exists():raise FileExistsError(target)
        content = stage / 'local-first'
        content.mkdir()
        shutil.move(wheel, content / wheel.name)
        shutil.copy2(ROOT / 'tools/release_installer.py', content / 'install.py')
        shutil.copy2(ROOT / 'tools/local_bootstrap.py', content / 'local_bootstrap.py')
        shutil.copy2(ROOT / 'cosmesh/resources/local-install.sh', content / 'local-install.sh')
        lock = []
        for row in (ROOT / 'requirements-release.txt').read_text().splitlines():
            if not row.strip():continue
            name, ver = row.split('==')
            with urllib.request.urlopen('https://pypi.org/pypi/%s/%s/json' % (name, ver), timeout=30) as response:
                metadata = json.load(response)
            hashes = sorted({url['digests']['sha256'] for url in metadata['urls'] if url['packagetype'] == 'bdist_wheel'})
            lock.append(row + ' ' + ' '.join('--hash=sha256:' + sha for sha in hashes))
        (content / 'requirements.lock').write_text('\n'.join(lock) + '\n')
        info = {'format': 1, 'version': version, 'wheel': wheel.name,
                'schema_write': [1,2,3] if tuple(map(int,version.split('.'))) >= (0,3,0) else [1,2] if tuple(map(int,version.split('.'))) >= (0,2,0) else [1],
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in content.iterdir()}}
        (content / 'portable.json').write_text(json.dumps(info, indent=2) + '\n')
        with tarfile.open(target, 'w:gz') as tar:tar.add(content, arcname='local-first')
        print(json.dumps({'archive': str(target.resolve()), 'version': version,
                          'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':main()
