import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmesh import field_crypto
from cosmesh.errors import Error
from cosmesh.profile import PersonalProfile, decode


class ProfileEncryptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cos-profile-crypto-')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.profile = PersonalProfile(self.folder / 'profile')
        self.env = dict(os.environ, COSMESH_PROFILE_DIR=str(self.profile.directory),
                        PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONDONTWRITEBYTECODE='1')
        self.password = 'unit-only-password-测试'
        self.contact = '邮箱：test@example.invalid\n电话：000-000-0000'

    def cli(self, app, *args, password=None, input=None, success=True):
        code = 'from cosmesh.cli import main; raise SystemExit(main(product=%r))' % app
        command = [sys.executable, '-c', code, *args, '--json']
        reader = writer = None
        try:
            if password is not None:
                reader, writer = os.pipe()
                os.write(writer, password.encode('utf-8') + b'\n')
                os.close(writer)
                writer = None
                command += ['--key-fd', str(reader)]
            result = subprocess.run(command, env=self.env, input=input, text=True, capture_output=True,
                                    pass_fds=() if reader is None else (reader,), timeout=20)
        finally:
            for fd in (reader, writer):
                if fd is not None:
                    os.close(fd)
        self.assertNotIn(self.password, result.stdout + result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual(result.returncode == 0, success, reply)
        self.assertIsNone(reply['sync'])
        return reply

    def seed(self):
        return self.profile.set({'contact': self.contact, 'display_name': '测试', 'about': '正文保持。\n'}, 'missing')

    def assertCode(self, code, callback):
        with self.assertRaises(Error) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_both_clients_encrypt_reveal_export_and_explicit_disable(self):
        original = self.seed()
        encrypted = self.cli('cosbase', 'profile', 'encrypt', '--field', 'contact', '--expect', original['version'],
                             password=self.password)['data']
        self.assertEqual(encrypted['encrypted_fields'], ['contact'])
        self.assertNotIn(self.contact, json.dumps(encrypted, ensure_ascii=False))
        self.assertNotIn('example.invalid', self.profile.path.read_text())
        shown = self.cli('cosmesh', 'profile', 'show')['data']
        self.assertEqual(shown['fields'], encrypted['fields'])
        before = self.profile.path.read_bytes()
        revealed = self.cli('cosmesh', 'profile', 'reveal', '--field', 'contact', password=self.password)['data']
        self.assertEqual(revealed['fields'], {'contact': self.contact})
        self.assertFalse(revealed['persisted'])
        self.assertFalse(revealed['published'])
        self.assertEqual(self.profile.path.read_bytes(), before)
        exported = self.cli('cosbase', 'profile', 'export', '--field', 'contact')['data']
        self.assertEqual(exported['fields'], {'contact': encrypted['fields']['contact']})
        self.assertEqual(exported['encrypted_fields'], ['contact'])
        changed = self.cli('cosbase', 'profile', 'set', '--expect', shown['version'], '--organization', '测试机构')['data']
        self.assertEqual(changed['fields']['contact'], shown['fields']['contact'])
        plain = self.cli('cosmesh', 'profile', 'decrypt', '--field', 'contact', '--expect', changed['version'],
                         password=self.password)['data']
        self.assertEqual(plain['fields']['contact'], self.contact)
        self.assertEqual(plain['encrypted_fields'], [])
        self.assertEqual(plain['fields']['about'], original['fields']['about'])

    def test_encrypt_on_first_save_and_updates_preserve_encryption(self):
        first = self.cli('cosmesh', 'profile', 'set', '--expect', 'missing', '--contact-stdin', '--encrypt-contact',
                         password=self.password, input=self.contact)['data']
        self.assertEqual(first['encrypted_fields'], ['contact'])
        before = self.profile.path.read_bytes()
        missing = self.cli('cosbase', 'profile', 'set', '--expect', first['version'], '--contact-stdin',
                           input='new@example.invalid', success=False)
        self.assertEqual(missing['error']['code'], 'SECRET_INPUT_REQUIRED')
        wrong = self.cli('cosbase', 'profile', 'set', '--expect', first['version'], '--contact-stdin',
                         password='wrong', input='new@example.invalid', success=False)
        self.assertEqual(wrong['error']['code'], 'DECRYPTION_FAILED')
        self.assertEqual(self.profile.path.read_bytes(), before)
        updated = self.cli('cosbase', 'profile', 'set', '--expect', first['version'], '--contact-stdin',
                           password=self.password, input='new@example.invalid')['data']
        self.assertEqual(updated['encrypted_fields'], ['contact'])
        self.assertEqual(self.profile.crypt('reveal', 'contact', self.password)['fields']['contact'], 'new@example.invalid')
        cleared = self.cli('cosmesh', 'profile', 'set', '--expect', updated['version'], '--contact', '')['data']
        self.assertEqual(cleared['fields']['contact'], '')
        self.assertEqual(cleared['encrypted_fields'], [])
        self.assertEqual(sorted(p.name for p in self.profile.directory.iterdir()), ['profile.lock', 'profile.md'])
        for path in self.profile.directory.iterdir():
            self.assertNotIn(self.password.encode('utf-8'), path.read_bytes())

    def test_wrong_password_tampering_and_unknown_format_preserve_data(self):
        first = self.seed()
        result = self.profile.crypt('encrypt', 'contact', self.password, first['version'])
        before = self.profile.path.read_bytes()
        self.assertCode('DECRYPTION_FAILED', lambda: self.profile.crypt('decrypt', 'contact', 'wrong', result['version']))
        self.assertEqual(self.profile.path.read_bytes(), before)
        value = result['fields']['contact']
        index = len(value) - 25
        tampered = value[:index] + ('A' if value[index] != 'A' else 'B') + value[index + 1:]
        self.assertCode('DECRYPTION_FAILED', lambda: field_crypto.decrypt(tampered, self.password))
        self.assertCode('DECRYPTION_FAILED', lambda: field_crypto.decrypt('ENC[cosmesh:v1:contact:AAAA:xxxx]', self.password))
        self.assertCode('ENCRYPTION_FORMAT_UNSUPPORTED', lambda: field_crypto.decrypt(value.replace(':v1:', ':v9:'), self.password))
        self.assertCode('INPUT_INVALID', lambda: self.profile.set({'contact': value}, result['version']))
        self.assertCode('INPUT_INVALID', lambda: self.profile.crypt('encrypt', 'about', self.password, result['version']))
        self.assertCode('FIELD_ALREADY_ENCRYPTED', lambda: self.profile.crypt('encrypt', 'contact', self.password, result['version']))

    def test_preview_version_conflict_and_atomic_failure(self):
        first = self.seed()
        before = self.profile.path.read_bytes()
        preview = self.profile.crypt('encrypt', 'contact', self.password, first['version'], dry_run=True)
        self.assertTrue(preview['dry_run'])
        self.assertEqual(self.profile.path.read_bytes(), before)
        self.profile.set({'preferences': '简洁'}, first['version'])
        current = self.profile.path.read_bytes()
        conflict = self.cli('cosbase', 'profile', 'encrypt', '--field', 'contact', '--expect', first['version'], success=False)
        self.assertEqual(conflict['error']['code'], 'STATE_CONFLICT')
        self.assertEqual(self.profile.path.read_bytes(), current)
        version = self.profile.read()['version']
        with patch('cosmesh.profile.atomic', side_effect=OSError('injected failure')):
            with self.assertRaises(OSError):
                self.profile.crypt('encrypt', 'contact', self.password, version)
        self.assertEqual(self.profile.path.read_bytes(), current)
        self.assertEqual(decode(current)['contact'], self.contact)

    def test_random_salts_context_and_secret_input_boundaries(self):
        first = field_crypto.encrypt(self.contact, self.password)
        second = field_crypto.encrypt(self.contact, self.password)
        self.assertNotEqual(first, second)
        self.assertEqual(field_crypto.decrypt(first, self.password), self.contact)
        self.assertCode('SECRET_INPUT_INVALID', lambda: field_crypto.encrypt('contact', ''))
        self.assertCode('INPUT_INVALID', lambda: field_crypto.encrypt('contact', self.password, field='about'))
        initial = self.seed()
        with open(self.folder / 'not-a-secret-file', 'w+') as handle:
            result = subprocess.run(self.command_for_fd(initial['version'], handle.fileno()), env=self.env,
                                    pass_fds=(handle.fileno(),), capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(result.stdout)['error']['code'], 'SECRET_INPUT_INVALID')
        result = self.cli('cosbase', 'profile', 'set', '--expect', initial['version'], '--contact-stdin',
                          '--encrypt-contact', '--key-fd', '0', input='not-a-key', success=False)
        self.assertEqual(result['error']['code'], 'INPUT_INVALID')

    def command_for_fd(self, version, fd):
        return [sys.executable, '-m', 'cosmesh', 'profile', 'encrypt', '--field', 'contact', '--expect', version,
                '--key-fd', str(fd), '--json']


if __name__ == '__main__':
    unittest.main()
