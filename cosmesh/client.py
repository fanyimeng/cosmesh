"""Explicit local application updates; normal document commands never call this."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from . import __version__
from .errors import Error


def config():
    prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
    if not prefix or not (Path(prefix) / 'client.json').is_file():
        raise Error('UPDATE_SOURCE_REQUIRED', 'Run the local-first installer once to configure updates.')
    saved = json.loads((Path(prefix) / 'client.json').read_text())
    if not all(saved.get(k) for k in ('ssh', 'source', 'skills')):
        raise Error('UPDATE_SOURCE_REQUIRED', 'Run the local-first installer once to configure updates.')
    return Path(prefix), saved


def fetch(cfg, file):
    host = cfg['ssh']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise Error('INPUT_INVALID', 'Invalid existing SSH alias.')
    command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ForwardAgent=no', '-o', 'ConnectTimeout=8', host,
               shlex.join(['cat', cfg['source'].rstrip('/') + '/' + file])]
    try:
        r = subprocess.run(command, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise Error('SYNC_NETWORK', 'Update server unavailable. Local Base and Mesh remain usable.') from None
    if r.returncode:
        raise Error('SYNC_NETWORK', 'Update server unavailable. Local Base and Mesh remain usable.')
    return r.stdout


def check():
    _, cfg = config()
    channel = json.loads(fetch(cfg, 'stable-local.json'))
    return {'installed_version': __version__, 'available_version': channel['version'],
            'update_available': tuple(map(int, channel['version'].split('.'))) > tuple(map(int, __version__.split('.'))),
            'execution': 'local', 'automatic_updates': False}


def update():
    prefix, cfg = config()
    script = fetch(cfg, 'install.sh')
    with tempfile.TemporaryDirectory(prefix='cosmesh-update-') as folder:
        file = Path(folder) / 'install.sh'
        file.write_bytes(script)
        env = dict(os.environ, COSMESH_BOOTSTRAP_PYTHON=sys.executable)
        r = subprocess.run(['sh', str(file), cfg['ssh'], '--prefix', str(prefix), '--dest', cfg['skills'],
                            '--source', cfg['source']], env=env, capture_output=True, text=True)
    if r.returncode:
        raise Error('UPDATE_FAILED', 'Local update stopped; documents are preserved.', (r.stderr or r.stdout)[-1800:])
    state = json.loads((prefix / 'state.json').read_text())
    saved = json.loads((prefix / 'client.json').read_text())
    return {'updated': True, 'version': state['current'], 'execution': 'local', 'data_unchanged': True,
            'mesh': saved.get('mesh_setup')}
