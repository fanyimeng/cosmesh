"""One private personal profile shared by both clients, outside document stores."""
import fcntl
import hashlib
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .access import system_user
from .errors import Error
from .protocol import StrictLoader
from .store import atomic, safe_path
from . import field_crypto

FIELDS = ('display_name', 'organization', 'contact', 'language', 'timezone', 'preferences', 'about')
MAX_PROFILE = 64 * 1024
MISSING = 'missing'


def validate(fields):
    if set(fields) != set(FIELDS) or any(not isinstance(v, str) for v in fields.values()):
        raise Error('PROFILE_INVALID', 'Profile fields must be known text fields.')
    if any('\x00' in v or '\r' in v for v in fields.values()):
        raise Error('PROFILE_INVALID', 'Profile text must use LF and contain no NUL.')
    if fields['timezone']:
        try:
            ZoneInfo(fields['timezone'])
        except (ZoneInfoNotFoundError, ValueError):
            raise Error('INPUT_INVALID', 'Unknown profile timezone.')
    if fields['language'] and not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*', fields['language']):
        raise Error('INPUT_INVALID', 'Use a language tag such as zh-CN or en.')


def encode(fields):
    validate(fields)
    metadata = {'schema_version': 1, **{k: fields[k] for k in FIELDS if k != 'about'}}
    text = '---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + '---\n' + fields['about']
    raw = text.encode('utf-8')
    if len(raw) > MAX_PROFILE:
        raise Error('INPUT_TOO_LARGE', 'The personal profile exceeds 64 KiB.')
    return raw


def decode(raw):
    try:
        text = raw.decode('utf-8')
        if not text.startswith('---\n') or '\n---\n' not in text[4:]:
            raise ValueError()
        front, body = text[4:].split('\n---\n', 1)
        meta = yaml.load(front, Loader=StrictLoader)
    except (UnicodeError, ValueError, yaml.YAMLError):
        raise Error('PROFILE_INVALID', 'Expected UTF-8 Markdown with YAML frontmatter.')
    if not isinstance(meta, dict) or type(meta.get('schema_version')) is not int or meta['schema_version'] != 1:
        raise Error('PROFILE_VERSION_UNSUPPORTED', 'This client supports profile schema 1.')
    if set(meta) != {'schema_version', *(k for k in FIELDS if k != 'about')}:
        raise Error('PROFILE_INVALID', 'Unexpected or missing profile metadata.')
    fields = {**{k: meta[k] for k in FIELDS if k != 'about'}, 'about': body}
    validate(fields)
    return fields


