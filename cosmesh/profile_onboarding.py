"""Local document previews and interactive filling over the existing private Profile."""
import hashlib
import json
import re
import sys
from pathlib import Path

from .errors import Error
from .profile import FIELDS, MAX_PROFILE, encode, validate
from .field_crypto import is_encrypted

LABELS = {'display_name': ('姓名', '名字', '显示名', 'name', 'full_name'),
          'organization': ('机构', '单位', 'organization', 'affiliation'),
          'contact': ('联系方式', '邮箱', '电子邮箱', 'email', 'contact'),
          'language': ('语言', 'language'), 'timezone': ('时区', 'timezone'),
          'preferences': ('偏好', 'preferences'), 'about': ('简介', '个人简介', 'about', 'bio')}


def preview(profile, changes):
    if not isinstance(changes, dict) or set(changes) - set(FIELDS) or any(not isinstance(v, str) for v in changes.values()):
        raise Error('INPUT_INVALID', 'Candidate fields must be known Profile text fields.')
    current = profile.read()
    if is_encrypted(changes.get('contact', '')):
        raise Error('INPUT_INVALID', 'Import contact text; use Profile commands for encryption.')
    encode({**current['fields'], **changes})
    diff = [{'field': k, 'before': '[已加密]' if k in current['encrypted_fields'] else current['fields'][k],
             'after': changes[k]} for k in FIELDS if k in changes and current['fields'][k] != changes[k]]
    return {'scope': 'local_private', 'source_version': current['version'], 'proposed_fields': changes,
            'diff': diff, 'dry_run': True, 'persisted': False, 'published': False,
            'next_step': 'Show the changes to the user; after confirmation use profile set --expect source_version.'}


def labelled_fields(text):
    aliases = {alias.casefold(): field for field, names in LABELS.items() for alias in (field, *names)}
    values = {}
    for line in text.splitlines():
        line = re.sub(r'^\s*(?:#{1,6}\s+|[-*]\s+)', '', line).replace('**', '')
        match = re.match(r'^\s*([^:：]{1,30})\s*[:：]\s*(\S.*?)\s*$', line)
        if not match:
            continue
        field = aliases.get(match[1].strip().casefold())
        if field:
            values.setdefault(field, set()).add(match[2])
    ambiguous = [field for field, found in values.items() if len(found) > 1]
    fields = {field: next(iter(found)) for field, found in values.items() if len(found) == 1}
    if fields.get('language') in ('中文', 'Chinese'):
        fields['language'] = 'zh-CN'
    if fields.get('language') in ('英文', 'English'):
        fields['language'] = 'en'
    for field in list(fields):
        try:
            validate({**dict.fromkeys(FIELDS, ''), field: fields[field]})
        except Error:
            fields.pop(field)
            ambiguous.append(field)
    if is_encrypted(fields.get('contact', '')):
        fields.pop('contact');ambiguous.append('contact')
    return fields, ambiguous


def import_preview(profile, file, fields_file=None, offset=0, chars=12000):
    from .knowledge import extract, MAX_FILE
    path = Path(file).expanduser()
    if path.suffix.lower() not in ('.md', '.txt', '.pdf', '.docx'):
        raise Error('INPUT_INVALID', 'Use Markdown, TXT, PDF or DOCX for a CV or introduction.')
    if not path.is_file():
        raise Error('INPUT_INVALID', 'Choose an existing local document.')
    if path.stat().st_size > MAX_FILE:
        raise Error('INPUT_TOO_LARGE', 'The document exceeds the existing attachment size limit.')
    if not 0 <= offset or not 1 <= chars <= 64000:
        raise Error('INPUT_INVALID', 'Use offset >= 0 and chars from 1 to 64000.')
    try:
        extracted = extract(path)
    except Exception as exc:
        if isinstance(exc, Error):
            raise
        raise Error('DOCUMENT_READ_FAILED', 'Document text could not be extracted; provide readable text or another format.') from None
    fields, ambiguous = labelled_fields(extracted['text'])
    if fields_file:
        if fields_file == '-':
            raw = sys.stdin.read(MAX_PROFILE + 1)
        else:
            with Path(fields_file).expanduser().open(encoding='utf-8') as handle:
                raw = handle.read(MAX_PROFILE + 1)
        if len(raw.encode('utf-8')) > MAX_PROFILE:
            raise Error('INPUT_TOO_LARGE', 'Candidate fields exceed 64 KiB.')
        fields = json.loads(raw)
    result = preview(profile, fields)
    text = extracted['text'];excerpt = text[offset:offset + chars]
    return {**result, 'candidate_origin': 'agent_supplied' if fields_file else 'explicit_document_labels',
            'ambiguous_fields': ambiguous, 'source': {'path': str(path.resolve()),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'status': extracted['status'], 'tool': extracted['tool']},
            'text': excerpt, 'truncated': offset + len(excerpt) < len(text),
            'next_offset': offset + len(excerpt) if offset + len(excerpt) < len(text) else None,
            'agent_step': 'Use readable source text for unlabelled fields; omit uncertain values, preview --fields-file, then ask for confirmation.'}


def interactive(profile, dry_run=False, encrypt_contact=False, password_reader=None):
    current = profile.read();changes = {}
    def ask(message):
        print(message, end='', file=sys.stderr, flush=True)
        value = sys.stdin.readline()
        return None if not value else value.rstrip('\r\n')
    print('填写私人 Profile：回车跳过；/clear 明确清空。共享实名另用 user me / set-name。', file=sys.stderr)
    for field in FIELDS:
        old = '[已加密]' if field in current['encrypted_fields'] else current['fields'][field]
        value = ask(f'{LABELS[field][0]} [{old}]：')
        if value is None:
            return {**preview(profile, changes), 'cancelled': True}
        if value:
            changes[field] = '' if value == '/clear' else value
    result = preview(profile, changes)
    if current['version'] != result['source_version']:
        raise Error('STATE_CONFLICT', 'The profile changed while answering; read it again before saving.')
    print(json.dumps(result['diff'], ensure_ascii=False, indent=2), file=sys.stderr)
    if dry_run or not result['diff']:
        return result
    confirm = ask('确认保存以上变更？输入 yes 或 确认；其他输入取消：')
    if confirm is None or confirm.strip().casefold() not in ('yes', 'y', '确认', '是'):
        return {**result, 'cancelled': True}
    needs_password = bool(changes.get('contact')) and (encrypt_contact or 'contact' in current['encrypted_fields'])
    password = password_reader() if needs_password and password_reader else None
    saved = profile.set(changes, result['source_version'], encrypt_contact=encrypt_contact, password=password)
    return {**saved, 'persisted': True, 'published': False, 'confirmed': True}
