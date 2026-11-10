import io
import json
from contextlib import redirect_stdout
from cosmesh.cli import main
from cosmesh.hub_pages import build
from test_project_updates_mail import UpdateMailFixture


class UnifiedSendTests(UpdateMailFixture):
    def test_private_warning_no_message_then_public_delivery_and_reply(self):
        with self.use('alice') as wb:
            tid = wb.create('公开科研', '可公开的计划')['id']; before = wb.base.head
        args = ('request', tid, '--to', 'bob', '--body', '请审阅', '--title','请审阅方法口径', '--interaction-summary','本次提交了需要审阅的方法说明，并请对方确认。', '--idempotency-key', 'ask-1')
        value = self.call('alice', *args, ok=False)
        self.assertEqual(value['error']['code'], 'PRIVATE_THREAD_NOT_SENT')
        with redirect_stdout(io.StringIO()) as output:
            main(['thread', *args, '--root',str(self.wbs['alice'].base.root)])
        self.assertTrue(output.getvalue().startswith('⚠️ 尚未发送\n'))
        self.assertNotIn('分支', output.getvalue())
        with self.use('alice') as wb: self.assertEqual(wb.base.head, before)
        self.call('alice','share',tid,'--sync')
        sent = self.call('alice',*args,'--ready')['data']
        self.assertTrue(sent['sent']); self.assertTrue(sent['publication']['accepted'])
        self.assertEqual(sent['result']['title'],'请审阅方法口径')
        again = self.call('alice',*args)['data']; self.assertEqual(sent['result']['id'],again['result']['id'])
        build(self.hub)
        inbox = self.call('bob','inbox','--view','messages','--require-fresh')['data']
        self.assertEqual([m['source']['id'] for m in inbox['messages']], [sent['result']['id']])
        self.update('bob')
        reply = self.call('bob','reply',sent['result']['id'],'--body','已收到')['data']
        self.assertTrue(reply['sent']); build(self.hub)
        inbox = self.call('alice','inbox','--view','messages','--require-fresh')['data']
        self.assertEqual(inbox['messages'][0]['source']['id'], reply['result']['id'])
        work = self.shared('bob', '审阅结果')
        result = self.call('bob','result',work,'--body','完成核对')['data']
        delivery = self.call('bob','deliver',sent['result']['id'],'--result',result['id'],'--body','交付审阅')['data']
        self.assertTrue(delivery['sent'], delivery)
        with self.use('bob') as wb: self.assertTrue(wb.accepted(wb.find(result['id'])))

    def test_offline_retry_private_reference_and_unrelated_drafts(self):
        tid = self.shared('alice','讨论')
        args = ('request',tid,'--to','bob','--body','检查方法')
        pending = self.call('alice',*args,'--offline')['data']
        self.assertFalse(pending['sent']); self.assertEqual(pending['delivery_state'],'pending')
        sent = self.call('alice',*args)['data']; self.assertTrue(sent['sent'])
        self.assertEqual(sent['result']['id'],pending['result']['id'])
        with self.use('alice') as wb:
            private = wb.create('私人资料')['id']
            draft = wb.append(tid,'其他草稿')['id']
        blocked = self.call('alice',*args,'--ref',private,ok=False)
        self.assertEqual(blocked['error']['code'],'PRIVATE_THREAD_NOT_SENT')
        blocked = self.call('alice','request',tid,'--to','bob','--body','另一个请求')['data']
        self.assertFalse(blocked['sent']); self.assertEqual(blocked['publication']['error']['code'],'UNRELATED_PENDING')
        build(self.hub)
        inbox = self.call('bob','inbox','--view','messages','--require-fresh')['data']
        self.assertEqual(len(inbox['messages']),1)

    def test_public_branch_excludes_private_history_and_retries_same_id(self):
        with self.use('alice') as wb:
            tid = wb.create('SECRET TITLE','SECRET BODY')['id']; wb.load()
            wb.append(tid,'SECRET HISTORY')
        args = ('public-branch',tid,'--title','公开进展','--body','批准的公开内容','--sync','--idempotency-key','branch-1')
        self.call('alice',*args); self.call('alice',*args)
        with self.use('alice') as wb:
            self.assertEqual(wb.find(tid)['repo'],'local')
            self.assertEqual(len(wb.client['public_branches']),1)
            child = wb.client['public_branches'][0]['thread']
        self.update('bob')
        with self.use('bob') as wb:
            self.assertEqual(wb.find(child)['doc']['meta']['title'],'公开进展')
            history=wb.stores[self.repos['alice']].git('log','-p')
            self.assertNotIn('SECRET',history); self.assertNotIn(tid,history)
        sent = self.call('alice','request',child,'--to','bob','--body','请看公开进展')['data']
        self.assertTrue(sent['sent'])


class UnifiedPrivateTests(__import__('unittest').TestCase):
    def test_private_offline_aliases_and_old_document_lifecycle(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from cosmesh.threads import initialize, Workbench
        from cosmesh.service import initialize as legacy_init
        from cosmesh.store import Store
        def call(root,*args,product='cosmesh'):
            with redirect_stdout(io.StringIO()) as output:
                code=main([*args,'--root',str(root),'--json'],product=product)
            value=json.loads(output.getvalue()); self.assertEqual(code,0,value)
            return value['data']
        with tempfile.TemporaryDirectory() as folder, patch('cosmesh.spaces.rpc',side_effect=AssertionError('private work must stay offline')):
            root=Path(folder).resolve(); work=root/'work'; initialize(work,'alice')
            tid=call(work,'thread','create','--title','私人工作')['id']
            note=call(work,'thread','note',tid,'--body','完整句子。'*60)
            self.assertEqual(note['title'],'记录')
            corrected=call(work,'thread','correct',note['id'],'--body','更正私人记录')
            self.assertEqual(corrected['delivery_state'],'draft')
            self.assertFalse(corrected['sent'])
            self.assertEqual(len(call(work,'thread','list','--scope','private')),1)
            self.assertEqual(call(work,'thread','list','--scope','public'),[])
            found=call(work,'search','query','私人工作','--scope','private','--offline')
            self.assertTrue(found['hits'])
            legacy=root/'legacy'; legacy_init(Store(legacy),'cosbase','alice','Asia/Shanghai')
            task=call(legacy,'task','create','--title','保留旧任务')['result']
            self.assertEqual(call(legacy,'doctor')['store'],'local_unverified')
            converted=call(legacy,'doc','convert',task['id'],'--to','project','--expect',task['version'])['result']['target']
            current=call(legacy,'doc','show',converted['id'])
            trashed=call(legacy,'doc','trash',current['id'],'--expect',current['version'])['result']['document']
            restored=call(legacy,'doc','restore',trashed['id'],'--expect',trashed['version'])['result']['document']
            self.assertEqual(call(legacy,'doc','show',restored['id'],product='cosbase')['id'],restored['id'])
