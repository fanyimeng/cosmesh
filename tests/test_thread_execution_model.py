"""Pure execution event folding, deterministic revisions, and projection privacy."""
import copy
import json
import random
import unittest

from cosmesh.thread_execution_model import build_execution_model
from cosmesh.thread_execution_projection import compute_shared_revision, owner_projection, shared_projection, validate_shared_projection


T1 = '2026-01-01T01:00:00+00:00'
T2 = '2026-01-01T02:00:00+00:00'
T3 = '2026-01-01T03:00:00+00:00'
OBSERVED = '2026-01-02T00:00:00Z'


def project(location, device='device-a', agent='dsh', host='host-a', workdir='/work/repo',
            project_id='project-a', kind='project', name='Project A', at=T1, **extra):
    binding = {'thread': 'W-test', 'agent': agent, 'host': host, 'workdir': workdir,
               'kind': kind, 'name': name, 'project_id': project_id, 'status': 'registered',
               'removed': False, 'at': at}
    binding.update(extra)
    return {'id': location, 'device': device, 'replica': 'replica-' + location,
            'kind': 'project_bindings', 'binding': binding}


def session(location, device='device-a', agent='dsh', host='host-a', workdir='/work/repo',
            session_id='session-a', **extra):
    binding = {'thread': 'W-test', 'agent': agent, 'host': host,
               'workdir': workdir, 'session_id': session_id}
    binding.update(extra)
    return {'id': location, 'device': device, 'replica': 'replica-' + location,
            'kind': 'bindings', 'binding': binding}


