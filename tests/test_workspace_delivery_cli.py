"""Outbound consent and replay, plus pinned multi-chunk workspace downloads."""

import base64
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from cosmesh.cli import parser
from cosmesh.errors import Error
from cosmesh import workspace_delivery_cli as client


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / ".git").mkdir()
        self.wb = SimpleNamespace(base=SimpleNamespace(root=self.root))
        self.online = Mock()
        self.online.info = {"workspace_protocol": "workspace.v1", "hub_id": "hub", "person": {"id": "alice"}}
        self.payload = dict(work="work", to=["recipient"], title="", body="message", files=[], sources=[], parent=None)
        self.signature = "3" * 64
        self.ticket = {"thread": "work", "snapshot": "1" * 64, "hub": "hub", "actor": "alice", "cursor": {}}
        self.status = {"granted": [], "missing": ["bob"]}
        self.args = SimpleNamespace(share_workspace=False, message_only=False, json=False)

    def deliver(self, key=None):
        return client.deliver(self.wb, self.online, self.args, self.payload, self.signature,
                              key, "Survey", [], self.root)

    def test_prompt_cancel_is_read_only_and_describes_grant(self):
        terminal = MagicMock()
        terminal.isatty.return_value = True
        terminal.readline.side_effect = ["unrecognized\n", "\n"]
        output = io.StringIO()
        output.isatty = lambda: True
        self.online.call.return_value = self.status
        with patch.object(client.sys, "stdin", terminal), patch.object(client.sys, "stderr", output):
            with self.assertRaisesRegex(Error, "取消"):
                self.deliver()
        self.assertIn("bob", output.getvalue())
        self.assertIn("Survey", output.getvalue())
        self.assertIn("后续", output.getvalue())
        self.assertEqual(client.pending(self.wb, self.online)["pending"], [])
        self.assertEqual([c.args[0] for c in self.online.call.call_args_list], ["delivery_workspace_status"])

    def test_unattended_first_contact_sends_only_message_and_preserves_choice_on_replay(self):
        self.args.json = True
        self.online.call.side_effect = [self.status, {"id": "M", "accepted": True},
                                        {"id": "M", "accepted": True, "replayed": True}]
        self.deliver("known-key")
        result = self.deliver("known-key")
        self.assertTrue(result["replayed"])
        self.assertEqual([c.args[0] for c in self.online.call.call_args_list],
                         ["delivery_workspace_status", "delivery_send", "delivery_send"])
        self.assertEqual(self.online.call.call_args.kwargs["to"], ["bob"])

    def test_old_hub_rejects_share_before_any_upload(self):
        self.online.info.pop("workspace_protocol")
        self.args.share_workspace = True
        with self.assertRaises(Error) as error:
            self.deliver()
        self.assertEqual(error.exception.code, "FEATURE_UNAVAILABLE")
        self.online.call.assert_not_called()

    def test_lost_acceptance_pins_original_snapshot_and_reuses_consent(self):
        self.args.share_workspace = True
        requests = []
        saved = {"id": "M", "accepted": True, "workspace": {"thread": "work", "revision": "2" * 64}}

        def call(method, **params):
            if method == "delivery_workspace_status":
                return self.status
            self.assertEqual(method, "delivery_workspace_send")
            requests.append(params)
            if len(requests) == 1:
                raise Error("OFFLINE", "lost response")
            return dict(saved, replayed=True)

        self.online.call.side_effect = call
        with patch("cosmesh.workspace_progress.prepare_workspace", return_value=self.ticket) as prepare, \
             patch("cosmesh.workspace_progress.arm_workspace") as arm:
            with self.assertRaisesRegex(Error, "lost"):
                self.deliver("chosen")
            pending = client.pending(self.wb, self.online)["pending"]
            self.assertEqual(len(pending), 1)
            arm.assert_not_called()
            client.pending(self.wb, self.online, pending[0]["operation"])
            self.assertTrue(self.deliver("chosen")["replayed"])
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(arm.call_count, 1)
        self.assertEqual(requests, [requests[0]] * 3)
        self.assertEqual(requests[0]["allow_new"], ["bob"])
        self.assertEqual(requests[0]["snapshot"], self.ticket["snapshot"])
        self.assertEqual(client.pending(self.wb, self.online)["pending"], [])

    def test_existing_grant_is_reused_and_cannot_regrant(self):
        self.status = {"granted": ["bob"], "missing": []}
        self.online.call.side_effect = [self.status, {"accepted": True, "workspace": {"thread": "work"}}]
        with patch("cosmesh.workspace_progress.prepare_workspace", return_value=self.ticket), \
             patch("cosmesh.workspace_progress.arm_workspace"):
            self.deliver()
        self.assertFalse(self.online.call.call_args.kwargs["grant"])
        self.assertEqual(self.online.call.call_args.kwargs["allow_new"], [])

    def test_download_pins_revision_and_cleans_partial_after_access_ends(self):
        full = b"ab"
        sha = hashlib.sha256(full).hexdigest()
        output = self.root / "download"
        args = parser("cosmesh", "thread").parse_args(
            ["thread", "mail", "open", "M", "--download", "A", "--output", str(output), "--at-send"])
        revision = "f" * 64

        def read(method, **params):
            self.assertEqual(method, "delivery_workspace_file")
            if params["offset"] == 1:
                self.assertEqual(params["revision"], revision)
                self.assertNotIn("at_send", params)
                raise Error("AUTH_DENIED", "permission withdrawn")
            self.assertTrue(params["at_send"])
            return {"revision": revision, "sha": sha, "size": 2, "offset": 0,
                    "data": base64.b64encode(b"a").decode()}

        self.online.call.side_effect = read
        with self.assertRaisesRegex(Error, "withdrawn"):
            client.open_workspace(self.online, args)
        self.assertFalse(output.exists())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [".git"])


