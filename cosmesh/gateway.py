"""Local kernel-authenticated Git access; one trusted relay between execution hosts."""
import fcntl
import json
import os
import re
import select
import shlex
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .access import connect, discovery, peer_identity, system_user
from .errors import Error
from .hub import Hub, REPO
from .store import safe_path
from .connections import Frames, send_frame, session

LIMIT = 1024 * 1024


def receive(channel):
    data = bytearray()
    deadline = time.monotonic() + 20
    while len(data) <= LIMIT:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([channel], [], [], remaining)[0]:
            raise Error('SYNC_NETWORK', 'Gateway handshake timed out.')
        byte = channel.recv(1) if isinstance(channel, socket.socket) else os.read(channel.fileno(), 1)
        if byte == b'\n':
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError):
                raise Error('SYNC_PROTOCOL', 'Malformed gateway header.')
            if not isinstance(value, dict):
                raise Error('SYNC_PROTOCOL', 'Gateway header must be an object.')
            return value
        if not byte:
            raise Error('SYNC_NETWORK', 'The gateway closed before its response.')
        data.extend(byte)
    raise Error('INPUT_TOO_LARGE', 'Gateway header exceeds its limit.')


def send(channel, value):
    data = (json.dumps(value, ensure_ascii=True) + '\n').encode()
    if isinstance(channel, socket.socket):
        channel.sendall(data)
    else:
        channel.write(data)
        channel.flush()


def request(value):
    expected = {'protocol', 'repo', 'operation'} | ({'payload'} if value.get('operation') == 'cosmesh-space' else set())
    if set(value) != expected or value['protocol'] != 1:
        raise Error('SYNC_PROTOCOL', 'Only the operation and repository belong in a client request.')
    if not isinstance(value['repo'], str) or not REPO.fullmatch(value['repo']):
        raise Error('PATH_INVALID', 'Invalid repository name.')
    if value['operation'] not in {'cosmesh-status', 'git-upload-pack', 'git-receive-pack', 'cosmesh-space'}:
        raise Error('COMMAND_DENIED', 'Unsupported gateway operation.')
    if 'payload' in value and not isinstance(value['payload'], dict):
        raise Error('INPUT_INVALID', 'Space request must be an object.')
    return value


def checked_reply(value, service):
    if not value.get('ok'):
        error = value.get('error', {})
        raise Error(error.get('code', 'SYNC_PROTOCOL'), error.get('message', 'Gateway denied the request.'))
    if value.get('endpoint') != service['endpoint']:
        raise Error('AUTH_DENIED', 'Gateway endpoint does not match trusted discovery.')
    return value


def open_request(config, operation, payload=None):
    connection, service = connect(config)
    try:
        value = {'protocol': 1, 'repo': config['repo'], 'operation': operation}
        if payload is not None:
            value['payload'] = payload
        send(connection, value)
        reply = checked_reply(receive(connection), service)
        connection.settimeout(None)
        return connection, reply
    except Exception:
        connection.close()
        raise


def status(config):
    connection, reply = open_request(config, 'cosmesh-status')
    connection.close()
    value = reply.get('data', {})
    if value.get('actor') != system_user()['user'] or value.get('uid') != os.geteuid():
        raise Error('AUTH_DENIED', 'Gateway did not verify the current system account.')
    return value


def space_control(config, payload):
    connection, reply = open_request(config, 'cosmesh-space', payload)
    connection.close()
    data = reply['data']
    if data.get('actor') != system_user()['user'] or data.get('uid') != os.geteuid():
        raise Error('AUTH_DENIED', 'Space request did not verify the current system account.')
    return data


def space_dispatch(hub, value, identity, channel, endpoint):
    data = hub.spaces(value['payload'], identity['user'])
    data.update(actor=identity['user'], uid=identity['uid'])
    send(channel, {'ok': True, 'endpoint': endpoint, 'rpc_stream': 1, 'data': data})


def control_dispatch(hub, value, identity, channel, endpoint):
    if value['operation'] == 'cosmesh-space':
        return space_dispatch(hub, value, identity, channel, endpoint)
    if value['operation'] != 'cosmesh-status':
        raise Error('COMMAND_DENIED', 'Only control requests belong on a control channel.')
    with hub.locked(value['repo']):
        data = hub.status(value['repo'], identity['user'])
    data.update(uid=identity['uid'], mode='system_identity')
    send(channel, {'ok': True, 'endpoint': endpoint, 'rpc_stream': 1, 'data': data})


def pump(connection, incoming, outgoing):
    """Keep Git's full duplex stream intact; headers are consumed without read-ahead."""
    def write_input():
        try:
            while True:
                data = os.read(incoming.fileno(), 65536)
                if not data:
                    break
                connection.sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    thread = threading.Thread(target=write_input, daemon=True)
    thread.start()
    while True:
        data = connection.recv(65536)
        if not data:
            break
        outgoing.write(data)
        outgoing.flush()


