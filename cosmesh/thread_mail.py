"""Private delivery/read receipts over accepted ordinary Thread interactions."""
import base64
import fcntl
import hashlib
import json
import os
import threading
from uuid import uuid4
from contextlib import contextmanager

from .errors import Error
from .store import atomic
from .threads import now

_LOCK = threading.RLock()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def root(hub):
    path = hub.root / 'mail'
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise Error('AUTH_DENIED', 'Mail receipts require a private owned directory.')
    return path


@contextmanager
def locked(hub):
    path = root(hub)
    with _LOCK, (path / 'mail.lock').open('a+') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX)
        yield path


def recipients(wb, item, seen=None):
    """Only explicit request/supersession edges route mail; project progress is opt-in."""
    seen = set() if seen is None else seen
    meta = item['doc']['meta']; marker = (item['repo'], meta['id'])
    if marker in seen:
        return set()
    seen.add(marker)
    if meta.get('type') == 'request':
        return {meta['to']} - {meta['author']}
    if meta.get('request'):
        ref = meta['request']; wb.resolve(ref)
        request = wb.find(ref['id']); q = request['doc']['meta']
        if request['repo'] != ref['repo'] or q.get('type') != 'request':
            return set()
        return {q['author'], q['to']} - {meta['author']}
    if meta.get('supersedes'):
        ref = meta['supersedes']; wb.resolve(ref); original = wb.find(ref['id'])
        if original['repo'] == ref['repo']:
            return (recipients(wb, original, seen) | {original['doc']['meta']['author']}) - {meta['author']}
    return set()


def initialize(hub):
    """A new Hub starts before its first interaction; upgrades bootstrap history on rebuild."""
    with locked(hub) as path:
        file = path / 'deliveries.json'
        if not file.exists():
            atomic(file, packed({'format': 1, 'sequence': 0, 'events': {}, 'source_token': None}))
        if not (path / 'baseline.json').exists():
            atomic(path / 'baseline.json', packed({'historical_ids': []}))


def rebuild(hub, wb, users, generation):
    """Replay accepted snapshots. Atomic checkpoints survive interrupted workers/restarts."""
    from .thread_chat import summary
    names = {u['account']: u['name'] or u['account'] for u in users}
    with locked(hub) as path:
        filename = path / 'deliveries.json'
        first = not filename.is_file()
        state = json.loads(filename.read_text()) if not first else {'format': 1, 'sequence': 0, 'events': {}}
        state.setdefault('epoch', uuid4().hex)
        baseline = path / 'baseline.json'
        if not baseline.is_file():
            history = [mid for mid,e in state['events'].items() if e['historical']]
            if first:
                history = ['M-' + digest(wb.ref(item)) for item in wb.catalog.values()
                           if item['doc']['meta']['kind'] == 'entry' and recipients(wb,item)]
            atomic(baseline, packed({'historical_ids': history}))
        historical = set(json.loads(baseline.read_text())['historical_ids'])
        changed = state.get('source_token') != wb.public_tokens['mesh']
        if changed:
            for item in sorted(wb.catalog.values(), key=lambda x: (x['doc']['meta']['created_at'], x['repo'], x['doc']['meta']['id'])):
                meta = item['doc']['meta']
                if item['repo'] == 'local' or meta['kind'] != 'entry':
                    continue
                targets = recipients(wb, item)
                if not targets:
                    continue
                source = wb.ref(item); mid = 'M-' + digest(source)
                parent = wb.find(meta['thread'], 'thread')
                request = meta['id'] if meta.get('type') == 'request' else (meta.get('request') or {}).get('id')
                status = wb.request_state(wb.find(request))['state'] if request else None
                if mid not in state['events']:
                    state['sequence'] += 1
                    state['events'][mid] = {'id': mid, 'sequence': state['sequence'], 'source': source,
                        'sender': meta['author'], 'sender_name': names.get(meta['author'], meta['author']),
                        'recipients': sorted(targets), 'kind': meta['type'], 'created_at': meta['created_at'],
                        'indexed_at': now(), 'historical': mid in historical, 'thread': meta['thread'],
                        'request': request, 'title': meta['title'], **summary(item['doc']['body'], 1200)}
                event = state['events'][mid]
                naming = wb._thread_names.get(meta['thread'], {})
                event.update(thread_name=naming.get('chinese') or naming.get('english') or parent['doc']['meta']['title'],
                             sender_name=names.get(meta['author'], meta['author']), request_state=status)
            state['source_token'] = wb.public_tokens['mesh']
        state.update(generation=generation, generated_at=now(), users=users)
        atomic(filename, packed(state))
    return {'events': len(state['events']), 'sequence': state['sequence'], 'initialized_history': first}


def visible(hub, event, actor, folder):
    if (folder == 'inbox' and actor not in event['recipients']) or (folder == 'outbox' and event['sender'] != actor):
        return False
    try:
        hub.policy(event['source']['repo'], actor)
        return True
    except Error:
        return False


