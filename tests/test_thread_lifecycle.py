"""Private recycling and public provenance through real source commits and hooks."""
import tempfile
import unittest
from pathlib import Path

from test_threads import ThreadFixture
from cosmesh.errors import Error
from cosmesh.threads import Workbench, initialize
from cosmesh.thread_lifecycle import split, merge, placement, continuation, locate
from cosmesh.knowledge import Knowledge


class PrivateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);initialize(self.root/'work','alice');self.wb=Workbench(self.root/'work')

    def test_recycle_restore_preserves_placement_work_result_and_bindings(self):
        w=self.wb
        with w.base.locked():
            w.load();a=w.create('私人工作','保留背景');w.load()
            w.bind(a['id'],'test-agent',str(self.root),session_id='private-session')
            placement(w,a['id'],'archive');w.load()
            removed=placement(w,a['id'],'delete',key='trash');w.load()
            self.assertEqual(w.present(w.find(a['id']))['lifecycle'],'trash')
            with self.assertRaises(Error):w.append(a['id'],'不应写入回收站')
            self.assertTrue(placement(w,a['id'],'delete',key='trash')['replayed'])
            placement(w,a['id'],'restore');w.load()
            self.assertEqual(w.present(w.find(a['id']))['lifecycle'],'archived')
            placement(w,a['id'],'restore');w.load()
            w.append(a['id'],'完成','state',state='done');w.load()
            placement(w,a['id'],'delete');w.load();placement(w,a['id'],'restore');w.load()
            shown=w.present(w.find(a['id']));self.assertEqual(shown['work_state'],'done');self.assertEqual(shown['lifecycle'],'archived')
            self.assertEqual(w.context(a['id'])['local_execution'][0]['session_id'],'private-session')

    def test_new_session_locates_existing_work_and_follows_merge(self):
        w=self.wb
        with w.base.locked():
            w.load();a=w.create('同一工作')['id'];w.load();b=w.create('另一工作')['id'];w.load()
            for tid in (a,b):w.bind(tid,'test-agent',str(self.root),session_id='earlier-session')
            head=w.base.head
            self.assertEqual(len(locate(w,str(self.root),session_id='new-session')['candidates']),2)
            self.assertEqual(w.base.head,head)
            merge(w,[b],a);w.load()
            found=locate(w,str(self.root),session_id='new-session')['candidates']
            self.assertEqual(len(found),1);self.assertEqual(found[0]['thread']['id'],a)
            self.assertEqual(found[0]['continued_from'][0]['id'],b)
            placement(w,a,'delete');w.load()
            self.assertFalse(locate(w,str(self.root))['candidates'])

    def test_atomic_split_merge_retries_and_continuation(self):
        w=self.wb
        with w.base.locked():
            w.load();a=w.create('原工作','原概况');w.load();original=w.append(a['id'],'原结果','result');w.load()
            original_ref=w.ref(w.find(original['id']))
            before=w.base.head
            preview=split(w,a['id'],'独立工作','新范围',refs=[original_ref],key='split',dry_run=True)
            self.assertEqual(w.base.head,before)
            result=split(w,a['id'],'独立工作','新范围',refs=[original_ref],key='split');w.load();b=result['thread']['id']
            self.assertEqual(w.resolve(original_ref)['body'],'原结果')
            w.bind(b,'test-agent',str(self.root),session_id='session-b')
            self.assertEqual(split(w,a['id'],'独立工作','新范围',refs=[original_ref],key='split')['thread']['id'],b)
            merged=merge(w,[b],a['id'],key='merge');w.load();head=w.base.head
            self.assertEqual(w.present(w.find(b))['merged_into']['id'],a['id'])
            self.assertEqual(continuation(w,b)['thread']['id'],a['id'])
            self.assertEqual(continuation(w,b)['local_execution'][0]['session_id'],'session-b')
            self.assertTrue(merge(w,[b],a['id'],key='merge')['replayed']);self.assertEqual(w.base.head,head)
            with self.assertRaises(Error):merge(w,[a['id']],b)
            placement(w,b,'delete');w.load();self.assertEqual(w.present(w.find(b))['lifecycle'],'trash')
            placement(w,b,'restore');w.load();self.assertEqual(w.present(w.find(b))['lifecycle'],'merged')
            placement(w,b,'restore');w.load();self.assertEqual(w.present(w.find(b))['lifecycle'],'active')
            w.append(b,'重开后完成','state',state='done');w.load()
            self.assertEqual(w.present(w.find(b))['lifecycle'],'archived')


