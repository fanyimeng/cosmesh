#!/usr/bin/env python3
"""Explicit, synthetic probes for two hosts using the same private test home directory.

Run only with --root pointing to an owned mode-700 .cosmesh-isolated-* directory
immediately under the caller's home. Never points at an installed workbench/Hub.
The controller invokes independent processes through the user's existing SSH login.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('setup','attest','hold','probe','release','write','inspect','crash','recover','cache','authority'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--user',required=True)
    parser.add_argument('--token',default='test')
    parser.add_argument('--count',type=int,default=8)
    parser.add_argument('--phase',choices=('before_commit','after_commit'),default='before_commit')
    args=parser.parse_args();root=args.root
    assert root.is_absolute() and root.resolve()==root and root.parent==Path.home()
    assert root.name.startswith('.cosmesh-isolated-') and root.stat().st_uid==os.geteuid()
    assert not root.stat().st_mode&0o077 and pwd.getpwuid(os.geteuid()).pw_name==args.user
    assert 1<=args.count<=32 and all(c.isalnum() or c=='-' for c in args.token)
    os.environ['COSMESH_DATA_DIR']=str(root/'data')
    os.environ['COSMESH_PAGE_BACKGROUND']='0'
    os.environ['COSMESH_PAGE_CACHE']='/tmp/cosmesh-sharedhome-'+root.name.rsplit('-',1)[-1]+'/pages'
    from cosmesh import __version__
    from cosmesh.errors import Error
    from cosmesh.personal import Personal,initialize as personal_init
    from cosmesh.personal_protocol import save_json,read_json
    from cosmesh.store import atomic
    from cosmesh.threads import Workbench,initialize
    host=socket.gethostname();work=root/'work';meta=root/'probe.json'
    result={'host':host,'uid':os.geteuid(),'version':__version__,'action':args.action}
    if args.action=='setup':
        initialize(work,args.user);wb=Workbench(work)
        with wb.base.locked():
            wb.load();tid=wb.create('共享 home 隔离验证','合成内容')['id'];wb.load()
            personal_init(wb);p=Personal(wb)
            huge=root/'large-reference.fits'
            with huge.open('wb') as stream:stream.truncate(100*1024**3)
            huge.chmod(0o600);aid=p.add(tid,huge)['id']
            save_json(meta,{'thread':tid,'asset':aid,'workspace':p.state['workspace'],'device':p.state['device'],'owner':args.user})
        from cosmesh.hub import Hub
        from cosmesh.personal_relay import initialize as relay_init,Relay
        hub=Hub(root/'hub');hub.initialize();relay_init(hub)
        Relay(hub,args.user).dispatch({'method':'personal_info'})
        result['fixture']=read_json(meta)
    else:
        fixture=read_json(meta);assert fixture['owner']==args.user
        wb=Workbench(work)
        if args.action=='attest':
            result.update(marker_sha=hashlib.sha256(meta.read_bytes()).hexdigest(),inode=meta.stat().st_ino,
                mount=subprocess.check_output(['findmnt','-T',str(root),'-o','TARGET,SOURCE,FSTYPE,OPTIONS','-n'],text=True).strip())
        elif args.action=='hold':
            with wb.base.locked():
                save_json(root/(args.token+'.held'),{'host':host,'pid':os.getpid()})
                deadline=time.monotonic()+45
                while not (root/(args.token+'.release')).exists():
                    if time.monotonic()>deadline:raise RuntimeError('Test lock holder timed out')
                    time.sleep(.05)
            result['released']=True
        elif args.action=='release':
            atomic(root/(args.token+'.release'),'release\n');result['released']=True
        elif args.action=='probe':
            started=time.monotonic()
            try:
                with wb.base.locked():result['lock']='acquired'
            except Error as exc:
                if exc.code!='STORE_BUSY':raise
                result['lock']=exc.code
            result['seconds']=time.monotonic()-started
        elif args.action=='write':
            ids=[];retries=0
            for i in range(args.count):
                for attempt in range(8):
                    try:
                        with wb.base.locked():
                            wb.load();r=wb.append(fixture['thread'],'parallel/'+host+'/'+str(i),key='parallel-'+host+'-'+str(i))
                            ids.append(r['id'])
                            if i==0:
                                wb.bind(fixture['thread'],'synthetic-agent',str(root),session_id='synthetic-'+host,host=host)
                                file=root/('result-'+host+'.csv');file.write_text('host,value\n'+host+',1\n')
                                Personal(wb).add(fixture['thread'],file)
                        break
                    except Error as exc:
                        if exc.code!='STORE_BUSY' or attempt==7:raise
                        retries+=1;time.sleep(.05*(attempt+1))
            result.update(ids=ids,retries=retries)
        elif args.action=='inspect':
            with wb.base.locked():
                wb.load();p=Personal(wb)
                with patch.object(p,'rpc',side_effect=AssertionError('Shared-file reference must not transfer bytes')):
                    opened=p.open(fixture['asset'],offline=True)
                result.update(head=wb.base.head,records=[{'id':r['doc']['meta']['id'],'body':r['doc']['body']} for r in wb.records(fixture['thread'])],
                    bindings=wb.client['bindings'],asset_count=len(p.state['assets']),device=p.state['device'],
                    workspace=p.state['workspace'],shared_file=opened,source_bytes=Path(opened['path']).stat().st_size,
                    dirty=wb.base.git('status','--porcelain'))
        elif args.action=='crash':
            with wb.base.locked():
                wb.load();before=wb.base.head;real_git=wb.base.git;real_unlink=Path.unlink
                def die(code):
                    save_json(root/(args.token+'.crash'),{'phase':args.phase,'before':before,'head':wb.base.head,'host':host})
                    os._exit(code)
                def git(*arguments,**kwargs):
                    if arguments and arguments[0]=='commit' and args.phase=='before_commit':die(73)
                    return real_git(*arguments,**kwargs)
                def unlink(path,*arguments,**kwargs):
                    if path==wb.base.pending and args.phase=='after_commit':die(74)
                    return real_unlink(path,*arguments,**kwargs)
                with patch.object(wb.base,'git',side_effect=git),patch.object(Path,'unlink',unlink):
                    wb.append(fixture['thread'],'crash/'+args.token,key='crash-'+args.token)
            raise AssertionError('Crash injection did not run')
        elif args.action=='recover':
            interrupted=read_json(root/(args.token+'.crash'))
            with wb.base.locked():
                wb.load();found=[r for r in wb.records(fixture['thread']) if r['doc']['body']=='crash/'+args.token]
                expected=0 if interrupted['phase']=='before_commit' else 1
                assert len(found)==expected and not wb.base.pending.exists()
                if expected:
                    retried=wb.append(fixture['thread'],'crash/'+args.token,key='crash-'+args.token)
                    assert retried['replayed'] and retried['id']==found[0]['doc']['meta']['id']
                else:assert wb.base.head==interrupted['before']
                result.update(recovered=True,preserved_count=len(found),head=wb.base.head,dirty=wb.base.git('status','--porcelain'))
        elif args.action=='cache':
            from cosmesh.status_pages import cache_root,refresh,database,meta as cache_meta,stamp
            directory=cache_root(wb);marker=directory/'host-marker.json'
            prior=read_json(marker)
            assert prior is None or prior['host']==host,'Cache was shared between hosts'
            save_json(marker,{'host':host});refresh(wb,directory)
            with database(directory) as db:
                assert cache_meta(db,'tokens')==stamp(wb)
                count=db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
            result.update(cache=str(directory),host_marker=read_json(marker),entries=count,head=wb.base.head,
                mount=subprocess.check_output(['findmnt','-T',str(directory),'-o','FSTYPE,SOURCE','-n'],text=True).strip())
        elif args.action=='authority':
            from cosmesh.hub import Hub
            from cosmesh.personal_relay import Relay
            hub=Hub(root/'hub');authority=json.loads((hub.root/'hub.json').read_text())['host']
            db=hub.root/'personal-v1'/hashlib.sha256(args.user.encode()).hexdigest()/'relay.sqlite3'
            before=hashlib.sha256(db.read_bytes()).hexdigest()
            try:
                value=Relay(hub,args.user).dispatch({'method':'personal_info'})
                assert host==authority;result['protocol']=value['protocol']
            except Error as exc:
                assert host!=authority and exc.code=='HUB_WRONG_NODE'
                assert before==hashlib.sha256(db.read_bytes()).hexdigest()
                result.update(error=exc.code,database_unchanged=True)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
