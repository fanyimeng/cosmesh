"""Prepared human text and a small source index; no extra source reads."""
import html
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def cell(value):
    return html.escape(str(value), quote=False).replace('\\', '\\\\').replace('|', '\\|').replace('\n', '<br>')


def state(card):
    value = {'active': '进行中', 'done': '已完成', 'cancelled': '已取消'}.get(card['work_state'], card['work_state'])
    if card['lifecycle'] != 'active':
        value += '／' + {'archived': '已归档', 'trash': '回收站', 'withdrawn': '已撤回', 'merged': '已合并'}.get(card['lifecycle'], card['lifecycle'])
    if card['shared'] and not card['accepted']: value += '〔接受状态未核验〕'
    return value


def overview_text(card):
    naming=card.get('naming',{})
    if naming.get('conflicted'):return '命名与简介有并行修改，请按需确认。'
    if naming.get('intro'):return naming['intro']
    text = card['overall']['text'] or '整体介绍缺失。'
    return text + ('〔介绍截断，可展开〕' if card['overall']['truncated'] else '')


def short_title(card):
    names=card.get('naming',{})
    if names.get('conflicted'):return card['title']+'〔命名待确认〕'
    return ' · '.join(x for x in (names.get('english'),names.get('chinese')) if x) or card['title']


def progress_text(card):
    if not card['progress']: return '暂无进展记录'
    entry = card['progress'][0]
    text = entry['excerpt']['text']
    if entry['author'] != card['author']: text = entry.get('author_name', entry['author']) + '：' + text
    return text + ('〔摘录截断〕' if entry['excerpt']['truncated'] else '')


def project_text(card, private):
    if not private: return '私人关联不公开'
    labels = {'codex':'Codex','dsh':'DSH','opencode':'OpenCode'}
    projects = card.get('projects',[])
    if not projects:
        agent = (card.get('execution') or {}).get('agent')
        return (labels.get(agent,agent)+'：' if agent else '')+'未关联项目／工作区'
    values = []
    for p in projects:
        kind = {'project':'项目','workspace':'工作区','directory':'目录工作区'}[p['kind']]
        state = '宿主核验登记' if p['status']=='verified' else '已登记'
        values.append(labels.get(p['agent'],p['agent'])+' · '+p['name']+'（'+kind+'，'+state+'）')
    if card['project_count'] > len(projects): values.append('另有'+str(card['project_count']-len(projects))+'个关联，按需展开')
    return '\n'.join(values)


def user_document(user, cards, level, private=False):
    lines = ['# ' + user + '的工作总览', '',
             '| 工作 | 简介 | 状态与进展 | Agent 项目／工作区（关联情况） | 私人/共享 |', '| --- | --- | --- | --- | --- |']
    for card in cards:
        lines.append('| ' + ' | '.join(map(cell, (short_title(card), overview_text(card),
                     state(card)+'；'+progress_text(card), project_text(card,private), 'Mesh（共享）' if card['shared'] else 'Base（私人）'))) + ' |')
    if not cards: lines.append('当前所选范围没有工作。')
    if level == 'status':
        lines += ['', '本页已登记成果 ' + str(sum(c['counts']['results'] for c in cards)) +
                  ' 项；未终结请求 ' + str(sum(c['counts']['open_requests'] for c in cards)) + ' 项。']
    if level == 'index':
        lines += ['', '## 工作入口']
        for card in cards: lines.append('- ' + card['title'] + '：' + card['id'] + '；用 --level status / index 展开。')
    return '\n'.join(lines) + '\n'


def continuation_document(card, status):
    """Only information additional to the overview row; keep quotations explicit."""
    lines = ['## 继续：' + short_title(card), '',
             '已有 ' + str(status['details']['total']) + ' 条记录；状态为' + state(card) + '。']
    if card['merged_into']:
        lines.append('这条工作已合并，可沿接续去向展开。')
    summary = status['summary']
    if summary:
        text = summary['text']
        for heading, shown in (('整体介绍', card['overall']['text'] if not card['overall']['truncated'] else None),
                               ('下一步', card['next_step'] if not summary['pending'] else None)):
            if not shown: continue
            pattern = r'(?ms)^#{1,4}[ \t]*' + heading + r'[ \t]*\n(.*?)(?=^#{1,4}\s|\Z)'
            text = re.sub(pattern, lambda m: '' if m.group(1).strip() == shown.strip() else m.group(0), text)
        if text.strip():
            lines += ['', '已登记归纳' + ('〔有后续变化，仅供历史参考〕' if summary['pending'] else '') + '：', text.strip()]
        elif summary['pending']: lines.append('归纳有后续变化，仅供历史参考。')
        if summary['truncated']: lines.append('〔归纳截断，按来源展开〕')
    else: lines.append('尚无单独归纳；进展见已列出的原文摘录。')
    if card['next_step']:
        step = card['next_step']
        lines += ['', '已登记下一步：' + step[:1200] + ('〔截断，按来源展开〕' if len(step) > 1200 else '')]
    elif summary and summary['pending']: lines.append('下一步待重新整理，沿新记录核验。')
    if status['results']:
        lines += ['', '成果／更正：'] + ['- ' + r.get('author_name', r['author']) + '：' + r['excerpt']['text'] +
                  ('〔截断〕' if r['excerpt']['truncated'] else '') for r in status['results']]
    if status['requests']:
        lines += ['', '请求：'] + ['- ' + r['id'] + ' → ' + r['to'] + '：' + r['state'] +
                  ('' if r['confirmed'] else '〔接受状态未核验〕') for r in status['requests']]
        if status['request_count'] > len(status['requests']): lines.append('〔请求未全部展示，可展开〕')
    if status['dependency_count'] or status['backlink_count']:
        lines.append('另有 ' + str(status['dependency_count']) + ' 项引用、' + str(status['backlink_count']) + ' 项回链，按细节索引展开。')
    lines.append('记录和依据可按需展开；实际执行前核验原文与执行位置。')
    return '\n'.join(lines) + '\n'


