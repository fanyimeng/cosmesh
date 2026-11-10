"""Local Base never depends on a reachable Hub; update queries are explicit."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmesh import client
from cosmesh.service import initialize, Service
from cosmesh.store import Store


class LocalFirstTests(unittest.TestCase):
    def test_base_initialization_and_edit_without_server(self):
        with tempfile.TemporaryDirectory() as folder, patch('cosmesh.sync.remote_status', side_effect=AssertionError('network used')):
            store = Store(Path(folder).resolve() / 'base')
            initialize(store, 'cosbase', 'local-user', 'Asia/Shanghai')
            service = Service(store, 'cosbase')
            task = service.run('task.create', {'title': '离线事项', 'body': '本地输入'})[0]['result']
            document = service.run('doc.show', {'id': task['id']})[0]
            service.run('doc.edit', {'id': task['id'], 'expect': document['version'], 'next_action': '本地下一步'})
            changed = service.run('doc.show', {'id': task['id']})[0]
            self.assertEqual(changed['next_action'], '本地下一步')
            self.assertEqual(store.git('status', '--porcelain'), '')

    def test_update_check_is_explicit_and_read_only(self):
        with patch.object(client, 'config', return_value=(Path('/unused'), {})), patch.object(client, 'fetch', return_value=json.dumps({'version': '9.0.0'}).encode()) as fetch:
            result = client.check()
            self.assertTrue(result['update_available'])
            self.assertFalse(result['automatic_updates'])
            fetch.assert_called_once_with({}, 'stable-local.json')
