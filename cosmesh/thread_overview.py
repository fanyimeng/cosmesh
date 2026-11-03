"""Read-only private execution overview; host observations never enter source history."""
from datetime import datetime, timezone
from pathlib import Path, PurePath
import socket

from .errors import Error


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError()
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError()
    return parsed.astimezone(timezone.utc).isoformat()


def host_snapshot(value):
    """Accept only bounded metadata from a personal Agent's supported host APIs."""
    try:
        if not isinstance(value, dict) or set(value) - {'format', 'source', 'observed_at', 'sessions', 'projects'}:
            raise ValueError()
        if value.get('format') != 1:
            raise ValueError()
        timestamp(value['observed_at'])
        if not isinstance(value['source'], str) or not 1 <= len(value['source']) <= 200:
            raise ValueError()
        for kind, fields, required, limit in (
            ('sessions', {'agent', 'host', 'id', 'title', 'project_id', 'cwd', 'status', 'updated_at', 'url'},
             {'agent', 'host', 'id', 'project_id', 'status'}, 1000),
            ('projects', {'agent', 'host', 'id', 'name', 'path'}, {'agent', 'host', 'id', 'name'}, 200)):
            rows = value.get(kind, [])
            if not isinstance(rows, list) or len(rows) > limit:
                raise ValueError()
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) - fields or required - set(row):
                    raise ValueError()
                for key, text in row.items():
                    if key == 'project_id' and text is None:
                        continue
                    if not isinstance(text, str) or len(text) > 2000 or (key in required and not text):
                        raise ValueError()
                if kind == 'sessions':
                    if row['status'] not in {'active', 'idle', 'notLoaded', 'archived', 'unavailable'}:
                        raise ValueError()
                    if row.get('updated_at'):
                        timestamp(row['updated_at'])
                identity = (row['agent'].casefold(), row['host'], row['id'])
                if identity in seen:
                    raise ValueError()
                seen.add(identity)
        return value
    except (ValueError, KeyError, TypeError):
        raise Error('INPUT_INVALID', 'Invalid host metadata. Use the bounded format in guide show threads; omit chat bodies.')


def fact(value, status, source, checked_at=None, reason=None):
    result = {'value': value, 'status': status, 'source': source}
    if checked_at:
        result['checked_at'] = checked_at
    if reason:
        result['reason'] = reason
    return result


def excerpt(text, chars=280):
    return {'text': text[:chars], 'truncated': len(text) > chars}


def directory_exists(path):
    return Path(path).is_dir()


