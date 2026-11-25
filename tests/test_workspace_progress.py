"""Shared progress with real work stores, registered files and the selected personal relay."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cosmesh import workspace_progress as progress
from cosmesh.errors import Error
from cosmesh.personal_protocol import read_json
from cosmesh.thread_personal import ThreadPersonal
from tests import test_thread_personal as fixtures


class WorkspaceProgressTests(unittest.TestCase):
    work = fixtures.ThreadPersonalTests.work
    file = fixtures.ThreadPersonalTests.file
    use = fixtures.ThreadPersonalTests.use

    def setUp(self):
        fixtures.ThreadPersonalTests.setUp(self)
        self.published = []
        self.active = 2
        self.interrupt = False
        self.online = self
        with self.use(self.a) as p:
            self.config = p.state['connection']
        self.info = dict(actor='alice', hub_id='test-hub', workspace_protocol='workspace.v1')
        for mock in [patch.object(progress, 'Online', return_value=self), patch.object(progress, 'schedule')]:
            mock.start()
            self.addCleanup(mock.stop)

    def call(self, method, **kwargs):
        if method == 'delivery_workspace_status':
            return {'thread': self.t, 'active_count': self.active}
        self.assertEqual(method, 'delivery_workspace_publish')
        if self.interrupt:
            raise Error('SYNC_NETWORK', 'Network disconnected.')
        self.published.append(kwargs['snapshot'])
        return dict(thread=self.t, accepted=True, revision=kwargs['snapshot'], active_count=self.active)

    def start(self):
        ticket = progress.prepare_workspace(self.a, self.online, self.t)
        progress.arm_workspace(self.a, ticket)
        return ticket

    def test_coalesced_recovery_idle_and_unrelated_commit(self):
        file = self.file()
        with self.use(self.a) as p:
            p.add(self.t, file)
            other = self.a.create('another', 'private')['id']
        initial = self.start()
        with self.use(self.a):
            self.a.append(other, 'unrelated')
        with patch.object(ThreadPersonal, 'synchronize', side_effect=AssertionError('Unexpected full sync')):
            self.assertFalse(progress.report(self.a)[0]['pending'])
            repeated = progress.prepare_workspace(self.a, self.online, self.t)
            self.assertEqual(repeated['snapshot'], initial['snapshot'])
        with self.use(self.a):
            for note in ('one', 'two', 'three'):
                self.a.append(self.t, note)
                self.a.load()
        self.assertTrue(progress.report(self.a, offline=True)[0]['pending'])
        self.interrupt = True
        self.assertEqual(progress.report(self.a)[0]['error'], 'SYNC_NETWORK')
        self.assertEqual(read_json(progress.policy(self.a, self.t))['snapshot'], initial['snapshot'])
        self.interrupt = False
        self.assertTrue(progress.report(self.a)[0]['pending'])
        self.assertEqual(self.published, [])
        done = progress.report(self.a, force=True)[0]
        self.assertEqual(self.published, [done['snapshot']])
        self.assertFalse(progress.report(self.a)[0]['pending'])
        self.assertEqual(len(self.published), 1)
        file.write_text('changed bytes')
        self.assertTrue(progress.report(self.a, status=True)[0]['pending'])
        changed = progress.report(self.a, force=True)[0]
        self.assertNotEqual(changed['snapshot'], done['snapshot'])
        self.assertEqual(len(self.published), 2)

    def test_two_devices_foreign_files_pausing_and_last_revoke(self):
        with self.use(self.a) as p:
            p.add(self.t, self.file('registered.txt'))
        self.start()
        with self.use(self.b) as p:
            p.restore(self.t)
            p.server_info = self.info
            progress.after_personal(self.b, p, self.t, 'fetch')
            self.b.append(self.t, 'New work on my second device')
        self.assertFalse(progress.report(self.b, force=True)[0]['pending'])
        self.assertEqual(len(self.published), 1)
        progress.pause(self.b, self.t)
        with self.use(self.b) as p:
            self.b.append(self.t, 'Paused work')
            progress.after_personal(self.b, p, self.t, 'sync')
        self.assertFalse(progress.report(self.b)[0]['enabled'])
        with self.use(self.b) as p:
            progress.after_personal(self.b, p, self.t, 'personal')
        self.assertTrue(progress.report(self.b, status=True)[0]['pending'])
        self.active = 0
        with patch.object(ThreadPersonal, 'synchronize', side_effect=AssertionError('No active readers')):
            self.assertFalse(progress.report(self.b, force=True)[0]['enabled'])
        self.assertEqual(len(self.published), 1)

    def test_regrant_racing_zero_reader_status_keeps_tracking(self):
        ticket = self.start()
        with self.use(self.a):
            self.a.append(self.t, 'new progress')
        def stale_status(method, **kwargs):
            self.assertEqual(method, 'delivery_workspace_status')
            progress.arm_workspace(self.a, ticket)
            return {'thread': self.t, 'active_count': 0}
        with patch.object(self, 'call', side_effect=stale_status):
            self.assertTrue(progress.report(self.a)[0]['pending'])
        self.assertTrue(read_json(progress.policy(self.a, self.t))['enabled'])
        self.assertFalse(progress.report(self.a, force=True)[0]['pending'])

    def test_file_write_while_hub_acknowledges_stays_pending(self):
        file = self.file()
        with self.use(self.a) as p:
            p.add(self.t, file)
        self.start()
        file.write_text('first change')
        dispatch = self.call
        def change_during_publish(method, **kwargs):
            if method == 'delivery_workspace_publish':
                file.write_text('another change')
            return dispatch(method, **kwargs)
        with patch.object(self, 'call', side_effect=change_during_publish):
            result = progress.report(self.a, force=True)[0]
            self.assertTrue(result['pending'])
        self.assertTrue(progress.report(self.a, status=True)[0]['pending'])
        self.assertFalse(progress.report(self.a, force=True)[0]['pending'])
        self.assertEqual(len(set(self.published)), 2)

    def test_collector_coalesces_registered_directories_in_one_shared_snapshot(self):
        from cosmesh.collector import Collector
        self.start()
        collector = Collector(self.a)
        for name in ('left', 'right'):
            root = self.root / name
            root.mkdir()
            (root / 'output.txt').write_text(name)
            collector.run(SimpleNamespace(collector='add', id=self.t, directory=str(root), prefix=name,
                                          personal=True, context_file=None))
        before = collector.run(SimpleNamespace(collector='once', offline=True))
        self.assertFalse(before['complete'])
        self.assertEqual(self.published, [])
        after = collector.run(SimpleNamespace(collector='once', offline=False))
        self.assertTrue(after['complete'], after)
        self.assertEqual(len(self.published), 1)
        self.assertEqual({r['snapshot'] for r in after['scopes']}, set(self.published))
        again = collector.run(SimpleNamespace(collector='once', offline=False))
        self.assertTrue(again['complete'])
        self.assertEqual(len(self.published), 1)

    def test_register_or_change_file_between_prepare_and_arm_is_queued(self):
        path = self.file('changed.txt')
        with self.use(self.a) as p:
            p.add(self.t, path)
        ticket = progress.prepare_workspace(self.a, self.online, self.t)
        path.write_text('changed during delivery')
        progress.arm_workspace(self.a, ticket)
        self.assertTrue(progress.report(self.a, status=True)[0]['pending'])
        with patch.dict(self.info, hub_id='changed-hub'):
            self.assertEqual(progress.report(self.a, force=True)[0]['error'], 'AUTH_DENIED')
        file = self.file('missing-during-sync.txt')
        with self.use(self.a) as p:
            p.add(self.t, file)
        file.unlink()
        partial = progress.report(self.a, force=True)[0]
        self.assertEqual(partial['error'], 'WORKSPACE_INCOMPLETE')
        self.assertEqual(partial['issues'][0]['file'], file.name)
        self.assertEqual(self.published, [])
        file.write_text('restored')
        self.assertFalse(progress.report(self.a, force=True)[0]['pending'])
        progress.arm_workspace(self.a, ticket)
        self.assertFalse(progress.report(self.a, status=True)[0]['pending'])


if __name__ == '__main__':
    unittest.main()
