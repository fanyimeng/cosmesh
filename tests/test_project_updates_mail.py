"""Project capture and private mail, using accepted Git records and real hooks."""
import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.hub_pages import build
from cosmesh.threads import Workbench
from test_threads import ThreadFixture


class UpdateMailFixture(ThreadFixture):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, COSMESH_PAGE_BACKGROUND='0', COSMESH_PAGE_CACHE=str(self.root/'pages'),
                         COSMESH_HUB_PAGE_CACHE=str(self.root/'hub-pages'))
        env.start(); self.addCleanup(env.stop)
        self.hub.initialize_community(self.root/'library')
        # Emulate upgrading a pre-mail Hub; its accepted history becomes the baseline.
        (self.hub.root/'mail/deliveries.json').unlink()
        (self.hub.root/'mail/baseline.json').unlink()
        for user, name in {'alice':'张三','bob':'李四','carol':'王五'}.items():
            self.hub.spaces({'method':'community_name','name':name,'key':'name-'+user},user)
            with self.use(user) as wb: wb.discover()

    def call(self, user, *args, ok=True):
        wb = self.wbs[user] if isinstance(user,str) else user
        with redirect_stdout(io.StringIO()) as output:
            code = main(['thread',*args,'--root',str(wb.base.root),'--json'])
        result = json.loads(output.getvalue())
        self.assertEqual(code == 0, ok, result)
        return result


class ProjectUpdateTests(UpdateMailFixture):
    def test_private_capture_exact_deduplication_and_checkpoint_privacy(self):
        with self.use('alice') as wb: tid=wb.create('合成分析')['id']
        args=('update',tid,'--body','完成合成数据检查；下一步扩大样本。','--interaction-summary','已完成输入检查。','--ready')
        first=self.call('alice',*args)['data']; second=self.call('alice',*args)['data']
        self.assertEqual(first['disposition'],'created'); self.assertEqual(second['disposition'],'unchanged')
        self.assertEqual(first['record']['id'],second['record']['id'])
        self.assertEqual(second['publication']['state'],'private')
        with self.use('alice') as wb:
            self.assertEqual(len(wb.records(tid)),1)
            self.assertNotIn('project-updates',wb.base.git('ls-files'))
            self.assertEqual(wb.base.git('status','--porcelain'),'')
            self.assertTrue(wb.base.path('.cosmesh/project-updates/'+tid+'.json').is_file())

    def test_shared_capture_auto_sync_redacts_paths_and_does_not_notify(self):
        tid=self.shared('alice','合成分析'); build(self.hub)
        private_path=str(self.root/'private/result.txt')
        data=self.call('alice','update',tid,'--body','已完成。产物位于 '+private_path,'--ready')['data']
        self.assertTrue(data['publication']['accepted'],data)
        self.assertNotIn(private_path,self.hub.repo(self.repos['alice']).git('log','-p'))
        build(self.hub)
        inbox=self.call('bob','inbox','--view','messages','--require-fresh')['data']
        self.assertEqual(inbox['messages'],[])
        with self.use('bob') as wb:
            wb.synchronize(publish=False)
            self.assertIn('已完成',wb.find(data['record']['id'])['doc']['body'])

    def test_sync_blocks_other_drafts_and_retry_reuses_record(self):
        tid=self.shared('alice','目标'); other=self.shared('alice','其他工作')
        with self.use('alice') as wb: draft=wb.append(other,'尚未授权发布的草稿')['id']
        args=('update',tid,'--body','完成阶段一。','--idempotency-key','stage-one')
        first=self.call('alice',*args)['data']; second=self.call('alice',*args)['data']
        self.assertEqual(first['publication']['error']['code'],'UNRELATED_PENDING')
        self.assertEqual(first['record']['id'],second['record']['id'])
        remote=self.hub.repo(self.repos['alice']); docs=remote.documents(remote.snapshot(remote.git('rev-parse','main'))[1])
        self.assertNotIn(draft,docs); self.assertNotIn(first['record']['id'],docs)
        with self.use('alice') as wb:
            self.assertEqual(len([r for r in wb.records(tid) if r['doc']['body']=='完成阶段一。']),1)

    def test_offline_pending_retry_and_record_set_conflict(self):
        tid=self.shared('alice','目标')
        args=('update',tid,'--body','新的验证结果。','--idempotency-key','stage-two')
        first=self.call('alice',*args,'--offline')['data']
        self.assertEqual(first['publication']['error']['code'],'OFFLINE')
        second=self.call('alice',*args)['data']
        self.assertEqual(first['record']['id'],second['record']['id']); self.assertTrue(second['publication']['accepted'])
        with self.use('alice') as wb: wb.append(tid,'另一会话补充了新证据。')
        result=self.call('alice','update',tid,'--body','新结论。','--basis',second['basis'],ok=False)
        self.assertEqual(result['error']['code'],'STATE_CONFLICT')


