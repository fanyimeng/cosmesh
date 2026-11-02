"""Optional field encryption. Passwords and derived keys are never persisted."""
import base64
import binascii
import json
import os

from .errors import Error

PREFIX = 'ENC[cosmesh:v1:'
MAX_SECRET = 4096
MAX_VALUE = 64 * 1024


def is_encrypted(value):
    # Reserve the envelope marker, including versions this client cannot decrypt.
    return isinstance(value, str) and value.startswith('ENC[')


def primitives():
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError:
        raise Error('CRYPTO_UNAVAILABLE', 'Install a complete release with its bundled encryption dependencies.') from None
    return Fernet, InvalidToken, Scrypt


def derive(password, salt):
    if not isinstance(password, str) or not password or len(password.encode('utf-8')) > MAX_SECRET:
        raise Error('SECRET_INPUT_INVALID', 'Provide a nonempty password of at most 4096 UTF-8 bytes.')
    _, _, Scrypt = primitives()
    return base64.urlsafe_b64encode(Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode('utf-8')))


def encrypt(value, password, field='contact'):
    if field != 'contact':
        raise Error('INPUT_INVALID', 'Field encryption currently supports contact only.')
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > MAX_VALUE:
        raise Error('INPUT_INVALID', 'Encrypt a nonempty contact field within the Profile size limit.')
    if is_encrypted(value):
        raise Error('FIELD_ALREADY_ENCRYPTED', 'The contact field is already encrypted.')
    Fernet, _, _ = primitives()
    salt = os.urandom(16)
    payload = json.dumps({'field': field, 'value': value}, ensure_ascii=False).encode('utf-8')
    token = Fernet(derive(password, salt)).encrypt(payload).decode('ascii')
    return PREFIX + field + ':' + base64.urlsafe_b64encode(salt).decode('ascii') + ':' + token + ']'


def decrypt(value, password, field='contact'):
    if field != 'contact':
        raise Error('INPUT_INVALID', 'Field encryption currently supports contact only.')
    if not is_encrypted(value):
        raise Error('FIELD_NOT_ENCRYPTED', 'The contact field is not encrypted.')
    if not value.startswith(PREFIX) or not value.endswith(']'):
        raise Error('ENCRYPTION_FORMAT_UNSUPPORTED', 'Unsupported field encryption format; preserve the ciphertext.')
    Fernet, InvalidToken, _ = primitives()
    try:
        embedded_field, salt_text, token = value[len(PREFIX):-1].split(':')
        salt = base64.b64decode(salt_text, altchars=b'-_', validate=True)
        if embedded_field != field or len(salt) != 16 or len(value) > MAX_VALUE:
            raise ValueError()
    except (ValueError, binascii.Error, UnicodeError):
        raise Error('DECRYPTION_FAILED', 'The ciphertext is invalid; the Profile has been preserved.') from None
    try:
        raw = Fernet(derive(password, salt)).decrypt(token.encode('ascii'))
        payload = json.loads(raw.decode('utf-8'))
        if (not isinstance(payload, dict) or set(payload) != {'field', 'value'} or
                payload['field'] != field or not isinstance(payload['value'], str) or
                not payload['value'] or is_encrypted(payload['value'])):
            raise ValueError()
        return payload['value']
    except (InvalidToken, ValueError, UnicodeError):
        raise Error('DECRYPTION_FAILED', 'Incorrect password or modified ciphertext; the Profile has been preserved.') from None
