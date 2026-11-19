"""Shared execution summaries follow the current Thread ACL and fixed snapshots."""
import json
import os
from unittest.mock import patch

from tests.test_threads import ThreadFixture
from cosmesh.agent_projects import bind as bind_project
from cosmesh.cli import parser
from cosmesh.errors import Error
from cosmesh.personal import initialize
from cosmesh.personal_relay import initialize as enable
from cosmesh.thread_cli import execute
from cosmesh.thread_execution_reader import shared_execution
from cosmesh.thread_overview import overview
from cosmesh.thread_personal import ThreadPersonal


class ExecutionAccessTests(ThreadFixture):
    def setUp(self):
        super().setUp(); enable(self.hub)
        env = patch.dict(os.environ, {'COSMESH_DATA_DIR': str(self.root/'device-data'),
                                     'COSMESH_PAGE_BACKGROUND': '0'})
        env.start(); self.addCleanup(env.stop)
        self.hub.initialize_community(self.root/'library')
        for user in self.wbs:
            self.hub.spaces({'method': 'community_name', 'name': user.upper(), 'key': 'name-'+user}, user)

    def command(self, user, *args):
        wb = self.wbs[user] if isinstance(user, str) else user
        return execute(parser('cosmesh').parse_args(['thread', *args, '--root', str(wb.base.root)]))[0]

    def work(self, title='Execution metadata'):
        with self.use('alice') as wb:
            tid = wb.create(title, 'public work body')['id']; wb.load()
            initialize(wb)
            personal = ThreadPersonal(wb)
            personal.state.update(connection=self.connection('alice'), actor='alice'); personal.save()
            return tid

    def grant(self, tid, mode='public', readers=(), approve=True):
        args = ['access', tid, '--visibility', mode]
        if approve and mode != 'private': args.append('--share-projects')
        for person in readers: args.extend(['--to', person])
        return self.command('alice', *args)

    def bind(self, tid, project_id='project-one', name='CosMesh', kind='workspace', workdir=None, session='secret-session'):
        with self.use('alice') as wb:
            directory = workdir or self.root/'private-project'; directory.mkdir(exist_ok=True)
            root = str(directory)
            bind_project(wb, tid, 'DeepSeek Harness', name, project_id, kind=kind, workdir=root)
            wb.bind(tid, 'dsh', root, session)
            wb.load()

    def assert_safe_shared(self, value, expected_projects):
        encoded = json.dumps(value, ensure_ascii=False)
        for secret in (str(self.root), 'private-project', 'secret-session', 'session_id', 'workdir', 'replica'):
            self.assertNotIn(secret, encoded)
        metadata = value['execution_metadata']
        self.assertEqual(metadata['format'], 'thread.projects.v1')
        self.assertEqual(metadata['audience'], 'shared')
        self.assertEqual(metadata['summary']['project_count'], expected_projects)
        self.assertNotIn('session_count', metadata['summary'])
        return metadata

    def test_public_and_restricted_readers_get_only_shared_projection(self):
        tid = self.work(); self.bind(tid)
        self.grant(tid, 'restricted', ('bob',))
        with self.use('alice') as wb:
            owner = overview(wb, tid)['threads'][0]['execution_metadata']
            self.assertEqual(owner['audience'], 'owner')
            self.assertEqual(owner['summary']['session_count'], 1)
            self.assertEqual(owner['contexts'][0]['workdir'], str(self.root/'private-project'))
        locations = self.command('alice', 'locations', tid)
        self.assertEqual(locations['execution_metadata']['execution_revision'], owner['execution_revision'])
        local_list = self.command('alice', 'list')
        self.assertEqual(next(row for row in local_list if row['id'] == tid)['execution_metadata']['execution_revision'], owner['execution_revision'])
        listed = self.command('bob', 'browse')
        row = next(row for row in listed['threads'] if row['thread'] == tid)
        metadata = self.assert_safe_shared(row, 1)
        context = metadata['contexts'][0]
        self.assertEqual(context['agent'], 'dsh')
        self.assertEqual(context['project'], {'kind': 'workspace', 'name': 'CosMesh'})
        self.assertEqual(set(context), {'agent', 'project'})
        detail = self.command('bob', 'browse', tid)
        self.assertEqual(self.assert_safe_shared(detail, 1)['shared_revision'], metadata['shared_revision'])
        with self.use('bob') as wb:
            self.assertEqual(shared_execution(wb, tid)['shared_revision'], metadata['shared_revision'])
        with self.assertRaises(Error) as error: self.command('carol', 'browse', tid)
        self.assertEqual(error.exception.code, 'NOT_FOUND')
        self.grant(tid)
        public = self.command('carol', 'browse', tid)
        self.assert_safe_shared(public, 1)
        self.grant(tid, 'private')
        with self.assertRaises(Error): self.command('bob', 'browse', tid)

    def test_old_hub_without_execution_metadata_is_tolerated(self):
        tid = self.work(); self.bind(tid); self.grant(tid)
        from cosmesh.thread_access_cli import Online as OnlineClient
        real = OnlineClient.call
        def legacy(client, method, **payload):
            result = real(client, method, **payload)
            for row in result.get('threads', []): row.pop('execution_metadata', None)
            result.pop('execution_metadata', None)
            return result
        with patch.object(OnlineClient, 'call', legacy):
            listed = self.command('bob', 'browse')
            row = next(item for item in listed['threads'] if item['thread'] == tid)
            self.assertNotIn('execution_metadata', row)
            detail = self.command('bob', 'browse', tid)
            self.assertNotIn('execution_metadata', detail)
        with self.use('bob') as wb:
            self.assertIsNone(shared_execution(wb, tid))
        current = self.command('bob', 'browse')
        self.assertIn('execution_metadata', next(item for item in current['threads'] if item['thread'] == tid))

    def test_fixed_snapshot_empty_directory_and_execution_revision(self):
        tid = self.work('Fixed scopes')
        self.bind(tid)
        legacy = self.grant(tid, approve=False)
        original = legacy['content_revision']
        self.assertNotIn('execution_metadata', self.command('bob', 'browse', tid))
        first = self.grant(tid)
        self.assertNotEqual(original, first['content_revision'])
        self.assertNotIn('execution_metadata', self.command('bob', 'browse', tid, '--snapshot', original))
        metadata = self.assert_safe_shared(first, 1)
        self.bind(tid, project_id='project-two', name='New private project', session='secret-session-2')
        sync = self.command('alice', 'sync', tid)['access_update']
        self.assertEqual((sync['content_revision'], sync['acl_revision']),
                         (first['content_revision'], first['acl_revision']))
        self.assertEqual(self.command('bob', 'browse', tid, '--changes', first['content_revision'])['changes'], [])
        with self.use('alice') as wb:
            wb.append(tid, 'New public record')
        public = self.command('alice', 'sync', tid)['access_update']
        self.assertNotEqual(public['content_revision'], first['content_revision'])
        self.assertEqual(public['execution_metadata'], metadata)
        second = self.grant(tid)
        self.assert_safe_shared(second, 2)
        delta = self.command('bob', 'browse', tid, '--changes', first['content_revision'])
        self.assertEqual(sum(e['kind'] == 'execution' for e in delta['changes']), 1)
        pinned = self.command('bob', 'browse', tid, '--snapshot', first['content_revision'])
        self.assertEqual(pinned['execution_metadata'], metadata)
        self.assertNotIn('execution_metadata', self.command('bob', 'browse', tid, '--snapshot', original))
        self.grant(tid, 'private')
        with self.assertRaises(Error): self.command('bob', 'browse', tid, '--snapshot', original)
