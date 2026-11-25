"""Workspace projection, named grants, idempotency and revision boundaries on a real Hub."""
import base64
import json
import os
from unittest.mock import patch

from cosmesh.errors import Error
from cosmesh.personal import initialize
from cosmesh.personal_protocol import CHUNK
from cosmesh.personal_relay import initialize as enable_relay
from cosmesh.thread_personal import ThreadPersonal
from cosmesh.thread_protocol import new_id
from tests.test_threads import ThreadFixture


class WorkspaceHubTests(ThreadFixture):
    unified = True

    def setUp(self):
        super().setUp()
        data = patch.dict(os.environ, COSMESH_DATA_DIR=str(self.root / 'device'))
        data.start()
        self.addCleanup(data.stop)
        enable_relay(self.hub)
        for user in self.wbs:
            self.hub.spaces(dict(method='community_name', name=user.upper(), key=user), user)
        with self.use('alice') as wb:
            self.work = wb.create('Workspace', 'Work definition')['id']
            wb.load()
            self.rid = wb.append(self.work, 'Source detail')['id']
            initialize(wb)
            ThreadPersonal(wb).connect()

    def sync(self, files=True):
        with self.use('alice') as wb:
            return ThreadPersonal(wb).synchronize(self.work, files=files)['accepted_snapshot']

    def send(self, snapshot, user='alice', **overrides):
        payload = dict(work=self.work, snapshot=snapshot, grant=True, allow_new=['bob'], to=['bob'],
                       title='Review', body='Please read', files=[], sources=[], key='first')
        payload.update(overrides)
        return self.rpc(user, 'send', **payload)

    def rpc(self, user, method, **args):
        return self.hub.spaces(dict(method='delivery_workspace_' + method, **args), user)

    def denied(self, code, action, *args, **kwargs):
        with self.assertRaises(Error) as caught:
            action(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_latest_fixed_history_page_and_chunk_gate(self):
        source = self.root / 'private-source' / 'results.bin'
        source.parent.mkdir()
        source.write_bytes(b'x' * (CHUNK + 2))
        with self.use('alice') as wb:
            original = ThreadPersonal(wb)
            aid = original.add(self.work, source, relative_path='results/survey.bin')['id']
        first = self.sync()
        result = self.send(first)
        mid, pinned = result['id'], result['workspace']['revision']
        self.assertNotEqual(first, pinned)
        self.assertEqual(self.rpc('bob', 'open', id=mid)['body'], 'Work definition')
        initial = self.rpc('bob', 'records', id=mid, limit=1)
        page = self.rpc('bob', 'records', id=mid, revision=pinned, offset=initial['next_offset'], limit=1)
        self.assertEqual({r['id'] for r in initial['records'] + page['records']}, {self.work, self.rid})
        versions = self.rpc('bob', 'records', id=mid, record=self.rid)['records']
        self.assertEqual(self.rpc('bob', 'open', id=mid, record=self.rid, version=versions[0]['version'])['body'],
                         'Source detail')
        files = self.rpc('bob', 'files', id=mid)
        self.assertEqual(files['files'][0]['relative_path'], 'results/survey.bin')
        self.assertNotIn(str(source.parent), json.dumps(files))
        self.assertNotIn('device', files['files'][0])
        self.assertEqual(base64.b64decode(self.rpc('bob', 'file', id=mid, asset=aid, offset=0)['data']), b'x'*CHUNK)
        self.denied('NOT_FOUND', self.rpc, 'carol', 'open', id=mid)
        with self.use('alice') as wb:
            root_version = wb.ref(wb.find(self.work))['version']
            wb.edit(self.work, root_version, body='Revised goal')
            wb.load()
            latest_id = wb.append(self.work, 'Second update')['id']
        second = self.sync()
        newest = self.rpc('alice', 'publish', work=self.work, snapshot=second)['revision']
        self.assertNotEqual(newest, pinned)
        self.assertEqual(self.rpc('bob', 'open', id=mid)['body'], 'Revised goal')
        self.assertEqual(self.rpc('bob', 'open', id=mid, at_send=True)['body'], 'Work definition')
        self.assertEqual(self.rpc('bob', 'open', id=mid, record=self.work, version=root_version)['body'],
                         'Work definition')
        self.assertEqual(len(self.rpc('bob', 'records', id=mid, record=self.work)['records']), 2)
        self.assertEqual(self.rpc('bob', 'open', id=mid, record=latest_id)['body'], 'Second update')
        self.denied('NOT_FOUND', self.rpc, 'bob', 'open', id=mid, at_send=True, record=latest_id)
        self.denied('STATE_CONFLICT', self.rpc, 'alice', 'publish', work=self.work, snapshot=first)
        late = self.send(first, key='late', grant=False, allow_new=[])
        self.assertEqual(late['workspace']['revision'], pinned)
        self.assertEqual(self.rpc('bob', 'open', id=late['id'])['revision'], newest)
        self.assertTrue(self.send(first)['replayed'])

        self.rpc('alice', 'revoke', work=self.work, to=['bob'])
        self.denied('NOT_FOUND', self.rpc, 'bob', 'file', id=mid, asset=aid, offset=CHUNK)
        self.assertNotIn('workspace', self.hub.spaces(dict(method='delivery_show', id=mid), 'bob'))
        self.assertNotIn('workspace', self.send(first))
        self.denied('AUTH_DENIED', self.send, second, grant=False, allow_new=[], key='second')
        regrant = self.send(second, key='second')
        self.denied('NOT_FOUND', self.rpc, 'bob', 'open', id=mid, at_send=True)
        self.denied('NOT_FOUND', self.rpc, 'bob', 'open', id=regrant['id'], revision=pinned)
        self.assertEqual(self.rpc('bob', 'open', id=regrant['id'])['revision'], newest)

    def test_atomic_denials_sender_scope_and_incomplete_registration(self):
        snap = self.sync()
        self.denied('SOURCE_PENDING', self.send, snap, user='carol')
        self.denied('SOURCE_PENDING', self.send, snap, work=new_id('thread'))
        self.denied('REFERENCE_INVALID', self.send, snap, sources=[dict(record=self.rid, version='a'*64)])
        self.denied('AUTH_DENIED', self.send, snap, to=['bob', 'carol'], allow_new=['bob'])
        self.assertEqual(self.rpc('alice', 'status', work=self.work, to=['bob'])['missing'], ['bob'])
        self.assertFalse(self.hub.spaces(dict(method='delivery_list', folder='inbox'), 'bob')['messages'])

        path = self.root / 'data.txt'
        path.write_text('Selected file')
        with self.use('alice') as wb:
            ThreadPersonal(wb).add(self.work, path)
        incomplete = self.sync(files=False)
        self.denied('SOURCE_PENDING', self.send, incomplete)
        self.assertEqual(self.rpc('alice', 'status', work=self.work, to=[])['active_count'], 0)
        full = self.sync()
        receipt = self.send(full)
        self.assertTrue(receipt['accepted'])
        self.denied('IDEMPOTENCY_CONFLICT', self.send, full, grant=False)
        replay = self.send(full)
        self.assertEqual((receipt['id'], ['bob']), (replay['id'], replay['workspace']['active_recipients']))

    def test_recipient_consent_race_and_shared_quota(self):
        from cosmesh.transfer_policy import PUBLIC

        path = self.root / 'observation.bin'
        path.write_bytes(b'dataset')
        with self.use('alice') as wb:
            ThreadPersonal(wb).add(self.work, path)
        snapshot = self.sync()
        for limit in ('file', 'batch', 'current', 'retained'):
            with self.subTest(limit=limit), patch.dict(PUBLIC, {limit: 1}):
                self.denied('STORAGE_QUOTA', self.send, snapshot)
                self.assertEqual(self.rpc('alice', 'status', work=self.work, to=[])['active_count'], 0)
        first = self.send(snapshot)
        status = self.rpc('alice', 'status', work=self.work, to=['bob', 'carol'])
        self.assertEqual((status['granted'], status['missing']), (['bob'], ['carol']))
        second = dict(key='second', to=['bob','carol'], allow_new=status['missing'])
        self.rpc('alice', 'revoke', work=self.work, to=['bob'])
        self.denied('AUTH_DENIED', self.send, snapshot, **second)
        self.assertEqual(self.rpc('alice', 'status', work=self.work, to=[])['active_count'], 0)

        second['allow_new'] = ['bob', 'carol']
        confirmed = self.send(snapshot, **second)
        for person in ['bob', 'carol']:
            item = self.rpc(person, 'open', id=confirmed['id'])
            self.assertEqual(item['revision'], first['workspace']['revision'])
        self.rpc('alice', 'revoke', work=self.work, to=['bob'])
        replay = self.send(snapshot, **second)
        self.assertEqual(replay['workspace']['active_recipients'], ['carol'])
        self.denied('NOT_FOUND', self.rpc, 'bob', 'open', id=confirmed['id'])
        self.assertEqual(self.rpc('carol', 'open', id=confirmed['id'])['body'], 'Work definition')
