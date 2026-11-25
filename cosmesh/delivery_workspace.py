"""Named, read-only workspaces anchored to a participant's delivery.

The sender's personal relay proves snapshot/Thread membership. Only work
documents and registered files enter this projection. An immutable revision
is shared among readers and mail transactions serialize publication and ACLs.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import PurePosixPath

from .errors import Error
from .personal_protocol import HASH, MAX_METADATA, check_id, digest, integer, packed
from .personal_relay import Relay
from .protocol import decode
from .store import safe_path
from .thread_files import portable_path
from .thread_protocol import identifier
from .transfer_policy import PUBLIC, content_reason

PROTOCOL = 'workspace.v1'
SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_heads(
 owner TEXT,work TEXT,source TEXT,revision TEXT,PRIMARY KEY(owner,work));
CREATE TABLE IF NOT EXISTS workspace_grants(
 owner TEXT,work TEXT,person TEXT,since_seq INTEGER,since_event INTEGER,PRIMARY KEY(owner,work,person));
CREATE TABLE IF NOT EXISTS workspace_exposures(
 event INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT,work TEXT,revision TEXT,UNIQUE(owner,work,revision));
CREATE TABLE IF NOT EXISTS workspace_links(
 seq INTEGER PRIMARY KEY,owner TEXT,work TEXT,revision TEXT);
CREATE TABLE IF NOT EXISTS workspace_versions(
 id INTEGER PRIMARY KEY,owner TEXT,work TEXT,revision TEXT,UNIQUE(owner,work,revision));
CREATE TABLE IF NOT EXISTS workspace_documents(
 owner TEXT,work TEXT,version TEXT,document TEXT,title TEXT,author TEXT,created TEXT,
 PRIMARY KEY(owner,work,version));
CREATE TABLE IF NOT EXISTS workspace_records(
 snapshot INTEGER,record TEXT,version TEXT,current INTEGER,
 PRIMARY KEY(snapshot,record,version));
CREATE INDEX IF NOT EXISTS workspace_current_records ON workspace_records(snapshot,current,record,version);
CREATE TABLE IF NOT EXISTS workspace_files(
 snapshot INTEGER,asset TEXT,name TEXT,path TEXT,sha TEXT,size INTEGER,PRIMARY KEY(snapshot,asset));
CREATE TABLE IF NOT EXISTS workspace_usage(
 owner TEXT,work TEXT,kind TEXT,sha TEXT,size INTEGER,PRIMARY KEY(owner,work,kind,sha));
"""


READ_FIELDS = {'id', 'at_send', 'revision', 'offset'}
FIELDS = {
    'send': {'work', 'title', 'body', 'to', 'files', 'sources', 'key', 'parent', 'grant', 'allow_new', 'snapshot'},
    'publish': {'work', 'snapshot'}, 'status': {'work', 'to'}, 'revoke': {'work', 'to'},
    'open': READ_FIELDS | {'record', 'version', 'chars'},
    'records': READ_FIELDS | {'record', 'limit'}, 'files': READ_FIELDS | {'limit'},
    'file': READ_FIELDS | {'asset'},
}


def validate_request(p):
    method = p['method'][len('delivery_workspace_'):]
    if method not in FIELDS or set(p) - {'method'} - FIELDS[method]:
        raise Error('INPUT_INVALID', 'Use defined workspace fields. The Hub determines the caller identity.')


def active_count(db, owner, work):
    return db.execute('SELECT count(*) FROM workspace_grants WHERE owner=? AND work=?',
                      (owner, work)).fetchone()[0]


def usage(db, owner, work):
    return db.execute('SELECT coalesce(sum(size),0) FROM workspace_usage WHERE owner=? AND work=?',
                      (owner, work)).fetchone()[0]


def grant_accounts(deliveries, db, work, targets, active=True):
    if not isinstance(targets, list) or len(targets) > 100 or not all(
            isinstance(name, str) and 0 < len(name) <= 300 for name in targets):
        raise Error('INPUT_INVALID', 'Select at most 100 named workspace recipients.')
    if active:
        people = {deliveries.member(name) for name in targets}
    else:
        with deliveries.community.db() as members:
            people = {deliveries.community.resolve_user(members, name)['account'] for name in targets}
    current = {row[0] for row in db.execute('SELECT person FROM workspace_grants WHERE owner=? AND work=?',
                                             (deliveries.account, work))}
    return sorted(people & current), sorted(people - current)


