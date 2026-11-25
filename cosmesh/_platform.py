"""Local account and private-file checks used by the personal client."""
import os
import stat
import sys
import time

from .errors import Error

WINDOWS = sys.platform == "win32"
LOCK_DEADLINE = 15.0 if WINDOWS else 3.0


def account_name():
    if WINDOWS:
        from ._win_private import username
        return username()
    import pwd
    return pwd.getpwuid(os.geteuid()).pw_name


def cache_user():
    """Separate host-local caches by the actual local OS principal."""
    if WINDOWS:
        from ._win_private import current_sid
        return current_sid()
    return str(os.geteuid())


def private_dacl(user, owner, entries):
    """Accept ordinary Windows ACLs granting access only to trusted principals."""
    if owner != user or entries is None:
        return False
    allowed = {user, 'S-1-5-18', 'S-1-5-32-544'}  # Local System and Administrators
    for ace_type, flags, sid in entries:
        if ace_type == 1:  # ACCESS_DENIED_ACE
            continue
        if ace_type != 0:
            return False
        if sid not in allowed and not (flags & 0x08 and sid == 'S-1-3-0'):
            return False  # CREATOR OWNER only applies upon inheritance.
    return True


def private_path(path, info=None, *, owner_only=False):
    if WINDOWS:
        from ._win_private import trusted_path
        return trusted_path(path)
    if info is None:
        info = path.stat()
    return info.st_uid == os.geteuid() and (owner_only or not info.st_mode & 0o077)


def replace_managed_file(temporary, destination):
    """Short-lived readers may delay renaming an existing file on Windows."""
    if not WINDOWS:
        os.replace(temporary, destination)
        return
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(temporary, destination)
            return
        except OSError as exc:
            # ACCESS_DENIED, SHARING_VIOLATION, LOCK_VIOLATION. Real ACL
            # failures keep the original file and surface after the same bound.
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or time.monotonic() >= deadline:
                raise
            time.sleep(0.025)


def private_fd(path, error, *, create_lock=False):
    """Open a regular, single-link file and validate the very handle being used."""
    if WINDOWS:
        from ._win_private import open_fd
        fd = open_fd(path, error, create_lock)
    else:
        flags = os.O_CREAT | os.O_RDWR if create_lock else os.O_RDONLY
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        # GetFileInformationByHandle already checked the Windows link count.
        if (not stat.S_ISREG(info.st_mode) or
                (not WINDOWS and (info.st_nlink != 1 or not private_path(path, info)))):
            raise Error(error, "Expected a privately owned regular file with a single link.")
        return fd
    except BaseException:
        os.close(fd)
        raise