def cursor(value):
    try:
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(value.encode() + b'=' * (-len(value) % 4)))
        if set(data) != {'query', 'ceiling', 'before'} or any(type(data[k]) is not int or data[k] < 0 for k in ('ceiling', 'before')):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise Error('INPUT_INVALID', 'Use the previous mail next_cursor unchanged.')


def fetch(hub, payload, actor):
    allowed = {'method', 'folder', 'limit', 'before', 'unread', 'peer', 'thread', 'id', 'timezone'}
    if set(payload) - allowed:
        raise Error('INPUT_INVALID', 'Unsupported mailbox options.')
    folder = payload.get('folder', 'inbox'); limit = payload.get('limit', 10)
    if folder not in {'inbox', 'outbox'} or type(limit) is not int or not 1 <= limit <= 100 or type(payload.get('unread', False)) is not bool:
        raise Error('INPUT_INVALID', 'Use inbox/outbox, a boolean unread filter and limit 1–100.')
    with locked(hub) as path:
        filename = path / 'deliveries.json'
        if not filename.is_file() or not json.loads(filename.read_text()).get('source_token'):
            from .hub_pages import schedule
            schedule(hub)
            return {'ready': False, 'reason': 'preparing'}
        ledger = json.loads(filename.read_text())
        from datetime import datetime
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            zone = ZoneInfo(payload.get('timezone', 'UTC'))
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise Error('INPUT_INVALID', 'Use an available IANA timezone.')
        seenfile = path / ('seen-' + digest(actor) + '.json')
        seen = json.loads(seenfile.read_text()) if seenfile.is_file() else {'states': {}}
        peer = None
        if payload.get('peer'):
            from .knowledge import select_user
            peer = select_user(ledger['users'], payload['peer'])['account']
        identity = digest([actor, folder, bool(payload.get('unread')), peer, payload.get('thread'), ledger['epoch']])
        token = cursor(payload['before']) if payload.get('before') else None
        if token and token['query'] != identity:
            raise Error('VIEW_EXPIRED', 'Continue the same mailbox/filters, or reopen after an index rebuild.')
        ceiling = token['ceiling'] if token else ledger['sequence']
        bound = token['before'] if token else ceiling + 1
        events = []
        for event in ledger['events'].values():
            if not visible(hub, event, actor, folder):
                continue
            if peer and peer not in [event['sender'], *event['recipients']]:
                continue
            if payload.get('thread') and event['thread'] != payload['thread']:
                continue
            unread = not seen['states'].get(event['id'], event['historical'])
            if payload.get('unread') and not unread:
                continue
            if event['sequence'] <= ceiling:
                events.append({**event, 'unread': unread if folder == 'inbox' else None,
                               'delivery_state': 'indexed', 'accepted': True})
        events.sort(key=lambda x: x['sequence'], reverse=True)
        if payload.get('id'):
            events = [e for e in events if e['id'] == payload['id']]
            if not events:
                raise Error('NOT_FOUND', 'This message is not available in your mailbox.')
        selected = [e for e in events if e['sequence'] < bound][:limit]
        more = bool(selected) and any(e['sequence'] < selected[-1]['sequence'] for e in events)
        next_cursor = base64.urlsafe_b64encode(packed({'query': identity, 'ceiling': ceiling,
                         'before': selected[-1]['sequence']}).encode()).decode().rstrip('=') if more else None
        names = {u['account']: u['name'] or u['account'] for u in ledger['users']}
        for entry in selected:
            entry['recipient_names'] = [names.get(u, u) for u in entry['recipients']]
            entry['local_time'] = datetime.fromisoformat(entry['created_at']).astimezone(zone).strftime('%Y-%m-%d %H:%M')
        dirty = hub.root / 'views/dirty.json'
        current = not dirty.is_file() or json.loads(dirty.read_text())['generation'] == ledger['generation']
        if not current:
            from .hub_pages import schedule
            schedule(hub)
        lines = ['# ' + ('收件箱' if folder == 'inbox' else '发件箱'), '']
        labels = {'request':'请求','reply':'回复','assign':'承接','decline':'拒绝','delivery':'交付',
                  'adopt':'采用','cancel':'取消','correction':'更正'}
        for entry in selected:
            lines += [('〔新〕' if entry['unread'] else '') + entry['sender_name'] + ' · ' + entry['local_time'] + ' · ' + labels.get(entry['kind'],entry['kind']) + ' · ' + entry['thread_name'],
                      entry['text'] + ('〔截断，按需展开〕' if entry['truncated'] else ''), '']
        if not selected:
            lines += ['暂无匹配消息。' if current else '消息成品正在更新，当前缓存中暂无匹配消息。']
        value = {'format': 'thread.mail.v1', 'folder': folder, 'private': True, 'actor': actor,
                 'messages': selected, 'total': len(events), 'unread_count': sum(bool(e['unread']) for e in events),
                 'next_cursor': next_cursor, 'truncated': more, 'document': '\n'.join(lines),
                 'freshness': {'generated_at': ledger['generated_at'], 'remote_checked_at': now(),
                               'server_current': current, 'updating': not current},
                 'read_hint': '直接展示摘要；列表不标已查看。按固定source展开长正文；请求状态与消息查看状态独立。普通工作更新未自动通知。'}
        return {'ready': True, 'data': value}


