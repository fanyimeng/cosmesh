"""Shared document operations with separate personal and local-draft policies."""

import hashlib
import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import Error
from .protocol import KINDS, USER, day, encode, new_id, public, timestamp, version

UPDATE_TYPES = ("progress", "feedback", "decision", "session", "correction")
READS = {"doc.list", "doc.show", "context", "inbox", "agenda", "validate", "doc.history"}
TRANSITIONS = {"todo": {"in_progress", "cancelled"},
               "in_progress": {"blocked", "review", "cancelled"},
               "blocked": {"in_progress", "review", "cancelled"},
               "review": {"in_progress", "blocked", "done", "cancelled"},
               "done": {"in_progress"}, "cancelled": {"in_progress"}}


def timezone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise Error("INPUT_INVALID", "Unknown timezone: " + str(name))


def initialize(store, product, user, tz, dry_run=False):
    if not USER.fullmatch(user):
        raise Error("INPUT_INVALID", "Use a supported system account name or local user ID.")
    timezone(tz)
    return store.initialize({"schema_version": 1, "product": product, "user": user,
                             "timezone": tz, "mode": "local_unverified"}, dry_run)


class Service:
    def __init__(self, store, product):
        self.store, self.product = store, product

    def run(self, action, payload=None, revision=None, expect_revision=None, key=None, dry_run=False):
        payload = payload or {}
        with self.store.locked():
            head = self.store.head
            if action not in READS and revision:
                raise Error("INPUT_INVALID", "Historical snapshots are read-only.")
            self.revision, files = self.store.snapshot(revision)
            self.config = json.loads(files[".cosmesh/config.json"])
            if not isinstance(self.config, dict) or self.config.get("schema_version") != 1 or self.config.get("mode") != "local_unverified":
                raise Error("SCHEMA_UNSUPPORTED", "Unsupported store configuration.")
            if not isinstance(self.config.get("user"), str) or not USER.fullmatch(self.config["user"]):
                raise Error("SCHEMA_INVALID", "Invalid configured local user.")
            if self.config.get("product") != self.product:
                raise Error("PRODUCT_MISMATCH", "This store belongs to " + str(self.config.get("product")))
            self.user = self.store.client().get("user", self.config["user"])
            self.tz = timezone(self.config["timezone"])
            self.docs = self.store.documents(files)
            self.check_graph()
            if action in READS:
                return self.read(action, payload), self.revision
            operation = key or str(uuid4())
            if not isinstance(operation, str) or len(operation) > 200:
                raise Error("INPUT_INVALID", "Idempotency keys must be strings up to 200 characters.")
            fingerprint = hashlib.sha256(json.dumps(
                {"product": self.product, "author": self.user, "action": action,
                 "payload": payload, "expect_revision": expect_revision},
                ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            receipt_path = ".cosmesh/operations/" + hashlib.sha256((self.user + ":" + operation).encode()).hexdigest() + ".json"
            if receipt_path in files:
                receipt = json.loads(files[receipt_path])
                if receipt["fingerprint"] != fingerprint:
                    raise Error("IDEMPOTENCY_CONFLICT", "This key was used for a different request.")
                original_revision = self.store.git("log", "-1", "--format=%H", self.revision, "--", receipt_path)
                return dict(receipt["result"], replayed=True), original_revision
            if expect_revision and expect_revision != head:
                raise Error("STATE_CONFLICT", "The source revision has changed.", "Read context again and decide how to revise the request.")
            if self.product == "cosmesh" and action in {
                    "task.invite", "task.accept", "task.decline", "task.state", "task.schedule"} and not expect_revision:
                raise Error("PRECONDITION_REQUIRED", "Control actions require --expect-revision from context.")
            before = {key: encode(doc) for key, doc in self.docs.items()}
            data = self.mutate(action, payload)
            self.check_graph()
            changes = {"documents/" + key + ".md": encode(doc) for key, doc in self.docs.items()
                       if before.get(key) != encode(doc)}
            result = {"operation_id": operation, "replayed": False, "result": data,
                      "changed_ids": [path.split("/")[-1][:-3] for path in changes], "dry_run": dry_run}
            if dry_run:
                result["preview"] = [public(self.docs[key]) for key in result["changed_ids"]]
                return result, self.revision
            changes[receipt_path] = json.dumps({"fingerprint": fingerprint, "result": result}, ensure_ascii=False, indent=2) + "\n"
            return result, self.store.commit(changes, str(uuid4()))

    def get(self, doc_id, kind=None):
        if doc_id not in self.docs:
            raise Error("NOT_FOUND", "Document not found: " + str(doc_id))
        doc = self.docs[doc_id]
        if kind and doc["meta"]["kind"] != kind:
            raise Error("INPUT_INVALID", "Expected a " + kind + " document.")
        return doc

    def expect(self, doc, payload):
        if not payload.get("expect"):
            raise Error("PRECONDITION_REQUIRED", "This edit requires --expect from doc show or context.")
        if payload["expect"] != version(encode(doc)):
            raise Error("STATE_CONFLICT", "Document version has changed.", "Read the document again; preserve your proposed changes.")

    def fresh(self, kind, title, body="", **extra):
        now = timestamp(self.tz)
        doc = {"meta": {"schema_version": 1, "id": new_id(kind), "kind": kind,
                        "title": title, "author": self.user, "created_at": now,
                        "updated_at": now, "lifecycle": "active", **extra}, "body": body}
        self.docs[doc["meta"]["id"]] = doc
        return doc

    def project_for(self, project=None, default=False):
        if not project and default:
            matches = [d for d in self.docs.values() if d["meta"].get("builtin") == "personal"]
            doc = matches[0] if matches else self.fresh("project", "个人", builtin="personal")
        elif project:
            doc = self.get(project, "project")
        else:
            return None
        if doc["meta"]["lifecycle"] != "active":
            raise Error("STATE_CONFLICT", "Restore the destination project before adding new content.")
        return doc["meta"]["id"]

    def create(self, kind, p):
        if kind not in KINDS:
            raise Error("INPUT_INVALID", "Unknown document kind.")
        title, body = p.get("title"), p.get("body", "")
        title = title or (body.strip().splitlines()[0].lstrip("# ")[:100] if body.strip() else "")
        if not title:
            raise Error("INPUT_INVALID", "Provide a title or nonempty Update body.")
        extra = {"sources": p.get("sources", [])}
        if kind != "project":
            project = self.project_for(p.get("project"), default=kind == "task" and self.product == "cosbase")
            if self.product == "cosmesh" and not project:
                raise Error("INPUT_INVALID", "CosMesh drafts require --project.")
            if project:
                extra["project"] = project
        if kind == "project" and self.product == "cosmesh":
            members = p.get("members", [])
            if any(not USER.fullmatch(x) for x in members):
                raise Error("INPUT_INVALID", "Invalid project member ID.")
            extra.update(lead=self.user, members=sorted(set(members)))
        elif kind == "task":
            extra.update(next_action=p.get("next_action", ""), due=day(p.get("due")),
                         followup=day(p.get("followup")), waiting=p.get("waiting", ""))
            if self.product == "cosbase":
                extra["status"] = "open"
            else:
                project = self.get(extra["project"])["meta"]
                if self.user not in [project["lead"]] + project["members"]:
                    raise Error("AUTH_DENIED", "The configured local user is not a project member.")
                invitees = p.get("invite", [])
                if self.user in invitees or any(x not in project["members"] + [project["lead"]] for x in invitees):
                    raise Error("NEEDS_PROJECT_ACCESS", "Invitees must be different users listed in this local project.")
                extra.update(lead=self.user, members={x: "pending" for x in invitees}, initial_status="todo")
        elif kind == "update":
            update_type = p.get("type", "progress")
            if update_type not in UPDATE_TYPES:
                raise Error("INPUT_INVALID", "Control actions use task commands, not update add.")
            if not body.strip():
                raise Error("INPUT_INVALID", "Update body cannot be empty.")
            extra.update(type=update_type, needs_triage=self.product == "cosbase")
            for field in ("task", "reply_to", "supersedes", "recipients", "session"):
                if p.get(field):
                    extra[field] = p[field]
            if update_type == "correction" and not p.get("supersedes"):
                raise Error("INPUT_INVALID", "A correction requires --supersedes.")
            if self.product == "cosmesh" and p.get("task"):
                task = self.get(p["task"], "task")
                state = self.reduce(task)
                if self.user != task["meta"]["lead"] and state["participants"].get(self.user) != "accepted":
                    raise Error("AUTH_DENIED", "Only the Lead or accepted members can add task Updates.")
            elif self.product == "cosmesh":
                project = self.get(extra["project"])["meta"]
                if self.user not in [project["lead"]] + project["members"]:
                    raise Error("AUTH_DENIED", "The local user is not a project member.")
        return self.fresh(kind, title, body, **extra)

    def mutate(self, action, p):
        if action.endswith(".create") or action == "update.add":
            return public(self.create(action.split(".")[0], p))
        doc = self.get(p["id"])
        meta = doc["meta"]
        if action in {"task.invite", "task.accept", "task.decline", "task.state", "task.schedule"}:
            if self.product != "cosmesh":
                raise Error("INPUT_INVALID", "Use personal document edits in CosBase.")
            return public(self.control(action.split(".")[1], doc, p))
        self.expect(doc, p)
        if self.product == "cosmesh":
            if action != "doc.edit" or meta["kind"] == "update":
                raise Error("POLICY_DENIED", "CosMesh records use append-only Updates; personal lifecycle operations are unavailable.")
            if meta.get("lead") != self.user:
                raise Error("AUTH_DENIED", "Only the local Lead can revise this definition.")
            if set(p) - {"id", "expect", "title", "body"}:
                raise Error("POLICY_DENIED", "CosMesh dates and state require task control commands.")
        if action == "doc.edit":
            if meta["lifecycle"] != "active":
                raise Error("STATE_CONFLICT", "Restore the document before editing.")
            if meta["kind"] == "update" and any(k in p for k in ("title", "body")):
                raise Error("POLICY_DENIED", "Preserve the original record; add a correction or derived document.")
            for field in ("title", "next_action", "waiting", "needs_triage"):
                if field in p:
                    meta[field] = p[field]
            for field in ("due", "followup"):
                if field in p:
                    meta[field] = day(p[field])
            if "project" in p:
                if meta["kind"] == "project":
                    raise Error("INPUT_INVALID", "Nested projects are not part of the local format.")
                meta["project"] = self.project_for(p["project"], default=meta["kind"] == "task")
            if "body" in p:
                doc["body"] = p["body"]
        elif action in {"doc.convert", "doc.derive"}:
            if meta["lifecycle"] != "active":
                raise Error("STATE_CONFLICT", "Restore the source before converting or deriving.")
            if action == "doc.convert" and p["to"] == meta["kind"]:
                raise Error("INPUT_INVALID", "Conversion needs a different target kind.")
            target_payload = {"title": p.get("title") or meta["title"], "body": doc["body"] or "# " + meta["title"],
                              "project": meta.get("project"), "sources": [meta["id"]]}
            target = self.create(p["to"], target_payload)
            if action == "doc.convert":
                meta["lifecycle"] = "archived"
                meta.setdefault("converted_to", []).append(target["meta"]["id"])
            meta["updated_at"] = timestamp(self.tz)
            return {"source": public(doc), "target": public(target), "children_unchanged": True}
        elif action == "doc.archive":
            if meta["lifecycle"] == "trash":
                raise Error("STATE_CONFLICT", "Restore from trash before archiving.")
            meta["lifecycle"] = "archived"
        elif action == "doc.trash":
            if meta["lifecycle"] != "trash":
                meta["trashed_from"] = meta["lifecycle"]
                meta["lifecycle"] = "trash"
        elif action == "doc.restore":
            meta["lifecycle"] = meta.pop("trashed_from", "active") if meta["lifecycle"] == "trash" else "active"
        elif action in {"task.finish", "task.reopen"}:
            if meta["kind"] != "task" or meta["lifecycle"] != "active":
                raise Error("STATE_CONFLICT", "Finish or reopen an active personal Task.")
            meta["status"] = "closed" if action == "task.finish" else "open"
            meta["outcome"] = p.get("outcome", "done") if action == "task.finish" else None
            meta["outcome_reason"] = p.get("reason", "")
        else:
            raise Error("INPUT_INVALID", "Unknown operation: " + action)
        meta["updated_at"] = timestamp(self.tz)
        result = {"document": public(doc), "children_unchanged": True}
        if meta["kind"] == "project":
            result["open_child_tasks"] = [d["meta"]["id"] for d in self.docs.values()
                                           if d["meta"].get("project") == meta["id"] and d["meta"]["kind"] == "task"
                                           and d["meta"].get("status") != "closed" and d["meta"]["lifecycle"] != "trash"]
        return result

    def reduce(self, task):
        meta = task["meta"]
        updates = [d["meta"] for d in self.docs.values() if d["meta"].get("task") == meta["id"]]
        invitations = {"initial:" + meta["id"] + ":" + user: {"to": user, "status": "pending"}
                       for user in meta.get("members", {})}
        for u in updates:
            if u.get("type") == "invite":
                if u["author"] != meta["lead"]:
                    raise Error("AUTH_DENIED", "An invite was not authored by the local Lead.")
                invitations[u["id"]] = {"to": u["action"]["to"], "status": "pending"}
        for u in updates:
            if u.get("type") in {"accept", "decline"}:
                invitation = invitations.get(u["action"]["invitation"])
                if not invitation or invitation["to"] != u["author"]:
                    raise Error("AUTH_DENIED", "Invitation responses must belong to the invited local user.")
                if invitation["status"] != "pending":
                    raise Error("STATE_CONFLICT", "An invitation has multiple responses.")
                invitation["status"] = "accepted" if u["type"] == "accept" else "declined"
        participants = {}
        for invitation in invitations.values():
            user, status = invitation["to"], invitation["status"]
            rank = {"declined": 0, "pending": 1, "accepted": 2}
            if rank[status] >= rank.get(participants.get(user), -1):
                participants[user] = status
        state, previous = meta.get("initial_status", "todo"), None
        dates = {key: meta.get(key) for key in ("due", "followup")}
        previous_schedule = None
        for kind in ("status", "schedule"):
            stream = [u for u in updates if u.get("type") == kind]
            chain = {}
            for u in stream:
                if u["author"] != meta["lead"]:
                    raise Error("AUTH_DENIED", "A control Update was not authored by the local Lead.")
                predecessor = u["action"].get("previous")
                if predecessor in chain:
                    raise Error("STATE_CONFLICT", "Conflicting control actions share a predecessor.")
                chain[predecessor] = u
            cursor, seen = None, set()
            while cursor in chain:
                u = chain[cursor]
                if u["id"] in seen:
                    raise Error("STATE_CONFLICT", "Control cycle detected.")
                seen.add(u["id"])
                if kind == "status":
                    action = u["action"]
                    if action["from"] != state or action["to"] not in TRANSITIONS.get(state, set()):
                        raise Error("STATE_CONFLICT", "Invalid task status transition.")
                    state, previous = action["to"], u["id"]
                else:
                    dates.update(u["action"]["changes"])
                    previous_schedule = u["id"]
                cursor = u["id"]
            if len(seen) != len(stream):
                raise Error("STATE_CONFLICT", "A control stream is disconnected.")
        return {"status": state, "previous_status": previous, "previous_schedule": previous_schedule,
                "participants": participants, "invitations": invitations, **dates}

    def control(self, action, task, p):
        if task["meta"]["kind"] != "task":
            raise Error("INPUT_INVALID", "Control actions target Tasks.")
        meta = task["meta"]
        state = self.reduce(task)
        if action not in {"accept", "decline"} and self.user != meta["lead"]:
            raise Error("AUTH_DENIED", "This action requires the local Task Lead.")
        if action == "invite":
            project = self.get(meta["project"])["meta"]
            recipient = p["to"]
            if recipient == meta["lead"] or recipient not in project["members"] + [project["lead"]]:
                raise Error("NEEDS_PROJECT_ACCESS", "Invitees must be listed in the local project.")
            if state["participants"].get(recipient) in {"pending", "accepted"}:
                raise Error("STATE_CONFLICT", "This user already has a pending or accepted invitation.")
            event_type, data = "invite", {"to": recipient}
        elif action in {"accept", "decline"}:
            invitation = state["invitations"].get(p["invitation"])
            if not invitation or invitation["to"] != self.user:
                raise Error("AUTH_DENIED", "Only the invited local user can respond.")
            if invitation["status"] != "pending":
                raise Error("STATE_CONFLICT", "This invitation has already been answered.")
            event_type, data = action, {"invitation": p["invitation"]}
        elif action == "state":
            if p["to"] not in TRANSITIONS.get(state["status"], set()):
                raise Error("STATE_CONFLICT", "Transition not allowed from " + state["status"])
            if not p.get("reason", "").strip():
                raise Error("INPUT_INVALID", "A state action requires a reason.")
            event_type, data = "status", {"previous": state["previous_status"], "from": state["status"],
                                          "to": p["to"], "reason": p["reason"]}
        else:
            changes = {key: day(p[key]) for key in ("due", "followup") if key in p}
            if not changes:
                raise Error("INPUT_INVALID", "Provide a date change or an explicit clear flag.")
            event_type, data = "schedule", {"previous": state["previous_schedule"], "changes": changes}
        return self.fresh("update", action + ": " + meta["title"], p.get("reason", ""),
                          project=meta["project"], task=meta["id"], type=event_type, action=data)

    def check_graph(self):
        for doc in self.docs.values():
            meta = doc["meta"]
            if self.product == "cosbase" and meta["kind"] == "task" and meta.get("status") not in {"open", "closed"}:
                raise Error("SCHEMA_INVALID", "Invalid personal task status.")
            if self.product == "cosmesh":
                if meta["kind"] != "project" and not meta.get("project"):
                    raise Error("SCHEMA_INVALID", "CosMesh drafts require a project.")
                if meta["kind"] in {"project", "task"}:
                    if not isinstance(meta.get("lead"), str) or not USER.fullmatch(meta["lead"]):
                        raise Error("SCHEMA_INVALID", "Invalid Lead ID.")
                    members = meta.get("members")
                    if meta["kind"] == "project":
                        valid_members = isinstance(members, list) and all(isinstance(x, str) and USER.fullmatch(x) for x in members)
                    else:
                        valid_members = isinstance(members, dict) and all(USER.fullmatch(x) and v == "pending" for x, v in members.items())
                    if not valid_members:
                        raise Error("SCHEMA_INVALID", "Invalid initial member definitions.")
                if meta["kind"] == "task" and meta.get("initial_status") not in TRANSITIONS:
                    raise Error("SCHEMA_INVALID", "Invalid initial task status.")
                if meta["kind"] == "update":
                    kind = meta.get("type")
                    if kind not in UPDATE_TYPES + ("invite", "accept", "decline", "status", "schedule"):
                        raise Error("SCHEMA_INVALID", "Unknown Update type.")
                    if kind in {"invite", "accept", "decline", "status", "schedule"}:
                        action = meta.get("action")
                        required = {"invite": {"to"}, "accept": {"invitation"}, "decline": {"invitation"},
                                    "status": {"previous", "from", "to", "reason"}, "schedule": {"previous", "changes"}}[kind]
                        if not meta.get("task") or not isinstance(action, dict) or not required <= action.keys():
                            raise Error("SCHEMA_INVALID", "Malformed control Update.")
                        if kind == "schedule":
                            if not isinstance(action["changes"], dict) or set(action["changes"]) - {"due", "followup"}:
                                raise Error("SCHEMA_INVALID", "Invalid schedule change fields.")
                            for value in action["changes"].values():
                                day(value)
            for field in ("sources", "converted_to"):
                for reference in meta.get(field, []):
                    if reference.startswith(("P-", "T-", "U-")):
                        self.get(reference)
            for field, kind in (("project", "project"), ("task", "task"), ("reply_to", "update"), ("supersedes", "update")):
                if meta.get(field):
                    target = self.get(meta[field], kind)
                    if field == "task" and meta.get("project") != target["meta"].get("project"):
                        raise Error("SCHEMA_INVALID", "Task and Update project references disagree.")
            for field in ("reply_to", "supersedes"):
                seen, cursor = set(), meta["id"]
                while cursor:
                    if cursor in seen:
                        raise Error("SCHEMA_INVALID", "Reference cycle detected.")
                    seen.add(cursor)
                    cursor = self.docs[cursor]["meta"].get(field)
            if self.product == "cosmesh" and meta["kind"] == "task":
                self.reduce(doc)

    def read(self, action, p):
        ordered = sorted(self.docs.values(), key=lambda d: (d["meta"]["created_at"], d["meta"]["id"]))
        if action == "validate":
            return {"valid": True, "documents": len(ordered), "identity_verified": False}
        if action == "doc.list":
            docs = [d for d in ordered if (p.get("lifecycle", "active") == "all" or d["meta"]["lifecycle"] == p.get("lifecycle", "active"))
                    and (not p.get("kind") or d["meta"]["kind"] == p["kind"])
                    and (not p.get("project") or d["meta"].get("project") == p["project"])
                    and (not p.get("waiting") or d["meta"].get("waiting"))
                    and (not p.get("query") or p["query"].casefold() in (d["meta"]["title"] + d["body"]).casefold())]
            return [public(d) for d in docs]
        if action == "doc.show":
            doc = self.get(p["id"], p.get("kind"))
            result = public(doc)
            if self.product == "cosmesh" and doc["meta"]["kind"] == "task":
                result["derived"] = self.reduce(doc)
            return result
        if action == "doc.history":
            self.get(p["id"])
            return {"id": p["id"], "revisions": self.store.git("log", "--format=%H", self.revision, "--", "documents/" + p["id"] + ".md").splitlines()}
        if action == "context":
            doc = self.get(p["id"])
            related = [d for d in ordered if d["meta"]["lifecycle"] != "trash" and d["meta"]["id"] != p["id"]
                       and (p["id"] in [d["meta"].get("project"), d["meta"].get("task"),
                                        d["meta"].get("reply_to"), d["meta"].get("supersedes")]
                            or p["id"] in d["meta"].get("sources", [])
                            or d["meta"]["id"] in doc["meta"].get("sources", []))]
            result = {"document": self.read("doc.show", p), "related": [public(d) for d in related],
                      "source_revision": self.revision, "identity_verified": False}
            if doc["meta"].get("project"):
                parent = self.get(doc["meta"]["project"])
                result["project"] = public(parent) if parent["meta"]["lifecycle"] != "trash" else {
                    "id": parent["meta"]["id"], "lifecycle": "trash"}
            return result
        if action == "inbox":
            if self.product == "cosbase":
                return [public(d) for d in ordered if d["meta"]["lifecycle"] == "active" and d["meta"].get("needs_triage")]
            invitations = []
            for task in ordered:
                if task["meta"]["kind"] == "task":
                    invitations.extend({"task": task["meta"]["id"], "invitation": key, **value}
                                       for key, value in self.reduce(task)["invitations"].items()
                                       if value["to"] == self.user and value["status"] == "pending")
            return {"invitations": invitations,
                    "feedback": [public(d) for d in ordered if d["meta"].get("type") == "feedback"
                                 and self.user in d["meta"].get("recipients", [])]}
        if action == "agenda":
            start = day(p.get("from")) or datetime.now(self.tz).date().isoformat()
            end = day(p.get("to")) or start
            if start > end:
                raise Error("INPUT_INVALID", "Agenda end must not precede start.")
            entries = []
            for doc in ordered:
                meta = doc["meta"]
                if meta["kind"] != "task" or meta["lifecycle"] != "active":
                    continue
                state = self.reduce(doc) if self.product == "cosmesh" else meta
                if state.get("status") in {"closed", "done", "cancelled"}:
                    continue
                if self.product == "cosmesh" and self.user != meta["lead"] and state["participants"].get(self.user) != "accepted":
                    continue
                for field in ("due", "followup"):
                    if state.get(field) and start <= state[field] <= end:
                        parent = self.get(meta["project"])["meta"] if meta.get("project") else None
                        entries.append({"id": meta["id"], "title": meta["title"], "kind": field,
                                        "date": state[field], "project_lifecycle": parent["lifecycle"] if parent else None})
            return sorted(entries, key=lambda e: (e["date"], e["id"], e["kind"]))
        raise Error("INPUT_INVALID", "Unknown query.")
