"""Private source metadata crosses hosts; local managed paths retain native checks."""
import unittest
from contextlib import ExitStack
from pathlib import PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch

from cosmesh.errors import Error
from cosmesh.source_paths import absolute_source_path, display_path, manifest_path
from cosmesh.store import Store, check_windows_path, safe_path
from cosmesh.thread_execution_model import normalize_workdir


class WindowsPathTests(unittest.TestCase):
    def test_windows_paths_are_absolute_for_stock_hub_and_round_trip(self):
        for native in (r'C:\研究\目录', r'\\server\share\project', 'D:\\'):
            with self.subTest(native=native):
                wire = manifest_path(native)
                self.assertTrue(PurePosixPath(wire).is_absolute())
                self.assertTrue(PureWindowsPath(wire).is_absolute())
                self.assertEqual(manifest_path(wire), wire)
                self.assertEqual(display_path(wire), native)
                self.assertEqual(normalize_workdir(wire), normalize_workdir(native))
        for value in ('/home/work', '//posix/path'):
            self.assertTrue(absolute_source_path(value))
            self.assertEqual(manifest_path(value), value)
        for value in ('//?/UNC/server/share/目录', '//?/unc/server/share/目录'):
            self.assertTrue(absolute_source_path(value))
            self.assertEqual(manifest_path(value), value)
            self.assertEqual(normalize_workdir(value), r'\\server\share\目录')
        for value in (None, '', 'C:relative', r'\rooted', '../file', 'a\0b', '/' * 4097):
            self.assertFalse(absolute_source_path(value))

    def test_managed_paths_reject_drive_and_current_drive_root(self):
        store = Store('.')
        with patch('cosmesh.store.Path', PureWindowsPath):
            for value in (r'D:relative', r'\rooted', r'D:\absolute', r'..\file'):
                with self.subTest(path=value), self.assertRaises(Error):
                    store.path(value)

    def test_windows_components_and_reparse_points(self):
        check_windows_path(PureWindowsPath(r'C:\项目\file.txt'))
        check_windows_path(PureWindowsPath(r'\\?\C:\project\file.txt'))
        for name in ('dir.', 'dir ', 'NUL.txt', 'file:stream', 'a?b', r'\\.\pipe\device'):
            with self.subTest(name=name), self.assertRaises(Error):
                check_windows_path(PureWindowsPath(name))
        path = PureWindowsPath(r'C:\project\folder\file.txt')
        def lstat(component):
            return SimpleNamespace(st_file_attributes=0x400 if component == path.parent else 0)
        with ExitStack() as stack:
            stack.enter_context(patch('cosmesh.store.Path', return_value=path))
            stack.enter_context(patch('cosmesh.store.os.name', 'nt'))
            stack.enter_context(patch('cosmesh.store.os.path.abspath', return_value=str(path)))
            stack.enter_context(patch.object(PureWindowsPath, 'expanduser', lambda p: p, create=True))
            stack.enter_context(patch.object(PureWindowsPath, 'lstat', lstat, create=True))
            with self.assertRaises(Error):
                safe_path(path)
