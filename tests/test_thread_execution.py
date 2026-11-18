"""Public facts and owner-only execution locations through isolated real Git sources."""
import copy
import json
from uuid import uuid4
from unittest.mock import patch

from tests.test_threads import ThreadFixture
from cosmesh.cli import parser
from cosmesh.errors import Error
from cosmesh.personal import initialize
from cosmesh.personal_protocol import digest, packed
from cosmesh.personal_relay import Relay, initialize as enable
from cosmesh.thread_cli import execute
from cosmesh.thread_execution import ThreadExecution
from cosmesh.thread_personal import ThreadPersonal
from cosmesh.threads import Workbench, initialize as init_work


class ExecutionTests(ThreadFixture):
    participants = ('alice', 'bob')

    def setUp(self):
        super().setUp(); enable(self.hub)

    def personal(self, wb, actor, cls=ThreadExecution):
        initialize(wb, uuid4().hex)
        p = cls(wb)
        p.state.update(connection=self.connection(actor), actor=actor); p.save()
        return p

    def command(self, wb, *args):
        return execute(parser('cosmesh').parse_args(['thread', *args, '--root', str(wb.base.root)]))[0]

    def cold(self, name, actor):
        init_work(self.root / name, name)
        wb = Workbench(self.root / name)
        with self.use(wb): self.personal(wb, actor)
        return wb

    def test_peer_two_devices_cli_and_public_content_remain_separate(self):
        tid = self.shared('alice', 'One public work', 'public facts only'); self.update('bob')
        with self.use('bob') as wb:
            p = self.personal(wb, 'bob')
            wb.bind(tid, 'codex', str(self.root), 'bob-session-a')
            first = p.synchronize(tid)
            value = p.view(tid)
            self.assertEqual(set(value), {'format', 'thread', 'source', 'bindings'})
            self.assertNotIn('public facts only', packed(value))
        public_before = self.hub.repo(self.repos['alice']).git('rev-parse', 'main')
        b = self.cold('bob-second-device', 'bob')
        base_before = b.base.head
        target = self.root / 'existing-project'; target.mkdir(); (target / 'keep').write_text('keep')
        remote = self.command(b, 'list', '--personal', '--remote')
        self.assertEqual(remote['threads'][0]['personal_sync'], 'execution')
        with self.assertRaises(Error) as error:
            self.command(b, 'fetch', tid, '--workdir', str(target), '--agent', 'dsh', '--files')
        self.assertEqual(error.exception.code, 'INPUT_INVALID')
        result = self.command(b, 'fetch', tid, '--workdir', str(target), '--agent', 'dsh',
                              '--project-id', 'native-b', '--project-name', 'B', '--session-id', 'bob-session-b')
        self.assertEqual(result['thread'], tid)
        self.assertEqual(result['restoration']['records'], 'public_source_fetched')
        self.assertEqual(result['restoration']['execution'], 'registered_only')
        self.assertFalse(result['new_thread_created']); self.assertFalse(result['published'])
        b.load()
        self.assertEqual(b.base.head, base_before)
        self.assertEqual(b.find(tid)['repo'], self.repos['alice'])
        self.assertEqual(set(b.client['spaces']), {self.repos['alice']})
        self.assertNotIn(tid, b.client.get('personal_thread_authors', {}))
        self.assertEqual(b.client['bindings'][0]['session_id'], 'bob-session-b')
        self.assertEqual(len(b.client['bindings']), 1)
        self.command(b, 'fetch', tid, '--workdir', str(target), '--agent', 'dsh',
                     '--project-id', 'native-b', '--project-name', 'B', '--session-id', 'bob-session-b')
        self.assertEqual((target / 'keep').read_text(), 'keep')
        with self.use('bob') as wb:
            p = ThreadExecution(wb); p.synchronize(tid, pull=True)
            locations = p.locations(tid)['locations']
            self.assertEqual(len({r['device'] for r in locations}), 2)
            self.assertEqual(len(wb.client['bindings']), 1)
        self.assertEqual(self.hub.repo(self.repos['alice']).git('rev-parse', 'main'), public_before)
        history = self.hub.repo(self.repos['alice']).git('log', '-p', 'main')
        self.assertNotIn('bob-session', history)
        # Alice's identical public Thread ID has a separate private stream.
        with self.use('alice') as wb:
            p = self.personal(wb, 'alice'); wb.bind(tid, 'dsh', str(self.root), 'alice-only')
            p.synchronize(tid)
            self.assertNotIn('bob-session', packed(p.view(tid)))
        with self.assertRaises(Error):
            Relay(self.hub, 'alice').dispatch({'method': 'personal_get', 'sha': first['accepted_snapshot'], 'offset': 0})
        self.assertNotIn('alice-only', packed(self.command(b, 'locations', tid)))
        # Refresh through the public source brings new facts without private copies.
        with self.use('alice') as wb:
            wb.append(tid, 'new public result'); wb.load(); wb.synchronize()
        self.command(b, 'fetch', tid)
        b.load(); self.assertIn('new public result', packed(b.context(tid)))
        self.assertEqual(b.base.head, base_before)

    def test_private_to_public_preserves_prior_snapshot_and_recovers_lost_reply(self):
        a = self.wbs['alice']; b = self.cold('alice-second', 'alice')
        with self.use(a):
            tid = a.create('Publish later', 'private history')['id']; a.load()
            p = self.personal(a, 'alice', ThreadPersonal)
            a.bind(tid, 'codex', str(self.root), 'prior-location'); old = p.synchronize(tid)['accepted_snapshot']
        with self.use(b): ThreadPersonal(b).restore(tid)
        with self.use(a):
            a.share(tid); a.synchronize(); a.load()
            p = ThreadExecution(a); real = p.rpc
            def lost(payload):
                result = real(payload)
                if payload['method'] == 'personal_execution_commit': raise Error('SYNC_NETWORK', 'lost accepted reply')
                return result
            with patch.object(p, 'rpc', side_effect=lost), self.assertRaises(Error): p.synchronize(tid)
            p.synchronize(tid)
            self.assertEqual(p.rpc({'method': 'personal_thread_head', 'thread': tid})['sha'], old)
            self.assertIn('prior-location', packed(p.view(tid)))
        # Existing private history survives; the authoritative public ID takes precedence.
        self.command(b, 'fetch', tid, '--workdir', str(self.root / 'new-place'), '--agent', 'dsh')
        b.load(); self.assertEqual(b.find(tid)['repo'], self.repos['alice'])
        self.assertIn('documents/' + tid + '.md', b.base.snapshot()[1])
        rows = self.command(b, 'list', '--personal', '--remote')['threads']
        self.assertEqual(len([r for r in rows if r['thread'] == tid]), 1)
        # A failure writing the derived sidecar is recoverable from accepted owner metadata.
        with self.use(b):
            p = ThreadExecution(b); b.bind(tid, 'dsh', str(self.root), 'after-accept')
            from cosmesh.thread_execution import save_json
            def interrupted(path, value):
                if path.parent == p.directory: raise Error('TEST_INTERRUPT', 'local sidecar')
                return save_json(path, value)
            with patch('cosmesh.thread_execution.save_json', side_effect=interrupted), self.assertRaises(Error): p.synchronize(tid)
            p.synchronize(tid, pull=True)
            self.assertIn('after-accept', packed(p.view(tid)))

    def test_protocol_scope_old_hub_and_wrong_identity_are_rejected(self):
        tid = self.shared('alice', 'Known public work'); self.update('bob')
        with self.use('bob') as wb:
            p = self.personal(wb, 'bob'); wb.bind(tid, 'dsh', str(self.root), 'private-binding')
            p.synchronize(tid); value = p.view(tid)
            variants = []
            bad = copy.deepcopy(value); bad['documents'] = {'secret': 'must not travel'}; variants.append(bad)
            bad = copy.deepcopy(value); bad['source']['owner'] = 'bob'; variants.append(bad)
            bad = copy.deepcopy(value); bad['source']['repo'] = self.repos['bob']; bad['source']['owner'] = 'bob'; variants.append(bad)
            bad = copy.deepcopy(value); bad['bindings'] = {}; variants.append(bad)
            for bad in variants:
                raw = packed(bad).encode(); sha = digest(raw); file = self.root / 'invalid.json'; file.write_bytes(raw)
                p.upload(file, sha, len(raw))
                with self.assertRaises(Error):
                    p.rpc({'method': 'personal_execution_commit', 'thread': tid, 'sha': sha,
                           'expect': p.rpc({'method': 'personal_execution_head', 'thread': tid})['sha']})
            missing = 'W-' + str(uuid4())
            bad = dict(value, thread=missing, bindings={})
            raw = packed(bad).encode(); sha = digest(raw); file.write_bytes(raw); p.upload(file, sha, len(raw))
            with self.assertRaises(Error) as error:
                p.rpc({'method': 'personal_execution_commit', 'thread': missing, 'sha': sha, 'expect': None})
            self.assertEqual(error.exception.code, 'REFERENCE_UNAVAILABLE')
            with patch.object(p, 'upload', side_effect=AssertionError('no upload')):
                with patch.object(p, 'rpc', return_value={'protocol': 'personal.v1', 'thread_protocol': 'thread.personal.v1'}):
                    with self.assertRaises(Error) as error: p.synchronize(tid)
                    self.assertEqual(error.exception.code, 'FEATURE_UNAVAILABLE')
                with self.assertRaises(Error) as error: p.synchronize(tid, files=True)
                self.assertEqual(error.exception.code, 'INPUT_INVALID')
                p.state['actor'] = 'alice'
                with self.assertRaises(Error) as error: p.synchronize(tid)
                self.assertEqual(error.exception.code, 'AUTH_DENIED')
        other = Relay(self.hub, 'alice')
        self.assertEqual(other.dispatch({'method': 'personal_execution_list'})['threads'], [])
        with self.assertRaises(Error): other.dispatch({'method': 'personal_execution_list', 'owner': 'bob'})
