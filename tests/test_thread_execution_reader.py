"""Offline execution reads combine local and retained remote events without writes."""
import tempfile
import unittest
from pathlib import Path

from cosmesh.agent_projects import bind as bind_project
from cosmesh.personal import initialize as initialize_personal
from cosmesh.personal_protocol import digest, packed, save_json
from cosmesh.thread_execution_reader import personal_state, raw_locations
from cosmesh.thread_lifecycle import locate
from cosmesh.thread_personal import ThreadPersonal
from cosmesh.threads import Workbench, initialize


class ExecutionReaderTests(unittest.TestCase):
    def test_local_and_retained_remote_rows_are_combined_without_initializing_sync(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as directory:
            root = Path(directory); initialize(root/'work', 'alice'); wb = Workbench(root/'work')
            with wb.base.locked():
                wb.load(); tid = wb.create('Work')['id']; wb.load()
                wb.bind(tid, 'dsh', str(root), 'local-session')
                bind_project(wb, tid, 'dsh', 'CosMesh', 'native-one', kind='workspace', workdir=str(root))
                head, client = wb.base.head, wb.path.read_bytes()
            rows = raw_locations(wb, tid)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row['device'] is None for row in rows.values()))
            self.assertEqual(personal_state(wb), {})
            self.assertEqual(wb.base.head, head); self.assertEqual(wb.path.read_bytes(), client)
            with wb.base.locked(): initialize_personal(wb, 'd' * 32)
            personal = ThreadPersonal(wb)
            remote = {'device': 'e' * 32, 'replica': 'f' * 32, 'kind': 'bindings',
                      'binding': {'thread': tid, 'agent': 'codex', 'host': 'remote',
                                  'workdir': '/remote/private', 'session_id': 'remote-session'}}
            key = digest(packed(remote).encode())
            remote_project = {'device': 'e' * 32, 'replica': 'f' * 32, 'kind': 'project_bindings',
                'binding': {'thread': tid, 'agent': 'codex', 'host': 'remote', 'kind': 'project',
                            'name': 'Remote project', 'project_id': 'native-remote', 'workdir': '/remote/private',
                            'status': 'registered', 'removed': False, 'at': '2026-01-01T00:00:00+00:00'}}
            project_key = digest(packed(remote_project).encode())
            save_json(personal.directory/(tid + '.json'), {'bindings': {key: remote, project_key: remote_project}})
            combined = raw_locations(wb, tid)
            self.assertEqual(len(combined), 4)
            self.assertIn(key, combined)
            self.assertEqual({row['device'] for row in combined.values() if row['device'] != 'e' * 32}, {'d' * 32})
            self.assertEqual(personal_state(wb)['device'], 'd' * 32)
            with wb.base.locked():
                wb.load()
                found = locate(wb, workdir='/remote/private', session_id='remote-session', host='remote',
                               agent='codex', project_id='native-remote')['candidates']
            self.assertEqual([row['thread']['id'] for row in found], [tid])
            self.assertEqual(found[0]['execution_metadata']['summary']['project_count'], 2)