def mark(hub, payload, actor):
    if set(payload) != {'method', 'ids', 'seen'} or type(payload['seen']) is not bool or not isinstance(payload['ids'], list) or not 1 <= len(payload['ids']) <= 100:
        raise Error('INPUT_INVALID', 'Mark 1–100 explicit message IDs with a boolean seen state.')
    with locked(hub) as path:
        filename = path / 'deliveries.json'
        if not filename.is_file():
            raise Error('NOT_FOUND', 'The mailbox is not prepared yet.')
        ledger = json.loads(filename.read_text())
        if any(not isinstance(mid, str) for mid in payload['ids']):
            raise Error('INPUT_INVALID', 'Message IDs must be strings.')
        ids = list(dict.fromkeys(payload['ids']))
        for mid in ids:
            if not isinstance(mid, str) or mid not in ledger['events'] or not visible(hub, ledger['events'][mid], actor, 'inbox'):
                raise Error('NOT_FOUND', 'Only your available received messages can be marked.')
        filename = path / ('seen-' + digest(actor) + '.json')
        state = json.loads(filename.read_text()) if filename.is_file() else {'format': 1, 'states': {}}
        state['states'].update({mid: payload['seen'] for mid in ids}); state['at'] = now()
        atomic(filename, packed(state))
        return {'marked': ids, 'seen': payload['seen'], 'private': True}


def execute(wb, args):
    from .spaces import rpc
    from .status_pages import cache_root
    folder = 'outbox' if args.thread_command == 'outbox' or getattr(args, 'sent', False) else 'inbox'
    command = args.thread_command
    if command == 'mail' and args.mail_action == 'mark':
        if getattr(args, 'offline', False) or getattr(args, 'dry_run', False):
            raise Error('INPUT_INVALID', 'Explicit message marking requires an online write.')
        if not wb.client.get('connection') or not wb.client.get('actor'):
            raise Error('HUB_CONNECTION_REQUIRED', 'Discover your existing Mesh connection before using mail.')
        reply = rpc(wb.client['connection'], {'method': 'thread_mail_mark', 'ids': args.ids, 'seen': args.seen})
        if reply.get('actor') != wb.client.get('actor'):
            raise Error('AUTH_DENIED', 'Mailbox identity differs.')
        # A seen change invalidates every local mailbox page without touching Base sources.
        for file in cache_root(wb).glob('mail-*.json'):
            file.unlink()
        return reply
    if getattr(args, 'dry_run', False):
        raise Error('INPUT_INVALID', 'Mailbox listings are read-only and do not use dry-run.')
    payload = {'method': 'thread_mail', 'folder': folder, 'limit': getattr(args, 'limit', 10),
               'before': getattr(args, 'before', None), 'unread': getattr(args, 'unread', False),
               'peer': getattr(args, 'contact', None), 'thread': getattr(args, 'thread', None),
               'timezone': json.loads(wb.base.path('.cosmesh/config.json').read_text())['timezone']}
    if command == 'mail':
        payload['id'] = args.id
    actor = wb.client.get('actor'); connection = wb.client.get('connection')
    if not actor or not connection:
        raise Error('HUB_CONNECTION_REQUIRED', 'Discover your existing Mesh connection before using mail.')
    path = cache_root(wb) / ('mail-' + digest([actor, connection, payload]) + '.json')
    stamp = wb.path.stat().st_mtime_ns
    cached = json.loads(path.read_text()) if path.is_file() else None
    offline = getattr(args, 'offline', False)
    if cached and (offline or (not getattr(args, 'require_fresh', False) and cached['source_stamp'] == stamp)):
        value = cached['data']; value['freshness']['validation'] = 'cached'; return value
    if offline:
        raise Error('SOURCE_UNAVAILABLE', 'This mailbox page is not cached; retry online.')
    try:
        reply = rpc(connection, payload)
    except Error as error:
        if cached:
            value = cached['data']; value['freshness'].update(server_current=False, updating=True, refresh_error=error.code)
            return value
        if error.code in {'INPUT_INVALID', 'FEATURE_UNAVAILABLE', 'COMMAND_DENIED'}:
            raise Error('FEATURE_UNAVAILABLE', 'This Hub has no message inbox; request inbox remains available but does not cover new replies.')
        raise
    if reply.get('actor') != actor:
        raise Error('AUTH_DENIED', 'Mailbox identity differs.')
    if not reply.get('ready'):
        if cached:
            value = cached['data']; value['freshness'].update(server_current=False, updating=True); return value
        return {'format': 'thread.mail.v1', 'folder': folder, 'messages': [], 'private': True,
                'freshness': {'server_current': False, 'updating': True}, 'document': '收发件箱正在首次准备；尚不能判断是否有新消息。'}
    value = reply['data']
    if value.get('format') != 'thread.mail.v1' or value.get('actor') != actor:
        raise Error('SYNC_PROTOCOL', 'Unexpected private mailbox result.')
    atomic(path, packed({'source_stamp': stamp, 'data': value}))
    return value
