"""Targeted online access and person tracking with real isolated identities/Git."""
import json
import os
from pathlib import Path
from unittest.mock import patch

from tests.test_threads import ThreadFixture
from cosmesh.cli import parser
from cosmesh.errors import Error
from cosmesh.personal import initialize
from cosmesh.personal_protocol import packed
from cosmesh.personal_relay import Relay, initialize as enable
from cosmesh.thread_access import Access
from cosmesh.thread_access_cli import Online
from cosmesh.thread_cli import execute
from cosmesh.thread_personal import ThreadPersonal
from cosmesh.thread_tracking import attach
from cosmesh.threads import Workbench, initialize as init_work


class TrackingTests(ThreadFixture):
    def setUp(self):
        super().setUp();enable(self.hub)
        env=patch.dict(os.environ,{'COSMESH_DATA_DIR':str(self.root/'device-data'),'COSMESH_PAGE_BACKGROUND':'0'})
        env.start();self.addCleanup(env.stop)
        self.hub.initialize_community(self.root/'library')
        for user in self.wbs:
            self.hub.spaces({'method':'community_name','name':user.upper(),'key':'name-'+user},user)

    def command(self, user, *args):
        wb=self.wbs[user] if isinstance(user,str) else user
        return execute(parser('cosmesh').parse_args(['thread',*args,'--root',str(wb.base.root)]))[0]

    def work(self, title, body=None):
        with self.use('alice') as wb:
            tid=wb.create(title,body or title+' full content')['id'];wb.load()
            return tid

    def grant(self, tid, mode='restricted', readers=('bob',), files=False):
        args=['access',tid,'--visibility',mode]
        for person in readers:args.extend(['--to',person])
        if files:args.append('--files')
        return self.command('alice',*args)

    def test_two_direct_threads_suggest_reuse_and_follow_changes_without_copy(self):
        ta,tb,tc=(self.work(t) for t in ('TA','TB','all-user work'))
        self.grant(ta);self.grant(tb);self.grant(tc,'public',())
        bob=self.wbs['bob'];before=bob.base.head
        result=self.command('bob','track')
        person=result['people'][0]
        self.assertEqual({r['thread'] for r in person['threads']},{ta,tb})
        self.assertEqual(person['suggestion'],'create');self.assertEqual(bob.base.head,before)
        self.command('bob','track','alice','--dismiss')
        self.assertEqual(self.command('bob','track')['people'][0]['suggestion'],'none')
        self.assertEqual(bob.base.head,before)
        self.assertEqual(self.command('bob','browse',ta)['body'],'TA full content')
        bob.load();self.assertNotIn(ta,bob.catalog);self.assertEqual(bob.base.head,before)
        with self.use('bob') as wb: existing=wb.create('ALICE','my existing notes')['id']
        self.assertEqual(self.command('bob','track')['people'][0]['reuse_candidates'],[existing])
        tracked=self.command('bob','track','alice','--use',existing)['thread']
        self.assertEqual(tracked,existing)
        with self.use('bob') as wb, self.assertRaises(Error):
            attach(wb,Online(wb).info,{'owner_id':'different-person','account':'carol','name':'CAROL'},existing)
        self.assertEqual(self.command('bob','track','alice','--create')['thread'],tracked)
        with self.use('bob') as wb:wb.append(tracked,'keep my assessment')
        note_head=bob.base.head
        self.grant(ta,'public',());self.grant(tb,'private',())
        current=self.command('bob','track','--show',tracked)
        self.assertEqual(current['sources'],[]);self.assertEqual(bob.base.head,note_head)
        shown=self.command('bob','show',tracked)
        self.assertIn('keep my assessment',packed(shown));self.assertEqual(shown['tracking']['sources'],[])
        self.assertEqual(self.command('bob','show',tracked,'--offline')['tracking']['state'],'offline')
        td=self.work('new direct work');self.grant(td,readers=('bob','carol'))
        self.assertEqual([r['thread'] for r in self.command('bob','track','--show',tracked)['sources']],[td])
        self.hub.spaces({'method':'community_name','name':'Alice Renamed','key':'renamed','expect':self.hub.community().check('alice')['version']},'alice')
        self.assertEqual(self.command('bob','track','alice','--create')['thread'],tracked)
        self.assertEqual(self.command('bob','track')['people'][0]['name'],'Alice Renamed')

    def test_owner_acl_online_history_files_revocation_and_old_paths(self):
        tid=self.work('private details','first version');self.grant(tid)
        initial=self.command('bob','browse',tid)
        with self.use('alice') as wb:
            wb.edit(tid,wb.ref(wb.find(tid))['version'],body='new version');wb.load()
            wb.append(tid,'new record');wb.load()
        self.command('alice','sync',tid)
        self.assertEqual(self.command('bob','browse',tid)['body'],'new version')
        history=self.command('bob','browse',tid,'--records','--record',tid)['records']
        self.assertEqual(len(history),2)
        old=self.command('bob','browse',tid,'--record',tid,'--version',initial['version'])
        self.assertEqual(old['body'],'first version')
        self.assertFalse(self.command('carol','browse')['threads'])
        for actor in ('carol',):
            for args in (('browse',tid),('browse',tid,'--files'),('browse',tid,'--records'),('access',tid)):
                with self.assertRaises(Error) as error:self.command(actor,*args)
                self.assertEqual(error.exception.code,'NOT_FOUND')
        status=self.command('alice','access',tid)
        with self.assertRaises(Error):self.hub.spaces({'method':'thread_access_set','thread':tid,'sha':status['content_revision'],
            'visibility':'public','readers':[],'expect':status['acl_revision']},'bob')
        self.assertNotIn('private details',packed(self.hub.spaces({'method':'list_threads'},'carol')))
        self.assertNotIn('private details',packed(self.hub.spaces({'method':'community_info'},'carol')))
        with self.assertRaises(Error):Relay(self.hub,'bob').dispatch({'method':'personal_get','sha':status['content_revision'],'offset':0})
        with patch.object(ThreadPersonal,'synchronize',side_effect=AssertionError('revoke must not upload')):
            self.grant(tid,'private',())
        with self.assertRaises(Error):self.command('bob','browse',tid,'--snapshot',initial['content_revision'])
        self.assertEqual(self.command('alice','browse',tid)['body'],'new version')

    def test_explicit_download_preserves_destination_and_stops_after_revocation(self):
        tid=self.work('Files')
        with self.use('alice') as wb:
            initialize(wb);p=ThreadPersonal(wb)
            f=self.root/'input.txt';f.write_bytes(b'readable data\n'*30000)
            aid=p.add(tid,f,relative_path='data/input.txt')['id']
        self.grant(tid,files=True)
        files=self.command('bob','browse',tid,'--files')['files'];self.assertEqual(files[0]['id'],aid)
        self.assertNotIn('path',files[0]);self.assertNotIn(str(self.root),packed(files))
        out=self.root/'download.txt'
        self.command('bob','browse',tid,'--asset',aid,'--output',str(out))
        self.assertEqual(out.read_bytes(),f.read_bytes())
        with self.assertRaises(Error):self.command('bob','browse',tid,'--asset',aid,'--output',str(out))
        real=Online.call;count=0
        def revoke(client,method,**payload):
            nonlocal count
            result=real(client,method,**payload)
            if method=='thread_access_file':
                count+=1
                if count==1:self.grant(tid,'private',())
            return result
        other=self.root/'interrupted.txt'
        with patch.object(Online,'call',revoke),self.assertRaises(Error):
            self.command('bob','browse',tid,'--asset',aid,'--output',str(other))
        self.assertFalse(other.exists());self.assertEqual(list(self.root.glob('.cosmesh-download-*')),[])

    def test_quota_and_acl_conflicts_keep_accepted_data(self):
        tid=self.work('Quota');first=self.grant(tid)
        with self.assertRaises(Error) as error:
            self.command('alice','access',tid,'--visibility','public','--expect-acl','0')
        self.assertEqual(error.exception.code,'STATE_CONFLICT')
        with self.use('alice') as wb:
            wb.append(tid,'new unaccepted data');wb.load()
        with patch.dict('cosmesh.thread_access.LIMITS',{'current':1}),self.assertRaises(Error) as error:self.grant(tid)
        self.assertEqual(error.exception.code,'STORAGE_QUOTA')
        self.assertEqual(self.command('alice','access',tid)['content_revision'],first['content_revision'])
        with self.use('alice') as wb:
            p=ThreadPersonal(wb);f=self.root/'large.txt';f.write_text('a'*1024);p.add(tid,f)
        with patch.dict('cosmesh.thread_access.LIMITS',{'file':100}),self.assertRaises(Error) as error:self.grant(tid,files=True)
        self.assertEqual(error.exception.code,'FILE_QUOTA')
        self.assertTrue(f.exists());self.assertEqual(self.command('bob','browse',tid)['body'],'Quota full content')

    def test_lost_acceptance_retry_tracking_cross_device_and_no_legacy_git_copy(self):
        tid=self.work('Retry')
        real=Online.call
        def lost(client,method,**payload):
            result=real(client,method,**payload)
            if method=='thread_access_set':raise Error('SYNC_NETWORK','lost reply')
            return result
        with patch.object(Online,'call',lost),self.assertRaises(Error):self.grant(tid)
        retry=self.grant(tid);self.assertTrue(retry['replayed'])
        tracker=self.command('bob','track','alice','--create')['thread']
        with self.use('bob') as wb:
            initialize(wb);p=ThreadPersonal(wb);p.state.update(connection=self.connection('bob'),actor='bob');p.save();p.synchronize(tracker)
        root=self.root/'bob-second';init_work(root,'bob-local');second=Workbench(root)
        with self.use(second):
            initialize(second);p=ThreadPersonal(second);p.state.update(connection=self.connection('bob'),actor='bob');p.save();p.restore(tracker)
        self.assertEqual(self.command(second,'track','alice','--create')['thread'],tracker)
        # The server hook, not just the CLI, blocks publishing a managed ID into public Git.
        with self.use('alice') as wb:
            with self.assertRaises(Error):wb.share(tid)
            wb.client.pop('thread_access',None);wb.save_client()  # emulate an old client
            wb.share(tid);wb.load();result=wb.synchronize()
            self.assertFalse(result['synchronized'])
        raw=self.hub.repo(self.repos['alice']).git('show','main:documents/'+tid+'.md',check=False)
        self.assertNotEqual(raw.returncode,0)

    def test_legacy_public_requires_migration_and_old_hub_has_no_fallback(self):
        tid=self.shared('alice','already public')
        with self.assertRaises(Error) as error:self.grant(tid)
        self.assertEqual(error.exception.code,'LEGACY_PUBLIC_MIGRATION_REQUIRED')
        with self.use('bob') as wb:
            before=wb.base.head
            with patch('cosmesh.thread_access_cli.rpc',side_effect=Error('INPUT_INVALID','old hub')),self.assertRaises(Error) as error:
                self.command('bob','track')
            self.assertEqual(error.exception.code,'FEATURE_UNAVAILABLE');self.assertEqual(wb.base.head,before)
