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

    def grant(self, tid, mode='public', readers=()):
        args = ['access', tid, '--visibility', mode]
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
        self.assertEqual(metadata['format'], 'thread.execution.v2')
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
        self.assertEqual(context['project'], {'kind': 'workspace', 'id': 'project-one', 'name': 'CosMesh'})
        self.assertTrue(context['device']['id']); self.assertTrue(context['device']['host'])
        detail = self.command('bob', 'browse', tid)
        self.assertEqual(self.assert_safe_shared(detail, 1)['execution_revision'], metadata['execution_revision'])
        with self.use('bob') as wb:
            self.assertEqual(shared_execution(wb, tid)['execution_revision'], metadata['execution_revision'])
        with self.assertRaises(Error) as error: self.command('carol', 'browse', tid)
        self.assertEqual(error.exception.code, 'NOT_FOUND')
        self.grant(tid)
        public = self.command('carol', 'browse', tid)
        self.assert_safe_shared(public, 1)
        self.grant(tid, 'private')
        with self.assertRaises(Error): self.command('bob', 'browse', tid)

    def test_fixed_snapshot_empty_directory_and_execution_revision(self):
        empty = self.work('Empty execution')
        self.grant(empty)
        metadata = self.assert_safe_shared(self.command('bob', 'browse', empty), 0)
        self.assertEqual(metadata['contexts'], [])
        tid = self.work('Versioned execution')
        with self.use('alice') as wb:
            bind_project(wb, tid, 'codex', 'students', None, kind='directory', workdir=str(self.root/'secret-directory'))
        first = self.grant(tid)
        old = self.command('bob', 'browse', tid)
        old_metadata = self.assert_safe_shared(old, 1)
        self.assertEqual(old_metadata['contexts'][0]['project']['id'], None)
        self.assertEqual(old_metadata['contexts'][0]['project']['name'], 'students')
        with self.use('alice') as wb:
            bind_project(wb, tid, 'codex', 'CosMesh', 'native-two', kind='project', workdir=str(self.root/'other-secret'))
        self.command('alice', 'sync', tid)
        current = self.command('bob', 'browse', tid)
        current_metadata = self.assert_safe_shared(current, 2)
        self.assertNotEqual(current_metadata['execution_revision'], old_metadata['execution_revision'])
        changes = self.command('bob', 'browse', tid, '--changes', first['content_revision'])
        event = next(row for row in changes['changes'] if row['kind'] == 'execution')
        self.assertEqual(event['previous_execution_revision'], old_metadata['execution_revision'])
        self.assertEqual(event['execution_revision'], current_metadata['execution_revision'])
        self.assert_safe_shared({'execution_metadata': event['execution_metadata']}, 2)
        pinned = self.command('bob', 'browse', tid, '--snapshot', first['content_revision'])
        self.assertEqual(self.assert_safe_shared(pinned, 1)['execution_revision'], old_metadata['execution_revision'])
        self.grant(tid, 'private')
        with self.assertRaises(Error): self.command('bob', 'browse', tid, '--snapshot', first['content_revision'])
