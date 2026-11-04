"""Read-through pages, local updates, source provenance and private boundaries."""
import io
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.status_pages import cache_root, database
from cosmesh.threads import Workbench, initialize
from cosmesh.thread_lifecycle import merge, placement
from test_threads import ThreadFixture


class PageMixin:
    def page(self, *args, root=None):
        with redirect_stdout(io.StringIO()) as output:
            code = main(['thread', 'page', '--root', str(root or self.w.base.root), '--json', *args])
        value = json.loads(output.getvalue())
        self.assertEqual(code, 0, value)
        return value['data']


class StatusPagesTests(PageMixin, unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
        self.addCleanup(tmp.cleanup);self.root = Path(tmp.name)
        env = patch.dict(os.environ, COSMESH_PAGE_CACHE=str(self.root/'cache'), COSMESH_PAGE_BACKGROUND='0')
        env.start();self.addCleanup(env.stop)
        initialize(self.root/'work', 'alice');self.w = Workbench(self.root/'work')
        with self.w.base.locked():
            self.w.load();self.tid = self.w.create('冷气体研究', '研究冷气体如何形成恒星。比较模型与观测，明确适用条件。')['id'];self.w.load()
            self.old = self.w.append(self.tid, '早期约束：输入必须保留原始版本。')['id'];self.w.load()
            self.w.bind(self.tid, 'test-agent', str(self.root), 'private-session')

    def test_overall_at_every_level_warm_read_no_workbench_or_network_and_no_source_change(self):
        w = self.w;head, client = w.base.head, w.path.read_bytes()
        first = self.page();self.assertFalse(first['cache']['hit'])
        root = cache_root(w)
        self.assertTrue(list((root/'pages').rglob('*.md')))
        self.assertEqual((root/'index.sqlite3').stat().st_mode & 0o777, 0o600)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        with patch.object(Workbench, 'load', side_effect=AssertionError('No full load on hit')), \
             patch('cosmesh.spaces.rpc', side_effect=AssertionError('No network')), \
             patch('cosmesh.thread_overview.directory_exists', side_effect=AssertionError('No directory probe')):
            for level in ('brief', 'status', 'index'):
                page = self.page(self.tid, '--level', level, '--markdown')
                self.assertTrue(page['cache']['hit'])
                self.assertIn('比较模型与观测', page['thread']['overall']['text'])
                self.assertIn('## 整体介绍', page['markdown'])
            user = self.page('--continue', self.tid)
            self.assertIn('比较模型与观测', user['threads'][0]['overall']['text'])
            self.assertEqual(user['continuation']['thread']['id'], self.tid)
            self.assertEqual(self.page('--level','status')['overview']['coverage'],'selected_page')
            self.assertIn(self.tid,self.page('--level','index')['index'][0]['details'])
        self.assertEqual(w.base.head, head);self.assertEqual(w.path.read_bytes(), client)
        self.assertEqual(w.base.git('status', '--porcelain'), '')

    def test_prepared_overview_skips_git_checks_and_shows_pending_update(self):
        self.page('--ready')
        from cosmesh.store import atomic
        root=cache_root(self.w)
        with self.w.base.locked():
            self.w.load();self.w.append(self.tid,'新的进展已登记，成品后台准备。')
        atomic(root/'queued.json','{"at":"new-update"}')
        with patch.dict(os.environ,COSMESH_PAGE_BACKGROUND='1'), \
             patch.object(Workbench,'load',side_effect=AssertionError('no request-time load')), \
             patch('cosmesh.status_pages.stamp',side_effect=AssertionError('no Git revision scan')):
            old=self.page('--ready')
        self.assertTrue(old['freshness']['updating'])
        self.assertIn('上次成品',old['document'])
        self.page('--ready','--refresh')
        with patch.dict(os.environ,COSMESH_PAGE_BACKGROUND='1'), \
             patch.object(Workbench,'load',side_effect=AssertionError('no load')), \
             patch('cosmesh.status_pages.stamp',side_effect=AssertionError('no Git scan')):
            current=self.page('--ready')
        self.assertFalse(current['freshness']['updating'])
        self.assertIn('新的进展',current['document'])

    def test_authored_summary_is_human_progress_but_not_full_original_body(self):
        from cosmesh.thread_chat import with_summary
        with self.w.base.locked():
            self.w.load();self.w.append(self.tid,with_summary('仅原文可见的具体参数。','已经完成核对。下一步整理报告。'))
        overview=self.page('--ready')
        self.assertIn('已经完成核对',overview['document'])
        self.assertNotIn('## 交互摘要',overview['document'])
        ready=self.page('--continue',self.tid,'--ready')
        self.assertFalse(ready['record_preview']['body_complete'])
        self.assertEqual(ready['record_preview']['records'][0]['basis'],'interaction_summary')
        self.assertNotIn('仅原文可见',ready['record_preview']['records'][0]['text'])

    def test_upgrade_from_older_cache_prepares_once_without_manual_refresh(self):
        self.page('--ready')
        from cosmesh.status_pages import put_meta
        root=cache_root(self.w)
        with self.w.base.locked():
            self.w.load();self.w.append(self.tid,'升级后应自动读到的合成进展。')
        with database(root) as con:
            put_meta(con,'cache_format',5)
            put_meta(con,'tokens',{'all':'old','base':'old','mesh':'old'})
            con.execute("DELETE FROM pages WHERE key LIKE '%/ready'");con.commit()
        with patch.dict(os.environ,COSMESH_PAGE_BACKGROUND='1'):
            upgraded=self.page('--ready')
        self.assertIn('升级后应自动读到',upgraded['document'])
        with patch.dict(os.environ,COSMESH_PAGE_BACKGROUND='1'), \
             patch.object(Workbench,'load',side_effect=AssertionError('upgrade must only rebuild once')), \
             patch('cosmesh.status_pages.stamp',side_effect=AssertionError('warm read must not scan Git')):
            self.assertTrue(self.page('--ready')['cache_hit'])

    def test_incomplete_preparation_never_serves_previous_ready_product(self):
        self.page('--ready')
        from cosmesh.status_pages import put_meta,stamp
        with self.w.base.locked():
            self.w.load();self.w.append(self.tid,'准备中断后的新进展。')
        with database(cache_root(self.w)) as con:
            put_meta(con,'tokens',stamp(self.w));con.commit()
        with patch.dict(os.environ,COSMESH_PAGE_BACKGROUND='1'):
            current=self.page('--ready')
        self.assertIn('准备中断后的新进展',current['document'])

    def test_summary_provenance_stale_next_step_and_local_rebuild_only(self):
        w = self.w
        with w.base.locked():
            w.load();other = w.create('另一个工作', '其他研究的整体介绍。')['id'];w.load()
            parent = w.ref(w.find(self.tid))
            sid = w.append(self.tid, '## 整体介绍\n研究冷气体的转化机制。目标是解释观测。\n\n## 下一步\n先核验误差。',
                           'summary', refs=[parent, w.ref(w.find(self.old))])['id'];w.load()
        self.page()
        before = self.page(other)['cache']['page_version']
        p = self.page(self.tid);self.assertEqual(p['thread']['overall']['source']['id'], sid)
        self.assertEqual(p['thread']['next_step'], '先核验误差。')
        with w.base.locked():
            w.load();w.append(self.tid, '误差已核验，准备画图。');w.load()
        p = self.page(self.tid, '--level', 'status')
        self.assertEqual(p['cache']['update']['threads_rebuilt'], [self.tid])
        self.assertEqual(p['thread']['summary_state'], 'pending')
        self.assertIsNone(p['thread']['next_step'])
        self.assertIn('冷气体的转化机制', p['thread']['overall']['text'])
        self.assertTrue(p['status']['summary']['pending'])
        self.assertEqual(self.page(other)['cache']['page_version'], before)

    def test_ready_document_uses_prepared_parts_once_and_keeps_sources(self):
        w=self.w
        with w.base.locked():
            w.load()
            for i in range(3):w.create('其他工作'+str(i),'其他工作的整体目标。明确适用范围。');w.load()
        full=self.page('--continue',self.tid)
        with patch.object(Workbench,'load',side_effect=AssertionError('No repeated source load')):
            ready=self.page('--continue',self.tid,'--ready')
        self.assertEqual(ready['format'],'status.page.ready.v1')
        self.assertEqual(ready['document'].count('比较模型与观测'),1)
        self.assertEqual(ready['source_index'][ready['focus']]['id'],self.tid)
        self.assertEqual(len(ready['rows']),4)
        self.assertTrue(ready['record_preview']['body_complete'])
        self.assertEqual(ready['record_preview']['total'],1)
        self.assertIn(self.old,[x['id'] for x in ready['source_index']])
        self.assertNotIn('threads',ready);self.assertNotIn('status',ready)
        self.assertLess(len(json.dumps(ready)),len(json.dumps(full))*.8)
        root=cache_root(w)
        with database(root) as con:
            body=con.execute("SELECT body FROM pages WHERE key='all/user-alice/brief'").fetchone()[0]
            more=con.execute('SELECT body FROM pages WHERE key=?',('all/'+self.tid+'/followup',)).fetchone()[0]
        self.assertTrue(ready['document'].startswith(body+'\n'+more))
        self.assertEqual(hashlib.sha256(ready['document'].encode()).hexdigest(),ready['document_version'])

    def test_ready_page_markers_and_stale_summary_are_preserved(self):
        w=self.w
        with w.base.locked():
            w.load();w.append(self.tid,'## 整体介绍\n讨论冷气体的变化。目标是核验模型。\n\n## 下一步\n旧建议。','summary');w.load()
            w.append(self.tid,'新进展，原建议需要重新考虑。');w.load()
            w.create('另一个工作','另一个目标。');w.load()
        ready=self.page('--continue',self.tid,'--limit','1','--ready')
        self.assertTrue(ready['truncated']);self.assertIn('本页未全部展示',ready['document'])
        self.assertIn('仅供历史参考',ready['document'])
        self.assertIn('下一步待重新整理',ready['document'])
        self.assertTrue(ready['record_preview']['body_complete'])
        self.assertEqual(hashlib.sha256(ready['document'].encode()).hexdigest(),ready['document_version'])
        self.assertNotIn('cache',ready)

    def test_ready_markdown_escapes_table_cells_and_marks_missing_execution(self):
        with self.w.base.locked():
            self.w.load();self.w.create('目标|检查','输入含 <标记> 与竖线 | 。\n换行仍是文字。');self.w.load()
        ready=self.page('--ready')
        self.assertIn('目标\\|检查',ready['document']);self.assertIn('&lt;标记&gt;',ready['document'])
        self.assertIn('未关联项目／工作区',ready['document'])
        self.assertNotIn('可接续会话',ready['document'])

    def test_corrections_requests_backlinks_and_paginated_detail_index(self):
        w = self.w
        with w.base.locked():
            w.load();corrected = w.append(self.tid, '更正输入约束，保留原件。', 'correction', supersedes=w.ref(w.find(self.old)))['id'];w.load()
            req = w.append(self.tid, '核验单位', 'request', recipient='alice')['id'];w.load()
            w.append(self.tid, '已回应', 'reply', request=w.ref(w.find(req)));w.load()
            other = w.create('结果使用方', '复用已有输入检查。')['id'];w.load()
            result = w.append(other, '复用更正', 'result', refs=[w.ref(w.find(corrected))])['id'];w.load()
        p = self.page(self.tid, '--level', 'index', '--limit', '2')
        self.assertTrue(p['truncated']);self.assertEqual(len(p['entries']), 2)
        self.assertEqual(p['entries'][0]['superseded_by'][0]['id'], corrected)
        rest = self.page(self.tid, '--level', 'index', '--limit', '2', '--offset', '2')
        self.assertEqual(rest['total'], 4)
        self.assertEqual(len(rest['entries']), 2)
        status = self.page(self.tid, '--level', 'status')['status']
        self.assertEqual(status['requests'][0]['id'], req)
        self.assertIn(result, json.dumps(status['backlinks']))
        self.assertNotIn(self.old, [x['id'] for x in self.page(self.tid)['thread']['progress']])

    def test_lifecycle_merge_definition_edit_and_empty_intro(self):
        w = self.w
        self.page()
        with w.base.locked():
            w.load();target = w.create('新方向', '')['id'];w.load();merge(w, [self.tid], target);w.load()
        old = self.page(self.tid)
        self.assertEqual(old['thread']['lifecycle'], 'merged')
        self.assertEqual(old['thread']['merged_into']['id'], target)
        self.assertEqual(self.page()['total'], 1)
        self.assertEqual(self.page('--all')['total'], 2)
        self.assertTrue(self.page(target)['thread']['overall']['missing'])
        with w.base.locked():
            w.load();w.edit(target, w.ref(w.find(target))['version'], body='## 整体介绍\n新的研究目标。覆盖新的适用范围。');w.load()
        self.assertIn('新的研究目标', self.page(target)['thread']['overall']['text'])

    def test_corrupt_cache_recovery_and_source_permissions(self):
        self.page();root = cache_root(self.w)
        (root/'index.sqlite3').write_bytes(b'not a database')
        recovered = self.page()
        self.assertTrue(recovered['cache']['recovered'])
        self.assertTrue(list(root.glob('index-damaged-*')))
        (root/'index.sqlite3').chmod(0o644)
        with redirect_stdout(io.StringIO()) as output:
            code = main(['thread','page','--root',str(self.w.base.root),'--json'])
        self.assertNotEqual(code, 0);self.assertEqual(json.loads(output.getvalue())['error']['code'], 'AUTH_DENIED')

    def test_locked_index_is_not_quarantined_and_external_correction_invalidates_dependent(self):
        w=self.w
        with w.base.locked():
            w.load();other=w.create('来源工作','提供输入检查。')['id'];w.load()
            original=w.append(other,'原始检查结果','result')['id'];w.load()
            w.append(self.tid,'## 整体介绍\n核验输入后做误差分析。范围是合成观测。\n\n## 下一步\n使用检查结果。',
                     'summary',refs=[w.ref(w.find(original))]);w.load()
        self.page();root=cache_root(w)
        locked=sqlite3.connect(str(root/'index.sqlite3'));locked.execute('BEGIN EXCLUSIVE')
        try:
            with redirect_stdout(io.StringIO()) as output:
                code=main(['thread','page','--root',str(w.base.root),'--json'])
            self.assertEqual(code,4);self.assertEqual(json.loads(output.getvalue())['error']['code'],'STORE_BUSY')
            self.assertFalse(list(root.glob('index-damaged-*')))
        finally:locked.rollback();locked.close()
        with w.base.locked():
            w.load();new=w.append(other,'更正原始检查','correction',supersedes=w.ref(w.find(original)))['id'];w.load()
        page=self.page(self.tid,'--level','status')
        self.assertIn(self.tid,page['cache']['update']['threads_rebuilt'])
        self.assertTrue(page['status']['summary']['pending'])
        self.assertIn(new,json.dumps(page['status']['dependencies']))

    def test_cli_write_updates_in_real_background_without_read_trigger(self):
        self.page();root = cache_root(self.w)
        with patch.dict(os.environ, COSMESH_PAGE_BACKGROUND='1'):
            with redirect_stdout(io.StringIO()) as output:
                code = main(['thread','note',self.tid,'--body','后台更新到这一条','--root',str(self.w.base.root),'--json'])
            self.assertEqual(code, 0, output.getvalue())
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                with database(root) as con:
                    row = con.execute('SELECT card FROM threads WHERE id=?',(self.tid,)).fetchone()
                    if '后台更新到这一条' in row['card']: break
                time.sleep(.1)
            else: self.fail('Background worker did not update the materialized card.')
            # Also let the supervisor close before removing the fixture.
            while time.monotonic() < deadline:
                if (root/'applied.json').exists() and (root/'applied.json').read_bytes() == (root/'queued.json').read_bytes(): break
                time.sleep(.05)
        self.assertTrue(self.page()['cache']['hit'])


class SharedStatusPagesTests(PageMixin, ThreadFixture):
    def setUp(self):
        super().setUp();self.w = self.wbs['alice']
        env = patch.dict(os.environ, COSMESH_PAGE_CACHE=str(self.root/'page-cache'), COSMESH_PAGE_BACKGROUND='0')
        env.start();self.addCleanup(env.stop)

    def test_mesh_excludes_base_private_execution_backlinks_and_error_paths(self):
        tid = self.shared('alice', '共享工作', '共享整体介绍。目标是复用公开成果。')
        bob = self.shared('bob', '他人工作', '他人的共享介绍。')
        with self.use('alice') as w:
            w.synchronize(publish=False);private = w.create('私人标题', '私人整体介绍')['id'];w.load()
            w.append(private,'私人回链内容',refs=[w.ref(w.find(tid))]);w.load()
            w.bind(tid,'private-agent',str(self.root),'private-session')
        shared = self.page(tid,'--scope','mesh','--level','status','--markdown')
        text = json.dumps(shared,ensure_ascii=False)
        for secret in (private,'私人标题','私人整体介绍','私人回链内容','private-agent','private-session',str(self.root)):
            self.assertNotIn(secret,text)
        self.assertIn('private-session',json.dumps(self.page(tid,'--level','status')))
        ready=self.page(tid,'--scope','mesh','--level','status','--ready')
        text=json.dumps(ready,ensure_ascii=False)
        for secret in (private,'私人标题','私人整体介绍','私人回链内容','private-agent','private-session',str(self.root)):
            self.assertNotIn(secret,text)
        self.assertNotIn('私人执行登记',ready['document'])
        self.assertEqual(self.page('--scope','mesh','--user','bob')['threads'][0]['id'],bob)
        self.assertEqual(self.page('--scope','base')['threads'][0]['id'],private)
        with redirect_stdout(io.StringIO()) as output:
            code=main(['thread','page',private,'--scope','mesh','--root',str(self.w.base.root),'--json'])
        self.assertNotEqual(code,0);self.assertNotIn(str(self.root),output.getvalue());self.assertNotIn(private,output.getvalue())

    def test_other_user_real_names_ambiguity_and_shared_dependency_updates(self):
        tid=self.shared('bob','待核验','公开目标。')
        with self.use('alice') as w:
            w.synchronize(publish=False)
            from cosmesh.store import atomic
            path=w.base.path('.cosmesh/knowledge/snapshot.json');path.parent.mkdir(exist_ok=True)
            users=[{'id':'person-one','account':'alice','name':'同名'}, {'id':'person-two','account':'bob','name':'同名'}]
            atomic(path,json.dumps({'users':users,'versions':[],'heads':{},'events':[],'contributions':[],'archives':[]}))
        self.page(tid,'--scope','mesh')
        with redirect_stdout(io.StringIO()) as output:
            code=main(['thread','page','--user','同名','--scope','mesh','--root',str(self.w.base.root),'--json'])
        self.assertNotEqual(code,0);self.assertEqual(json.loads(output.getvalue())['error']['code'],'AMBIGUOUS_USER')
        with self.use('bob') as w:
            w.append(tid,'远端有新进展');w.load();self.assertTrue(w.synchronize()['synchronized'])
        with self.use('alice') as w: w.synchronize(publish=False)
        p=self.page(tid,'--scope','mesh')
        self.assertIn('远端有新进展',p['thread']['progress'][0]['excerpt']['text'])

    def test_private_local_alias_does_not_select_another_mesh_account(self):
        alice=self.shared('alice','本人共享','Alice 的公开目标。')
        bob=self.shared('bob','他人共享','Bob 的公开目标。')
        local=self.workbench('alice','alias-workbench',local_user='bob')
        with self.use(local) as w:
            own=w.create('本人私人','本地身份与远端身份不同。')['id'];w.load()
            from cosmesh.store import atomic
            path=w.base.path('.cosmesh/knowledge/snapshot.json');path.parent.mkdir(exist_ok=True)
            atomic(path,json.dumps({'users':[{'id':'person-a','account':'alice','name':'Alice A'},
                                            {'id':'person-b','account':'bob','name':'Bob B'}],
                                    'versions':[],'heads':{},'events':[],'contributions':[],'archives':[]}))
        p=self.page(root=local.base.root)
        self.assertEqual({c['id'] for c in p['threads']},{alice,own})
        self.assertEqual(next(c for c in p['threads'] if c['id']==own)['author_name'],'bob')
        other=self.page('--user','bob','--scope','mesh',root=local.base.root)
        self.assertEqual([c['id'] for c in other['threads']],[bob])

