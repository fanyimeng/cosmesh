#!/usr/bin/env python3
"""Materialize a platform-matched offline bundle locally, using the existing installer."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tempfile
import time

import install as manager


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def materialize(source, cache):
    info = json.loads((source / 'portable.json').read_text(encoding='utf-8'))
    for name, digest in info['files'].items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Portable release checksum mismatch.')
    version = info['version']
    release_id = 'cosmesh-%s-%s-%s-py%d%d' % (version, sys.platform, platform.machine(), *sys.version_info[:2])
    target = cache / release_id
    if (target / 'release.json').is_file():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=cache) as folder:
        stage = Path(folder)
        wheels = stage / 'wheels'
        wheels.mkdir()
        shutil.copy2(source / info['wheel'], wheels / Path(info['wheel']).name)
        wheel_cache = cache / 'wheels'
        wheel_cache.mkdir(exist_ok=True)
        base = [sys.executable, '-m', 'pip', 'download', '--disable-pip-version-check', '--only-binary=:all:',
                '--no-deps', '--require-hashes', '--dest', wheels, '--find-links', wheel_cache,
                '-r', source / 'requirements.lock']
        cached = False
        if any(wheel_cache.glob('*.whl')):
            cached = subprocess.run([str(a) for a in base] + ['--no-index'], capture_output=True).returncode == 0
        if not cached:
            run(base)
        for file in wheels.glob('*.whl'):
            if file.name != Path(info['wheel']).name:
                shutil.copy2(file, wheel_cache / file.name)
        shutil.copy2(source / 'install.py', stage / 'install.py')
        files = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in stage.rglob('*') if p.is_file()}
        meta = {'format': 1, 'product': 'cosmesh', 'version': version, 'release_id': release_id,
                'platform': sys.platform, 'machine': platform.machine(), 'python': '%d.%d' % sys.version_info[:2],
                'schema_write': info.get('schema_write', [1]), 'wheel': 'wheels/' + Path(info['wheel']).name,
                'thread_lifecycle_version': info.get('thread_lifecycle_version', 1),
                'wheels': ['wheels/' + p.name for p in sorted(wheels.glob('*.whl'))], 'files': files}
        (stage / 'release.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
        stage.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--skills-dir', type=Path, required=True)
    parser.add_argument('--ssh', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--service', help='Absolute service discovery path on the server')
    args = parser.parse_args()
    discovery = args.service or str(PurePosixPath(args.source).parent / 'service.json')
    if not discovery.startswith('/'):
        raise ValueError('Use an absolute server discovery path.')
    source = Path(__file__).resolve().parent
    cache = Path(os.environ.get('COSMESH_INSTALL_CACHE', str(Path.home() / '.cache/cosmesh/install'))).expanduser().resolve()
    bundle = materialize(source, cache)
    prefix = args.prefix.expanduser().resolve()
    skills = args.skills_dir.expanduser().resolve()
    legacy = []
    migrated = []
    backup = cache / ('ssh-skills-' + str(time.time_ns()))
    for name in ('cosmesh', 'cosbase'):
        folder = skills / name
        manifest = folder / 'SHA256SUMS'
        if manifest.is_file() and (folder / 'connection').is_file():
            expected = {'SKILL.md', 'scripts/run.sh', 'connection', 'version'}
            found = set()
            for line in manifest.read_text(encoding='utf-8').splitlines():
                digest, file = line.split(None, 1)
                if file not in expected:
                    raise ValueError('Unrecognized SSH Skill resource; preserve it before migration.')
                found.add(file)
                if hashlib.sha256((folder / file).read_bytes()).hexdigest() != digest:
                    raise ValueError('User-modified SSH Skill preserved; review it before switching to local mode.')
            actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
            if found != expected or actual != expected | {'SHA256SUMS'} or any(p.is_symlink() for p in folder.rglob('*')):
                raise ValueError('SSH Skill has extra materials; preserve them before migration.')
            legacy.append(name)
    try:
        for name in legacy:
            backup.mkdir(parents=True, exist_ok=True)
            shutil.move(str(skills / name), backup / name)
            migrated.append(name)
        action = 'upgrade' if (prefix / 'state.json').is_file() else 'install'
        result = manager.apply_release(prefix, bundle, action, ['cosmesh'], skills)
    except Exception:
        for name in migrated:
            if not (skills / name).exists():shutil.move(str(backup / name), skills / name)
        raise
    saved = json.loads((prefix / 'client.json').read_text(encoding='utf-8')) if (prefix / 'client.json').is_file() else {}
    saved.update(format=1, execution='local', ssh=args.ssh, source=args.source, skills=str(skills), discovery=discovery)
    manager.atomic(prefix / 'client.json', manager.json_bytes(saved))
    discovery_group = 'thread' if tuple(map(int, result['to'].split('.'))) >= (0, 2, 0) else 'space'
    launcher = ([sys.executable, '-I', '-X', 'utf8', str(prefix / 'manager.py'), 'launch', 'cosmesh']
                if sys.platform == 'win32' else [str(prefix / 'bin/cosmesh')])
    check = subprocess.run([*launcher, discovery_group, 'discover', '--ssh', args.ssh, '--service', discovery, '--json'],
                           text=True, encoding='utf-8', capture_output=True)
    try:
        mesh = json.loads(check.stdout)
    except ValueError:
        mesh = {'ok': False, 'error': {'code': 'DISCOVERY_PENDING', 'message': check.stderr[-1000:]}}
    saved = json.loads((prefix / 'client.json').read_text(encoding='utf-8'))
    if result.get('update_notice'):
        mesh['update_notice'] = result['update_notice']  # 0.3 clients return this existing field after upgrading.
    if result.get('project_guidance') is not None:
        mesh['project_guidance'] = result['project_guidance']
    saved['mesh_setup'] = mesh
    manager.atomic(prefix / 'client.json', manager.json_bytes(saved))
    if os.environ.get('COSMESH_BOOTSTRAP_HUMAN') == '1':
        label = '首次安装完成' if action == 'install' else '已更新' if result.get('update_notice') else '当前版本'
        print(f"CosMesh {result['to']} · {label}")
        print('程序入口：' + str(manager.command_path(prefix, 'cosmesh')))
        if migrated:
            print('原托管 Skill 备份于 ' + str(backup))
        if (result.get('project_guidance') or {}).get('complete') is False:
            print('项目说明有待处理项。用 cosmesh thread project-guidance 查看并重试。')
    else:
        print(json.dumps({'ok': True, 'data': result, 'execution': 'local', 'mesh': mesh,
                         'migrated_skill_backup': str(backup) if migrated else None}, ensure_ascii=False))
    if result.get('update_notice'):
        print(result['update_notice']['document'].rstrip())
    print('Local installation ready. Mesh discovery: ' + ('ready' if mesh.get('ok') else 'pending; retry ' + discovery_group + ' discover when connected.'))


if __name__ == '__main__':
    main()
