"""Schema-2 workflows through actual Git packs and pre-receive hooks in disposable spaces."""
import base64
import json
import io
import os
import shlex
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh.errors import Error
from cosmesh.hub import Hub
from cosmesh.protocol import encode
from cosmesh.store import Store
from cosmesh.threads import Workbench, initialize
from cosmesh import sync


class ThreadFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hub = Hub(self.root / 'hub'); self.hub.initialize()
        self.entry = self.root / 'prefix/bin/cosmesh'; self.entry.parent.mkdir(parents=True)
        source = str(Path(__file__).resolve().parents[1])
        code = 'import sys;sys.path.insert(0,%r);from cosmesh.cli import main;raise SystemExit(main())' % source
        self.entry.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-c', code]) + ' "$@"\n'); self.entry.chmod(0o700)
        self.configs = {}
        self.keys = {}
        self.repos = {}
        for index, user in enumerate(getattr(self, 'participants', ('alice', 'bob', 'carol')), 1):
            public = 'ssh-ed25519 ' + base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + bytes([index]) * 32).decode()
            self.keys[user] = self.hub.register_key(user, public, self.entry)['key_id']
            identity = self.root / (user + '.key'); identity.write_text('isolated-test'); identity.chmod(0o600)
            known = self.root / (user + '.known'); known.write_text('isolated-test'); known.chmod(0o600)
            wrapper = self.root / (user + '-ssh')
            code = ('import os,sys;sys.path.insert(0,%r);os.environ["SSH_CONNECTION"]="test";'
                    'os.environ["SSH_ORIGINAL_COMMAND"]=sys.argv[-1];from cosmesh.hub import Hub;'
                    'from cosmesh.errors import Error;h=Hub(%r)\n' % (source, str(self.hub.root)))
            code += 'try: raise SystemExit(h.serve(%r))\nexcept Error as e: print("[COSMESH:"+e.code+"] "+e.message,file=sys.stderr);raise SystemExit(1)' % self.keys[user]
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-c', code]) + ' "$@"\n'); wrapper.chmod(0o700)
            self.configs[user] = {'host':'test-hub','port':22,'login':'service','repo':'spaces',
                                  'identity':str(identity),'known_hosts':str(known),'host_key_alias':'test-hub','wrapper':str(wrapper)}
        def status(config):
            user = Path(config['identity']).stem
            return self.hub.status(config['repo'], user)
        def git_env(config):
            return {'GIT_SSH_COMMAND':shlex.quote(self.configs[Path(config['identity']).stem]['wrapper']),'GIT_SSH_VARIANT':'ssh'}
        def rpc(config, method):
            user = Path(config['identity']).stem
            with patch.dict(os.environ, {'COSMESH_INSTALL_PREFIX':str(self.root/'prefix')}):
                result = self.hub.spaces(method if isinstance(method,dict) else {'method':method},user)
            return {**result,'actor':user}
        for name, target in [('cosmesh.sync.remote_status',status),('cosmesh.sync.git_transport',git_env),('cosmesh.spaces.rpc',rpc)]:
            p = patch(name,target);p.start();self.addCleanup(p.stop)
        for user in self.configs:
            result = rpc(self.connection(user),'ensure_threads');self.repos[user]=result['own_id']
        self.wbs = {user:self.workbench(user) for user in self.configs}

    def connection(self,user):
        return {k:v for k,v in self.configs[user].items() if k != 'wrapper'}

    def workbench(self,user,name=None,local_user=None):
        root = self.root/(name or user)
        initialize(root,local_user or user)
        wb=Workbench(root)
        with wb.base.locked():
            wb.load();wb.client['connection']=self.connection(user);wb.save_client();wb.discover()
        return wb

    @contextmanager
    def use(self,user):
        wb=self.wbs[user] if isinstance(user,str) else user
        with wb.base.locked():
            wb.load();yield wb

    def shared(self,user,title,body=''):
        with self.use(user) as wb:
            thread=wb.create(title,body);wb.load();wb.share(thread['id']);result=wb.synchronize()
            self.assertTrue(result['synchronized'],result)
            return thread['id']

    def update(self,user):
        with self.use(user) as wb:
            result=wb.synchronize();self.assertTrue(result['synchronized'],result)


