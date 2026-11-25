"""Versioned, offline documentation with deterministic section retrieval."""
import hashlib
import re
from pathlib import Path

from . import __version__
from .errors import Error

TOPICS = {
    'usage':'工作、收集和收件', 'personal':'个人接续', 'sharing':'选定内容投递', 'chat':'按人交互与收件',
    'components':'组件安装与管理','dsh':'DSH 宿主适配','upgrade':'本版更新与快速上手',
    'architecture':'工作与权限','threads':'执行位置','files':'模块职责','profile':'私人档案','operations':'安装和运维','feedback':'开发反馈',
}



def document(topic):
    if topic not in TOPICS:
        raise Error('NOT_FOUND', 'Unknown guide topic; use guide list.')
    path = Path(__file__).parent / 'manual' / (topic + '.md')
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines()
    boundaries = [(i, line.lstrip('# ').strip()) for i, line in enumerate(lines) if re.match(r'^#{1,3} ', line)]
    sections = []
    for number, (start, heading) in enumerate(boundaries, 1):
        end = boundaries[number][0] if number < len(boundaries) else len(lines)
        sections.append({'section': number, 'heading': heading, 'start_line': start + 1,
                         'end_line': end, 'text': '\n'.join(lines[start:end]) + '\n'})
    return {'topic': topic, 'title': TOPICS[topic], 'client_version': __version__, 'text': text,
            'sections': sections, 'source': {'resource': 'manual/' + topic + '.md',
                                           'sha256': hashlib.sha256(text.encode()).hexdigest()}}


def listing():
    return {'client_version': __version__, 'topics': [{'topic': k, 'title': v} for k, v in TOPICS.items()]}


def show(topic, section=None):
    result = document(topic)
    if section is not None:
        item = next((s for s in result['sections'] if s['section'] == section), None)
        if item is None:
            raise Error('NOT_FOUND', 'No such guide section.')
        result = {**result, **item}
        result.pop('sections')
    return result


def search(query, limit=8):
    if not query.strip() or not 1 <= limit <= 30 or len(query) > 500:
        raise Error('INPUT_INVALID', 'Provide a short query and a limit from 1 to 30.')
    tokens = set(re.findall(r'[a-z0-9_./-]+', query.casefold()))
    for run in re.findall(r'[\u4e00-\u9fff]+', query):
        tokens.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    results = []
    for topic in TOPICS:
        doc = document(topic)
        for item in doc['sections']:
            if not any(line.strip() and not line.startswith('#') for line in item['text'].splitlines()):
                continue
            body, heading = item['text'].casefold(), item['heading'].casefold()
            score = sum((4 if token in heading else 2 if token == topic else 1)
                        for token in tokens if token in body or token == topic)
            if score:
                results.append({**{k: v for k, v in item.items() if k != 'text'}, 'topic': topic,
                                'title': doc['title'], 'score': score, 'snippet': item['text'][:800],
                                'source': doc['source']})
    results.sort(key=lambda x: (-x['score'], x['topic'], x['section']))
    return {'client_version': __version__, 'query': query, 'matches': results[:limit], 'total_matches': len(results)}
