"""Small status products from already-loaded records and library metadata only."""
import re

from .errors import Error
from .material_links import parse


def excerpt(text, chars=900):
    return {'text': text[:chars], 'truncated': len(text) > chars}


def recipient_responses(wb, item):
    ref = wb.ref(item)
    replies = {x['doc']['meta']['id']: x for x, relation, target in wb._incoming.get((ref['repo'], ref['id']), [])
               if relation == 'request' and target == ref and
               x['doc']['meta']['author'] == item['doc']['meta']['to'] and
               (item['repo'] == 'local' or x['repo'] != 'local')}
    return sorted(replies.values(), key=lambda x: (x['doc']['meta']['created_at'], x['doc']['meta']['id']))


def request_status(wb, item, names):
    from .thread_chat import summary
    ordered = recipient_responses(wb, item)
    state = wb.request_state(item)
    return {'state': state['state'], 'confirmed': state['confirmed'], 'response_count': len(ordered),
            'responses': [{'source': wb.ref(x), 'author': x['doc']['meta']['author'],
                           'author_name': names.get(x['doc']['meta']['author'], x['doc']['meta']['author']),
                           'kind': x['doc']['meta']['type'], 'accepted': wb.accepted(x) if x['repo'] != 'local' else False,
                           'created_at': x['doc']['meta']['created_at'], **summary(x['doc']['body']),
                           'superseded_by': [wb.ref(c) for c, relation, target in wb._incoming.get((x['repo'], x['doc']['meta']['id']), [])
                                             if relation == 'supersedes' and target == wb.ref(x) and
                                             (item['repo'] == 'local' or c['repo'] != 'local')]}
                          for x in reversed(ordered[-3:])]}


def material_status(wb, item, records):
    """A registered fixed link is not a checksum/read/render verification."""
    from .knowledge import Knowledge
    kb = Knowledge(wb)
    links = {}; notices = []; indexes = []
    for record in [item, *records]:
        text = record['doc']['body']
        for uri in re.findall(r'cosmesh://library/[^\s`<>\[\]()]+', text):
            if uri in links: continue
            try:
                ref, name = parse(uri)
                doc = kb.doc(ref['id'], ref['version'])
                file = next((f for f in doc['meta']['files'] if f['name'] == name), None)
                links[uri] = {'source': ref, 'name': name, 'reference': uri,
                              'registered': file is not None, 'bytes': file['size'] if file else None}
            except Error as error:
                links[uri] = {'reference': uri, 'registered': False, 'error': error.code}
        # Authored index declarations stay quotations, never inferred completion.
        if record['doc']['meta']['author'] == item['doc']['meta']['author'] and text.startswith('## 共享资料\n'):
            indexes.append(record)
    if indexes:
        index = indexes[-1]
        for heading in ('资料状态：', '代码仓库：', '固定 commit：', '仓库访问状态：'):
            notices.extend(line for line in index['doc']['body'].splitlines() if line.startswith(heading))
        match = re.search(r'(?m)^原文路径尚无共享原件：\n((?:- [^\n]*\n?)+)', index['doc']['body'])
        if match: notices.append(match.group().strip())
    files = list(links.values())
    return {'scope': 'thread', 'verification': 'metadata_only', 'file_count': len(files),
            'registered_count': sum(f['registered'] for f in files),
            'unavailable_count': sum(not f['registered'] for f in files),
            'files': files[:6], 'truncated': len(files) > 6,
            'index': wb.ref(indexes[-1]) if indexes else None,
            'declaration': excerpt('\n'.join(notices), 1200),
            'read_hint': '仅核对固定版本的资料登记；未下载原件、校验字节或检查显示效果。资料属于整个工作，不表示某条请求已完成。'}


