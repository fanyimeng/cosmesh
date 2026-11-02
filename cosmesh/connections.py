"""Lazy, command-scoped control channels and private OpenSSH connection reuse."""
import contextlib
import contextvars
import functools
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import time

from .errors import Error

LIMIT = 1024 * 1024
_current = contextvars.ContextVar('cosmesh_connections', default=None)


class Frames:
    """Buffered JSON control frames; never used on a Git binary stream."""
    def __init__(self, channel):
        self.channel, self.buffer = channel, bytearray()

    def read(self, timeout=90):
        deadline = time.monotonic() + timeout
        while True:
            end = self.buffer.find(b'\n')
            if end >= 0:
                if end > LIMIT: raise Error('INPUT_TOO_LARGE', 'Control frame exceeds its limit.')
                raw = bytes(self.buffer[:end]); del self.buffer[:end+1]
                try: value = json.loads(raw)
                except (ValueError, UnicodeError): raise Error('SYNC_PROTOCOL', 'Malformed control frame.')
                if not isinstance(value, dict): raise Error('SYNC_PROTOCOL', 'Control frame must be an object.')
                return value
            if len(self.buffer) > LIMIT: raise Error('INPUT_TOO_LARGE', 'Control frame exceeds its limit.')
            remaining = deadline-time.monotonic()
            if remaining <= 0 or not select.select([self.channel], [], [], remaining)[0]:
                raise Error('SYNC_NETWORK', 'Control channel timed out.')
            data = self.channel.recv(65536) if isinstance(self.channel, socket.socket) else os.read(self.channel.fileno(), 65536)
            if not data:
                if not self.buffer: return None
                raise Error('SYNC_NETWORK', 'Control channel closed within a frame.')
            self.buffer.extend(data)


def send_frame(channel, value):
    raw = (json.dumps(value, ensure_ascii=True)+'\n').encode()
    if len(raw) > LIMIT: raise Error('INPUT_TOO_LARGE', 'Control frame exceeds its limit.')
    if isinstance(channel, socket.socket): channel.sendall(raw)
    else: channel.write(raw); channel.flush()


def control_options(arguments):
    """Pass all options explicitly; never read keys or edit the user's SSH config."""
    pool = _current.get()
    key = tuple(arguments)
    if pool is not None and key in pool.options: return pool.options[key]
    # ssh -G resolves aliases, user, jump route and identity paths without connecting.
    try:
        resolved = subprocess.run([arguments[0], '-G', *arguments[1:]], capture_output=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Error('SYNC_NETWORK', 'Cannot resolve the existing SSH connection.') from exc
    if resolved.returncode:
        raise Error('SYNC_NETWORK', 'Cannot resolve the existing SSH connection.')
    parent = Path('/private/tmp' if sys.platform == 'darwin' else '/tmp')
    host = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:8]
    directory = parent/('cosmesh-ssh-%d-%s' % (os.geteuid(), host))
    try: directory.mkdir(mode=0o700)
    except FileExistsError: pass
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise Error('AUTH_DENIED', 'CosMesh SSH control directory must be private and owned by this account.')
    digest = hashlib.sha256(resolved.stdout + b'\0' + repr(key).encode()).hexdigest()[:24]
    target = directory/digest
    if target.exists() or target.is_symlink():
        info = target.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise Error('AUTH_DENIED', 'CosMesh SSH control socket must be private and owned by this account.')
    options = ['-o', 'ControlMaster=auto', '-o', 'ControlPersist=120', '-o', 'ControlPath='+str(target)]
    if pool is not None: pool.options[key] = options
    return options


