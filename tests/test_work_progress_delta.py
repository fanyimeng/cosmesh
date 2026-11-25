"""Compact wire updates use the ordinary authenticated immutable personal snapshots."""
import unittest
from unittest.mock import patch

from cosmesh.errors import Error
from cosmesh.personal_protocol import digest, packed
from cosmesh.personal_relay import Relay
from tests import test_thread_personal as fixture


class WorkProgressDeltaTests(unittest.TestCase):
    setUp = fixture.ThreadPersonalTests.setUp
    work = fixture.ThreadPersonalTests.work
    use = fixture.ThreadPersonalTests.use

    def test_small_change_and_lost_ack_match_full_snapshot(self):
        with self.use(self.a) as client:
            client.wb.append(self.t, "historical observation " * 2048)
            client.wb.load()
            base = client.synchronize(self.t, compact=True)["accepted_snapshot"]
            note = client.wb.append(self.t, "second night")
            client.wb.load()
            send = client.rpc
            deltas = []

            def lose_reply(request):
                reply = send(request)
                if request["method"] == "personal_thread_delta":
                    deltas.append(request)
                    raise Error("SYNC_NETWORK", "lost a test reply")
                return reply

            with patch.object(client, "rpc", side_effect=lose_reply), patch.object(client, "upload", side_effect=AssertionError):
                with self.assertRaisesRegex(Error, "lost a test reply"):
                    client.synchronize(self.t, compact=True)
            self.assertEqual(len(deltas), 1)
            delta = deltas[0]
            self.assertEqual(delta["expect"], base)
            self.assertNotIn("historical observation", packed(delta["changes"]))
            accepted = client.synchronize(self.t, compact=True)["accepted_snapshot"]
            self.assertEqual(accepted, delta["sha"])
            replay = send(delta)
            self.assertTrue(replay["replayed"])
            manifest = client.load_manifest(accepted, self.t)
            self.assertEqual(digest(packed(manifest).encode()), accepted)
            self.assertLess(len(packed(delta).encode()), len(packed(manifest).encode()) // 10)
            self.assertIn("documents/" + note["id"] + ".md", manifest["documents"])
            self.assertTrue(any("historical observation" in text for text in manifest["history"].values()))
            print("Verified metadata bytes (whole snapshot / delta request):",
                  len(packed(manifest).encode()), len(packed(delta).encode()))
            for wrong in [dict(delta, sha="0" * 64),
                          dict(delta, expect=accepted, sha="0" * 64)]:
                with self.assertRaises(Error):
                    send(wrong)
            self.assertEqual(send(dict(method="personal_thread_head", thread=self.t))["sha"], accepted)
            with self.assertRaises(Error):
                Relay(self.hub, "bob").dispatch(delta)
        with self.use(self.b) as client:
            result = client.synchronize(self.t, pull=True)
            self.assertEqual(result["accepted_snapshot"], accepted)

    def test_old_peer_uses_chunked_transfer(self):
        with self.use(self.a) as client:
            client.synchronize(self.t)
            client.wb.append(self.t, "new note")
            client.wb.load()
            send = client.rpc
            methods = []
            def older(request):
                methods.append(request["method"])
                result = send(request)
                if request["method"] == "personal_info":
                    result.pop("delta_protocol", None)
                return result
            with patch.object(client, "rpc", side_effect=older):
                result = client.synchronize(self.t, compact=True)
            self.assertTrue(result["accepted_snapshot"])
            self.assertIn("personal_put", methods)
            self.assertNotIn("personal_thread_delta", methods)