class ThreadTests(ThreadFixture):
    def test_private_idempotency_binding_and_safe_first_publication(self):
        wb=self.workbench('alice','mac','local-mac-name')
        with self.use(wb):
            t=wb.create('单位检查','旧私人文字',key='create-1');wb.load()
            self.assertTrue(wb.create('单位检查','旧私人文字',key='create-1')['replayed'])
            with self.assertRaises(Error):wb.create('不同内容',key='create-1')
            wb.edit(t['id'],t['version'],body='可以公开的概况');wb.load()
            wb.append(t['id'],record_type='state',state='done')
            with self.assertRaises(Error) as conflict:
                wb.append(t['id'],record_type='state',state='archived')
            self.assertEqual(conflict.exception.code,'STATE_CONFLICT')
            wb.load();self.assertEqual(wb.present(wb.find(t['id']))['work_state'],'done')
            wb.bind(t['id'],'test-agent',str(self.root),session_id='private-session')
            note=wb.append(t['id'],'正式工作记录');wb.load()
            wb.share(t['id']);self.assertTrue(wb.synchronize()['synchronized'])
            self.assertEqual(wb.context(t['id'])['thread']['id'],t['id'])
            self.assertTrue(wb.context(t['id'])['thread']['shared'])
            public_store=self.hub.repo(self.repos['alice'])
            all_history=public_store.git('log','--all','-p')
            self.assertNotIn('旧私人文字',all_history)
            self.assertNotIn('private-session',all_history)
            self.assertIn(note['id'],public_store.documents(public_store.snapshot(public_store.git('rev-parse','main'))[1]))
            self.assertEqual(wb.base.git('status','--porcelain'),'')
            self.assertEqual(wb.path.stat().st_mode & 0o777,0o600)

    def test_a_requests_people_b_c_assign_four_threads_and_deliver(self):
        ta=self.shared('alice','综合分析')
        work={u:[self.shared(u,u+'单位'),self.shared(u,u+'标定')] for u in ('bob','carol')}
        requests={}
        with self.use('alice') as a:
            for user in work:requests[user]=a.append(ta,'请检查并给出依据','request',recipient=user)['id'];a.load()
            self.assertTrue(a.synchronize()['synchronized'])
        for user in work:
            with self.use(user) as b:
                b.synchronize();q=b.find(requests[user]);ref=b.ref(q)
                assigned=b.append(ta,'已安排','assign',request=ref,work=[b.ref(b.find(x)) for x in work[user]])
                b.load();self.assertFalse(b.request_state(b.find(q['doc']['meta']['id']))['confirmed'])
                results=[]
                for index,thread in enumerate(work[user]):
                    results.append(b.append(thread,'检查结果 '+str(index),'result')['id']);b.load()
                b.append(ta,'结果已交付','delivery',request=ref,refs=[b.ref(b.find(x)) for x in results]);b.load()
                result=b.synchronize();self.assertTrue(result['synchronized'],result)
        with self.use('alice') as a:
            a.synchronize();context=a.context(ta)
            self.assertEqual([x['state'] for x in context['requests']],['delivered','delivered'])
            cited={x['source']['id'] for x in context['citations'] if x.get('document',{}).get('type')=='result'}
            self.assertEqual(len(cited),4)
            own_repo=a.stores[self.repos['alice']]
            own_docs=own_repo.documents(own_repo.snapshot()[1])
            self.assertFalse(any(x['meta'].get('type')=='result' for x in own_docs.values()))
            a.bind(ta,'test-agent',str(self.root),session_id='private-coordinator-session')
            private=a.create('私人协调备忘','private-coordinator-body')['id'];a.load()
            a.append(private,'private-coordinator-backlink',refs=[a.ref(a.find(ta))]);a.load()
            heads={repo:store.head for repo,store in a.stores.items()}
            packet=a.coordination(ta)
            self.assertEqual(packet['coordination']['default_coordinator'],'alice')
            self.assertEqual(len(packet['related_work']),4)
            self.assertTrue(packet['evidence_complete'])
            self.assertEqual({repo:store.head for repo,store in a.stores.items()},heads)
            self.assertEqual({x['source']['id'] for x in packet['citations'] if x.get('document',{}).get('type')=='result'},cited)
            for secret in ('private-coordinator-session','private-coordinator-body','private-coordinator-backlink'):
                self.assertNotIn(secret,json.dumps(packet))
            self.assertNotIn('local_execution',packet)
            delegated=a.append(ta,'请综合四项结果并指出缺口','request',recipient='bob')['id'];a.load()
            self.assertTrue(a.synchronize()['synchronized'])
        with self.use('bob') as b:
            b.synchronize();packet=b.coordination(delegated)
            self.assertEqual(packet['coordination']['acting_user'],'bob')
            self.assertEqual(packet['coordination']['default_coordinator'],'alice')
            self.assertEqual(packet['focus_request']['id'],delegated)
            refs=[b.citation(x['id']+'@'+x['version']) for x in packet['source_index'] if x['id'] in cited]
            combined=b.append(work['bob'][0],'四项结果的综合，保留各自依据','result',refs=refs)['id'];b.load()
            b.append(ta,'综合结果交付','delivery',request=b.ref(b.find(delegated)),refs=[b.ref(b.find(combined))]);b.load()
            self.assertTrue(b.synchronize()['synchronized'])
            self.assertEqual(b.find(combined)['doc']['meta']['author'],'bob')
        with self.use('alice') as a:
            a.synchronize();packet=a.coordination(delegated)
            self.assertTrue(cited <= {x['id'] for x in packet['source_index']})
            a.append(ta,'采用综合结果','adopt',request=a.ref(a.find(delegated)),refs=[a.ref(a.find(combined))]);a.load()
            self.assertTrue(a.synchronize()['synchronized'])
            self.assertEqual(a.request_state(a.find(delegated))['state'],'adopted')

    def test_offline_result_first_retry_and_correction_provenance(self):
        ta=self.shared('alice','分析');tb=self.shared('bob','检查')
        with self.use('alice') as a:
            q=a.append(ta,'请检查','request',recipient='bob')['id'];a.load();a.synchronize()
        with self.use('bob') as b:
            b.synchronize();r=b.append(tb,'原始结果','result')['id'];b.load()
            ref=b.ref(b.find(r));qref=b.ref(b.find(q))
            d=b.append(ta,'交付','delivery',request=qref,refs=[ref],key='delivery-1');b.load()
            replay=b.append(ta,'交付','delivery',request=qref,refs=[ref],key='delivery-1')
            self.assertEqual(d['id'],replay['id'])
            with patch('cosmesh.sync.remote_status',side_effect=Error('SYNC_NETWORK','offline')):
                result=b.synchronize();self.assertFalse(result['synchronized'])
            self.assertFalse(b.accepted(b.find(r)))
            result=b.synchronize();self.assertTrue(result['synchronized'],result)
            b.append(tb,'更正后的结果','correction',supersedes=ref);b.load();b.synchronize()
        with self.use('alice') as a:
            a.synchronize();citations=a.context(ta)['citations']
            original=next(x for x in citations if x['source']['id']==r)
            self.assertEqual(original['document']['body'],'原始结果')
            self.assertEqual(original['corrections'][0]['body'],'更正后的结果')
            packet=a.coordination(ta)
            original=next(x for x in packet['citations'] if x['source']['id']==r)
            self.assertEqual(original['document']['body'],'原始结果')
            correction=original['corrections'][0]
            self.assertIn(correction['id'],{x['id'] for x in packet['source_index']})

    def test_private_reference_and_other_person_response_rejected(self):
        ta=self.shared('alice','共享')
        with self.use('alice') as a:
            private=a.create('私人');a.load()
            with self.assertRaises(Error) as caught:a.append(ta,'引用','link',refs=[a.ref(a.find(private['id']))])
            self.assertEqual(caught.exception.code,'PRIVATE_REFERENCE')
            q=a.append(ta,'请 B 做','request',recipient='bob')['id'];a.load();a.synchronize()
        with self.use('carol') as c:
            c.synchronize()
            with self.assertRaises(Error) as caught:c.append(ta,'冒领','decline',request=c.ref(c.find(q)))
            self.assertEqual(caught.exception.code,'AUTH_DENIED')
            c.append(ta,'公开讨论');c.load();self.assertTrue(c.synchronize()['synchronized'])

    def test_concurrent_append_and_actual_hook_rejects_tampering(self):
        ta=self.shared('alice','共享讨论');self.update('bob')
        other=self.workbench('bob','bob-second')
        with self.use('bob') as b:
            record=b.append(ta,'B 的第一条');b.load()
        with self.use(other) as b2:
            b2.append(ta,'B 的并发第二条');b2.load();self.assertTrue(b2.synchronize()['synchronized'])
        with self.use('bob') as b:
            self.assertTrue(b.synchronize()['synchronized'])
            self.assertEqual(len(b.records(ta)),2)
            store=b.stores[self.repos['alice']];path='documents/'+record['id']+'.md'
            before=store.path(path).read_text()
            store.commit({path:before.replace('B 的第一条','篡改旧记录')},'tamper')
            store.commit({path:before},'restore')
            reply=b.synchronize()
            self.assertFalse(reply['synchronized'])
            self.assertIn('HISTORY_PROTECTED',str(reply['errors']))


