"""Absolute source locations in owner metadata. Restore destinations are local."""
from pathlib import PurePosixPath, PureWindowsPath


def absolute_source_path(value):
    return (isinstance(value, str) and 0 < len(value) <= 4096 and '\0' not in value
            and (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()))


def manifest_path(value):
    """Spell Windows drive/UNC locations as absolute paths for a stock POSIX Hub."""
    if not isinstance(value, str) or not value or value.startswith('/'):
        return value
    win = PureWindowsPath(value)
    if not win.is_absolute():
        return value
    path = win.as_posix()
    if path.startswith('//?/') or path.startswith('//./'):
        return path
    if path.startswith('//'):
        return '//?/UNC/' + path[2:]
    return '//?/' + path


def display_path(value):
    """Use familiar native spelling in owner views, preserving the hashed wire row."""
    if value and value[:8].casefold() == '//?/unc/':
        return '\\\\' + value[8:].replace('/', '\\')
    if value and value.startswith('//?/') and value[5:7] == ':/':
        return value[4:].replace('/', '\\')
    return value
