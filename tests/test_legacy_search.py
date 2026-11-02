"""Old sources retain their format and privacy while sharing one bounded reader."""
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.legacy_search import LegacySources
from cosmesh.protocol import encode, version
from cosmesh.search import Search
from cosmesh.service import Service, initialize
from cosmesh.store import Store


class LegacySearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()));self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.base=self.make('cosbase');self.mesh=self.make('cosmesh')
        self.bp=self.op(self.base,'project.create',title='数据生成计划',body='private-only 父概况 '+('长'*2000))
        self.bt=self.op(self.base,'task.create',title='按星系划分',body='保留所有 realization 在同一集合',project=self.bp['id'],next_action='确定性 subhalo split')
        self.bu=self.op(self.base,'update.add',title='首次查阅',body='12行没有正式split',project=self.bp['id'],type='progress')
        self.mp=self.op(self.mesh,'project.create',title='共享工作',body='public-only 共享背景')
        self.mt=self.op(self.mesh,'task.create',title='检查坐标',body='投影坐标需要核验',project=self.mp['id'])
        self.mu=self.op(self.mesh,'update.add',title='坐标检查输出',body='检查通过，三个条件成立',project=self.mp['id'],task=self.mt['id'],type='progress')
        self.mesh.save_client({'user':'alice','remote_revision':self.mesh.head})

    def make(self, product):
        s=Store(self.root/product);initialize(s,product,'alice','Asia/Shanghai');return s

    def op(self, store, action, **payload):
        return Service(store,json.loads(store.path('.cosmesh/config.json').read_text())['product']).run(action,payload)[0]['result']

    def cli(self,*args):
        with redirect_stdout(io.StringIO()) as output:code=main(list(args)+['--json'])
        result=json.loads(output.getvalue());return code,result

    def search(self, scope='all'):
        return Search(legacy=LegacySources([self.base.root,self.mesh.root],scope))

    def test_variants_search_all_original_kinds_and_metadata_once(self):
        calls=[];original=Store.snapshot
        def counted(store,*args,**kwargs):calls.append(str(store.root));return original(store,*args,**kwargs)
        with patch.object(Store,'snapshot',counted):
            s=self.search();result=s.query(['怎么防止泄漏','subhalo split','坐标 检查'],offline=True)
            read=s.read(self.bt['id'],offline=True)
        self.assertEqual(len(calls),2)
        self.assertIn(self.bt['id'],{h['source']['id'] for h in result['hits']})
        self.assertIn(self.mu['id'],{h['source']['id'] for h in result['hits']})
        self.assertEqual(read['source']['version'],self.bt['version']);self.assertEqual(read['document']['kind'],'task')
        self.assertEqual(read['text'],self.bt['body']);self.assertFalse(read['accepted'])

    def test_mesh_scope_excludes_base_and_private_backlinks(self):
        s=self.search('mesh');result=s.query(['private-only','public-only'],scope='mesh',offline=True)
        data=json.dumps(result,ensure_ascii=False)
        self.assertNotIn(str(self.base.root),data);self.assertNotIn(self.bp['id'],data)
        self.assertTrue(result['hits']);self.assertEqual(len(result['coverage']['legacy']['sources']),1)
        with self.assertRaises(Error):s.read(self.bt['id'],scope='mesh',offline=True)
        read=s.read(self.mt['id'],scope='mesh',offline=True)
        self.assertEqual({x['source']['id'] for x in read['incoming']},{self.mu['id']})
        self.assertTrue(read['accepted']);self.assertEqual(read['state']['work_state'],'todo')

    def test_old_source_reads_never_take_writer_lock_or_change_journal(self):
        self.base.pending.write_text('{"preserved":true}')
        before=(self.base.head,self.base.pending.read_bytes(),self.base.path('.cosmesh/lock').stat().st_mtime_ns)
        with patch.object(Store,'locked',side_effect=AssertionError('Old search must not take a write lock')):
            s=self.search();self.assertTrue(s.query(['subhalo split'],offline=True)['hits']);s.read(self.bt['id'],offline=True)
        self.assertEqual(before,(self.base.head,self.base.pending.read_bytes(),self.base.path('.cosmesh/lock').stat().st_mtime_ns))

    def test_bounded_batch_originals_and_parent_dedup(self):
        args=['search','read',self.bt['id'],self.bu['id'],'--root',str(self.base.root),'--offline','--chars','80','--parent-chars','100']
        code,result=self.cli(*args);self.assertEqual(code,0,result)
        a,b=result['data']['sources'];self.assertLessEqual(len(a['text']),80)
        self.assertEqual(len(a['thread']['overview']),100);self.assertEqual(b['thread']['overview'],'')
        self.assertEqual(b['thread']['overview_omitted'],'already_in_batch')
        self.assertNotIn('body',a['document']);self.assertTrue(a['source_version_matches'])
        self.assertEqual(a['outgoing'][0]['id'],self.bp['id'])

    def test_fixed_old_versions_and_current_correction_links(self):
        old=self.bt
        self.op(self.base,'doc.edit',id=old['id'],expect=old['version'],body='修订后的工作正文')
        correction=self.op(self.base,'update.add',title='更正查阅',body='更正原检查结果',project=self.bp['id'],type='correction',supersedes=self.bu['id'])
        s=self.search();read=s.read(old['id']+'@'+old['version'],offline=True)
        self.assertEqual(read['text'],old['body']);self.assertFalse(read['is_current']);self.assertTrue(read['source_version_matches'])
        result=s.read(self.bu['id'],offline=True)
        self.assertEqual(result['incoming'][0]['source']['id'],correction['id']);self.assertEqual(result['incoming'][0]['relation'],'supersedes')

    def test_duplicate_copies_dedup_but_differing_versions_are_ambiguous(self):
        copy=self.root/'copy';shutil.copytree(self.base.root,copy)
        s=Search(legacy=LegacySources([self.base.root,copy]))
        self.assertEqual(len([h for h in s.query(['subhalo split'],offline=True)['hits'] if h['source']['id']==self.bt['id']]),1)
        self.assertEqual(s.read(self.bt['id'],offline=True)['text'],self.bt['body'])
        self.op(Store(copy),'doc.edit',id=self.bt['id'],expect=self.bt['version'],body='不同副本内容')
        s=Search(legacy=LegacySources([self.base.root,copy]))
        with self.assertRaises(Error) as caught:s.read(self.bt['id'],offline=True)
        self.assertEqual(caught.exception.code,'AMBIGUOUS_ID')

    def test_owned_discovery_without_thread_workbench_and_explicit_opt_out(self):
        with patch.dict(os.environ,{'COSMESH_DATA_DIR':str(self.root)}):
            code,result=self.cli('search','query','subhalo split','--offline')
            self.assertEqual(code,0,result);self.assertTrue(result['data']['hits']);self.assertFalse(result['data']['coverage']['thread_workbench'])
            code,result=self.cli('search','query','subhalo','--no-legacy','--offline')
            self.assertEqual(code,0,result);self.assertEqual(result['data']['hits'],[])

    def test_unavailable_sources_report_coverage_and_symlinks_are_rejected(self):
        link=self.root/'linked';link.symlink_to(self.base.root,target_is_directory=True)
        old=LegacySources([link,self.mesh.root],'mesh');s=Search(legacy=old)
        result=s.query(['public-only'],scope='mesh',offline=True)
        self.assertTrue(result['hits']);self.assertTrue(result['coverage']['limited'])
        self.assertNotIn(str(self.base.root),json.dumps(result));self.assertNotIn(str(link),json.dumps(result))
        self.assertEqual(old.errors[0]['error']['code'],'PATH_INVALID')

    def test_freshness_checks_old_mesh_without_fetch_or_pushing_drafts(self):
        remote={'kind':'system','repo':'test-old','discovery':'/synthetic'}
        self.mesh.save_client({'user':'alice','remote_revision':self.mesh.head,'remote':remote})
        before=(self.mesh.head,self.mesh.path('.cosmesh/client.json').read_bytes())
        old=LegacySources([self.base.root,self.mesh.root])
        with patch('cosmesh.sync.remote_status',return_value={'revision':self.mesh.head}) as call:
            old.verify_fresh();self.assertEqual(call.call_count,1)
        self.assertTrue(next(m for m in old.models.values() if m['scope']=='mesh')['fresh'])
        self.assertEqual(before,(self.mesh.head,self.mesh.path('.cosmesh/client.json').read_bytes()))
        with patch('cosmesh.sync.remote_status',return_value={'revision':'f'*40}):
            with self.assertRaises(Error) as caught:old.verify_fresh()
        self.assertEqual(caught.exception.code,'FRESHNESS_UNAVAILABLE')

    def test_archived_and_finished_are_searchable_trash_requires_opt_in(self):
        self.op(self.base,'doc.archive',id=self.bt['id'],expect=self.bt['version'])
        s=self.search();self.assertTrue(any(h['source']['id']==self.bt['id'] for h in s.query(['subhalo split'],offline=True)['hits']))
        current=Service(self.base,'cosbase').run('doc.show',{'id':self.bt['id']})[0]
        self.op(self.base,'doc.trash',id=self.bt['id'],expect=current['version']);s=self.search()
        self.assertFalse(any(h['source']['id']==self.bt['id'] for h in s.query(['subhalo split'],offline=True)['hits']))
        self.assertTrue(any(h['source']['id']==self.bt['id'] for h in s.query(['subhalo split'],include_inactive=True,offline=True)['hits']))

    def test_batch_partial_errors_and_filters(self):
        code,result=self.cli('search','read',self.bt['id'],'T-00000000-0000-4000-8000-000000000001','--root',str(self.base.root),'--offline')
        self.assertEqual(code,0,result);self.assertFalse(result['data']['complete'])
        self.assertEqual(len(self.search().query(['subhalo'],scope='base',author='alice',after='2099-01-01',offline=True)['hits']),0)
        with self.assertRaises(Error):self.search().read(self.bt['id']+'@bad',offline=True)

    def test_mixed_workbench_batch_uses_one_snapshot_per_old_store(self):
        from cosmesh.threads import Workbench, initialize as init_work
        work=self.root/'work';init_work(work,'alice');wb=Workbench(work);wb.load()
        modern=wb.create('继续 subhalo split','当前工作')['id']
        args=['search','query','subhalo split','--root',str(work),'--legacy-root',str(self.base.root),'--legacy-root',str(self.mesh.root),'--offline']
        original=Store.snapshot;calls=[]
        def counted(store,*a,**kw):calls.append(str(store.root));return original(store,*a,**kw)
        with patch.object(Store,'snapshot',counted):code,result=self.cli(*args)
        self.assertEqual(code,0,result);self.assertEqual(calls.count(str(self.base.root)),1);self.assertEqual(calls.count(str(self.mesh.root)),1)
        ids={x['source']['id'] for x in result['data']['hits']};self.assertIn(modern,ids);self.assertIn(self.bt['id'],ids)
        selected=[x['source']['repo']+'/'+x['source']['id']+'@'+x['source']['version'] for x in result['data']['hits'] if x['source']['id'] in {modern,self.bt['id']}]
        code,result=self.cli('search','read',*selected,'--root',str(work),'--legacy-root',str(self.base.root),'--offline')
        self.assertEqual(code,0,result);self.assertTrue(result['data']['complete'])
        self.assertEqual(len(result['data']['sources']),2)

    def test_removed_parent_does_not_hide_retained_child_work(self):
        self.op(self.base,'doc.trash',id=self.bp['id'],expect=self.bp['version'])
        result=self.search().query(['subhalo split'],offline=True)
        hit=next(x for x in result['hits'] if x['source']['id']==self.bt['id'])
        self.assertEqual(hit['parent_lifecycle'],'trash')

    def test_git_directory_symlink_cannot_redirect_the_old_reader(self):
        target=self.root/'redirected-git';self.base.path('.git').rename(target)
        (self.base.root/'.git').symlink_to(target,target_is_directory=True)
        sources=LegacySources([self.base.root])
        self.assertEqual(sources.models,{})
        self.assertEqual(sources.errors[0]['error']['code'],'PATH_INVALID')

    def test_known_source_batch_skips_unrelated_old_stores(self):
        from cosmesh.legacy_search import source_repo
        ref=source_repo(self.base.root)+'/'+self.bt['id']+'@'+self.bt['version']
        original=Store.snapshot;calls=[]
        def counted(store,*a,**kw):calls.append(str(store.root));return original(store,*a,**kw)
        with patch.object(Store,'snapshot',counted):
            code,result=self.cli('search','read',ref,'--root',str(self.base.root),'--legacy-root',str(self.mesh.root),'--offline')
        self.assertEqual(code,0,result);self.assertEqual(calls,[str(self.base.root)])
        with patch.object(Store,'snapshot',side_effect=AssertionError('Knowledge-only query does not load old documents')):
            code,result=self.cli('search','query','文献','--kind','knowledge','--root',str(self.base.root),'--offline')
        self.assertEqual(code,0,result);self.assertEqual(result['data']['hits'],[])
