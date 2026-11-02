import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmesh.errors import Error
from cosmesh.profile import FIELDS, PersonalProfile, decode, encode


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cos-profile-', dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.profile = PersonalProfile(self.folder / 'private')
        self.env = dict(os.environ, COSMESH_PROFILE_DIR=str(self.profile.directory),
                        PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONDONTWRITEBYTECODE='1')

    def command(self, product, *args):
        code = 'from cosmesh.cli import main; raise SystemExit(main(product=%r))' % product
        return [sys.executable, '-c', code, *args, '--json']

    def cli(self, product, *args, success=True):
        result = subprocess.run(self.command(product, *args), env=self.env, capture_output=True, text=True)
        data = json.loads(result.stdout)
        self.assertEqual(result.returncode == 0, success, data)
        return data

    def assertCode(self, code, callback):
        with self.assertRaises(Error) as error:
            callback()
        self.assertEqual(error.exception.code, code)

    def test_both_clients_share_one_profile_without_document_stores(self):
        first = self.cli('cosbase', 'profile', 'show')
        self.assertFalse(first['data']['exists'])
        self.assertFalse(self.profile.directory.exists())
        self.assertIsNone(first['sync'])
        dry = self.cli('cosmesh', 'profile', 'init', '--dry-run')['data']
        self.assertTrue(dry['created'])
        self.assertFalse(self.profile.directory.exists())
        created = self.cli('cosbase', 'profile', 'init')['data']
        updated = self.cli('cosmesh', 'profile', 'set', '--expect', created['version'],
                           '--display-name', '显示名', '--timezone', 'Europe/Amsterdam',
                           '--preferences', '用简洁中文说明。')['data']
        self.assertEqual(self.cli('cosbase', 'profile', 'show')['data'],
                         {k: v for k, v in updated.items() if k not in {'dry_run', 'changed_fields'}})
        self.assertFalse(self.cli('cosbase', 'profile', 'init')['data']['created'])
        self.assertEqual(self.profile.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.profile.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.profile.path.read_bytes(), encode(updated['fields']))

    def test_defaults_only_affect_new_spaces_and_explicit_timezone_wins(self):
        old = self.folder / 'old'
        self.cli('cosbase', '--root', str(old), 'init', '--user', 'original', '--timezone', 'UTC')
        config = old / '.cosmesh/config.json'
        before = config.read_bytes()
        self.profile.set({'timezone': 'Europe/Amsterdam'}, 'missing')
        for app in ['cosbase', 'cosmesh']:
            root = self.folder / app
            reply = self.cli(app, '--root', str(root), 'init')['data']
            self.assertEqual(reply['config']['timezone'], 'Europe/Amsterdam')
            self.assertEqual(reply['config']['user'], self.profile.read()['owner']['user'])
        explicit = self.cli('cosmesh', '--root', str(self.folder / 'explicit'), 'init', '--timezone', 'UTC')['data']
        self.assertEqual(explicit['config']['timezone'], 'UTC')
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.cli('cosbase', '--root', str(old), 'whoami')['data']['document_user'], 'original')

    def test_selected_export_and_context_never_include_private_fields_implicitly(self):
        self.profile.set({'display_name': '公开显示名', 'contact': 'PRIVATE_CONTACT', 'about': 'PRIVATE_ABOUT',
                          'preferences': 'PRIVATE_PREFERENCES'}, 'missing')
        exported = self.cli('cosmesh', 'profile', 'export', '--field', 'display_name')
        self.assertEqual(exported['data']['fields'], {'display_name': '公开显示名'})
        self.assertFalse(exported['data']['published'])
        self.assertNotIn('PRIVATE_', json.dumps(exported))
        root = self.folder / 'team'
        self.cli('cosmesh', '--root', str(root), 'init')
        project = self.cli('cosmesh', '--root', str(root), 'project', 'create', '--title', '共同目标')['data']['result']
        context = self.cli('cosmesh', '--root', str(root), 'context', project['id'])
        self.assertNotIn('PRIVATE_', json.dumps(context))
        tracked = subprocess.check_output(['git', '-C', str(root), 'ls-tree', '-r', 'HEAD'], text=True)
        self.assertNotIn('profile', tracked)
        self.assertFalse(self.cli('cosbase', 'profile', 'export', success=False)['ok'])

    def test_versions_preview_and_concurrent_writes(self):
        before = self.profile.init()
        raw = self.profile.path.read_bytes()
        preview = self.profile.set({'about': '预览'}, before['version'], dry_run=True)
        self.assertTrue(preview['dry_run'])
        self.assertEqual(self.profile.path.read_bytes(), raw)
        processes = [subprocess.Popen(self.command(app, 'profile', 'set', '--expect', before['version'],
                                                  '--about', app), env=self.env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for app in ['cosbase', 'cosmesh']]
        replies = [json.loads(p.communicate(timeout=15)[0]) for p in processes]
        self.assertEqual(sum(r['ok'] for r in replies), 1)
        self.assertEqual([r['error']['code'] for r in replies if not r['ok']], ['STATE_CONFLICT'])
        self.assertIn(self.profile.read()['fields']['about'], ['cosbase', 'cosmesh'])
        self.assertCode('PRECONDITION_REQUIRED', lambda: self.profile.set({'contact': 'x'}, None))

    def test_permissions_symlinks_and_repository_locations(self):
        self.profile.init()
        self.profile.path.chmod(0o644)
        self.assertCode('PROFILE_PERMISSIONS', self.profile.read)
        self.profile.path.chmod(0o600)
        self.profile.directory.chmod(0o755)
        self.assertCode('PROFILE_PERMISSIONS', self.profile.read)
        self.profile.directory.chmod(0o700)
        linked = self.folder / 'linked'
        linked.symlink_to(self.profile.directory)
        self.assertCode('PATH_INVALID', lambda: PersonalProfile(linked))
        alias = self.folder / 'hardlink'
        os.link(self.profile.path, alias)
        self.assertCode('PROFILE_PERMISSIONS', self.profile.read)
        alias.unlink()
        target = self.folder / 'git'
        (target / '.git').mkdir(parents=True)
        self.assertCode('PATH_INVALID', lambda: PersonalProfile(target / 'profile'))
        with patch.dict(os.environ, COSMESH_INSTALL_PREFIX=str(self.folder)):
            self.assertCode('PATH_INVALID', lambda: PersonalProfile(self.folder / 'app-profile'))

    def test_safe_schema_validation_and_identity_is_not_profile_data(self):
        fields = dict.fromkeys(FIELDS, '')
        fields.update(about='简介\n\n保留 Markdown。\n', preferences='第一行\n第二行', language='zh-Hans-CN')
        self.assertEqual(decode(encode(fields)), fields)
        self.assertCode('INPUT_INVALID', lambda: self.profile.set({'user': 'someone-else'}, 'missing'))
        self.assertCode('INPUT_INVALID', lambda: encode({**fields, 'timezone': '../invalid'}))
        self.assertCode('INPUT_INVALID', lambda: encode({**fields, 'language': 'bad tag'}))
        self.assertCode('INPUT_TOO_LARGE', lambda: encode({**fields, 'about': 'x' * 65536}))
        bad = encode(fields).replace(b'schema_version: 1', b'schema_version: 2')
        self.assertCode('PROFILE_VERSION_UNSUPPORTED', lambda: decode(bad))
        duplicate = encode(fields).replace(b'schema_version: 1', b'schema_version: 1\nschema_version: 1')
        self.assertCode('SCHEMA_INVALID', lambda: decode(duplicate))
        with patch.dict(os.environ, USER='forged'):
            self.assertNotEqual(self.profile.read()['owner']['user'], 'forged')

    def test_failed_atomic_write_preserves_the_previous_profile(self):
        before = self.profile.init()
        raw = self.profile.path.read_bytes()
        with patch('cosmesh.profile.atomic', side_effect=OSError('injected failure')):
            with self.assertRaises(OSError):
                self.profile.set({'contact': 'new'}, before['version'])
        self.assertEqual(self.profile.path.read_bytes(), raw)
        result = self.profile.set({'contact': 'retry'}, before['version'])
        self.assertEqual(result['fields']['contact'], 'retry')

    def test_profile_flags_and_errors_do_not_claim_hub_verification(self):
        unavailable = self.cli('cosbase', 'profile', 'show', '--require-fresh', success=False)
        self.assertEqual(unavailable['error']['code'], 'FRESHNESS_UNAVAILABLE')
        self.assertIsNone(unavailable['sync'])
        wrong = self.cli('cosmesh', 'profile', 'show', '--revision', 'a' * 40, success=False)
        self.assertEqual(wrong['error']['code'], 'INPUT_INVALID')
        self.assertFalse(self.profile.directory.exists())
        bio = self.folder / 'bio.md'
        bio.write_text('可读的个人简介。\n')
        set_result = self.cli('cosbase', 'profile', 'set', '--expect', 'missing', '--file', str(bio))
        self.assertEqual(set_result['data']['fields']['about'], bio.read_text())
        self.assertEqual(set_result['warnings'], [])


if __name__ == '__main__':
    unittest.main()
