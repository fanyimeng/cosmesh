"""Stdlib-only SSH control adapter; one process carries many bounded requests."""
import ctypes
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import struct
import sys

LIMIT = 1024*1024


def discovery():
    path = Path(sys.argv[1]); service = json.loads(path.read_text())
    for item in (path, path.parent):
        info = item.lstat()
        if item.is_symlink() or info.st_uid != service['owner_uid'] or info.st_mode & 0o022:
            raise ValueError('Unsafe discovery ownership')
    if socket.gethostname() not in service['hosts']: raise ValueError('No gateway on this host')
    return service


def connect(service):
    target = Path(service['socket'])
    for item in (target, target.parent):
        info = item.lstat()
        if item.is_symlink() or info.st_uid != service['owner_uid'] or (item == target.parent and info.st_mode & 0o022):
            raise ValueError('Unsafe gateway identity')
    if not stat.S_ISSOCK(target.stat().st_mode): raise ValueError('Invalid gateway socket')
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        channel.settimeout(90); channel.connect(str(target))
        if hasattr(socket, 'SO_PEERCRED'):
            _, uid, _ = struct.unpack('3i', channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        elif sys.platform == 'darwin':
            uid, gid = ctypes.c_uint(), ctypes.c_uint()
            if ctypes.CDLL(None, use_errno=True).getpeereid(channel.fileno(), ctypes.byref(uid), ctypes.byref(gid)):
                raise ValueError('Kernel peer credentials unavailable')
            uid = uid.value
        else: raise ValueError('Kernel peer credentials unavailable')
        if uid != service['owner_uid']: raise ValueError('Gateway peer mismatch')
        return channel
    except BaseException:
        channel.close(); raise


def read_file(handle):
    raw = handle.readline(LIMIT+1)
    if not raw: return None
    if len(raw) > LIMIT or not raw.endswith(b'\n'): raise ValueError('Invalid control frame size')
    value = json.loads(raw)
    if not isinstance(value, dict): raise ValueError('Control frame must be an object')
    return value


def output(value):
    raw = (json.dumps(value, ensure_ascii=True)+'\n').encode()
    if len(raw) > LIMIT: raise ValueError('Control reply is too large')
    sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()


def main():
    service = discovery(); actor, uid = pwd.getpwuid(os.geteuid()).pw_name, os.geteuid()
    stream = len(sys.argv) > 2 and sys.argv[2] == '--stream'
    channel = reader = None
    if stream: output({'stream': 1, 'actor': actor, 'uid': uid, 'endpoint': service['endpoint']})
    try:
        while True:
            frame = read_file(sys.stdin.buffer)
            if frame is None: return 0
            if stream:
                if set(frame) != {'id', 'request'} or type(frame['id']) is not int: raise ValueError('Invalid request sequence')
                request = frame['request']
                if not isinstance(request, dict) or request.get('operation') not in {'cosmesh-space', 'cosmesh-status'}:
                    raise ValueError('Only control requests belong on this channel')
            else:
                if not frame.get('method', '').startswith('community_'): raise ValueError('Only knowledge RPC is supported')
                request = {'protocol': 1, 'repo': 'spaces', 'operation': 'cosmesh-space', 'payload': frame}
            try:
                if channel is None:
                    channel = connect(service); reader = channel.makefile('rb')
                raw = (json.dumps(request, ensure_ascii=True)+'\n').encode()
                if len(raw) > LIMIT: raise ValueError('Control request is too large')
                channel.sendall(raw); reply = read_file(reader)
                if not reply: raise OSError('Gateway closed before its response')
                if (reply.get('ok') or 'endpoint' in reply) and reply.get('endpoint') != service['endpoint']: raise ValueError('Endpoint mismatch')
                if reply.get('ok'):
                    data = reply.get('data', {})
                    if data.get('uid') != uid or data.get('actor') != actor: raise ValueError('Caller identity mismatch')
                if not stream or reply.get('rpc_stream') != 1:
                    reader.close(); channel.close(); channel = reader = None
            except OSError:
                if reader is not None: reader.close()
                if channel is not None: channel.close()
                channel = reader = None
                reply = {'ok': False, 'endpoint': service['endpoint'], 'error': {'code': 'SYNC_NETWORK', 'message': 'Gateway connection failed; retry the original operation with its existing key.'}}
            if stream: output({'id': frame['id'], 'reply': reply})
            elif reply.get('ok'): output(reply['data'])
            else:
                error = reply.get('error', {})
                print('[COSMESH:'+error.get('code', 'SYNC_PROTOCOL')+'] '+error.get('message', 'Control request failed'), file=sys.stderr)
                return 1
            if not stream: return 0
    finally:
        if reader is not None: reader.close()
        if channel is not None: channel.close()


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print('[COSMESH:SYNC_PROTOCOL] '+str(exc), file=sys.stderr); raise SystemExit(1)