class DeliveryReplayIntegration(unittest.TestCase):
    def test_recovery_after_hub_accepted_and_owner_revoked(self):
        from cosmesh.thread_access_cli import Online
        from tests.test_work_v1 import WorkV1Tests

        fixture = WorkV1Tests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cmd = fixture.cmd
        work = cmd("alice", "thread", "create", "--title", "Replay project", "--body", "initial")["id"]
        # An interactive first send has no explicit --share-workspace flag on retries.
        args = ["thread", "send", work, "--to", "bob", "--body", "first grant",
                "--idempotency-key", "lost-ack"]
        request = Online.call

        def lose_response(online, method, **payload):
            result = request(online, method, **payload)
            if method == "delivery_workspace_send":
                raise Error("OFFLINE", "response lost after commit")
            return result

        with patch.object(Online, "call", lose_response), patch.object(client, "choose", return_value=True):
            with self.assertRaisesRegex(Error, "lost after commit"):
                cmd("alice", *args)
        with patch.object(client, "supported", return_value=False):
            with self.assertRaises(Error) as downgraded:
                cmd("alice", *args)
        self.assertEqual(downgraded.exception.code, "FEATURE_UNAVAILABLE")
        mid = cmd("bob", "thread", "inbox")["messages"][0]["id"]
        self.assertEqual(cmd("bob", "thread", "mail", "open", mid)["body"], "initial")
        cmd("alice", "thread", "mail", "unshare", work, "--from", "bob")
        with self.assertRaises(Error):
            cmd("bob", "thread", "mail", "open", mid, "--at-send")
        cmd("alice", "thread", "note", work, "--body", "later local work")
        interrupted = cmd("alice", "thread", "mail", "pending")["pending"]
        replay = cmd("alice", "thread", "mail", "resume", interrupted[0]["operation"])
        self.assertEqual(replay["id"], mid)
        self.assertTrue(replay["replayed"])
        self.assertNotIn("workspace", replay)
        self.assertEqual(cmd("alice", *args)["id"], mid)
        self.assertEqual(cmd("alice", "thread", "mail", "pending")["pending"], [])
        self.assertEqual(cmd("alice", "thread", "mail", "sharing", work, "--to", "bob")["missing"], ["bob"])
        self.assertEqual(len(cmd("bob", "thread", "inbox")["messages"]), 1)
