"""Recipient consent, resumable linked delivery, and online read-only workspace views."""

import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from . import _fcntl as fcntl
from .errors import Error
from .personal_protocol import HASH, check_id, digest, packed, private_dir, read_json, save_json


def mode(args):
    if getattr(args, "share_workspace", False):
        return "share"
    if getattr(args, "message_only", False):
        return "message_only"
    return "ask"


def supported(online):
    return online.info.get("workspace_protocol") == "workspace.v1"


def require(online):
    if not supported(online):
        raise Error("FEATURE_UNAVAILABLE", "工作区共享需要支持 workspace.v1 的 Hub。")


def preview(work, title, recipients, choice):
    return {
        "thread": work, "title": title, "to": sorted(set(recipients)), "mode": choice,
        "scope": "所选 Thread 的定义、记录、可用历史、已登记文件及后续同步进展；收件人只读。",
        "choices": ["--share-workspace", "--message-only", "cancel"],
    }


def interactive(args):
    return not getattr(args, "json", False) and sys.stdin.isatty() and sys.stderr.isatty()


def select_source(args, online, recipients):
    if not online or not supported(online) or not interactive(args) or mode(args) == "message_only":
        return None
    hint = "回车取消。" if mode(args) == "share" else "输入 q 取消，回车仅发消息。"
    print(f"给 {', '.join(recipients)} 的消息可以关联本人的工作。输入要共享的 Thread ID。{hint}",
          file=sys.stderr, flush=True)
    answer = sys.stdin.readline()
    if not answer or answer.strip().lower() == "q" or (mode(args) == "share" and not answer.strip()):
        raise Error("CANCELLED", "已取消发送。")
    return answer.strip() or None