class ExecutionModelTests(unittest.TestCase):
    def test_empty_input_and_public_shared_revision(self):
        for rows in ([], {}, iter(())):
            shared = shared_projection(rows)
            self.assertEqual(shared['format'], 'thread.projects.v1')
            self.assertEqual(shared['audience'], 'shared')
            self.assertEqual(shared['contexts'], [])
            self.assertNotIn('removed_count', shared)
            self.assertEqual(shared['summary']['project_count'], 0)
            self.assertNotIn('session_count', shared['summary'])
            self.assertEqual(shared['shared_revision'], compute_shared_revision(shared))
        owner = owner_projection([])
        self.assertEqual(owner['shared_revision'], shared_projection([])['shared_revision'])
        self.assertIsNone(owner['current_device'])

    def test_directory_lexical_path_agent_and_legacy_host_fallback(self):
        row = project('directory', device=None, agent='DeepSeek Harness', host='Legacy-Host',
                      workdir='/work/repo/./child/..', project_id=None, kind='directory', name='repo')
        owner = owner_projection([row], current_device='device-a')
        context = owner['contexts'][0]
        self.assertEqual(context['device'], {'id': None, 'host': 'Legacy-Host'})
        self.assertEqual(context['agent'], 'dsh')
        self.assertEqual(context['workdir'], '/work/repo')
        self.assertEqual(context['project'], {'kind': 'directory', 'id': None, 'name': 'repo'})
        self.assertFalse(context['current_device'])
        self.assertEqual(context['recovery']['identity_quality'], 'directory_workdir')

    def test_native_id_ignores_project_workspace_kind_and_keeps_observation(self):
        rows = [
            project('one', kind='project', at=T1),
            project('two', kind='workspace', at=T2, status='verified', observed_at=OBSERVED,
                    source='host-api', verification='personal_agent_reported'),
            # A later registration changes attributes but cannot erase older evidence.
            project('three', kind='project', at=T3),
        ]
        manifest = {row['id']: row for row in rows}
        owner = owner_projection(manifest)
        self.assertEqual(len(owner['contexts']), 1)
        context = owner['contexts'][0]
        self.assertEqual(context['project']['kind'], 'project')
        self.assertEqual(context['project']['id'], 'project-a')
        self.assertEqual(context['status'], 'verified')
        self.assertEqual(context['observed_at'], '2026-01-02T00:00:00+00:00')
        self.assertEqual(context['source'], 'host-api')
        self.assertEqual(context['verification'], 'personal_agent_reported')

    def test_directory_is_a_session_clue_not_project_membership(self):
        rows = [
            project('directory', project_id=None, kind='directory', name='fallback', at=T1),
            project('workspace', project_id='native-workspace', kind='workspace', name='Native', at=T2,
                    workdir='/work/./repo'),
            session('session', workdir='/work/repo/../repo'),
        ]
        owner = owner_projection(rows)
        self.assertEqual(len(owner['contexts']), 2)
        native = next(c for c in owner['contexts'] if c['project']['id'])
        self.assertEqual(native['sessions'], [])
        directory = next(c for c in owner['contexts'] if c['sessions'])
        self.assertEqual(directory['project']['kind'], 'directory')
        self.assertEqual(directory['sessions'][0]['project_match'], 'directory_clue')

    def test_bare_directory_two_sessions_and_multiple_project_paths(self):
        bare = owner_projection([session('dir', session_id=None)])['contexts'][0]
        self.assertEqual((bare['project'], bare['workdir'], bare['sessions']), (None, '/work/repo', []))
        rows = [session('one'), session('two', session_id='other')]
        ctx = owner_projection(rows)['contexts'][0]
        self.assertIsNone(ctx['project'])
        self.assertEqual([s['project_match'] for s in ctx['sessions']], ['unmatched'] * 2)
        projects = [project('a', at=T1), project('b', at=T2, workdir='/other')]
        self.assertEqual(owner_projection(projects)['contexts'][0]['workdirs'], ['/other', '/work/repo'])
        self.assertEqual(shared_projection(projects + rows), shared_projection(projects[:1]))

    def test_same_project_on_multiple_agents_does_not_merge(self):
        rows = [project('dsh', agent='DeepSeek-Harness'), project('codex', agent='Codex')]
        owner = owner_projection(rows)
        self.assertEqual(len(owner['contexts']), 2)
        self.assertEqual([context['agent'] for context in owner['contexts']], ['codex', 'dsh'])
        self.assertEqual(owner['summary']['agent_count'], 2)

    def test_same_directory_multiple_projects_and_session_are_ambiguous(self):
        rows = [project('one', project_id='project-one'), project('two', project_id='project-two'), session('session')]
        owner = owner_projection(rows)
        native = [context for context in owner['contexts'] if context['project'] is not None]
        self.assertEqual(len(native), 2)
        self.assertTrue(all(context['recovery']['directory_ambiguous'] for context in native))
        session_context = next(context for context in owner['contexts'] if context['sessions'])
        self.assertIsNone(session_context['project'])
        self.assertEqual(session_context['sessions'][0]['project_match'], 'ambiguous')
        self.assertIn('project_id', {conflict['field'] for conflict in session_context['conflicts']})

    def test_null_session_is_not_a_session(self):
        owner = owner_projection([project('project'), session('null', session_id=None)])
        self.assertEqual(owner['summary']['session_count'], 0)
        self.assertEqual(owner['contexts'][0]['sessions'], [])

    def test_removed_tombstone_is_counted_not_active(self):
        rows = [project('active', project_id='keep'), project('before', at=T1),
                project('removed', at=T2, removed=True, name='Project A')]
        owner = owner_projection(rows)
        self.assertEqual([c['project']['id'] for c in owner['contexts']], ['keep'])
        self.assertEqual(owner['removed_count'], 1)
        self.assertEqual(shared_projection(rows), shared_projection(rows[:1]))

    def test_cross_device_project_and_session_identity_and_current_device(self):
        rows = []
        for device in ('device-a', 'device-b'):
            rows.extend([project('project-' + device, device=device), session('session-' + device, device=device)])
        owner = owner_projection(rows, current_device='device-b')
        self.assertEqual(owner['summary']['device_count'], 2)
        self.assertEqual(owner['summary']['session_count'], 2)
        self.assertTrue(all(c['current_device'] == (c['device']['id'] == 'device-b') for c in owner['contexts']))

    def test_shared_recursive_canary_and_key_whitelist(self):
        canaries = ('CANARY_WORKDIR', 'CANARY_SESSION', 'CANARY_REPLICA', 'CANARY_URL', 'CANARY_CURRENT')
        project_row = project('private', workdir='/private/CANARY_WORKDIR', replica='ignored',
                              url='https://CANARY_URL.invalid', journal='CANARY_JOURNAL', handoff='CANARY_HANDOFF')
        project_row['replica'] = 'CANARY_REPLICA'
        project_row['current_device'] = 'CANARY_CURRENT'
        session_row = session('private-session', workdir='/private/CANARY_WORKDIR', session_id='CANARY_SESSION',
                              url='https://CANARY_URL.invalid', operation_key='CANARY_OPERATION')
        shared = shared_projection([project_row, session_row])
        from cosmesh.status_receipts import project_text
        self.assertEqual(project_text({'execution_metadata': shared}, False), 'DSH · Project A')
        encoded = json.dumps(shared)
        for canary in canaries:
            self.assertNotIn(canary, encoded)
        forbidden = {'workdir', 'session', 'sessions', 'replica', 'url', 'current_device',
                     'conflicts', 'recovery', 'journal', 'handoff', 'operation_key', 'thread'}
        def walk(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden & set(value))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(shared)
        for context in shared['contexts']:
            self.assertNotIn('device', context)
            if context['project'] is not None:
                self.assertEqual(set(context['project']), {'kind', 'name'})
        owner = owner_projection([project_row, session_row])
        self.assertIn('/private/CANARY_WORKDIR', json.dumps(owner))
        self.assertIn('CANARY_SESSION', json.dumps(owner))
        self.assertEqual(owner['shared_revision'], shared['shared_revision'])

    def test_shared_projection_validation_rejects_owner_fields_and_tampering(self):
        shared = shared_projection([project('one'), session('session')])
        self.assertIs(validate_shared_projection(shared), shared)
        for mutate in (
            lambda value: value['contexts'][0].update(workdir='/private'),
            lambda value: value['summary'].update(session_count=1),
            lambda value: value.update(shared_revision='0' * 64),
        ):
            bad = copy.deepcopy(shared)
            mutate(bad)
            with self.assertRaises(ValueError): validate_shared_projection(bad)

    def test_shuffle_duplicate_and_revision_determinism(self):
        rows = [
            project('directory', project_id=None, kind='directory', name='fallback'),
            project('native', kind='workspace', at=T2, status='verified', observed_at=OBSERVED,
                    source='host-api', verification='personal_agent_reported'),
            session('session'), project('old', project_id='removed', at=T1),
            project('tombstone', project_id='removed', at=T2, removed=True),
        ]
        baseline = build_execution_model(rows)
        shuffled = copy.deepcopy(rows)
        random.Random(19).shuffle(shuffled)
        self.assertEqual(build_execution_model(shuffled), baseline)
        self.assertEqual(build_execution_model(list(reversed(rows))), baseline)
        self.assertEqual(build_execution_model(rows + [copy.deepcopy(rows[0])]), baseline)
        self.assertEqual(shared_projection(shuffled), shared_projection(rows))
        self.assertEqual(owner_projection(shuffled, 'device-a'), owner_projection(rows, 'device-a'))

    def test_equal_timestamp_conflict_is_explicit_and_deterministic(self):
        rows = [project('location-b', workdir='/work/b', at=T1),
                project('location-a', workdir='/work/a', at=T1)]
        owner = owner_projection(rows)
        reverse = owner_projection(list(reversed(rows)))
        self.assertEqual(owner, reverse)
        context = owner['contexts'][0]
        self.assertEqual(context['workdir'], '/work/b')
        conflict = next(item for item in context['conflicts'] if item['field'] == 'workdir')
        self.assertEqual(conflict['values'], ['/work/a', '/work/b'])
        self.assertNotIn('conflicts', shared_projection(rows)['contexts'][0])


if __name__ == '__main__':
    unittest.main()