class PersonalCoordinationCLITests(unittest.TestCase):
    def test_offline_context_and_pinned_summary_without_model_or_server(self):
        from cosmesh.cli import main, base_main
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as directory:
            root=Path(directory)/'work'
            def cli(*arguments,base=False):
                output=io.StringIO()
                with redirect_stdout(output):
                    argv=['thread',*arguments,'--root',str(root),'--json']
                    if base:
                        with patch.object(sys,'argv',['cosbase',*argv]):code=base_main()
                    else:
                        code=main(argv)
                value=json.loads(output.getvalue())
                self.assertEqual(code,0,value)
                self.assertTrue(value['ok'],value)
                return value['data']
            cli('init')
            original=cli('create','--title','分析','--body','最初的依据')
            target=cli('create','--title','汇总')
            cli('edit',original['id'],'--expect',original['version'],'--body','修改后的依据')
            summary=cli('result',target['id'],'--body','根据最初版本综合',
                        '--ref',original['id']+'@'+original['version'],base=True)
            self.assertEqual(summary['refs'][0]['version'],original['version'])
            before=Store(root).head
            packet=cli('coordinate',target['id'],'--offline')
            self.assertEqual(Store(root).head,before)
            evidence=next(x for x in packet['citations'] if x['source']['id']==original['id'])
            self.assertEqual(evidence['document']['body'],'最初的依据')
            self.assertFalse(packet['coordination']['executes_actions'])

    def test_record_backlinks_and_focused_context_are_offline_and_read_only(self):
        from cosmesh.cli import main, base_main
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as directory:
            root = Path(directory) / 'work'
            def cli(*arguments, base=False):
                output = io.StringIO()
                argv = ['thread', *arguments, '--root', str(root), '--json']
                with redirect_stdout(output):
                    if base:
                        with patch.object(sys, 'argv', ['cosbase', *argv]):
                            code = base_main()
                    else:
                        code = main(argv)
                value = json.loads(output.getvalue())
                self.assertEqual(code, 0, value)
                return value['data']
            cli('init')
            tb = cli('create', '--title', '原始工作')
            b = cli('result', tb['id'], '--body', '原始依据')
            ta = cli('create', '--title', '综合工作')
            a = cli('result', ta['id'], '--body', '适用条件明确的综合', '--ref', b['id'])
            head = Store(root).head
            links = cli('backlinks', b['id'], '--offline', base=True)
            self.assertEqual(links['coverage'], 'local_and_cached_mesh')
            self.assertEqual([x['record']['id'] for x in links['backlinks']], [a['id']])
            self.assertEqual(links['backlinks'][0]['target']['version'], b['version'])
            self.assertEqual([x['record']['id'] for x in cli('context', tb['id'], '--offline')['backlinks']], [a['id']])
            context = cli('context', a['id'], '--offline')
            self.assertEqual(context['focus_record']['id'], a['id'])
            self.assertEqual(context['thread']['id'], ta['id'])
            self.assertEqual(context['citations'][0]['document']['body'], '原始依据')
            self.assertEqual(cli('show', a['id'], '--offline')['id'], a['id'])
            self.assertEqual(Store(root).head, head)


