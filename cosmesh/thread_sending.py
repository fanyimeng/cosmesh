"""Explicit outbound receipts. Private work never becomes a message by syncing."""
import hashlib
import json
from types import SimpleNamespace

from .errors import Error
from .thread_updates import check_pending

ACTIONS = {'request', 'reply', 'assign', 'decline', 'deliver', 'adopt', 'cancel', 'correct'}


def require_public(wb, item):
    if item['repo'] == 'local':
        if item['doc']['meta']['kind'] != 'thread': item = wb.find(item['doc']['meta']['thread'])
        title = item['doc']['meta'].get('title', '')
        raise Error('PRIVATE_THREAD_NOT_SENT',
                    '⚠️ 尚未发送\n关联的 Thread「' + title + '」为私人，对方目前看不到这条消息。',
                    '公开当前 Thread 并发送：先预览正式文本范围，确认后用 thread share ID --sync，再重试原消息。'
                    '公开后当前 Hub 用户均可读，指定收件人用于通知。也可暂不发送。')


def prepare(wb, args, body, key):
    """No record is created while publication consent is missing."""
    verb = args.thread_command
    target = wb.find(wb.maintenance_entry()['source']['id'] if verb == 'request' and args.maintenance else args.id)
    parent = target if target['doc']['meta']['kind'] == 'thread' else wb.find(target['doc']['meta']['thread'])
    if verb == 'correct' and parent['repo'] == 'local' and target['doc']['meta'].get('type') in {'note','result','summary'} and not args.sync:
        args.local_only = True  # Correcting a private work note is a local edit.
    if not args.local_only:
        require_public(wb, parent)
        for name in ('ref', 'work', 'result'):
            for value in getattr(args, name, []) or []:
                ref = wb.citation(value)
                if ref['repo'] == 'local':
                    require_public(wb, wb.find(ref['id']))
    if not key:
        payload = [verb, parent['doc']['meta']['id'], args.id, body,
                   getattr(args, 'to', None), getattr(args, 'ref', []), getattr(args, 'work', []),
                   getattr(args, 'result', []), getattr(args, 'partial', False), getattr(args, 'title', None)]
        key = 'send-' + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return key



def publication_scope(wb, item):
    """Publish the chosen record and its fixed, explicitly cited pending sources."""
    from .thread_protocol import references
    allowed = {}; queue = [item]; seen = set()
    while queue:
        current = queue.pop(); ref = wb.ref(current)
        marker = (ref['repo'], ref['id'], ref['version'])
        if marker in seen: continue
        seen.add(marker)
        require_public(wb, current)
        allowed.setdefault(ref['repo'], set()).add(ref['id'])
        for source in references(current['doc']['meta']):
            if source['repo'] == 'library': continue
            try:
                wb.resolve(source, accepted=True)
            except Error as exc:
                if exc.code != 'SOURCE_PENDING': raise
                dependency = wb.find(source['id'])
                if wb.ref(dependency) != source:
                    raise Error('SOURCE_PENDING', 'Referenced pending version changed; review its fixed reference before retrying.')
                queue.append(dependency)
    for repo, ids in allowed.items(): check_pending(wb, repo, ids)
    return list(allowed)


def receipt(wb, args, record, dry=False):
    from .thread_chat import summary
    if not dry: wb.load()
    publication = {'accepted': False, 'synchronized': False}
    state = 'draft' if args.local_only or dry else 'pending'
    recipients = []
    if not dry:
        from .thread_mail import recipients as routed
        recipients = sorted(routed(wb, wb.find(record['id'])))
    if not args.local_only and not dry:
        item = wb.find(record['id'])
        try:
            if getattr(args, 'offline', False):
                raise Error('OFFLINE', '已保存，尚未送达；联网后重试原消息。')
            selected = publication_scope(wb, item)
            result = wb.synchronize(knowledge_keys=[], publish_repos=selected)
            accepted = wb.accepted(wb.find(record['id']))
            publication.update(accepted=accepted, synchronized=accepted, errors=result['errors'])
            if accepted: state = 'sent' if recipients else 'published'
        except Error as exc:
            publication['error'] = exc.as_dict()
    labels = {'draft': '⚠️ 尚未发送（预览）' if dry else '⚠️ 草稿已保存，尚未发送',
              'pending': '⚠️ 待发送，尚未送达', 'sent': '已发送（服务器已接收）', 'published':'已公开；此记录没有通知对象'}
    small = {k:v for k,v in record.items() if k not in {'body', 'generation'}}
    small['excerpt'] = summary(record.get('body',''), 180)
    return {'format':'thread.send.v1', 'action':args.thread_command, 'result':small,
            'delivery_state':state, 'recipients':recipients, 'sent':state == 'sent', 'saved_locally':not dry,
            'publication':publication, 'document':labels[state] + '\n',
            'read_hint':'首先展示 document；草稿与待发送不能称已发送。重试使用原输入与操作键。服务器接收不代表对方已查看。'}


def branch(wb, args, body, key, dry=False):
    """A selected-text branch; its provenance stays in the private client file."""
    source = wb.find(args.id, 'thread')
    if source['doc']['meta']['author'] not in {wb.config['user'], wb.user}:
        raise Error('AUTH_DENIED', 'Only your own Thread can create a public branch.')
    if not body.strip(): raise Error('INPUT_INVALID', 'Provide the approved public text with --body or --file.')
    if not key:
        key = 'public-branch-' + hashlib.sha256(json.dumps([args.id,args.title,body]).encode()).hexdigest()
    from .thread_materials import rewrite, publish
    # Retrying a published branch reuses the private creation receipt and the same ID.
    created = wb.create(args.title, rewrite(body, {}), key=key, dry_run=dry)
    if dry: return {'thread':created['id'], 'dry_run':True, 'saved_locally':False}
    wb.load()
    links = wb.client.setdefault('public_branches', [])
    link = {'source':wb.ref(source), 'thread':created['id']}
    if link not in links: links.append(link); wb.save_client()
    options = SimpleNamespace(id=created['id'], attach=[], materials=None, resource=[], git=None,
                              commit=None, code_path=[], summary_only=True, sync=args.sync)
    return publish(wb, options, key + '-publish')
