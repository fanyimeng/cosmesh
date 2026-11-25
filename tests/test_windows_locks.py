"""Close-only release in a long-lived collector and bounded lock error handling."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from cosmesh import _fcntl


class LockTests(unittest.TestCase):
    def test_second_round_observes_another_process_after_close(self):
        # Collector.run takes flock anew every time and relies on close to unlock.
        holder = """
from cosmesh import _fcntl
import sys
with open(sys.argv[1], "a+b") as lock:
    _fcntl.flock(lock, _fcntl.LOCK_EX)
    print("acquired", flush=True)
    sys.stdin.readline()
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "collector.lock"
            with path.open("a+b") as lock:
                _fcntl.flock(lock, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            with subprocess.Popen([sys.executable, "-c", holder, str(path)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True) as child:
                try:
                    self.assertEqual(child.stdout.readline().strip(), "acquired")
                    with path.open("a+b") as lock:
                        with self.assertRaises(BlockingIOError):
                            _fcntl.flock(lock, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                finally:
                    child.communicate("\n", timeout=10)
            with path.open("a+b") as lock:
                _fcntl.flock(lock, _fcntl.LOCK_EX | _fcntl.LOCK_NB)

    def test_nested_store_context_keeps_outer_lock(self):
        from cosmesh.store import _store_lock
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "store.lock"
            with patch("cosmesh.store.fcntl.lockf", wraps=_fcntl.lockf) as lock:
                with self.assertRaisesRegex(ValueError, "test"), _store_lock(path):
                    with _store_lock(path):
                        self.assertEqual(lock.call_count, 1)
                    self.assertEqual(lock.call_count, 1)
                    raise ValueError("test")
                self.assertEqual(lock.call_count, 2)
                with _store_lock(path):
                    self.assertEqual(lock.call_count, 3)

    def test_byte_zero_position_errors_and_retry(self):
        crt = Mock(LK_NBLCK=2, LK_UNLCK=0)
        with tempfile.TemporaryFile("w+b") as lock:
            lock.write(b"position")
            lock.flush()

            def byte_zero(fd, operation, size):
                self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 0)
                self.assertEqual(size, 1)

            crt.locking.side_effect = byte_zero
            for op in (_fcntl.LOCK_EX, _fcntl.LOCK_UN):
                _fcntl._windows_lock(lock, op, crt)
                self.assertEqual(lock.tell(), 8)
            crt.locking.side_effect = [PermissionError(errno.EACCES, "busy"), None]
            _fcntl._windows_lock(lock, _fcntl.LOCK_EX, crt)
            crt.locking.side_effect = OSError(errno.EBADF, "bad descriptor")
            with self.assertRaises(OSError) as raised:
                _fcntl._windows_lock(lock, _fcntl.LOCK_EX, crt)
            self.assertEqual(raised.exception.errno, errno.EBADF)
            crt.locking.side_effect = PermissionError(errno.EACCES, "busy")
            with self.assertRaises(BlockingIOError):
                _fcntl._windows_lock(lock, _fcntl.LOCK_EX | _fcntl.LOCK_NB, crt)
            self.assertEqual(lock.tell(), 8)


if __name__ == "__main__":
    unittest.main()
