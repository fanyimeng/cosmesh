"""Component lifecycle tests use an isolated home and a synthetic official-CLI boundary."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cosmesh import component_artifacts as artifacts, component_dsh as host, components
from cosmesh.cli import main, parser
from cosmesh.errors import Error

FAKE = r'''
import json, os, sys
from pathlib import Path
home = Path(os.environ['DSH_HOME'])
control = home / 'control.json'
c = json.loads(control.read_text())
if sys.argv[1:] == ['--version']:
    print(c.get('version', '0.2.2-rc.3')); sys.exit(0)
with (home / 'calls.jsonl').open('a') as f: f.write(json.dumps(sys.argv[1:])+'\n')
failure = c.pop('fail', None); control.write_text(json.dumps(c))
if failure == 'before': sys.exit(1)
a = sys.argv[1:]
if a[3] == 'remove' and any(x in a for x in ('--ignore-scripts', '--offline')):
    sys.exit(1)  # pnpm 10 rejects add-only flags on remove.
profile = home/'profiles'/a[2]; p = profile/'package.json'
m = json.loads(p.read_text()); name = '@cosmesh/dsh-connector'
link = profile/'node_modules'/name
if link.is_symlink(): link.unlink()
if a[3] == 'add':
    target = Path(a[5]); link.parent.mkdir(parents=True, exist_ok=True); link.symlink_to(target)
    m['dependencies'][name] = 'link:' + str(target)
    if name not in m['dsh']['profile']['bundles']: m['dsh']['profile']['bundles'].append(name)
else:
    m['dependencies'].pop(name, None)
    m['dsh']['profile']['bundles'] = [x for x in m['dsh']['profile']['bundles'] if x != name]
p.write_text(json.dumps(m))
if failure == 'after': sys.exit(1)
'''


class ComponentManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='component-test-', dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'host'
        self.home.mkdir()
        self.control = self.home / 'control.json'
        self.control.write_text('{}')
        self.cli = self.root / 'fake-dsh'
        self.cli.write_text('#!' + sys.executable + '\n' + FAKE)
        self.cli.chmod(0o700)
        self.env = patch.dict(os.environ, {'DSH_HOME': str(self.home)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.profile('desktop')
        self.store = self.root / 'components'
        self.item = copy.deepcopy(artifacts.select('dsh'))
        self.archives = {}
        for v in ('0.1.0', '0.1.1'):
            path = self.root / (v + '.zip')
            with zipfile.ZipFile(path, 'w') as z:
                package = {'name': self.item['package'], 'version': v, 'files': ['index.js']}
                z.writestr('bundle/package.json', json.dumps(package))
                for name in ('index.js', 'cordis.patch.yml', 'icon.svg', 'locale/en.json', 'locale/zh.json'):
                    z.writestr('bundle/' + name, 'synthetic ' + v)
            self.item['releases'][v]['archive_sha256'] = artifacts.digest(path.read_bytes())
            self.archives[v] = path
        p = patch.object(artifacts, 'catalog', return_value=[self.item]); p.start(); self.addCleanup(p.stop)

    def future_version(self):
        # Derive a version that is genuinely newer than the packaged catalog so
        # catalog additions by this release never turn a synthetic pin into a conflict.
        versions = [tuple(map(int, version.split('.'))) for version in self.item['releases']]
        major, minor, patch = max(versions)
        return '%d.%d.%d' % (major, minor, patch + 1)

    def profile(self, name):
        p = self.home / 'profiles' / name
        p.mkdir(parents=True)
        (p / 'package.json').write_text(json.dumps({'dependencies': {}, 'dsh': {'profile': {'bundles': []}}}))
        (p / 'cordis.patch.yml').write_text('private-config: preserve\n')
        (p / 'session-data').write_text('keep original session')
        return p

    def command(self, verb, version=None, profile='desktop', extra=()):
        cmd = ['component', verb, 'dsh', '--component-root', str(self.store), '--profile', profile]
        if verb != 'status':
            cmd += ['--dsh-cli', str(self.cli), '--host-stopped', '--offline']
        if version:
            cmd += ['--version', version, '--artifact', str(self.archives[version])]
        return parser('cosmesh').parse_args(cmd + list(extra))

    def run_op(self, verb, version=None, **kwargs):
        return components.execute(self.command(verb, version, **kwargs))[0]

    def calls(self):
        p = self.home/'calls.jsonl'
        return p.read_text().splitlines() if p.exists() else []

    def fail(self, kind):
        self.control.write_text(json.dumps({'fail': kind}))

    def assert_code(self, code, call):
        with self.assertRaises(Error) as caught: call()
        self.assertEqual(caught.exception.code, code)

    def test_status_discovers_actual_profiles_without_host_changes(self):
        (self.home/'profiles/desktop').rename(self.home/'profiles/web')
        self.run_op('install', '0.1.1', profile='web')
        args = parser('cosmesh').parse_args(['component','status','dsh','--component-root',str(self.store)])
        calls = self.calls()
        web = components.execute(args)[0]
        self.assertEqual((web['profile'], web['installed']['version']), ('web', '0.1.1'))
        self.assertIn('update dsh --profile web --host-stopped', web['next_command'])
        self.profile('headless')
        rows = components.execute(args)[0]['profiles']
        self.assertEqual([r['profile'] for r in rows], ['headless', 'web'])
        self.assertIn('install dsh --profile headless', rows[0]['next_command'])
        self.assertTrue(all(r['activation'] == 'unknown' for r in rows))
        self.assertEqual(self.run_op('status', profile='web'), web)
        self.assertEqual(self.calls(), calls)

    def test_read_only_and_dry_run_do_not_create_state_or_touch_threads(self):
        with patch('cosmesh.cli.Store', side_effect=AssertionError('Thread store opened')):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['component', 'list', '--json']), 0)
            self.assertIsNone(json.loads(out.getvalue())['sync'])
        self.assertIsNone(self.run_op('status')['installed'])
        self.run_op('install', '0.1.1', extra=['--dry-run'])
        self.assertFalse(self.store.exists()); self.assertEqual(self.calls(), [])

    def test_install_update_rollback_and_other_profile_preserved(self):
        other = self.profile('other'); original = (other/'package.json').read_bytes()
        self.run_op('install', '0.1.0')
        self.run_op('update', '0.1.1')
        result = self.run_op('rollback')
        self.assertEqual(result['installed']['version'], '0.1.0')
        self.assertEqual(self.run_op('status')['activation'], 'unknown')
        self.assertEqual((other/'package.json').read_bytes(), original)
        p = self.home/'profiles/desktop'
        self.assertEqual((p/'session-data').read_text(), 'keep original session')
        self.assertEqual((p/'cordis.patch.yml').read_text(), 'private-config: preserve\n')
        self.assertTrue(any((components.state_path(self.store,'desktop').parent/'backups').iterdir()))
        self.assertTrue((self.store/'versions/dsh-connector-0.1.1/bundle').is_dir())

    def test_external_fixed_bundle_adoption_preserves_original_rollback_path(self):
        bundle, _ = artifacts.prepare(self.store, self.item, '0.1.0', self.archives['0.1.0'])
        import shutil
        external = self.root/'old-fixed-bundle'; shutil.copytree(bundle, external)
        host.apply(str(self.cli), 'desktop', external, self.item['package'])
        self.run_op('install', '0.1.1')
        self.assertEqual(self.run_op('rollback')['installed']['path'], str(external))
        self.assertEqual((external/'index.js').read_text(), 'synthetic 0.1.0')

    def test_failure_before_effect_retries_same_operation(self):
        self.fail('before')
        self.assert_code('COMPONENT_HOST_FAILED', lambda: self.run_op('install', '0.1.1'))
        self.assertIn('pending', self.run_op('status')['managed'])
        self.run_op('install', '0.1.1')
        self.assertNotIn('pending', self.run_op('status')['managed'])
        self.assertEqual(len(self.calls()), 2)

    def test_failure_after_effect_recovers_without_repeating_host_write(self):
        self.fail('after')
        self.assert_code('COMPONENT_HOST_FAILED', lambda: self.run_op('install', '0.1.1'))
        result = self.run_op('install', '0.1.1')
        self.assertTrue(result['recovered']); self.assertEqual(len(self.calls()), 1)

    def test_failed_install_can_rollback_to_absent(self):
        self.fail('after')
        self.assert_code('COMPONENT_HOST_FAILED', lambda: self.run_op('install', '0.1.1'))
        self.assertIsNone(self.run_op('rollback')['installed'])
        self.assertTrue((self.store/'versions/dsh-connector-0.1.1/bundle').exists())

    def test_failed_rollback_retries_the_original_target(self):
        self.run_op('install', '0.1.0'); self.run_op('update', '0.1.1')
        self.fail('before')
        self.assert_code('COMPONENT_HOST_FAILED', lambda: self.run_op('rollback'))
        self.assertEqual(self.run_op('rollback')['installed']['version'], '0.1.0')

    def test_host_and_cli_compatibility_and_stopped_gate_precede_host_mutation(self):
        self.control.write_text('{"version":"9.9.9"}')
        self.assert_code('COMPONENT_INCOMPATIBLE', lambda: self.run_op('install','0.1.1'))
        self.control.write_text('{}')
        args=self.command('install','0.1.1');args.host_stopped=False
        self.assert_code('HOST_MUST_BE_STOPPED', lambda: components.execute(args))
        with patch.object(host,'__version__','0.4.0'):
            self.assert_code('COMPONENT_INCOMPATIBLE', lambda: self.run_op('install','0.1.1'))
        self.assertEqual(self.calls(), [])

    def test_tampered_zip_and_managed_files_block_install(self):
        self.archives['0.1.1'].write_bytes(b'wrong bytes')
        self.assert_code('COMPONENT_INTEGRITY', lambda: self.run_op('install','0.1.1'))
        self.run_op('install','0.1.0')
        (self.store/'versions/dsh-connector-0.1.0/bundle/index.js').write_text('tamper')
        self.assert_code('COMPONENT_INTEGRITY', lambda: self.run_op('install','0.1.0'))
        self.assertEqual(len(self.calls()), 1)

    def test_pending_version_change_drift_and_parallel_operations_are_blocked(self):
        self.fail('before')
        self.assert_code('COMPONENT_HOST_FAILED',lambda:self.run_op('install','0.1.1'))
        self.assert_code('COMPONENT_PENDING',lambda:self.run_op('install','0.1.0'))
        with components.locked(self.store):
            self.assert_code('STORE_BUSY',lambda:self.run_op('install','0.1.1'))
        self.assertEqual(len(self.calls()),1)

    def test_unsafe_archive_members_are_rejected_even_with_matching_pin(self):
        for name in ('bundle/../secret', 'bundle/.env', 'other/index.js', 'bundle/a\\b'):
            raw = io.BytesIO()
            with zipfile.ZipFile(raw,'w') as z:z.writestr(name,'private')
            item=copy.deepcopy(self.item);item['releases']['0.1.1']['archive_sha256']=artifacts.digest(raw.getvalue())
            self.assert_code('COMPONENT_INTEGRITY',lambda:artifacts.unpack(raw.getvalue(),item,'0.1.1'))

    def test_remove_disables_scripts_and_preserves_offline_policy(self):
        for offline in (False, True):
            with patch.object(host, 'invoke') as invoke:
                host.apply(str(self.cli), 'desktop', None, self.item['package'], offline)
            args = invoke.call_args.args[1]
            self.assertIn('--config.ignore-scripts=true', args)
            self.assertEqual('--config.offline=true' in args, offline)
            self.assertNotIn('--ignore-scripts', args)
            self.assertNotIn('--offline', args)

    def test_successful_first_install_can_be_rolled_back_without_deleting_files(self):
        self.run_op('install','0.1.1')
        self.assertIsNone(self.run_op('rollback')['installed'])
        self.assertTrue((self.store/'versions/dsh-connector-0.1.1/bundle').exists())

    def test_remote_catalog_updates_component_without_upgrading_cli(self):
        # The existing SSH software source is the trust boundary, separate from the Hub.
        future=self.future_version()
        remote=copy.deepcopy(self.item)
        remote['version']=future
        raw=io.BytesIO()
        with zipfile.ZipFile(self.archives['0.1.1']) as old, zipfile.ZipFile(raw,'w') as new:
            for name in old.namelist():
                data=old.read(name)
                if name=='bundle/package.json':
                    package=json.loads(data);package['version']=future;data=json.dumps(package).encode()
                new.writestr(name,data)
        remote['releases'][future]={'archive_sha256':artifacts.digest(raw.getvalue()),'requires':self.item['requires']}
        document={'schema':'cosmesh.components.v1','components':[remote]}
        def fetch(cfg,name):
            self.assertEqual(cfg,{'source':'synthetic-release-source'})
            return json.dumps(document).encode() if name=='components/catalog.json' else raw.getvalue()
        args=self.command('install');args.offline=False
        with patch('cosmesh.client.config',return_value=(self.root,{'source':'synthetic-release-source'})), patch('cosmesh.client.fetch',side_effect=fetch):
            result=components.execute(args)[0]
        self.assertEqual(result['installed']['version'],'0.1.2')
        self.assertEqual(self.run_op('status')['recommended_version'],'0.1.2')
        self.assertIsNone(self.run_op('rollback')['installed'])

    def test_remote_catalog_cannot_replace_existing_pins_or_package(self):
        for field in ('digest','package'):
            remote=copy.deepcopy(self.item)
            if field=='digest':remote['releases']['0.1.1']['archive_sha256']='a'*64
            else:remote['package']='@unknown/other'
            self.assert_code('COMPONENT_CATALOG',lambda:artifacts.merge_catalog(self.item,{'schema':'cosmesh.components.v1','components':[remote]}))

    def test_new_incompatible_recommendation_does_not_block_rollback_of_installed_version(self):
        self.run_op('install','0.1.1')
        future_version=self.future_version()
        future=copy.deepcopy(self.item);future['version']=future_version
        future['releases'][future_version]={'archive_sha256':'a'*64,'requires':{'cosmesh':'>=0.4.8','dsh':'9.9.9'}}
        (self.store/'catalog.json').write_text(json.dumps({'schema':'cosmesh.components.v1','components':[future]}))
        self.assertIsNone(self.run_op('rollback')['installed'])

    def test_restored_manifest_with_new_node_link_is_not_reported_as_success(self):
        self.run_op('install','0.1.0')
        manifest=self.home/'profiles/desktop/package.json';before=manifest.read_bytes()
        self.fail('after')
        self.assert_code('COMPONENT_HOST_FAILED',lambda:self.run_op('update','0.1.1'))
        manifest.write_bytes(before)  # A host rollback may restore metadata before node_modules.
        self.assertEqual(self.run_op('status')['host_issue']['code'],'COMPONENT_DRIFT')
        self.assert_code('COMPONENT_DRIFT',lambda:self.run_op('update','0.1.1'))
        self.assertIn('pending',self.run_op('status')['managed'])


if __name__ == '__main__': unittest.main()
