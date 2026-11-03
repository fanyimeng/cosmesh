"""Execution evidence, ambiguous project membership, and the shared privacy boundary."""
import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.threads import Workbench, initialize
from cosmesh.thread_overview import overview, host_snapshot
from test_threads import ThreadFixture


def snapshot(root):
    return {'format': 1, 'source': 'supported_host_api', 'observed_at': '2026-01-02T12:00:00+00:00',
            'sessions': [{'agent': 'codex', 'host': socket.gethostname(), 'id': 'session-two',
                          'title': '原生会话标题', 'project_id': None, 'cwd': str(root), 'status': 'idle',
                          'updated_at': '2026-01-02T11:00:00Z'}],
            'projects': [{'agent': 'codex', 'host': socket.gethostname(), 'id': 'project-one',
                          'name': '目录对应项目', 'path': str(root)}]}


class PrivateOverviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); initialize(self.root/'work', 'alice')
        self.w = Workbench(self.root/'work')

    def test_many_to_many_preferred_session_project_candidate_and_no_writes(self):
        w = self.w
        with w.base.locked():
            w.load(); first = w.create('工作一', '目标和待办')['id']; w.load()
            second = w.create('工作二')['id']; w.load()
            w.generation = {'tool': 'generation-only'}
            w.append(first, '验证完成，下一步发布', 'result'); w.load()
            w.bind(first, 'codex', str(self.root), 'old-session')
            for tid in (first, second):
                w.bind(tid, 'Codex', str(self.root), 'session-two')
            # A later registration is not necessarily the best continuation point.
            w.bind(first, 'remote-agent', '/remote/work', 'unverified-later', 'remote-host')
            head, client = w.base.head, w.path.read_bytes()
            result = overview(w, details=True, host=snapshot(self.root))
            row = next(x for x in result['threads'] if x['id'] == first)
            preferred = row['execution']['preferred']
            self.assertEqual(preferred['agent']['status'], 'verified')
            self.assertEqual(preferred['session']['value']['title'], '原生会话标题')
            self.assertEqual(preferred['project']['status'], 'missing')
            self.assertIn('尚未归属', preferred['project']['reason'])
            self.assertEqual(preferred['project_candidates'][0]['status'], 'candidate')
            self.assertEqual(preferred['also_bound_threads'], [second])
            self.assertEqual(preferred['directory']['status'], 'verified')
            remote = next(x for x in row['execution']['bindings'] if x['host']['value'] == 'remote-host')
            self.assertEqual(remote['directory']['status'], 'registered')
            self.assertIsNone(remote['directory']['exists'])
            self.assertEqual(row['progress']['recent'][0]['text'], '验证完成，下一步发布')
            self.assertEqual(row['generation_tools'], ['generation-only'])
            self.assertEqual(w.base.head, head); self.assertEqual(w.path.read_bytes(), client)

    def test_real_membership_unavailable_provider_missing_bindings_and_archived_session(self):
        w = self.w
        with w.base.locked():
            w.load(); bound = w.create('绑定工作')['id']; w.load(); unbound = w.create('未绑定')['id']; w.load()
            w.bind(bound, 'codex', str(self.root), 'session-two')
            data = snapshot(self.root); data['sessions'][0]['project_id'] = 'project-one'
            row = overview(w, bound, host=data)['threads'][0]
            self.assertEqual(row['execution']['preferred']['project']['value']['name'], '目录对应项目')
            self.assertNotIn('project_candidates', row['execution']['preferred'])
            data['sessions'][0]['status'] = 'archived'
            self.assertEqual(overview(w, bound, host=data)['threads'][0]['execution']['preferred']['resume']['status'], 'missing')
            data['sessions'][0]['status'] = 'unavailable'
            fallback = overview(w, bound, host=data)['threads'][0]['execution']['preferred']
            self.assertEqual(fallback['session']['status'], 'registered')
            self.assertEqual(fallback['project']['status'], 'missing')
            self.assertEqual(overview(w, bound)['threads'][0]['execution']['preferred']['session']['status'], 'registered')
            self.assertEqual(overview(w, unbound)['threads'][0]['execution']['status'], 'missing')
            # An identical Session ID from another host must not validate this binding.
            data['sessions'][0]['host'] = 'another-host'; data['sessions'][0]['status'] = 'idle'
            self.assertEqual(overview(w, bound, host=data)['threads'][0]['execution']['preferred']['session']['status'], 'registered')

    def test_limits_validation_and_cli_scope_privacy_on_success_and_failure(self):
        w = self.w
        with w.base.locked():
            w.load(); tid = w.create('私人标题')['id']; w.load(); w.bind(tid, 'codex', str(self.root), 'private-session')
            with self.assertRaises(Error): overview(w, limit=0)
            bad = snapshot(self.root); bad['sessions'][0]['body'] = 'private chat body'
            with self.assertRaises(Error): host_snapshot(bad)
            bad = snapshot(self.root); bad['observed_at'] = '2026-01-02'
            with self.assertRaises(Error): host_snapshot(bad)
        for product in ('cosbase', 'cosmesh'):
            with redirect_stdout(io.StringIO()) as output:
                code = main(['thread', 'overview', '--root', str(w.base.root), '--mine', '--json'], product=product)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['data']['threads'][0]['execution']['preferred']['session']['value']['id'], 'private-session')
            for extra, expected in (([], 0), (['--host-file', '/file-not-read'], 2)):
                with redirect_stdout(io.StringIO()) as output:
                    code = main(['thread', 'overview', '--root', str(w.base.root), '--scope', 'mesh', '--json', *extra], product=product)
                self.assertEqual(code, expected)
                for secret in (str(self.root), 'private-session', '私人标题'):
                    self.assertNotIn(secret, output.getvalue())

    def test_local_directory_probed_once_and_limit_is_explicit(self):
        w = self.w
        with w.base.locked():
            w.load()
            for index in range(3):
                tid = w.create('工作' + str(index))['id']; w.load()
                w.bind(tid, 'agent', str(self.root), 's' + str(index))
            with patch('cosmesh.thread_overview.directory_exists', return_value=True) as probe:
                data = overview(w, limit=2)
                self.assertEqual(probe.call_count, 1)
            self.assertTrue(data['truncated']); self.assertEqual(data['total'], 3)

    def test_scoped_resolution_never_suggests_private_ids(self):
        w = self.w
        with w.base.locked():
            w.load()
            for title in ('私人一', '私人二'):
                w.create(title); w.load()
            with self.assertRaises(Error) as error:
                overview(w, 'W-', scope='mesh')
            self.assertEqual(error.exception.code, 'NOT_FOUND')
            self.assertNotIn('W-', json.dumps(error.exception.as_dict()))

    def test_compact_keeps_fields_and_pinned_sources_without_directory_probes(self):
        w = self.w
        with w.base.locked():
            w.load(); tid = w.create('紧凑工作', '背景' * 100)['id']; w.load()
            for index in range(4):
                w.append(tid, '进展' + str(index) + '原文' * 100); w.load()
            w.bind(tid, 'codex', str(self.root), 'session-two')
            w.bind(tid, 'other-agent', '/remote/work', None, 'remote-host')
            head, client = w.base.head, w.path.read_bytes()
            with patch('cosmesh.thread_overview.directory_exists', side_effect=AssertionError('Unexpected probe')):
                data = overview(w, compact=True)
            row = data['threads'][0]
            self.assertEqual(data['format'], 'thread.overview.compact.v1')
            self.assertEqual(row['id'], tid); self.assertEqual(row['scope'], 'base')
            self.assertEqual(row['execution']['session']['id'], 'session-two')
            self.assertEqual(row['execution']['directory']['status'], 'registered')
            self.assertEqual(row['execution']['binding_count'], 2)
            self.assertEqual(row['execution']['project']['status'], 'missing')
            self.assertEqual(len(row['progress']['recent']), 1)
            record = row['progress']['recent'][0]
            self.assertTrue(record['truncated']); self.assertTrue(row['purpose']['truncated'])
            self.assertEqual(w.resolve(data['source_index'][record['source']])['body'][:160], record['text'])
            self.assertEqual(data['source_index'][row['source']], w.ref(w.find(tid)))
            self.assertEqual(w.base.head, head); self.assertEqual(w.path.read_bytes(), client)
            with self.assertRaises(Error): overview(w, compact=True, details=True)

    def test_compact_host_observation_and_cli_one_local_read(self):
        w = self.w
        with w.base.locked():
            w.load(); tid = w.create('宿主信息')['id']; w.load()
            w.bind(tid, 'codex', str(self.root), 'session-two')
            with patch('cosmesh.thread_overview.directory_exists', side_effect=AssertionError('Unexpected probe')):
                row = overview(w, host=snapshot(self.root), compact=True)['threads'][0]
            self.assertEqual(row['execution']['status'], 'verified')
            self.assertTrue(row['execution']['project']['verified_absence'])
            self.assertEqual(row['execution']['project_candidates'][0]['status'], 'candidate')
            self.assertEqual(row['execution']['directory']['status'], 'registered')
        original = Workbench.load
        loads = []
        def loaded(instance):
            loads.append(instance.base.root)
            return original(instance)
        with patch.object(Workbench, 'load', loaded), patch('cosmesh.spaces.rpc', side_effect=AssertionError('Unexpected network')), patch('cosmesh.thread_overview.directory_exists', side_effect=AssertionError('Unexpected probe')):
            with redirect_stdout(io.StringIO()) as output:
                code = main(['thread', 'overview', '--root', str(w.base.root), '--mine', '--compact', '--json'])
        self.assertEqual(code, 0); self.assertEqual(len(loads), 1)
        response = json.loads(output.getvalue())
        self.assertNotIn('root', response['sync'])
        self.assertNotIn('thread_count', response['sync'])

    def test_uninitialized_shared_overview_does_not_expose_private_error_paths(self):
        private_root = self.root/'private-missing-workbench'
        for extra in ([], ['--compact']):
            with redirect_stdout(io.StringIO()) as output:
                code = main(['thread', 'overview', '--root', str(private_root), '--scope', 'mesh', '--json', *extra])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())['error']['code'], 'NOT_INITIALIZED')
            self.assertNotIn(str(private_root), output.getvalue())