class PersonalProfile:
    def __init__(self, directory=None):
        config_home = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))).expanduser()
        chosen = Path(directory or os.environ.get('COSMESH_PROFILE_DIR', str(config_home / 'cosmesh'))).expanduser()
        if not chosen.is_absolute():
            raise Error('PATH_INVALID', 'Use an absolute profile directory.')
        self.directory = safe_path(chosen)
        self.path = self.directory / 'profile.md'
        prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
        if prefix and self.directory.is_relative_to(Path(prefix).expanduser().resolve()):
            raise Error('PATH_INVALID', 'The personal profile must be outside the installed application.')
        if any((parent / '.git').exists() for parent in (self.directory, *self.directory.parents)):
            raise Error('PATH_INVALID', 'Keep the private profile outside Git working copies.')

    def check_directory(self):
        safe_path(self.directory)
        info = self.directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise Error('PROFILE_PERMISSIONS', 'The profile directory must be owned by you with mode 700.')

    @staticmethod
    def check_file(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or
                info.st_nlink != 1):
            raise Error('PROFILE_PERMISSIONS', 'Profile files must be private regular files owned by you.')

    def read(self):
        fields, raw = dict.fromkeys(FIELDS, ''), None
        if self.directory.exists():
            self.check_directory()
            safe_path(self.path)
            try:
                descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                descriptor = None
            if descriptor is not None:
                with os.fdopen(descriptor, 'rb') as handle:
                    info = os.fstat(handle.fileno())
                    self.check_file(info)
                    if info.st_size > MAX_PROFILE:
                        raise Error('INPUT_TOO_LARGE', 'The personal profile exceeds 64 KiB.')
                    raw = handle.read(MAX_PROFILE + 1)
                    if len(raw) > MAX_PROFILE:
                        raise Error('INPUT_TOO_LARGE', 'The personal profile exceeds 64 KiB.')
                    fields = decode(raw)
        return {'path': str(self.path), 'exists': raw is not None,
                'version': hashlib.sha256(raw).hexdigest() if raw is not None else MISSING,
                'scope': 'local_private', 'owner': system_user(), 'fields': fields,
                'encrypted_fields': ['contact'] if field_crypto.is_encrypted(fields['contact']) else []}

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.check_directory()
        path = safe_path(self.directory / 'profile.lock')
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, 'a+') as handle:
            self.check_file(os.fstat(handle.fileno()))
            deadline = time.monotonic() + 3
            while True:
                try:
                    fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Error('PROFILE_BUSY', 'Another process is updating the personal profile.')
                    time.sleep(.05)
            try:
                yield
            finally:
                fcntl.lockf(handle, fcntl.LOCK_UN)

    def init(self, dry_run=False):
        if dry_run:
            result = self.read()
            return {**result, 'created': not result['exists'], 'dry_run': True}
        with self.locked():
            result = self.read()
            if result['exists']:
                return {**result, 'created': False, 'dry_run': False}
            atomic(self.path, encode(result['fields']).decode('utf-8'))
            return {**self.read(), 'created': True, 'dry_run': False}

    def set(self, changes, expect, dry_run=False, *, encrypt_contact=False, password=None):
        if not expect:
            raise Error('PRECONDITION_REQUIRED', 'Use --expect VERSION from profile show.')
        if not changes or set(changes) - set(FIELDS):
            raise Error('INPUT_INVALID', 'Provide at least one known profile field.')
        if encrypt_contact and 'contact' not in changes:
            raise Error('INPUT_INVALID', 'Use --encrypt-contact with new contact information.')
        if 'contact' in changes and field_crypto.is_encrypted(changes['contact']):
            raise Error('INPUT_INVALID', 'Use the Profile encryption commands to create encrypted fields.')
        def apply(current):
            if expect != current['version']:
                raise Error('STATE_CONFLICT', 'The profile changed; read it again before revising.')
            fields = {**current['fields'], **changes}
            if 'contact' in changes and changes['contact'] and (
                    encrypt_contact or 'contact' in current['encrypted_fields']):
                if 'contact' in current['encrypted_fields']:
                    field_crypto.decrypt(current['fields']['contact'], password)
                fields['contact'] = field_crypto.encrypt(changes['contact'], password)
            raw = encode(fields)
            return {**current, 'exists': True, 'fields': fields, 'version': hashlib.sha256(raw).hexdigest(),
                    'encrypted_fields': ['contact'] if field_crypto.is_encrypted(fields['contact']) else [],
                    'changed_fields': [k for k in FIELDS if fields[k] != current['fields'][k]], 'dry_run': dry_run}
        if dry_run:
            return apply(self.read())
        with self.locked():
            result = apply(self.read())
            atomic(self.path, encode(result['fields']).decode('utf-8'))
            return result

    def export(self, selected):
        if not selected or set(selected) - set(FIELDS):
            raise Error('INPUT_INVALID', 'Select profile fields explicitly with --field.')
        result = self.read()
        return {'scope': 'selected_fields', 'source_version': result['version'],
                'fields': {k: result['fields'][k] for k in FIELDS if k in selected}, 'published': False,
                'encrypted_fields': [k for k in result['encrypted_fields'] if k in selected]}

    def crypt(self, operation, field, password, expect=None, dry_run=False):
        if field != 'contact':
            raise Error('INPUT_INVALID', 'Profile field encryption currently supports contact only.')
        if operation not in ('encrypt', 'decrypt', 'reveal'):
            raise Error('INPUT_INVALID', 'Unknown Profile encryption operation.')
        if operation != 'reveal' and not expect:
            raise Error('PRECONDITION_REQUIRED', 'Use --expect VERSION from profile show.')

        def apply(current):
            if operation != 'reveal' and expect != current['version']:
                raise Error('STATE_CONFLICT', 'The profile changed; read it again before revising.')
            value = current['fields'][field]
            transformed = (field_crypto.encrypt(value, password, field) if operation == 'encrypt'
                           else field_crypto.decrypt(value, password, field))
            if operation == 'reveal':
                return {'scope': 'local_private_ephemeral', 'source_version': current['version'],
                        'fields': {field: transformed}, 'decrypted_fields': [field], 'persisted': False,
                        'published': False}
            fields = {**current['fields'], field: transformed}
            raw = encode(fields)
            return {**current, 'fields': fields, 'version': hashlib.sha256(raw).hexdigest(),
                    'encrypted_fields': [field] if operation == 'encrypt' else [],
                    'changed_fields': [field], 'dry_run': dry_run}

        if operation == 'reveal' or dry_run:
            return apply(self.read())
        with self.locked():
            result = apply(self.read())
            atomic(self.path, encode(result['fields']).decode('utf-8'))
            return result

    def default_timezone(self):
        return self.read()['fields']['timezone'] or 'Asia/Shanghai'
