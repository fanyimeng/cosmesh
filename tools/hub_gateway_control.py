#!/usr/bin/env python3
"""Rootless per-host gateway process control, deployed separately from user data."""
import argparse
import json
import os
import shlex
import socket
import struct
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'status', 'stop'])
    parser.add_argument('--base', default='/srv/cosmesh')
    args = parser.parse_args()
    base = Path(args.base)
    service = json.loads((base / 'service.json').read_text())
    if service['owner_uid'] != os.geteuid() or socket.gethostname() not in service['hosts']:
        raise SystemExit('Run as the service owner on a configured execution host.')
    session = 'cosmesh-hub'
    exists = subprocess.run(['tmux', 'has-session', '-t', session], capture_output=True).returncode == 0
    log = base / 'control' / ('gateway-' + socket.gethostname() + '.log')
    def peer_pid():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(.5)
                connection.connect(service['socket'])
                pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                return pid if uid == service['owner_uid'] else None
        except OSError:
            return None
    def listening():
        return peer_pid() is not None
    if args.action == 'start' and not exists:
        previous_pid = peer_pid()
        # Read the stable manager through Python to avoid NFS script-exec ETXTBSY
        # immediately after another node atomically replaces an entry wrapper.
        manager = Path.home() / '.local/opt/cosmesh/manager.py'
        command = shlex.join(['/usr/bin/python3', '-I', '-B', str(manager), 'launch', 'cosmesh',
                             'hub', 'run', '--hub-root', str(base / 'hub'), '--service', str(base / 'service.json')])
        command = 'exec ' + command + ' >>' + shlex.quote(str(log)) + ' 2>&1'
        subprocess.run(['tmux', 'new-session', '-d', '-s', session, command], check=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            exists = subprocess.run(['tmux', 'has-session', '-t', session], capture_output=True).returncode == 0
            current_pid = peer_pid()
            new_socket = current_pid is not None and current_pid != previous_pid
            if not exists or new_socket:
                break
            time.sleep(.1)
        if not exists or not new_socket or not listening():
            raise SystemExit('Gateway did not start; inspect ' + str(log))
    elif args.action == 'stop' and exists:
        subprocess.run(['tmux', 'kill-session', '-t', session], check=True)
        exists = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and listening():
            time.sleep(.1)
    print(json.dumps({'host': socket.gethostname(), 'tmux_session': session, 'running_session': exists,
                      'socket_present': Path(service['socket']).exists(), 'socket_listening': listening(), 'log': str(log)}))


if __name__ == '__main__':
    main()
