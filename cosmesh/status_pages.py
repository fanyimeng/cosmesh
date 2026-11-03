"""Disposable local status pages: source records remain authoritative.

No model, polling, network, or shared-home database. A bounded one-shot worker
coalesces committed changes; readers recover cold, stale, and damaged caches.
"""
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import Error
from .store import Store, atomic, safe_path
from .threads import Workbench, now
from .thread_protocol import REPO, reference_edges

FORMAT = 5
LEVELS = ('brief', 'status', 'index')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def snippet(text, chars):
    return {'text': text[:chars], 'truncated': len(text) > chars}


def cache_root(wb, create=True):
    # tempfile is host-local on supported installations; do not put SQLite in
    # the workbench or a shared home. The directory is private even for Mesh.
    parent = safe_path(os.environ.get('COSMESH_PAGE_CACHE', str(Path(tempfile.gettempdir()).resolve() / ('cosmesh-pages-' + str(os.geteuid())))))
    if parent == wb.base.root or wb.base.root in parent.parents:
        raise Error('PATH_INVALID', 'Keep derived status pages outside the source workbench.')
    if create: parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in (parent, parent / digest(str(wb.base.root))[:32]):
        if path.exists() and (path.is_symlink() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077):
            raise Error('AUTH_DENIED', 'Status-page caches must be private and owned by this account.')
    root = parent / digest(str(wb.base.root))[:32]
    if create: root.mkdir(mode=0o700, exist_ok=True)
    return root