def choose(args, status, work, title):
    missing = status["missing"]
    if not missing or mode(args) == "share":
        return True
    if not interactive(args):
        # Existing non-interactive callers retain selected-content delivery.
        return False
    print(f"是否向 {', '.join(missing)} 共享「{title}」({work})？\n"
          "收件人可在线查看完整工作记录、可用历史、已登记文件以及后续同步的进展。\n"
          "[1] 共享工作区并发送   [2] 仅发送消息   [3] 取消",
          file=sys.stderr, flush=True)
    while True:
        print("请选择 1、2 或 3（回车取消） > ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline().strip()
        if answer in ("1", "2"):
            return answer == "1"
        if answer in ("", "3"):
            raise Error("CANCELLED", "已取消发送。")


def journal_location(wb, online):
    identity = digest(packed([online.info["hub_id"], online.info["person"]["id"]]).encode())
    return wb.base.root / ".git" / "cosmesh-workspace-delivery" / identity


def operation_id(signature, key):
    return digest(packed(["key", key] if key else ["auto", signature]).encode())


def previously_chosen(wb, online, signature, key):
    return (journal_location(wb, online) / (operation_id(signature, key) + ".json")).is_file()


def locked_journal(directory, operation):
    check_id(operation, HASH)
    lock = (directory / ("lock-" + operation[:2])).open("a+b")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return lock
    except BaseException:
        lock.close()
        raise


def deliver(wb, online, args, payload, signature, key, title, paths, staged):
    require(online)
    directory = private_dir(journal_location(wb, online))
    operation = operation_id(signature, key)
    path = directory / (operation + ".json")
    with locked_journal(directory, operation):
        state = read_json(path)
        if state is None:
            status = online.call("delivery_workspace_status", work=payload["work"], to=payload["to"])
            sharing = choose(args, status, payload["work"], title)
            targets = sorted(status["granted"] + status["missing"])
            state = {"key": key or uuid4().hex, "fixed_key": bool(key), "signature": signature,
                     "payload": dict(payload, to=targets), "sharing": sharing,
                     "allow_new": status["missing"] if sharing else [],
                     "paths": [str(Path(p).expanduser().resolve()) for p in paths]}
            save_json(path, state)
        if state["signature"] != signature:
            raise Error("IDEMPOTENCY_CONFLICT", "This key identifies a different delivery or sharing choice.")
        if state.get("complete"):
            # Use the original canonical recipients and pinned snapshot, even if the
            # Thread has advanced or a recipient was subsequently revoked.
            request = dict(payload, to=state["to"])
            return submit_state(online, state, request)
        return finish(wb, online, path, state, staged)


def submit_state(online, state, payload):
    if not state["sharing"]:
        return online.call("delivery_send", **payload, key=state["key"])
    return online.call("delivery_workspace_send", **payload, key=state["key"],
                       snapshot=state["ticket"]["snapshot"], grant=bool(state["allow_new"]),
                       allow_new=state["allow_new"])


def finish(wb, online, path, state, staged=None):
    from .delivery_cli import AttachmentUpload, attachment

    payload = state["payload"]
    if state["sharing"] and not state.get("ticket"):
        from .workspace_progress import prepare_workspace
        state["ticket"] = prepare_workspace(wb, online, payload["work"])
        save_json(path, state)
    if not state.get("ready"):
        # A retry after a partial upload can re-stage only the originally reviewed files.
        with tempfile.TemporaryDirectory() as temp:
            for i, f in enumerate(payload["files"]):
                if staged is None and attachment(state["paths"][i], temp, i) != f:
                    raise Error("STATE_CONFLICT", "An attachment changed since this operation was prepared.")
                AttachmentUpload(online).upload(Path(staged or temp) / str(i), f["sha"], f["size"])
        state["ready"] = True
        save_json(path, state)
    result = submit_state(online, state, payload)
    if result.get("accepted") is not True:
        raise Error("SYNC_PROTOCOL", "Delivery acceptance is unconfirmed. Retry this pending operation.")
    if state["sharing"] and not result.get("revoked"):
        if not result.get("workspace"):
            # Replayed after the grant or delivery was withdrawn.
            if not result.get("replayed"):
                raise Error("SYNC_PROTOCOL", "The Hub did not confirm workspace access.")
        else:
            from .workspace_progress import arm_workspace
            arm_workspace(wb, dict(state["ticket"], revision=result["workspace"].get("revision")))
    if state["fixed_key"]:
        # Keep only the metadata necessary for server-side replay after new progress.
        state.update(complete=True, to=payload["to"])
        for field in ("paths", "payload"):
            state.pop(field, None)
        save_json(path, state)
    else:
        path.unlink()
    return result


def pending(wb, online, operation=None):
    directory = private_dir(journal_location(wb, online))
    if operation is None:
        rows = []
        for path in sorted(directory.glob("*.json")):
            try:
                state = read_json(path)
            except FileNotFoundError:
                continue
            if state and not state.get("complete"):
                rows.append({"operation": path.stem, "thread": state["payload"]["work"],
                             "recipients": state["payload"]["to"], "share_workspace": state["sharing"]})
        return {"pending": rows}
    with locked_journal(directory, operation):
        path = directory / (operation + ".json")
        state = read_json(path)
        if not state or state.get("complete"):
            raise Error("NOT_FOUND", "Choose a pending delivery from thread mail pending.")
        return finish(wb, online, path, state)


def open_workspace(online, args):
    params = {"id": args.id}
    if args.at_send:
        params["at_send"] = True
    if args.workspace_revision:
        params["revision"] = args.workspace_revision
    if args.version and not args.record:
        raise Error("INPUT_INVALID", "--version requires --record.")
    if bool(args.output) != bool(args.download):
        raise Error("INPUT_INVALID", "Use --download ASSET with --output NEWFILE.")
    if args.download:
        from .delivery_cli import receive_file

        if args.offset or args.limit is not None or args.chars is not None:
            raise Error("INPUT_INVALID", "A file download starts at the beginning of the selected file.")

        def read_chunk(offset):
            result = online.call("delivery_workspace_file", asset=args.download, offset=offset, **params)
            revision = check_id(result.get("revision"), HASH)
            if params.get("revision", revision) != revision:
                raise Error("SYNC_PROTOCOL", "The workspace revision changed during download.")
            params.pop("at_send", None)
            params["revision"] = revision
            return result

        result = receive_file(Path(args.output), read_chunk)
        return dict(result, revision=params["revision"])
    params["offset"] = args.offset
    if args.records or args.files or args.history:
        if args.chars is not None:
            raise Error("INPUT_INVALID", "--chars applies only to the record body.")
        if args.limit is not None:
            params["limit"] = args.limit
        if args.history:
            params["record"] = args.history
        method = "delivery_workspace_files" if args.files else "delivery_workspace_records"
    else:
        if args.limit is not None:
            raise Error("INPUT_INVALID", "Choose --records, --history or --files with --limit.")
        if args.record:
            params["record"] = args.record
        if args.version:
            params["version"] = args.version
        if args.chars is not None:
            params["chars"] = args.chars
        method = "delivery_workspace_open"
    return online.call(method, **params)


def mail(wb, online, args):
    require(online)
    action = args.mail_action
    if action == "open":
        return open_workspace(online, args)
    if action in ("pending", "resume"):
        return pending(wb, online, getattr(args, "id", None))
    method = "delivery_workspace_revoke" if action == "unshare" else "delivery_workspace_status"
    return online.call(method, work=args.id, to=args.accounts)
