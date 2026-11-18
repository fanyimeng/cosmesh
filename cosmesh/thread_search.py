"""Rebuildable text index with per-record updates and authorization before search."""
import sqlite3
from contextlib import contextmanager
from .errors import Error
from .personal_protocol import private_dir
from .protocol import decode
from .thread_protocol import content_version


class Index:
    def __init__(self,hub):self.path=private_dir(hub.root/'thread-search-v1')/'index.sqlite3'

    @contextmanager
    def open(self):
        db=sqlite3.connect(str(self.path));db.row_factory=sqlite3.Row;self.path.chmod(0o600)
        db.executescript('''CREATE TABLE IF NOT EXISTS heads(scope TEXT PRIMARY KEY,revision TEXT);
          CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,scope TEXT,thread TEXT,record TEXT,version TEXT,title TEXT,body TEXT,UNIQUE(scope,record));
          CREATE INDEX IF NOT EXISTS record_scope ON records(scope);''')
        existed=db.execute("SELECT 1 FROM sqlite_master WHERE name='text_index'").fetchone()
        try:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS text_index USING fts5(title,body,tokenize='trigram')")
            fts=True
            if not existed:db.execute('INSERT INTO text_index(rowid,title,body) SELECT id,title,body FROM records')
        except sqlite3.OperationalError:fts=False
        try:
            with db:yield db,fts
        finally:db.close()

    def refresh(self,db,fts,scope,tid,revision,documents):
        old=db.execute('SELECT revision FROM heads WHERE scope=?',(scope,)).fetchone()
        if old and old['revision']==revision:return 0
        changed=0;present=set()
        for doc in documents():
            rid=doc['meta']['id'];version=content_version(doc);present.add(rid)
            found=db.execute('SELECT id,version FROM records WHERE scope=? AND record=?',(scope,rid)).fetchone()
            if found and found['version']==version:continue
            if found:
                if fts:db.execute('DELETE FROM text_index WHERE rowid=?',(found['id'],))
                db.execute('DELETE FROM records WHERE id=?',(found['id'],))
            cur=db.execute('INSERT INTO records(scope,thread,record,version,title,body) VALUES(?,?,?,?,?,?)',
                           (scope,tid,rid,version,doc['meta']['title'],doc['body']))
            if fts:db.execute('INSERT INTO text_index(rowid,title,body) VALUES(?,?,?)',(cur.lastrowid,doc['meta']['title'],doc['body']))
            changed+=1
        for row in list(db.execute('SELECT id,record FROM records WHERE scope=?',(scope,))):
            if row['record'] not in present:
                if fts:db.execute('DELETE FROM text_index WHERE rowid=?',(row['id'],))
                db.execute('DELETE FROM records WHERE id=?',(row['id'],));changed+=1
        db.execute('INSERT OR REPLACE INTO heads VALUES(?,?)',(scope,revision))
        return changed

    def search(self,db,fts,query,allowed,offset,limit):
        if not isinstance(query,str) or not query.strip() or len(query)>200:raise Error('INPUT_INVALID','Use 1–200 characters of search text.')
        if type(offset)is not int or offset<0 or type(limit)is not int or not 1<=limit<=100:raise Error('INPUT_INVALID','Use bounded search pagination.')
        # The temporary allow-list gates both FTS hits and short-query fallback.
        db.execute('CREATE TEMP TABLE permitted(scope TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO permitted VALUES(?)',((s,) for s in allowed))
        if fts and len(query)>=3:
            sql='SELECT r.* FROM text_index f JOIN records r ON r.id=f.rowid JOIN permitted p ON p.scope=r.scope WHERE text_index MATCH ?'
            params=['"'+query.replace('"','""')+'"']
        else:
            sql='SELECT r.* FROM records r JOIN permitted p ON p.scope=r.scope WHERE instr(lower(r.title),lower(?)) OR instr(lower(r.body),lower(?))'
            params=[query,query]
        rows=list(db.execute(sql+' ORDER BY r.scope,r.record LIMIT ? OFFSET ?',(*params,limit+1,offset)))
        hits=[]
        for row in rows[:limit]:
            text=row['body'];pos=text.casefold().find(query.casefold());start=max(0,pos-100)
            hits.append(dict(scope=row['scope'],thread=row['thread'],record=row['record'],version=row['version'],
                             title=row['title'][:300],excerpt=text[start:start+500],offset=start,revision=allowed[row['scope']]))
        return {'hits':hits,'next_offset':offset+limit if len(rows)>limit else None,'server_current':True,'marks_read':False}


def changes(access,row,p):
    """A pinned version-to-version difference; no original file transfer."""
    from .personal_protocol import integer,check_id,HASH
    check_id(p['since'],HASH)
    offset=integer(p['offset'],2**31-1);limit=integer(p['limit'],100,1)
    if offset and not p['revision']:raise Error('INPUT_INVALID','Pin the returned destination revision on subsequent change pages.')
    target=p['revision'] or row['sha']
    previous,_=access.manifest(row,p['since'])
    current,_=access.manifest(row,target)
    events=[]
    for path,text in sorted(current['documents'].items()):
        if previous['documents'].get(path)==text:continue
        doc=decode(text);events.append(dict(kind='record',action='upsert',record=doc['meta']['id'],version=content_version(doc),title=doc['meta']['title']))
    for path,text in sorted(previous['documents'].items()):
        if path not in current['documents']:events.append(dict(kind='record',action='remove',record=decode(text)['meta']['id']))
    for aid,asset in sorted(current['assets'].items()):
        old=previous['assets'].get(aid)
        # Local paths and device bindings never appear in public change receipts.
        def public(a):return {k:a.get(k) for k in ('name','size','blob','relative_path')} if a else None
        if public(old)!=public(asset):events.append(dict(kind='file',action='upsert',asset=aid,**public(asset)))
    for aid in sorted(set(previous['assets'])-set(current['assets'])):events.append(dict(kind='file',action='remove',asset=aid))
    return dict(thread=p['thread'],from_revision=p['since'],revision=target,changes=events[offset:offset+limit],
                next_offset=offset+limit if offset+limit<len(events) else None,unchanged=not events,acl_revision=row['acl_revision'],read_only=True)
