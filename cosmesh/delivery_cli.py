"""Prepare exactly the selected content, then deliver it to named people."""

import base64
from . import _fcntl as fcntl
from uuid import uuid4
import hashlib
import os
from pathlib import Path
import tempfile

from .errors import Error
from .personal import Personal
from .personal_protocol import (
    CHUNK,
    digest,
    fingerprint,
    inspect_file,
    packed,
    private_dir,
    read_json,
    save_json,
)
from .store import safe_path
from .thread_access_cli import Online
from .transfer_policy import PUBLIC
from .work_directory import lookup, recommend


def add_commands(group, command, body):
    for verb in ("send", "request", "reply"):
        parser = command(group, verb, "thread." + verb)
        if verb == "reply":
            parser.add_argument("id")
            parser.add_argument("--thread")
            parser.add_argument("--to", action="append", default=[])
        else:
            parser.add_argument("id", nargs="?")
            parser.add_argument("--to", action="append", required=True)
        body(parser)
        parser.add_argument("--title")
        parser.add_argument("--record", action="append", default=[])
        parser.add_argument("--attach", action="append", default=[])
        parser.add_argument("--expect")
        sharing = parser.add_mutually_exclusive_group()
        sharing.add_argument("--share-workspace", action="store_true", help="Grant named recipients ongoing read access to this work")
        sharing.add_argument("--message-only", action="store_true", help="Deliver only the selected message content")
    for verb in ("inbox", "outbox", "chat", "activity", "search"):
        parser = command(group, verb, "thread." + verb)
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--before", type=int)
        if verb == "activity":
            parser.add_argument("id")
        else:
            parser.add_argument("--with", dest="contact", required=verb == "chat")
            parser.add_argument("--thread")
        if verb == "inbox":
            parser.add_argument("--unread", action="store_true")
        if verb == "search":
            parser.add_argument("query")
    mail = command(group, "mail").add_subparsers(dest="mail_action", required=True)
    for action in ("show", "file", "revoke", "download", "mark", "open", "unshare", "sharing", "pending", "resume"):
        parser = command(mail, action, "thread.mail")
        if action == "mark":
            parser.add_argument("ids", nargs="+")
            seen = parser.add_mutually_exclusive_group(required=True)
            seen.add_argument("--seen", dest="seen", action="store_true")
            seen.add_argument("--unseen", dest="seen", action="store_false")
        elif action != "pending":
            parser.add_argument("id")
        if action == "open":
            view = parser.add_mutually_exclusive_group()
            view.add_argument("--records", action="store_true")
            view.add_argument("--record")
            view.add_argument("--history", metavar="RECORD")
            view.add_argument("--files", action="store_true")
            view.add_argument("--download", metavar="ASSET")
            version = parser.add_mutually_exclusive_group()
            version.add_argument("--at-send", action="store_true")
            version.add_argument("--workspace-revision", metavar="REVISION")
            parser.add_argument("--version", help="Authored version of --record")
            parser.add_argument("--limit", type=int)
            parser.add_argument("--offset", type=int, default=0)
            parser.add_argument("--chars", type=int)
            parser.add_argument("--output")
        if action == "unshare":
            parser.add_argument("--from", dest="accounts", action="append", required=True)
        if action == "sharing":
            parser.add_argument("--to", dest="accounts", action="append", required=True)
        if action == "file":
            filing = parser.add_mutually_exclusive_group(required=True)
            filing.add_argument("--thread")
            filing.add_argument("--defer", action="store_true")
        if action == "download":
            parser.add_argument("--name", required=True)
            parser.add_argument("--output", required=True)


class AttachmentUpload:
    upload = Personal.upload

    def __init__(self, online):
        self.online = online

    def rpc(self, payload):
        return self.online.call(
            payload["method"], **{k: v for k, v in payload.items() if k != "method"}
        )


def attachment(path, directory, index):
    path = safe_path(path)
    inspected = inspect_file(path, PUBLIC["file"])
    if inspected.get("reason"):
        raise Error("TRANSFER_FILTERED", "Selected attachment cannot be sent.", inspected["reason"])
    staged = Path(directory) / str(index)
    checksum = hashlib.sha256()
    with path.open("rb") as source, staged.open("xb") as out:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            if out.tell() + len(chunk) > inspected["bytes"]:
                raise Error("STATE_CONFLICT", "Attachment grew during preparation.")
            checksum.update(chunk)
            out.write(chunk)
        if fingerprint(os.fstat(source.fileno())) != inspected["fingerprint"]:
            raise Error("STATE_CONFLICT", "Attachment changed while preparing the delivery.")
    return {"name": path.name, "sha": checksum.hexdigest(), "size": inspected["bytes"]}


