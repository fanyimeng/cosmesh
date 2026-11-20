"""Command receipts and retained legacy metadata on a migrated workbench."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh import sync
from cosmesh.threads import initialize, Workbench


class UnifiedStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'COSMESH_DATA_DIR': temp.name, 'COSMESH_PAGE_BACKGROUND': '0'})
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(temp.name) / 'work'
        initialize(self.root, 'owner')
        self.wb = Workbench(self.root)
        self.wb.client.update(actor='owner', hub_id='hub-test', retired_spaces={},
                              connection={'kind':'system','discovery':'/example/service.json','repo':'spaces'},
                              last_sync={'complete':False, 'errors':[{'error':{'code':'COMMAND_RETIRED'}}]})
        self.wb.save_client()
        self.info = {'protocol':'thread.access.v1','actor':'owner','hub_id':'hub-test',
                     'person':{'account':'owner','id':'person-owner'},'unified':True}

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            main([*args, '--json'])
        return json.loads(out.getvalue())

    def test_online_receipts_and_local_reads(self):
        with patch('cosmesh.spaces.rpc', side_effect=[
                self.info, self.info, self.info, {'actor':'owner','threads':[]},
                {'actor':'owner','protocol':'personal.v1','limits':{}}, {'actor':'owner','thread_protocol':'thread.personal.v1'},
                {'actor':'owner','threads':[]}]) as rpc:
            who = self.run_cli('whoami', '--require-fresh')
            self.assertEqual(who['data']['hub_actor'], 'owner', who)
            cap = self.run_cli('capabilities', '--require-fresh')
            self.assertEqual(cap['data']['mode'], 'thread_access', cap)
            for result in (who, cap, self.run_cli('thread','find','--require-fresh'),
                           self.run_cli('thread','list','--personal','--remote')):
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['warnings'], [])
                self.assertEqual(result['sync']['hub_check'], 'verified')
                self.assertTrue(result['sync']['identity_verified'])
            count = rpc.call_count
            for args in (('capabilities',), ('whoami',), ('thread','page')):
                result = self.run_cli(*args)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['warnings'], [])
                self.assertEqual(result['sync']['hub_check'], 'not_checked')
            self.assertEqual(rpc.call_count, count)

    def test_fresh_check_rejects_changed_account_or_hub(self):
        for change, code in (({'actor':'other'},'SYNC_PROTOCOL'), ({'hub_id':'different'},'STATE_CONFLICT'),
                             ({'actor':'other', 'person':{'account':'other'}},'AUTH_DENIED')):
            with patch('cosmesh.spaces.rpc', return_value=dict(self.info, **change)):
                result = self.run_cli('whoami','--require-fresh')
                self.assertEqual(result['error']['code'], code)
                self.assertFalse(result['sync']['identity_verified'])
        with patch('cosmesh.spaces.rpc', side_effect=Error('SYNC_NETWORK','unavailable')):
            self.assertEqual(self.run_cli('whoami','--require-fresh')['error']['code'], 'SYNC_NETWORK')

    def test_retired_sync_and_history_are_read_only(self):
        before = self.wb.path.read_bytes()
        with patch('cosmesh.spaces.rpc', side_effect=AssertionError('unexpected network call')):
            for args in (('sync',), ('thread','sync')):
                result = self.run_cli(*args)
                self.assertEqual(result['error']['code'], 'COMMAND_RETIRED', result)
            status = self.run_cli('thread','status')
            self.assertIsNone(status['sync']['last_sync'], status)
        self.assertEqual(before, self.wb.path.read_bytes())
        self.wb.client['last_sync']['errors'].append({'error':{'code':'SYNC_NETWORK'}})
        self.wb.save_client()
        self.assertEqual(self.run_cli('thread','status')['sync']['last_sync']['errors'], [{'error':{'code':'SYNC_NETWORK'}}])

    def test_first_retirement_from_server_keeps_previous_client_state(self):
        other = {'kind':'ssh-system', 'host':'legacy', 'discovery':'/example/other.json', 'repo':'shared'}
        self.wb.base.save_client({'remote':other, 'user':'owner'})
        before = self.wb.base.path('.cosmesh/client.json').read_bytes()
        with patch('cosmesh.sync.git_transport', return_value={}), patch(
                'cosmesh.sync.remote_status', side_effect=Error('COMMAND_RETIRED', 'old transport')) as rpc:
            result = self.run_cli('sync','--root',str(self.root))
            self.assertEqual(result['error']['code'], 'COMMAND_RETIRED')
            self.assertEqual(result['sync']['state'], 'transport_retired')
            rpc.assert_called_once_with(other)
        self.assertEqual(self.wb.base.path('.cosmesh/client.json').read_bytes(), before)
        self.wb.base.save_client(dict(self.wb.base.client(), last_error='COMMAND_RETIRED'))
        self.assertEqual(sync.state(self.wb.base)['state'], 'transport_retired')
        with patch('cosmesh.sync.remote_status', return_value={'actor':'owner','product':'cosmesh'}):
            sync.configure(self.wb.base, 'cosmesh', other)
        self.assertNotIn('last_error', self.wb.base.client())
