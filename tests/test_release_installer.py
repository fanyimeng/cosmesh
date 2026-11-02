import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import release_installer as release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()), prefix='release-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root/'app'
        self.prefix.mkdir()

    def test_switch_failure_rolls_back_skills_and_state(self):
        skill = self.root/'skills/cosbase/SKILL.md'
        release.atomic(skill,b'original')
        original = release.atomic
        def fail_state(path, data, mode=0o600):
            if Path(path) == self.prefix/'state.json':
                raise OSError('injected activation failure')
            original(path,data,mode)
        with patch.object(release,'atomic',side_effect=fail_state):
            with self.assertRaises(OSError):
                release.switch_files(self.prefix,[(skill,b'new',0o600)],{'current':'0.1.1'})
        self.assertEqual(skill.read_bytes(),b'original')
        self.assertFalse((self.prefix/'state.json').exists())
        self.assertFalse((self.prefix/'pending.json').exists())

    def test_new_lifecycle_records_have_a_rollback_compatibility_gate(self):
        release.atomic(self.prefix/'owner.json',release.json_bytes(release.MARKER))
        state={'format':1,'current':'0.3.7','previous':'0.3.6','skills':{},
               'versions':{v:{'schema_write':[1,2,3],'manifest_sha256':v} for v in ('0.3.6','0.3.7')}}
        release.atomic(self.prefix/'state.json',release.json_bytes(state))
        for version,level in [('0.3.6',1),('0.3.7',2)]:
            release.atomic(self.prefix/'versions'/version/'release.json',release.json_bytes({'thread_lifecycle_version':level}))
        with self.assertRaises(release.Failure) as raised:release.rollback(self.prefix,'0.3.6',dry_run=True)
        self.assertEqual(raised.exception.code,'MIGRATION_REQUIRED')
        self.assertEqual(json.loads((self.prefix/'state.json').read_text()),state)

    def test_recovery_preserves_unrelated_user_edit(self):
        file = self.root/'skills/cosbase/SKILL.md'
        release.atomic(file,b'user edit')
        journal = {'before':{str(file):base64.b64encode(b'old').decode()},
                   'after':{str(file):base64.b64encode(b'new').decode()},'modes':{str(file):0o600}}
        release.atomic(self.prefix/'pending.json',release.json_bytes(journal))
        with self.assertRaises(release.Failure) as context:
            release.recover(self.prefix)
        self.assertEqual(context.exception.code,'RECOVERY_REQUIRED')
        self.assertEqual(file.read_bytes(),b'user edit')

    def test_skill_preflight_refuses_unmanaged_and_modified(self):
        path = self.root/'skills/cosbase/SKILL.md'
        release.atomic(path,b'mine')
        with self.assertRaises(release.Failure):
            release.preflight_skills(None,['cosbase'],self.root/'skills')
        state = {'skills':{'cosbase':{'path':str(path),'sha256':release.digest(b'old')}}}
        with self.assertRaises(release.Failure):
            release.preflight_skills(state)
        self.assertEqual(path.read_bytes(),b'mine')

    def test_existing_non_install_directory_is_preserved(self):
        file = self.prefix/'important.txt'
        file.write_text('preserve me')
        with self.assertRaises(release.Failure) as context:
            with release.locked(self.prefix,create=True):
                pass
        self.assertEqual(context.exception.code,'PREFIX_CONFLICT')
        self.assertEqual(file.read_text(),'preserve me')

    def test_interrupted_first_install_status_is_an_actionable_error(self):
        release.atomic(self.prefix/'owner.json',release.json_bytes(release.MARKER))
        with self.assertRaises(release.Failure) as context:
            release.status(self.prefix)
        self.assertEqual(context.exception.code,'NOT_INSTALLED')

    def test_symbolic_skill_directory_is_preserved(self):
        original = self.root/'original'
        original.mkdir()
        (self.root/'alias').symlink_to(original,target_is_directory=True)
        with self.assertRaises(release.Failure) as context:
            release.canonical(self.root/'alias/SKILL.md')
        self.assertEqual(context.exception.code,'PATH_CONFLICT')

    def test_upgrade_provision_failure_keeps_active_version(self):
        state = {'format':1,'current':'0.1.0','versions':{'0.1.0':{'schema_write':[1],'manifest_sha256':'old'}},'skills':{}}
        release.atomic(self.prefix/'owner.json',release.json_bytes(release.MARKER))
        release.atomic(self.prefix/'state.json',release.json_bytes(state))
        meta = {'version':'0.1.1','schema_write':[1],'manifest_sha256':'new'}
        with patch.object(release,'bundle',return_value=(self.root,meta)), patch.object(
                release,'prepare',side_effect=release.Failure('INSTALL_FAILED','injected pip failure')):
            with self.assertRaises(release.Failure):
                release.apply_release(self.prefix,self.root,'upgrade')
        self.assertEqual(release.load_state(self.prefix),state)

    def test_version_reuse_with_different_payload_is_refused(self):
        state = {'format':1,'current':'0.1.1','versions':{'0.1.1':{'schema_write':[1],'manifest_sha256':'original'}},'skills':{}}
        release.atomic(self.prefix/'state.json',release.json_bytes(state))
        meta = {'version':'0.1.1','schema_write':[1],'manifest_sha256':'changed'}
        with patch.object(release,'bundle',return_value=(self.root,meta)):
            with self.assertRaises(release.Failure) as context:
                release.apply_release(self.prefix,self.root,'upgrade',dry_run=True)
        self.assertEqual(context.exception.code,'IMMUTABLE_RELEASE')
        self.assertEqual(release.load_state(self.prefix),state)


if __name__=='__main__':
    unittest.main()
