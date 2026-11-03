"""Shareable prompts exclude inviter data; Profile onboarding requires explicit saving."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh import __version__
from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.onboarding import installation_prompt
from cosmesh.profile import PersonalProfile
from cosmesh.profile_onboarding import import_preview, interactive


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cosmesh-onboarding-', dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = PersonalProfile(self.root/'profile')
        self.source = self.root/'cv.md'
        self.source.write_text('姓名：张三\n机构：示例大学\n邮箱：reader@example.invalid\n语言：中文\n简介：研究星际介质。\n')

    def test_prompts_for_three_agents_and_two_modes_use_real_installers(self):
        for agent in ('codex', 'opencode', 'dsh'):
            server = installation_prompt('server', agent, source='/srv/team/agent', service='/srv/team/public.json')
            self.assertIn('--only both', server['prompt'])
            self.assertIn('/srv/team/agent/install.sh', server['prompt'])
            self.assertIn('--service /srv/team/public.json', server['prompt'])
            self.assertIn('YOUR_SSH_ALIAS', server['prompt'])
            self.assertIn('user me', server['prompt'])
            self.assertIn('thread status', server['prompt'])
            github = installation_prompt('github', agent)
            self.assertIsNone(github['server'])
            self.assertIn('v'+__version__, github['prompt'])
            self.assertIn('requirements-release.txt', github['prompt'])
            self.assertIn("--upgrade pip 'setuptools>=61' wheel", github['prompt'])
            self.assertIn('tools/build_release.py', github['prompt'])
            self.assertIn('install.py install --agent '+agent, github['prompt'])

    def test_cached_public_fields_only_and_github_never_reads_connection(self):
        work=self.root/'work';(work/'.cosmesh').mkdir(parents=True)
        (work/'.cosmesh/thread-client.json').write_text(json.dumps({'connection':{'kind':'system','discovery':'/srv/team/service.json','token':'PRIVATE_TOKEN'},'workdir':'PRIVATE_WORKDIR'}))
        saved={'source':'/srv/team/agent','discovery':'/srv/team/service.json','ssh':'PRIVATE_INVITER_ALIAS','profile':'PRIVATE_PROFILE','identity':'PRIVATE_KEY'}
        with patch('cosmesh.spaces.settings',return_value=saved):
            text=json.dumps(installation_prompt('server','dsh',root=work))
            self.assertNotIn('PRIVATE_',text)
            self.assertFalse((work/'.cosmesh/config.json').exists())
            override=installation_prompt('server','dsh',root=work,source='/srv/other/agent')
            self.assertEqual(override['server']['service'],'/srv/other/service.json')
            service=installation_prompt('server','dsh',root=work,service='/srv/another/service.json')
            self.assertEqual(service['server']['source'],'/srv/another/agent')
        with patch('cosmesh.spaces.settings',side_effect=AssertionError('GitHub must not read inviter data')):
            installation_prompt('github','dsh')

    def test_missing_connection_and_invalid_modes_are_actionable(self):
        with patch('cosmesh.spaces.settings',return_value={}):
            with self.assertRaises(Error) as e:installation_prompt('server','codex',root=self.root/'missing')
            self.assertEqual(e.exception.code,'HUB_CONNECTION_REQUIRED')
        for options in ({'ssh':'-bad'},{'source':'/srv/team;command'},{'version':'main'}):
            with self.assertRaises(Error):installation_prompt('server','codex',**options)
        with self.assertRaises(Error):installation_prompt('github','dsh',source='/srv/private/agent')

    def test_import_preview_does_not_create_or_overwrite_profile(self):
        first=import_preview(self.profile,self.source)
        self.assertEqual(first['proposed_fields']['display_name'],'张三')
        self.assertEqual(first['proposed_fields']['language'],'zh-CN')
        self.assertFalse(first['persisted']);self.assertFalse(self.profile.directory.exists())
        old=self.profile.set({'organization':'原机构','preferences':'保留此偏好'},'missing');raw=self.profile.path.read_bytes()
        view=import_preview(self.profile,self.source)
        self.assertEqual(view['source_version'],old['version'])
        self.assertEqual(self.profile.path.read_bytes(),raw)
        self.assertNotIn('preferences',view['proposed_fields'])
        saved=self.profile.set(view['proposed_fields'],view['source_version'])
        self.assertEqual(saved['fields']['preferences'],'保留此偏好')
        with self.assertRaises(Error):self.profile.set(view['proposed_fields'],view['source_version'])

    def test_unstructured_text_ambiguity_and_agent_proposals(self):
        self.source.write_text('姓名：张三\n姓名：李四\n语言：待确认\n这是一份自由格式的研究简介。\n')
        view=import_preview(self.profile,self.source,chars=18)
        self.assertNotIn('display_name',view['proposed_fields'])
        self.assertIn('display_name',view['ambiguous_fields'])
        self.assertNotIn('language',view['proposed_fields'])
        self.assertTrue(view['truncated']);self.assertIsNotNone(view['next_offset'])
        candidates=self.root/'fields.json';candidates.write_text(json.dumps({'about':'用户确认前的候选简介。'}))
        chosen=import_preview(self.profile,self.source,str(candidates))
        self.assertEqual(chosen['candidate_origin'],'agent_supplied')
        self.assertFalse(self.profile.directory.exists())
        candidates.write_text('{"unknown": "value"}')
        with self.assertRaises(Error):import_preview(self.profile,self.source,str(candidates))

    def test_docx_reuses_existing_extractor_and_scanned_pdf_does_not_guess(self):
        document=self.root/'cv.docx'
        with zipfile.ZipFile(document,'w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="urn:example"><w:p><w:r><w:t>姓名：张三</w:t></w:r></w:p></w:document>')
        self.assertEqual(import_preview(self.profile,document)['proposed_fields']['display_name'],'张三')
        from pypdf import PdfWriter
        pdf=self.root/'scan.pdf';writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        with pdf.open('wb') as f:writer.write(f)
        scanned=import_preview(self.profile,pdf)
        self.assertEqual(scanned['source']['status'],'needs_ocr')
        self.assertEqual(scanned['proposed_fields'],{})
        self.assertFalse(self.profile.directory.exists())

    def test_interactive_cancel_confirm_skip_and_dry_run(self):
        answers='张三\n示例机构\n\nzh-CN\nAsia/Shanghai\n\n简短简介\n'
        with patch('sys.stdin',io.StringIO(answers+'no\n')),patch('sys.stderr',io.StringIO()):
            self.assertTrue(interactive(self.profile)['cancelled'])
        self.assertFalse(self.profile.directory.exists())
        with patch('sys.stdin',io.StringIO(answers)),patch('sys.stderr',io.StringIO()):
            self.assertFalse(interactive(self.profile,dry_run=True)['persisted'])
        with patch('sys.stdin',io.StringIO(answers+'确认\n')),patch('sys.stderr',io.StringIO()):
            self.assertTrue(interactive(self.profile)['persisted'])
        before=self.profile.path.read_bytes()
        with patch('sys.stdin',io.StringIO('\n'*7)),patch('sys.stderr',io.StringIO()):
            self.assertFalse(interactive(self.profile)['persisted'])
        self.assertEqual(self.profile.path.read_bytes(),before)

    def test_contact_preview_preserves_existing_encryption(self):
        self.profile.set({'contact':'old@example.invalid'},'missing',encrypt_contact=True,password='test-only')
        before=self.profile.path.read_bytes();view=import_preview(self.profile,self.source)
        diff=next(d for d in view['diff'] if d['field']=='contact')
        self.assertEqual(diff['before'],'[已加密]')
        self.assertEqual(self.profile.path.read_bytes(),before)

    def test_cli_envelopes_and_copyable_prompt_do_not_initialize_stores(self):
        env={'COSMESH_PROFILE_DIR':str(self.profile.directory),'COSMESH_DATA_DIR':str(self.root/'data'),'COSMESH_INSTALL_PREFIX':''}
        for product in ('cosbase','cosmesh'):
            with patch.dict(os.environ,env),redirect_stdout(io.StringIO()) as out:
                code=main(['invite','prompt','--mode','github','--agent','dsh','--json'],product=product)
            result=json.loads(out.getvalue());self.assertEqual(code,0,result);self.assertIsNone(result['sync']);self.assertEqual(result['warnings'],[])
            with patch.dict(os.environ,env),redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['profile','import','--file',str(self.source),'--dry-run','--json'],product=product),0)
            self.assertFalse(json.loads(out.getvalue())['data']['persisted'])
        with patch.dict(os.environ,env),redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['invite','prompt','--mode','github','--agent','dsh']),0)
        self.assertTrue(out.getvalue().startswith('请帮我'))
        self.assertFalse((self.root/'data').exists());self.assertFalse(self.profile.directory.exists())


if __name__ == '__main__':
    unittest.main()