def accepted_source(relay, work, snapshot):
    """Only hashes accepted as this owner's selected Thread can be projected."""
    path = safe_path(relay.root / 'relay.sqlite3')
    if path.is_file():
        with relay.hub.locked('personal-' + relay.owner[:32]), closing(sqlite3.connect(str(path))) as db:
            # A prior-release relay can also contain only whole-store data.
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'thread_history', 'thread_heads'} <= tables:
                row = db.execute('SELECT h.sha FROM thread_heads h JOIN thread_history v USING(thread) '
                                 'WHERE h.thread=? AND v.sha=?', (work, snapshot)).fetchone()
                if row:
                    return row[0]
    raise Error('SOURCE_PENDING', 'Synchronize this Thread in the authenticated sender account first.')


def source_files(value, relay):
    files = {}
    paths = set()
    for aid, item in value['assets'].items():
        if item.get('removed'):
            continue
        name = portable_path(item['name'])
        path = portable_path(item.get('relative_path', name))
        if '/' in name or content_reason(name, b'') or content_reason(PurePosixPath(path).name, b''):
            raise Error('TRANSFER_FILTERED', 'A registered file name is unsuitable for sharing.')
        if path.casefold() in paths:
            raise Error('PATH_INVALID', 'Choose distinct relative paths for registered files.')
        paths.add(path.casefold())
        sha, size = item['blob'], item['size']
        if not sha:
            raise Error('SOURCE_PENDING', 'A registered file has no synchronized bytes. Complete file sync first.')
        if size > PUBLIC['file']:
            raise Error('STORAGE_QUOTA', 'A registered file exceeds the external file limit.')
        blob = safe_path(relay.blobs / sha)
        if not blob.is_file() or blob.stat().st_size != size:
            raise Error('SOURCE_PENDING', 'A registered file is unavailable in the sender relay.')
        files[aid] = (name, path, sha, size)
    return files


def index_snapshot(db, relay, owner, work, snapshot):
    path = safe_path(relay.blobs / snapshot)
    if not path.is_file() or path.stat().st_size > MAX_METADATA:
        raise Error('SOURCE_PENDING', 'The accepted Thread manifest is unavailable.')
    raw = path.read_bytes()
    if digest(raw) != snapshot:
        raise Error('CHECKSUM_MISMATCH', 'The accepted Thread manifest checksum differs.')
    # Personal commit has already validated the entire immutable manifest, its
    # history keys and every asset. Never serialize its private envelope.
    value = json.loads(raw)
    if value['thread'] != work:
        raise Error('POLICY_DENIED', 'The accepted manifest belongs to a different Thread.')
    history = value['history']
    fixed = {text: key.rsplit('@', 1)[1] for key, text in history.items()}
    current = {PurePosixPath(path).stem: fixed[text] for path, text in value['documents'].items()}
    files = source_files(value, relay)
    shape = packed([work, current, sorted(history), files]).encode()
    revision = digest(shape)
    if db.execute('SELECT 1 FROM workspace_versions WHERE owner=? AND work=? AND revision=?',
                  (owner, work, revision)).fetchone():
        return revision

    file_sizes = {sha: size for _, _, sha, size in files.values()}
    document_sizes = {fixed[text]: len(text.encode()) for text in history.values()}
    if sum(row[3] for row in files.values()) + sum(document_sizes.values()) + len(shape) > PUBLIC['current']:
        raise Error('STORAGE_QUOTA', 'The synchronized work exceeds the current sharing limit.')
    before_files = {row[0] for row in db.execute(
        "SELECT sha FROM workspace_usage WHERE owner=? AND work=? AND kind='file'", (owner, work))}
    if sum(size for sha, size in file_sizes.items() if sha not in before_files) > PUBLIC['batch']:
        raise Error('STORAGE_QUOTA', 'The newly shared files exceed the external batch limit.')
    for kind, sizes in [('file', file_sizes), ('record', document_sizes), ('index', {revision: len(shape)})]:
        db.executemany('INSERT OR IGNORE INTO workspace_usage VALUES(?,?,?,?,?)',
                       ((owner, work, kind, sha, size) for sha, size in sizes.items()))
    mail = db.execute('SELECT coalesce(sum(bytes),0) FROM deliveries WHERE sender=? AND origin=?',
                      (owner, work)).fetchone()[0]
    if mail + usage(db, owner, work) > PUBLIC['retained']:
        raise Error('STORAGE_QUOTA', 'The work and its deliveries exceed the retained sharing limit.')

    number = db.execute('INSERT INTO workspace_versions(owner,work,revision) VALUES(?,?,?)',
                        (owner, work, revision)).lastrowid
    # Only membership is rebuilt for a new immutable revision. Store the body
    # and its display index once across revisions, including out-of-order pins.
    known = {r[0] for r in db.execute('SELECT version FROM workspace_documents WHERE owner=? AND work=?',
                                     (owner, work))}
    records = []
    for key, text in history.items():
        rid, version = key.rsplit('@', 1)
        if version not in known:
            meta = decode(text)['meta']
            db.execute('INSERT INTO workspace_documents VALUES(?,?,?,?,?,?,?)',
                       (owner, work, version, text, meta['title'][:300], meta['author'], meta['created_at']))
        records.append((number, rid, version, int(current.get(rid) == version)))
    db.executemany('INSERT INTO workspace_records VALUES(?,?,?,?)', records)
    db.executemany('INSERT INTO workspace_files VALUES(?,?,?,?,?,?)',
                   ((number, aid, *item) for aid, item in files.items()))
    return revision


