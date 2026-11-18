"""Selected Thread isolation, restore/merge, recovery and old-runtime compatibility."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_personal_sharing as fixtures
from cosmesh.errors import Error
from cosmesh.personal_protocol import digest, packed, save_json
from cosmesh.personal_relay import Relay
from cosmesh.thread_personal import ThreadPersonal
from cosmesh.thread_personal_protocol import validate_manifest


class ThreadPersonalTests(unittest.TestCase):
    setUp = fixtures.PersonalTests.setUp
    work = fixtures.PersonalTests.work
    file = fixtures.PersonalTests.file

    @contextmanager
    def use(self, wb):
        with wb.base.locked():
            wb.load(); yield ThreadPersonal(wb)

    def start(self):
        with self.use(self.a) as p: return p.synchronize(self.t)

    def test_selected_thread_restore_into_nonempty_directory_and_repeat(self):
        destination = self.root / 'project-b'; destination.mkdir()
        original = destination / 'notes.txt'; original.write_text('preserve')
        with self.use(self.a) as p:
            other = p.wb.create('other-secret', 'do-not-upload')['id']; p.wb.load()
            p.wb.bind(other, 'codex', str(self.root)); p.wb.bind(self.t, 'dsh', str(self.root))
            p.wb.append(self.t, 'work', refs=[p.wb.ref(p.wb.find(other))]); p.wb.load()
            result = p.synchronize(self.t)
            raw = (p.cache / result['accepted_snapshot']).read_text()
            self.assertNotIn('do-not-upload', raw)
            manifest = json.loads(raw)
            self.assertTrue(all(r['binding']['thread'] == self.t for r in manifest['bindings'].values()))
            self.assertEqual(len(result['missing_private_references']), 1)
        with self.use(self.b) as p:
            local = p.wb.create('keep local', 'destination private')['id']; p.wb.load()
            config = dict(p.wb.config)
            for _ in range(2):
                result = p.restore(self.t, str(destination), 'codex', 'native-project', 'Project B')
                self.assertFalse(result['new_thread_created'])
            self.assertEqual(p.wb.config, config)
            self.assertEqual(p.wb.find(local)['doc']['body'], 'destination private')
            self.assertEqual(sum(d['doc']['meta']['kind'] == 'thread' for d in p.wb.catalog.values()), 2)
            self.assertEqual(len(p.wb.client['bindings']), 1)
            self.assertEqual(len(p.wb.client['project_bindings']), 1)
            self.assertEqual(original.read_text(), 'preserve')
            with self.assertRaises(Error): p.wb.find(other)
        with self.use(self.a) as p:
            p.synchronize(self.t, pull=True)
            rows = p.locations(self.t)['locations']
            self.assertEqual(len({r['device'] for r in rows}), 2)
            self.assertTrue(any(r['binding'].get('project_id') == 'native-project' for r in rows))
            # Foreign locations are retained privately, never injected into local execution.
            self.assertFalse(any(r.get('workdir') == str(destination) for r in p.wb.client['bindings']))

    def test_owner_isolation_and_no_legacy_index_exposure(self):
        sha = self.start()['accepted_snapshot']
        bob = Relay(self.hub, 'bob')
        self.assertEqual(bob.dispatch({'method': 'personal_thread_list'})['threads'], [])
        self.assertIsNone(bob.dispatch({'method': 'personal_thread_head', 'thread': self.t})['sha'])
        with self.assertRaises(Error): bob.dispatch({'method': 'personal_get', 'sha': sha, 'offset': 0})
        with self.assertRaises(Error): bob.dispatch({'method': 'personal_thread_list', 'owner': 'alice'})
        self.assertEqual(Relay(self.hub, 'alice').dispatch({'method': 'personal_list'})['workspaces'], [])
        self.assertEqual(self.hub.spaces({'method': 'list_threads'}, 'bob')['spaces'], [])

    def test_history_bidirectional_offline_append_and_small_file_lazy_fetch(self):
        with self.use(self.a) as p:
            old = p.wb.ref(p.wb.find(self.t))
            p.wb.edit(self.t, old['version'], body='new definition'); p.wb.load()
            small = p.add(self.t, self.file())['id']
            huge_path = self.root / 'huge.fits'
            with huge_path.open('wb') as f: f.truncate(100 * 1024**3)
            huge = p.add(self.t, huge_path)['id']
            other = p.wb.create('private-file-thread')['id']; p.wb.load()
            secret = p.add(other, self.file('secret.txt', b'do not send'))['id']
            result = p.synchronize(self.t, files=True)
            self.assertEqual({r['id'] for r in result['transfers']['files']}, {small, huge})
            self.assertIsNone(p.asset(secret)['blob'])
        with self.use(self.b) as p:
            p.restore(self.t)
            self.assertEqual(p.wb.resolve(old)['body'], '私人目标')
            self.assertFalse((p.cache / p.asset(small)['blob']).exists())
            self.assertEqual(Path(p.open(small)['path']).read_bytes(), b'x,y\n1,2\n')
            with self.assertRaises(Error) as exc: p.open(huge)
            self.assertEqual(exc.exception.code, 'SOURCE_PENDING')
            p.wb.append(self.t, 'B offline'); p.wb.load()
        with self.use(self.a) as p:
            p.wb.append(self.t, 'A offline'); p.wb.load(); p.synchronize(self.t)
        with self.use(self.b) as p: p.synchronize(self.t)
        with self.use(self.a) as p:
            p.synchronize(self.t, pull=True)
            self.assertEqual({r['doc']['body'] for r in p.wb.records(self.t)}, {'A offline', 'B offline'})

    def test_old_hub_rejected_before_upload(self):
        with self.use(self.a) as p:
            with patch.object(p, 'rpc', return_value={'protocol': 'personal.v1'}) as rpc:
                with self.assertRaises(Error) as exc: p.synchronize(self.t, files=True)
                self.assertEqual(exc.exception.code, 'FEATURE_UNAVAILABLE')
                self.assertEqual(rpc.call_count, 1)

    def test_conflict_preserves_both_and_unrelated_data(self):
        self.start()
        with self.use(self.b) as p: p.restore(self.t)
        for wb, body in ((self.a, 'A changed'), (self.b, 'B changed')):
            with self.use(wb) as p:
                p.wb.edit(self.t, p.wb.ref(p.wb.find(self.t))['version'], body=body); p.wb.load()
        with self.use(self.a) as p: p.synchronize(self.t)
        with self.use(self.b) as p:
            before = p.wb.base.head
            with self.assertRaises(Error) as exc: p.synchronize(self.t)
            self.assertEqual(exc.exception.code, 'PERSONAL_CONFLICT')
            self.assertEqual(p.wb.base.head, before)
            self.assertEqual(p.wb.find(self.t)['doc']['body'], 'B changed')

    def test_lost_reply_and_interrupted_apply_recover(self):
        with self.use(self.a) as p:
            real = p.rpc
            def lost(payload):
                result = real(payload)
                if payload['method'] == 'personal_thread_commit': raise Error('SYNC_NETWORK', 'lost')
                return result
            with patch.object(p, 'rpc', side_effect=lost):
                with self.assertRaises(Error): p.synchronize(self.t)
            p.wb.append(self.t, 'after loss'); p.wb.load(); p.synchronize(self.t)
        with self.use(self.b) as p:
            with patch.object(p, 'apply_thread', side_effect=Error('TEST_INTERRUPT', 'before apply')):
                with self.assertRaises(Error): p.restore(self.t)
        with self.use(self.b) as p:
            self.assertEqual(p.wb.find(self.t)['doc']['meta']['id'], self.t)
            self.assertEqual(len(p.wb.records(self.t)), 1)
            p.restore(self.t)

    def test_lost_merged_reply_then_edit_and_postcommit_recovery(self):
        self.start()
        with self.use(self.b) as p: p.restore(self.t)
        with self.use(self.a) as p:
            p.wb.append(self.t, 'remote addition'); p.wb.load(); p.synchronize(self.t)
        with self.use(self.b) as p:
            real = p.rpc
            def lost(payload):
                result = real(payload)
                if payload['method'] == 'personal_thread_commit': raise Error('SYNC_NETWORK', 'lost')
                return result
            with patch.object(p, 'rpc', side_effect=lost):
                with self.assertRaises(Error): p.synchronize(self.t)
            p.wb.edit(self.t, p.wb.ref(p.wb.find(self.t))['version'], body='after lost merge'); p.wb.load()
            p.synchronize(self.t)
            self.assertEqual(len(p.wb.records(self.t)), 1)
        with self.use(self.a) as p:
            original = p.save
            with patch.object(p, 'save', side_effect=Error('TEST_INTERRUPT', 'after commit')):
                with self.assertRaises(Error): p.synchronize(self.t, pull=True)
        with self.use(self.a) as p:
            self.assertEqual(p.wb.find(self.t)['doc']['body'], 'after lost merge')
            self.assertFalse((p.directory / 'apply.json').exists())

    def test_same_owner_alias_state_and_receipt_replay(self):
        with self.use(self.a) as p:
            record = p.wb.append(self.t, 'once', key='cross-device-operation'); p.wb.load(); p.synchronize(self.t)
        with self.use(self.b) as p:
            p.restore(self.t)
            from cosmesh.cli import parser
            from cosmesh.thread_cli import execute
            # Own private pages must include a restored Thread despite a different local username.
            page, _ = execute(parser('cosmesh').parse_args(['thread','page','--root',str(self.b.base.root)]))
            self.assertIn(self.t, packed(page))
            again = p.wb.append(self.t, 'once', key='cross-device-operation')
            self.assertEqual(again['id'], record['id'])
            self.assertTrue(again['replayed'])
            p.wb.append(self.t, 'done on B', record_type='state', state='done'); p.wb.load()
            self.assertEqual(p.wb.records(self.t)[-1]['doc']['meta']['author'], 'local-alice')
            p.synchronize(self.t)
        with self.use(self.a) as p:
            p.synchronize(self.t, pull=True)
            self.assertEqual(p.wb.present(p.wb.find(self.t))['work_state'], 'done')

    def test_server_rejects_cross_thread_manifest(self):
        with self.use(self.a) as p:
            other = p.wb.create('secret', 'private')['id']; p.wb.load()
            value = p.manifest(self.t)
            path = 'documents/' + other + '.md'
            value['documents'][path] = p.wb._models['local'][1][path]
            raw = packed(value).encode(); sha = digest(raw)
            source = self.file('malformed.json', raw); p.upload(source, sha, len(raw))
            with self.assertRaises(Error) as exc:
                p.rpc({'method': 'personal_thread_commit', 'thread': self.t, 'sha': sha, 'expect': None})
            self.assertEqual(exc.exception.code, 'POLICY_DENIED')

    def test_preexisting_hub_alias_operation_replays_after_restore(self):
        with self.use(self.a) as p:
            p.wb.client['actor'] = 'server-alias'; p.wb.save_client(); p.wb.load()
            first = p.wb.append(self.t, 'old alias operation', key='alias-replay'); p.wb.load()
            p.synchronize(self.t)
            self.assertEqual(p.wb.append(self.t, 'old alias operation', key='alias-replay')['id'], first['id'])
        with self.use(self.b) as p:
            p.restore(self.t)
            self.assertEqual(p.wb.append(self.t, 'old alias operation', key='alias-replay')['id'], first['id'])
            with self.assertRaises(Error) as exc: p.wb.append(self.t, 'changed input', key='alias-replay')
            self.assertEqual(exc.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_cli_restore_and_retired_commands(self):
        from cosmesh.cli import parser
        from cosmesh.thread_cli import execute
        self.start()
        target = self.root / 'project'; target.mkdir()
        args = parser('cosmesh').parse_args(['thread', 'fetch', self.t, '--root', str(self.b.base.root),
                                   '--workdir', str(target), '--agent', 'dsh'])
        result, _ = execute(args)
        self.assertEqual(result['thread'], self.t)
        from cosmesh.personal_cli import execute as personal_execute
        for words in (['sync'], ['join', '0' * 32], ['asset', 'push']):
            args = parser('cosmesh').parse_args(['personal', *words, '--root', str(self.a.base.root)])
            with self.assertRaises(Error) as exc: personal_execute(args)
            self.assertEqual(exc.exception.code, 'COMMAND_RETIRED')


if __name__ == '__main__': unittest.main()
