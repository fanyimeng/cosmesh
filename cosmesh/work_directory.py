"""Owner directory: small derived rows, independent of execution hosts and transfers."""

import time

from .errors import Error
from .personal_protocol import read_json, save_json
from .thread_names import normalized, parse
from .thread_access_cli import Online


def names_from_docs(tid, docs):
    main = docs[tid]["meta"]
    cards = sorted(
        (
            d
            for d in docs.values()
            if d["meta"].get("thread") == tid and d["meta"]["author"] == main["author"] and parse(d)
        ),
        key=lambda d: (d["meta"]["created_at"], d["meta"]["id"]),
    )
    aliases = {main["title"]}
    display = main["title"]
    for doc in cards:
        card = parse(doc)
        display = " · ".join(x for x in (card["english"], card["chinese"]) if x) or main["title"]
        aliases.update(x for x in (card["english"], card["chinese"], display, *card["aliases"]) if x)
    return {"thread": tid, "title": display, "aliases": sorted(aliases)}


def local_rows(wb):
    if not wb.base.path(".cosmesh/config.json").is_file():
        return []
    path = wb.base.path(".git/cosmesh-work-directory/local.json")
    stamp = [wb.base.head, wb.client.get("actor"), wb.client.get("personal_thread_authors", {})]
    cache = read_json(path, {})
    if cache.get("stamp") == stamp:
        return cache["rows"]
    with wb.base.locked():
        wb.load()
        rows = []
        for tid, item in wb.catalog.items():
            meta = item["doc"]["meta"]
            if (
                item["repo"] != "local"
                or meta["kind"] != "thread"
                or meta["author"] != wb.author_for(item)
            ):
                continue
            card = wb._thread_names[tid]
            rows.append(
                {
                    "thread": tid,
                    "title": " · ".join(n for n in (card["english"], card["chinese"]) if n)
                    or meta["title"],
                    "aliases": sorted({meta["title"], *card["aliases"]}),
                }
            )
    save_json(path, {"stamp": stamp, "rows": rows})
    return rows


def lookup(wb, query="", offline=False, fresh=False, online=None):
    local = {r["thread"]: r for r in local_rows(wb)}

    def matches(row):
        return any(
            normalized(query) in normalized(n) for n in (row["thread"], row["title"], *row["aliases"])
        )

    remote = {}
    scope = "local_only"
    issue = None
    if not offline:
        try:
            online = online or Online(wb)
            from .personal_protocol import digest

            identity = digest((online.info["hub_id"] + "\0" + online.info["person"]["id"]).encode())
            path = wb.base.path(".git/cosmesh-work-directory/" + identity + ".json")
            # Uninitialized machines may discover work without creating a workbench.
            initialized = wb.base.path(".cosmesh/config.json").is_file()
            cached = read_json(path, {}) if initialized else {}
            rows = cached.get("rows", [])
            if (
                fresh
                or time.time() - cached.get("at", 0) > 60
                or (query and not any(matches(r) for r in [*local.values(), *rows]))
            ):
                rows = online.call("personal_work_list")["threads"]
                if initialized:
                    save_json(path, {"at": time.time(), "rows": rows})
                scope = "local_and_current_hub"
            else:
                scope = "local_and_cached_hub"
            remote = {r["thread"]: r for r in rows}
        except Error as exc:
            if fresh or exc.code in {"AUTH_DENIED", "STATE_CONFLICT", "USER_DISABLED"}:
                raise
            issue = exc.code
    rows = []
    for tid in sorted(local.keys() | remote.keys()):
        item = dict(local.get(tid) or remote[tid])
        if tid in remote and tid in local:
            item["aliases"] = sorted(set(item["aliases"]) | set(remote[tid]["aliases"]))
        if matches(item):
            rows.append(
                dict(
                    item,
                    location="both"
                    if tid in local and tid in remote
                    else "remote"
                    if tid in remote
                    else "local",
                    snapshot=remote.get(tid, {}).get("sha"),
                )
            )
    return {"threads": rows, "scope": scope, "hub_issue": issue}


def recommend(message, rows):
    if message["filing"] != "pending":
        return []
    text = normalized(message["title"] + " " + message["excerpt"])
    hits = []
    for row in rows:
        labels = [
            s
            for s in set([row["title"], *row["aliases"]])
            if len(normalized(s)) >= 2 and normalized(s) in text
        ]
        if labels:
            best = max(labels, key=lambda x: (len(x), x))
            hits.append(
                (
                    len(best),
                    row["thread"],
                    {"thread": row["thread"], "title": row["title"], "matched_name": best},
                )
            )
    return [v for _, _, v in sorted(hits, key=lambda x: (-x[0], x[1]))[:3]]
