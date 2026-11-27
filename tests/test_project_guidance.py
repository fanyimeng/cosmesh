import codecs
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmesh import project_guidance as guidance


class ProjectGuidanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.file = self.root / 'AGENTS.md'

    def ensure(self, dry=False):
        return guidance.ensure(self.root, dry_run=dry)

    def test_preview_create_repeat_and_locate_current_binding(self):
        self.assertEqual(self.ensure(True)['status'], 'created')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.ensure()['missing'], ['startup', 'result'])
        data = self.file.read_bytes()
        self.assertIn(b'--bound-projects', data)
        self.assertNotIn(str(self.root).encode(), data)
        self.assertNotIn(b'W-', data)
        self.assertEqual(self.ensure()['reason'], 'guidance_present')
        self.assertEqual(data, self.file.read_bytes())

    def test_large_instructions_remain_identical_around_visible_bom_crlf_section(self):
        original = '---\r\nproject: sample\r\n---\r\n# Current rules\r\n\r\n' + ('原有指引\r\n' * 9000)
        self.file.write_bytes(codecs.BOM_UTF8 + original.encode())
        if os.name != 'nt':
            self.file.chmod(0o640)
        self.assertEqual(self.ensure()['status'], 'updated')
        data = self.file.read_bytes()
        self.assertTrue(data.startswith(codecs.BOM_UTF8))
        text = data[len(codecs.BOM_UTF8):].decode()
        self.assertLess(text.index(guidance.BEGIN), 128)
        self.assertTrue(text.endswith('原有指引\r\n' * 9000))
        self.assertNotIn('\n', text.replace('\r\n', ''))
        self.assertEqual(self.ensure()['status'], 'unchanged')
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o640)

    def test_keep_utf16_utf8_error_and_hand_edited_rules(self):
        original = '# 工作规则\r\n\r\n原有人写文字。'
        self.file.write_bytes(codecs.BOM_UTF16_LE + original.encode('utf-16-le'))
        self.assertEqual(self.ensure()['status'], 'updated')
        text = self.file.read_bytes().decode('utf-16')
        self.assertTrue(text.endswith('原有人写文字。'))
        self.file.write_bytes(text.replace('## CosMesh 工作记录', '## Human changes').encode('utf-16'))
        changed = self.file.read_bytes()
        self.assertEqual(self.ensure()['status'], 'skipped')
        self.assertEqual(self.file.read_bytes(), changed)
        self.file.write_bytes(b'\xff\xee legacy encoding')
        self.assertEqual(self.ensure()['status'], 'skipped')

    def test_links_to_local_workflow_and_historical_sections(self):
        self.file.write_text('先读README与项目交接说明。')
        readme = self.root / 'README.md'
        readme.write_text('[CosMesh 接续信息](docs/thread_link.txt)')
        (self.root / 'docs').mkdir()
        link = self.root / 'docs' / 'thread_link.txt'
        link.write_text('Read status:\ncosmesh thread page sample --level status --ready\n\n'
                        'Future incremental research notes should update this Thread.')
        self.assertEqual(self.ensure(True)['missing'], [])
        link.write_text('## 接入回执 (2026-01-01)\n\nCosMesh 已写入一条结果到 Thread。\n\n'
                        '后续在此目录使用 CosBase 时，先定位并续接该工作串。')
        self.assertEqual(self.ensure(True)['missing'], ['result'])
        link.write_text('这里管理 CosMesh 的工作记录。\n\n后续以普通 note/result 记录实际进展。')
        self.assertEqual(self.ensure(True)['missing'], ['startup'])

    def test_partial_existing_rule_and_release_history(self):
        self.file.write_text('## CosBase project\n\nUse the installed cosbase skill and this known ID for authorized milestone\nupdates.')
        self.assertEqual(self.ensure()['missing'], ['startup'])
        self.assertNotIn(guidance.RULES['result'], self.file.read_text())
        self.file.write_text('## 2026-11-16 版本发布\n\n已知 ID 的 Agent 接续用 thread continue ID --ready。\n'
                             '后续接续只用 thread personal 保存本人进展。\n'
                             '测试样例：更新结果并写入 Thread。')
        self.assertEqual(self.ensure(True)['missing'], ['result'])
        self.file.write_text('CosBase 工作以项目 Thread 为准。\n\n动手前先读该 Thread 的最近记录；做完追加结果。')
        self.assertEqual(self.ensure(True)['status'], 'unchanged')

    def test_opt_out_override_and_manual_authorization(self):
        for rule in ('本项目禁止使用 CosMesh。', 'Do not automatically edit AGENTS.md.',
                     '不要自动写入 Thread。', '必须经用户授权再使用 CosMesh。',
                     'Only use CosMesh when explicitly requested by the user.'):
            with self.subTest(rule=rule):
                self.file.write_text(rule)
                self.assertEqual(self.ensure()['status'], 'skipped')
                self.assertEqual(self.file.read_text(), rule)
        for rule in ('本项目使用 CosMesh。', '上传附件和共享 Thread 须由用户明确授权。'):
            self.file.write_text(rule)
            self.assertEqual(self.ensure(True)['status'], 'updated')
        (self.root / 'AGENTS.override.md').write_text('Use local instructions.')
        self.assertEqual(self.ensure()['status'], 'skipped')

    def test_only_explicit_safe_instruction_sources(self):
        self.file.write_text('资料见 [操作](data/guide.md)、[外部](../another/README.md)、[项目指引](docs/workflow.md)。')
        (self.root / 'data').mkdir()
        (self.root / 'docs').mkdir()
        (self.root / 'data' / 'guide.md').write_text('不要使用 CosMesh。')
        (self.root / 'docs' / 'workflow.md').write_text('通过本人 Thread 接续并登记结果。')
        self.assertEqual(self.ensure(True)['missing'], [])

    @unittest.skipIf(os.name == 'nt', 'POSIX links, modes and commit primitives')
    def test_refuse_symlinks_hardlinks_readonly_and_unsafe_roots(self):
        outside = self.root / 'elsewhere'
        outside.write_text('keep')
        self.file.symlink_to(outside)
        self.assertEqual(self.ensure()['status'], 'skipped')
        self.file.unlink()
        os.link(outside, self.file)
        self.assertEqual(self.ensure()['status'], 'skipped')
        self.file.unlink()
        self.file.write_text('keep')
        self.file.chmod(0o444)
        self.assertEqual(self.ensure()['status'], 'pending')
        self.assertEqual(outside.read_text(), 'keep')
        internal = self.root / '.git'
        internal.mkdir()
        for path in (Path.home(), Path('/'), Path('/usr'), internal):
            self.assertEqual(guidance.ensure(path, dry_run=True)['status'], 'skipped')

    @unittest.skipIf(os.name == 'nt', 'POSIX commit race injection')
    def test_concurrent_replacement_and_creation_preserve_competing_content(self):
        self.file.write_text('# Before')
        real_copy = guidance._copy_permissions

        def concurrent(source, dest):
            real_copy(source, dest)
            other = self.root / 'external-edit'
            other.write_text('# External')
            os.replace(other, self.file)

        with patch.object(guidance, '_copy_permissions', side_effect=concurrent):
            self.assertEqual(self.ensure()['status'], 'pending')
        self.assertEqual(self.file.read_text(), '# External')
        self.file.unlink()
        link = os.link

        def intervene(*args, **kwargs):
            self.file.write_text('# Created elsewhere')
            return link(*args, **kwargs)

        with patch.object(os, 'link', side_effect=intervene):
            self.assertEqual(self.ensure()['status'], 'pending')
        self.assertEqual(self.file.read_text(), '# Created elsewhere')
        self.assertEqual(list(self.root.iterdir()), [self.file])
