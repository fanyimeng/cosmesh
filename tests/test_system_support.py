import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import test_system_access as fixtures
from cosmesh import guide
from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.feedback import Feedback
from cosmesh.protocol import encode
from cosmesh.service import initialize
from cosmesh.store import Store, atomic
from cosmesh.sync import configure, synchronize, git_transport, url


def request(body='希望说明更清楚', category='suggestion', reply_to=None):
    return {'title': None, 'body': body, 'product': 'system', 'category': category,
            'component': 'guide', 'reply_to': reply_to}


class GuideTests(unittest.TestCase):
    def test_resources_sections_and_code_coverage(self):
        self.assertEqual(len(guide.listing()['topics']), 12)
        self.assertIn('search', {x['topic'] for x in guide.listing()['topics']})
        for topic in guide.TOPICS:
            doc = guide.document(topic)
            self.assertTrue(doc['source']['sha256'])
            for section in doc['sections']:
                result = guide.show(topic, section['section'])
                text = '\n'.join(doc['text'].splitlines()[section['start_line'] - 1:section['end_line']]) + '\n'
                self.assertEqual(result['text'], text)
        files = guide.show('files')['text']
        for path in (Path(__file__).resolve().parents[1] / 'cosmesh').glob('*.py'):
            self.assertIn(path.name, files)

    def test_retrieval_is_offline_and_has_useful_sources(self):
        results = guide.search('SO_PEERCRED')
        self.assertTrue(any(r['topic'] == 'architecture' for r in results['matches']))
        self.assertTrue(all(len(r['snippet'].splitlines()) > 2 for r in results['matches']))
        with tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve())) as folder:
            missing = Path(folder) / 'unused'
            with patch.dict(os.environ, COSMESH_DATA_DIR=str(missing)), redirect_stdout(io.StringIO()) as output:
                code = main(['guide', 'show', 'files', '--json'], product='cosbase')
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertIsNone(result['sync'])
            self.assertFalse(missing.exists())


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='support-', dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, COSMESH_FEEDBACK_DIR=str(self.root / 'feedback'),
                              COSMESH_DATA_DIR=str(self.root / 'data'), COSMESH_SERVICE=str(self.root / 'no-service.json'))
        self.env.start(); self.addCleanup(self.env.stop)
        self.fb = Feedback()

    def test_offline_retention_idempotency_and_discard(self):
        preview = self.fb.queue(request(), key='one', dry_run=True)
        self.assertFalse(self.fb.root.exists())
        first = self.fb.queue(request(), key='one')
        replay = self.fb.queue(request(), key='one')
        self.assertEqual(first['id'], replay['id'])
        self.assertTrue(replay['replayed'])
        with self.assertRaises(Error) as error:
            self.fb.queue(request('不同内容'), key='one')
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.fb.read()['pending_count'], 1)
        self.assertEqual(self.fb.read(doc_id=first['id'])['feedback']['publication'], 'pending')
        self.fb.discard(first['id'])
        self.assertEqual(self.fb.read()['pending_count'], 0)
        self.assertEqual(len(self.fb.drafts()), 1)

    def test_failed_sync_keeps_id_and_material(self):
        with patch('cosmesh.feedback.Feedback.sync', side_effect=Error('SYNC_NETWORK', 'test offline')):
            with redirect_stdout(io.StringIO()) as output:
                code = main(['feedback', 'add', '--body', '发现系统问题', '--sync', '--idempotency-key', 'same', '--json'], product='cosbase')
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result['data']['saved_locally'])
        self.assertEqual(result['data']['sync_error']['code'], 'SYNC_NETWORK')
        self.assertEqual(self.fb.read()['records'][0]['id'], result['data']['id'])
        self.assertFalse(result['sync']['identity_verified'])

    def test_private_files_and_two_processes_one_key(self):
        code = 'from cosmesh.cli import main; raise SystemExit(main())'
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable, '-c', code, 'feedback', 'add', '--product', 'system', '--body', '并发反馈', '--idempotency-key', 'concurrent', '--json']
        processes = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [json.loads(p.communicate(timeout=15)[0]) for p in processes]
        self.assertTrue(all(r['ok'] for r in results), results)
        self.assertEqual(results[0]['data']['id'], results[1]['data']['id'])
        self.assertEqual(len(self.fb.drafts()), 1)
        path, _ = self.fb.drafts()[0]
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.chmod(0o644)
        with self.assertRaises(Error):
            self.fb.drafts()


class FeedbackHubTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SystemAccessTests('test_kernel_identity_and_environment_cannot_change_it')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.hub.create_repo('cosmesh-feedback', 'cosmesh', f.entry, f.user, system_feedback=True)
        self.config = {**f.config, 'repo': 'cosmesh-feedback'}
        seed = Store(f.folder / 'feedback-seed')
        initialize(seed, 'cosmesh', f.user, 'UTC')
        configure(seed, 'cosmesh', self.config); synchronize(seed, 'cosmesh')
        self.env = patch.dict(os.environ, COSMESH_FEEDBACK_DIR=str(f.folder / 'support'), COSMESH_SERVICE=str(f.discovery))
        self.env.start(); self.addCleanup(self.env.stop)
        self.fb = Feedback()

    def assertCode(self, code, fn):
        with self.assertRaises(Error) as error:
            fn()
        self.assertEqual(error.exception.code, code)

    def test_publish_pull_reply_and_repeat(self):
        f = self.fixture
        before = f.hub.status('cosmesh-feedback', f.user)['revision']
        first = self.fb.queue(request('离线保留的建议'), key='request')
        self.fb.pull()
        self.assertEqual(f.hub.status('cosmesh-feedback', f.user)['revision'], before)
        self.assertEqual(self.fb.read()['records'][0]['publication'], 'pending')
        self.fb.sync()
        after = f.hub.status('cosmesh-feedback', f.user)['revision']
        doc = self.fb.read(doc_id=first['id'])['feedback']
        self.assertEqual(doc['author'], f.user)
        self.assertEqual(doc['publication'], 'published')
        self.assertTrue(self.fb.queue(request('离线保留的建议'), key='request')['replayed'])
        self.fb.sync()
        self.assertEqual(f.hub.status('cosmesh-feedback', f.user)['revision'], after)
        reply = self.fb.queue(request('维护者补充说明', 'reply', first['id']))
        self.fb.sync()
        thread = self.fb.read(doc_id=reply['id'])
        self.assertEqual(thread['feedback']['id'], first['id'])
        self.assertEqual(thread['replies'][0]['id'], reply['id'])
        self.assertCode('HISTORY_PROTECTED', lambda: self.fb.discard(first['id']))

    def test_public_scope_and_explicit_readonly_or_deny(self):
        f = self.fixture
        self.assertTrue(f.hub.policy('cosmesh-feedback', 'root', write=True)['system_feedback'])
        self.assertTrue(f.hub.policy('team', 'root'))  # Mesh reads have been public since 0.1.10.
        self.assertCode('AUTH_DENIED', lambda: f.hub.policy('team', 'root', write=True))
        f.hub.grant('cosmesh-feedback', 'root', 'read')
        self.assertCode('AUTH_DENIED', lambda: f.hub.policy('cosmesh-feedback', 'root', write=True))
        f.hub.grant('cosmesh-feedback', 'root', 'none')
        self.assertCode('AUTH_DENIED', lambda: f.hub.policy('cosmesh-feedback', 'root'))
        f.hub.grant('cosmesh-feedback', f.user, 'read')
        first = self.fb.queue(request())
        self.fb.pull()  # Read-only users can clone and refresh without receive-pack.
        self.assertCode('SYNC_REJECTED', self.fb.sync)
        self.assertEqual(self.fb.read()['pending_count'], 1)
        f.hub.grant('cosmesh-feedback', f.user, 'write')
        self.fb.sync()
        self.assertEqual(self.fb.read(doc_id=first['id'])['feedback']['publication'], 'published')

    def test_accepted_feedback_is_protected_and_response_loss_is_idempotent(self):
        f = self.fixture
        first = self.fb.queue(request('网络结果丢失也只能保存一次'))
        target = self.fb.drafts()[0][0]
        def fail_receipt(path, text):
            if path == target and '"state": "published"' in text:
                raise OSError('injected acknowledgement failure')
            return atomic(path, text)
        with patch('cosmesh.feedback.atomic', side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.fb.sync()
        accepted = f.hub.status('cosmesh-feedback', f.user)['revision']
        self.fb.sync()
        self.assertEqual(f.hub.status('cosmesh-feedback', f.user)['revision'], accepted)
        path = 'documents/' + first['id'] + '.md'
        doc = self.fb.store.documents(self.fb.store.snapshot()[1])[first['id']]
        doc['body'] = '改写已接受反馈'
        self.fb.store.commit({path: encode(doc)}, 'tamper')
        pushed = self.fb.store.git('push', url(self.config), 'HEAD:refs/heads/main', extra_env=git_transport(self.config), check=False)
        self.assertNotEqual(pushed.returncode, 0)
        self.assertEqual(f.hub.status('cosmesh-feedback', f.user)['revision'], accepted)

    def test_nonfeedback_channel_and_cancelled_snapshot_are_safe(self):
        self.assertCode('POLICY_DENIED', lambda: self.fb.connect(repo='team'))
        draft = self.fb.queue(request('同步快照后用户取消'))
        stale = self.fb.drafts()
        self.fb.discard(draft['id'])
        with patch.object(self.fb, 'drafts', return_value=stale):
            self.fb.sync()
        self.assertEqual(self.fb.read()['total'], 0)
        self.assertFalse(self.fb.store.path('documents/' + draft['id'] + '.md').exists())


if __name__ == '__main__':
    unittest.main()
