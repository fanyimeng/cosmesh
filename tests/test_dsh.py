"""DSH uses the same portable Skills and preserves user customizations."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh.cli import main
from cosmesh.errors import Error
from cosmesh.skills import AGENTS, install


class DshTests(unittest.TestCase):
    def test_cli_install_and_doctor_use_the_shared_skill_format(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();target=root/'skills';bin_dir=root/'bin';bin_dir.mkdir()
            executable=bin_dir/'dsh';executable.write_text('#!/bin/sh\nexit 0\n');executable.chmod(0o700)
            env={'COSMESH_INSTALL_PREFIX':'','COSMESH_DATA_DIR':str(root/'data'),
                 'COSMESH_PROFILE_DIR':str(root/'profile'),'PATH':str(bin_dir)+os.pathsep+os.environ['PATH']}
            with patch.dict(os.environ,env),redirect_stdout(io.StringIO()) as output:
                code=main(['skills','install','--agent','dsh','--only','both','--dest',str(target),'--json'])
            value=json.loads(output.getvalue());self.assertEqual(code,0,value)
            for name in ('cosbase','cosmesh'):
                self.assertTrue((target/name/'SKILL.md').is_file())
            with patch.dict(os.environ,env),redirect_stdout(io.StringIO()) as output:
                code=main(['doctor','--agent','dsh','--dest',str(target),'--root',str(root/'base'),'--json'])
            value=json.loads(output.getvalue());self.assertEqual(code,0,value)
            self.assertIn('dsh',value['data']['capabilities']['agent_targets'])
            self.assertEqual(value['data']['agent']['agent_binary'],str(executable))
            self.assertTrue(all(s['status']=='installed_identical' for s in value['data']['agent']['skills']))
            custom=target/'cosbase/SKILL.md';custom.write_text('User customization')
            with patch.dict(os.environ,env),self.assertRaises(Error):install('dsh',['cosbase','cosmesh'],target)
            self.assertEqual(custom.read_text(),'User customization')

    def test_installer_and_client_offer_the_same_agent_choices(self):
        from tools.release_installer import AGENTS as release_agents
        self.assertEqual(AGENTS,release_agents)
