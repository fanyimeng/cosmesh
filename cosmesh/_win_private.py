"""NTFS owner and DACL validation for the Windows personal client.

GetSecurityInfo returns the owner and DACL for an open handle. Only the user,
Local System and built-in Administrators may receive access. Inheritable
grants are checked as well, since managed directories hold private children.
"""
import ctypes
from ctypes import wintypes as w
from functools import lru_cache
import os

from .errors import Error
from ._platform import private_dacl

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
advapi = ctypes.WinDLL("advapi32", use_last_error=True)
PTR = ctypes.c_void_p
PPTR = ctypes.POINTER(PTR)
DWORD_PTR = ctypes.POINTER(w.DWORD)
READ_CONTROL = 0x00020000
FILE_READ_ATTRIBUTES = 0x0080
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
FILE_ATTRIBUTE_DIRECTORY = 0x10
INVALID_HANDLE = PTR(-1).value


def api(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


create_file = api(kernel, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, PTR, w.DWORD, w.DWORD, w.HANDLE)
close = api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
local_free = api(kernel, "LocalFree", PTR, PTR)
process = api(kernel, "GetCurrentProcess", w.HANDLE)
open_token = api(advapi, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, PPTR)
token_info = api(advapi, "GetTokenInformation", w.BOOL, w.HANDLE, ctypes.c_int, PTR, w.DWORD, DWORD_PTR)
to_sid_text = api(advapi, "ConvertSidToStringSidW", w.BOOL, PTR, PPTR)
get_security = api(advapi, "GetSecurityInfo", w.DWORD, w.HANDLE, ctypes.c_int, w.DWORD,
                   PPTR, PPTR, PPTR, PPTR, PPTR)
get_ace = api(advapi, "GetAce", w.BOOL, PTR, w.DWORD, PPTR)
handle_info = api(kernel, "GetFileInformationByHandle", w.BOOL, w.HANDLE, PTR)
get_username = api(advapi, "GetUserNameW", w.BOOL, w.LPWSTR, DWORD_PTR)


def checked(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def username():
    buf = ctypes.create_unicode_buffer(257)
    size = w.DWORD(len(buf))
    checked(get_username(buf, ctypes.byref(size)))
    return buf.value


def sid_text(sid):
    value = PTR()
    checked(to_sid_text(sid, ctypes.byref(value)))
    try:
        return ctypes.wstring_at(value)
    finally:
        local_free(value)


@lru_cache(maxsize=1)
def current_sid():
    token = PTR()
    checked(open_token(process(), 0x0008, ctypes.byref(token)))  # TOKEN_QUERY
    try:
        size = w.DWORD()
        token_info(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        checked(token_info(token, 1, buffer, len(buffer), ctypes.byref(size)))
        return sid_text(PTR.from_buffer(buffer).value)
    finally:
        close(token)


class ACL(ctypes.Structure):
    _fields_ = [("revision", w.BYTE), ("reserved", w.BYTE), ("size", w.WORD),
                ("count", w.WORD), ("reserved2", w.WORD)]


class FileInformation(ctypes.Structure):
    _fields_ = [("attributes", w.DWORD), ("created", w.FILETIME), ("accessed", w.FILETIME),
                ("modified", w.FILETIME), ("volume", w.DWORD), ("size_high", w.DWORD),
                ("size_low", w.DWORD), ("links", w.DWORD), ("index_high", w.DWORD),
                ("index_low", w.DWORD)]


def acl_entries(acl):
    for n in range(ACL.from_address(acl.value).count):
        ace = PTR()
        checked(get_ace(acl, n, ctypes.byref(ace)))
        ace_type = ctypes.c_ubyte.from_address(ace.value).value
        flags = ctypes.c_ubyte.from_address(ace.value + 1).value
        # Only a standard ACCESS_ALLOWED_ACE has the SID at this offset.
        sid = sid_text(ace.value + 8) if ace_type == 0 else None
        yield ace_type, flags, sid


def trusted(handle, *, regular=False):
    file_info = FileInformation()
    checked(handle_info(handle, ctypes.byref(file_info)))
    if file_info.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    if regular and (file_info.attributes & FILE_ATTRIBUTE_DIRECTORY or file_info.links != 1):
        return False

    owner, acl, descriptor = PTR(), PTR(), PTR()
    rc = get_security(handle, 1, 0x5, ctypes.byref(owner), None, ctypes.byref(acl),
                      None, ctypes.byref(descriptor))  # SE_FILE_OBJECT, OWNER | DACL
    if rc:
        raise ctypes.WinError(rc)
    try:
        return bool(owner.value and acl.value and
                    private_dacl(current_sid(), sid_text(owner), acl_entries(acl)))
    finally:
        local_free(descriptor)


def open_handle(path, access, disposition):
    share = FILE_SHARE_READ | FILE_SHARE_WRITE
    if disposition == OPEN_EXISTING:
        # Keep the open snapshot readable across an atomic file replacement.
        # Lock files opened with OPEN_ALWAYS must retain their filesystem identity.
        share |= FILE_SHARE_DELETE
    handle = create_file(str(path), READ_CONTROL | FILE_READ_ATTRIBUTES | access,
                         share, None, disposition, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, None)
    if handle == INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def trusted_path(path):
    handle = open_handle(path, 0, OPEN_EXISTING)
    try:
        return trusted(handle)
    finally:
        close(handle)


def trusted_fd(fd):
    import msvcrt
    return trusted(msvcrt.get_osfhandle(fd), regular=True)


def open_fd(path, error, lock):
    import msvcrt
    access = GENERIC_READ | GENERIC_WRITE if lock else GENERIC_READ
    handle = open_handle(path, access, OPEN_ALWAYS if lock else OPEN_EXISTING)
    try:
        if not trusted(handle, regular=True):
            raise Error(error, "Use a private regular file owned by the current Windows account.")
        # The CRT fd now owns the kernel handle. Standard os.close releases it.
        return msvcrt.open_osfhandle(handle, (os.O_RDWR if lock else os.O_RDONLY) | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
