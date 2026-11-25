"""Pinned optional bundles, separate from the Python runtime and Thread storage."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

from .errors import Error
from .store import safe_path

LIMIT = 16 * 1024 * 1024


def catalog():
    return json.loads((Path(__file__).parent / 'resources/components.json').read_text(encoding="utf-8"))['components']


def select(name):
    for item in catalog():
        if name in (item['id'], item.get('alias')):
            return item
    raise Error('NOT_FOUND', 'Unknown component; use component list.')


def merge_catalog(base, document):
    """The configured SSH software source may publish new versions, never new host adapters."""
    if document.get('schema') != 'cosmesh.components.v1':
        raise Error('COMPONENT_CATALOG', 'Unknown component catalog format.')
    matches = [entry for entry in document.get('components', []) if entry.get('id') == base['id']]
    if len(matches) != 1:
        raise Error('COMPONENT_CATALOG', 'Component release entry is missing or ambiguous.')
    remote = matches[0]
    releases = dict(base['releases'])
    for version, release in remote.get('releases', {}).items():
        required = release.get('requires', {})
        additional = required.get('dsh_also', [])
        if (not isinstance(additional, list) or any(not isinstance(v, str) or
                not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', v) for v in additional) or
                not re.fullmatch(r'\d+\.\d+\.\d+', version) or
                not re.fullmatch(r'[a-f0-9]{64}', release.get('archive_sha256', '')) or
                not re.fullmatch(r'>=\d+\.\d+\.\d+', required.get('cosmesh', '')) or
                not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', required.get('dsh', ''))):
            raise Error('COMPONENT_CATALOG', 'Invalid component version, digest or compatibility constraint.')
        if version in releases and releases[version] != release:
            raise Error('COMPONENT_CATALOG', 'Published component versions are immutable; the catalog changed an existing pin.')
        releases[version] = release
    if remote.get('package') != base['package'] or remote.get('version') not in releases:
        raise Error('COMPONENT_CATALOG', 'Component package identity or recommended version is invalid.')
    return dict(base, version=remote['version'], releases=releases)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unpack(raw, item, version):
    release = item['releases'].get(version)
    if not release or len(raw) > LIMIT or digest(raw) != release['archive_sha256']:
        raise Error('COMPONENT_INTEGRITY', 'Bundle does not match the pinned component release.')
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 256 or sum(e.file_size for e in entries) > LIMIT:
                raise ValueError('Bundle exceeds extraction limits')
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (not entry.filename.startswith('bundle/') or entry.is_dir() or
                        any(p in ('', '.', '..') or p.startswith('.') for p in entry.filename.split('/')) or
                        '\\' in entry.filename or any(ord(c) < 32 for c in entry.filename) or
                        stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise ValueError('Unsafe bundle member')
                name = str(path.relative_to('bundle'))
                if name in files:
                    raise ValueError('Duplicate bundle member')
                files[name] = archive.read(entry)
        package = json.loads(files['package.json'])
        if package['name'] != item['package'] or package['version'] != version:
            raise ValueError('Package identity mismatch')
        if package.get('dependencies') or package.get('scripts', {}).keys() & {
                'install', 'preinstall', 'postinstall', 'prepare', 'prepack'}:
            raise ValueError('Dependency or lifecycle execution is unsupported')
        if not {'index.js', 'cordis.patch.yml', 'icon.svg', 'locale/en.json', 'locale/zh.json'} <= files.keys():
            raise ValueError('Incomplete bundle')
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise Error('COMPONENT_INTEGRITY', 'Invalid component archive.', str(exc)) from None
    return files


def verify_tree(directory, files):
    directory = safe_path(directory)
    actual = set()
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise Error('COMPONENT_INTEGRITY', 'Managed bundle contains a symlink.')
        if path.is_file():
            name = path.relative_to(directory).as_posix()
            actual.add(name)
            if name not in files or path.read_bytes() != files[name]:
                raise Error('COMPONENT_INTEGRITY', 'Bundle resource was modified.')
        elif not path.is_dir():
            raise Error('COMPONENT_INTEGRITY', 'Bundle contains a non-regular resource.')
    if actual != set(files):
        raise Error('COMPONENT_INTEGRITY', 'Bundle resources are missing.')


def prepare(root, item, version, artifact=None, offline=False):
    if version not in item['releases'] or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise Error('COMPONENT_VERSION', 'Version is absent from the installed compatibility catalog.')
    destination = safe_path(root / 'versions' / (item['id'] + '-' + version))
    if destination.exists():
        archive = safe_path(destination / 'bundle.zip')
        files = unpack(archive.read_bytes(), item, version)
        verify_tree(destination / 'bundle', files)
        return destination / 'bundle', files
    if artifact:
        archive = safe_path(Path(artifact).expanduser().absolute())
        if archive.is_dir():
            archive = safe_path(archive / 'bundle.zip')
        if archive.stat().st_size > LIMIT:
            raise Error('COMPONENT_INTEGRITY', 'Component archive exceeds the size limit.')
        raw = archive.read_bytes()
    else:
        if offline:
            raise Error('COMPONENT_UNAVAILABLE', 'Pinned bundle is not cached; supply --artifact or allow the configured release source.')
        from .client import config, fetch
        _, cfg = config()
        raw = fetch(cfg, 'components/' + item['id'] + '-' + version + '/bundle.zip')
    files = unpack(raw, item, version)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=str(destination.parent)) as temporary:
        stage = Path(temporary) / 'release'
        stage.mkdir(mode=0o700)
        (stage / 'bundle.zip').write_bytes(raw)
        for name, data in files.items():
            path = stage / 'bundle' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        stage.rename(destination)
    return destination / 'bundle', files
