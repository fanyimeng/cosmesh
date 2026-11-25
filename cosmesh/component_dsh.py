"""One host adapter: inspect local metadata, mutate through the official DSH CLI."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from . import __version__
from .errors import Error
from .store import safe_path

DESKTOP_CLI = Path('/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh')
PROFILE_FILES = ('package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'cordis.yml', 'cordis.patch.yml')


def home():
    return safe_path(Path(os.environ.get('DSH_HOME', str(Path.home() / '.dsh'))))


PROFILE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')


def profiles():
    directory = safe_path(home() / 'profiles')
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir()
                  if PROFILE_NAME.fullmatch(p.name) and not p.is_symlink() and (p / 'package.json').is_file())


def profile_path(profile):
    if not PROFILE_NAME.fullmatch(profile):
        raise Error('INPUT_INVALID', 'Invalid DSH profile name.')
    return safe_path(home() / 'profiles' / profile)


def executable(value=None):
    candidate = value or (str(DESKTOP_CLI) if DESKTOP_CLI.is_file() else shutil.which('dsh'))
    if not candidate or not Path(candidate).is_absolute() or not os.access(candidate, os.X_OK):
        raise Error('HOST_UNAVAILABLE', 'DSH CLI is unavailable; provide --dsh-cli with an absolute executable path.')
    return str(Path(candidate))


def invoke(cli, args):
    try:
        result = subprocess.run([cli] + args, capture_output=True, text=True, encoding='utf-8', timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Error('COMPONENT_HOST_FAILED', 'DSH command did not complete; inspect status before retrying.', type(exc).__name__) from None
    if result.returncode:
        # Host diagnostics may contain private paths/configuration. Keep them out of ordinary receipts.
        raise Error('COMPONENT_HOST_FAILED', 'DSH command failed; original bundles and transaction record are retained.',
                    {'exit_code': result.returncode})
    return result.stdout


def compatibility(cli, item, version):
    required = item['releases'][version]['requires']
    minimum = required['cosmesh'].removeprefix('>=')
    if tuple(map(int, __version__.split('.'))) < tuple(map(int, minimum.split('.'))):
        raise Error('COMPONENT_INCOMPATIBLE', 'Update CosMesh before installing this component.')
    found = invoke(cli, ['--version']).strip()
    verified = [required['dsh'], *required.get('dsh_also', [])]
    if found not in verified:
        raise Error('COMPONENT_INCOMPATIBLE', 'This DSH version has not been verified for the selected component.',
                    {'required': verified, 'detected': found})
    return found


def installed(profile, package):
    directory = profile_path(profile)
    manifest_path = safe_path(directory / 'package.json')
    if not manifest_path.is_file():
        raise Error('HOST_PROFILE_REQUIRED', 'Initialize the intended DSH profile through DSH first.')
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dependencies = manifest.get('dependencies', {})
    bundles = manifest.get('dsh', {}).get('profile', {}).get('bundles', [])
    if '@local/cosmesh-dsh-bridge' in dependencies or '@local/cosmesh-dsh-bridge' in bundles:
        raise Error('COMPONENT_CONFLICT', 'The old bridge is registered; use DSH to disable/remove it before installing this connector.')
    if package not in dependencies and package not in bundles:
        return None
    path = directory / 'node_modules' / package
    if not (path / 'package.json').is_file():
        raise Error('COMPONENT_DRIFT', 'DSH package registration is incomplete; repair it through DSH first.')
    metadata = json.loads((path / 'package.json').read_text(encoding="utf-8"))
    if metadata.get('name') != package or package not in bundles or package not in dependencies:
        raise Error('COMPONENT_DRIFT', 'DSH bundle registration and installed package disagree.')
    dependency = dependencies[package]
    if dependency.startswith('link:'):
        declared = (directory / dependency[5:]).resolve()
        if declared != path.resolve():
            raise Error('COMPONENT_DRIFT', 'DSH dependency and actual package link disagree; preserve the pending record and repair through DSH.')
    return {'version': metadata['version'], 'path': str(path.resolve()),
            'dependency': dependency}


def apply(cli, profile, bundle, package, offline=False):
    args = ['plugin', '--profile', profile]
    # pnpm remove accepts configuration options, not add's command flags.
    args += ['add', '--ignore-scripts', str(bundle)] if bundle else ['remove', '--config.ignore-scripts=true', package]
    if offline:
        args.append('--offline' if bundle else '--config.offline=true')
    invoke(cli, args)


def backup(profile, directory):
    directory.mkdir(parents=True, mode=0o700)
    for name in PROFILE_FILES:
        source = safe_path(profile_path(profile) / name)
        if source.is_file():
            target = directory / name
            target.write_bytes(source.read_bytes())
            target.chmod(0o600)
