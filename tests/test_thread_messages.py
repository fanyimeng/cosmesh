"""Necessary owner/recipient isolation, delivery retry and personal inbox closures."""
from unittest.mock import patch
from tests.test_thread_tracking import TrackingTests
from cosmesh.errors import Error
from cosmesh.thread_access_cli import Online
from cosmesh.thread_migration import migrate
from cosmesh.thread_tracking import bindings
from cosmesh.thread_personal import ThreadPersonal
from cosmesh.personal_protocol import packed
from cosmesh.threads import Workbench,initialize


class MessageTests(TrackingTests):
    # Reuse only the isolated fixture, not its unrelated test cases.
    def setUp(self):
        super().setUp();migrate(self.hub,True,True)

    def mailbox(self,user,**kw):
        return self.hub.spaces(dict(method='thread_message_list',folder='inbox',limit=10,before=None,unread=False,peer=None,thread=None,kind=None,**kw),user)

    def test_send_reply_isolation_and_read_receipts(self):
        tid=self.work('My work');before=self.wbs['alice'].base.head
        with self.assertRaises(Error):self.command('alice','send',tid,'--to','bob','--body','hello')
        self.assertEqual(before,self.wbs['alice'].base.head)
        self.grant(tid)
        with self.use('alice') as wb:wb.append(tid,'private unrelated draft')
        first=self.command('alice','send',tid,'--to','bob','--body','hello')
        self.assertTrue(first['sent'])
        inbox=self.command('bob','inbox');m=inbox['messages'][0]
        self.assertTrue(m['unread']);self.assertEqual(m['id'],first['id']);self.assertEqual(m['excerpt'],'hello')
        self.assertNotIn('private unrelated draft',packed(self.command('bob','browse',tid,'--records')))
        self.assertEqual(self.command('bob','mail','show',m['id'])['body'],'hello')
        self.assertTrue(self.command('bob','inbox')['messages'][0]['unread'])
        self.command('bob','mail','mark',m['id'],'--seen')
        self.assertFalse(self.command('bob','inbox')['messages'][0]['unread'])
        self.assertTrue(all(self.command('alice','outbox')['messages'][0]['read_by'].values()))
        self.assertEqual(self.command('carol','inbox')['messages'],[])
        with self.assertRaises(Error):self.command('carol','mail','show',m['id'])
        with self.assertRaises(Error):self.command('alice','mail','mark',m['id'],'--seen')
        with self.use('bob') as wb:own=wb.create('My reply')['id']
        self.command('bob','access',own,'--visibility','restricted','--to','alice')
        reply=self.command('bob','reply',m['id'],'--thread',own,'--body','thanks')
        row=self.command('alice','inbox')['messages'][0]
        self.assertEqual(row['id'],reply['id']);self.assertEqual(row['thread'],own);self.assertEqual(row['reply_to'],m['id'])
        self.grant(tid,'private',())
        self.assertEqual(self.command('bob','inbox')['messages'],[])
        with self.assertRaises(Error):self.command('bob','mail','show',m['id'])
        with self.assertRaises(Error):self.command('bob','reply',m['id'],'--thread',own,'--body','again')

    def test_retry_offline_draft_and_frozen_page(self):
        tid=self.work('Retry');self.grant(tid)
        args=('send',tid,'--to','bob','--body','exactly once')
        draft=self.command('alice',*args,'--local-only');self.assertFalse(draft['sent'])
        self.assertEqual(self.command('bob','inbox')['messages'],[])
        real=Online.call
        def lost(client,method,**payload):
            result=real(client,method,**payload)
            if method=='thread_message_send':raise Error('SYNC_NETWORK','lost acceptance')
            return result
        with patch.object(Online,'call',lost),self.assertRaises(Error):self.command('alice',*args)
        # Advancing a private head cannot strand an already accepted send/retry.
        with self.use('alice') as wb:wb.append(tid,'later private work');wb.load();ThreadPersonal(wb).synchronize(tid)
        done=self.command('alice',*args);self.assertTrue(done['replayed']);self.assertEqual(done['record'],draft['record'])
        self.assertEqual(len(self.command('bob','inbox')['messages']),1)
        original=Online.__init__
        def another_hub(client,wb):
            original(client,wb);client.info['hub_id']='other-hub'
        with patch.object(Online,'__init__',another_hub),self.assertRaises(Error) as denied:
            self.command('alice',*args)
        self.assertEqual(denied.exception.code,'AUTH_DENIED')
        self.command('alice','send',tid,'--to','bob','--body','second')
        page=self.command('bob','inbox','--limit','1');self.assertTrue(page['next_cursor'])
        newest=self.command('alice','send',tid,'--to','bob','--body','third')['id']
        rest=self.command('bob','inbox','--limit','1','--before',page['next_cursor'])
        self.assertEqual([r['id'] for r in rest['messages']],[done['id']]);self.assertNotIn(newest,packed(rest))
        with self.assertRaises(Error):self.command('bob','inbox','--offline')

    def test_person_thread_automatic_reuse_collision_and_second_device(self):
        tid=self.work('Contact');self.grant(tid)
        self.command('alice','send',tid,'--to','bob','--body','first contact')
        first=self.command('bob','inbox');tracked=first['messages'][0]['tracking_thread']
        self.assertTrue(first['tracking'][0]['created'])
        self.assertEqual(self.command('bob','inbox')['messages'][0]['tracking_thread'],tracked)
        with self.use('bob') as wb:
            self.assertEqual(wb.find(tracked)['doc']['meta']['title'],'ALICE')
            self.assertEqual(len(bindings(wb,Online(wb).info['hub_id'])),1)
        root=self.root/'bob-second';initialize(root,'bob-other');second=Workbench(root)
        second.client.update(connection=self.connection('bob'),actor='bob');second.save_client()
        again=self.command(second,'inbox');self.assertEqual(again['messages'][0]['tracking_thread'],tracked)
        second.load();self.assertNotIn(tid,second.catalog)
        with self.use('carol') as wb:existing=wb.create('ALICE','Unrelated personal material')['id']
        self.grant(tid,readers=('bob','carol'))
        self.command('alice','send',tid,'--to','carol','--body','another contact')
        collision=self.command('carol','inbox')
        self.assertEqual(collision['tracking'][0]['state'],'choose_existing')
        self.assertEqual(collision['tracking'][0]['candidates'],[existing])
        self.assertNotIn('tracking_thread',collision['messages'][0])
        self.command('carol','track','alice','--use',existing)
        self.assertEqual(self.command('carol','inbox')['messages'][0]['tracking_thread'],existing)
        self.assertEqual(len(self.command('bob','chat',tracked)['messages']),1)

    def test_search_incremental_versions_and_revoked_index(self):
        tid=self.work('可检索工作','初始内容 银盘结构');self.grant(tid)
        first=self.command('bob','access',tid)['content_revision']
        one=self.command('bob','search','银盘结构','--thread',tid)
        self.assertEqual(len(one['hits']),1);self.assertEqual(one['indexed_changes'],1)
        self.assertEqual(self.command('bob','search','银盘结构','--thread',tid)['indexed_changes'],0)
        with self.use('alice') as wb:
            note=wb.append(tid,'新增分子云目录')['id'];wb.load()
        self.command('alice','sync',tid)
        second=self.command('bob','access',tid)['content_revision']
        delta=self.command('bob','browse',tid,'--changes',first,'--snapshot',second)
        self.assertEqual([r['record'] for r in delta['changes']],[note])
        self.assertEqual(self.command('bob','search','分子云','--thread',tid)['indexed_changes'],1)
        self.assertTrue(self.command('bob','browse',tid,'--changes',second)['unchanged'])
        sent=self.command('alice','send',tid,'--to','bob','--body','消息里的特殊检索词')['id']
        hit=self.command('bob','search','特殊检索词','--messages')['hits'][0]
        self.assertEqual(hit['message'],sent)
        self.assertEqual(self.command('bob','mail','show',hit['message'])['body'],'消息里的特殊检索词')
        self.assertEqual(self.command('bob','search','特殊检索词','--messages')['indexed_changes'],0)
        self.assertEqual(self.command('carol','search','特殊检索词','--messages')['hits'],[])
        self.assertEqual(self.command('carol','search','银盘结构')['hits'],[])
        before=len(self.command('bob','inbox')['messages'])
        with patch.dict('cosmesh.thread_access.LIMITS',{'retained':1}),self.assertRaises(Error):
            self.command('alice','send',tid,'--to','bob','--body','quota must include delivery')
        self.assertEqual(len(self.command('bob','inbox')['messages']),before)
        self.grant(tid,'private',())
        self.assertEqual(self.command('bob','search','特殊检索词','--messages')['hits'],[])
        self.assertEqual(self.command('bob','search','银盘结构')['hits'],[])
        with self.assertRaises(Error):self.command('bob','browse',tid,'--changes',first,'--snapshot',second)

    def test_quota_defaults_and_legacy_message_read_state(self):
        from cosmesh.transfer_policy import PERSONAL,PUBLIC,GB,effective,PREVIOUS,WIDE_046
        self.assertEqual((PERSONAL['auto_file'],PERSONAL['batch_bytes'],PERSONAL['server_bytes']),(5*GB,10*GB,30*GB))
        self.assertEqual((PUBLIC['file'],PUBLIC['batch'],PUBLIC['current'],PUBLIC['retained']),(2*GB,5*GB,15*GB,15*GB))
        self.assertEqual(effective(PREVIOUS),PERSONAL);self.assertEqual(effective(WIDE_046),PERSONAL)
        self.assertEqual(effective(WIDE_046,['server_bytes'])['server_bytes'],WIDE_046['server_bytes'])
        # A fresh legacy fixture creates ordinary old records before offline migration.
        legacy=TrackingTests()
        try:
            legacy.setUp();tid=legacy.shared('alice','legacy')
            with legacy.use('alice') as wb:
                rid=wb.append(tid,'old message',record_type='request',recipient='bob')['id'];wb.load()
                wb.synchronize();wb.load();source=wb.ref(wb.find(rid))
            from cosmesh.thread_mail import digest
            mid='M-'+digest(source)
            from cosmesh.personal_protocol import private_dir,save_json
            mail=private_dir(legacy.hub.root/'mail')
            save_json(mail/('seen-'+digest('bob')+'.json'),{'states':{mid:True}})
            migrate(legacy.hub,True,True)
            inbox=legacy.command('bob','inbox')
            self.assertEqual(inbox['messages'][0]['id'],mid)
            self.assertTrue(inbox['messages'][0]['historical']);self.assertFalse(inbox['messages'][0]['unread'])
            self.assertEqual(inbox['tracking'],[])
            self.assertEqual(legacy.command('bob','mail','show',mid)['body'],'old message')
            legacy.command('alice','fetch',tid)
            # A 0.4.6 migration may lack the additive direct_current accounting field.
            path=legacy.hub.root/'thread-access-v1/catalog.json'
            from cosmesh.personal_protocol import read_json
            catalog=read_json(path);catalog['threads'][tid].pop('direct_current',None);save_json(path,catalog)
            sent=legacy.command('alice','send',tid,'--to','bob','--body','first new message after migration')
            self.assertTrue(sent['sent'])
            self.assertEqual(legacy.command('bob','mail','show',sent['id'])['body'],'first new message after migration')
        finally:legacy.doCleanups()

# Inherited scenarios use the pre-migration protocol; this suite runs the new closures only.
for _name in tuple(dir(TrackingTests)):
    if _name.startswith('test_'):setattr(MessageTests,_name,None)
