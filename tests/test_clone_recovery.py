"""Shared-home launchers and failed first clones remain usable and traceable."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmesh import sync
from cosmesh.errors import Error
from cosmesh.hub import receive_hook
from cosmesh.store import Store
from test_threads import ThreadFixture


class LauncherTests(unittest.TestCase):
    def test_remote_transport_reads_manager_instead_of_executing_wrapper(self):
        with tempfile.TemporaryDirectory() as folder:
            prefix=Path(folder)/'.local/opt/cosmesh';(prefix/'bin').mkdir(parents=True)
            (prefix/'bin/cosmesh').write_text('This wrapper must never execute.')
            (prefix/'manager.py').write_text('import json,sys;print(json.dumps(sys.argv[1:]))')
            config={'discovery':'/srv/team/service.json','repo':'team'}
            env=dict(os.environ,HOME=folder,PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
            result=subprocess.run(['sh','-c',sync.system_command(config,'git-upload-pack')],capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)[:3],['launch','cosmesh','_remote'])
            self.assertIn('manager.py',receive_hook(prefix/'bin/cosmesh',Path(folder)/'hub','team'))


class CloneRecoveryTests(ThreadFixture):
    participants=('alice','bob')

    def test_failed_fetch_leaves_no_half_initialized_destination(self):
        target=self.root/'new-clone';config=dict(self.connection('bob'),repo=self.repos['alice'])
        original=Store.git
        def failing(store,*args,**kwargs):
            if args[0]=='fetch':raise Error('GIT_FAILED','Interrupted fixture transfer.')
            return original(store,*args,**kwargs)
        with patch.object(Store,'git',new=failing),self.assertRaises(Error):
            sync.clone(target,'cosmesh',config,publish=False)
        self.assertFalse(target.exists());self.assertFalse(list(target.parent.glob('.new-clone-clone-*')))
        sync.clone(target,'cosmesh',config,publish=False)
        self.assertTrue((target/'.cosmesh/config.json').is_file())

    def test_old_unborn_mesh_clone_is_preserved_before_retry(self):
        wb=self.wbs['bob'];repo=self.repos['alice'];target=wb.base.root/'.cosmesh/mesh'/repo
        shutil.rmtree(target);target.mkdir(mode=0o700)
        store=Store(target);store.git('init','-q');store.git('symbolic-ref','HEAD','refs/heads/main')
        marker=target/'.git/fixture-marker';marker.write_text('Keep this original diagnostic.')
        with self.use('bob') as current:
            result=current.discover();self.assertTrue(all(x['cached'] for x in result['spaces']),result)
            preserved=Path(current.client['preserved_clone']['path'])
            self.assertEqual((preserved/'.git/fixture-marker').read_text(),'Keep this original diagnostic.')
            self.assertTrue((target/'.cosmesh/config.json').is_file())
