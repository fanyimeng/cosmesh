"""End-user contracts for owner work, selected delivery, receipt filing and collection."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmesh.cli import execute, parser
from cosmesh.errors import Error
from cosmesh.personal_relay import initialize as enable_relay
from cosmesh.threads import Workbench, initialize
from tests.test_threads import ThreadFixture


class WorkV1Tests(ThreadFixture):
    unified = True

    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"COSMESH_DATA_DIR": str(self.root / "device")})
        env.start()
        self.addCleanup(env.stop)
        enable_relay(self.hub)
        for user in self.wbs:
            self.hub.spaces(
                {"method": "community_name", "name": user.upper(), "key": "name-" + user}, user
            )

    def cmd(self, user, *args):
        wb = self.wbs[user] if isinstance(user, str) else user
        return execute(parser("cosmesh").parse_args([*args, "--root", str(wb.base.root)]), "cosmesh")[0]

    def other_device(self, user):
        root = self.root / ("second-" + user)
        initialize(root, user)
        wb = Workbench(root)
        wb.client.update(actor=user, connection=self.connection(user))
        wb.save_client()
        return wb

    def test_compact_handoff_and_atomic_mail_marks(self):
        tid = self.cmd("alice","thread","create","--title","Observation","--body","Research goal")["id"]
        self.cmd("alice","thread","bind",tid,"--agent","test","--workdir",str(self.root),"--session-id","local")
        summary = self.cmd("alice","collect","summary",tid,"--event","stopped","--body","Next: check velocity.")
        for i in range(4):
            self.cmd("alice","thread","note",tid,"--body",str(i)+"x"*700)
        handoff = self.cmd("alice","thread","continue",tid,"--ready","--offline")
        self.assertEqual(handoff["latest_summary"]["source"]["id"],summary["id"])
        self.assertEqual(handoff["goal"]["text"],"Research goal")
        self.assertEqual(handoff["local_locations"][0]["workdir"],str(self.root))
        self.assertEqual(len(handoff["recent"]),3)
        self.assertTrue(all(r["truncated"] for r in handoff["recent"]))
        self.cmd("alice","thread","personal",tid)
        self.assertEqual(self.cmd("alice","thread","continue",tid,"--ready","--require-fresh")["thread"],tid)
        mids=[self.cmd("alice","thread","send","--to","bob","--body",v)["id"] for v in ("first","second")]
        other=self.cmd("alice","thread","send","--to","carol","--body","private")["id"]
        with self.assertRaises(Error):
            self.cmd("bob","thread","mail","mark",mids[0],other,"--seen")
        self.assertEqual(len(self.cmd("bob","thread","inbox","--unread")["messages"]),2)
        self.cmd("bob","thread","mail","mark",*mids,"--seen")
        self.assertEqual(self.cmd("bob","thread","inbox","--unread")["messages"],[])

    def test_selective_delivery_and_private_filing(self):
        a = self.cmd("alice", "thread", "create", "--title", "Lab notebook")["id"]
        b = self.cmd("bob", "thread", "create", "--title", "Survey Beta")["id"]
        self.cmd("alice", "thread", "note", a, "--body", "private unrelated notes")
        note = self.cmd("alice", "thread", "note", a, "--body", "Survey Beta results")["id"]
        src = self.root / "sample.txt"
        src.write_text("approved file")
        args = ["thread", "send", a, "--to", "bob", "--record", note, "--attach", str(src)]
        plan = self.cmd("alice", *args, "--dry-run")
        mid = self.cmd("alice", *args, "--expect", plan["preview"])["id"]
        self.assertEqual(mid, self.cmd("alice", *args, "--expect", plan["preview"])["id"])
        inbox = self.cmd("bob", "thread", "inbox")["messages"]
        self.assertEqual(len(inbox), 1)
        self.assertEqual(self.cmd("bob", "thread", "search", "Beta results")["messages"][0]["id"], mid)
        self.assertFalse(self.cmd("carol", "thread", "search", "Beta results")["messages"])
        self.assertEqual(inbox[0]["recommendations"][0]["thread"], b)
        self.assertNotIn("private unrelated", str(self.cmd("bob", "thread", "mail", "show", mid)))
        self.assertNotIn(a, str(inbox))
        self.assertEqual(self.cmd("carol", "thread", "inbox")["messages"], [])
        with self.assertRaises(Error):
            self.cmd("carol", "thread", "mail", "show", mid)
        self.assertEqual(self.cmd("alice", "thread", "activity", a)["messages"][0]["id"], mid)
        self.cmd("bob", "thread", "mail", "file", mid, "--defer")
        self.assertEqual(
            self.cmd("bob", "thread", "inbox")["messages"][0].get("recommendations", []), []
        )
        self.cmd("bob", "thread", "mail", "file", mid, "--thread", b)
        reply = self.cmd("bob", "thread", "reply", mid, "--body", "received")["id"]
        self.assertEqual(self.cmd("alice", "thread", "mail", "show", reply)["work"], a)
        self.assertEqual(len(self.cmd("bob", "thread", "chat", "--with", "alice")["messages"]), 2)
        dest = self.root / "received.txt"
        self.cmd(
            "bob", "thread", "mail", "download", mid, "--name", "sample.txt", "--output", str(dest)
        )
        self.assertEqual(dest.read_bytes(), src.read_bytes())
        self.cmd("alice", "thread", "mail", "revoke", mid)
        with self.assertRaises(Error):
            self.cmd("bob", "thread", "mail", "show", mid)
        with self.use("bob") as wb:
            self.assertEqual(
                len([i for i in wb.catalog.values() if i["doc"]["meta"]["kind"] == "thread"]), 1
            )

    def test_private_collection_and_discovery_across_agents(self):
        tid = self.cmd("alice", "thread", "create", "--title", "Original", "--english", "Agenda")["id"]
        project = self.root / "project"
        project.mkdir()
        (project / "work.txt").write_text("data")
        (project / ".env").write_text("excluded")
        self.cmd("alice", "collect", "add", tid, str(project), "--personal")
        r = self.cmd("alice", "collect", "once")
        self.assertTrue(r["complete"], r)
        self.assertEqual(r["scopes"][0]["changed"], 1)
        self.assertEqual(self.cmd("alice", "collect", "once")["scopes"][0]["changed"], 0)
        self.cmd(
            "alice", "collect", "summary", tid, "--body", "Decision from this turn", "--event", "turn-1"
        )
        self.assertTrue(self.cmd("alice", "collect", "once")["complete"])
        other = self.other_device("alice")
        head = other.base.head
        rows = self.cmd(other, "thread", "find", "Agenda", "--require-fresh")["threads"]
        self.assertEqual([(r["thread"], r["location"]) for r in rows], [(tid, "remote")])
        self.assertEqual(other.base.head, head)
        self.assertEqual(self.cmd("bob", "thread", "find", "Agenda", "--require-fresh")["threads"], [])
        out = self.root / "restored"
        r = self.cmd(
            other, "thread", "fetch", tid, "--workdir", str(out), "--agent", "other-host", "--files"
        )
        self.assertEqual(r["restoration"]["files"], "complete")
        self.assertEqual((out / "work.txt").read_text(), "data")
        self.assertFalse((out / ".env").exists())
        self.assertIn("Decision from this turn", str(self.cmd(other, "thread", "show", tid)))
        (project / "work.txt").unlink()
        self.assertTrue(self.cmd("alice", "collect", "once")["complete"])
        self.assertTrue(self.cmd(other, "thread", "sync", tid, "--pull")["pulled"])
        self.assertTrue((out / "work.txt").exists())

    def test_bulk_upload_allows_an_independent_writer_and_large_file_is_index_only(self):
        from cosmesh.personal import Personal

        tid = self.cmd("alice", "thread", "create", "--title", "Continuity")["id"]
        project = self.root / "transfer"
        project.mkdir()
        with (project / "large.bin").open("wb") as stream:
            stream.truncate(5_000_000_001)
        (project / "small.txt").write_text("payload")
        self.cmd("alice", "collect", "add", tid, str(project), "--personal")
        locked = self.wbs["alice"].base.path(".cosmesh/lock")
        upload = Personal.upload
        observed = []

        def check_lock(personal, path, sha, size):
            if Path(path).suffix == ".part":
                script = "import sys,fcntl; f=open(sys.argv[1],'a+'); fcntl.lockf(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
                subprocess.run([sys.executable, "-c", script, str(locked)], check=True)
                observed.append(size)
            return upload(personal, path, sha, size)

        with patch.object(Personal, "upload", check_lock):
            result = self.cmd("alice", "collect", "once")
        self.assertEqual(observed, [7])
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["scopes"][0]["skipped"][0]["reason"], "file_limit")
        self.assertEqual(
            self.cmd("alice", "collect", "status")["scopes"][0]["excluded"], {"large.bin": "file_limit"}
        )
        self.assertEqual(self.cmd("alice", "collect", "once")["scopes"][0]["changed"], 0)

    def test_direct_delivery_keeps_other_recipients_private(self):
        mid = self.cmd(
            "alice", "thread", "send", "--to", "bob", "--to", "carol", "--body", "Shared result"
        )["id"]
        self.assertEqual(
            set(self.cmd("alice", "thread", "mail", "show", mid)["recipients"]), {"bob", "carol"}
        )
        self.assertEqual(self.cmd("bob", "thread", "mail", "show", mid)["recipients"], ["bob"])
        self.assertFalse(self.cmd("bob", "thread", "chat", "--with", "carol")["messages"])
        with self.assertRaises(Error):
            self.cmd("bob", "thread", "reply", mid, "--to", "carol", "--body", "misrouted reply")
        reply = self.cmd("bob", "thread", "reply", mid, "--body", "Thanks")["id"]
        with self.assertRaises(Error):
            self.cmd("carol", "thread", "mail", "show", reply)

    def test_directory_labels_do_not_merge_same_name_in_different_paths(self):
        from cosmesh.agent_projects import active

        tid = self.cmd("alice", "thread", "create", "--title", "Directory test")["id"]
        paths = [self.root / "one" / "project", self.root / "two" / "project"]
        for path in paths:
            path.mkdir(parents=True)
            self.cmd(
                "alice",
                "thread",
                "bind-project",
                tid,
                "--agent",
                "test",
                "--kind",
                "directory",
                "--workdir",
                str(path),
            )
        with self.use("alice") as wb:
            self.assertEqual({row["workdir"] for row in active(wb, tid)}, set(map(str, paths)))

    def test_passive_summary_offline_retry_and_deferred_filing(self):
        tid = self.cmd("bob", "thread", "create", "--title", "Field study")["id"]
        p = self.root / "notes"
        p.mkdir()
        self.cmd("bob", "collect", "add", tid, str(p), "--context-file", "handoff.md", "--personal")
        (p / "handoff.md").write_text("Chosen method and next observation.")
        (p / "data.txt").write_text("input")
        local = self.cmd("bob", "collect", "once", "--offline")
        self.assertTrue(local["scopes"][0]["pending"])
        self.assertTrue(local["scopes"][0]["last_summary"])
        self.assertTrue(self.cmd("bob", "collect", "once")["complete"])
        self.cmd("bob", "thread", "note", tid, "--body", "Separate progress")
        receipt = self.cmd("bob", "collect", "once")
        self.assertEqual(receipt["records"][0]["thread"], tid)
        self.cmd("bob", "thread", "personal", tid, "--off")
        self.cmd("bob", "thread", "note", tid, "--body", "local after disabling")
        self.assertFalse(self.cmd("bob", "collect", "once")["records"])
        mid = self.cmd("alice", "thread", "send", "--to", "bob", "--body", "Dinner?")["id"]
        self.assertEqual(self.cmd("bob", "thread", "inbox")["messages"][0]["recommendations"], [])
        new = self.cmd("alice", "thread", "send", "--to", "bob", "--body", "Dinner?")["id"]
        self.assertNotEqual(mid, new)
        self.cmd("bob", "thread", "mail", "file", mid, "--defer")
        self.assertEqual(
            self.cmd(self.other_device("bob"), "thread", "mail", "show", mid)["filing"], "deferred"
        )


class GatewayV1Tests(unittest.TestCase):
    def test_personal_handoff_and_mailbox_through_real_socket(self):
        import json
        import select
        import signal
        import socket
        import tempfile
        from uuid import uuid4
        from cosmesh.access import system_user
        from cosmesh.hub import Hub
        from cosmesh.spaces import rpc
        from cosmesh.store import atomic

        tmp = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
        with (
            tempfile.TemporaryDirectory(dir=tmp) as folder,
            patch.dict(os.environ, {"COSMESH_DATA_DIR": folder, "COSMESH_PAGE_BACKGROUND": "0"}),
        ):
            root = Path(folder)
            hub = Hub(root / "hub")
            hub.initialize()
            enable_relay(hub)
            hub.initialize_community(root / "library")
            user = system_user()["user"]
            for account in (user, "fixture-peer"):
                hub.spaces(
                    {"method": "community_name", "name": account, "key": "name-" + account}, account
                )
            service = root / "discovery.json"
            atomic(
                service,
                json.dumps(
                    dict(
                        format=1,
                        owner_uid=os.geteuid(),
                        endpoint=uuid4().hex,
                        hosts=[socket.gethostname()],
                        socket=str(root / "run/sock"),
                    )
                ),
            )
            atomic(hub.root / "gateway.json", json.dumps(dict(authority=socket.gethostname())))
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cosmesh",
                    "hub",
                    "run",
                    "--hub-root",
                    str(hub.root),
                    "--service",
                    str(service),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                text=True,
            )
            try:
                self.assertTrue(select.select([proc.stderr], [], [], 10)[0])
                self.assertIn('"ready": true', proc.stderr.readline())

                def cmd(client, *args):
                    return execute(
                        parser("cosmesh").parse_args([*args, "--root", str(client)]), "cosmesh"
                    )[0]

                first, second = root / "first", root / "second"
                for client in (first, second):
                    cmd(client, "thread", "init")
                    cmd(client, "thread", "discover", "--service", str(service))
                tid = cmd(
                    first, "thread", "create", "--title", "Gateway project", "--body", "owner only"
                )["id"]
                cmd(first, "thread", "personal", tid)
                self.assertEqual(
                    cmd(second, "thread", "find", "Gateway project", "--require-fresh")["threads"][0][
                        "thread"
                    ],
                    tid,
                )
                self.assertEqual(cmd(second, "thread", "fetch", tid)["thread"], tid)
                mid = cmd(
                    first, "thread", "send", tid, "--to", "fixture-peer", "--body", "selected result"
                )["id"]
                self.assertEqual(cmd(second, "thread", "outbox")["messages"][0]["id"], mid)
                self.assertEqual(
                    hub.spaces(dict(method="delivery_show", id=mid), "fixture-peer")["body"],
                    "selected result",
                )
                with self.assertRaises(Error):
                    rpc(
                        dict(kind="system", discovery=str(service), repo="spaces"),
                        {"method": "thread_access_set"},
                    )
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
                proc.stderr.close()