def git_client(discovery_path, arguments):
    if len(arguments) != 2 or arguments[0] != 'cosmesh':
        raise Error('INPUT_INVALID', 'Malformed Git gateway invocation.')
    try:
        parts = shlex.split(arguments[1])
    except ValueError:
        raise Error('INPUT_INVALID', 'Malformed Git request.')
    if len(parts) != 2 or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}\.git', parts[1]):
        raise Error('PATH_INVALID', 'Only logical repository names are accepted.')
    config = {'kind': 'system', 'discovery': discovery_path, 'repo': parts[1][:-4]}
    request({'protocol': 1, 'repo': config['repo'], 'operation': parts[0]})
    connection, _ = open_request(config, parts[0])
    try:
        pump(connection, sys.stdin.buffer, sys.stdout.buffer)
    finally:
        connection.close()
    return 0


def relay_entry(hub, key_id, stream=False):
    """Called only after the protected maintenance SSH key is authenticated."""
    try:
        public = discovery(hub.root.parent / 'service.json')
        if stream: send(sys.stdout.buffer, {'ok': True, 'endpoint': public['endpoint'], 'relay_stream': 2})
        reader = Frames(sys.stdin.buffer) if stream else None
        while True:
            header = reader.read(timeout=120) if stream else receive(sys.stdin.buffer)
            if header is None: return 0
            if stream:
                keys = json.loads((hub.root/'policy/keys.json').read_text())
                if hub.identity(key_id) != hub.settings()['admin'] or not keys[key_id].get('relay'):
                    raise Error('AUTH_DENIED', 'The service relay is no longer authorized.')
            if set(header) != {'uid', 'user', 'request', 'endpoint'} or type(header['uid']) is not int:
                raise Error('SYNC_PROTOCOL', 'Malformed service relay header.')
            identity = system_user(header['uid'])
            if identity['user'] != header['user']:
                raise Error('AUTH_DENIED', 'Execution nodes disagree about the system account.')
            value = request(header['request'])
            if header['endpoint'] != public['endpoint']:
                raise Error('AUTH_DENIED', 'Untrusted relay endpoint.')
            if not stream:
                return relay_dispatch(hub, value, identity, key_id, public['endpoint'])
            try:
                control_dispatch(hub, value, identity, sys.stdout.buffer, public['endpoint'])
            except Error as exc:
                send(sys.stdout.buffer, {'ok': False, 'endpoint': public['endpoint'], 'rpc_stream': 1, 'error': exc.as_dict()})
    except (Error, OSError, ValueError, KeyError) as exc:
        send(sys.stdout.buffer, {'ok': False, 'error': {'code': getattr(exc, 'code', 'SYNC_PROTOCOL'), 'message': str(exc)}})
        return 1


class RelayControl:
    """One protected maintenance-key process, with identity checked per request."""
    def __init__(self, config, endpoint):
        self.process = None
        self.errors = tempfile.TemporaryFile()
        try:
            from .sync import ssh_args
            command = ssh_args(config)+['-l', config['login'], config['host'], 'cosmesh-relay', 'v2']
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors)
            self.reader = Frames(self.process.stdout)
            hello = self.reader.read()
            if hello and not hello.get('ok'):
                error = hello.get('error', {})
                code = error.get('code', 'SYNC_NETWORK')
                raise Error('FEATURE_UNAVAILABLE' if code == 'COMMAND_DENIED' else code,
                            error.get('message', 'Relay handshake failed.'))
            if not hello or hello.get('relay_stream') != 2:
                raise Error('FEATURE_UNAVAILABLE', 'Relay does not support continuous control requests.')
            if hello.get('endpoint') != endpoint:
                raise Error('AUTH_DENIED', 'Relay endpoint differs from trusted discovery.')
        except BaseException:
            self.close(); raise

    def request(self, value, identity, endpoint):
        send_frame(self.process.stdin, {**identity, 'request': value, 'endpoint': endpoint})
        reply = self.reader.read()
        if not reply or reply.get('endpoint') != endpoint:
            raise Error('AUTH_DENIED', 'Relay returned an untrusted control response.')
        return reply

    def close(self):
        if self.process is not None:
            if self.process.stdin:
                try: self.process.stdin.close()
                except OSError: pass
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
            if self.process.stdout: self.process.stdout.close()
            self.process = None
        self.errors.close()


def relay_dispatch(hub, value, identity, key_id, endpoint):
    # SSH has separate stdin and stdout; socket dispatch uses one full-duplex fd.
    if value['operation'] == 'cosmesh-space':
        space_dispatch(hub, value, identity, sys.stdout.buffer, endpoint)
        return 0
    if value['operation'] == 'cosmesh-status':
        with hub.locked(value['repo']):
            data = hub.status(value['repo'], identity['user'])
            data.update(uid=identity['uid'], mode='system_identity')
            send(sys.stdout.buffer, {'ok': True, 'endpoint': endpoint, 'data': data})
        return 0
    hub.policy(value['repo'], identity['user'], value['operation'] == 'git-receive-pack')
    send(sys.stdout.buffer, {'ok': True, 'endpoint': endpoint, 'actor': identity['user'], 'uid': identity['uid']})
    return hub.dispatch(value['repo'], value['operation'], identity['user'], key_id=key_id, system_uid=identity['uid'])