class SharedOverviewTests(ThreadFixture):
    def test_shared_work_private_bindings_and_progress_roundtrip(self):
        tid = self.shared('alice', '公开工作', '公开目标')
        with self.use('alice') as w:
            w.bind(tid, 'private-agent', str(self.root), 'secret-session')
            w.append(tid, '公开进展', 'result'); w.load(); self.assertTrue(w.synchronize()['synchronized'])
            self.assertEqual(overview(w, tid)['threads'][0]['execution']['preferred']['agent']['value'], 'private-agent')
            data = overview(w, scope='mesh', details=True)
            encoded = json.dumps(data)
            for secret in ('private-agent', str(self.root), 'secret-session', 'execution', 'binding_count'):
                self.assertNotIn(secret, encoded)
            compact = overview(w, scope='mesh', compact=True)
            encoded = json.dumps(compact)
            for secret in ('private-agent', str(self.root), 'secret-session', 'execution', 'binding_count', 'local'):
                self.assertNotIn(secret, encoded)
            compact_row = compact['threads'][0]
            self.assertEqual(compact['source_index'][compact_row['source']], w.ref(w.find(tid)))
        with self.use('bob') as w:
            w.synchronize(publish=False)
            row = overview(w, tid, scope='mesh')['threads'][0]
            self.assertEqual(row['progress']['recent'][0]['text'], '公开进展')
            self.assertTrue(row['progress']['recent'][0]['accepted'])
            self.assertNotIn('execution', row)