def ready_check(card, status, request, limit, freshness):
    from .status_receipts import overview_text, short_title, state
    sources = []; positions = {}
    def add(ref):
        key = (ref['repo'], ref['id'], ref['version'])
        if key not in positions: positions[key] = len(sources); sources.append(ref)
        return positions[key]
    thread_source = add(card['source'])
    intro = overview_text(card)
    lines = ['# ' + short_title(card), '', intro, '', '当前状态：' + state(card) + '。']
    if request:
        lines += ['本次请求：' + request['excerpt']['text'] + ('〔截断〕' if request['excerpt']['truncated'] else '')]
        selected = [{'id': request['id'], 'source': request['source'], 'to': request['to'],
                     'to_name': request['to_name'], **request['request_status']}]
    else:
        selected = list(reversed(status['requests']))[:limit]
        if card['progress']:
            p = card['progress'][0]
            lines += ['最近进展〔原文摘录〕：' + p['excerpt']['text'] + ('〔截断〕' if p['excerpt']['truncated'] else '')]
    states = {'pending':'待处理', 'accepted':'已承接', 'partial':'部分交付', 'delivered':'已交付',
              'adopted':'已采用', 'declined':'已拒绝', 'cancelled':'已取消'}
    requests = []
    for r in selected:
        responses = []
        lines += ['', r.get('to_name', r['to']) + '：' + states[r['state']] +
                  ('；已有 ' + str(r['response_count']) + ' 条回应。' if r['response_count'] else '；尚无回应。')]
        for response in r['responses'][:1]:
            shown = {**response, 'source': add(response['source']),
                     'superseded_by': [add(ref) for ref in response['superseded_by']]}
            responses.append(shown)
        if responses:
            latest = responses[0]
            lines += ['最近回应〔' + latest['author_name'] + ('／交互摘要' if latest['origin']=='author' else '／原文摘录') +
                      ('／有更正' if latest['superseded_by'] else '') +
                      ('／未确认发布' if not latest['accepted'] else '') + '〕：', latest['text'] + ('〔截断，按需展开〕' if latest['truncated'] else '')]
            latest.pop('text'); latest['text_in_document'] = True
        requests.append({k: r[k] for k in ('id','to','state','confirmed','response_count')})
        requests[-1].update(source=add(r['source']), responses=responses)
    materials = dict(status['materials'])
    materials['files'] = [{**f, **({'source': add(f['source'])} if 'source' in f else {})} for f in materials['files']]
    if materials['index']: materials['index'] = add(materials['index'])
    lines += ['', '资料：固定引用 ' + str(materials['file_count']) + ' 份，已登记 ' + str(materials['registered_count']) +
              ' 份，未核到登记 ' + str(materials['unavailable_count']) + ' 份；原件未校验。']
    if materials['declaration']['text']:
        lines += ['资料索引说明〔原文〕：', materials['declaration']['text'] + ('〔截断〕' if materials['declaration']['truncated'] else '')]
    total = 1 if request else status['request_count']
    if total > len(requests): lines += ['〔请求未全部展示，可按需展开〕']
    return {'format': 'thread.check.ready.v1', 'document': '\n'.join(lines) + '\n',
            'thread': thread_source, 'overall_source': add(card['overall']['source']),
            'private': not card['shared'], 'focus': add(request['source']) if request else thread_source,
            'source_index': sources, 'requests': requests, 'request_count': total,
            'truncated': total > len(requests), 'materials': materials, 'freshness': freshness,
            'read_hint': '根据整体介绍、回应摘录和登记状态，用两三句话直接概括。当前材料足够就结束；用户进一步要求内容、打开或核验时再定点读取。待处理与已有回应可同时成立，不改写正式交付状态。'}


def ready_inbox(rows, limit):
    """Use the successful ordinary inbox read; never load/read sources again."""
    shown = []
    for row in rows[:limit]:
        q = row['request']
        shown.append({'id': q['id'], 'thread': q['thread'], 'source': q['source'], 'author': q.get('author_name') or q['author'],
                      'to': q.get('to_name') or q['to'], 'state': row['state'], 'confirmed': row['confirmed'],
                      'has_response': row['response_count'] > 0, **excerpt(q['body'], 200)})
    return {'format': 'thread.inbox.ready.v1', 'requests': shown, 'total': len(rows), 'truncated': len(rows) > len(shown),
            'read_hint': '直接简述请求和状态；has_response 包含普通回应，正式状态仍按承接／交付动作。需要查看具体回应与资料时，选定 ID 用 thread check 一次读取。'}


def ready_write(result, action):
    from .thread_chat import summary
    publication = result.get('publication') if isinstance(result.get('publication'), dict) else None
    record = result['result'] if publication is not None else result
    small = {k: v for k, v in record.items() if k not in {'body','generation'}}
    if 'body' in record: small['excerpt'] = summary(record['body'],180)
    receipt = {'format': 'thread.write.ready.v1', 'action': action, 'result': small,
               'read_hint': '直接使用本次成功回执回答；保存与远端接受分别说明，无需再次 context/show。需内容或进一步执行时才展开。'}
    if publication is not None:
        receipt['publication'] = {k: publication[k] for k in ('synchronized','refreshed','errors') if k in publication}
    return receipt