class SharedLifecycleTests(ThreadFixture):
    def test_existing_request_can_be_answered_after_source_is_merged(self):
        target=self.shared('alice','继续工作');source=self.shared('alice','旧请求工作')
        with self.use('alice') as a:
            request=a.append(source,'请检查','request',recipient='bob')['id'];a.load()
            self.assertTrue(a.synchronize()['synchronized'])
            merge(a,[source],target);a.load();self.assertTrue(a.synchronize()['synchronized'])
        with self.use('bob') as b:
            b.synchronize(publish=False)
            b.append(source,'收到，沿原请求回复','reply',request=b.ref(b.find(request)));b.load()
            self.assertTrue(b.synchronize()['synchronized'])
            self.assertEqual(b.present(b.find(source))['lifecycle'],'merged')

    def test_private_reorganization_can_later_be_shared_in_source_order(self):
        with self.use('alice') as a:
            first=a.create('私人主工作','正式概况')['id'];a.load()
            child=split(a,first,'私人分支','分支范围')['thread']['id'];a.load()
            merge(a,[child],first);a.load()
            placement(a,child,'delete');a.load();placement(a,child,'restore');a.load()
            self.assertTrue(any(x['thread']['id']==child for x in a.context(first)['backlinks']))
            a.share(first);a.load();self.assertTrue(a.synchronize()['synchronized'])
            a.share(child);a.load();self.assertTrue(a.synchronize()['synchronized'])
            self.assertEqual(a.present(a.find(child))['merged_into']['id'],first)
            self.assertEqual(continuation(a,child)['thread']['id'],first)

    def test_shared_split_merge_withdraw_restore_and_private_boundary(self):
        a_id=self.shared('alice','公开工作','原公开背景')
        with self.use('alice') as a:
            old_ref=a.ref(a.find(a_id))
            child=split(a,a_id,'分出的公开工作','独立范围',key='split')['thread']['id'];a.load()
            merge(a,[child],a_id,key='merge');a.load();self.assertTrue(a.synchronize()['synchronized'])
            placement(a,child,'delete');a.load();self.assertTrue(a.synchronize()['synchronized'])
            self.assertEqual(a.present(a.find(child))['lifecycle'],'withdrawn')
            placement(a,child,'restore');a.load();self.assertTrue(a.synchronize()['synchronized'])
            self.assertEqual(a.present(a.find(child))['lifecycle'],'merged')
            private=a.create('私人准备','private-body')['id'];a.load();head=a.stores[self.repos['alice']].head
            with self.assertRaises(Error):merge(a,[private],a_id)
            self.assertEqual(a.stores[self.repos['alice']].head,head)
            placement(a,a_id,'delete');a.load();self.assertTrue(a.synchronize()['synchronized'])
            self.assertEqual(a.present(a.find(a_id))['lifecycle'],'withdrawn')
            self.assertEqual(a.resolve(old_ref,accepted=True)['body'],'原公开背景')
        with self.use('bob') as b:
            b.synchronize(publish=False)
            self.assertEqual(b.context(a_id)['thread']['lifecycle'],'withdrawn')
            self.assertEqual(b.resolve(old_ref,accepted=True)['body'],'原公开背景')
            with self.assertRaises(Error):placement(b,a_id,'delete')
            with self.assertRaises(Error):b.append(a_id,'撤回后追加')
        with self.use('alice') as a:
            placement(a,a_id,'restore');a.load();a.append(a_id,'恢复后追加');a.load()
            self.assertTrue(a.synchronize()['synchronized'])
            self.assertEqual(a.present(a.find(a_id))['lifecycle'],'active')

    def test_cross_author_sources_are_referenced_without_reassignment(self):
        target=self.shared('alice','综合工作');source=self.shared('bob','他人结果','保留作者')
        with self.use('alice') as a:
            a.synchronize(publish=False)
            source_head=a.stores[self.repos['bob']].head
            result=merge(a,[source],target);a.load()
            self.assertFalse(result['merged']);self.assertEqual(len(result['retained_sources']),1)
            self.assertEqual(a.stores[self.repos['bob']].head,source_head)
            self.assertTrue(a.synchronize()['synchronized'])
        with self.use('bob') as b:
            b.synchronize(publish=False)
            self.assertEqual(b.present(b.find(source))['lifecycle'],'active')
            self.assertEqual(b.find(source)['doc']['meta']['author'],'bob')

    def test_withdrawal_creates_history_archive_without_private_execution(self):
        self.hub.initialize_community(self.root/'knowledge')
        for user in self.configs:
            self.hub.spaces({'method':'community_name','name':user,'key':'name-'+user},user)
            with self.use(user) as w:w.discover()
        tid=self.shared('alice','撤回留档','公开概况')
        with self.use('alice') as a:
            a.bind(tid,'test-agent',str(self.root),session_id='never-share-session')
            placement(a,tid,'delete');a.load();result=a.synchronize();self.assertTrue(result['synchronized'],result)
            kb=Knowledge(a)
            archives=[x for x in kb.versions.values() if x['meta']['resource_kind']=='archive']
            self.assertTrue(archives)
            file=archives[-1]['meta']['files'][0]
            raw=kb.download(file['sha256'],file['size'])
            self.assertNotIn(b'never-share-session',raw)
