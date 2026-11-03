"""Optional maintenance entry discovery using ordinary shared Threads."""
import io
import json
from contextlib import redirect_stdout

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.store import atomic
from test_threads import ThreadFixture


class MaintenanceEntryTests(ThreadFixture):
    participants = ('alice', 'bob')

    def setUp(self):
        super().setUp()
        self.hub.initialize_community(self.root/'library')
        for user, name in (('alice', '张三'), ('bob', '李四')):
            self.hub.spaces({'method':'community_name','name':name,'key':'name-'+user},user)
            with self.use(user) as wb: wb.discover()

    def cli(self, user, *args, error=None):
        with redirect_stdout(io.StringIO()) as output:
            code = main([*args, '--root', str(self.wbs[user].base.root), '--json'])
        value = json.loads(output.getvalue())
        if error:
            self.assertNotEqual(code, 0, value)
            self.assertEqual(value['error']['code'], error, value)
        else:
            self.assertEqual(code, 0, value)
            return value['data']

    def test_configure_discover_and_clear_without_changing_thread(self):
        self.assertIsNone(self.hub.spaces({'method':'list_threads'},'bob')['maintenance'])
        thread = self.shared('alice','Application maintenance','Requests are ordinary Thread records.')
        repo = self.repos['alice'];before = self.hub.repo(repo).git('rev-parse','main')
        with redirect_stdout(io.StringIO()) as output:
            code = main(['hub','maintenance','--hub-root',str(self.hub.root),
                         '--repo',repo,'--thread',thread,'--json'])
        result = json.loads(output.getvalue());self.assertEqual(code,0,result)
        entry = result['data']['maintenance']
        self.assertEqual(entry['maintainer'],'alice');self.assertEqual(entry['source']['id'],thread)
        with self.use('bob') as wb:
            reply = wb.discover()
            self.assertEqual(reply['maintenance'],entry)
            self.assertEqual(wb.client['maintenance'],entry)
        self.assertEqual(self.hub.repo(repo).git('rev-parse','main'),before)
        self.assertEqual(self.hub.settings()['maintenance_thread'],{'repo':repo,'id':thread})
        self.hub.maintenance(clear=True)
        with self.use('bob') as wb:
            wb.discover();self.assertIsNone(wb.client['maintenance'])

    def test_private_invalid_and_unavailable_targets(self):
        with self.use('alice') as wb:private=wb.create('Private work')['id']
        with self.assertRaises(Error):self.hub.maintenance(self.repos['alice'],private)
        with self.assertRaises(Error):self.hub.maintenance('missing-space',private)
        with self.assertRaises(Error):self.hub.maintenance(self.repos['alice'],None)
        thread=self.shared('alice','Maintenance')
        self.hub.maintenance(self.repos['alice'],thread)
        settings=self.hub.settings();settings['maintenance_thread']['id']=private
        atomic(self.hub.root/'hub.json',json.dumps(settings)+'\n')
        reply=self.hub.spaces({'method':'list_threads'},'bob')
        self.assertIsNone(reply['maintenance']);self.assertEqual(reply['maintenance_error'],'NOT_FOUND')
        self.assertTrue(reply['spaces'])

    def test_ordinary_request_loop_in_maintainers_thread(self):
        thread = self.shared('alice', 'Application maintenance')
        self.hub.maintenance(self.repos['alice'], thread)
        self.cli('bob', 'thread', 'discover')
        entry = self.cli('bob', 'thread', 'maintenance', '--offline')
        self.assertEqual(entry['maintainer_name'], '张三')
        args = ('thread', 'request', '--maintenance', '--body', 'Please clarify the help text.',
                '--idempotency-key', 'ordinary-feedback')
        draft = self.cli('bob', *args, '--offline')
        self.assertTrue(self.cli('bob', *args, '--offline')['replayed'])
        request = draft['id']
        before = self.hub.repo(self.repos['alice']).git('rev-parse', 'main')
        self.cli('bob', 'thread', 'maintenance', '--require-fresh')
        self.assertEqual(self.hub.repo(self.repos['alice']).git('rev-parse', 'main'), before)
        self.assertTrue(self.cli('bob', 'thread', 'sync')['synchronized'])
        self.cli('alice', 'thread', 'sync', '--pull')
        inbox = self.cli('alice', 'thread', 'inbox')
        self.assertEqual(inbox[0]['request']['id'], request)
        self.cli('bob', 'thread', 'deliver', request, '--result', request, error='AUTH_DENIED')
        self.cli('alice', 'thread', 'reply', request, '--body', 'I will clarify it.', '--sync')
        self.cli('alice', 'thread', 'assign', request, '--work', thread, '--body', 'Handle here.', '--sync')
        self.cli('alice', 'thread', 'adopt', request, error='AUTH_DENIED')
        result = self.cli('alice', 'thread', 'result', thread, '--body', 'Help text clarified.', '--sync')['result']['id']
        self.cli('alice', 'thread', 'deliver', request, '--result', result, '--body', 'Ready to verify.', '--sync')
        self.cli('bob', 'thread', 'sync', '--pull')
        sent = self.cli('bob', 'thread', 'inbox', '--sent')
        self.assertEqual(sent[0]['state'], 'delivered')
        self.cli('bob', 'thread', 'adopt', request, '--result', result, '--body', 'Verified.', '--sync')
        with self.use('bob') as wb:
            state = wb.request_state(wb.find(request))
            self.assertEqual(state['state'], 'adopted'); self.assertTrue(state['confirmed'])
            self.assertEqual(wb.find(request)['repo'], self.repos['alice'])
            self.assertEqual(wb.find(request)['doc']['meta']['to'], 'alice')
            self.assertFalse([x for x in wb.catalog.values() if x['doc']['meta']['kind']=='thread' and x['doc']['meta']['author']=='bob'])
            self.assertTrue(all(wb.accepted(x) for x in wb.records(thread)))
            self.assertEqual(wb.context(thread)['thread']['lifecycle'], 'active')

    def test_selector_privacy_and_explicit_name_compatibility(self):
        self.cli('bob', 'thread', 'maintenance', error='MAINTENANCE_UNAVAILABLE')
        self.cli('bob', 'thread', 'request', '--body', 'Missing target.', error='INPUT_INVALID')
        thread = self.shared('alice', 'Maintenance')
        self.hub.maintenance(self.repos['alice'], thread)
        self.cli('bob', 'thread', 'discover')
        self.cli('bob', 'thread', 'request', thread, '--maintenance', '--body', 'Two targets.', error='INPUT_INVALID')
        with self.use('bob') as wb: private = wb.create('Private investigation')['id']
        self.cli('bob', 'thread', 'request', '--maintenance', '--ref', private, '--body', 'Private source.', error='PRIVATE_REFERENCE')
        explicit = self.cli('bob', 'thread', 'request', thread, '--to', '张三', '--body', 'Explicit name.')
        with self.use('bob') as wb:
            self.assertEqual(wb.find(explicit['id'])['doc']['meta']['to'], 'alice')
        self.cli('alice', 'thread', 'delete', thread, '--body', 'Retired.', '--sync')
        self.cli('bob', 'thread', 'sync', '--pull')
        self.cli('bob', 'thread', 'request', '--maintenance', '--body', 'Unavailable.', error='MAINTENANCE_UNAVAILABLE')
