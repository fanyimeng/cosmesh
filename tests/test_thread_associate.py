"""Evidence-based, explicitly confirmed execution association for existing Threads."""
import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh.agent_projects import active, bind as bind_project
from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.thread_execution_associate import confirm as confirm_association
from cosmesh.thread_updates import signature
from cosmesh.threads import Workbench, initialize


class AssociateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); initialize(self.root/'work', 'alice')
        self.w = Workbench(self.root/'work')
        with self.w.base.locked():
            self.w.load(); self.tid = self.w.create('Existing work')['id']; self.w.load()
            self.w.bind(self.tid, 'codex', str(self.root), 'session-one')

    def host(self, projects=1, direct=True):
        return {'format': 1, 'source': 'supported_host_api', 'observed_at': '2026-01-02T12:00:00+00:00',
                'sessions': [{'agent': 'codex', 'host': socket.gethostname(), 'id': 'session-one',
                              'title': 'private session title', 'project_id': 'project-one' if direct else None,
                              'cwd': str(self.root), 'status': 'idle', 'updated_at': '2026-01-02T11:00:00Z'}],
                'projects': [{'agent': 'codex', 'host': socket.gethostname(), 'id': 'project-one' if index == 0 else 'project-two',
                              'name': 'CosMesh' if index == 0 else 'Other', 'path': str(self.root)}
                             for index in range(projects)]}

    def call(self, *args, product='cosmesh'):
        with redirect_stdout(io.StringIO()) as output:
            code = main(['thread', *args, '--root', str(self.w.base.root), '--json'], product=product)
        value = json.loads(output.getvalue())
        self.assertEqual(code, 0, value)
        return value['data']

    def preview(self, value):
        path = self.root/'observed.json'; path.write_text(json.dumps(value))
        return self.call('associate', self.tid, '--host-file', str(path))

    def test_unique_preview_has_no_writes_and_confirmation_is_idempotent(self):
        with self.w.base.locked():
            self.w.load(); head, client = self.w.base.head, self.w.path.read_bytes()
            before = signature(self.w, self.tid)
        result = self.preview(self.host())
        self.assertEqual(result['status'], 'confirmation_required')
        candidate = result['candidates'][0]
        self.assertEqual(candidate['project']['id'], 'project-one')
        self.assertEqual(candidate['project']['name'], 'CosMesh')
        self.assertEqual(result['execution_revision'], candidate['base_revision'])
        self.assertEqual(result['thread_basis'], before)
        shared = json.dumps(result['shared_summary_preview'])
        for secret in (str(self.root), 'session-one', 'private session title', 'workdir', 'session_id'):
            self.assertNotIn(secret, shared)
        with self.w.base.locked():
            self.w.load(); self.assertEqual(self.w.base.head, head); self.assertEqual(self.w.path.read_bytes(), client)
            self.assertEqual(len(self.w.records(self.tid)), 0)
        confirmed = self.call('associate', self.tid, '--host-file', str(self.root/'observed.json'),
                              '--confirm', candidate['candidate_id'],
                              '--expect', result['execution_revision'], '--idempotency-key', 'associate-one')
        self.assertEqual(confirmed['status'], 'applied')
        self.assertEqual(confirmed['thread'], self.tid)
        self.assertEqual(confirmed['association']['project_id'], 'project-one')
        self.assertEqual(confirmed['association']['session_id'], 'session-one')
        with self.w.base.locked():
            self.w.load(); projects = active(self.w, self.tid)
            self.assertEqual(len(projects), 1); self.assertEqual(projects[0]['project_id'], 'project-one')
            self.assertEqual(len(self.w.records(self.tid)), 0)
        replay = self.call('associate', self.tid, '--host-file', str(self.root/'observed.json'),
                           '--confirm', candidate['candidate_id'],
                           '--expect', result['execution_revision'], '--idempotency-key', 'associate-one')
        self.assertTrue(replay['replayed'])
        with self.w.base.locked():
            self.w.load(); self.assertEqual(len(active(self.w, self.tid)), 1)
            self.assertEqual(len(self.w.records(self.tid)), 0)

    def test_selected_connector_journal_requires_host_verification(self):
        with self.w.base.locked():
            self.w.load(); tid = self.w.create('Recovered Connector work')['id']; self.w.load()
        journal = self.root/'journal.json'
        journal.write_text(json.dumps({'format': 1,
            'input': {'thread_id': tid, 'workdir': str(self.root)},
            'owner': {'host': socket.gethostname(), 'namespace': 'desktop'},
            'sessionId': 'session-one', 'workspaceId': 'project-one', 'workdir': str(self.root),
            'projectTitle': 'CosMesh', 'handoff': 'private handoff must not be returned',
            'steps': {'workspace': True, 'session': True, 'project_binding': False, 'session_binding': False}}))
        journal.chmod(0o600)
        observed_data = self.host()
        for key in ('sessions', 'projects'):
            for row in observed_data[key]: row['agent'] = 'dsh'
        observed = self.root/'observed.json'; observed.write_text(json.dumps(observed_data))
        result = self.call('associate', tid, '--host-file', str(observed), '--journal', str(journal))
        self.assertEqual(result['status'], 'confirmation_required', result)
        candidate = result['candidates'][0]
        self.assertEqual(candidate['evidence'][0]['code'], 'connector_journal_host_verified')
        self.assertNotIn('private handoff', json.dumps(result))
        confirmed = self.call('associate', tid, '--host-file', str(observed), '--journal', str(journal),
                              '--confirm', candidate['candidate_id'], '--expect', result['execution_revision'],
                              '--idempotency-key', 'associate-journal')
        self.assertEqual(confirmed['status'], 'applied')
        with self.w.base.locked():
            self.w.load(); self.assertEqual(len(active(self.w, tid)), 1)
            self.assertTrue(any(row['thread'] == tid and row['session_id'] == 'session-one' for row in self.w.client['bindings']))

    def test_missing_lists_only_unassociated_threads(self):
        result = self.call('associate', '--missing')
        self.assertIn(self.tid, {row['thread'] for row in result['threads']})
        self.assertFalse(result['automatic_association'])
        confirmed = self.preview(self.host())
        candidate = confirmed['candidates'][0]
        self.call('associate', self.tid, '--host-file', str(self.root/'observed.json'),
                  '--confirm', candidate['candidate_id'], '--expect', confirmed['execution_revision'],
                  '--idempotency-key', 'associate-missing')
        result = self.call('associate', '--missing')
        self.assertNotIn(self.tid, {row['thread'] for row in result['threads']})

    def test_ambiguous_and_weak_evidence_cannot_be_confirmed(self):
        ambiguous = self.preview(self.host(projects=2, direct=False))
        self.assertEqual(ambiguous['status'], 'ambiguous')
        self.assertFalse(any('candidate_id' in row for row in ambiguous['candidates']))
        with self.w.base.locked():
            self.w.load(); self.assertEqual(active(self.w, self.tid), [])
        with self.w.base.locked():
            self.w.load(); other = self.w.create('CosMesh')['id']; self.w.load()
            self.w.generation = {'tool': 'not-an-execution-agent'}
            self.w.append(other, 'Generated near a project name'); self.w.load()
        empty = self.root/'empty.json'; empty.write_text(json.dumps({'format': 1, 'source': 'none',
            'observed_at': '2026-01-02T12:00:00+00:00', 'sessions': [], 'projects': []}))
        result = self.call('associate', other, '--host-file', str(empty))
        self.assertEqual(result['status'], 'no_candidates')
        with self.w.base.locked():
            self.w.load(); self.assertEqual(active(self.w, other), [])

    def test_mixed_evidence_and_remote_same_hostname(self):
        observed = self.host()
        local = observed['sessions'][0]
        directory = self.root/'second'
        directory.mkdir()
        with self.w.base.locked():
            self.w.load()
            self.w.bind(self.tid, 'codex', str(directory), 'loose')
        observed['projects'].append(dict(observed['projects'][0], id='two', path=str(directory)))
        observed['sessions'].append(dict(local, id='loose', cwd=str(directory), project_id=None))
        observed['sessions'].append(dict(local, id='remote'))
        remote = {'kind': 'bindings', 'device': 'another-device', 'replica': 'another-replica',
                  'binding': {'thread': self.tid, 'host': local['host'], 'agent': 'codex',
                              'session_id': 'remote', 'workdir': str(self.root)}}
        from cosmesh import thread_execution_associate as assoc
        original = assoc.raw_locations
        with patch.object(assoc, 'raw_locations', lambda wb, tid: dict(original(wb, tid), remote=remote)):
            result = self.preview(observed)
        by_session = {c['session_id']: c for c in result['candidates']}
        self.assertEqual(set(by_session), {'session-one', 'loose'})
        self.assertTrue(by_session['session-one']['confirmable'])
        self.assertNotIn('candidate_id', by_session['loose'])
        self.assertFalse(by_session['loose']['confirmable'])

    def test_interrupted_receipt_recovers_without_duplicate_association(self):
        result = self.preview(self.host())
        candidate = result['candidates'][0]
        host = self.host()
        with self.w.base.locked():
            self.w.load()
            with patch.object(self.w.base, 'commit', side_effect=Error('TEST_INTERRUPT', 'after private save')):
                with self.assertRaises(Error) as error:
                    confirm_association(self.w, self.tid, candidate['candidate_id'],
                                        result['execution_revision'], host, 'associate-interrupted')
            self.assertEqual(error.exception.code, 'TEST_INTERRUPT')
            self.assertEqual(len(active(self.w, self.tid)), 1)
            self.assertEqual(len(self.w.records(self.tid)), 0)
        recovered = self.call('associate', self.tid, '--host-file', str(self.root/'observed.json'),
                              '--confirm', candidate['candidate_id'], '--expect', result['execution_revision'],
                              '--idempotency-key', 'associate-interrupted')
        self.assertEqual(recovered['status'], 'unchanged')
        replay = self.call('associate', self.tid, '--host-file', str(self.root/'observed.json'),
                           '--confirm', candidate['candidate_id'], '--expect', result['execution_revision'],
                           '--idempotency-key', 'associate-interrupted')
        self.assertTrue(replay['replayed'])
        with self.w.base.locked():
            self.w.load(); self.assertEqual(len(active(self.w, self.tid)), 1)
            self.assertEqual(len(self.w.records(self.tid)), 0)

    def test_stale_revision_and_other_association_are_rejected(self):
        result = self.preview(self.host())
        candidate = result['candidates'][0]
        with self.w.base.locked():
            self.w.load(); bind_project(self.w, self.tid, 'codex', 'Other', 'other-project', workdir=str(self.root))
        with redirect_stdout(io.StringIO()) as output:
            code = main(['thread', 'associate', self.tid, '--host-file', str(self.root/'observed.json'),
                         '--confirm', candidate['candidate_id'],
                         '--expect', result['execution_revision'], '--idempotency-key', 'associate-stale',
                         '--root', str(self.w.base.root), '--json'])
        value = json.loads(output.getvalue())
        self.assertEqual(code, 4, value); self.assertEqual(value['error']['code'], 'STATE_CONFLICT', value)
        with self.w.base.locked():
            self.w.load(); self.assertEqual(len(active(self.w, self.tid)), 1)
