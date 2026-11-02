"""Real local peer credentials, Git streaming and hooks without application keys."""
import json
import os
import select
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from cosmesh.access import system_user, peer_identity
from cosmesh.errors import Error
from cosmesh.gateway import send, receive
from cosmesh.hub import Hub
from cosmesh.service import Service, initialize
from cosmesh.store import Store, atomic
from cosmesh.sync import clone, configure, synchronize, remote_status, git_transport, url


class SystemAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cg-', dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.user = system_user()['user']
        self.hub = Hub(self.folder / 'hub')
        self.hub.initialize()
        self.entry = self.folder / 'entry'
        source = str(Path(__file__).resolve().parents[1])
        code = 'import sys; sys.path.insert(0,%r); from cosmesh.cli import main; raise SystemExit(main())' % source
        self.entry.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-c', code]) + ' "$@"\n')
        self.entry.chmod(0o700)
        self.hub.create_repo('team', 'cosmesh', self.entry, self.user)
        self.hub.grant('team', 'alice', 'write')
        self.discovery = self.folder / 'service.json'
        atomic(self.discovery, json.dumps({'format': 1, 'owner_uid': os.geteuid(), 'endpoint': uuid4().hex,
               'hosts': [socket.gethostname()], 'socket': str(self.folder / 'run/sock')}))
        atomic(self.hub.root / 'gateway.json', json.dumps({'authority': socket.gethostname()}))
        self.process = None
        self.addCleanup(self.stop)
        self.start()
        self.config = {'kind': 'system', 'discovery': str(self.discovery), 'repo': 'team'}
        self.store = Store(self.folder / 'work')
        initialize(self.store, 'cosmesh', self.user, 'Asia/Shanghai')
        configure(self.store, 'cosmesh', self.config)
        self.project = Service(self.store, 'cosmesh').run('project.create', {'title': '共同目标', 'members': ['alice']})[0]['result']
        synchronize(self.store, 'cosmesh')

    def start(self):
        self.process = subprocess.Popen([str(self.entry), 'hub', 'run', '--hub-root', str(self.hub.root),
                                         '--service', str(self.discovery)], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, text=True, start_new_session=True)
        ready, _, _ = select.select([self.process.stderr], [], [], 10)
        self.assertTrue(ready, 'Gateway startup timed out')
        line = self.process.stderr.readline()
        self.assertIn('"ready": true', line, line)

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=10)
            self.process.stderr.close()
            self.process = None

    def update(self, store, title):
        return Service(store, 'cosmesh').run('update.add', {'project': self.project['id'], 'title': title, 'body': title})[0]['result']

    def test_kernel_identity_and_environment_cannot_change_it(self):
        old = os.environ.get('USER')
        os.environ['USER'] = 'alice'
        try:
            value = remote_status(self.config)
            self.assertEqual(value['actor'], self.user)
            self.assertEqual(value['uid'], os.geteuid())
        finally:
            if old is None: os.environ.pop('USER', None)
            else: os.environ['USER'] = old
        a, b = socket.socketpair()
        try:
            self.assertEqual(peer_identity(a)['uid'], os.geteuid())
        finally:
            a.close(); b.close()

    def test_public_mesh_auto_space_and_private_base(self):
        from cosmesh.spaces import run
        root = self.folder / 'automatic'
        with patch.dict(os.environ, {'USER': 'root'}):
            first = run('discover', root, service=str(self.discovery))
        self.assertEqual(first['actor'], self.user)
        self.assertTrue(first['ready'])
        again = run('discover', root, service=str(self.discovery))
        self.assertEqual(first['own_id'], again['own_id'])
        self.assertEqual(Store(root).client()['user'], self.user)
        self.hub.create_repo('private-base', 'cosbase', self.entry, 'root')
        with self.assertRaises(Error):
            self.hub.policy('private-base', self.user)
        self.assertNotIn('private-base', [s['id'] for s in first['spaces']])

    def test_thread_workspace_through_real_kernel_gateway(self):
        from cosmesh.threads import Workbench, initialize as initialize_threads
        from cosmesh.knowledge import Knowledge
        self.hub.initialize_community(self.folder/'knowledge-library')
        self.hub.spaces({'method':'community_name','name':'网关实名验收','key':'kernel-name'},self.user)
        root = self.folder / 'threads'
        initialize_threads(root, self.user)
        wb = Workbench(root)
        with wb.base.locked():
            wb.load()
            found = wb.discover(service=str(self.discovery))
            self.assertEqual(found['actor'], self.user)
            work = wb.create('真实系统身份的工作')
            wb.load(); wb.share(work['id']); wb.synchronize()
            self.assertTrue(wb.context(work['id'])['thread']['accepted'])
            self.assertEqual(wb.context(work['id'])['thread']['author'], self.user)
            kb=Knowledge(wb)
            self.assertEqual(kb.user('网关实名验收')['account'],self.user)
            material=self.folder/'large-text.md';material.write_text('独立知识库的大段文字。\n'*4000)
            files=kb.import_files([material])
            from uuid import uuid4
            resource=kb.stage('resource_add',{'id':'K-'+str(uuid4()),'title':'长文本资料','files':files},'large-text')
            self.assertTrue(kb.synchronize()['synchronized'])
            self.assertTrue(kb.search('大段文字',full_text=True))
            self.assertIn('独立知识库',kb.text(resource['id'])['parts'][0]['text'])
            # Exercise the stdlib SSH proxy code on the same real kernel socket.
            from cosmesh import spaces
            original=subprocess.Popen
            def local_remote(arguments,**kwargs):
                if arguments[0]=='ssh':arguments=shlex.split(arguments[-1])
                return original(arguments,**kwargs)
            with patch('cosmesh.sync.control_options',return_value=[]), patch('cosmesh.connections.subprocess.Popen',side_effect=local_remote):
                reply=spaces.rpc({'kind':'ssh-system','host':'verified-test','discovery':str(self.discovery),'repo':'spaces'},
                                 {'method':'community_get','sha256':files[0]['sha256'],'offset':0})
            self.assertGreater(len(reply['data']),8192)

    def test_public_fetch_retains_document_write_roles(self):
        from cosmesh.spaces import run
        other = self.hub.spaces({'method': 'ensure'}, 'root')['own_id']
        seed = Store(self.folder / 'root-work')
        subprocess.run(['git', 'clone', '-q', str(self.hub.repo(other).root), str(seed.root)], check=True)
        project = Service(seed, 'cosmesh').run('project.create', {'title': '公开协作', 'members': [self.user]})[0]['result']
        self.hub.repo(other).git('fetch', '--no-tags', str(seed.root), 'HEAD:refs/heads/main')
        fetched = run('fetch', self.folder / 'reader', service=str(self.discovery), user='root')
        work = Store(Path(fetched['root']))
        svc = Service(work, 'cosmesh')
        document = svc.run('doc.show', {'id': project['id']})[0]
        with self.assertRaises(Error):
            svc.run('doc.edit', {'id': project['id'], 'expect': document['version'], 'title': '越权'})
        task = svc.run('task.create', {'project': project['id'], 'title': '按成员规则创建行动'})[0]['result']
        synchronize(work, 'cosmesh')
        accepted = self.hub.repo(other).documents(self.hub.repo(other).snapshot()[1])
        self.assertEqual(accepted[task['id']]['meta']['author'], self.user)

    def test_real_git_clone_divergence_and_generic_records(self):
        other_root = self.folder / 'other'
        clone(other_root, 'cosmesh', self.config)
        other = Store(other_root)
        left, right = self.update(self.store, '采购讨论'), self.update(other, '活动安排')
        synchronize(self.store, 'cosmesh')
        synchronize(other, 'cosmesh')
        synchronize(self.store, 'cosmesh')
        self.assertEqual(other.head, self.store.head)
        docs = self.store.documents(self.store.snapshot()[1])
        self.assertIn(left['id'], docs); self.assertIn(right['id'], docs)
        self.assertEqual(docs[right['id']]['meta']['author'], self.user)

    def test_no_self_reported_uid_or_arbitrary_shell_request(self):
        for extra, code in [({'uid': 0}, 'SYNC_PROTOCOL'), ({'operation': 'shell'}, 'COMMAND_DENIED')]:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                connection.connect(json.loads(self.discovery.read_text())['socket'])
                send(connection, {'protocol': 1, 'repo': 'team', 'operation': 'cosmesh-status', **extra})
                reply = receive(connection)
                self.assertFalse(reply['ok'])
                self.assertEqual(reply['error']['code'], code)
            finally:
                connection.close()

    def test_forged_document_author_is_denied_by_hook(self):
        client = self.store.client(); client['user'] = 'alice'; self.store.save_client(client)
        self.update(self.store, '冒用系统身份')
        before = self.hub.status('team', self.user)['revision']
        result = self.store.git('push', '--porcelain', url(self.config), 'HEAD:refs/heads/main',
                                extra_env=git_transport(self.config), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('AUTH_DENIED', result.stderr)
        self.assertEqual(self.hub.status('team', self.user)['revision'], before)

    def test_offline_commits_survive_gateway_restart(self):
        self.stop()
        update = self.update(self.store, '离线保留输入')
        with self.assertRaises(Error) as caught:
            synchronize(self.store, 'cosmesh')
        self.assertEqual(caught.exception.code, 'SYNC_NETWORK')
        self.assertIn(update['id'], self.store.documents(self.store.snapshot()[1]))
        self.start()
        synchronize(self.store, 'cosmesh')
        self.assertEqual(self.store.head, self.hub.status('team', self.user)['revision'])

    def test_read_only_acl_and_discovery_tampering(self):
        self.hub.grant('team', self.user, 'read')
        self.update(self.store, '只读用户的本地草稿')
        with self.assertRaises(Error) as caught:
            synchronize(self.store, 'cosmesh')
        self.assertEqual(caught.exception.code, 'SYNC_REJECTED')
        self.discovery.chmod(0o666)
        try:
            with self.assertRaises(Error) as caught:
                remote_status(self.config)
            self.assertEqual(caught.exception.code, 'AUTH_DENIED')
        finally:
            self.discovery.chmod(0o600)

    def test_init_uses_system_identity_without_user_argument(self):
        path = self.folder / 'new-local'
        result = subprocess.run([str(self.entry), '--root', str(path), 'init', '--json'],capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['data']['config']['user'], self.user)

    def test_unrelated_legacy_connection_is_not_silently_retargeted(self):
        identity, known = self.folder / 'identity', self.folder / 'known'
        for file in (identity, known):
            file.write_text('isolated fixture'); file.chmod(0o600)
        legacy = {'host': 'another-hub', 'login': self.user, 'repo': 'team', 'port': 22,
                  'identity': str(identity), 'known_hosts': str(known), 'host_key_alias': 'another-hub'}
        client = self.store.client(); client['remote'] = legacy; self.store.save_client(client)
        seen = []
        def stop(config):
            seen.append(config)
            raise Error('EXPECTED_STOP', 'No network request in this test.')
        with patch('cosmesh.access.DEFAULT_DISCOVERY', str(self.discovery)), patch('cosmesh.sync.remote_status', stop):
            with self.assertRaises(Error):
                synchronize(self.store, 'cosmesh')
        self.assertEqual(seen[0], legacy)
        self.assertEqual(self.store.client()['remote'], legacy)


if __name__ == '__main__':
    unittest.main()
