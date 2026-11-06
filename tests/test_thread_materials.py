"""Real Hub upload, fixed opening, limits, retry and private-boundary workflows."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.knowledge import Knowledge
from cosmesh.knowledge_protocol import MAX_FILE,MAX_TOTAL
from cosmesh.material_links import link,parse,html_ready
from cosmesh.thread_materials import preflight,rewrite
from test_threads import ThreadFixture


class MaterialPreflightTests(unittest.TestCase):
    def test_size_suffix_total_and_html_dependencies_without_reading_data(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);files=[]
            for i in range(6):
                p=root/(str(i)+'.txt')
                p.write_bytes(b'a'*MAX_FILE)
                files.append({'path':str(p)})
            big=root/'too-big.pdf'
            with big.open('wb') as h:h.truncate(MAX_FILE+1)
            files += [{'path':str(big)},{'path':str(root/'cube.fits')},{'path':str(root/'data.zip')}]
            accepted,skipped,total=preflight(files)
            self.assertEqual(len(accepted),5);self.assertEqual(total,MAX_TOTAL)
            self.assertEqual([f['reason'] for f in skipped],['share_exceeds_100_MiB','file_exceeds_20_MiB','data_or_archive_suffix','data_or_archive_suffix'])
            self.assertTrue(html_ready(b'<html><style>body {color: red}</style><img src="data:image/png;base64,AA=="></html>'))
            for html in (b'<img src="local.png">',b'<script src="https://example.org/app.js"></script>',b'<style>a{background:url(local.png)}</style>'):
                self.assertFalse(html_ready(html))
    def test_fixed_reference_and_no_private_location_in_shared_rewrite(self):
        ref={'repo':'library','id':'K-12345678-1234-4234-8234-123456789012','version':'a'*64};uri=link(ref,'中文报告.html')
        self.assertEqual(parse(uri),(ref,'中文报告.html'))
        with self.assertRaises(Error):parse(uri.rsplit('/',1)[0]+'/%2e%2e')
        text='## 来源\n[说明](README.md) 与 `README.md`\n## 执行环境\n目录：/private/example/private\n## 下一步\n核验输入。'
        result=rewrite(text,{'README.md':(uri,'中文报告.html')})
        self.assertNotIn('/private/example/',result);self.assertIn('## 下一步',result);self.assertIn('('+uri+')',result)


class SharedMaterialTests(ThreadFixture):
    def test_utf8_sources_attach_extract_and_open_with_fixed_references(self):
        tid=self.shared('alice','源码交付','合成测试资料')
        contents={'table.tex':'\\begin{tabular}{c} 合成表格 \\end{tabular}',
                  'analysis.py':'print("synthetic")\n', 'result.csv':'name,value\na,1\n',
                  'settings.custom':'参数 = 合成\n', 'README':'没有后缀的说明\n',
                  'run.sh':'#!/bin/sh\necho synthetic\n'}
        args=[]
        for name,body in contents.items():
            path=self.root/name;path.write_text(body,encoding='utf-8')
            if name=='run.sh':path.chmod(0o700)
            args.extend(['--attach',str(path)])
        for name in ('data.zip','cube.fits','program.exe','binary.py','invalid.custom'):
            path=self.root/name
            path.write_bytes(b'\xff' if name=='invalid.custom' else b'abc\x00def' if name=='binary.py' else b'synthetic')
            args.extend(['--attach',str(path)])
        materials=self.cli('alice','thread','share',tid,*args,'--sync')['result']['materials']
        self.assertEqual(materials['state'],'partial')
        self.assertEqual({f['name'] for f in materials['files']},set(contents))
        reasons={f['name']:f['reason'] for f in materials['skipped']}
        self.assertEqual(reasons,{'data.zip':'data_or_archive_suffix','cube.fits':'data_or_archive_suffix',
                                 'program.exe':'executable_suffix','binary.py':'binary_content','invalid.custom':'not_utf8_text'})
        self.update('bob')
        with patch.dict(os.environ,COSMESH_OPEN_CACHE=str(self.root/'opened')):
            for f in materials['files']:
                opened=self.cli('bob','library','open',f['reference'])
                self.assertTrue(opened['verified'])
                self.assertEqual(Path(opened['path']).read_text(),contents[f['name']])
                self.assertEqual(Path(opened['path']).stat().st_mode & 0o111,0)
        ref,_=parse(materials['files'][0]['reference'])
        with self.use('bob') as w:
            parts=Knowledge(w).text(ref['id'])['parts']
            self.assertEqual({p['file']:p['text'] for p in parts},contents)

    def test_library_and_hub_apply_content_rules(self):
        source=self.root/'source.tex';source.write_text('合成 LaTeX',encoding='utf-8')
        added=self.cli('alice','library','add','--title','Source','--file',str(source),'--sync')
        self.assertTrue(added['publication']['synchronized'])
        binary=self.root/'binary.txt';binary.write_bytes(b'\x7fELF\x00')
        with self.use('alice') as w:
            kb=Knowledge(w)
            with self.assertRaises(Error):kb.import_files([str(binary)])
            files=kb.import_files([str(source)])
            files[0]['name']='program.exe'
            from uuid import uuid4
            value={'id':'K-'+str(uuid4()),'title':'Rejected','files':files}
            with self.assertRaises(Error) as caught:
                self.hub.community().apply('alice','resource_add',value,'reject-executable',self.hub)
            self.assertIn('executable_suffix',str(caught.exception))

    def setUp(self):
        super().setUp();self.hub.initialize_community(self.root/'knowledge-library')
        for user,name in {'alice':'张三','bob':'李四','carol':'王五'}.items():
            self.hub.spaces({'method':'community_name','name':name,'key':'name-'+user},user)
            with self.use(user) as w:w.discover()
    def cli(self,user,*args):
        with redirect_stdout(io.StringIO()) as out:code=main([*args,'--root',str(self.wbs[user].base.root),'--json'])
        value=json.loads(out.getvalue());self.assertEqual(code,0,value);return value['data']
    def test_selected_upload_fixed_open_backlinks_and_private_original(self):
        with self.use('alice') as w:
            tid=w.create('共享报告','## 说明\n请读 README.md、report.html 和 summary.json。\n\n## 执行环境\n目录：/private/example/private-project\n')['id'];w.load()
            original=w.base.head;w.bind(tid,'codex',str(self.root),'private-session')
        generation=self.root/'generation.json';generation.write_text(json.dumps({'tool':'test-agent','instruction':'从 '+str(self.root)+' 选取资料。'}))
        readme=self.root/'README.md';readme.write_text('项目说明：固定输入与适用范围。[报告](docs/report.html)')
        report=self.root/'report.html';report.write_text('<!doctype html><meta charset="utf-8"><h1>合成验收报告</h1><p>结果为测试数据。</p>')
        summary=self.root/'summary.json';summary.write_text('{"synthetic":true,"value":3}')
        # Unrelated drafts must remain local even when selected materials are synchronized.
        draft=self.cli('alice','library','add','--title','unrelated-private-draft','--body','keep local')
        shared=self.cli('alice','thread','share',tid,'--attach',str(readme),'--attach',str(report),'--attach',str(summary),'--generation-file',str(generation),'--sync','--idempotency-key','share-once')
        self.assertTrue(shared['publication']['synchronized']);data=shared['result']['materials']
        self.assertEqual(data['state'],'ready');self.assertEqual(len(data['files']),3)
        again=self.cli('alice','thread','share',tid,'--attach',str(readme),'--attach',str(report),'--attach',str(summary),'--generation-file',str(generation),'--sync','--idempotency-key','share-once')
        self.assertTrue(again['replayed'])
        self.update('bob')
        with self.use('alice') as w:
            self.assertEqual(w.base.head,original)
            published=w.stores[w.client['own_repo']].snapshot()[1]
            self.assertNotIn('private-session',str(published));self.assertNotIn('/private/example/',str(published));self.assertNotIn(str(self.root),str(published))
            pending=Knowledge(w).outbox();self.assertEqual(len(list(pending.glob('*.json'))),1)
        uri=next(x['reference'] for x in data['files'] if x['name']=='report.html')
        with patch.dict(os.environ,COSMESH_OPEN_CACHE=str(self.root/'opened')):
            opened=self.cli('bob','library','open',uri)
            self.assertTrue(opened['verified']);self.assertTrue(opened['html_self_contained'])
            self.assertEqual(Path(opened['path']).read_bytes(),report.read_bytes())
            self.assertEqual(Path(opened['path']).stat().st_mode&0o777,0o600)
            md=next(x['reference'] for x in data['files'] if x['name']=='README.md')
            docs=self.cli('bob','library','open',md)
            self.assertFalse(docs['unresolved_links'])
            self.assertEqual((Path(docs['path']).parent/'docs/report.html').read_bytes(),report.read_bytes())
            with patch('cosmesh.spaces.rpc',side_effect=AssertionError('No offline network')):
                cached=self.cli('bob','library','open',uri,'--offline');self.assertEqual(cached['path'],opened['path'])
        ref,_=parse(uri);back=self.cli('bob','library','backlinks',ref['id']);self.assertIn(tid,json.dumps(back))
        with self.use('bob') as w:self.assertIn('synthetic',str(Knowledge(w).text(ref['id'])))
        with self.use('bob') as w:
            k=Knowledge(w);k.refresh();self.assertNotIn(draft['id'],k.data['heads'])
    def test_default_brief_never_reads_or_uploads_files_and_opt_in_skips_are_partial(self):
        with self.use('alice') as w:
            tid=w.create('待补资料','说明在 README.md，数据在 cube.fits。')['id'];w.load();head=w.base.head
        self.cli('alice','library','add','--title','Unrelated pending document','--body','Must stay local.')
        with patch('cosmesh.thread_materials.selection',side_effect=AssertionError('Default must not select files')), \
             patch.object(Knowledge,'import_files',side_effect=AssertionError('Default must not read files')), \
             patch.object(Knowledge,'upload',side_effect=AssertionError('Default must not upload files')):
            brief=self.cli('alice','thread','share',tid,'--sync')
        self.assertEqual(brief['result']['materials']['state'],'not_requested')
        self.assertEqual(brief['result']['materials']['files'],[])
        with self.use('alice') as w:
            k=Knowledge(w);k.refresh();self.assertFalse(k.data['heads'])
            self.assertIn('共享范围：工作简报',w.find(tid)['doc']['body'])
        readme=self.root/'README.md';readme.write_text('项目说明')
        result=self.cli('alice','thread','share',tid,'--attach',str(readme),'--attach',str(self.root/'cube.fits'),'--sync')['result']['materials']
        self.assertEqual(result['state'],'partial');self.assertEqual(result['skipped'][0]['reason'],'data_or_archive_suffix')
        self.assertEqual(self.wbs['alice'].base.head,head)
    def test_existing_shared_thread_reuse_and_new_versions_preserve_fixed_files(self):
        tid=self.shared('alice','补充已共享资料','现有工作背景。')
        p=self.root/'notes.md';p.write_text('旧说明')
        first=self.cli('alice','thread','share',tid,'--attach',str(p),'--sync')['result']['materials']
        old=first['files'][0]['reference'];self.update('bob')
        with patch.dict(os.environ,COSMESH_OPEN_CACHE=str(self.root/'opened')):
            a=self.cli('bob','library','open',old);self.assertEqual(Path(a['path']).read_text(),'旧说明')
            p.write_text('新版说明')
            second=self.cli('alice','thread','share',tid,'--attach',str(p),'--sync')['result']['materials']
            self.assertNotEqual(second['files'][0]['reference'],old)
            self.assertEqual(parse(second['files'][0]['reference'])[0]['id'],parse(old)[0]['id'])
            self.update('bob');a=self.cli('bob','library','open',old);self.assertEqual(Path(a['path']).read_text(),'旧说明')
    def test_dry_run_no_upload_no_source_changes_and_code_is_pinned(self):
        with self.use('alice') as w:tid=w.create('只读预检','背景说明。')['id'];w.load();head=w.base.head
        with redirect_stdout(io.StringIO()) as out:code=main(['thread','share',tid,'--root',str(self.wbs['alice'].base.root),'--dry-run','--attach',str(self.root/'data.zip'),'--json'])
        self.assertEqual(code,0,out.getvalue());self.assertEqual(json.loads(out.getvalue())['data']['materials']['files'],[])
        self.assertEqual(self.wbs['alice'].base.head,head)
        good=self.cli('alice','thread','share',tid,'--git','https://example.org/group/code','--commit','a'*40,'--code-path','scripts/tasks','--sync')['result']['materials']
        self.assertEqual(good['code']['commit'],'a'*40)

    def test_interrupted_publication_retries_without_duplicate_index(self):
        from cosmesh.threads import Workbench
        with self.use('alice') as w:tid=w.create('重试上传','读取 notes.md。')['id'];w.load()
        p=self.root/'notes.md';p.write_text('测试重试资料')
        args=['thread','share',tid,'--attach',str(p),'--sync','--idempotency-key','retry-selected']
        with patch.object(Workbench,'synchronize',return_value={'synchronized':False}):
            with redirect_stdout(io.StringIO()) as out:code=main([*args,'--root',str(self.wbs['alice'].base.root),'--json'])
            self.assertNotEqual(code,0);self.assertEqual(json.loads(out.getvalue())['error']['code'],'MATERIAL_SHARE_PENDING')
        value=self.cli('alice',*args);self.assertTrue(value['publication']['synchronized'])
        with self.use('alice') as w:
            self.assertEqual(len([r for r in w.records(tid) if '## 共享资料' in r['doc']['body']]),1)
    def test_unrelated_thread_draft_blocks_before_material_upload(self):
        with self.use('alice') as w:
            other=w.create('别的待发布工作','暂存共享草稿。')['id'];w.load();w.share(other);w.load()
            tid=w.create('当前工作','只共享当前工作。')['id'];w.load()
        p=self.root/'notes.md';p.write_text('当前资料')
        with redirect_stdout(io.StringIO()) as out:code=main(['thread','share',tid,'--attach',str(p),'--sync','--root',str(self.wbs['alice'].base.root),'--json'])
        self.assertNotEqual(code,0);self.assertEqual(json.loads(out.getvalue())['error']['code'],'UNRELATED_PENDING')
        with self.use('alice') as w:
            k=Knowledge(w);k.refresh();self.assertFalse(k.data['heads'])
