"""File locks for the CosMesh client. Native handles own Windows byte locks."""
import errno
import os
import sys
import time


def _windows_lock(target, operation, crt):
    fd = target if isinstance(target, int) else target.fileno()
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if operation == LOCK_UN:
            crt.locking(fd, crt.LK_UNLCK, 1)
            return
        if operation not in (LOCK_EX, LOCK_EX | LOCK_NB, LOCK_SH, LOCK_SH | LOCK_NB):
            raise ValueError("Unsupported file lock operation")
        # Callers take exclusive locks. Shared requests are serialized as well.
        while True:
            try:
                crt.locking(fd, crt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if operation & LOCK_NB:
                    raise BlockingIOError(errno.EAGAIN, "File lock is busy") from exc
                time.sleep(0.02)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


if sys.platform == "win32":
    import msvcrt

    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8

    def lockf(target, operation):
        _windows_lock(target, operation, msvcrt)

    flock = lockf
else:
    from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN, flock, lockf