def forward(connection, identity, value, service, config):
    from .sync import ssh_args
    backend = config['relay']
    command = ssh_args(backend) + ['-l', backend['login'], backend['host'], 'cosmesh-relay', 'v1']
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        try:
            send(process.stdin, {**identity, 'request': value, 'endpoint': service['endpoint']})
            reply = receive(process.stdout)
            send(connection, reply)
            if not reply.get('ok') or value['operation'] in {'cosmesh-status', 'cosmesh-space'}:
                process.wait(timeout=15)
                return
            connection.settimeout(None)
            # Socket input -> SSH stdin; SSH stdout -> socket output.
            def socket_input():
                try:
                    while True:
                        data = connection.recv(65536)
                        if not data:
                            break
                        process.stdin.write(data)
                        process.stdin.flush()
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
            threading.Thread(target=socket_input, daemon=True).start()
            while True:
                data = os.read(process.stdout.fileno(), 65536)
                if not data:
                    break
                connection.sendall(data)
            process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        connection = self.request
        connection.settimeout(15)
        try:
            identity = peer_identity(connection)
            value = request(receive(connection))
            service = self.server.service
            config = self.server.configuration
            authority = socket.gethostname() == config['authority']
            if value['operation'] not in {'cosmesh-space', 'cosmesh-status'}:
                if authority:
                    connection.settimeout(None)
                    self.server.hub.dispatch(value['repo'], value['operation'], identity['user'],
                                            system_uid=identity['uid'], channel=connection, endpoint=service['endpoint'])
                else: forward(connection, identity, value, service, config)
                return
            relay = None
            with session():
                try:
                    if not authority:
                        try: relay = RelayControl(config['relay'], service['endpoint'])
                        except Error as exc:
                            if exc.code != 'FEATURE_UNAVAILABLE': raise
                            forward(connection, identity, value, service, config); return
                    reader = Frames(connection)
                    while value is not None:
                        try:
                            if value['operation'] not in {'cosmesh-space', 'cosmesh-status'}:
                                raise Error('COMMAND_DENIED', 'A control connection cannot switch to a Git stream.')
                            if relay is not None: send(connection, relay.request(value, identity, service['endpoint']))
                            else: control_dispatch(self.server.hub, value, identity, connection, service['endpoint'])
                        except Error as exc:
                            send(connection, {'ok': False, 'endpoint': service['endpoint'], 'rpc_stream': 1, 'error': exc.as_dict()})
                        incoming = reader.read(timeout=120)
                        value = request(incoming) if incoming is not None else None
                finally:
                    if relay is not None: relay.close()
        except (Error, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            try:
                send(connection, {'ok': False, 'error': {'code': getattr(exc, 'code', 'SYNC_NETWORK'), 'message': str(exc)}})
            except OSError:
                pass


class Server(socketserver.ForkingMixIn, socketserver.UnixStreamServer):
    max_children = 32
    timeout = 1


def run(hub_root, discovery_path):
    hub = Hub(hub_root)
    service = discovery(discovery_path)
    if os.geteuid() != service['owner_uid'] or hub.root.stat().st_uid != os.geteuid():
        raise Error('AUTH_DENIED', 'Only the service maintainer can run a gateway.')
    config_path = safe_path(hub.root / 'gateway.json')
    if config_path.stat().st_uid != os.geteuid() or config_path.stat().st_mode & 0o077:
        raise Error('AUTH_DENIED', 'Gateway control configuration must be private.')
    config = json.loads(config_path.read_text())
    socket_path = Path(service['socket'])
    directory = safe_path(socket_path.parent)
    directory.mkdir(mode=0o711, exist_ok=True)
    if directory.stat().st_uid != os.geteuid() or directory.stat().st_mode & 0o022:
        raise Error('AUTH_DENIED', 'Gateway directory is owned or writable by another account.')
    os.chmod(directory, 0o711)
    with (directory / 'server.lock').open('a+') as handle:
        try:
            fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error('SERVICE_BUSY', 'A gateway is already running on this execution host.')
        if socket_path.exists():
            if not stat.S_ISSOCK(socket_path.stat().st_mode) or socket_path.stat().st_uid != os.geteuid():
                raise Error('PATH_INVALID', 'Unexpected existing gateway path.')
            socket_path.unlink()
        with Server(str(socket_path), Handler) as server:
            os.chmod(socket_path, 0o666)
            server.service, server.configuration, server.hub = service, config, hub
            print(json.dumps({'ready': True, 'host': socket.gethostname(), 'socket': str(socket_path)}), file=sys.stderr, flush=True)
            try:
                server.serve_forever(poll_interval=.25)
            finally:
                socket_path.unlink(missing_ok=True)
