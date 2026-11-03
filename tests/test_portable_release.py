"""Portable releases keep the same protocol gate and explicit deployment path."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmesh import spaces
from tools import release_installer


def bootstrap():
    path = Path(__file__).resolve().parents[1]/'tools/local_bootstrap.py'
    spec = importlib.util.spec_from_file_location('portable_bootstrap_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, install=release_installer): spec.loader.exec_module(module)
    return module


class PortableReleaseTests(unittest.TestCase):
    def test_materialized_bundle_preserves_lifecycle_gate(self):
        module = bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'source';source.mkdir()
            for name in ('app.whl','install.py','requirements.lock'): (source/name).write_text('fixture')
            info={'version':'0.3.12','files':{},'wheel':'app.whl','schema_write':[1,2,3],'thread_lifecycle_version':2}
            (source/'portable.json').write_text(json.dumps(info))
            def download(args):
                target=Path(args[args.index('--dest')+1]);(target/'dependency.whl').write_bytes(b'fixture')
            with patch.object(module,'run',side_effect=download):
                bundle=module.materialize(source,root/'cache')
            manifest=json.loads((bundle/'release.json').read_text())
            self.assertEqual(manifest['thread_lifecycle_version'],2)
            self.assertEqual(manifest['schema_write'],[1,2,3])

    def test_install_remembers_and_uses_custom_hub_discovery(self):
        module=bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);prefix=root/'prefix';prefix.mkdir()
            argv=['bootstrap','--prefix',str(prefix),'--skills-dir',str(root/'skills'),
                  '--ssh','existing-hub','--source','/srv/team/agent']
            reply=type('Reply',(),{'stdout':'{"ok":true}', 'stderr':'', 'returncode':0})()
            with patch.object(sys,'argv',argv), patch.object(module,'materialize',return_value=root/'bundle'), \
                    patch.object(module.manager,'apply_release',return_value={'to':'0.3.12'}), \
                    patch.object(module.subprocess,'run',return_value=reply) as run, redirect_stdout(io.StringIO()):
                module.main()
            self.assertIn('--service',run.call_args.args[0])
            self.assertIn('/srv/team/service.json',run.call_args.args[0])
            self.assertEqual(json.loads((prefix/'client.json').read_text())['discovery'],'/srv/team/service.json')

    def test_saved_discovery_is_scoped_to_the_saved_host(self):
        saved={'ssh':'existing-hub','discovery':'/srv/team/service.json'}
        with patch.object(spaces,'settings',return_value=saved), patch.dict(os.environ,COSMESH_SERVICE='/srv/default/service.json'):
            self.assertEqual(spaces.connection(Path('/unused'),'existing-hub')['discovery'],saved['discovery'])
            self.assertEqual(spaces.connection(Path('/unused'),'other-hub')['discovery'],'/srv/default/service.json')
            self.assertEqual(spaces.connection(Path('/unused'),'existing-hub','/srv/explicit/service.json')['discovery'],'/srv/explicit/service.json')
