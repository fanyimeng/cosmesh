#!/usr/bin/env python3
"""Rootless, explicit tmux lifecycle for a model-ready Mesh coordinator."""
import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('start','status','stop'))
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--session',default='cosmesh-agent-preview')
    p.add_argument('--interval',type=int,default=30)
    p.add_argument('--refresh',action='store_true')
    args=p.parse_args()
    if not args.session.replace('-','').replace('_','').isalnum() or args.interval<5:
        raise SystemExit('Use a simple tmux session name and interval >=5.')
    executable=args.prefix.expanduser().resolve()/'bin/cosmesh'
    state=args.state.expanduser().resolve()
    if not executable.is_file() or not (state/'config.json').is_file() or state.stat().st_uid!=os.geteuid():
        raise SystemExit('Prepare the installed CLI and owned Mesh Agent state first.')
    def running():return subprocess.run(['tmux','has-session','-t',args.session],capture_output=True).returncode==0
    if args.action=='stop' and running():subprocess.run(['tmux','kill-session','-t',args.session],check=True)
    if args.action=='start' and not running():
        command=[str(executable),'mesh-agent','serve','--root',str(state),'--interval',str(args.interval),'--json']
        if args.refresh:command.append('--refresh')
        log=state/'service.log'
        with log.open('a') as handle:os.chmod(log,0o600)
        subprocess.run(['tmux','new-session','-d','-s',args.session,'exec '+shlex.join(command)+' >>'+shlex.quote(str(log))+' 2>&1'],check=True)
    result=subprocess.run([str(executable),'mesh-agent','status','--root',str(state),'--json'],capture_output=True,text=True,check=True)
    print(json.dumps({'running':running(),'session':args.session,'log':str(state/'service.log'),'status':json.loads(result.stdout)},ensure_ascii=False))


if __name__=='__main__':main()
