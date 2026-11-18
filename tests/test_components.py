"""Optional components are packaged separately from the Python client; no live host access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('build_component', ROOT / 'tools/build_component.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ComponentPackagingTests(unittest.TestCase):
    def fixture(self, root):
        component = root / 'components/dsh-connector'
        component.mkdir(parents=True)
        (root / 'components/catalog.json').write_text(json.dumps({'components': [{'id': 'dsh-connector', 'source': 'dsh-connector'}]}))
        files = ['index.js', 'cordis.patch.yml', 'locale', 'icon.svg']
        (component / 'package.json').write_text(json.dumps({'name': '@test/connector', 'version': '0.1.0', 'files': files}))
        (component / 'locale').mkdir()
        for name in ('index.js', 'cordis.patch.yml', 'locale/en.json', 'locale/zh.json', 'icon.svg'):
            (component / name).write_text('synthetic-resource')
        return component

    def test_actual_candidate_matches_source_and_declared_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            result = builder.build('dsh-connector', Path(directory))
            candidate = Path(result['directory'])
            manifest = json.loads((candidate / 'manifest.json').read_text())
            source = ROOT / 'components/dsh-connector'
            with zipfile.ZipFile(candidate / 'bundle.zip') as archive:
                self.assertEqual(set(archive.namelist()), {'bundle/' + name for name in manifest['files']})
                for name, info in manifest['files'].items():
                    data = (candidate / 'bundle' / name).read_bytes()
                    self.assertEqual(data, (source / name).read_bytes())
                    self.assertEqual(builder.sha(data), info['sha256'])
                    self.assertEqual(data, archive.read('bundle/' + name))
            self.assertFalse(result['installed'])
            self.assertFalse(result['published'])
            self.assertNotIn('test/core.test.mjs', manifest['files'])
            self.assertNotIn('VERIFICATION.md', manifest['files'])
            with self.assertRaises(FileExistsError):
                builder.build('dsh-connector', Path(directory))

    def test_connector_guide_is_available_offline_with_fixed_sections(self):
        from cosmesh import guide
        doc = guide.document('dsh')
        self.assertIn('可选 DSH Connector（独立组件）', doc['text'])
        self.assertIn('不随 0.4.7 CLI', doc['text'])
        self.assertIn('initialize:false', doc['text'])
        self.assertIn('cosmesh_open_work', doc['text'])
        for section in doc['sections']:
            text = '\n'.join(doc['text'].splitlines()[section['start_line'] - 1:section['end_line']]) + '\n'
            self.assertEqual(guide.show('dsh', section['section'])['text'], text)
        self.assertTrue(any(r['topic'] == 'dsh' for r in guide.search('DSH Connector')['matches']))

    def test_reproducible_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a = builder.build('dsh-connector', root / 'a')
            b = builder.build('dsh-connector', root / 'b')
            self.assertEqual(a['archive_sha256'], b['archive_sha256'])

    def test_symlinks_and_unsafe_resources_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            component = self.fixture(root)
            outside = root / 'private.txt'
            outside.write_text('must-not-package')
            (component / 'linked').symlink_to(outside)
            manifest = component / 'package.json'
            values = json.loads(manifest.read_text())
            values['files'].append('linked')
            manifest.write_text(json.dumps(values))
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                builder.build('dsh-connector', root / 'output', root)
            for unsafe in ('../secret', '/secret', '.', './index.js', 'a/../secret', '.env', 'a\\secret'):
                with self.assertRaises(ValueError, msg=unsafe):
                    builder.relative(unsafe)

    def test_lifecycle_scripts_and_missing_resources_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            component = self.fixture(root)
            manifest = component / 'package.json'
            values = json.loads(manifest.read_text())
            values['scripts'] = {'install': 'unexpected-command'}
            manifest.write_text(json.dumps(values))
            with self.assertRaisesRegex(ValueError, 'lifecycle'):
                builder.build('dsh-connector', root / 'output', root)
            values.pop('scripts')
            values['files'].append('missing.js')
            manifest.write_text(json.dumps(values))
            with self.assertRaisesRegex(ValueError, 'Missing'):
                builder.build('dsh-connector', root / 'output', root)


if __name__ == '__main__':
    unittest.main()