class Control:
    def __init__(self, config, pool):
        self.config, self.pool = config, pool
        self.channel = self.process = self.errors = self.reader = None
        self.service = self.identity = None
        self.sequence = 0
        self.used = 0

    def open(self):
        if self.config['kind'] == 'system':
            from .access import connect, system_user
            self.channel, self.service = connect(self.config)
            self.identity = system_user()
            self.reader = Frames(self.channel)
            self.pool.stats['local_channels'] += 1
        else:
            from .sync import system_ssh
            source = (Path(__file__).parent/'resources/community_rpc.py').read_text()
            command = shlex.join(['python3', '-c', source, self.config['discovery'], '--stream'])
            self.errors = tempfile.TemporaryFile()
            self.process = subprocess.Popen(system_ssh(self.config['host'])+[command], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=self.errors)
            self.pool.stats['ssh_rpc_processes'] += 1
            self.reader = Frames(self.process.stdout)
            hello = self.reader.read()
            if not hello or hello.get('stream') != 1 or not isinstance(hello.get('endpoint'), str):
                raise Error('SYNC_PROTOCOL', 'Invalid SSH control handshake.')
            if type(hello.get('uid')) is not int or not isinstance(hello.get('actor'), str):
                raise Error('AUTH_DENIED', 'SSH control channel did not verify an account.')
            self.identity = {'uid': hello['uid'], 'user': hello['actor']}
            self.service = {'endpoint': hello['endpoint']}

    def request(self, value):
        if self.reader is not None and time.monotonic()-self.used > 100: self.close()
        try:
            if self.reader is None: self.open()
            self.pool.stats['rpc_requests'] += 1
            if self.channel is not None:
                send_frame(self.channel, value); reply = self.reader.read()
            else:
                self.sequence += 1
                send_frame(self.process.stdin, {'id': self.sequence, 'request': value})
                frame = self.reader.read()
                if not frame or frame.get('id') != self.sequence:
                    raise Error('SYNC_PROTOCOL', 'SSH control response does not match its request.')
                reply = frame.get('reply')
            self.used = time.monotonic()
            if not isinstance(reply, dict): raise Error('SYNC_NETWORK', 'Control channel closed before its response.')
            if (reply.get('ok') or 'endpoint' in reply) and reply.get('endpoint') != self.service['endpoint']:
                raise Error('AUTH_DENIED', 'Control endpoint differs from trusted discovery.')
            if not reply.get('ok'):
                error = reply.get('error', {})
                raise Error(error.get('code', 'SYNC_PROTOCOL'), error.get('message', 'Control request failed.'))
            data = reply.get('data', {})
            if not isinstance(data, dict): raise Error('SYNC_PROTOCOL', 'Control reply data must be an object.')
            if data.get('actor') != self.identity['user'] or data.get('uid') != self.identity['uid']:
                raise Error('AUTH_DENIED', 'Control reply changed the authenticated account.')
            if self.channel is not None and reply.get('rpc_stream') != 1: self.close()
            return data
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.close()
            raise Error('SYNC_NETWORK', 'Control connection failed; local records are retained.') from exc
        except Exception:
            self.close(); raise

    def close(self):
        if self.channel is not None: self.channel.close(); self.channel = None
        if self.process is not None:
            if self.process.stdin:
                try: self.process.stdin.close()
                except OSError: pass
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
            if self.process.stdout: self.process.stdout.close()
            self.process = None
        if self.errors is not None: self.errors.close(); self.errors = None
        self.reader = None


class Pool:
    def __init__(self):
        self.channels, self.options = {}, {}
        self.stats = {'ssh_rpc_processes': 0, 'local_channels': 0, 'rpc_requests': 0}

    def request(self, config, value):
        key = tuple((k, config[k]) for k in ('kind', 'host', 'discovery') if k in config)
        channel = self.channels.setdefault(key, Control(config, self))
        return channel.request(value)

    def close(self):
        for channel in self.channels.values(): channel.close()


@contextlib.contextmanager
def session():
    existing = _current.get()
    if existing is not None:
        yield existing; return
    pool = Pool(); token = _current.set(pool)
    try: yield pool
    finally: pool.close(); _current.reset(token)


def networked(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with session(): return function(*args, **kwargs)
    return wrapped


def request(config, value):
    with session() as pool: return pool.request(config, value)