@contextmanager
def database(root):
    path = safe_path(root / 'index.sqlite3')
    if path.exists() and (path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077):
        raise Error('AUTH_DENIED', 'The derived index must be private.')
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600); os.close(fd)
    con = sqlite3.connect(str(path), timeout=2)
    con.row_factory = sqlite3.Row
    try:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS threads (
          id TEXT PRIMARY KEY, repo TEXT, author TEXT, title TEXT,
          lifecycle TEXT, updated TEXT, signature TEXT, card TEXT, status TEXT, execution TEXT);
        CREATE INDEX IF NOT EXISTS thread_owner ON threads(author, repo, lifecycle, updated);
        CREATE TABLE IF NOT EXISTS aliases (thread TEXT, name TEXT, PRIMARY KEY(thread,name));
        CREATE INDEX IF NOT EXISTS alias_lookup ON aliases(name);
        CREATE TABLE IF NOT EXISTS entries (
          thread TEXT, id TEXT PRIMARY KEY, kind TEXT, created TEXT, data TEXT);
        CREATE INDEX IF NOT EXISTS entry_parent ON entries(thread, created, id);
        CREATE TABLE IF NOT EXISTS pages (
          key TEXT PRIMARY KEY, version TEXT, body TEXT);
        ''')
        yield con
    finally:
        con.close()


def meta(con, key, default=None):
    row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put_meta(con, key, value):
    con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, encoded(value)))


def stamp(wb):
    """Cheap committed-version checks; no snapshots, bound paths or network."""
    client = wb.read_client()
    config = json.loads(wb.base.path('.cosmesh/config.json').read_text())
    sources = {}
    for repo in ['local', *sorted(client['spaces'])]:
        if repo != 'local' and not REPO.fullmatch(repo):
            raise Error('SCHEMA_INVALID', 'Invalid cached repository ID.')
        store = wb.base if repo == 'local' else Store(wb.base.path('.cosmesh/mesh/' + repo))
        if not store.path('.cosmesh/config.json').is_file(): continue
        store.path('.git')
        if store.pending.exists() or store.path('.cosmesh/conflict.json').exists():
            raise Error('STORE_BUSY', 'A source operation needs recovery before reading status pages.')
        sources[repo] = {'head': store.head, 'config': digest(store.path('.cosmesh/config.json').read_text()),
                         'client': store.client()}
    knowledge = wb.base.path('.cosmesh/knowledge/snapshot.json')
    # Directory/knowledge changes matter without a Thread HEAD change.
    kb = (knowledge.stat().st_mtime_ns, knowledge.stat().st_size) if knowledge.is_file() else None
    public = {'sources': {r: v for r, v in sources.items() if r != 'local'}, 'knowledge': kb,
              'actor': client.get('actor'), 'spaces': client['spaces'], 'last_sync': client.get('last_sync')}
    return {'all': digest([FORMAT, sources, client, kb]),
            'base': digest([FORMAT, sources, config, client.get('bindings'), client.get('project_bindings'), kb]),
            'mesh': digest([FORMAT, public])}


def section(text, heading):
    match = re.search(r'(?ms)^#{1,4}[ \t]*' + re.escape(heading) + r'[ \t]*\n(.*?)(?=^#{1,4}\s|\Z)', text)
    return match.group(1).strip() if match else None


def introduction(text):
    explicit = section(text, '整体介绍')
    if explicit: return explicit
    for paragraph in text.strip().split('\n\n'):
        value = '\n'.join(line for line in paragraph.splitlines() if not line.lstrip().startswith('#')).strip()
        if value: return value
    return ''


def reference_item(wb, item, corrected, names=None):
    doc, m = item['doc'], item['doc']['meta']
    from .thread_names import parse
    return {'id': m['id'], 'kind': 'naming' if parse(doc) is not None else m.get('type', 'thread'), 'author': m['author'],
            'author_name': (names or {}).get(m['author'], m['author']),
            'created_at': m['created_at'], 'title': m['title'], 'source': wb.ref(item),
            'excerpt': snippet(doc['body'], 220), 'superseded_by': corrected.get(m['id'], []),
            'refs': [{'relation': relation, 'source': ref} for relation, ref in reference_edges(m)]}


def dependency(wb, ref, private):
    source = wb.catalog.get(ref['id'])
    corrections = [wb.ref(x) for x, relation, _ in wb._incoming.get((ref['repo'], ref['id']), [])
                   if relation == 'supersedes' and (private or x['repo'] != 'local')]
    current, records = None, []
    if source:
        current = wb.ref(source)
        parent = source['doc']['meta'].get('thread', source['doc']['meta']['id'])
        records = [wb.ref(x) for x in wb.records(parent)]
    if ref['repo'] == 'library':
        from .knowledge import Knowledge
        current = Knowledge(wb).data['heads'].get(ref['id'])
    return {'current': current, 'corrections': corrections}, records


def input_signature(wb, item, users):
    """Only regenerate cards whose facts, references or personal bindings changed."""
    from .knowledge import Knowledge
    docs = [item, *wb.records(item['doc']['meta']['id'])]
    inputs = [(wb.ref(x), wb.accepted(x) if x['repo'] != 'local' else False) for x in docs]
    related = []
    for x in docs:
        for _, ref in reference_edges(x['doc']['meta']):
            related.append(dependency(wb, ref, item['repo'] == 'local'))
        for source, relation, ref in wb._incoming.get((x['repo'], x['doc']['meta']['id']), []):
            if item['repo'] == 'local' or source['repo'] != 'local':
                parent = wb.find(source['doc']['meta']['thread'], 'thread')
                related.append([wb.ref(source), relation, ref, wb.ref(parent)])
    contributors = {x['doc']['meta']['author'] for x in docs}
    from .agent_projects import active
    return digest([FORMAT, inputs, related,
                   [u for u in users if u['account'] in contributors],
                   [b for b in wb.client['bindings'] if b['thread'] == item['doc']['meta']['id']],
                   active(wb,item['doc']['meta']['id'])])


def thread_data(wb, item, users):
    doc, m = item['doc'], item['doc']['meta']; records = wb.records(m['id'])
    corrected = {}
    for r in records:
        old = r['doc']['meta'].get('supersedes')
        if old: corrected.setdefault(old['id'], []).append(wb.ref(r))
    names = {u['account']: u['name'] or u['account'] for u in users
             if item['repo'] != 'local' or u['account'] == wb.user}
    index = [reference_item(wb, r, corrected, names) for r in records]
    active = [r for r in records if r['doc']['meta']['id'] not in corrected]
    from .thread_names import parse,view
    summaries = [r for r in active if r['doc']['meta']['type'] == 'summary' and parse(r['doc']) is None]
    summary = summaries[-1] if summaries else None
    pending = bool(summary and any(r['doc']['meta']['created_at'] > summary['doc']['meta']['created_at']
                                   for r in active if r['doc']['meta']['type'] != 'summary'))
    intro_pending = False
    dependencies = []
    for r in records:
        for relation, ref in reference_edges(r['doc']['meta']):
            dep, _ = dependency(wb, ref, item['repo'] == 'local')
            current = dep['current']
            dependencies.append({'record': r['doc']['meta']['id'], 'relation': relation,
                                 'pinned': ref, **dep})
            if summary and r is summary and current and current not in (ref, ref['version']): pending = True
            if summary and r is summary and (dep['corrections'] or current is None): pending = True
            if summary and r is summary and ref['id'] == m['id'] and current != ref: intro_pending = True
    overview = section(summary['doc']['body'], '整体介绍') if summary and not intro_pending else None
    basis = wb.ref(summary) if overview else wb.ref(item)
    # Overall describes the work itself, never the last progress message.
    overall = {**snippet(overview or introduction(doc['body']), 600), 'source': basis,
               'basis': 'authored_summary' if overview else 'thread_definition',
               'missing': not bool((overview or doc['body']).strip())}
    shown = wb.present(item)
    latest = [r for r in active if r['doc']['meta']['type'] != 'summary'][-3:]
    author = next((u for u in users if u['account'] == m['author'] and
                   (item['repo'] != 'local' or m['author'] == wb.user)), {})
    card = {key: shown[key] for key in ('id', 'title', 'author', 'work_state', 'lifecycle', 'merged_into', 'source', 'shared', 'accepted')}
    recent = [reference_item(wb, r, corrected, names) for r in reversed(latest)]
    card.update(author_name=author.get('name') or m['author'], overall=overall,naming=view(wb,m['id']),
                progress=[{key: value for key, value in r.items() if key in {'id','kind','author','author_name','source','excerpt'}} for r in recent[:1]],
                updated_at=max([m['created_at']] + [r['doc']['meta']['created_at'] for r in records]),
                next_step=section(summary['doc']['body'], '下一步') if summary and not pending else None,
                summary_state='pending' if pending else ('recorded' if summary else 'not_recorded'))
    requests = []
    for r in records:
        if r['doc']['meta']['type'] == 'request':
            state = wb.request_state(r)
            requests.append({'id': r['doc']['meta']['id'], 'to': r['doc']['meta']['to'],
                             'source': wb.ref(r), 'state': state['state'], 'confirmed': state['confirmed']})
    card['counts'] = {'records': len(index),
                      'results': sum(r['kind'] in {'result','correction'} and not r['superseded_by'] for r in index),
                      'open_requests': sum(r['state'] not in {'declined','adopted','cancelled'} for r in requests)}
    incoming = wb.incoming_links([(x['repo'], x['doc']['meta']['id']) for x in [item, *records]],
                                 include_private=item['repo'] == 'local')
    backlinks = [{'record': x['record']['source'], 'thread': x['thread'], 'relation': x['relation'],
                  'target': x['target']} for x in incoming]
    groups = {}
    for r in index: groups[r['kind']] = groups.get(r['kind'], 0) + 1
    status = {'goal': snippet(doc['body'], 2400), 'summary':
              {'source': wb.ref(summary), 'author': summary['doc']['meta']['author'],
               'generation': summary['doc']['meta'].get('generation'),
               **snippet(summary['doc']['body'], 3000), 'pending': pending} if summary else None,
              'recent': recent,
              'results': [r for r in index if r['kind'] in {'result', 'correction'} and not r['superseded_by']][-5:],
              'requests': requests[-20:], 'request_count': len(requests),
              'details': {'total': len(index), 'by_kind': groups, 'read': 'thread page ' + m['id'] + ' --level index'},
              'dependencies': dependencies[:20], 'dependency_count': len(dependencies),
              'backlinks': backlinks[:20], 'backlink_count': len(backlinks)}
    # Personal execution data is stored separately and never enters Mesh pages.
    from .agent_projects import active
    execution = {'sessions':[b for b in wb.client['bindings'] if b['thread'] == m['id']],
                 'projects':active(wb,m['id'])}
    signature = digest([card, status, index, execution])
    return card, status, index, execution, signature


def md_thread(card, status=None, entries=None, execution=None, private=False):
    if entries is None:
        from .status_receipts import thread_document
        return thread_document(card, status, private)
    lines = ['# ' + card['title'], '', '## 整体介绍', card['overall']['text'] or '尚未登记整体介绍。', '',
             '状态：' + card['work_state'] + '；收纳：' + card['lifecycle'] + '；作者：' + card['author_name'],
             '介绍依据：' + card['overall']['source']['id'] + '@' + card['overall']['source']['version'],
             '整体介绍为有界摘录。' if card['overall']['truncated'] else '', '', '## 近期进展']
    lines += ['- ' + r['excerpt']['text'] + ' [' + r['id'] + ']' for r in card['progress']]
    if not card['progress']: lines.append('暂无进展记录。')
    lines += ['', '下一步：' + (card['next_step'] or '未单独整理；按需查看进展与细节。')]
    if status:
        lines += ['', '## 工作目标', status['goal']['text'], '', '## 已登记的归纳']
        summary = status['summary']
        lines.append(('有后续变化，以下归纳仅供历史参考：\n' if summary and summary['pending'] else '') +
                     (summary['text'] if summary else '尚未登记；当前页面由程序组织事实与原文摘录。'))
        lines += ['', '## 成果与更正'] + ['- ' + r['title'] + ' [' + r['id'] + ']' for r in status['results']]
        lines += ['', '## 请求'] + ['- ' + r['id'] + '：' + r['state'] for r in status['requests']]
        lines += ['', '## 细节入口', encoded(status['details'])]
    if execution:
        lines += ['', '## 私人执行登记（尚未核验）'] + ['- ' + encoded(b) for b in execution]
    if entries is not None:
        lines += ['', '## 细节索引']
        for r in entries:
            lines += ['- ' + r['kind'] + ' | ' + r['author'] + ' | ' + r['title'],
                      '  ' + r['id'] + '@' + r['source']['version'],
                      '  ' + r['excerpt']['text'].replace('\n', ' ')]
            if r['superseded_by']: lines.append('  已更正：' + encoded(r['superseded_by']))
    return '\n'.join(x for x in lines if x is not None) + '\n'


def md_user(user, cards, level, private=False):
    from .status_receipts import user_document
    return user_document(user, cards, level, private)


def save_page(con, root, key, body):
    version = hashlib.sha256(body.encode()).hexdigest()
    old = con.execute('SELECT version FROM pages WHERE key=?', (key,)).fetchone()
    if old and old[0] == version: return False
    # The SQLite transaction is the coherent read surface; MD files are readable
    # materializations with a content version, and may be reconstructed.
    atomic(safe_path(root / 'pages' / (key + '.md')), '<!-- status-page ' + version + ' -->\n' + body)
    con.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?)', (key, version, body))
    return True


def rebuild(wb, root):
    """Called with the ordinary source lock and a loaded fixed workbench."""
    from .knowledge import Knowledge
    users = Knowledge(wb).data['users']
    rebuilt, removed, owners = [], [], set()
    with database(root) as con, con:
        if not meta(con, 'identity'): owners.add(wb.user)
        ids = set()
        for item in wb.catalog.values():
            if item['doc']['meta']['kind'] != 'thread': continue
            tid = item['doc']['meta']['id']; ids.add(tid)
            old = con.execute('SELECT signature, author, repo FROM threads WHERE id=?', (tid,)).fetchone()
            signature = input_signature(wb, item, users)
            if old and old['signature'] == signature: continue
            card, status, index, execution, _ = thread_data(wb, item, users)
            rebuilt.append(tid); owners.add(wb.user if item['repo'] == 'local' else card['author'])
            if old: owners.add(wb.user if old['repo'] == 'local' else old['author'])
            status['local_execution'] = execution['sessions'];status['local_projects'] = execution['projects']
            con.execute('INSERT OR REPLACE INTO threads VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (tid, item['repo'], card['author'], card['title'], card['lifecycle'], card['updated_at'],
                         signature, encoded(card), encoded(status), encoded(execution)))
            from .thread_names import normalized
            con.execute('DELETE FROM aliases WHERE thread=?',(tid,))
            con.executemany('INSERT OR IGNORE INTO aliases VALUES (?,?)',[(tid,normalized(a)) for a in card['naming']['aliases']])
            con.execute('DELETE FROM entries WHERE thread=?', (tid,))
            con.executemany('INSERT INTO entries VALUES (?,?,?,?,?)',
                            [(tid, r['id'], r['kind'], r['created_at'], encoded(r)) for r in index])
            for scope in ('all', 'base', 'mesh'):
                if (scope == 'mesh' and item['repo'] == 'local') or (scope == 'base' and item['repo'] != 'local'): continue
                shown = execution_card(card, execution if scope != 'mesh' else [])
                for level in LEVELS:
                    save_page(con, root, scope + '/' + tid + '/' + level,
                              md_thread(shown, status if level == 'status' else None,
                                        index[:50] if level == 'index' else None,
                                        execution if scope != 'mesh' and level == 'status' else None, scope != 'mesh'))
                from .status_receipts import continuation_document
                save_page(con, root, scope + '/' + tid + '/followup', continuation_document(shown, status))
        for row in con.execute('SELECT id, author, repo FROM threads').fetchall():
            if row['id'] not in ids:
                removed.append(row['id']); owners.add(wb.user if row['repo'] == 'local' else row['author'])
                con.execute('DELETE FROM threads WHERE id=?', (row['id'],));con.execute('DELETE FROM entries WHERE thread=?', (row['id'],))
                con.execute('DELETE FROM aliases WHERE thread=?',(row['id'],))
                con.execute('DELETE FROM pages WHERE key LIKE ?', ('%/' + row['id'] + '/%',))
        # Only owners whose cards changed need aggregate pages replaced.
        for owner in owners:
            display = next((u['name'] for u in users if u['account'] == owner and u['name']), owner)
            for scope in (('all', 'base', 'mesh') if owner == wb.user else ('mesh',)):
                selected = (wb.config['user'], wb.user) if owner == wb.user else owner
                rows = select_rows(con, scope, selected, False, None, 20, 0)
                cards = [personal_card(row, scope) for row in rows]
                for level in LEVELS:
                    save_page(con, root, scope + '/user-' + owner + '/' + level, md_user(display, cards, level, scope != 'mesh'))
        put_meta(con, 'tokens', stamp(wb));put_meta(con, 'users', users)
        put_meta(con, 'identity', {'local': wb.config['user'], 'actor': wb.user, 'timezone': wb.config['timezone']})
        put_meta(con, 'sync', wb.status());put_meta(con, 'generated_at', now())
        result = {'threads_rebuilt': rebuilt, 'threads_removed': removed, 'users_rebuilt': sorted(owners)}
        put_meta(con, 'last_update', result)
    atomic(root / 'last-update.json', encoded(result))
    return result


def refresh(wb, root):
    with wb.base.locked():
        wb.load()
        return rebuild(wb, root)


def select_rows(con, scope, user, all_states, query, limit, offset, count=False):
    where, values = [], []
    if scope == 'mesh': where.append("repo!='local'")
    if scope == 'base': where.append("repo='local'")
    if isinstance(user, tuple):
        where.append("((repo='local' AND author=?) OR (repo!='local' AND author=?))"); values += list(user)
    elif user is not None: where.append('author=?');values.append(user)
    if not all_states: where.append("lifecycle='active'")
    if query: where.append('(instr(lower(title),?)>0 OR instr(lower(card),?)>0)');values += [query.casefold()] * 2
    columns = 'count(*)' if count else 'id,author,title,lifecycle,updated,card,execution'
    sql = 'SELECT ' + columns + ' FROM threads' + (' WHERE ' + ' AND '.join(where) if where else '')
    if count: return con.execute(sql, values).fetchone()[0]
    return con.execute(sql + ' ORDER BY updated DESC,id LIMIT ? OFFSET ?', (*values, limit, offset)).fetchall()


def personal_card(row, scope):
    return execution_card(json.loads(row['card']), json.loads(row['execution']) if scope != 'mesh' else [])


def execution_card(card, bindings):
    card = dict(card)
    if isinstance(bindings,dict):
        card['projects'] = bindings['projects'][:3];card['project_count'] = len(bindings['projects'])
        bindings = bindings['sessions']
    if bindings:
        b = bindings[-1]
        card['execution'] = {'agent': b['agent'], 'host': b['host'], 'status': 'registered',
                             'session': {'id': b['session_id'], 'status': 'registered' if b['session_id'] else 'missing'},
                             'directory': {'path': b['workdir'], 'status': 'registered'},
                             'project': {'value': None, 'status': 'missing'}, 'binding_count': len(bindings)}
    return card


def target_page(con, args, value, level):
    where = "repo!='local'" if args.scope == 'mesh' else ("repo='local'" if args.scope == 'base' else '1')
    from .thread_names import normalized
    rows = con.execute('SELECT * FROM threads WHERE '+where+' AND id=?',(value,)).fetchall()
    if not rows: rows=con.execute('SELECT * FROM threads WHERE '+where+' AND instr(id,?)=1 LIMIT 2',(value,)).fetchall()
    if not rows: rows=con.execute('SELECT * FROM threads WHERE '+where+' AND id IN (SELECT thread FROM aliases WHERE name=?) LIMIT 2',(normalized(value),)).fetchall()
    if len(rows) != 1:
        raise Error('NOT_FOUND' if not rows else 'AMBIGUOUS_ID', 'Choose one visible Thread ID.', ', '.join(r['id'] for r in rows))
    row = rows[0];card = personal_card(row, args.scope);status = json.loads(row['status'])
    conditions, values = 'thread=?', [row['id']]
    if args.query: conditions += ' AND instr(lower(data),?)>0';values.append(args.query.casefold())
    total = con.execute('SELECT count(*) FROM entries WHERE ' + conditions, values).fetchone()[0]
    entries = [json.loads(r[0]) for r in con.execute('SELECT data FROM entries WHERE ' + conditions +
               ' ORDER BY created,id LIMIT ? OFFSET ?', (*values, args.limit, args.offset))] if level == 'index' else None
    execution = status.pop('local_execution')
    projects = status.pop('local_projects',[])
    result = {'target': 'thread', 'thread': card}
    if level == 'status':
        result['status'] = status
        if args.scope != 'mesh': result.update(local_execution=execution,local_projects=projects)
    if entries is not None: result.update(entries=entries, total=total, truncated=total > args.offset + len(entries))
    body = md_thread(card, status if level == 'status' else None, entries,
                     execution if args.scope != 'mesh' and level == 'status' else None, args.scope != 'mesh')
    return result, body, args.scope + '/' + row['id'] + '/' + level


def read_page(wb, args):
    if not 1 <= args.limit <= 100 or args.offset < 0:
        raise Error('INPUT_INVALID', 'Use a limit from 1 to 100 and a nonnegative offset.')
    root = cache_root(wb)
    token = stamp(wb)
    recovered = False
    try:
        with database(root) as con:
            ready = meta(con, 'tokens', {}).get(args.scope) == token[args.scope]
    except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        if isinstance(exc, sqlite3.OperationalError):
            raise Error('STORE_BUSY', 'Status pages are being updated; retry shortly.')
        # Preserve a damaged derivative for diagnosis; never alter source files.
        path = root / 'index.sqlite3'; path.rename(root / ('index-damaged-' + str(time.time_ns()) + '.sqlite3'))
        ready = False; recovered = True
    update = None
    if not ready or args.refresh:
        update = refresh(wb, root)
    with database(root) as con:
        con.execute('BEGIN')
        source_token = meta(con, 'tokens')[args.scope]
        identity = meta(con, 'identity'); users = meta(con, 'users', [])
        requested = args.user
        person = identity['actor']
        local_alias = args.scope != 'mesh' and requested == identity['local']
        if requested and requested not in {'me', identity['actor']} and not local_alias:
            from .knowledge import select_user
            accounts = con.execute("SELECT DISTINCT author FROM threads WHERE repo!='local' AND author=?", (requested,)).fetchall()
            person = requested if accounts else select_user(users, requested)['account']
        mine = person == identity['actor']
        if not mine and args.scope != 'mesh':
            raise Error('PRIVATE_REFERENCE', 'Other users have Mesh-only status pages; use --scope mesh.')
        selected_user = (identity['local'], identity['actor']) if mine else person
        page_user = next((u['name'] for u in users if u['account'] == person and u['name']), person)
        if args.id:
            result, body, key = target_page(con, args, args.id, args.level)
        else:
            rows = select_rows(con, args.scope, selected_user, args.all, args.query, args.limit, args.offset)
            # Count uses the same predicates without loading the source workspace.
            total = select_rows(con, args.scope, selected_user, args.all, args.query, 0, 0, count=True)
            cards = [personal_card(r, args.scope) for r in rows]
            result = {'target': 'user', 'user': person, 'name': page_user, 'threads': cards,
                      'total': total, 'truncated': total > args.offset + len(cards)}
            if args.level == 'status':
                result['overview'] = {'coverage': 'selected_page', 'shown': len(cards),
                                      'results': sum(c['counts']['results'] for c in cards),
                                      'open_requests': sum(c['counts']['open_requests'] for c in cards)}
            if args.level == 'index':
                result['index'] = [{'thread': c['id'], 'source': c['source'],
                                    'status': 'thread page ' + c['id'] + ' --level status',
                                    'details': 'thread page ' + c['id'] + ' --level index'} for c in cards]
            key = args.scope + '/user-' + person + '/' + args.level
            body = md_user(page_user, cards, args.level, args.scope != 'mesh')
        if getattr(args, 'continue_id', None):
            result['continuation'], more, _ = target_page(con, args, args.continue_id, 'status')
            if args.markdown: body += '\n' + more
        is_ready = getattr(args, 'ready', False)
        if is_ready:
            saved = con.execute('SELECT body FROM pages WHERE key=?', (key,)).fetchone()
            if saved and saved['body'] == body: body = saved['body']
            if 'continuation' in result:
                focus = result['continuation']; included = any(c['source'] == focus['thread']['source'] for c in result.get('threads', []))
                part_key = args.scope + '/' + focus['thread']['id'] + ('/followup' if included else '/status')
                part = con.execute('SELECT body FROM pages WHERE key=?', (part_key,)).fetchone()
                if part: more = part['body']
                elif included:
                    from .status_receipts import continuation_document
                    more = continuation_document(focus['thread'], focus['status'])
                body += '\n' + more
        # MD is useful to humans. JSON consumers get the same facts without a
        # duplicate Markdown body; explicitly request --markdown for that body.
        if args.markdown: result['markdown'] = body
        cached_page = con.execute('SELECT version,body FROM pages WHERE key=?', (key,)).fetchone()
        materialized = bool(cached_page and cached_page['body'] == body)
        if materialized: body = cached_page['body']
        sync = meta(con, 'sync')
        sync = {k: sync[k] for k in ('state', 'identity_verified', 'last_sync')}
        result.update(format='status.page.v1', level=args.level, scope=args.scope, private=args.scope != 'mesh',
                      offset=args.offset, cache={'hit': ready and not args.refresh, 'recovered': recovered,
                      'generated_at': meta(con, 'generated_at'), 'source_token': source_token,
                      'page_version': hashlib.sha256(body.encode()).hexdigest(),
                      'materialized': materialized, 'local_sources_current': source_token == token[args.scope],
                      'remote_checked_at': (sync.get('last_sync') or {}).get('at'),
                      'update': update},
                      read_hint='overall 是工作整体介绍；进展为原文摘录。按 details 索引读取固定版本原文，实际执行前核验控制状态与定位。')
        if args.scope != 'mesh': result['cache']['directory'] = str(root)
        else: result['cache'].pop('update', None)
        args._thread_sync_state = sync
        if is_ready:
            from .status_receipts import ready_receipt
            focus = result.get('continuation', result if 'status' in result else None)
            preview = None
            if focus:
                tid = focus['thread']['id']
                records = [json.loads(row[0]) for row in con.execute(
                    "SELECT data FROM entries WHERE thread=? AND kind!='naming' ORDER BY created DESC,id LIMIT 8", (tid,))]
                preview = {'total': focus['status']['details']['total']-focus['status']['details']['by_kind'].get('naming',0), 'records': records}
            result = ready_receipt(result, body, preview, identity.get('timezone', 'UTC'))
        return result, None


def schedule(wb):
    """Best effort after successful mutations; never change the write receipt."""
    try:
        root = cache_root(wb, create=False)
        if not (root / 'index.sqlite3').is_file(): return
        atomic(root / 'queued.json', encoded({'at': now()}))
        if os.environ.get('COSMESH_PAGE_BACKGROUND') == '0': return
        with (root / 'worker.lock').open('a+') as lock:
            try: fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return
        code = 'import sys;sys.path.insert(0,%r);from cosmesh.status_pages import worker;worker(%r)' % (
            str(Path(__file__).resolve().parent.parent), str(wb.base.root))
        child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, close_fds=True)
        from threading import Thread
        Thread(target=child.wait, daemon=True).start()
    except (Error, OSError):
        pass


def worker(path):
    wb = Workbench(path);root = cache_root(wb)
    with (root / 'worker.lock').open('a+') as lock:
        try: fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        # This supervisor has no source lock and puts a hard bound on each child.
        code = 'import sys;sys.path.insert(0,%r);from cosmesh.status_pages import refresh,cache_root;from cosmesh.threads import Workbench;w=Workbench(%r);refresh(w,cache_root(w))' % (
            str(Path(__file__).resolve().parent.parent), str(wb.base.root))
        deadline = time.monotonic() + 45
        try:
            for _ in range(3):
                time.sleep(.15)
                queued = (root / 'queued.json').read_bytes()
                subprocess.run([sys.executable, '-c', code], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=max(.1, deadline - time.monotonic()), check=True)
                atomic(root / 'applied.json', queued.decode())
                if (root / 'queued.json').read_bytes() == queued: return
                if time.monotonic() >= deadline: return
        except (OSError, subprocess.SubprocessError):
            atomic(root / 'worker-error.json', encoded({'at': now(), 'code': 'PAGE_UPDATE_PENDING'}))

