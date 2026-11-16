import json,os,tempfile,unittest,socket
from pathlib import Path
from unittest.mock import patch
from cosmesh.client import hub
from cosmesh.spaces import connection
from cosmesh.errors import Error

class ClientHubTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.prefix=self.root/'prefix';self.prefix.mkdir()
  self.path=self.prefix/'client.json';self.old={'source':'/srv/software/agent','ssh':'update-host','mesh_root':'existing','other':{'keep':True}}
  self.path.write_text(json.dumps(self.old));self.service=self.root/'service.json'
  self.service.write_text(json.dumps({'format':1,'owner_uid':os.getuid(),'endpoint':'test-endpoint','hosts':[socket.gethostname()],'socket':str(self.root/'test.sock')}));self.service.chmod(0o600)
  env=patch.dict(os.environ,{'COSMESH_INSTALL_PREFIX':str(self.prefix),'COSMESH_SERVICE':''});env.start();self.addCleanup(env.stop)
 def test_save_only_hub_and_default_connection(self):
  reply=hub(str(self.service));self.assertTrue(reply['saved']);value=json.loads(self.path.read_text())
  self.assertEqual({k:v for k,v in value.items() if k!='default_hub'},self.old)
  self.assertEqual(connection(self.root),reply['default_hub']);self.assertEqual(hub()['default_hub'],reply['default_hub'])
 def test_explicit_routes_win(self):
  hub(str(self.service));self.assertEqual(connection(self.root,'another-host')['host'],'another-host')
  self.assertEqual(connection(self.root,service='/srv/other.json')['discovery'],'/srv/other.json')
  with patch.dict(os.environ,{'COSMESH_SERVICE':str(self.service)}):self.assertEqual(connection(self.root)['discovery'],str(self.service))
 def test_existing_workbench_route_preserved(self):
  hub(str(self.service));(self.root/'.cosmesh').mkdir();(self.root/'.cosmesh/config.json').write_text('{}')
  remote={'kind':'system','discovery':'/srv/old.json','repo':'old'}
  with patch('cosmesh.spaces.Store') as store:
   store.return_value.client.return_value={'remote':remote}
   self.assertEqual(connection(self.root)['discovery'],remote['discovery'])
 def test_dry_run_and_invalid_path_preserve_config(self):
  before=self.path.read_bytes();self.assertFalse(hub(str(self.service),True)['saved']);self.assertEqual(before,self.path.read_bytes())
  with self.assertRaises(Error):hub(str(self.root/'missing.json'))
  self.assertEqual(before,self.path.read_bytes())
 def test_legacy_ssh_install_still_works(self):
  self.assertEqual(connection(self.root)['host'],'update-host')
