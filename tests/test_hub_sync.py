"""Actual Git pack/hook integration with an isolated SSH stand-in."""
import base64
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmesh.errors import Error
from cosmesh.hub import Hub
from cosmesh.service import Service, initialize
from cosmesh.store import Store
from cosmesh.sync import clone, configure, git_transport, synchronize, url, state


class HubSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hub = Hub(self.root / "hub")
        self.hub.initialize()
        self.entry = self.root / "cosmesh"
        source = str(Path(__file__).resolve().parents[1])
        code = "import sys; sys.path.insert(0,%r); from cosmesh.cli import main; raise SystemExit(main())" % source
        self.entry.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-c", code]) + ' "$@"\n')
        self.entry.chmod(0o700)
        self.hub.create_repo("team", "cosmesh", self.entry, "alice")
        self.hub.grant("team", "bob", "write")
        self.configs, self.keys = {}, {}
        for index, user in enumerate(("alice", "bob"), 1):
            public = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([index]) * 32).decode()
            registration = self.hub.register_key(user, public, self.entry)
            self.keys[user] = registration["key_id"]
            identity, known = self.root / (user + ".key"), self.root / (user + ".known")
            identity.write_text("stand-in"); identity.chmod(0o600)
            known.write_text("stand-in"); known.chmod(0o600)
            wrapper = self.root / (user + "-ssh")
            code = ("import os,sys; sys.path.insert(0,%r); os.environ['SSH_CONNECTION']='isolated-test'; "
                    "os.environ['SSH_ORIGINAL_COMMAND']=sys.argv[-1]; "
                    "from cosmesh.hub import Hub; from cosmesh.errors import Error; "
                    "h=Hub(%r); " % (source, str(self.hub.root)))
            code += "\ntry: raise SystemExit(h.serve(%r))\nexcept Error as e: print('[COSMESH:'+e.code+'] '+e.message,file=sys.stderr); raise SystemExit(1)" % self.keys[user]
            wrapper.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-c", code]) + ' "$@"\n')
            wrapper.chmod(0o700)
            self.configs[user] = {"host": "test-hub", "port": 22, "login": "service", "repo": "team",
                                  "identity": str(identity), "known_hosts": str(known), "host_key_alias": "test-hub"}
            self.configs[user]["wrapper"] = str(wrapper)
        def status(config):
            user = Path(config["identity"]).stem
            return self.hub.status(config["repo"], self.hub.identity(self.keys[user]))
        def git_env(config):
            user = Path(config["identity"]).stem
            return {"GIT_SSH_COMMAND": shlex.quote(self.configs[user]["wrapper"]), "GIT_SSH_VARIANT": "ssh"}
        self.env = git_env
        for name, function in (("remote_status", status), ("git_transport", git_env)):
            item = patch("cosmesh.sync." + name, function)
            item.start(); self.addCleanup(item.stop)
        self.alice = Store(self.root / "alice")
        initialize(self.alice, "cosmesh", "alice", "Asia/Shanghai")
        configure(self.alice, "cosmesh", self.config("alice"))
        self.project = Service(self.alice, "cosmesh").run("project.create", {"title": "合作", "members": ["bob"]})[0]["result"]
        self.task = Service(self.alice, "cosmesh").run("task.create", {"title": "检验", "project": self.project["id"], "invite": ["bob"]})[0]["result"]
        synchronize(self.alice, "cosmesh")

    def config(self, user):
        return {k: v for k, v in self.configs[user].items() if k != "wrapper"}

    def second(self, user="bob", name=None):
        root = self.root / (name or user)
        clone(root, "cosmesh", self.config(user))
        return Store(root)

    def update(self, store, text):
        return Service(store, "cosmesh").run("update.add", {"project": self.project["id"], "title": text, "body": text})[0]["result"]

    def rejected(self, store):
        before = self.hub.status("team", "alice")["revision"]
        result = store.git("push", url(self.config("bob")), "HEAD:refs/heads/main", extra_env=self.env(self.config("bob")), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.hub.status("team", "alice")["revision"], before)
        return result.stderr

    def test_two_identities_append_merge_and_accept_invitation(self):
        bob = self.second()
        self.assertEqual(bob.client()["user"], "bob")
        Service(bob, "cosmesh").run("task.accept", {"id": self.task["id"], "invitation": "initial:" + self.task["id"] + ":bob", "reason": "接下"}, expect_revision=bob.head)
        synchronize(bob, "cosmesh")
        synchronize(self.alice, "cosmesh")
        a, b = self.update(self.alice, "甲的进度"), self.update(bob, "乙的反馈")
        synchronize(self.alice, "cosmesh")
        synchronize(bob, "cosmesh")
        synchronize(self.alice, "cosmesh")
        documents = self.alice.documents(self.alice.snapshot()[1])
        self.assertEqual(documents[b["id"]]["meta"]["author"], "bob")
        self.assertIn(a["id"], documents)
        self.assertEqual(self.alice.head, bob.head)
        self.assertEqual(bob.git("status", "--porcelain"), "")

    def test_offline_cached_writes_rejoin(self):
        bob = self.second()
        accepted = bob.head
        with patch('cosmesh.sync.remote_status', side_effect=Error('SYNC_NETWORK', 'offline')):
            with self.assertRaises(Error):
                synchronize(bob, 'cosmesh')
            service = Service(bob, 'cosmesh')
            cached, _ = service.run('context', {'id': self.project['id']})
            self.assertIn('document', cached)
            task = service.run('task.create', {'title': '离线行动', 'project': self.project['id']})[0]['result']
            note = self.update(bob, '离线进展')
            pending = bob.head
            self.assertNotEqual(pending, accepted)
            self.assertGreater(state(bob)['unpushed_count'], 0)
            self.assertEqual(state(bob)['state'], 'offline')
            with self.assertRaises(Error):
                synchronize(bob, 'cosmesh')
            self.assertEqual(bob.head, pending)
        synchronize(bob, 'cosmesh')
        synchronize(self.alice, 'cosmesh')
        self.assertEqual(state(bob)['state'], 'synced')
        documents = self.alice.documents(self.alice.snapshot()[1])
        self.assertIn(task['id'], documents)
        self.assertIn(note['id'], documents)

    def test_forged_author_is_rejected_by_real_pre_receive(self):
        bob = self.second()
        local = bob.client(); local["user"] = "alice"; bob.save_client(local)
        self.update(bob, "冒用署名")
        self.assertIn("AUTH_DENIED", self.rejected(bob))

    def test_intermediate_history_tampering_is_rejected(self):
        update = self.update(self.alice, "原始记录")
        synchronize(self.alice, "cosmesh")
        bob = self.second()
        path = "documents/" + update["id"] + ".md"
        original = bob.path(path).read_text()
        bob.commit({path: original.replace("原始记录", "篡改记录")}, "tamper")
        bob.commit({path: original}, "restore")
        self.assertIn("HISTORY_PROTECTED", self.rejected(bob))

    def test_control_conflict_retains_clean_local_commits(self):
        other = self.second("alice", "alice-other")
        Service(self.alice, "cosmesh").run("task.state", {"id": self.task["id"], "to": "in_progress", "reason": "开始"}, expect_revision=self.alice.head)
        Service(other, "cosmesh").run("task.state", {"id": self.task["id"], "to": "cancelled", "reason": "取消"}, expect_revision=other.head)
        before = other.head
        synchronize(self.alice, "cosmesh")
        with self.assertRaises(Error) as caught:
            synchronize(other, "cosmesh")
        self.assertEqual(caught.exception.code, "SYNC_CONFLICT")
        self.assertEqual(other.head, before)
        self.assertEqual(other.git("status", "--porcelain"), "")
        self.assertTrue(other.path(".cosmesh/conflict.json").exists())

    def test_read_only_acl_private_cosbase_and_key_revocation(self):
        bob = self.second()
        self.hub.grant("team", "bob", "read")
        self.update(bob, "无写权限")
        self.assertIn("AUTH_DENIED", self.rejected(bob))
        self.hub.create_repo("private", "cosbase", self.entry, "alice")
        with self.assertRaises(Error):
            self.hub.grant("private", "bob", "read")
        self.hub.revoke_key(self.keys["bob"])
        with self.assertRaises(Error):
            self.hub.identity(self.keys["bob"])

    def test_forced_command_denies_shell_and_unknown_repo(self):
        with patch.dict(os.environ, {"SSH_CONNECTION": "test", "SSH_ORIGINAL_COMMAND": "cat /etc/passwd"}):
            with self.assertRaises(Error) as caught:
                self.hub.serve(self.keys["bob"])
            self.assertEqual(caught.exception.code, "COMMAND_DENIED")
        with patch.dict(os.environ, {"SSH_CONNECTION": "test", "SSH_ORIGINAL_COMMAND": "git-upload-pack ../private.git"}):
            with self.assertRaises(Error) as caught:
                self.hub.serve(self.keys["bob"])
            self.assertEqual(caught.exception.code, "PATH_INVALID")


if __name__ == "__main__":
    unittest.main()