def publish(deliveries, db, work, snapshot, pin=False):
    owner = deliveries.account
    check_id(snapshot, HASH)
    relay = Relay(deliveries.hub, owner)
    current_source = accepted_source(relay, work, snapshot)
    old = db.execute('SELECT source,revision FROM workspace_heads WHERE owner=? AND work=?',
                     (owner, work)).fetchone()
    if not pin and current_source != snapshot:
        raise Error('STATE_CONFLICT', 'The personal Thread advanced. Sync its latest work before publishing.')
    if old and old['source'] == snapshot:
        return old['revision']
    revision = index_snapshot(db, relay, owner, work, snapshot)
    if not old or current_source == snapshot:
        if not old or old['revision'] != revision:
            db.execute('INSERT OR REPLACE INTO workspace_exposures(owner,work,revision) VALUES(?,?,?)',
                       (owner, work, revision))
        db.execute('INSERT OR REPLACE INTO workspace_heads VALUES(?,?,?,?)', (owner, work, snapshot, revision))
    return revision


def attach(deliveries, db, p, seq, recipients):
    owner, work = deliveries.account, identifier(p.get('work'), 'W')
    current = {r[0] for r in db.execute('SELECT person FROM workspace_grants WHERE owner=? AND work=?',
                                        (owner, work))}
    missing = set(recipients) - current
    allowed = p.get('allow_new', recipients if p['grant'] else [])
    if not isinstance(allowed, list) or not all(isinstance(s, str) and s in recipients for s in allowed):
        raise Error('INPUT_INVALID', 'Confirm new grants using the canonical recipients shown in the preview.')
    if missing and (not p['grant'] or not missing <= set(allowed)):
        raise Error('AUTH_DENIED', 'A recipient needs an explicit workspace grant. Review the recipients again.')
    revision = publish(deliveries, db, work, p['snapshot'], pin=True)
    for source in p.get('sources', []):
        if not db.execute('SELECT 1 FROM workspace_versions v JOIN workspace_records r ON r.snapshot=v.id '
                          'WHERE v.owner=? AND v.work=? AND v.revision=? AND r.record=? AND r.version=?',
                          (owner, work, revision, source['record'], source['version'])).fetchone():
            raise Error('REFERENCE_INVALID', 'A selected source record does not belong to the pinned workspace.')
    start = db.execute('SELECT max(event) FROM workspace_exposures WHERE owner=? AND work=?', (owner, work)).fetchone()[0]
    db.executemany('INSERT INTO workspace_grants VALUES(?,?,?,?,?)',
                   ((owner, work, person, seq, start) for person in missing))
    db.execute('INSERT INTO workspace_links VALUES(?,?,?,?)', (seq, owner, work, revision))
    return {'thread': work, 'revision': revision}


def link(db, seq, account):
    return db.execute('SELECT l.work,l.revision,l.owner,h.revision AS latest,g.since_event FROM workspace_links l '
                      'JOIN workspace_heads h USING(owner,work) '
                      'LEFT JOIN workspace_grants g ON g.owner=l.owner AND g.work=l.work AND g.person=? '
                      'WHERE l.seq=? AND (l.owner=? OR g.since_seq<=l.seq)', (account, seq, account)).fetchone()


