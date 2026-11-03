"""Public Mesh discovery over the existing kernel-authenticated SSH gateway."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import shlex

from .access import DEFAULT_DISCOVERY
from .errors import Error
from .store import Store, atomic
from .sync import clone, synchronize, system_command, system_ssh


def settings():
    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
    path = Path(prefix) / 'client.json' if prefix else None
    return json.loads(path.read_text()) if path and path.is_file() else {}


def root_for(product, explicit=None):
    if explicit:
        return Path(explicit).expanduser()
    parent = Path(os.environ.get('COSMESH_DATA_DIR', str(Path.home() / '.local/share/cosmesh')))
    active = settings().get('mesh_root') if product == 'cosmesh' and not os.environ.get('COSMESH_DATA_DIR') else None
    return Path(active) if active else parent / product


def connection(root, ssh=None, service=None):
    if ssh:
        saved = settings()
        remembered = saved.get('discovery') if saved.get('ssh') == ssh else None
        return {'kind': 'ssh-system', 'host': ssh, 'discovery': service or remembered or os.environ.get('COSMESH_SERVICE', DEFAULT_DISCOVERY), 'repo': 'spaces'}
    if service or Path(os.environ.get('COSMESH_SERVICE', DEFAULT_DISCOVERY)).is_file():
        return {'kind': 'system', 'discovery': service or os.environ.get('COSMESH_SERVICE', DEFAULT_DISCOVERY), 'repo': 'spaces'}
    if root.joinpath('.cosmesh/config.json').is_file():
        remote = Store(root).client().get('remote')
        if remote and remote.get('kind') in {'system', 'ssh-system'}:
            return dict(remote, repo='spaces')
    saved = settings()
    if saved.get('ssh'):
        return {'kind': 'ssh-system', 'host': saved['ssh'], 'discovery': saved.get('discovery', DEFAULT_DISCOVERY), 'repo': 'spaces'}
    raise Error('HUB_CONNECTION_REQUIRED', 'Use space discover --ssh with your existing server connection once.')


def rpc(config, method):
    payload = method if isinstance(method,dict) else {'method': method}
    from .connections import request
    return request(config, {'protocol': 1, 'repo': 'spaces', 'operation': 'cosmesh-space', 'payload': payload})


def matches(path, config):
    if not (path / '.cosmesh/config.json').is_file():
        return False
    return Store(path).client().get('remote') == config


def cache(root, config, space, refresh=False):
    remote = dict(config, repo=space['id'])
    target = root
    if target.exists() and not matches(target, remote):
        target = (root.parent if root.parent.name == 'spaces' else root.parent / 'spaces') / space['id']
    if matches(target, remote):
        if refresh:
            synchronize(Store(target), 'cosmesh', publish=False)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        clone(target, 'cosmesh', remote, publish=False)
    return str(target)


def activate(path):
    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
    if prefix and not os.environ.get('COSMESH_DATA_DIR'):
        with (Path(prefix) / '.lock').open('a+') as handle:
            fcntl.lockf(handle, fcntl.LOCK_EX)
            data = settings()
            data['mesh_root'] = path
            atomic(Path(prefix) / 'client.json', json.dumps(data, ensure_ascii=False) + '\n')


def run(verb, root, ssh=None, service=None, user=None):
    config = connection(root, ssh, service)
    data = rpc(config, 'ensure' if verb == 'discover' else 'list')
    if verb == 'discover':
        own = next(s for s in data['spaces'] if s['id'] == data['own_id'])
        # Already-cached own work stays local; installation never pushes its drafts.
        data['root'] = cache(root, config, own)
        activate(data['root'])
        data.update(ready=True, own=own)
    elif verb == 'fetch':
        candidates = [s for s in data['spaces'] if user in (s['id'], s['owner'], s['title'])]
        personal = [s for s in candidates if s['personal'] and s['owner'] == user]
        candidates = personal or candidates
        if len(candidates) != 1:
            raise Error('SPACE_NOT_FOUND' if not candidates else 'AMBIGUOUS_ID', 'Choose a user or space ID from space list.')
        space = candidates[0]
        data = {'space': space, 'actor': data['actor'],
                'root': cache((root.parent if root.parent.name == 'spaces' else root.parent / 'spaces') / space['id'], config, space, refresh=True)}
    return data
