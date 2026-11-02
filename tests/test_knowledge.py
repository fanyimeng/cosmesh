"""Real-name requests, immutable documents, accepted Thread archives and contribution lineage."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from test_threads import ThreadFixture
from cosmesh.errors import Error
from cosmesh.knowledge import Knowledge,extract,select_user
from cosmesh.community import packed,digest
from cosmesh.cli import main
from cosmesh.protocol import public
from cosmesh.protocol import encode
from cosmesh.thread_protocol import new_id
from cosmesh.threads import now


class KnowledgeTests(ThreadFixture):
    def setUp(self):
        super().setUp()
        self.hub.initialize_community(self.root/'knowledge-library')
        self.names={'alice':'张三','bob':'李四','carol':'王五'}
        for user,name in self.names.items():
            self.hub.spaces({'method':'community_name','name':name,'key':'name-'+user},user)
        for user in self.names:
            with self.use(user) as wb:wb.discover()

    def cli(self,user,*args):
        output=io.StringIO()
        with redirect_stdout(output):code=main([*args,'--root',str(self.wbs[user].base.root),'--json'])
        result=json.loads(output.getvalue())
        self.assertEqual(code,0,result)
        return result['data']

    def test_real_names_requests_overview_disambiguation_and_account_state(self):
        ta=self.shared('alice','合作总览')
        sent=self.cli('alice','thread','request',ta,'--to','李四','--body','请核对并给出依据','--sync')
        self.assertTrue(sent['publication']['synchronized'],sent)
        with self.use('alice') as a:
            a.synchronize(publish=False)
            q=a.find(sent['result']['id']);shown=a.present(q)
            self.assertEqual(shown['to'],'bob');self.assertEqual(shown['to_name'],'李四')
            overview=Knowledge(a).overview('李四')
            self.assertEqual(overview['user']['account'],'bob');self.assertEqual(len(overview['requests']),1)
        before=self.hub.community().check('carol')
        self.hub.spaces({'method':'community_name','name':'李四','expect':before['version'],'key':'same-name'},'carol')
        with self.use('alice') as a:
            kb=Knowledge(a);kb.refresh()
            with self.assertRaises(Error) as caught:kb.user('李四')
            self.assertEqual(caught.exception.code,'AMBIGUOUS_USER')
            self.assertEqual(kb.user('bob')['id'],overview['user']['id'])
        admin=self.hub.settings()['admin'];community=self.hub.community()
        bob=community.check('bob')
        with self.assertRaises(Error):community.apply('alice','manage_user',{'user':'bob','expect':bob['version'],'status':'disabled'},'deny',self.hub)
        disabled=community.apply(admin,'manage_user',{'user':'bob','expect':bob['version'],'status':'disabled'},'disable',self.hub)
        with self.assertRaises(Error) as caught:self.hub.policy(self.repos['alice'],'bob')
        self.assertEqual(caught.exception.code,'USER_DISABLED')
        active=community.apply(admin,'manage_user',{'user':'bob','expect':disabled['version'],'status':'active'},'enable',self.hub)
        self.assertEqual(active['id'],bob['id'])
        pending='root' if admin!='root' else 'nobody'
        with self.assertRaises(Error) as caught:community.check(pending,write=True)
        self.assertEqual(caught.exception.code,'REAL_NAME_REQUIRED')

    def test_document_versions_sources_archives_reopen_and_private_boundary(self):
        ta=self.shared('alice','可归档的分析','共同背景')
        path=self.root/'guide.md';path.write_text('# 使用方法\n\n保留原始处理顺序。\n',encoding='utf-8')
        added=self.cli('alice','library','add','--title','处理手册','--file',str(path),'--body','供组内反复使用','--sync','--idempotency-key','manual')
        self.assertTrue(added['publication']['synchronized'],added)
        kid=added['result']['id']
        with self.use('alice') as a:
            kb=Knowledge(a);ref=kb.ref(kid);original=kb.doc(kid)
            self.assertEqual(kb.present(original)['author_name'],'张三')
            self.assertIn('保留原始',kb.text(kid)['parts'][0]['text'])
            a.bind(ta,'test-agent',str(self.root),session_id='never-publish-this-session')
            a.generation={'tool':'test-agent','model':'test-model','instruction':'基于手册形成结果','verification':'client_reported'}
            result=a.append(ta,'检查结果及适用条件','result',refs=[ref])['id'];a.load()
            done=a.append(ta,'完成本轮检查','state',state='done')['id'];a.load()
            synchronized=a.synchronize();self.assertTrue(synchronized['synchronized'],synchronized)
            kb=Knowledge(a)
            archives=[d for d in kb.versions.values() if d['meta']['resource_kind']=='archive']
            self.assertEqual(len(archives),1)
            archive=archives[0];asset=archive['meta']['id'];old_version=kb.ref(asset)['version']
            raw=kb.download(archive['meta']['files'][0]['sha256'],archive['meta']['files'][0]['size'])
            self.assertNotIn(b'never-publish-this-session',raw)
            frozen=json.loads(raw)
            self.assertEqual(frozen['closing'],done)
            self.assertTrue(any(x['meta']['id']==result for x in frozen['records']))
            self.assertEqual(frozen['citations'][0]['document']['meta']['id'],kid)
            self.assertEqual(a.present(a.find(ta))['lifecycle'],'archived')
            a.append(ta,'更正一个适用条件','correction',supersedes=a.ref(a.find(result)));a.load()
            self.assertTrue(a.synchronize()['synchronized'])
            kb=Knowledge(a)
            self.assertNotEqual(kb.ref(asset)['version'],old_version)
            self.assertIn((asset,old_version),kb.versions)
            self.assertEqual(len([x for x in kb.versions if x[0]==asset]),2)
            a.append(ta,'重开检查','state',state='active');a.load()
            a.append(ta,record_type='lifecycle',lifecycle='active');a.load()
            self.assertTrue(a.synchronize()['synchronized'])
            self.assertEqual(a.present(a.find(ta))['work_state'],'active')
            self.assertEqual(a.present(a.find(ta))['lifecycle'],'active')
            self.assertIn((asset,old_version),Knowledge(a).versions)
            a.append(ta,'再次完成','state',state='done');a.load()
            self.assertEqual(a.present(a.find(ta))['lifecycle'],'archived')
            self.assertTrue(a.synchronize()['synchronized'])
            overview=Knowledge(a).overview('张三')
            self.assertTrue(any(x['id']==result for x in overview['contributions']))
            self.assertTrue(any(x['action']=='thread_archived' for x in overview['activity']))
            self.assertTrue(any(x['payload'].get('generation',{}).get('model')=='test-model' for x in overview['activity']))
            self.assertNotIn('never-publish-this-session',json.dumps(Knowledge(a).data))
        path.write_text('# 新版手册\n\n修订后的使用方法。\n',encoding='utf-8')
        updated=self.cli('alice','library','revise',kid,'--expect',ref['version'],'--file',str(path),'--sync')
        self.assertTrue(updated['publication']['synchronized'],updated)
        with self.use('alice') as a:
            kb=Knowledge(a)
            self.assertEqual(a.resolve(ref)['body'],'供组内反复使用')
            self.assertNotEqual(kb.ref(kid)['version'],ref['version'])
            self.assertEqual(kb.doc(kid)['meta']['previous'],ref['version'])
            self.assertTrue(kb.search('修订后的使用方法',full_text=True))
            self.assertFalse(kb.data['archive_errors'])
            # Append-only activity chain can be checked without trusting presentation indexes.
            previous=None
            for event in kb.data['events']:
                record={k:event[k] for k in ('id','object','actor','person','action','at','payload','previous')}
                self.assertEqual(record['previous'],previous)
                self.assertEqual(digest(packed(record).encode()),event['hash']);previous=event['hash']

    def test_local_drafts_idempotency_and_maintainer_transfer(self):
        path=self.root/'notes.md';path.write_text('离线草稿材料')
        staged=self.cli('alice','library','add','--title','待发布资料','--file',str(path),'--idempotency-key','offline-item')
        self.assertEqual(staged['publication'],'pending')
        self.assertEqual(len(self.hub.community().snapshot('alice')['user']['name']),2)
        synced=self.cli('alice','library','sync');self.assertTrue(synced['synchronized'])
        replay=self.cli('alice','library','add','--title','待发布资料','--file',str(path),'--idempotency-key','offline-item')
        self.assertEqual(replay['id'],staged['id']);self.assertTrue(replay['replayed'])
        with self.use('alice') as a:ref=Knowledge(a).ref(staged['id'])
        changed=self.cli('alice','library','transfer',staged['id'],'--to','李四','--expect',ref['version'],'--sync')
        self.assertTrue(changed['publication']['synchronized'],changed)
        with self.use('bob') as b:
            b.synchronize(publish=False);kb=Knowledge(b);doc=kb.doc(staged['id'])
            self.assertEqual(doc['meta']['author'],'alice');self.assertEqual(doc['meta']['maintainer'],'bob')
            expected=kb.ref(staged['id'])['version']
        revised=self.cli('bob','library','revise',staged['id'],'--expect',expected,'--body','接手以后补充的说明','--sync')
        self.assertTrue(revised['publication']['synchronized'],revised)
        community=self.hub.community()
        with community.db() as db:
            heads=dict(db.execute('SELECT id,version FROM heads').fetchall());db.execute('DELETE FROM search_text')
        restored=self.hub.spaces({'method':'community_reindex'},self.hub.settings()['admin'])
        self.assertTrue(restored['source_versions_unchanged'])
        with community.db() as db:self.assertEqual(dict(db.execute('SELECT id,version FROM heads').fetchall()),heads)
        self.assertTrue(self.hub.spaces({'method':'community_search','query':'接手以后','offset':0},'bob')['hits'])

    def test_forged_person_and_private_knowledge_source_are_rejected(self):
        ta=self.shared('alice','身份边界')
        with self.use('alice') as a:
            private=a.create('私人材料','do-not-publish')['id'];a.load();kb=Knowledge(a)
            pending=kb.stage('resource_add',{'id':'K-'+__import__('uuid').uuid4().__str__(),'title':'非法私人来源','refs':[a.ref(a.find(private))]},'private-source')
            result=kb.synchronize()
            self.assertFalse(result['synchronized']);self.assertIn('PRIVATE_REFERENCE',str(result['errors']))
            self.assertNotIn('do-not-publish',json.dumps(kb.data))
            person=kb.user('张三')['id']
        abandoned=self.cli('alice','library','abandon','private-source','--reason','撤销不合适的私人来源')
        self.assertTrue(abandoned['record_preserved'])
        with self.use('bob') as b:
            b.synchronize(publish=False)
            doc={'meta':{'schema_version':2,'kind':'entry','id':new_id('entry'),'author':'bob','author_id':person,
                 'created_at':now(),'title':'伪造稳定身份','thread':ta,'type':'note'},'body':'伪造内容'}
            b.stores[self.repos['alice']].commit({'documents/'+doc['meta']['id']+'.md':encode(doc)},'forged-person')
            result=b.synchronize()
            self.assertFalse(result['synchronized']);self.assertIn('AUTH_DENIED',str(result['errors']))

    def test_library_backlinks_find_work_results_corrections_and_archived_sources(self):
        ta = self.shared('alice', '已有综合')
        tb = self.shared('bob', '使用资料的检查')
        tc = self.shared('carol', '另一项检查')
        added = self.cli('alice', 'library', 'add', '--title', '参考资料', '--body', '固定原始依据', '--sync')
        kid = added['result']['id']
        with self.use('alice') as a:
            source = Knowledge(a).ref(kid)
        results = {}
        for user, thread in (('bob', tb), ('carol', tc)):
            with self.use(user) as worker:
                self.assertTrue(worker.synchronize(publish=False)['refreshed'])
            result = self.cli(user, 'thread', 'result', thread, '--body', user + ' 根据资料得出的结果',
                              '--ref', kid, '--ref', kid, '--sync')
            self.assertTrue(result['publication']['synchronized'])
            results[user] = result['result']['id']
        corrected = self.cli('bob', 'thread', 'correct', results['bob'], '--body', '补充结果适用条件', '--sync')['result']['id']
        self.cli('bob', 'thread', 'state', tb, '--to', 'done', '--sync')
        with self.use('alice') as a:
            a.synchronize(publish=False)
        combined = self.cli('alice', 'thread', 'result', ta, '--body', '基于 B 的已有综合结果',
                            '--ref', results['bob'], '--sync')['result']['id']
        with self.use('alice') as a:
            private = a.create('private-library-work', 'private-library-overview')['id']; a.load()
            a.append(private, 'private-library-result', 'result', refs=[source]); a.load()
            a.bind(tb, 'test-agent', str(self.root), session_id='private-library-session')
            heads = {repo: store.head for repo, store in a.stores.items()}
        # The cached read must not make a network request or rewrite any source.
        with patch.object(Knowledge, 'rpc', side_effect=AssertionError('offline read contacted Hub')):
            usage = self.cli('alice', 'library', 'backlinks', kid, '--offline')
            shown = self.cli('alice', 'library', 'show', kid, '--offline')
        self.assertEqual(usage['version_scope'], 'all')
        self.assertEqual(usage['coverage'], 'cached_mesh')
        self.assertEqual({x['id'] for x in usage['threads']}, {tb, tc})
        self.assertEqual(len(usage['backlinks']), 2)  # Duplicate citations are one edge.
        self.assertEqual({x['record']['id'] for x in usage['backlinks']}, set(results.values()))
        self.assertEqual(shown['backlinks'], usage['backlinks'])
        for link in usage['backlinks']:
            self.assertEqual(link['target'], source)
            self.assertTrue(link['record']['accepted'])
            self.assertIn(link['record']['author_name'], ('李四', '王五'))
        for secret in ('private-library-work', 'private-library-overview', 'private-library-result', 'private-library-session'):
            self.assertNotIn(secret, json.dumps(usage))
        context = self.cli('alice', 'thread', 'context', results['bob'], '--offline')
        self.assertEqual(context['thread']['lifecycle'], 'archived')
        self.assertEqual(context['citations'][0]['source'], source)
        self.assertIn(corrected, {x['record']['id'] for x in context['backlinks'] if x['relation'] == 'supersedes'})
        self.assertIn(combined, {x['record']['id'] for x in context['backlinks']})
        with self.use('alice') as a:
            self.assertEqual(heads, {repo: store.head for repo, store in a.stores.items()})
            a.load()
            self.assertEqual({x['record']['id'] for x in Knowledge(a).backlinks(kid)['backlinks']}, set(results.values()))

    def test_library_backlinks_pin_versions_and_fresh_reads_keep_drafts_private(self):
        ta = self.shared('alice', '新版分析')
        tb = self.shared('bob', '旧版分析')
        kid = self.cli('alice', 'library', 'add', '--title', '版本化资料', '--body', 'v1', '--sync')['result']['id']
        with self.use('bob') as b:
            b.synchronize(publish=False)
            v1 = Knowledge(b).ref(kid)['version']
        first = self.cli('bob', 'thread', 'result', tb, '--body', '依照 v1 的结果', '--ref', kid + '@' + v1, '--sync')['result']['id']
        self.cli('alice', 'library', 'revise', kid, '--expect', v1, '--body', 'v2', '--sync')
        with self.use('alice') as a:
            v2 = Knowledge(a).ref(kid)['version']
        second = self.cli('alice', 'thread', 'result', ta, '--body', '依照 v2 的结果', '--ref', kid + '@' + v2, '--sync')['result']['id']
        all_usage = self.cli('alice', 'library', 'backlinks', kid, '--require-fresh')
        self.assertEqual({x['record']['id'] for x in all_usage['backlinks']}, {first, second})
        old_usage = self.cli('alice', 'library', 'backlinks', kid + '@' + v1, '--offline')
        new_usage = self.cli('alice', 'library', 'backlinks', kid + '@' + v2, '--offline')
        self.assertEqual(old_usage['version_scope'], 'exact')
        self.assertEqual([x['record']['id'] for x in old_usage['backlinks']], [first])
        self.assertEqual([x['record']['id'] for x in new_usage['backlinks']], [second])
        self.assertEqual(old_usage['source']['version'], v1)
        self.assertEqual(old_usage['current_source']['version'], v2)
        self.cli('alice', 'library', 'revise', kid, '--expect', v2, '--status', 'withdrawn', '--sync')
        with self.use('alice') as a:
            draft = a.append(ta, '未发布的引用', 'result', refs=[Knowledge(a).ref(kid + '@' + v1)])['id']; a.load()
        self.cli('alice', 'library', 'add', '--title', '尚未发布的资料', '--body', '只留本地')
        remote_head = self.hub.repo(self.repos['alice']).git('rev-parse', 'main')
        usage = self.cli('alice', 'library', 'backlinks', kid + '@' + v1, '--require-fresh')
        self.assertEqual(usage['resource']['status'], 'published')
        self.assertEqual(usage['current_status'], 'withdrawn')
        self.assertNotEqual(usage['current_source']['version'], v2)
        pending = next(x for x in usage['backlinks'] if x['record']['id'] == draft)
        self.assertFalse(pending['record']['accepted'])
        self.assertEqual(self.hub.repo(self.repos['alice']).git('rev-parse', 'main'), remote_head)
        self.assertEqual(len(self.cli('alice', 'library', 'pending')), 1)
        with self.use('carol') as c:
            c.synchronize(publish=False)
            visible = Knowledge(c).backlinks(kid)
            self.assertNotIn(draft, {x['record']['id'] for x in visible['backlinks']})
            with self.assertRaises(Error) as caught:
                Knowledge(c).backlinks(kid + '@' + '0' * 64)
            self.assertEqual(caught.exception.code, 'REFERENCE_UNAVAILABLE')


class DocumentExtractionTests(unittest.TestCase):
    def test_cli_initializes_hub_and_its_independent_library(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as folder:
            root=Path(folder)
            for args in (['hub','init','--hub-root',str(root/'hub')],
                         ['hub','community-init','--hub-root',str(root/'hub'),'--library-root',str(root/'library')]):
                output=io.StringIO()
                with redirect_stdout(output):code=main([*args,'--json'])
                self.assertEqual(code,0,json.loads(output.getvalue()))
            self.assertTrue((root/'library/catalog.sqlite3').is_file())

    def test_office_html_pdf_and_original_fallback(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as folder:
            root=Path(folder)
            doc=root/'a.docx'
            with zipfile.ZipFile(doc,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="urn:w"><w:p><w:r><w:t>文字资料</w:t></w:r></w:p></w:document>')
            self.assertIn('文字资料',extract(doc)['text'])
            ppt=root/'a.pptx'
            with zipfile.ZipFile(ppt,'w') as z:
                for index in (2,1):z.writestr('ppt/slides/slide'+str(index)+'.xml','<a:sld xmlns:a="urn:a"><a:p><a:r><a:t>Slide '+str(index)+'</a:t></a:r></a:p></a:sld>')
            self.assertLess(extract(ppt)['text'].index('Slide 1'),extract(ppt)['text'].index('Slide 2'))
            html=root/'a.html';html.write_text('<h1>资料标题</h1><script>hidden()</script><p>可搜索内容</p>')
            self.assertIn('可搜索内容',extract(html)['text']);self.assertNotIn('hidden',extract(html)['text'])
            from pypdf import PdfWriter
            from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
            writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
            stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 200 Td (Searchable PDF) Tj ET');page[NameObject('/Contents')]=stream
            pdf=root/'a.pdf'
            with pdf.open('wb') as f:writer.write(f)
            self.assertIn('Searchable PDF',extract(pdf)['text'])
            legacy=root/'a.ppt';legacy.write_bytes(b'opaque legacy binary')
            self.assertEqual(extract(legacy)['status'],'not_extracted')