def read(deliveries, db, method, p):
    row = deliveries.entry(db, p.get('id'))
    info = link(db, row['seq'], deliveries.account)
    if not info:
        raise Error('NOT_FOUND', 'Workspace access is unavailable for this message.')
    at_send = p.get('at_send', False)
    if type(at_send) is not bool:
        raise Error('INPUT_INVALID', 'Select a current or sent workspace version.')
    revision = p.get('revision') or (info['revision'] if at_send else info['latest'])
    check_id(revision, HASH)
    if at_send and revision != info['revision']:
        raise Error('INPUT_INVALID', 'The requested revision differs from the sent workspace version.')
    owner, work = info['owner'], info['work']
    if deliveries.account != owner and revision not in (info['revision'], info['latest']):
        allowed = db.execute('SELECT 1 FROM workspace_exposures WHERE owner=? AND work=? AND revision=? AND event>=?',
                             (owner, work, revision, info['since_event'])).fetchone()
        if not allowed:
            raise Error('NOT_FOUND', 'Workspace version unavailable during this grant.')
    found = db.execute('SELECT id FROM workspace_versions WHERE owner=? AND work=? AND revision=?',
                       (owner, work, revision)).fetchone()
    if not found:
        raise Error('NOT_FOUND', 'Workspace version unavailable.')
    number = found[0]
    result = dict(thread=work, revision=revision, owner=owner, read_only=True)
    if method == 'file':
        asset = check_id(p.get('asset'))
        item = db.execute('SELECT name,sha FROM workspace_files WHERE snapshot=? AND asset=?',
                          (number, asset)).fetchone()
        if not item:
            raise Error('NOT_FOUND', 'File unavailable in this workspace version.')
        part = Relay(deliveries.hub, owner).dispatch(dict(method='personal_get', sha=item['sha'], offset=p.get('offset', 0)))
        return dict(result, name=item['name'], **part)
    offset = integer(p.get('offset', 0), 2**31 - 1)
    if method == 'open':
        rid = identifier(p.get('record') or work)
        version = p.get('version')
        condition = 'r.version=?' if version else 'r.current=1'
        args = [owner, work, number, rid] + ([check_id(version, HASH)] if version else [])
        item = db.execute('SELECT d.document,r.version FROM workspace_documents d JOIN workspace_records r '
                          'ON d.version=r.version WHERE d.owner=? AND d.work=? AND r.snapshot=? AND r.record=? AND '
                          + condition, args).fetchone()
        if not item:
            raise Error('NOT_FOUND', 'Record unavailable in this workspace version.')
        doc = decode(item['document'])
        meta = {k: v for k, v in doc['meta'].items() if k != 'generation'}
        chars = integer(p.get('chars', 32000), 32000, 1)
        end = offset + chars
        return dict(result, record=rid, version=item['version'], title=meta['title'], author=meta['author'],
                    metadata=meta, body=doc['body'][offset:end], next_offset=end if end < len(doc['body']) else None)
    limit = integer(p.get('limit', 50), 100, 1)
    if method == 'records':
        record = p.get('record')
        where, args = ('record=?', [identifier(record)]) if record else ('current=1', [])
        rows = db.execute('SELECT r.record AS id,d.title,d.author,r.version,d.created AS created_at FROM workspace_records r '
                          'JOIN workspace_documents d ON r.version=d.version AND d.owner=? AND d.work=? '
                          'WHERE r.snapshot=? AND ' + where + ' ORDER BY d.created,r.record,r.version LIMIT ? OFFSET ?',
                          [owner, work, number, *args, limit+1, offset]).fetchall()
    else:
        rows = db.execute("SELECT asset AS id,name,path AS relative_path,sha,size,'available' AS state "
                          'FROM workspace_files WHERE snapshot=? ORDER BY asset LIMIT ? OFFSET ?',
                          (number, limit+1, offset)).fetchall()
    result[method] = [dict(r) for r in rows[:limit]]
    result['next_offset'] = offset + limit if len(rows) > limit else None
    return result


def dispatch(deliveries, db, p):
    method = p['method'][len('delivery_workspace_'):]
    if method in ('open', 'records', 'files', 'file'):
        return read(deliveries, db, method, p)
    work = identifier(p.get('work'), 'W')
    owner = deliveries.account
    if method == 'status':
        granted, missing = grant_accounts(deliveries, db, work, p.get('to', []))
        head = db.execute('SELECT revision FROM workspace_heads WHERE owner=? AND work=?', (owner, work)).fetchone()
        return dict(thread=work, granted=granted, missing=missing, revision=head[0] if head else None,
                    active_count=active_count(db, owner, work))
    deliveries.community.check(owner, write=True)
    if method == 'revoke':
        granted, _ = grant_accounts(deliveries, db, work, p.get('to'), active=False)
        db.executemany('DELETE FROM workspace_grants WHERE owner=? AND work=? AND person=?',
                       ((owner, work, person) for person in granted))
        return dict(thread=work, revoked=granted, active_count=active_count(db, owner, work))
    if method == 'publish':
        count = active_count(db, owner, work)
        revision = publish(deliveries, db, work, p.get('snapshot')) if count else None
        return dict(thread=work, revision=revision, accepted=True, active_count=count)
    raise Error('COMMAND_DENIED', 'Unsupported workspace operation.')