def overview(wb, value=None, mine=False, query=None, lifecycle='active', scope='all',
             details=False, limit=20, host=None, compact=False):
    if not 1 <= limit <= 100:
        raise Error('INPUT_INVALID', 'Overview limit must be between 1 and 100.')
    if scope == 'mesh' and host is not None:
        raise Error('INPUT_INVALID', 'Host metadata belongs to a private overview; omit --host-file for --scope mesh.')
    if compact and details:
        raise Error('INPUT_INVALID', 'Choose a compact overview or expanded binding details.')
    observed = host_snapshot(host) if host is not None else None
    checked_at = datetime.now(timezone.utc).isoformat()
    sessions, projects = {}, {}
    for target, name in ((sessions, 'sessions'), (projects, 'projects')):
        for row in (observed or {}).get(name, []):
            target[(row['agent'].casefold(), row['host'], row['id'])] = row
    bindings, related, directories = {}, {}, {}
    # Shared output does not even inspect private bindings or host paths.
    if scope != 'mesh':
        for binding in wb.client['bindings']:
            bindings.setdefault(binding['thread'], []).append(binding)
            if binding.get('session_id'):
                identity = (binding['agent'].casefold(), binding['host'], binding['session_id'])
                related.setdefault(identity, set()).add(binding['thread'])

    def execution(binding):
        agent, machine, sid = binding['agent'], binding['host'], binding.get('session_id')
        identity = (agent.casefold(), machine, sid)
        session = sessions.get(identity)
        usable = session is not None and session['status'] != 'unavailable'
        source = observed['source'] if usable else 'private_binding'
        at = observed['observed_at'] if usable else None
        result = {
            'agent': fact(agent, 'verified' if usable else 'registered', source, at),
            'host': fact(machine, 'verified' if usable else 'registered', source, at),
            'session': fact({'id': sid, 'title': session.get('title') if usable else None,
                             'host_status': session['status'] if usable else None},
                            'verified' if usable else ('registered' if sid else 'missing'), source, at,
                            '宿主元数据核验；尚未执行会话恢复。' if usable else
                            ('会话仅有私人登记；宿主未提供可用核验信息。' if sid else '未登记 Session ID。')),
            'project': fact(None, 'missing', source, at, '尚无宿主项目归属信息。'),
            'directory': directory(binding['workdir'], machine),
            'resume': {'status': 'verified' if usable and session['status'] != 'archived' else
                       ('registered' if sid and not usable else 'missing'),
                       'method': 'host_session_id' if sid else 'thread_context',
                       'reason': '会话已归档，需在宿主中恢复。' if usable and session['status'] == 'archived' else
                       ('按实际 Session ID 在宿主中选择；运行环境仍由宿主检查。' if sid else '可从 Thread 上下文继续，旧会话入口缺失。')},
            'also_bound_threads': sorted(related.get(identity, set()) & visible_ids - {binding['thread']}) if sid else []}
        if usable:
            pid = session['project_id']
            project = projects.get((agent.casefold(), machine, pid))
            result['project'] = fact({'id': pid, 'name': project.get('name') if project else None} if pid else None,
                                     'verified' if pid else 'missing', source, at,
                                     ('项目名称未返回，保留宿主项目 ID。' if pid and not project else None) if pid else '宿主已核验：该会话尚未归属项目。')
            if session.get('cwd'):
                result['session_workdir'] = fact(session['cwd'], 'verified', source, at)
            if session.get('url'):
                result['resume']['url'] = session['url']
        if not usable or session['project_id'] is None:
            candidates = [p for p in projects.values() if p['agent'].casefold() == agent.casefold() and
                          p['host'] == machine and p.get('path') and
                          (PurePath(binding['workdir']) == PurePath(p['path']) or
                           PurePath(p['path']) in PurePath(binding['workdir']).parents)]
            result['project_candidates'] = [{'id': p['id'], 'name': p['name'], 'status': 'candidate',
                                            'reason': '目录匹配；尚未证明会话归属。'}
                                           for p in sorted(candidates, key=lambda p: len(p['path']), reverse=True)[:3]]
        # Prefer a host-listed, unarchived session, then a registered session.
        score = (2 if result['resume']['status'] == 'verified' else 1 if result['resume']['status'] == 'registered' else 0,
                 timestamp(session['updated_at']) if usable and session.get('updated_at') else '')
        return result, score

    def directory(path, machine):
        key = (machine, path)
        if key not in directories:
            local = machine == socket.gethostname()
            exists = None
            if local and not compact:
                try:
                    exists = directory_exists(path)
                except OSError:
                    pass
            directories[key] = {'path': path, 'host': machine, 'kind': 'work',
                                'status': 'verified' if exists else 'registered',
                                'source': 'filesystem.is_dir' if exists is not None else 'private_binding',
                                'exists': exists, 'checked_at': checked_at if exists is not None else None,
                                'reason': '私人绑定；本次未核验目录。' if compact else
                                          '已核验本机目录存在；目录用途来自绑定。' if exists else
                                          ('本机目录不存在或不可访问；保留登记。' if local else '远端登记；本次未联网检查。')}
        return directories[key]

    selected = []
    pool = [item for item in wb.catalog.values() if item['doc']['meta']['kind'] == 'thread' and
            (scope != 'base' or item['repo'] == 'local') and (scope != 'mesh' or item['repo'] != 'local') and
            (not mine or item['doc']['meta']['author'] == (wb.config['user'] if item['repo'] == 'local' else wb.user))]
    if value:
        pool = [item for item in pool if item['doc']['meta']['id'].startswith(value) or item['doc']['meta']['title'] == value]
        if len(pool) != 1:
            raise Error('NOT_FOUND' if not pool else 'AMBIGUOUS_ID', 'Choose one visible Thread ID.',
                        ', '.join(item['doc']['meta']['id'] for item in pool))
    for item in pool:
        meta = item['doc']['meta']
        if query and query.casefold() not in (meta['title'] + '\n' + item['doc']['body']).casefold():
            continue
        shown = wb.present(item)
        if lifecycle != 'all' and shown['lifecycle'] != lifecycle:
            continue
        records = wb.records(meta['id'])
        latest = sorted(records, key=lambda r: (r['doc']['meta']['created_at'], r['doc']['meta']['id']), reverse=True)
        if compact:
            # Select before rendering: unshown rows need no source acceptance or excerpt work.
            selected.append((item, shown, latest))
            continue
        row = {key: shown[key] for key in ('id', 'title', 'author', 'created_at', 'work_state', 'lifecycle', 'source', 'accepted')}
        if 'author_name' in shown:
            row['author_name'] = shown['author_name']
        row.update(scope='mesh' if item['repo'] != 'local' else 'base', purpose=excerpt(shown['body']),
                   progress={'status': 'registered', 'source': shown['source'], 'record_count': len(records),
                             'recent': [{**{k: r['doc']['meta'][k] for k in ('id', 'type', 'created_at', 'author')},
                                         **excerpt(r['doc']['body']), 'source': wb.ref(r), 'accepted': wb.accepted(r) if r['repo'] != 'local' else False}
                                        for r in latest[:3]],
                             'reason': '进展来自已登记原文；个人 Agent 按需读 context 核验并概括下一步。'},
                   generation_tools=sorted({r['doc']['meta']['generation']['tool'] for r in [item, *records]
                                            if r['doc']['meta'].get('generation', {}).get('tool')}))
        selected.append((row, latest[0]['doc']['meta']['created_at'] if latest else meta['created_at']))
    if compact:
        selected.sort(key=lambda x: (x[2][0]['doc']['meta']['created_at'] if x[2] else x[1]['created_at'], x[1]['id']), reverse=True)
        visible_ids = {shown['id'] for _, shown, _ in selected}
        source_index, source_keys = [], {}
        def source(ref):
            key = (ref['repo'], ref['id'], ref['version'])
            if key not in source_keys:
                source_keys[key] = len(source_index)
                source_index.append(ref)
            return source_keys[key]
        rows = []
        for item, shown, latest in selected[:limit]:
            recent = next((r for r in latest if r['doc']['body'].strip()), None)
            row = {key: shown[key] for key in ('id', 'title', 'author', 'work_state', 'lifecycle', 'accepted')}
            if 'author_name' in shown: row['author_name'] = shown['author_name']
            row.update(scope='mesh' if item['repo'] != 'local' else 'base', source=source(shown['source']),
                       purpose=excerpt(shown['body'], 100), progress={'record_count': len(latest), 'recent': []})
            if recent:
                meta = recent['doc']['meta']
                row['progress']['recent'].append({**{k: meta[k] for k in ('id', 'type', 'author', 'created_at')},
                    **excerpt(recent['doc']['body'], 160), 'source': source(wb.ref(recent)),
                    'accepted': wb.accepted(recent) if recent['repo'] != 'local' else False})
            tools = {r['doc']['meta']['generation']['tool'] for r in [item, *latest]
                     if r['doc']['meta'].get('generation', {}).get('tool')}
            if tools: row['generation_tools'] = sorted(tools)
            if scope != 'mesh':
                entries = [(*execution(b), index) for index, b in enumerate(bindings.get(shown['id'], []))]
                entries.sort(key=lambda entry: (entry[1], entry[2]), reverse=True)
                if not entries:
                    row['execution'] = {'status': 'missing', 'binding_count': 0, 'agent': None,
                                        'project': None, 'session': None, 'directory': None}
                else:
                    preferred = entries[0][0]
                    row['execution'] = {'binding_count': len(entries),
                        'agent': preferred['agent']['value'], 'host': preferred['host']['value'],
                        'status': preferred['agent']['status'],
                        'session': {**preferred['session']['value'], 'status': preferred['session']['status']},
                        'project': {'value': preferred['project']['value'], 'status': preferred['project']['status']},
                        'directory': {'path': preferred['directory']['path'], 'status': 'registered', 'kind': 'work'},
                        'resume': {key: preferred['resume'][key] for key in ('status', 'method', 'url') if key in preferred['resume']}}
                    if preferred['project'].get('checked_at') and preferred['project']['value'] is None:
                        row['execution']['project']['verified_absence'] = True
                    if preferred.get('project_candidates'):
                        row['execution']['project_candidates'] = preferred['project_candidates']
            rows.append(row)
        result = {'format': 'thread.overview.compact.v1', 'threads': rows, 'total': len(selected),
                  'truncated': len(selected) > limit, 'scope': scope, 'private': scope != 'mesh',
                  'checked_at': checked_at, 'source_index': source_index,
                  'read_hint': 'source 为 source_index 下标；进展是有界原文摘录，概括结论和下一步时按需核验原文及更正。'}
        if scope != 'mesh':
            result['evidence'] = {'registered': '仅登记；会话未恢复，目录未探测。', 'missing': '缺失／待核验。',
                                  'verified': '宿主元数据已核验，来源和时间见 host_observation；实际恢复由宿主检查。'}
            result['expand_hint'] = 'thread overview ID --details --json；续接用 thread continue ID --json，按需要核验目标会话与目录。'
            if observed:
                result['host_observation'] = {'source': observed['source'], 'observed_at': observed['observed_at'],
                                              'verification': 'personal_agent_reported'}
        return result
    selected.sort(key=lambda pair: (pair[1], pair[0]['id']), reverse=True)
    visible_ids = {row['id'] for row, _ in selected}
    total = len(selected)
    rows = [row for row, _ in selected[:limit]]
    if scope != 'mesh':
        for row in rows:
            entries = [(*execution(b), index) for index, b in enumerate(bindings.get(row['id'], []))]
            entries.sort(key=lambda entry: (entry[1], entry[2]), reverse=True)
            row['execution'] = {'preferred': entries[0][0] if entries else None,
                                'binding_count': len(entries),
                                'status': entries[0][0]['session']['status'] if entries else 'missing',
                                'reason': None if entries else '未登记执行 Agent、会话和目录；可先读 Thread 上下文。',
                                'details_command': 'thread overview ' + row['id'] + ' --details --json'}
            if details:
                row['execution']['bindings'] = [entry[0] for entry in entries[:100]]
                row['execution']['truncated'] = len(entries) > 100
    result = {'threads': rows, 'total': total, 'truncated': total > limit, 'scope': scope,
              'private': scope != 'mesh', 'checked_at': checked_at}
    if scope != 'mesh':
        result['host_observation'] = {'source': observed['source'], 'observed_at': observed['observed_at'],
                                     'verification': 'personal_agent_reported'} if observed else None
        result['execution_hint'] = 'verified=已核验且附来源/时间；registered=仅登记；missing=缺失。目录匹配为候选，生成工具不等于执行 Agent。数据/产物目录用途未登记时不猜测。'
    return result
