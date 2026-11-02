"""Operating-system identity and public discovery; no application login."""
import ctypes
import json
import os
import pwd
import socket
import stat
import struct
from pathlib import Path

from .errors import Error
from .protocol import USER
from .store import safe_path

DEFAULT_DISCOVERY = '/srv/cosmesh/service.json'


def system_user(uid=None):
    uid = os.geteuid() if uid is None else uid
    if type(uid) is not int or uid < 0:
        raise Error('AUTH_DENIED', 'Invalid system UID.')
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        raise Error('AUTH_DENIED', 'This UID has no system account.')
    if not USER.fullmatch(name):
        raise Error('IDENTITY_UNSUPPORTED', 'System account name is outside the supported document ID format.')
    return {'uid': uid, 'user': name}


def peer_identity(connection):
    if hasattr(socket, 'SO_PEERCRED'):
        _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    elif __import__('sys').platform == 'darwin':
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)):
            raise Error('AUTH_DENIED', 'Cannot verify local peer credentials.')
        uid = uid.value
    else:
        raise Error('IDENTITY_UNSUPPORTED', 'Local peer credentials are unavailable on this platform.')
    return system_user(uid)


def discovery(path=None):
    path = safe_path(path or os.environ.get('COSMESH_SERVICE', DEFAULT_DISCOVERY))
    if not path.is_file():
        raise Error('SERVICE_UNAVAILABLE', 'No local service discovery at ' + str(path))
    value = json.loads(path.read_text())
    owner = value.get('owner_uid')
    if type(owner) is not int or value.get('format') != 1 or not isinstance(value.get('endpoint'), str):
        raise Error('SYNC_PROTOCOL', 'Invalid service discovery.')
    if path.stat().st_uid != owner or path.stat().st_mode & 0o022:
        raise Error('AUTH_DENIED', 'Service discovery must be writable only by its maintainer.')
    # Protect the advertised socket identity against replacing the discovery file.
    parent = path.parent
    if parent.stat().st_uid != owner or parent.stat().st_mode & 0o022:
        raise Error('AUTH_DENIED', 'Unsafe discovery directory ownership or permissions.')
    socket_path = safe_path(value.get('socket', ''))
    if not socket_path.is_absolute() or len(os.fsencode(socket_path)) > 100:
        raise Error('PATH_INVALID', 'Invalid local socket path.')
    if not isinstance(value.get('hosts'), list) or socket.gethostname() not in value['hosts']:
        raise Error('SERVICE_UNAVAILABLE', 'No local gateway is configured on this execution host.')
    value.update(path=str(path), socket=str(socket_path))
    return value


def connect(config):
    service = discovery(config['discovery'])
    path = Path(service['socket'])
    parent = safe_path(path.parent)
    if not parent.is_dir() or parent.stat().st_uid != service['owner_uid'] or parent.stat().st_mode & 0o022:
        raise Error('AUTH_DENIED', 'Unsafe local gateway directory.')
    if not stat.S_ISSOCK(path.stat().st_mode) or path.stat().st_uid != service['owner_uid']:
        raise Error('AUTH_DENIED', 'Unexpected local gateway socket owner.')
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(15)
    try:
        connection.connect(str(path))
        if peer_identity(connection)['uid'] != service['owner_uid']:
            raise Error('AUTH_DENIED', 'Local gateway process has the wrong system identity.')
        return connection, service
    except Exception:
        connection.close()
        raise
