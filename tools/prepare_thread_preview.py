#!/usr/bin/env python3
"""Prepare an independent Thread Hub for personal Agents."""
import argparse
import hashlib
import json
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path,required=True)
    p.add_argument('--prefix',type=Path,required=True)
    args=p.parse_args()
    base=args.base.expanduser().absolute();prefix=args.prefix.expanduser().absolute()
    if base.is_symlink() or prefix.is_symlink() or prefix==Path.home()/'.local/opt/cosmesh':
        raise SystemExit('Use new dedicated preview paths, outside the stable installation.')
    entry=prefix/'bin/cosmesh'
    if not entry.is_file():raise SystemExit('Install the verified candidate into --prefix first.')
    if base.exists() and any(base.iterdir()) and not (base/'preview.json').is_file():
        raise SystemExit('Nonempty directory is not an owned Thread preview; preserved.')
    base.mkdir(parents=True,exist_ok=True,mode=0o755)
    if base.stat().st_uid!=os.geteuid():raise SystemExit('Run as the preview owner.')
    os.chmod(base,0o755)
    marker={'format':1,'owner_uid':os.geteuid(),'host':socket.gethostname(),'prefix':str(prefix)}
    if (base/'preview.json').exists() and json.loads((base/'preview.json').read_text())!=marker:
        raise SystemExit('Preview identity differs; existing setup preserved.')
    (base/'preview.json').write_text(json.dumps(marker,indent=2)+'\n');os.chmod(base/'preview.json',0o644)
    def cli(*arguments):
        proc=subprocess.run([str(entry),*map(str,arguments),'--json'],text=True,capture_output=True,timeout=180)
        try:result=json.loads(proc.stdout)
        except ValueError:raise SystemExit('Preview command returned no JSON; inspect its local log.')
        if not result.get('ok'):raise SystemExit(json.dumps(result,ensure_ascii=False))
        return result
    control=base/'control';control.mkdir(exist_ok=True,mode=0o700);os.chmod(control,0o700)
    if not (base/'hub/hub.json').exists():cli('hub','init','--hub-root',base/'hub')
    gateway=base/'hub/gateway.json'
    if not gateway.exists():gateway.write_text(json.dumps({'authority':socket.gethostname()})+'\n');os.chmod(gateway,0o600)
    discovery=base/'service.json'
    if not discovery.exists():
        token=hashlib.sha256(str(base).encode()).hexdigest()[:8]
        service={'format':1,'owner_uid':os.geteuid(),'endpoint':uuid4().hex,'hosts':[socket.gethostname()],
                 'socket':'/tmp/cosmesh-preview-%d-%s/hub.sock'%(os.geteuid(),token)}
        discovery.write_text(json.dumps(service,indent=2)+'\n');os.chmod(discovery,0o644)
    service=json.loads(discovery.read_text())
    session='cosmesh-thread-hub-020'
    if subprocess.run(['tmux','has-session','-t',session],capture_output=True).returncode:
        log=control/'hub.log'
        log.touch(exist_ok=True);os.chmod(log,0o600)
        command=shlex.join([str(entry),'hub','run','--hub-root',str(base/'hub'),'--service',str(discovery)])
        subprocess.run(['tmux','new-session','-d','-s',session,'exec '+command+' >>'+shlex.quote(str(log))+' 2>&1'],check=True)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
                connection.settimeout(.5);connection.connect(service['socket'])
            break
        except OSError:time.sleep(.1)
    else:raise SystemExit('Preview gateway did not become ready; inspect '+str(control/'hub.log'))
    library=cli('hub','community-init','--hub-root',base/'hub','--library-root',base/'library')
    work=base/'workbench'
    if not (work/'.cosmesh/config.json').exists():cli('thread','init','--root',work)
    connected=cli('thread','discover','--root',work,'--service',discovery)
    result={'base':str(base),'prefix':str(prefix),'host':socket.gethostname(),'discovery':str(discovery),
            'gateway_session':session,'workbench':str(work),'coordination':'personal_agents',
            'library':library['data'],
            'connection':connected['data']}
    (control/'ready.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');os.chmod(control/'ready.json',0o600)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
