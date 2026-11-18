"""Isolated owner-only replicas, real files, conflicts, resumability and rollback."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from cosmesh.errors import Error
from cosmesh.hub import Hub
from cosmesh.personal import initialize
from tests.legacy_personal import Personal
from cosmesh.personal_protocol import CHUNK,DEFAULTS,digest,packed,save_json
from cosmesh.personal_relay import Relay,initialize as enable
from cosmesh.threads import Workbench,initialize as init_work


class PersonalTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'COSMESH_PAGE_BACKGROUND':'0'})
        env.start();self.addCleanup(env.stop)
        self.tmp=tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.hub=Hub(self.root/'hub');self.hub.initialize();enable(self.hub)
        def rpc(config,payload):
            actor=config['host']
            return {**self.hub.spaces(payload,actor),'actor':actor}
        self.rpc_patch=patch('cosmesh.spaces.rpc',side_effect=rpc);self.rpc_patch.start();self.addCleanup(self.rpc_patch.stop)
        self.a=self.work('mac-a','local-alice');self.b=self.work('mac-b','other-mac-name')
        with self.use(self.a) as p:
            self.t=p.wb.create('个人跨端工作','私人目标')['id'];p.wb.load();p.connect('alice')
        with self.use(self.b) as p:p.connect('alice')

    def work(self,name,user):
        root=self.root/name;init_work(root,user)
        wb=Workbench(root)
        with wb.base.locked(): wb.load();initialize(wb,uuid4().hex)
        return wb

    @contextmanager
    def use(self,wb):
        with wb.base.locked():
            wb.load();yield Personal(wb)

    def join(self):
        with self.use(self.a) as p:
            result=p.sync();workspace=result['workspace']
        with self.use(self.b) as p:p.join(workspace)
        return workspace

    def file(self,name='result.csv',raw=b'x,y\n1,2\n'):
        path=self.root/name;path.write_bytes(raw);return path

    def test_local_huge_reference_no_copy_and_other_agent(self):
        path=self.root/'huge.fits'
        with path.open('wb') as stream:stream.truncate(100*1024**3)
        with self.use(self.a) as p:
            aid=p.add(self.t,path)['id']
            self.assertEqual(p.open(aid)['path'],str(path))
            self.assertEqual(p.open(aid)['copied_bytes'],0)
            self.assertEqual(p.plan()['files'][0]['reason'],'file_limit')
            self.assertEqual(list(p.staging.iterdir()),[])
        # Independent Agent object reads the same registered reference.
        with self.use(Workbench(self.a.base.root)) as other:
            self.assertEqual(other.open(aid,True)['path'],str(path))
        self.assertEqual(self.a.base.git('status','--porcelain'),'')

    def test_cross_device_private_roundtrip_and_lazy_pull(self):
        path=self.file()
        with self.use(self.a) as p:
            aid=p.add(self.t,path)['id'];self.assertEqual(p.push()['files'][0]['state'],'server_available')
        self.join()
        with self.use(self.b) as p:
            row=p.asset(aid);self.assertFalse((p.cache/row['blob']).exists())
            result=p.open(aid);self.assertEqual(Path(result['path']).read_bytes(),path.read_bytes())
            self.assertEqual(p.wb.config['user'],'local-alice')
            with patch.object(p,'rpc',side_effect=AssertionError('cached read must stay offline')):
                self.assertEqual(p.open(aid,True)['path'],result['path'])
        with self.use(self.a) as p:
            self.assertEqual(p.open(aid)['path'],str(path))
        self.assertEqual(self.hub.spaces({'method':'list_threads'},'bob')['spaces'],[])

    def test_owner_isolation_including_raw_hash_and_workspace(self):
        workspace=self.join()
        alice=Relay(self.hub,'alice');bob=Relay(self.hub,'bob')
        sha=alice.dispatch({'method':'personal_head','workspace':workspace})['sha']
        self.assertEqual(bob.dispatch({'method':'personal_list'})['workspaces'],[])
        self.assertIsNone(bob.dispatch({'method':'personal_head','workspace':workspace})['sha'])
        with self.assertRaises(Error):bob.dispatch({'method':'personal_get','sha':sha,'offset':0})
        with self.assertRaises(Error):bob.dispatch({'method':'personal_get','sha':sha,'offset':0,'owner':'alice'})

    def test_bidirectional_offline_append_merge_and_pinned_sources(self):
        self.join()
        with self.use(self.a) as p:
            a=p.wb.append(self.t,'Mac A 离线进展');p.wb.load();p.sync()
        with self.use(self.b) as p:
            b=p.wb.append(self.t,'Mac B 离线进展');p.wb.load();p.sync()
        with self.use(self.a) as p:
            p.sync(pull=True)
            self.assertEqual({r['doc']['meta']['id'] for r in p.wb.records(self.t)},{a['id'],b['id']})
            self.assertEqual(p.wb.find(a['id'])['doc']['body'],'Mac A 离线进展')

    def test_concurrent_state_conflict_keeps_both_histories(self):
        self.join()
        with self.use(self.a) as p:
            p.wb.append(self.t,'完成',record_type='state',state='done');p.wb.load();p.sync()
        with self.use(self.b) as p:
            p.wb.append(self.t,'取消',record_type='state',state='cancelled');p.wb.load();before=p.wb.base.head
            with self.assertRaises(Error) as error:p.sync()
            self.assertEqual(error.exception.code,'STATE_CONFLICT')
            self.assertEqual(p.wb.base.head,before)

    def test_definition_conflict_keeps_local(self):
        self.join()
        for wb,title in ((self.a,'A title'),(self.b,'B title')):
            with self.use(wb) as p:
                item=p.wb.find(self.t);p.wb.edit(self.t,p.wb.ref(item)['version'],title=title)
        with self.use(self.a) as p:p.wb.load();p.sync()
        with self.use(self.b) as p:
            p.wb.load();before=p.wb.base.head
            with self.assertRaises(Error) as error:p.sync()
            self.assertEqual(error.exception.code,'PERSONAL_CONFLICT');self.assertEqual(before,p.wb.base.head)

    def test_lost_commit_reply_retries_same_result(self):
        with self.use(self.a) as p:
            real=p.rpc
            def lost(payload):
                result=real(payload)
                if payload['method']=='personal_commit':raise Error('SYNC_NETWORK','Lost reply')
                return result
            with patch.object(p,'rpc',side_effect=lost):
                with self.assertRaises(Error):p.sync()
            self.assertIsNone(p.state['base'])
            result=p.sync();self.assertTrue(result['personal_uploaded'])
            self.assertEqual(len(real({'method':'personal_list'})['workspaces']),1)

    def test_lost_reply_then_new_local_record(self):
        with self.use(self.a) as p:
            real=p.rpc
            def lost(payload):
                result=real(payload)
                if payload['method']=='personal_commit':raise Error('SYNC_NETWORK','Lost reply')
                return result
            with patch.object(p,'rpc',side_effect=lost):
                with self.assertRaises(Error):p.sync()
            p.wb.append(self.t,'重试前新增');p.wb.load()
            p.sync();self.assertEqual(len(p.wb.records(self.t)),1)

    def test_snapshot_cache_quota_preserves_work(self):
        with self.use(self.a) as p:
            p.state['policy']['cache_bytes']=1;p.save();before=p.wb.base.head
            with self.assertRaises(Error) as error:p.sync()
            self.assertEqual(error.exception.code,'STORAGE_QUOTA')
            self.assertEqual(p.wb.base.head,before)

    def test_resumable_upload_and_duplicate_chunk(self):
        relay=Relay(self.hub,'alice');raw=b'x'*(CHUNK+31);sha=digest(raw)
        relay.dispatch({'method':'personal_begin','sha':sha,'size':len(raw)})
        req={'method':'personal_put','sha':sha,'offset':0,'data':base64.b64encode(raw[:CHUNK]).decode()}
        relay.dispatch(req);again=relay.dispatch(req);self.assertEqual(again['offset'],CHUNK)
        self.assertEqual(relay.dispatch({'method':'personal_begin','sha':sha,'size':len(raw)})['offset'],CHUNK)
        relay.dispatch({'method':'personal_put','sha':sha,'offset':CHUNK,'data':base64.b64encode(raw[CHUNK:]).decode()})
        self.assertTrue(relay.dispatch({'method':'personal_begin','sha':sha,'size':len(raw)})['complete'])

    def test_download_resume_and_checksum_failure(self):
        raw=b'a'*(CHUNK+80)
        with self.use(self.a) as p:
            aid=p.add(self.t,self.file(raw=raw))['id'];p.push()
        self.join()
        with self.use(self.b) as p:
            sha=p.asset(aid)['blob'];part=p.cache/(sha+'.part');part.write_bytes(raw[:CHUNK]);part.chmod(0o600)
            self.assertEqual(Path(p.open(aid)['path']).read_bytes(),raw)
            (p.cache/sha).unlink();part.write_bytes(b'bad');part.chmod(0o600)
            with self.assertRaises(Error) as error:p.open(aid)
            self.assertEqual(error.exception.code,'CHECKSUM_MISMATCH');self.assertFalse(part.exists())

    def test_budgets_and_reservations(self):
        save_json(self.hub.root/'personal-v1/policy.json',dict(DEFAULTS,server_bytes=10,daily_bytes=4))
        relay=Relay(self.hub,'alice');sha=digest(b'abcde')
        relay.dispatch({'method':'personal_begin','sha':sha,'size':5})
        with self.assertRaises(Error) as error:relay.dispatch({'method':'personal_begin','sha':digest(b'other'),'size':6})
        self.assertEqual(error.exception.code,'STORAGE_QUOTA')
        with self.assertRaises(Error) as error:relay.dispatch({'method':'personal_put','sha':sha,'offset':0,'data':base64.b64encode(b'abcde').decode()})
        self.assertEqual(error.exception.code,'TRANSFER_BUDGET')

    def test_concurrent_processes_share_storage_reservation(self):
        save_json(self.hub.root/'personal-v1/policy.json',dict(DEFAULTS,server_bytes=10))
        code='''import sys
from cosmesh.hub import Hub
from cosmesh.personal_relay import Relay
from cosmesh.errors import Error
try:
 Relay(Hub(sys.argv[1]),'alice').dispatch({'method':'personal_begin','sha':sys.argv[2],'size':6})
 print('accepted')
except Error as e: print(e.code)
'''
        children=[subprocess.Popen([sys.executable,'-c',code,str(self.hub.root),digest(str(i).encode())],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(2)]
        outputs=[]
        for child in children:
            out,err=child.communicate(timeout=15);self.assertEqual(child.returncode,0,err);outputs.append(out.strip())
        self.assertEqual(sorted(outputs),['STORAGE_QUOTA','accepted'])

    def test_science_filter_credential_and_batch_limits(self):
        with self.use(self.a) as p:
            fits=self.file('slice.fits',b'SIMPLE  ='+b' '*100)
            aid=p.add(self.t,fits)['id'];self.assertEqual(p.plan()['files'][0]['reason'],'science_rule_required')
            p.add(self.t,fits,True);self.assertIsNone(p.plan()['files'][0]['reason'])
            p.add(self.t,self.file('.env',b'TOKEN=secret'))
            p.add(self.t,self.file('fake.pdf',b'\x7fELF'+b' '*100))
            reasons={r['reason'] for r in p.plan()['files']}
            self.assertIn('credential_or_cache_path',reasons);self.assertIn('executable_content',reasons)
            p.state['policy']['batch_bytes']=1
            self.assertEqual(p.plan([aid])['files'][0]['reason'],'batch_limit')

    def test_disabled_service_keeps_old_hub_behavior(self):
        hub=Hub(self.root/'old-hub');hub.initialize()
        with self.assertRaises(Error) as error:hub.spaces({'method':'personal_info'},'alice')
        self.assertEqual(error.exception.code,'FEATURE_UNAVAILABLE')
        self.assertEqual(hub.spaces({'method':'list_threads'},'alice')['spaces'],[])

    def test_no_project_overwrite_and_offline_missing_file(self):
        with self.use(self.a) as p:aid=p.add(self.t,self.file())['id'];p.push()
        self.join()
        with self.use(self.b) as p:
            with self.assertRaises(Error) as error:p.open(aid,True)
            self.assertEqual(error.exception.code,'OFFLINE')
            row=p.open(aid);self.assertFalse(row['project_files_overwritten'])

    def test_recover_after_git_commit_before_sidecar_save(self):
        self.join()
        with self.use(self.a) as p:p.wb.append(self.t,'下一步');p.wb.load();p.sync()
        with self.use(self.b) as p:
            with patch.object(p,'save',side_effect=OSError('simulated disk error')):
                with self.assertRaises(OSError):p.sync(pull=True)
            self.assertTrue((p.root/'apply.json').exists())
        with self.use(self.b) as p:
            self.assertFalse((p.root/'apply.json').exists())
            self.assertEqual(len(p.wb.records(self.t)),1)

    def test_cli_rejects_pull_upload_and_dry_mutation(self):
        from cosmesh.cli import parser
        from cosmesh.personal_cli import execute
        for argv in (['personal','sync','--pull','--files'],['personal','sync','--dry-run'],['personal','sync','--offline']):
            args=parser('cosmesh').parse_args(argv+['--root',str(self.a.base.root)])
            with self.assertRaises(Error):execute(args)
        args=parser('cosmesh').parse_args(['personal','policy','--set','daily_bytes=1','--root',str(self.a.base.root)])
        with self.assertRaises(Error) as error:execute(args)
        self.assertEqual(error.exception.code,'POLICY_DENIED')

    def test_fixed_historical_source_survives_join(self):
        with self.use(self.a) as p:
            old=p.wb.ref(p.wb.find(self.t))
            p.wb.append(self.t,'引用旧目标',refs=[old]);p.wb.load()
            p.wb.edit(self.t,p.wb.ref(p.wb.find(self.t))['version'],body='新目标');p.wb.load()
        self.join()
        with self.use(self.b) as p:
            self.assertEqual(p.wb.resolve(old)['body'],'私人目标')
            p.sync()

    def test_old_program_reads_and_writes_then_candidate_resumes(self):
        baseline=Path(__file__).resolve().parents[1]/'build/personal-sharing-0.4.1/baseline-src'
        if not baseline.is_dir():self.skipTest('Protected baseline source fixture is not available')
        self.join()
        args=[sys.executable,'-m','cosmesh','thread','note',self.t,'--body','旧版继续记录',
              '--root',str(self.a.base.root),'--json']
        result=subprocess.run(args,cwd=baseline,capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(baseline)})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        with self.use(self.a) as p:p.sync()
        with self.use(self.b) as p:
            p.sync(pull=True)
            self.assertIn('旧版继续记录',[r['doc']['body'] for r in p.wb.records(self.t)])

    def test_large_file_streamed_above_public_attachment_limit(self):
        path=self.root/'large.csv'
        with path.open('wb') as stream:
            for _ in range(21):stream.write(b'x'*(1024**2))
        with self.use(self.a) as p:
            aid=p.add(self.t,path)['id'];p.push()
        self.join()
        with self.use(self.b) as p:
            result=p.open(aid)
            self.assertEqual(Path(result['path']).stat().st_size,21*1024**2)
        from cosmesh.knowledge_protocol import MAX_FILE
        self.assertEqual(MAX_FILE,20*1024**2)


class PersonalGatewayTests(unittest.TestCase):
    # Reuse the existing fixture that starts an actual kernel-authenticated socket
    # gateway in a temporary directory; none of its other tests are inherited.
    from test_system_access import SystemAccessTests as _fixture
    setUp=_fixture.setUp
    start=_fixture.start
    stop=_fixture.stop

    def test_real_gateway_cli_selected_thread_and_ssh_proxy(self):
        enable(self.hub)
        def run(*arguments):
            result=subprocess.run([str(self.entry),*arguments,'--json'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            value=json.loads(result.stdout);self.assertTrue(value['ok'],value)
            return value['data']
        a=self.folder/'a';b=self.folder/'b'
        for root in (a,b):
            run('thread','init','--root',str(root))
            wb=Workbench(root)
            with wb.base.locked(): wb.load();initialize(wb,uuid4().hex)
        tid=run('thread','create','--root',str(a),'--title','本人内核身份')['id']
        path=self.folder/'result.csv';path.write_bytes(b'col\n'+b'1\n'*(CHUNK//2))
        aid=run('personal','asset','add',tid,str(path),'--root',str(a))['id']
        result=run('thread','personal',tid,'--files','--root',str(a),'--service',str(self.discovery))
        run('thread','fetch',tid,'--root',str(b),'--service',str(self.discovery),
            '--workdir',str(self.folder),'--agent','dsh')
        self.assertEqual(result['thread'],tid)
        opened=run('personal','asset','open',aid,'--root',str(b))
        self.assertEqual(Path(opened['path']).read_bytes(),path.read_bytes())
        # Exercise the exact SSH stdlib proxy against the local kernel gateway.
        import shlex
        from cosmesh.spaces import rpc
        original=subprocess.Popen
        def local_remote(arguments,**kwargs):
            if arguments[0]=='ssh':arguments=shlex.split(arguments[-1])
            return original(arguments,**kwargs)
        with patch('cosmesh.sync.control_options',return_value=[]),patch('cosmesh.connections.subprocess.Popen',side_effect=local_remote):
            reply=rpc({'kind':'ssh-system','host':'isolated-proxy','discovery':str(self.discovery),'repo':'spaces'},
                      {'method':'personal_get','sha':opened['sha'],'offset':0})
        self.assertEqual(base64.b64decode(reply['data']),path.read_bytes()[:CHUNK])


if __name__=='__main__':unittest.main()
