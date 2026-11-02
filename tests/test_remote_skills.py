import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cosmesh.errors import Error
from cosmesh.remote_skills import export
from tools import remote_bootstrap as bootstrap


class RemoteSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ssh-skills-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = Path(__file__).resolve().parents[1]
        self.prefix = self.root / 'server prefix'
        self.skills = self.root / 'local skills'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, COSMESH_INSTALL_PREFIX=str(self.prefix), COSMESH_SKILLS_DIR=str(self.skills),
                        COSMESH_PROFILE_DIR=str(self.root / 'profile'), COSMESH_DATA_DIR=str(self.root / 'spaces'),
                        COSMESH_FEEDBACK_DIR=str(self.root / 'feedback'), COSMESH_SERVICE=str(self.root / 'no-service'),
                        PYTHONPATH=str(self.source), PYTHONDONTWRITEBYTECODE='1',
                        PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        ssh = self.bin / 'ssh'
        ssh.write_text('#!' + sys.executable + '\nimport os,subprocess,sys\n'
                       'if os.environ.get("SSH_TEST_OFFLINE"):sys.exit(255)\n'
                       'command=sys.argv[-1].replace("python3",' + repr(sys.executable) + ',1)\n'
                       'sys.exit(subprocess.run(["sh","-c",command]).returncode)\n')
        ssh.chmod(0o700)
        (self.prefix / 'bin').mkdir(parents=True)
        for name in ('cosmesh', 'cosbase'):
            file = self.prefix / 'bin' / name
            code = 'from cosmesh.cli import main; raise SystemExit(main(product=%r))' % name
            file.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(code) + ' "$@"\n')
            file.chmod(0o700)

    def install(self, alias='existing-server', success=True):
        with patch.dict(os.environ, COSMESH_INSTALL_PREFIX=str(self.prefix)):
            content = export(alias)
        script = self.root / 'install.sh'
        script.write_text(content)
        result = subprocess.run(['sh', str(script)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return result

    def run_skill(self, name, *args, body=None, success=True, env=None):
        result = subprocess.run(['sh', str(self.skills / name / 'scripts/run.sh'), *args],
                                env=env or self.env, input=body, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return result

    def test_only_two_skills_and_no_local_runtime_are_needed(self):
        self.install()
        self.assertEqual(sorted(p.name for p in self.skills.iterdir()), ['cosbase', 'cosmesh'])
        self.assertFalse((self.root / 'local-runtime').exists())
        for name in ('cosmesh', 'cosbase'):
            result = json.loads(self.run_skill(name, 'capabilities', '--json').stdout)
            self.assertTrue(result['data']['features']['ssh_only_skills'])
            self.assertEqual(result['data']['product'], name)
        self.install()
        self.assertEqual((self.skills / 'cosbase/SKILL.md').stat().st_mode & 0o777, 0o600)

    def test_quoting_unicode_trailing_newlines_and_stdin_reach_server(self):
        self.install()
        app = self.prefix / 'bin/cosbase'
        app.write_text('#!' + sys.executable + '\nimport sys,json\nprint(json.dumps({"args":sys.argv[1:],"stdin":sys.stdin.read()}))\n')
        sentinel = self.root / 'must-not-exist'
        arguments = ["单引号 ' 双引号 \"", '$(touch ' + str(sentinel) + ')', '; touch ' + str(sentinel), '末尾\n\n', '--file', '-']
        result = json.loads(self.run_skill('cosbase', *arguments, body='正文\n第二行\n').stdout)
        self.assertEqual(result['args'], arguments)
        self.assertEqual(result['stdin'], '正文\n第二行\n')
        self.assertFalse(sentinel.exists())

    def test_remote_personal_flow_and_streamed_profile_and_feedback(self):
        self.install()
        self.run_skill('cosbase', 'init', '--json')
        created = json.loads(self.run_skill('cosbase', 'task', 'create', '--title', '远程事项', '--file', '-',
                                           '--idempotency-key', 'one-remote-action', '--json', body='正文\n').stdout)
        doc = created['data']['result']
        self.assertEqual(doc['body'], '正文\n')
        repeated = json.loads(self.run_skill('cosbase', 'task', 'create', '--title', '远程事项', '--file', '-',
                                            '--idempotency-key', 'one-remote-action', '--json', body='正文\n').stdout)
        self.assertEqual(repeated['data']['result']['id'], doc['id'])
        profile = json.loads(self.run_skill('cosmesh', 'profile', 'set', '--expect', 'missing', '--file', '-', '--json', body='简介\n').stdout)
        self.assertEqual(profile['data']['fields']['about'], '简介\n')
        feedback = json.loads(self.run_skill('cosbase', 'feedback', 'add', '--file', '-', '--idempotency-key', 'remote-feedback',
                                            '--json', body='系统使用问题\n').stdout)
        self.assertTrue(feedback['data']['saved_locally'])
        bad = self.run_skill('cosmesh', 'feedback', 'add', '--file', '-', '--json', body='x' * 65537, success=False)
        self.assertEqual(json.loads(bad.stdout)['error']['code'], 'INPUT_TOO_LARGE')

    def test_modified_extra_and_native_material_are_preserved(self):
        self.install()
        first = (self.skills / 'cosmesh/SKILL.md').read_bytes()
        target = self.skills / 'cosbase/SKILL.md'
        target.write_text('用户自定义 Skill')
        self.install(success=False)
        self.assertEqual(target.read_text(), '用户自定义 Skill')
        self.assertEqual((self.skills / 'cosmesh/SKILL.md').read_bytes(), first)
        other = self.root / 'native'
        (other / 'cosmesh').mkdir(parents=True)
        (other / 'cosmesh/SKILL.md').write_text('native managed Skill')
        result = subprocess.run(['sh', str(self.root / 'install.sh')], env=dict(self.env, COSMESH_SKILLS_DIR=str(other)), capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((other / 'cosbase').exists())

    def test_failed_second_activation_rolls_back_the_pair(self):
        self.install()
        before = {name: (self.skills / name / 'connection').read_bytes() for name in ('cosmesh', 'cosbase')}
        move = self.bin / 'mv'
        move.write_text('#!/bin/sh\ncase "$1" in */new-cosbase) exit 1;; esac\nexec /bin/mv "$@"\n')
        move.chmod(0o700)
        self.install(alias='another-existing-server', success=False)
        self.assertEqual(before, {name: (self.skills / name / 'connection').read_bytes() for name in before})

    def test_alias_injection_and_ssh_failure_are_explicit(self):
        with patch.dict(os.environ, COSMESH_INSTALL_PREFIX=str(self.prefix)):
            with self.assertRaises(Error):
                export('-oProxyCommand=bad')
        self.install()
        result = self.run_skill('cosmesh', 'capabilities', '--json', success=False,
                                env=dict(self.env, SSH_TEST_OFFLINE='1'))
        self.assertEqual(result.returncode, 255)
        self.assertEqual(result.stdout, '')

    def test_local_bootstrap_script_and_update_use_the_same_ssh_route(self):
        server = self.root / 'public-agent'
        server.mkdir()
        (server / 'bootstrap.py').write_text('import json\nprint(json.dumps({"ok":True,"data":{"available_version":"0.1.7"}}))\n')
        installer = server / 'install.sh'
        installer.write_bytes((self.source / 'cosmesh/resources/remote-install.sh').read_bytes())
        command = ['sh', str(installer), 'existing-server', '--dest', str(self.skills),
                   '--remote-prefix', str(self.prefix), '--bootstrap', str(server / 'bootstrap.py')]
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = json.loads(self.run_skill('cosmesh', '--check-updates').stdout)
        self.assertTrue(checked['ok'])
        updated = self.run_skill('cosbase', '--update')
        self.assertIn('two SSH Skills installed', updated.stdout)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='remote-bootstrap-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.releases = self.root / 'releases'
        self.releases.mkdir(mode=0o755)
        self.prefix = self.root / 'app'
        self.version = '0.1.7'
        self.name = 'cosmesh-%s-%s-%s-py%d%d' % (self.version, sys.platform, bootstrap.platform.machine(), *sys.version_info[:2])
        selected = self.releases / self.name
        selected.mkdir(mode=0o755)
        installer = selected / 'install.py'
        installer.write_text('import argparse,json,os\nfrom pathlib import Path\n'
                             'p=argparse.ArgumentParser();p.add_argument("action");p.add_argument("--prefix");p.add_argument("--skills");p.add_argument("--json",action="store_true");a=p.parse_args()\n'
                             'root=Path(a.prefix);root.mkdir(exist_ok=True);s=root/"state.json";s.write_text(json.dumps({"current":"0.1.7"}));s.chmod(0o600);print(json.dumps({"ok":True}))\n')
        (selected / 'release.json').write_text('{}\n')
        installer.chmod(0o644)
        (selected / 'release.json').chmod(0o644)
        self.channel = {'format': 1, 'version': self.version, 'release_id': self.name,
                        'platform': sys.platform, 'machine': bootstrap.platform.machine(),
                        'python': '%d.%d' % sys.version_info[:2],
                        'installer_sha256': hashlib.sha256(installer.read_bytes()).hexdigest(),
                        'manifest_sha256': hashlib.sha256((selected / 'release.json').read_bytes()).hexdigest()}
        self.channel_path = self.releases / 'stable-linux.json'
        self.channel_path.write_text(json.dumps(self.channel))
        self.channel_path.chmod(0o644)

    def call(self, action):
        r = subprocess.run([sys.executable, str(Path(bootstrap.__file__)), action, '--releases-dir', str(self.releases),
                            '--prefix', str(self.prefix), '--local-skill-version', '0.1.6'], text=True, capture_output=True)
        return r, json.loads(r.stdout)

    def test_fresh_install_and_idempotent_update_preserve_data(self):
        result, status = self.call('status')
        self.assertTrue(status['data']['server_update_available'])
        self.assertFalse(self.prefix.exists())
        result, first = self.call('ensure')
        self.assertEqual(result.returncode, 0, first)
        self.assertEqual(first['data']['action'], 'install')
        note = self.prefix / 'user-note'
        note.write_text('保留用户材料')
        result, repeated = self.call('ensure')
        self.assertEqual(repeated['data']['action'], 'unchanged')
        self.assertEqual(note.read_text(), '保留用户材料')
        self.assertTrue(repeated['data']['skill_update_available'])

    def test_channel_checksum_and_permissions_are_verified_before_install(self):
        self.channel['installer_sha256'] = '0' * 64
        self.channel_path.write_text(json.dumps(self.channel))
        result, error = self.call('ensure')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(error['error']['code'], 'REMOTE_SETUP_FAILED')
        self.assertFalse(self.prefix.exists())
        self.channel_path.chmod(0o666)
        with self.assertRaises(bootstrap.Failure):
            bootstrap.release(self.releases)


if __name__ == '__main__':
    unittest.main()
