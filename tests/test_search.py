"""Real source records, bounded candidate retrieval and immutable source checks."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.community import Community, packed, digest
from cosmesh.errors import Error
from cosmesh.knowledge import Knowledge
from cosmesh.protocol import encode
from cosmesh.search import Search, query_groups, excerpt
from cosmesh.store import Store, atomic
from cosmesh.thread_protocol import new_id, content_version
from cosmesh.threads import Workbench, initialize
import test_system_access as native_fixtures


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()));self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.work=self.root/'work';initialize(self.work,'alice')
        self.wb=Workbench(self.work);self.wb.load()
        self.shared=Store(self.work/'.cosmesh/mesh/test-space')
        self.shared.initialize({'schema_version':2,'product':'cosmesh','user':'alice','timezone':'Asia/Shanghai','mode':'local_unverified','scope':'mesh'})
        self.thread=self.document('thread','执行环境交接','目录映射和主机定位')
        self.record=self.document('entry','定位方式','恢复 Session 时按绑定的路径和主机读取交接上下文。',thread=self.thread['meta']['id'],type='result')
        self.shared.commit({'documents/'+d['meta']['id']+'.md':encode(d) for d in (self.thread,self.record)},'seed')
        self.shared.save_client({'user':'alice','remote':{'kind':'system','repo':'test-space','discovery':'/synthetic-only'},'remote_revision':self.shared.head})
        self.wb.client.update(actor='alice',own_repo='test-space',spaces={'test-space':{'owner':'alice'}});self.wb.save_client();self.wb.load()
        self.private=self.wb.create('私人材料','薪酬草稿，不共享')['id'];self.wb.load()
        self.wb.append(self.private,'私人引用不应出现在公开回链',refs=[self.wb.ref(self.wb.find(self.record['meta']['id']))]);self.wb.load()
        self.server=Community(self.root/'library','alice');self.server.initialize()
        self.server.apply('alice','set_name',{'name':'林岚'},'name',None)
        self.text='这是资料里的原文：坐标投影。'+'x'*600+' Projected map 使用明确的坐标约定。'
        sha=self.server.save_blob(self.text.encode())
        self.resource=self.server.apply('alice','resource_add',{'id':'K-'+str(__import__('uuid').uuid4()),'title':'坐标手册','body':'资料入口',
            'files':[{'name':'manual.md','sha256':sha,'size':len(self.text.encode()),'text_sha256':sha,'text_status':'extracted','extractor':'cosmesh.text'}]},'add',None)['id']
        info=self.server.snapshot('alice');data=json.loads(self.server.blob(info['sha256']).read_text())
        data.update(sha256=info['sha256'],synced_at='2026-11-01T00:00:00+00:00')
        self.cache=self.work/'.cosmesh/knowledge/snapshot.json';atomic(self.cache,packed(data));self.cache.chmod(0o600)
        self.search=Search(self.wb)

    def document(self,kind,title,body,**fields):
        return {'meta':{'schema_version':2,'kind':kind,'id':new_id(kind),'author':'alice',
                        'created_at':'2026-11-01T00:00:00+00:00','title':title,**fields},'body':body}

    def test_batch_variants_find_records_and_metadata_with_real_names(self):
        direct=self.search.query(['电脑换了以后怎么接着干'],offline=True)
        self.assertEqual(direct['hits'],[])
        result=self.search.query(['电脑换了以后怎么接着干','session 恢复','坐标 手册'],offline=True)
        ids={h['source']['id'] for h in result['hits']}
        self.assertIn(self.record['meta']['id'],ids);self.assertIn(self.resource,ids)
        self.assertEqual(next(h for h in result['hits'] if h['source']['id']==self.record['meta']['id'])['author']['name'],'林岚')
        self.assertTrue(all(h['source']['version'] and h['requires_source_review'] for h in result['hits']))

    def test_mesh_scope_never_exposes_private_results_or_backlinks(self):
        with patch.object(self.search.kb,'rpc',side_effect=AssertionError('Unexpected network')):
            self.assertEqual(self.search.query(['薪酬'],scope='mesh',offline=True)['hits'],[])
            result=self.search.query(['薪酬'],scope='base',full_text=True,offline=True)
        self.assertEqual(result['hits'][0]['source']['id'],self.private)
        result=self.search.read(self.record['meta']['id'],repo='test-space',scope='mesh',offline=True)
        self.assertIsNone(result['coverage']['base_revision'])
        self.assertNotIn('私人引用',json.dumps(result,ensure_ascii=False))
        self.assertEqual(result['incoming'],[])
        with self.assertRaises(Error):self.search.read(self.private,scope='mesh',offline=True)

    def test_source_read_pins_old_content_and_exposes_corrections(self):
        original=self.wb.ref(self.wb.find(self.record['meta']['id']))
        correction=self.wb.append(self.thread['meta']['id'],'旧方案后来被替换，需要读更正。',record_type='correction',supersedes=original)['id']
        self.wb.load();search=Search(self.wb)
        result=search.read(original['id']+'@'+original['version'],repo='test-space',offline=True)
        self.assertTrue(result['source_version_matches']);self.assertEqual(result['text'],self.record['body'])
        self.assertIn(correction,{x['source']['id'] for x in result['incoming']})
        self.assertEqual(result['thread']['source']['id'],self.thread['meta']['id'])
        changed={'meta':dict(self.thread['meta']),'body':'更新后的工作概况'}
        old=self.wb.ref(self.wb.find(self.thread['meta']['id']));self.shared.commit({'documents/'+old['id']+'.md':encode(changed)},'edit')
        self.wb.load();result=Search(self.wb).read(old['id']+'@'+old['version'],offline=True)
        self.assertFalse(result['is_current']);self.assertEqual(result['text'],self.thread['body'])

    def test_fulltext_batch_matches_across_distant_terms_then_verifies_original(self):
        self.wb.client['connection']={'kind':'system','repo':'spaces','discovery':'/synthetic-only'}
        search=Search(self.wb);calls=[]
        def rpc(payload):calls.append(payload);return self.server.rpc(payload,'alice',None)
        with patch.object(search.kb,'rpc',side_effect=rpc):
            result=search.query(['坐标 projected'],full_text=True)
            self.assertEqual(len(result['hits']),1)
            hit=result['hits'][0];self.assertTrue(hit['excerpt_from_index'])
            self.assertEqual(len(calls),1);self.assertEqual(calls[0]['method'],'community_search_batch')
            read=search.read(self.resource+'@'+hit['source']['version'],file='manual.md')
        self.assertTrue(read['source_version_matches']);self.assertEqual(read['text'],self.text)
        self.assertEqual(read['location']['sha256'],digest(self.text.encode()))

    def test_offline_fulltext_reports_missing_coverage_and_bad_cache_is_rejected(self):
        result=self.search.query(['projected'],full_text=True,offline=True)
        self.assertEqual(result['hits'],[]);self.assertTrue(result['coverage']['limited'])
        self.assertEqual(result['coverage']['missing_text_count'],1)
        file=self.search.kb.doc(self.resource)['meta']['files'][0]
        path=self.search.kb.root/file['text_sha256'];path.write_text(self.text)
        result=self.search.query(['projected'],full_text=True,offline=True)
        self.assertEqual(result['hits'][0]['source']['id'],self.resource)
        path.write_text('tampered')
        with self.assertRaises(Error) as caught:self.search.read(self.resource,file='manual.md',offline=True)
        self.assertEqual(caught.exception.code,'CHECKSUM_MISMATCH')
        path.unlink();outside=self.root/'outside.txt';outside.write_text('private outside text');path.symlink_to(outside)
        with self.assertRaises(Error) as caught:self.search.read(self.resource,file='manual.md',offline=True)
        self.assertEqual(caught.exception.code,'PATH_INVALID')
        path.unlink()
        self.search.kb.doc(self.resource)['body']='tampered'
        with self.assertRaises(Error):self.search.read(self.resource,offline=True)

    def test_pagination_dedup_constraints_and_date_timezone(self):
        result=self.search.query(['session','SESSION'],offline=True,limit=1)
        self.assertEqual(len(result['query_groups']),1)
        self.assertEqual(len(result['hits']),1)
        self.assertEqual(self.search.query(['session'],author='林岚',after='2026-11-02',offline=True)['hits'],[])
        result=self.search.query(['session'],author='林岚',after='2026-11-01',before='2026-11-01',offline=True)
        self.assertTrue(result['hits'])
        for queries in ([],[''],['a']*9):
            with self.assertRaises(Error):query_groups(queries)
        self.assertIn('Straße',excerpt('Eine Straße hier',['strasse'])['text'])

    def test_server_cap_and_legacy_failure_are_explicit(self):
        self.wb.client['connection']={'kind':'system','repo':'spaces','discovery':'/synthetic-only'};search=Search(self.wb)
        with patch.object(search.kb,'rpc',side_effect=Error('COMMAND_DENIED','old Hub')):
            result=search.query(['session'],full_text=True)
        self.assertTrue(result['coverage']['limited']);self.assertEqual(result['coverage']['server'][0]['error']['code'],'COMMAND_DENIED')
        result=self.server.rpc({'method':'community_search_batch','queries':['坐标','projected'],'limit':1},'alice',None)
        self.assertEqual(len(result['hits']),1)
        self.assertEqual(self.server.rpc({'method':'community_search_batch','queries':["' OR 1=1 --"],'limit':1},'alice',None)['hits'],[])

    def test_cli_batch_uses_one_load_and_search_is_read_only(self):
        before=(self.wb.base.head,self.shared.head)
        original=Workbench.load;calls=[]
        def counted(wb):calls.append(wb);return original(wb)
        for product in ('cosmesh','cosbase'):
            calls.clear()
            with patch.object(Workbench,'load',counted),redirect_stdout(io.StringIO()) as output:
                code=main(['search','query','--no-legacy','怎么接着干','--query','session 恢复','--root',str(self.work),'--offline','--json'],product)
            result=json.loads(output.getvalue());self.assertEqual(code,0,result);self.assertEqual(len(calls),1)
            self.assertTrue(result['data']['hits']);self.assertIsNone(result['sync'])
        self.assertEqual(before,(self.wb.base.head,self.shared.head))

    def test_source_batch_loads_once_and_keeps_partial_errors_explicit(self):
        original=Workbench.load;calls=[]
        def counted(wb):calls.append(wb);return original(wb)
        with patch.object(Workbench,'load',counted),redirect_stdout(io.StringIO()) as output:
            code=main(['search','read','--no-legacy',self.record['meta']['id'],self.resource,'missing-id','--root',str(self.work),'--offline','--json'])
        value=json.loads(output.getvalue());self.assertEqual(code,0,value);self.assertEqual(len(calls),1)
        self.assertFalse(value['data']['complete']);self.assertEqual(len(value['data']['sources']),3)
        self.assertTrue(value['data']['sources'][0]['source_version_matches'])
        self.assertIn('error',value['data']['sources'][2])

    def test_real_name_maps_private_owner_without_mixing_another_hub_account(self):
        import shutil
        self.server.apply('mac-user','set_name',{'name':'另一位成员'},'other-name',None)
        work=self.root/'different-account';initialize(work,'mac-user');wb=Workbench(work)
        shutil.copytree(self.shared.root,work/'.cosmesh/mesh/test-space')
        wb.client.update(actor='alice',own_repo='test-space',spaces={'test-space':{'owner':'alice'}});wb.save_client();wb.load()
        private=wb.create('共同关键词 私人','本人私人工作')['id'];wb.load()
        shared=wb.stores['test-space']
        mine=self.document('entry','共同关键词 本人','本人的共享记录',thread=self.thread['meta']['id'],type='note')
        other=self.document('entry','共同关键词 另一位','另一位成员的共享记录',thread=self.thread['meta']['id'],type='note');other['meta']['author']='mac-user'
        shared.commit({'documents/'+d['meta']['id']+'.md':encode(d) for d in (mine,other)},'different-people')
        info=self.server.snapshot('alice');data=json.loads(self.server.blob(info['sha256']).read_text())
        cache=Knowledge(wb);atomic(cache.path,packed(data));cache.path.chmod(0o600);wb.load();search=Search(wb)
        own={h['source']['id'] for h in search.query(['共同关键词'],author='林岚',offline=True)['hits']}
        self.assertEqual(own,{private,mine['meta']['id']})
        theirs={h['source']['id'] for h in search.query(['共同关键词'],author='另一位成员',offline=True)['hits']}
        self.assertEqual(theirs,{other['meta']['id']})
        read=search.read(private,offline=True)
        self.assertEqual(read['author']['account'],'mac-user');self.assertEqual(read['author']['identity_account'],'alice')
        self.assertEqual(read['author']['name'],'林岚');self.assertEqual(read['document']['author'],'mac-user')
        read=search.read(other['meta']['id'],offline=True)
        self.assertEqual(read['author']['name'],'另一位成员');self.assertEqual(read['author']['identity_account'],'mac-user')
        search.people={};search.kb.data=dict(search.kb.data,users=[])
        own={h['source']['id'] for h in search.query(['共同关键词'],author='alice',offline=True)['hits']}
        self.assertEqual(own,{private,mine['meta']['id']})


class NativeSearchTests(unittest.TestCase):
    setUp=native_fixtures.SystemAccessTests.setUp
    start=native_fixtures.SystemAccessTests.start
    stop=native_fixtures.SystemAccessTests.stop

    def test_fulltext_batch_native_identity_and_fresh_reads_preserve_drafts(self):
        self.hub.initialize_community(self.folder/'library')
        self.hub.spaces({'method':'community_name','name':'搜索隔离验收','key':'name'},self.user)
        raw='文档原文明确写着：continuation session 用来跨环境接续。'.encode()
        sha=self.hub.community().save_blob(raw)
        kid='K-'+str(__import__('uuid').uuid4())
        self.hub.community().apply(self.user,'resource_add',{'id':kid,'title':'资料入口','body':'参见所附文件',
            'files':[{'name':'manual.md','sha256':sha,'size':len(raw),'text_sha256':sha,'text_status':'extracted','extractor':'cosmesh.text'}]},'add',self.hub)
        work=self.folder/'search-work';initialize(work,self.user);wb=Workbench(work)
        with wb.base.locked():
            wb.load();wb.discover(service=str(self.discovery))
            thread=wb.create('共享查询验证')['id'];wb.load();wb.share(thread);wb.synchronize()
            pending=wb.append(thread,'尚未发布的接续线索')['id'];wb.load()
            Knowledge(wb).stage('resource_add',{'id':'K-'+str(__import__('uuid').uuid4()),'title':'仅本地待提交资料'})
            repo=wb.client['own_repo'];before=self.hub.repo(repo).git('rev-parse','main')
        with redirect_stdout(io.StringIO()) as output:
            code=main(['search','query','--no-legacy','怎么接着干','--query','continuation session','--full-text','--require-fresh','--root',str(work),'--json'])
        value=json.loads(output.getvalue());self.assertEqual(code,0,value)
        hit=next(x for x in value['data']['hits'] if x['source']['id']==kid)
        self.assertTrue(hit['excerpt_from_index']);self.assertEqual(self.hub.repo(repo).git('rev-parse','main'),before)
        wb=Workbench(work)
        with wb.base.locked():
            wb.load();self.assertFalse(wb.accepted(wb.find(pending)))
            self.assertEqual(len(list(Knowledge(wb).outbox().glob('*.json'))),1)
        with redirect_stdout(io.StringIO()) as output:
            code=main(['search','read','--no-legacy','library/'+kid+'@'+hit['source']['version'],'--file','manual.md','--root',str(work),'--json'])
        value=json.loads(output.getvalue());self.assertEqual(code,0,value)
        self.assertEqual(value['data']['text'],raw.decode());self.assertTrue(value['data']['source_version_matches'])
