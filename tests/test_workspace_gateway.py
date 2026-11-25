"""Exercise the installed CLI and its background publisher against an isolated Unix Hub."""
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

from cosmesh.errors import Error


@unittest.skipIf(os.name == "nt", "The test Hub uses a POSIX socket; Windows is a client.")
class WorkspaceGatewayTests(unittest.TestCase):
    def test_detached_publisher_and_pinned_recipient_view(self):
        from cosmesh.access import system_user
        from cosmesh.hub import Hub
        from cosmesh.personal_relay import initialize
        from cosmesh.store import atomic

        with tempfile.TemporaryDirectory(prefix="cmw-", dir="/tmp") as temp:
            root = Path(temp).resolve()
            env = dict(os.environ, COSMESH_DATA_DIR=str(root / "data"),
                       COSMESH_PROFILE_DIR=str(root / "profile"), COSMESH_PAGE_BACKGROUND="0",
                       COSMESH_PAGE_CACHE=str(root / "pages"), COSMESH_HUB_PAGE_CACHE=str(root / "hub-pages"))
            hub = Hub(root / "hub")
            hub.initialize()
            initialize(hub)
            hub.initialize_community(root / "library")
            owner = system_user()["user"]
            peer = "workspace-fixture-reader"
            for user in [owner, peer]:
                hub.spaces({"method": "community_name", "name": user, "key": "name-" + user}, user)
            service = root / "service.json"
            atomic(service, json.dumps(dict(format=1, owner_uid=os.geteuid(), endpoint=uuid4().hex,
                                            hosts=[socket.gethostname()], socket=str(root / "sock/run"))))
            atomic(hub.root / "gateway.json", json.dumps(dict(authority=socket.gethostname())))

            def cli(*args):
                command = [sys.executable, "-m", "cosmesh", "--root", str(root / "store"), *args, "--json"]
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, str(args[:2]) + (result.stdout + result.stderr)[-1800:])
                return json.loads(result.stdout)["data"]

            def read(method, **params):
                return hub.spaces(dict(method="delivery_workspace_" + method, id=mid, **params), peer)

            proc = subprocess.Popen([sys.executable, "-m", "cosmesh", "hub", "run", "--hub-root",
                                     str(hub.root), "--service", str(service)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
            try:
                self.assertTrue(select.select([proc.stderr], [], [], 10)[0])
                self.assertIn('"ready": true', proc.stderr.readline())
                cli("thread", "init")
                cli("thread", "discover", "--service", str(service))
                tid = cli("thread", "create", "--title", "测试目标", "--body", "第一版定义")["id"]
                anchor = cli("thread", "note", tid, "--body", "首轮进展")["id"]
                receipt = cli("thread", "send", tid, "--to", peer, "--body", "请看进展", "--share-workspace")
                mid = receipt["id"]
                self.assertEqual(read("open", at_send=True)["body"].strip(), "第一版定义")
                old = read("records", at_send=True)
                self.assertIn(anchor, [r["id"] for r in old["records"]])
                update = cli("thread", "note", tid, "--body", "后台自动同步")["id"]
                deadline = time.monotonic() + 35
                while True:
                    latest = read("records")
                    if update in {r["id"] for r in latest["records"]}:
                        break
                    if time.monotonic() >= deadline:
                        self.fail("Background sync did not deliver the new record: " + str(cli("collect", "status")))
                    time.sleep(0.5)
                self.assertNotEqual(latest["revision"], old["revision"])
                self.assertEqual(read("open", record=update)["body"].strip(), "后台自动同步")
                self.assertEqual(read("records", at_send=True), old)
                cli("thread", "mail", "unshare", tid, "--from", peer)
                for params in ({}, {"at_send": True}, {"revision": latest["revision"]}):
                    with self.assertRaises(Error):
                        read("records", **params)
                self.assertEqual(hub.spaces(dict(method="delivery_show", id=mid), peer)["body"], "请看进展")
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
                proc.stderr.close()