def download(online, mid, name, output):
    path = safe_path(output)
    if path.exists() or not path.parent.is_dir():
        raise Error("PATH_INVALID", "Choose a new file in an existing directory.")
    item = online.call("delivery_show", id=mid)
    f = next((f for f in item["files"] if f["name"] == name), None)
    if not f:
        raise Error("NOT_FOUND", "Attachment unavailable.")
    return receive_file(path, lambda offset: online.call("delivery_file", id=mid, name=name, offset=offset), f)


def receive_file(path, read_chunk, expected=None):
    path = safe_path(path)
    if path.exists() or not path.parent.is_dir():
        raise Error("PATH_INVALID", "Choose a new file in an existing directory.")
    # Close the temporary handle before publishing the verified file on Windows.
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        partial = Path(directory) / "content"
        with partial.open("xb") as stream:
            size, sha = stream_file(stream, read_chunk, expected)
        if os.name == "nt":
            os.rename(partial, path)  # Windows rejects an existing destination.
        else:
            os.link(partial, path)
    return {"path": str(path), "size": size, "sha": sha}


def stream_file(stream, read_chunk, expected):
    f = expected
    checksum = hashlib.sha256()
    while True:
        part = read_chunk(stream.tell())
        if not f:
            f = {"size": part["size"], "sha": part["sha"]}
        raw = base64.b64decode(part["data"], validate=True)
        if (part["offset"] != stream.tell() or part["size"] != f["size"] or part["sha"] != f["sha"]
                or stream.tell() + len(raw) > f["size"] or (not raw and f["size"])):
            raise Error("SYNC_PROTOCOL", "File changed during transfer.")
        checksum.update(raw)
        stream.write(raw)
        if stream.tell() == f["size"]:
            break
    if checksum.hexdigest() != f["sha"]:
        raise Error("CHECKSUM_MISMATCH", "Downloaded bytes differ from the approved file.")
    stream.flush()
    os.fsync(stream.fileno())
    return f["size"], f["sha"]


def submit(online, payload, signature, key):
    if key:
        return online.call("delivery_send", **payload, key=key)
    # Keep an operation key across a lost response, then allow intentional repeats.
    identity = digest(packed([online.info["hub_id"], online.info["person"]["id"]]).encode())
    data = Path(os.environ.get("COSMESH_DATA_DIR", Path.home() / ".local/share/cosmesh"))
    directory = private_dir(data / "pending-deliveries" / identity)
    pending = directory / signature
    # A fixed set of shard locks avoids a permanent file for every delivery.
    with (directory / ("lock-" + signature[:2])).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        attempt = read_json(pending)
        if not attempt:
            attempt = {"key": uuid4().hex}
            save_json(pending, attempt)
        result = online.call("delivery_send", **payload, key=attempt["key"])
        pending.unlink()
        return result


