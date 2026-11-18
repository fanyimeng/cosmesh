#!/usr/bin/env python3
"""Build one optional component as an immutable local candidate; never install or publish."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in ('', '.', '..') or p.startswith('.') for p in value.split('/')) or '\\' in value:
        raise ValueError('Unsafe component path: ' + value)
    return path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(component_id, output, root=ROOT):
    catalog = json.loads((root / 'cosmesh/resources/components.json').read_text(encoding='utf-8'))
    matches = [c for c in catalog['components'] if c['id'] == component_id]
    if len(matches) != 1:
        raise ValueError('Unknown or ambiguous component: ' + component_id)
    entry = matches[0]
    source = root / 'components' / str(relative(entry['source']))
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Component source must be an ordinary directory')
    package_file = source / 'package.json'
    if package_file.is_symlink():
        raise ValueError('Symlink package manifest refused')
    package = json.loads(package_file.read_text(encoding='utf-8'))
    version = package['version']
    import re
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Component requires an explicit version')
    if not re.fullmatch(r'[a-z][a-z0-9-]*', component_id):
        raise ValueError('Invalid component identifier')
    if package.get('scripts', {}).keys() & {'preinstall', 'install', 'postinstall', 'prepare', 'prepack'}:
        raise ValueError('Component build does not execute lifecycle scripts')
    files = {}

    def collect(path):
        name = path.relative_to(source).as_posix()
        relative(name)
        if path.is_symlink():
            raise ValueError('Symlink component resource refused: ' + name)
        if path.is_dir():
            if path.name in ('node_modules', 'build', 'dist'):
                raise ValueError('Build/dependency trees are not component resources')
            for child in sorted(path.iterdir()):
                collect(child)
        elif path.is_file():
            files[name] = path.read_bytes()
        else:
            raise ValueError('Missing or non-regular resource: ' + name)

    collect(package_file)
    for item in package['files']:
        target = source / str(relative(item))
        # Reject symlink parents as well as the final path.
        for parent in target.parents:
            if parent == source:
                break
            if parent.is_symlink():
                raise ValueError('Symlink resource parent refused')
        collect(target)
    for resource in ('index.js', 'cordis.patch.yml', 'locale/en.json', 'locale/zh.json', 'icon.svg'):
        if resource not in files:
            raise ValueError('Missing required bundle resource: ' + resource)
    manifest = {'schema': 'cosmesh.component-artifact.v1', 'component': entry,
                'package': package['name'], 'version': version,
                'files': {name: {'size': len(data), 'sha256': sha(data)} for name, data in sorted(files.items())}}
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (component_id + '-' + version)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Component candidates are immutable: ' + str(destination))
    with tempfile.TemporaryDirectory(prefix='.component-stage-', dir=str(output)) as temporary:
        stage = Path(temporary) / 'candidate'
        stage.mkdir()
        for name, data in files.items():
            target = stage / 'bundle' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        archive = stage / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo('bundle/' + name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zf.writestr(info, data)
        manifest['archive_sha256'] = sha(archive.read_bytes())
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Exclusive destination reservation prevents concurrent replacement; copy only new files.
        destination.mkdir()
        for child in stage.iterdir():
            target = destination / child.name
            if target.exists():
                raise FileExistsError(str(target))
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                with target.open('xb') as stream:
                    stream.write(child.read_bytes())
    return {'component': component_id, 'version': version, 'directory': str(destination),
            'install_directory': str(destination / 'bundle'), 'archive_sha256': manifest['archive_sha256'],
            'files': len(files), 'installed': False, 'published': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', nargs='?', help='ID from the packaged component catalog')
    parser.add_argument('--list', action='store_true', help='Read the source component catalog only')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/components')
    args = parser.parse_args()
    if args.list:
        print((ROOT / 'cosmesh/resources/components.json').read_text(encoding='utf-8'))
    elif args.component:
        print(json.dumps(build(args.component, args.output), ensure_ascii=False))
    else:
        parser.error('provide a component ID or --list')


if __name__ == '__main__':
    main()
