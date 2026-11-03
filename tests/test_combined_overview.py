"""One-read composition preserves context, corrections, lifecycle and private boundaries."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.thread_lifecycle import continuation, merge
from cosmesh.thread_overview import overview, with_continuation
from cosmesh.threads import Workbench, initialize
from test_threads import ThreadFixture


def expanded(value, sources):
    if isinstance(value, list): return [expanded(item, sources) for item in value]
    if not isinstance(value, dict): return value
    return {key: sources[item] if key == 'source' and isinstance(item, int) else expanded(item, sources)
            for key, item in value.items()}


def pointed(value, path):
    for part in path.strip('/').split('/'):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class CombinedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        initialize(self.root/'work', 'alice'); self.w = Workbench(self.root/'work')

    def test_exact_context_and_preview_dedup_with_old_constraints_and_corrections(self):
        w = self.w
        with w.base.locked():
            w.load(); tid = w.create('继续分析', '工作目标' * 50)['id']; w.load()
            old = w.append(tid, '长期约束：必须核验固定输入；这条早期决定需要保留。')['id']; w.load()
            w.append(tid, '更正后的长期约束', 'correction', supersedes=w.ref(w.find(old))); w.load()
            request = w.append(tid, '请核验输入', 'request', recipient='alice')['id']; w.load()
            w.append(tid, '收到，保留原请求', 'reply', request=w.ref(w.find(request))); w.load()
            other = w.create('原始依据')['id']; w.load(); evidence = w.append(other, '原始结果', 'result')['id']; w.load()
            w.append(tid, '当前结果，引用实际原始依据。', 'result', refs=[w.ref(w.find(evidence))]); w.load()
            w.bind(tid, 'test-agent', str(self.root), 'private-session')
            expected = continuation(w, tid); expected.pop('sources')
            view = overview(w, compact=True)
            original = json.dumps(view)
            combined = with_continuation(w, view, tid)
            self.assertEqual(expanded(combined['continuation'], combined['source_index']), expected)
            self.assertEqual(len(combined['continuation']['requests']), 1)
            self.assertEqual(json.dumps(view), original)
            row = next(row for row in combined['threads'] if row['id'] == tid)
            self.assertEqual(pointed(combined, row['purpose']['from']), '工作目标' * 50)
            recent = row['progress']['recent'][0]
            self.assertEqual(pointed(combined, recent['text_from']), '当前结果，引用实际原始依据。')
            self.assertNotIn('text', recent)
            self.assertIn('长期约束：必须核验固定输入', json.dumps(combined, ensure_ascii=False))
            self.assertEqual(len(combined['source_index']), len({tuple(sorted(ref.items())) for ref in combined['source_index']}))

    def test_one_load_no_network_or_directory_probe_and_no_source_changes(self):
        w = self.w
        with w.base.locked():
            w.load(); tid = w.create('单次读取')['id']; w.load(); w.append(tid, '已有进展'); w.load()
            w.bind(tid, 'test-agent', str(self.root), 'private-session')
            head, client = w.base.head, w.path.read_bytes()
        original = Workbench.load; loads = []
        def load(instance):
            loads.append(instance.base.root); return original(instance)
        with patch.object(Workbench, 'load', load), patch('cosmesh.spaces.rpc', side_effect=AssertionError('Unexpected network')), patch('cosmesh.thread_overview.directory_exists', side_effect=AssertionError('Unexpected probe')):
            with redirect_stdout(io.StringIO()) as output:
                code = main(['thread', 'overview', '--root', str(w.base.root), '--mine', '--compact', '--continue', tid, '--json'])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0); self.assertEqual(len(loads), 1)
        self.assertEqual(result['data']['format'], 'thread.overview.continue.v1')
        self.assertEqual(result['data']['continuation']['thread']['id'], tid)
        self.assertTrue(result['data']['private'])
        self.assertEqual(w.base.head, head); self.assertEqual(w.path.read_bytes(), client)

    def test_merged_target_and_target_outside_overview_limit(self):
        w = self.w
        with w.base.locked():
            w.load(); target = w.create('目标工作')['id']; w.load()
            old = w.create('原入口')['id']; w.load(); w.bind(old, 'test-agent', str(self.root), 'old-session')
            merge(w, [old], target); w.load()
            latest = w.create('另一个最近工作')['id']; w.load()
            view = overview(w, compact=True, limit=1)
            self.assertEqual(view['threads'][0]['id'], latest)
            combined = with_continuation(w, view, old)
            self.assertEqual(combined['continuation']['thread']['id'], target)
            self.assertEqual(combined['continuation']['continued_from'][0]['id'], old)
            self.assertEqual(combined['continuation']['local_execution'][0]['session_id'], 'old-session')
            self.assertTrue(combined['truncated'])

    def test_combined_private_view_rejected_before_mesh_loading(self):
        for extra in (['--compact', '--scope', 'mesh'], []):
            with redirect_stdout(io.StringIO()) as output:
                code = main(['thread', 'overview', '--root', str(self.root/'missing-private-root'), '--continue', 'private-target', '--json', *extra])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())['error']['code'], 'INPUT_INVALID')
            self.assertNotIn('private-target', output.getvalue())
            self.assertNotIn(str(self.root), output.getvalue())


class SharedCombinedTests(ThreadFixture):
    def test_private_execution_kept_in_personal_view_and_mine_limits_named_target(self):
        tid = self.shared('alice', '同名工作'); self.shared('bob', '同名工作')
        with self.use('alice') as w:
            w.synchronize(publish=False); w.bind(tid, 'private-agent', str(self.root), 'private-session')
            view = overview(w, compact=True, mine=True)
            combined = with_continuation(w, view, '同名工作', mine=True)
            self.assertEqual(combined['continuation']['thread']['id'], tid)
            self.assertEqual(combined['continuation']['local_execution'][0]['session_id'], 'private-session')
            with self.assertRaises(Error): with_continuation(w, overview(w, compact=True, scope='mesh'), tid)
            encoded = json.dumps(overview(w, compact=True, scope='mesh'))
            for secret in ('private-session', 'private-agent', str(self.root)): self.assertNotIn(secret, encoded)
