"""Project policy belongs to actual local projects; exercise normal binding and upgrade entry points."""
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.threads import initialize, Workbench
from tools import release_installer


class BoundProjectGuidanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.work = self.base / 'data' / 'work'
        env = patch.dict(os.environ, COSMESH_PAGE_BACKGROUND='0', COSMESH_DATA_DIR=str(self.work.parent),
                         COSMESH_PROFILE_DIR=str(self.base/'profile'))
        env.start()
        self.addCleanup(env.stop)
        initialize(self.work, 'alice')
        wb = Workbench(self.work)
        with wb.base.locked():
            wb.load()
            self.one = wb.create('Research')['id']
            self.two = wb.create('Software')['id']
        self.project = self.base/'project'
        self.project.mkdir()
        self.file = self.project/'AGENTS.md'

    def cli(self, *args, root=None):
        with redirect_stdout(io.StringIO()) as output:
            code = main(['thread', *args, '--offline', '--json', '--root', str(root or self.work)])
        self.assertEqual(code, 0, output.getvalue())
        return json.loads(output.getvalue())['data']

    def bind(self, tid, directory, *flags):
        return self.cli('bind-project', tid, '--agent', 'codex', '--workdir', str(directory),
                        '--kind', 'directory', *flags)['project_guidance']

    def test_binding_preview_retries_multiple_threads_and_session_only(self):
        self.cli('bind', self.one, '--agent', 'codex', '--workdir', str(self.project))
        self.assertFalse(self.file.exists())
        planned = self.bind(self.one, self.project, '--dry-run')
        self.assertEqual(planned['status'], 'created')
        self.assertTrue(planned['dry_run'])
        self.assertFalse(self.file.exists())
        self.assertEqual(self.bind(self.one, self.project)['status'], 'created')
        original = self.file.read_bytes()
        self.assertEqual(self.bind(self.two, self.project)['status'], 'unchanged')
        self.assertEqual(self.file.read_bytes(), original)
        self.assertNotIn(self.one.encode(), original)
        self.assertNotIn(self.two.encode(), original)
        self.file.unlink()
        backfill = self.cli('project-guidance', '--dry-run')
        self.assertEqual(backfill['counts'], {'created': 1})
        self.assertEqual(set(backfill['directories'][0]['threads']), {self.one, self.two})
        self.assertFalse(self.file.exists())
        self.assertEqual(self.cli('project-guidance')['counts'], {'created': 1})
        self.assertEqual(self.file.read_bytes(), original)

    def test_active_local_roots_only_and_retained_builds(self):
        self.bind(self.one, self.project)
        fixture = self.project/'build'/'isolated-checkout'
        fixture.mkdir(parents=True)
        self.assertEqual(self.bind(self.one, fixture)['status'], 'skipped')
        self.assertEqual(self.bind(self.one, self.project/'..'/'project'/'build'/'isolated-checkout')['reason'],
                         'project_build_artifact')
        self.assertEqual(self.bind(self.one, self.project/'..'/'data'/'work')['reason'], 'managed_store')
        self.assertFalse((self.work/'AGENTS.md').exists())
        remote = self.base/'remote'
        remote.mkdir()
        self.assertEqual(self.bind(self.one, remote, '--host', socket.gethostname()+'-other')['status'], 'skipped')
        orphan = self.base/'removed'
        orphan.mkdir()
        self.bind(self.one, orphan)
        self.cli('bind-project', self.one, '--kind', 'directory', '--agent', 'codex',
                 '--workdir', str(orphan), '--remove')
        (orphan/'AGENTS.md').unlink()
        missing = self.base/'missing'
        self.assertEqual(self.bind(self.one, missing)['status'], 'pending')
        receipt = self.cli('project-guidance')
        self.assertEqual(receipt['counts'], {'unchanged': 1, 'skipped': 2, 'pending': 1})
        self.assertFalse(receipt['complete'])
        for path in [fixture, remote, orphan, missing]:
            self.assertFalse((path/'AGENTS.md').exists())
        missing.mkdir()
        self.assertTrue(self.cli('project-guidance')['complete'])

    def test_lightweight_lookup_excludes_archived_removed_and_session_only(self):
        def locate():
            return self.cli('locate', '--bound-projects', '--workdir', str(self.project))['candidates']
        self.cli('bind', self.one, '--agent', 'codex', '--workdir', str(self.project))
        self.assertEqual(locate(), [])
        self.bind(self.one, self.project)
        self.bind(self.two, self.project)
        self.assertEqual({r['thread']: r['title'] for r in locate()}, {self.one:'Research', self.two:'Software'})
        self.cli('archive', self.two)
        self.assertEqual(locate(), [{'thread': self.one, 'title':'Research'}])
        self.cli('bind-project', self.one, '--kind', 'directory', '--agent', 'codex',
                 '--workdir', str(self.project), '--remove')
        self.assertEqual(locate(), [])

    def test_explicit_workdir_disagrees_with_host(self):
        other = self.base/'other'
        other.mkdir()
        observation = self.base/'host.json'
        observation.write_text(json.dumps({
            'format': 1, 'source': 'host_api', 'observed_at': '2026-11-27T01:00:00Z', 'sessions': [],
            'projects': [{'agent':'codex','host':socket.gethostname(),'id':'project-1','name':'Chosen','path':str(other)}]
        }))
        with redirect_stdout(io.StringIO()) as output:
            code = main(['thread', 'bind-project', self.one, '--agent', 'codex', '--project-id', 'project-1',
                         '--project-name', 'Chosen', '--workdir', str(self.project), '--host-file', str(observation),
                         '--root', str(self.work), '--json'])
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['error']['code'], 'STATE_CONFLICT')
        self.assertFalse(self.file.exists())
        self.assertFalse((other/'AGENTS.md').exists())
        self.assertEqual(self.cli('project-guidance')['directories'], [])

    def test_first_activation_and_explicit_isolated_prefix(self):
        prefix = self.base/'install'
        prefix.mkdir()
        state = {'format': 1, 'current': '0.7.1', 'versions': {'0.7.1': {}}, 'skills': {}}
        self.bind(self.one, self.project)
        self.file.unlink()

        def run_new_runtime(command, env):
            self.assertEqual(command[command.index('--root')+1], self.work)
            self.assertEqual(env['COSMESH_INSTALL_PREFIX'], str(prefix))
            return json.dumps({'ok': True, 'data': self.cli('project-guidance')})

        with patch.object(release_installer, 'checked', side_effect=run_new_runtime) as runtime:
            with patch.dict(os.environ):
                os.environ.pop('COSMESH_DATA_DIR')
                self.assertEqual(release_installer.activated_guidance(prefix, state)['status'], 'skipped')
                runtime.assert_not_called()
            result = release_installer.activated_guidance(prefix, state)
            self.assertTrue(result['complete'])
            self.assertEqual(result['counts'], {'created': 1})
            self.assertEqual(release_installer.activated_guidance(prefix, state), result)
            self.assertEqual(runtime.call_count, 1)
            with patch('socket.gethostname', return_value='second-node'):
                second = release_installer.activated_guidance(prefix, state)
                self.assertTrue(second['complete'])
                self.assertEqual(second['counts'], {})
            self.assertEqual(release_installer.activated_guidance(prefix, state), result)
            self.assertEqual(runtime.call_count, 2)
            self.assertEqual(len(state['project_guidance']), 2)
        empty = self.base/'no-store'
        self.assertEqual(self.cli('project-guidance', root=empty)['directories'], [])
        self.assertFalse(empty.exists())
