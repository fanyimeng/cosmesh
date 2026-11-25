"""A delivery owns its selected bytes. Filing is private per participant."""

import json
import sqlite3
from contextlib import closing
from uuid import uuid4

from . import delivery_workspace as workspace
from .errors import Error
from .personal_protocol import HASH, check_id, content_reason, digest, integer, packed, private_dir
from .personal_relay import Relay
from .store import safe_path
from .thread_protocol import identifier
from .threads import now
from .transfer_policy import PUBLIC

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries(
 seq INTEGER PRIMARY KEY, id TEXT UNIQUE, sender TEXT, origin TEXT, title TEXT,
 body TEXT, files TEXT, sources TEXT, parent TEXT, at TEXT, key TEXT, fingerprint TEXT,
 bytes INTEGER, revoked INTEGER DEFAULT 0, UNIQUE(sender,key));
CREATE TABLE IF NOT EXISTS mailbox(
 person TEXT, seq INTEGER, incoming INTEGER, work TEXT, filing TEXT, seen INTEGER DEFAULT 0,
 PRIMARY KEY(person,seq));
CREATE INDEX IF NOT EXISTS mailbox_delivery ON mailbox(seq,person);
CREATE INDEX IF NOT EXISTS mailbox_work ON mailbox(person,work,seq);
CREATE INDEX IF NOT EXISTS mailbox_folder ON mailbox(person,incoming,seq);
CREATE INDEX IF NOT EXISTS delivery_usage ON deliveries(sender,origin);
"""


class Deliveries:
    def __init__(self, hub, account):
        self.hub, self.account = hub, account
        self.community = hub.community()
        if not self.community:
            raise Error("FEATURE_UNAVAILABLE", "Enable the Hub member directory first.")
        self.community.check(account)

    def dispatch(self, payload):
        directory = private_dir(self.hub.root / "deliveries-v1")
        path = safe_path(directory / "mail.sqlite3")
        # The Hub lock also serializes gateways on a shared home.
        with self.hub.locked("deliveries-v1"), closing(sqlite3.connect(str(path))) as db:
            path.chmod(0o600)
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA + workspace.SCHEMA)
            with db:
                return self.run(db, payload, self.enable_search(db))

    @staticmethod
    def enable_search(db):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_text'").fetchone():
            return True
        try:
            db.execute("CREATE VIRTUAL TABLE delivery_text USING fts5(title,body,tokenize='trigram')")
        except sqlite3.OperationalError:
            return False
        db.execute(
            "INSERT INTO delivery_text(rowid,title,body) SELECT seq,title,body FROM deliveries WHERE revoked=0"
        )
        return True

    def member(self, value):
        with self.community.db() as db:
            person = self.community.resolve_user(db, value)
        if person["status"] != "active":
            raise Error("USER_DISABLED", "The recipient account is inactive.")
        return person["account"]

    def entry(self, db, mid):
        row = db.execute(
            "SELECT d.*,m.work,m.filing,m.seen,m.incoming FROM mailbox m JOIN deliveries d USING(seq) "
            "WHERE m.person=? AND d.id=? AND d.revoked=0",
            (self.account, mid),
        ).fetchone()
        if row is None:
            raise Error("NOT_FOUND", "Delivery unavailable to this account.")
        return row

    def present(self, db, row, detail=False):
        result = {k: row[k] for k in ("id", "seq", "sender", "title", "at", "parent", "work", "filing")}
        result.update(
            excerpt=row["body"][:400],
            unread=bool(row["incoming"] and not row["seen"]),
            recipients=[self.account]
            if row["incoming"]
            else [
                r[0]
                for r in db.execute(
                    "SELECT person FROM mailbox WHERE seq=? AND incoming=1", (row["seq"],)
                )
            ],
        )
        access = workspace.link(db, row["seq"], self.account)
        if access:
            result["workspace"] = {"thread": access["work"], "revision": access["revision"]}
        if detail:
            result.update(
                body=row["body"], files=json.loads(row["files"]), sources=json.loads(row["sources"])
            )
        return result

    def send(self, db, p, indexed):
        self.community.check(self.account, write=True)
        title, body, files, targets, key = (p.get(k) for k in ("title", "body", "files", "to", "key"))
        if (
            not isinstance(title, str)
            or len(title) > 300
            or not isinstance(body, str)
            or len(body.encode()) > 192000
            or not isinstance(files, list)
            or len(files) > 64
            or not (body.strip() or files)
            or not isinstance(targets, list)
            or not 1 <= len(targets) <= 100
            or not all(isinstance(t, str) and 0 < len(t) <= 300 for t in targets)
            or not isinstance(key, str)
            or not 1 <= len(key) <= 512
        ):
            raise Error("INPUT_INVALID", "Choose recipients and bounded text or selected attachments.")
        origin = identifier(p["work"], "W") if p.get("work") else None
        sources = p.get("sources", [])
        if not isinstance(sources, list) or len(sources) > 100:
            raise Error("INPUT_INVALID", "Select up to 100 source records.")
        for source in sources:
            if not isinstance(source, dict) or set(source) != {"record", "version"}:
                raise Error("INPUT_INVALID", "Invalid fixed source.")
            identifier(source["record"], "E")
            check_id(source["version"], HASH)
        signature = [origin, title, body, files, sources, targets, p.get("parent")]
        sharing = p["method"] == "delivery_workspace_send"
        if sharing:
            if not origin or type(p.get("grant")) is not bool:
                raise Error("INPUT_INVALID", "Choose a source work and an explicit workspace grant decision.")
            signature.extend([workspace.PROTOCOL, check_id(p.get("snapshot"), HASH), p["grant"], p.get("allow_new")])
        fingerprint = digest(packed(signature).encode())
        old = db.execute(
            "SELECT seq,id,fingerprint,revoked FROM deliveries WHERE sender=? AND key=?",
            (self.account, key),
        ).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise Error("IDEMPOTENCY_CONFLICT", "This key identifies a different delivery.")
            receipt = {
                "id": old["id"],
                "accepted": True,
                "replayed": True,
                "revoked": bool(old["revoked"]),
            }
            if sharing:
                saved = workspace.link(db, old["seq"], self.account)
                active = db.execute("SELECT g.person FROM workspace_grants g JOIN mailbox m ON m.person=g.person "
                                    "WHERE g.owner=? AND g.work=? AND g.since_seq<=m.seq AND m.seq=? AND m.incoming=1 "
                                    "ORDER BY g.person", (self.account, origin, old["seq"])).fetchall()
                if active and not old["revoked"]:
                    receipt["workspace"] = {"thread": saved["work"], "revision": saved["revision"],
                                            "active_recipients": [r[0] for r in active]}
            return receipt
        recipients = sorted({self.member(t) for t in targets})
        if self.account in recipients:
            raise Error("INPUT_INVALID", "Save your own note with thread note ID --body TEXT.")
        parent = self.entry(db, p["parent"]) if p.get("parent") else None
        if parent:
            members = (
                {parent["sender"]}
                if parent["incoming"]
                else {
                    r[0] for r in db.execute("SELECT person FROM mailbox WHERE seq=?", (parent["seq"],))
                }
            )
            if not set(recipients) <= members:
                raise Error("AUTH_DENIED", "Reply recipients must belong to the conversation.")
        names = set()
        total = 0
        for f in files:
            if not isinstance(f, dict) or set(f) != {"name", "sha", "size"}:
                raise Error("INPUT_INVALID", "Invalid attachment descriptor.")
            from .thread_files import portable_path

            name = portable_path(f["name"])
            if "/" in name or name.casefold() in names:
                raise Error("INPUT_INVALID", "Choose distinct attachment filenames.")
            names.add(name.casefold())
            sha = check_id(f["sha"], HASH)
            size = integer(f["size"], PUBLIC["file"])
            relay = Relay(self.hub, self.account)
            path = safe_path(relay.blobs / sha)
            if not path.is_file() or path.stat().st_size != size or content_reason(path, name):
                raise Error("TRANSFER_FILTERED", "Attachment unavailable or filtered.")
            total += size
        used = db.execute(
            "SELECT coalesce(sum(bytes),0) FROM deliveries WHERE sender=? AND origin IS ?",
            (self.account, origin),
        ).fetchone()[0]
        size = total + len(body.encode()) + len(title.encode())
        if total > PUBLIC["batch"] or size + used + workspace.usage(db, self.account, origin) > PUBLIC["retained"]:
            raise Error("STORAGE_QUOTA", "Delivery exceeds the sharing quota.")
        mid = "M-" + uuid4().hex
        seq = db.execute(
            "INSERT INTO deliveries(id,sender,origin,title,body,files,sources,parent,at,key,fingerprint,bytes) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mid,
                self.account,
                origin,
                title,
                body,
                packed(files),
                packed(sources),
                p.get("parent"),
                now(),
                key,
                fingerprint,
                size,
            ),
        ).lastrowid
        if indexed:
            db.execute("INSERT INTO delivery_text(rowid,title,body) VALUES(?,?,?)", (seq, title, body))
        for account in [self.account, *recipients]:
            incoming = account != self.account
            work = origin if not incoming else None
            if parent and not work:
                work = db.execute(
                    "SELECT work FROM mailbox WHERE person=? AND seq=?", (account, parent["seq"])
                ).fetchone()[0]
            db.execute(
                "INSERT INTO mailbox VALUES(?,?,?,?,?,?)",
                (account, seq, incoming, work, "linked" if work else "pending", not incoming),
            )
        receipt = {"id": mid, "accepted": True, "replayed": False}
        if sharing:
            receipt["workspace"] = workspace.attach(self, db, p, seq, recipients)
        return receipt

    def run(self, db, p, indexed):
        method = p["method"]
        if method.startswith("delivery_workspace_"):
            workspace.validate_request(p)
        if method in ("delivery_send", "delivery_workspace_send"):
            return self.send(db, p, indexed)
        if method.startswith("delivery_workspace_"):
            return workspace.dispatch(self, db, p)
        if method == "delivery_mark_many":
            ids=p.get("ids")
            if not isinstance(ids,list) or not 1<=len(ids)<=100 or not all(isinstance(mid,str) for mid in ids) or type(p.get("seen")) is not bool:
                raise Error("INPUT_INVALID","Mark 1–100 deliveries with an explicit read state.")
            # All entries must belong to the caller before changing any read marks.
            for mid in ids:
                self.entry(db,mid)
            db.executemany("UPDATE mailbox SET seen=? WHERE person=? AND seq=(SELECT seq FROM deliveries WHERE id=?)",
                           ((p["seen"],self.account,mid) for mid in ids))
            return {"results":[{"id":mid,"accepted":True} for mid in ids]}
        if method == "delivery_list":
            folder = p.get("folder")
            clauses = ["m.person=?", "d.revoked=0"]
            values = [self.account]
            if folder in ("inbox", "outbox"):
                clauses.append("m.incoming=?")
                values.append(int(folder == "inbox"))
            elif folder != "all":
                raise Error("INPUT_INVALID", "Invalid mailbox folder.")
            if p.get("peer"):
                clauses.append(
                    "((m.incoming=1 AND d.sender=?) OR (m.incoming=0 AND EXISTS("
                    "SELECT 1 FROM mailbox peer WHERE peer.seq=m.seq AND peer.incoming=1 AND peer.person=?)))"
                )
                peer = self.member(p["peer"])
                values.extend((peer, peer))
            if p.get("work"):
                clauses.append("m.work=?")
                values.append(identifier(p["work"], "W"))
            if p.get("before"):
                clauses.append("m.seq<?")
                values.append(integer(p["before"], 2**63 - 1, 1))
            if p.get("unread"):
                clauses.append("m.incoming=1 AND m.seen=0")
            if p.get("query"):
                query = p["query"]
                if not isinstance(query, str) or not query.strip() or len(query) > 200:
                    raise Error("INPUT_INVALID", "Use 1–200 characters of search text.")
                if indexed and len(query) >= 3:
                    clauses.append(
                        "d.seq IN (SELECT rowid FROM delivery_text WHERE delivery_text MATCH ?)"
                    )
                    values.append('"' + query.replace('"', '""') + '"')
                else:
                    clauses.append("instr(lower(d.title || char(10) || d.body),lower(?))>0")
                    values.append(query)
            limit = integer(p.get("limit", 20), 100, 1)
            rows = db.execute(
                "SELECT d.seq,d.id,d.sender,d.title,substr(d.body,1,400) AS body,d.at,d.parent,"
                "m.work,m.filing,m.seen,m.incoming FROM mailbox m JOIN deliveries d USING(seq) WHERE "
                + " AND ".join(clauses)
                + " ORDER BY m.seq DESC LIMIT ?",
                (*values, limit + 1),
            ).fetchall()
            return {
                "messages": [self.present(db, r) for r in rows[:limit]],
                "next_cursor": rows[limit - 1]["seq"] if len(rows) > limit else None,
                "server_current": True,
            }
        row = self.entry(db, p.get("id"))
        if method == "delivery_show":
            return self.present(db, row, True)
        if method == "delivery_file":
            f = next((x for x in json.loads(row["files"]) if x["name"] == p.get("name")), None)
            if not f:
                raise Error("NOT_FOUND", "Attachment unavailable.")
            return Relay(self.hub, row["sender"]).dispatch(
                {"method": "personal_get", "sha": f["sha"], "offset": p["offset"]}
            )
        if method == "delivery_file_into":
            work = identifier(p["work"], "W") if p.get("work") else None
            db.execute(
                "UPDATE mailbox SET work=?,filing=? WHERE person=? AND seq=?",
                (work, "linked" if work else "deferred", self.account, row["seq"]),
            )
        elif method == "delivery_mark":
            db.execute(
                "UPDATE mailbox SET seen=? WHERE person=? AND seq=?",
                (bool(p["seen"]), self.account, row["seq"]),
            )
        elif method == "delivery_revoke" and row["sender"] == self.account:
            db.execute("UPDATE deliveries SET revoked=1 WHERE seq=?", (row["seq"],))
            if indexed:
                db.execute("DELETE FROM delivery_text WHERE rowid=?", (row["seq"],))
        else:
            raise Error("COMMAND_DENIED", "Unsupported delivery operation.")
        return {"id": row["id"], "accepted": True}