class RecordBacklinkTests(ThreadFixture):
    participants = ('alice', 'bob', 'carol', 'dave')

    def test_d_finds_a_synthesis_via_b_c_and_reuses_original_record(self):
        ta = self.shared('alice', 'TA 综合')
        tb = self.shared('bob', 'TB 检查')
        tc = self.shared('carol', 'TC 检查')
        td = self.shared('dave', 'TD 后续需求')
        requests, results = {}, {}
        with self.use('alice') as a:
            for user in ('bob', 'carol'):
                requests[user] = a.append(ta, '请提供检查结果', 'request', recipient=user)['id']
                a.load()
            self.assertTrue(a.synchronize()['synchronized'])
        for user, thread in (('bob', tb), ('carol', tc)):
            with self.use(user) as worker:
                worker.synchronize(publish=False)
                qref = worker.ref(worker.find(requests[user]))
                worker.append(ta, '承接', 'assign', request=qref, work=[worker.ref(worker.find(thread))])
                worker.load()
                result = worker.append(thread, user + ' 的依据，数据版本 v1', 'result')['id']
                worker.load()
                results[user] = worker.ref(worker.find(result))
                worker.append(ta, '交付', 'delivery', request=qref, refs=[results[user]])
                worker.load()
                self.assertTrue(worker.synchronize()['synchronized'])
        with self.use('alice') as a:
            a.synchronize(publish=False)
            combined = a.append(ta, 'TA_rec01：基于两份 v1 结果的综合', 'result',
                                refs=[results['bob'], results['carol'], dict(results['bob'])])['id']
            a.load()
            self.assertTrue(a.synchronize()['synchronized'])
            private = a.create('私人结果', 'private-reuse-overview')['id']
            a.load()
            a.append(private, 'private-reuse-body', refs=[results['bob'], a.ref(a.find(tb))])
            a.load()
            a.bind(ta, 'test-agent', str(self.root), session_id='private-reuse-session')
            heads = {repo: store.head for repo, store in a.stores.items()}
            for packet in (a.backlinks(tb), a.context(tb), a.coordination(tb)):
                for secret in ('private-reuse-overview', 'private-reuse-body', 'private-reuse-session'):
                    self.assertNotIn(secret, json.dumps(packet))
            self.assertEqual(heads, {repo: store.head for repo, store in a.stores.items()})
        with self.use('dave') as d:
            d.synchronize(publish=False)
            request = d.append(td, '请 B 检查现有成果能否用于这次 v1 需求', 'request', recipient='bob')['id']
            d.load()
            self.assertTrue(d.synchronize()['synchronized'])
            heads = {repo: store.head for repo, store in d.stores.items()}
            for thread, user in ((tb, 'bob'), (tc, 'carol')):
                links = d.backlinks(thread)
                matches = [x for x in links['backlinks'] if x['record']['id'] == combined]
                self.assertEqual(len(matches), 1)  # Duplicate forward references produce one edge.
                self.assertEqual(matches[0]['target'], results[user])
                self.assertEqual(matches[0]['relation'], 'refs')
                self.assertEqual(matches[0]['thread']['id'], ta)
                self.assertTrue(matches[0]['record']['accepted'])
                self.assertEqual(links['coverage'], 'cached_mesh')
                self.assertIn(combined, {x['record']['id'] for x in d.coordination(thread)['backlinks']})
            record_links = d.backlinks(results['bob']['id'])
            self.assertIn(combined, {x['record']['id'] for x in record_links['backlinks']})
            packet = d.context(combined)
            self.assertEqual(packet['focus_record']['id'], combined)
            self.assertEqual({x['source']['id'] for x in packet['citations']}, {x['id'] for x in results.values()})
            self.assertEqual(d.request_state(d.find(request))['state'], 'pending')
            self.assertEqual(heads, {repo: store.head for repo, store in d.stores.items()})
            d.load()  # No persistent reverse index is needed to recover the same links.
            self.assertIn(combined, {x['record']['id'] for x in d.backlinks(tb)['backlinks']})
        with self.use('bob') as b:
            b.synchronize(publish=False)
            b.append(td, '核对需求和版本后复用 A 的结果', 'delivery', request=b.ref(b.find(request)),
                     refs=[b.ref(b.find(combined))])
            b.load()
            self.assertTrue(b.synchronize()['synchronized'])
        with self.use('dave') as d:
            d.synchronize(publish=False)
            context = d.context(td)
            self.assertEqual(context['requests'][0]['state'], 'delivered')
            evidence = next(x for x in context['citations'] if x['source']['id'] == combined)
            self.assertEqual(evidence['document']['author'], 'alice')
            self.assertFalse(any(x['doc']['meta'].get('type') == 'result' for x in d.records(td)))

    def test_corrections_pending_publication_and_old_source_versions_remain_distinct(self):
        ta = self.shared('alice', '综合')
        tb = self.shared('bob', '依据', 'v1 概况')
        with self.use('bob') as b:
            result = b.append(tb, '原始结果', 'result')['id']
            b.load()
            b.synchronize()
        with self.use('alice') as a:
            a.synchronize(publish=False)
            old_thread = a.ref(a.find(tb))
            original = a.ref(a.find(result))
            summary = a.append(ta, '尚未发布的综合', 'result', refs=[original, old_thread])['id']
            a.load()
            self.assertFalse(next(x for x in a.backlinks(result)['backlinks'] if x['record']['id'] == summary)['record']['accepted'])
        with self.use('dave') as d:
            d.synchronize(publish=False)
            self.assertNotIn(summary, {x['record']['id'] for x in d.backlinks(tb)['backlinks']})
        self.update('alice')
        with self.use('bob') as b:
            b.synchronize(publish=False)
            b.edit(tb, b.ref(b.find(tb))['version'], body='v2 概况')
            b.load()
            corrected = b.append(tb, '修正后的结果，仅适用 v2', 'correction', supersedes=original)['id']
            b.load()
            self.assertTrue(b.synchronize()['synchronized'])
        with self.use('alice') as a:
            a.synchronize(publish=False)
            updated = a.append(ta, '综合结果也需重新核查', 'correction', supersedes=a.ref(a.find(summary)))['id']
            a.load()
            self.assertTrue(a.synchronize()['synchronized'])
        with self.use('dave') as d:
            d.synchronize(publish=False)
            links = [x for x in d.backlinks(tb)['backlinks'] if x['record']['id'] == summary]
            self.assertEqual({x['target']['id'] for x in links}, {tb, result})
            self.assertEqual(next(x['target'] for x in links if x['target']['id'] == tb), old_thread)
            self.assertNotEqual(d.ref(d.find(tb))['version'], old_thread['version'])
            packet = d.context(summary)
            cited = next(x for x in packet['citations'] if x['source']['id'] == result)
            self.assertEqual(cited['document']['body'], '原始结果')
            self.assertEqual(cited['corrections'][0]['id'], corrected)
            self.assertIn(updated, {x['record']['id'] for x in packet['backlinks'] if x['relation'] == 'supersedes'})