def thread_document(card, status=None, private=False):
    lines = ['# ' + short_title(card), '', '## 整体介绍', overview_text(card), '',
             '状态：' + state(card) + '；作者：' + card['author_name'] + '；范围：' + ('Mesh' if card['shared'] else 'Base'),
             '', '## 近期进展', progress_text(card)]
    if private:
        lines += ['', 'Agent 项目／工作区：' + project_text(card,True)]
    if status:
        if status['goal']['text'].strip() != card['overall']['text'].strip():
            lines += ['', '## 工作定义', status['goal']['text'] + ('〔截断〕' if status['goal']['truncated'] else '')]
        lines += ['', continuation_document(card, status)]
    return '\n'.join(lines) + '\n'


def ready_receipt(result, document, preview=None, timezone='UTC'):
    """Keep fixed references and attribution without a second copy of each card."""
    sources, positions, authors = [], {}, {}
    def add(ref, author=None):
        key = (ref['repo'], ref['id'], ref['version'])
        if key not in positions: positions[key] = len(sources);sources.append(ref)
        index = positions[key]
        if author: authors[str(index)] = author
        return index
    def walk(value):
        if isinstance(value, list):
            for item in value: walk(item)
        elif isinstance(value, dict):
            if set(value) == {'repo','id','version'}: add(value);return
            if isinstance(value.get('source'), dict): add(value['source'], value.get('author'))
            for item in value.values(): walk(item)
    cards = result.get('threads', [result['thread']] if 'thread' in result else [])
    for card in cards: walk(card)
    focus = result.get('continuation', result if 'status' in result else {})
    if 'thread' in focus: walk(focus['thread'])
    if 'status' in focus:
        status = focus['status']
        for field in ('summary', 'results', 'requests'): walk(status[field])
    if 'entries' in result: walk(result['entries'])
    if preview: walk(preview['records'])
    rows = [add(card['source'], card['author']) for card in cards]
    cache = result['cache']
    ready = {'format': 'status.page.ready.v1', 'target': result['target'], 'scope': result['scope'], 'private': result['private'],
             'level': result['level'], 'document': document, 'source_index': sources, 'authors': authors,
             'rows': rows, 'total': result.get('total', len(rows)), 'offset': result['offset'],
             'truncated': result.get('truncated', False),
             'freshness': {k: cache[k] for k in ('generated_at','local_sources_current','remote_checked_at')},
             'cache_hit': cache['hit'], 'document_version': cache['page_version'],
             'read_hint': 'document 是已排版成品，可直接呈现；rows/focus 为 source_index 下标，正文摘录按固定来源核验。'}
    if 'continuation' in result: ready['focus'] = add(result['continuation']['thread']['source'])
    if preview:
        ready['record_preview'] = {'total': preview['total'], 'shown': len(preview['records']),
            'scope':'work_record_bodies_excluding_naming_and_referenced_documents',
            'body_complete': preview['total'] == len(preview['records']) and all(not r['excerpt']['truncated'] for r in preview['records']),
            'records': [{'source': add(r['source'], r['author']), 'kind': r['kind'],
                         'author': r.get('author_name', r['author']), 'text': r['excerpt']['text'],
                         'truncated': r['excerpt']['truncated'],
                         'superseded_by': [add(ref) for ref in r['superseded_by']]} for r in preview['records']]}
        ready['read_hint'] += ' record_preview 是本次判断用的有界原文；body_complete 只表示目标记录正文已覆盖，不表示引用资料全文已读。不重复读取已覆盖正文或转贴这份预览。'
    if ready['truncated']: ready['document'] += '\n〔本页未全部展示；按 offset/limit 继续〕\n'
    def display_time(value):
        if not value: return '未核验'
        try: return datetime.fromisoformat(value).astimezone(ZoneInfo(timezone)).strftime('%Y-%m-%d %H:%M %z')
        except (ValueError, ZoneInfoNotFoundError): return value
    ready['document'] += '\n本地成品截至 ' + display_time(cache['generated_at']) + '；远端核验：' + display_time(cache['remote_checked_at']) + '。\n'
    if not cache['local_sources_current']: ready['document'] += '〔来源已变化，实际执行前需重新核验〕\n'
    ready['document_version'] = hashlib.sha256(ready['document'].encode()).hexdigest()
    return ready