class MailTests(UpdateMailFixture):
    def prepare_request(self):
        self.tid=self.shared('alice','合成咨询')
        with self.use('alice') as wb:
            self.qid=wb.append(self.tid,'请提供方法说明。','request',recipient='bob')['id']; wb.synchronize()
        build(self.hub)  # Historical bootstrap does not flood everyone's unread box.
        with self.use('bob') as wb:
            wb.synchronize(publish=False)
            self.rid=wb.append(self.tid,'方法说明已补充，正式结果还在验证。','reply',request=wb.ref(wb.find(self.qid)))['id']
            wb.synchronize()
        build(self.hub)

    def test_reply_reaches_requester_without_changing_pending_and_lists_do_not_mark(self):
        self.prepare_request()
        old=self.call('alice','inbox','--require-fresh')['data']; self.assertEqual(old,[])
        args=('inbox','--view','messages','--unread','--require-fresh','--ready')
        first=self.call('alice',*args)['data']; second=self.call('alice',*args)['data']
        self.assertEqual(first['unread_count'],1); self.assertEqual(second['unread_count'],1)
        self.assertEqual(first['messages'][0]['source']['id'],self.rid)
        self.assertEqual(first['messages'][0]['request_state'],'pending')
        with patch.object(Workbench,'load',side_effect=AssertionError('mail hot read must not load Git')), \
             patch('cosmesh.spaces.rpc',side_effect=AssertionError('cached read must not use network')):
            self.call('alice','inbox','--view','messages','--unread','--ready')
        sent=self.call('bob','outbox','--require-fresh')['data']
        self.assertEqual(sent['messages'][0]['source']['id'],self.rid)

    def test_seen_state_cross_device_rebuild_and_recipient_authorization(self):
        self.prepare_request()
        data=self.call('alice','inbox','--view','messages','--unread','--require-fresh')['data']; mid=data['messages'][0]['id']
        self.call('carol','mail','mark',mid,'--seen',ok=False)
        self.call('carol','mail','show',mid,ok=False)
        self.call('alice','mail','mark',mid,'--seen')
        other=self.workbench('alice','second-device','local-other')
        self.assertEqual(self.call(other,'inbox','--view','messages','--unread','--require-fresh')['data']['unread_count'],0)
        build(self.hub)
        self.assertEqual(self.call('alice','inbox','--view','messages','--unread','--require-fresh')['data']['unread_count'],0)
        (self.hub.root/'mail/deliveries.json').unlink()
        build(self.hub)  # Disposable delivery index loss must not lose explicit seen state.
        self.assertEqual(self.call('alice','inbox','--view','messages','--unread','--require-fresh')['data']['unread_count'],0)
        self.call('alice','mail','mark',mid,'--unseen')
        self.assertEqual(self.call(other,'inbox','--view','messages','--unread','--require-fresh')['data']['unread_count'],1)
        (self.hub.root/'mail/deliveries.json').unlink(); build(self.hub)
        self.assertEqual(self.call(other,'inbox','--view','messages','--unread','--require-fresh')['data']['unread_count'],1)

    def test_corrections_pagination_late_arrivals_and_idempotent_index(self):
        self.prepare_request()
        with self.use('bob') as wb:
            correction=wb.append(self.tid,'方法参数更正为合成值。','correction',supersedes=wb.ref(wb.find(self.rid)))['id']
            wb.synchronize()
        build(self.hub)
        first=self.call('alice','inbox','--view','messages','--limit','1','--require-fresh')['data']
        self.assertEqual(first['messages'][0]['source']['id'],correction)
        token=first['next_cursor']; self.assertIsNotNone(token)
        with self.use('bob') as wb:
            late=wb.append(self.tid,'追加验证说明。','reply',request=wb.ref(wb.find(self.qid)))['id']; wb.synchronize()
        build(self.hub); build(self.hub)
        page=self.call('alice','inbox','--view','messages','--limit','1','--before',token,'--require-fresh')['data']
        self.assertEqual(page['messages'][0]['source']['id'],self.rid)
        latest=self.call('alice','inbox','--view','messages','--unread','--require-fresh')['data']
        self.assertEqual(latest['unread_count'],3)
        self.assertEqual(len([e for e in latest['messages'] if e['source']['id']==late]),1)

    def test_first_prepare_reports_unknown_not_empty_and_history_is_not_unread(self):
        first=self.call('alice','inbox','--view','messages','--require-fresh')['data']
        self.assertTrue(first['freshness']['updating']); self.assertNotIn('暂无',first['document'])
        self.prepare_request()
        bob=self.call('bob','inbox','--view','messages','--require-fresh')['data']
        self.assertEqual(bob['unread_count'],0)
        self.assertTrue(bob['messages'][0]['historical'])
