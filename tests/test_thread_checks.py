"""One status read, reply-vs-delivery, metadata-only materials and warm cache."""
import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.knowledge import Knowledge
from cosmesh.threads import Workbench
from test_threads import ThreadFixture


class QuickCheckTests(ThreadFixture):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, COSMESH_PAGE_CACHE=str(self.root/'pages'), COSMESH_PAGE_BACKGROUND='0')
        env.start(); self.addCleanup(env.stop)
        self.hub.initialize_community(self.root/'library')
        for user, name in {'alice':'张三','bob':'李四','carol':'王五'}.items():
            self.hub.spaces({'method':'community_name','name':name,'key':'name-'+user},user)
            with self.use(user) as w: w.discover()
        self.tid = self.shared('bob','云研究','研究分子云的形成机制。比较观测与模型，检验动力学解释。')
        with self.use('alice') as w:
            w.synchronize(publish=False)
            self.qid = w.append(self.tid,'请补充说明和报告，并提供固定引用。','request',recipient='bob')['id']
            w.synchronize()

    def call(self, user, *args, ok=True):
        with redirect_stdout(io.StringIO()) as output:
            code = main(['thread', *args, '--root', str(self.wbs[user].base.root), '--json'])
        result = json.loads(output.getvalue())
        self.assertEqual(code == 0, ok, result)
        return result

    def respond(self):
        report = self.root/'report.html'; report.write_text('<h1>合成报告</h1><p>输入为合成数据。</p>')
        self.call('bob','share',self.tid,'--attach',str(report),'--sync')
        self.reply = self.call('bob','reply',self.qid,'--body','报告已入库。代码仓库和 commit 尚待补充。','--sync')['data']['result']
        with self.use('alice') as w: w.synchronize(publish=False)

    def test_request_reply_materials_intro_and_warm_read_without_extra_work(self):
        self.respond()
        first = self.call('alice','check',self.qid)['data']
        self.assertIn('研究分子云的形成机制',first['overall'])
        self.assertNotIn('研究分子云的形成机制',first['document'])
        self.assertIn('研究分子云的形成机制',self.call('alice','check',self.qid,'--details')['data']['document'])
        self.assertIn('代码仓库和 commit 尚待补充',first['document'])
        self.assertEqual(first['requests'][0]['state'],'pending')
        self.assertEqual(first['requests'][0]['response_count'],1)
        self.assertEqual(first['materials']['registered_count'],1)
        self.assertEqual(first['materials']['verification'],'metadata_only')
        response = first['requests'][0]['responses'][0]
        self.assertEqual(first['source_index'][response['source']]['id'],self.reply['id'])
        self.assertNotIn('text',response)  # The original excerpt is already in document.
        w = self.wbs['alice']; before = w.base.head
        with patch.object(Workbench,'load',side_effect=AssertionError('warm reads do not reload')), \
             patch.object(Workbench,'context',side_effect=AssertionError('no full context')), \
             patch.object(Knowledge,'download',side_effect=AssertionError('no file download')), \
             patch('cosmesh.spaces.rpc',side_effect=AssertionError('no network')):
            second = self.call('alice','check',self.qid)['data']
        self.assertEqual(first,second)
        self.assertEqual(w.base.head,before)
        self.assertNotIn('path',first['materials']['files'][0])

    def test_fresh_check_pulls_once_and_never_opens_originals(self):
        self.respond()
        synchronize = Workbench.synchronize; calls = []
        def pull(wb, *args, **kwargs):
            calls.append(kwargs)
            return synchronize(wb,*args,**kwargs)
        with patch.object(Workbench,'synchronize',pull), \
             patch.object(Knowledge,'download',side_effect=AssertionError('unchanged metadata requires no original')):
            data = self.call('alice','check',self.qid,'--require-fresh')['data']
        self.assertEqual(calls,[{'publish':False}])
        self.assertIsNotNone(data['freshness']['remote_checked_at'])
        self.assertEqual(data['requests'][0]['response_count'],1)

    def test_shared_check_excludes_private_backlinks_bindings_and_error_paths(self):
        self.respond()
        with self.use('alice') as w:
            directory = self.root/'secret-project'; directory.mkdir()
            w.bind(self.tid,'codex',str(directory),'secret-session')
            private = w.create('私人事项','private-body')['id']; w.load()
            w.append(private,'private-link',refs=[w.ref(w.find(self.tid))])
        result = self.call('alice','check',self.qid,'--scope','mesh')
        text = json.dumps(result)
        for secret in ('secret-project','secret-session','private-body','private-link',str(self.root),'local_execution'):
            self.assertNotIn(secret,text)
        error = self.call('alice','check',self.qid,'--scope','base',ok=False)
        self.assertEqual(error['error']['code'],'NOT_FOUND')
        self.assertNotIn(str(self.root),json.dumps(error))

    def test_inbox_counts_plain_reply_but_keeps_formal_pending_and_limit(self):
        self.respond()
        data = self.call('alice','inbox','--sent','--ready','--limit','1')['data']
        self.assertEqual(data['total'],1)
        self.assertTrue(data['requests'][0]['has_response'])
        self.assertEqual(data['requests'][0]['state'],'pending')
        old = self.call('alice','inbox','--sent')['data']
        self.assertIsInstance(old,list)
        self.assertEqual(old[0]['request']['id'],self.qid)

    def test_no_reply_and_unregistered_link_are_explicit(self):
        fake = 'cosmesh://library/K-12345678-1234-4234-8234-123456789012@'+'a'*64+'/missing.pdf'
        self.call('bob','note',self.tid,'--body','待补资料 '+fake,'--sync')
        with self.use('alice') as w: w.synchronize(publish=False)
        data = self.call('alice','check',self.tid)['data']
        self.assertEqual(data['requests'][0]['response_count'],0)
        self.assertEqual(data['materials']['unavailable_count'],1)
        self.assertFalse(data['materials']['files'][0]['registered'])
        self.assertIn('尚无回应',data['document'])
        # A literal fixed link without a typed --ref must also invalidate when
        # the library metadata arrives; no Thread source commit has changed.
        snapshot = Knowledge(self.wbs['alice']).path
        stat = snapshot.stat(); os.utime(snapshot,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
        with patch.object(Knowledge,'doc',return_value={'meta':{'files':[{'name':'missing.pdf','size':8}]}}), \
             patch.object(Knowledge,'download',side_effect=AssertionError('metadata only')):
            updated = self.call('alice','check',self.tid)['data']
        self.assertEqual(updated['materials']['registered_count'],1)
        self.assertEqual(updated['materials']['unavailable_count'],0)

    def test_compact_write_keeps_acceptance_failures_and_does_not_repeat_body(self):
        body = '新的进展与依据。'*200
        result = self.call('bob','note',self.tid,'--body',body,'--sync','--ready')['data']
        self.assertEqual(result['format'],'thread.write.ready.v1')
        self.assertTrue(result['publication']['synchronized'])
        self.assertEqual(result['publication']['errors'],[])
        self.assertIn('id',result['result'])
        self.assertIn('version',result['result'])
        self.assertNotIn('body',result['result'])
        self.assertTrue(result['result']['excerpt']['truncated'])
        with self.use('bob') as w:
            self.assertEqual(w.find(result['result']['id'])['doc']['body'],body)
        from cosmesh.thread_checks import ready_write
        error = {'repo':'test','error':{'code':'SYNC_NETWORK','message':'offline'}}
        failed = ready_write({'result':{'id':'test','saved_locally':True},
                              'publication':{'synchronized':False,'errors':[error]}},'note')
        self.assertFalse(failed['publication']['synchronized'])
        self.assertEqual(failed['publication']['errors'],[error])
