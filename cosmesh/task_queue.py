"""Durable assignments inside the authenticated owner's personal relay transaction."""
import json
from uuid import uuid4

from .errors import Error
from .personal_protocol import packed
from .thread_execution_model import normalize_agent
from .thread_protocol import identifier

ENDPOINT = ('device', 'host', 'agent', 'session')
ROUTE = {'thread', *ENDPOINT}
METHODS = {'targets': {'thread'}, 'list': {'thread'},
           'send': {'thread', 'device', 'agent', 'session', 'key', 'body'},
           'next': ROUTE | {'replica'}, 'claim': ROUTE | {'replica', 'id', 'cursor'},
           'uncertain': ROUTE | {'replica', 'id'},
           'report': ROUTE | {'replica', 'id', 'outcome', 'result'},
           'finish': ROUTE | {'replica', 'id', 'record'}}
FIELDS = {'personal_task_' + name: fields | {'method'} for name, fields in METHODS.items()}
MAX_BODY = 32000
MAX_RESULT = 64000


def string(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit or '\0' in value:
        raise Error('INPUT_INVALID', 'Invalid or oversized task field.')
    return value


def targets(relay, head):
    snapshot = json.loads((relay.blobs / head).read_bytes())
    rows = set()
    for row in snapshot['bindings'].values():
        b = row['binding']
        if row['kind'] == 'bindings' and isinstance(b.get('session_id'), str) and b['session_id']:
            agent = normalize_agent(b['agent'])
            if agent in ('dsh', 'codex'):
                rows.add((row['device'], b['host'], agent, b['session_id']))
    return [dict(zip(ENDPOINT, r)) for r in sorted(rows)]


def receipt(row, detail=False):
    keys = ('id', 'thread', *ENDPOINT, 'status', 'record')
    if detail:
        keys += ('body', 'cursor', 'outcome', 'result')
    return {k: row[k] for k in keys}


def dispatch(relay, db, method, p):
    tid = identifier(p['thread'], 'W')
    head = db.execute('SELECT sha FROM thread_heads WHERE thread=?', (tid,)).fetchone()
    if not head:
        raise Error('NOT_FOUND', 'Choose a Thread in your personal sync space.')
    db.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, thread TEXT NOT NULL, operation TEXT NOT NULL, selection TEXT NOT NULL,
        device TEXT NOT NULL, host TEXT NOT NULL, agent TEXT NOT NULL, session TEXT NOT NULL,
        body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', replica TEXT,
        cursor INTEGER, outcome TEXT, result TEXT, record TEXT, UNIQUE(thread,operation))''')
    db.execute('CREATE INDEX IF NOT EXISTS task_route ON tasks(device,host,agent,session,status,thread)')
    if method == 'targets':
        return {'targets': targets(relay, head['sha'])}
    if method == 'list':
        rows = db.execute('SELECT * FROM tasks WHERE thread=? ORDER BY rowid DESC LIMIT 100', (tid,))
        return {'tasks': [receipt(row) for row in rows]}
    if method == 'send':
        key, body = string(p['key'], 200), string(p['body'], MAX_BODY)
        device, agent = string(p['device'], 255), string(p['agent'], 40)
        session = None if p['session'] is None else string(p['session'], 512)
        selection = packed([device, agent, session])
        old = db.execute('SELECT * FROM tasks WHERE thread=? AND operation=?', (tid, key)).fetchone()
        if old:
            if (old['body'], old['selection']) != (body, selection):
                raise Error('STATE_CONFLICT', 'This operation key belongs to another assignment.')
            return receipt(old)
        found = [r for r in targets(relay, head['sha']) if device.casefold() in
                 (r['host'].casefold(), r['device'].casefold()) and r['agent'] == agent
                 and (session is None or session == r['session'])]
        if len(found) != 1:
            raise Error('TARGET_AMBIGUOUS' if found else 'TARGET_NOT_FOUND',
                        'Select one registered session using thread task targets.')
        task_id = str(uuid4())
        db.execute('INSERT INTO tasks(id,thread,operation,selection,device,host,agent,session,body) VALUES(?,?,?,?,?,?,?,?,?)',
                   (task_id, tid, key, selection, *(found[0][k] for k in ENDPOINT), body))
        return receipt(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    endpoint = tuple(string(p[k], 512) for k in ENDPOINT)
    replica = string(p['replica'], 128)
    scope = 'device=? AND host=? AND agent=? AND session=?'
    # One native Session can be registered in more than one Thread or work store.
    current = db.execute('SELECT * FROM tasks WHERE ' + scope + " AND status IN ('submitted','unconfirmed','reporting')", endpoint).fetchone()
    if current and (current['thread'] != tid or current['replica'] != replica):
        return {'task': None, 'claimed': False} if method in ('next','claim') else denied()
    first = current or db.execute('SELECT * FROM tasks WHERE ' + scope +
                                  " AND thread=? AND status='queued' ORDER BY rowid LIMIT 1", (*endpoint, tid)).fetchone()
    if method == 'next':
        return {'task': receipt(first, True) if first else None}
    row = db.execute('SELECT * FROM tasks WHERE ' + scope + ' AND thread=? AND id=?',
                     (*endpoint, tid, string(p['id'], 64))).fetchone()
    if not row:
        return denied()
    if method == 'claim':
        if type(p['cursor']) is not int or not -1 <= p['cursor'] < 2**53:
            raise Error('INPUT_INVALID', 'Invalid native history cursor.')
        claimed = first and first['id'] == row['id'] and row['status'] == 'queued'
        if claimed:
            db.execute("UPDATE tasks SET status='submitted',replica=?,cursor=? WHERE id=?", (replica,p['cursor'],row['id']))
        return {'claimed': bool(claimed)}
    if row['replica'] != replica:
        return denied()
    if method == 'uncertain':
        if row['status'] == 'submitted':
            db.execute("UPDATE tasks SET status='unconfirmed' WHERE id=?", (row['id'],))
    elif method == 'report':
        result = string(p['result'], MAX_RESULT)
        if p['outcome'] not in ('completed','failed'):
            raise Error('INPUT_INVALID', 'Invalid task outcome.')
        if row['status'] in ('submitted','unconfirmed'):
            db.execute("UPDATE tasks SET status='reporting',result=?,outcome=? WHERE id=?", (result,p['outcome'],row['id']))
        elif (row['result'],row['outcome']) != (result,p['outcome']):
            raise Error('STATE_CONFLICT', 'A different outcome is already recorded.')
    else:
        record = identifier(p['record'], 'E')
        if row['status'] == 'reporting':
            from .protocol import decode
            manifest = json.loads((relay.blobs / head['sha']).read_bytes())
            raw = manifest['documents'].get('documents/' + record + '.md')
            doc = decode(raw) if raw else {}
            if doc.get('meta', {}).get('type') != 'result' or doc.get('body') != result_body(row):
                raise Error('SOURCE_PENDING', 'The result must be synced into its original Thread first.')
            db.execute('UPDATE tasks SET status=outcome,record=? WHERE id=?', (record,row['id']))
        elif row['record'] != record:
            raise Error('STATE_CONFLICT', 'No matching synced result.')
    return receipt(db.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())


def result_body(row):
    return f"CosMesh task {row['id']} ({row['outcome']})\n\n{row['body']}\n\n{row['result']}".rstrip()


def denied():
    raise Error('NOT_FOUND', 'Assignment unavailable for this receiver.')