def send(args, wb, online):
    from . import workspace_delivery_cli as workspace
    from .cli import read_input
    from .protocol import MAX_DOCUMENT

    verb = args.thread_command
    origin = getattr(args, "thread", None) if verb == "reply" else args.id
    parent = args.id if verb == "reply" else getattr(args, "reply_to", None)
    recipients = getattr(args, "to", []) or []
    if isinstance(recipients, str):
        recipients = [recipients]
    body = read_input(args.file, MAX_DOCUMENT) if hasattr(args, "file") else getattr(args, "body", "")
    if not recipients and parent:
        if not online:
            raise Error("INPUT_INVALID", "Specify --to when previewing a reply offline.")
        original = online.call("delivery_show", id=parent)
        recipients = (
            original["recipients"]
            if original["sender"] == online.info["actor"]
            else [original["sender"]]
        )
    if not origin and not getattr(args, "expect", None):
        origin = workspace.select_source(args, online, recipients)
    records = getattr(args, "record", []) or []
    sources = []
    work_title = None
    if origin:
        with wb.base.locked():
            wb.load()
            item = wb.find(origin, "thread")
            origin = item["doc"]["meta"]["id"]
            work_title = item["doc"]["meta"]["title"]
            if item["repo"] != "local" or item["doc"]["meta"]["author"] != wb.author_for(item):
                raise Error("AUTH_DENIED", "Choose your own work.")
            parts = [body] if body else []
            for rid in records:
                r = wb.find(rid, "entry")
                if r["repo"] != "local" or r["doc"]["meta"]["thread"] != origin:
                    raise Error("INPUT_INVALID", "The selected record belongs to different work.")
                from .thread_protocol import content_version

                sources.append({"record": r["doc"]["meta"]["id"], "version": content_version(r["doc"])})
                parts.append(r["doc"]["meta"]["title"] + "\n" + r["doc"]["body"])
            body = "\n\n".join(parts)
    elif records:
        raise Error("INPUT_INVALID", "Choose the source work for selected records.")
    paths = getattr(args, "attach", [])
    if sum(safe_path(p).stat().st_size for p in paths) > PUBLIC["batch"]:
        raise Error("TRANSFER_BUDGET", "Attachments exceed one delivery batch.")
    with tempfile.TemporaryDirectory() as temp:
        files = [attachment(path, temp, i) for i, path in enumerate(paths)]
        payload = dict(
            work=origin,
            to=sorted(set(recipients)),
            parent=parent,
            title=getattr(args, "title", None) or "",
            body=body,
            files=files,
            sources=sources,
        )
        mode = workspace.mode(args)
        if mode == "share" and not origin:
            raise Error("INPUT_INVALID", "Choose the source Thread for --share-workspace.")
        # The legacy default preview remains valid. Explicit consent is bound to the preview.
        signature = digest(packed(payload if mode == "ask" else [mode, payload]).encode())
        if not online:
            result = dict(payload, preview=uuid4().hex + "." + signature, sent=False)
            if origin:
                result["workspace_choice"] = workspace.preview(origin, work_title, recipients, mode)
            return result
        expected = getattr(args, "expect", None)
        if expected and (
            len(expected) != 97
            or expected[32:] != "." + signature
            or any(c not in "0123456789abcdef" for c in expected[:32])
        ):
            raise Error("STATE_CONFLICT", "Delivery differs from the reviewed preview.")
        if not recipients:
            raise Error("INPUT_INVALID", "Choose a recipient.")
        key = getattr(args, "idempotency_key", None) or expected
        if mode == "share" or (origin and mode == "ask" and (
                workspace.supported(online) or workspace.previously_chosen(wb, online, signature, key))):
            return workspace.deliver(wb, online, args, payload, signature, key, work_title, paths, temp)
        for i, f in enumerate(files):
            AttachmentUpload(online).upload(Path(temp) / str(i), f["sha"], f["size"])
        return submit(online, payload, signature, key)


def execute(args, wb):
    verb = args.thread_command
    preview = getattr(args, "dry_run", False) or getattr(args, "local_only", False)
    if preview and verb not in ("send", "reply", "request"):
        raise Error("INPUT_INVALID", "Only send supports previews.")
    if getattr(args, "offline", False) and not preview:
        raise Error("OFFLINE", "Mailbox reads and delivery require the configured Hub.")
    online = None if preview else Online(wb)
    if online and online.info.get("delivery_protocol") != "delivery.v1":
        raise Error(
            "FEATURE_UNAVAILABLE", "A CosMesh 0.6 Hub is required for selected-content delivery."
        )
    if verb in ("send", "reply", "request"):
        return send(args, wb, online)
    if verb == "mail":
        action = args.mail_action
        if action in {"open", "unshare", "sharing", "pending", "resume"}:
            from .workspace_delivery_cli import mail
            return mail(wb, online, args)
        if action == "show":
            return online.call("delivery_show", id=args.id)
        if action == "download":
            return download(online, args.id, args.name, args.output)
        if action == "mark":
            return online.call("delivery_mark_many",ids=args.ids,seen=args.seen)
        if action == "revoke":
            return online.call("delivery_revoke", id=args.id)
        work = args.thread
        if work:
            rows = lookup(wb, work, online=online)["threads"]
            if not any(r["thread"] == work for r in rows):
                raise Error("NOT_FOUND", "Choose a work ID in your directory.")
        return online.call("delivery_file_into", id=args.id, work=work)
    folder = "all" if verb in ("chat", "search", "activity") else verb
    result = online.call(
        "delivery_list",
        folder=folder,
        limit=args.limit,
        before=args.before,
        peer=getattr(args, "contact", None),
        work=getattr(args, "thread", None) or getattr(args, "id", None),
        unread=getattr(args, "unread", False),
        query=getattr(args, "query", None),
    )
    if folder == "inbox" and any(m["filing"] == "pending" for m in result["messages"]):
        rows = lookup(wb, online=online)["threads"]
        for m in result["messages"]:
            m["recommendations"] = recommend(m, rows)
    return result
