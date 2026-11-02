import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmesh.errors import Error
from cosmesh.protocol import StrictLoader, decode, encode, new_id
from cosmesh.service import Service, initialize
from cosmesh.skills import install
from cosmesh.store import Store, atomic

import yaml


class LocalCase(unittest.TestCase):
    product = "cosbase"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cosmesh-test-", dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "store"
        self.store = Store(self.root)
        initialize(self.store, self.product, "alice", "Asia/Shanghai")
        self.service = Service(self.store, self.product)

    def run_op(self, action, payload=None, **kwargs):
        return self.service.run(action, payload, **kwargs)

    def create(self, kind="task", **kwargs):
        payload = {"title": "测试 " + kind, "body": "原始正文\n\n有出处的记录。", **kwargs}
        return self.run_op(kind + (".add" if kind == "update" else ".create"), payload)[0]["result"]

    def show(self, doc):
        return self.run_op("doc.show", {"id": doc["id"]})[0]

    def edit(self, doc, **kwargs):
        return self.run_op("doc.edit", {"id": doc["id"], "expect": doc["version"], **kwargs})[0]["result"]["document"]

    def lifecycle(self, action, doc):
        return self.run_op("doc." + action, {"id": doc["id"], "expect": doc["version"]})[0]["result"]["document"]

    def assertCode(self, code, callback):
        with self.assertRaises(Error) as error:
            callback()
        self.assertEqual(error.exception.code, code)


class PersonalTests(LocalCase):
    def test_independent_update_and_default_project(self):
        update = self.create("update")
        self.assertNotIn("project", update)
        first, second = self.create(), self.create()
        self.assertEqual(first["project"], second["project"])
        self.assertEqual(len(self.run_op("doc.list", {"kind": "project"})[0]), 1)

    def test_six_conversions_preserve_original_and_links(self):
        for source_kind in ("project", "task", "update"):
            for target_kind in ("project", "task", "update"):
                if source_kind == target_kind:
                    continue
                with self.subTest(source=source_kind, target=target_kind):
                    original = self.create(source_kind)
                    result = self.run_op("doc.convert", {"id": original["id"], "expect": original["version"], "to": target_kind})[0]["result"]
                    source, target = result["source"], result["target"]
                    self.assertEqual(source["lifecycle"], "archived")
                    self.assertEqual(source["body"], original["body"])
                    self.assertEqual(target["body"], original["body"])
                    self.assertEqual(target["sources"], [source["id"]])
                    self.assertIn(target["id"], source["converted_to"])
                    self.assertNotEqual(target["id"], source["id"])

    def test_derive_keeps_source_active(self):
        source = self.create("update")
        result = self.run_op("doc.derive", {"id": source["id"], "expect": source["version"], "to": "task"})[0]["result"]
        self.assertEqual(self.show(source)["lifecycle"], "active")
        self.assertEqual(result["target"]["sources"], [source["id"]])

    def test_empty_body_task_can_become_a_record(self):
        source = self.create(body="")
        target = self.run_op("doc.convert", {"id": source["id"], "expect": source["version"], "to": "update"})[0]["result"]["target"]
        self.assertIn(source["title"], target["body"])
        self.assertEqual(self.show(source)["body"], "")

    def test_archive_trash_restore_keeps_previous_lifecycle(self):
        doc = self.create()
        doc = self.lifecycle("archive", doc)
        doc = self.lifecycle("trash", doc)
        restored = self.lifecycle("restore", doc)
        self.assertEqual(restored["id"], doc["id"])
        self.assertEqual(restored["lifecycle"], "archived")

    def test_project_lifecycle_does_not_hide_open_child_dates(self):
        project = self.create("project")
        task = self.create(project=project["id"], due="2026-10-30", followup="2026-10-30")
        project = self.lifecycle("archive", project)
        agenda = self.run_op("agenda", {"from": "2026-10-30", "to": "2026-10-30"})[0]
        self.assertEqual({x["kind"] for x in agenda}, {"due", "followup"})
        self.assertTrue(all(x["project_lifecycle"] == "archived" for x in agenda))
        self.lifecycle("trash", project)
        self.assertEqual(self.show(task)["lifecycle"], "active")
        context = self.run_op("context", {"id": task["id"]})[0]
        self.assertEqual(context["project"], {"id": project["id"], "lifecycle": "trash"})

    def test_finish_is_distinct_from_archive(self):
        task = self.create(due="2026-10-30")
        result = self.run_op("task.finish", {"id": task["id"], "expect": task["version"], "outcome": "cancelled"})[0]["result"]["document"]
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["lifecycle"], "active")
        self.assertEqual(self.run_op("agenda", {"from": "2026-10-30", "to": "2026-10-30"})[0], [])

    def test_correction_keeps_update_body(self):
        original = self.create("update")
        self.assertCode("POLICY_DENIED", lambda: self.edit(original, body="覆盖原文"))
        correction = self.create("update", type="correction", supersedes=original["id"])
        self.assertEqual(self.show(original)["body"], original["body"])
        context = self.run_op("context", {"id": original["id"]})[0]
        self.assertIn(correction["id"], [x["id"] for x in context["related"]])

    def test_versions_reject_lost_updates(self):
        original = self.create()
        changed = self.edit(original, next_action="下一步 A")
        self.assertCode("STATE_CONFLICT", lambda: self.edit(original, next_action="下一步 B"))
        self.assertEqual(self.show(changed)["next_action"], "下一步 A")

    def test_retry_returns_original_result_even_after_later_edits(self):
        payload = {"title": "可重试", "body": "保留"}
        first, revision = self.run_op("task.create", payload, key="one-request")
        self.edit(first["result"], next_action="后来推进")
        retry, retry_revision = self.run_op("task.create", payload, key="one-request")
        self.assertTrue(retry["replayed"])
        self.assertEqual(retry["result"], first["result"])
        self.assertEqual(retry_revision, revision)
        self.assertCode("IDEMPOTENCY_CONFLICT", lambda: self.run_op("task.create", {"title": "另一个请求"}, key="one-request"))

    def test_dry_run_and_invalid_date_do_not_write(self):
        head = self.store.head
        result, _ = self.run_op("task.create", {"title": "预览"}, dry_run=True)
        self.assertTrue(result["preview"])
        self.assertEqual(head, self.store.head)
        self.assertCode("INPUT_INVALID", lambda: self.create(due="2026-02-30"))
        self.assertEqual(head, self.store.head)

    def test_historical_context_survives_new_process_and_edits(self):
        original = self.create()
        snapshot = self.store.head
        self.edit(original, title="新的标题")
        old = Service(Store(self.root), "cosbase").run("context", {"id": original["id"]}, revision=snapshot)[0]
        self.assertEqual(old["document"]["title"], original["title"])
        self.assertEqual(old["source_revision"], snapshot)

    def test_wrong_product_and_missing_preconditions(self):
        self.assertCode("PRODUCT_MISMATCH", lambda: Service(self.store, "cosmesh").run("doc.list"))
        task = self.create()
        self.assertCode("PRECONDITION_REQUIRED", lambda: self.run_op("doc.trash", {"id": task["id"]}))

    def test_manual_dirty_file_is_preserved(self):
        doc = self.create()
        path = self.root / "documents" / (doc["id"] + ".md")
        path.write_text("人工修改")
        self.assertCode("STORE_DIRTY", lambda: self.run_op("doc.list"))
        self.assertEqual(path.read_text(), "人工修改")

    def test_failed_commit_rolls_back_multi_document_conversion(self):
        source = self.create()
        before = self.store.head
        original_git = self.store.git
        def fail_commit(*args, **kwargs):
            if args and args[0] == "commit":
                raise Error("GIT_FAILED", "Injected commit failure")
            return original_git(*args, **kwargs)
        with patch.object(self.store, "git", side_effect=fail_commit):
            self.assertCode("GIT_FAILED", lambda: self.run_op("doc.convert", {"id": source["id"], "expect": source["version"], "to": "project"}))
        self.assertEqual(self.store.head, before)
        self.assertEqual(self.show(source)["lifecycle"], "active")
        self.assertFalse(self.store.pending.exists())

    def test_interrupted_transaction_recovers_without_touching_manual_changes(self):
        doc = self.create()
        path = "documents/" + doc["id"] + ".md"
        before = (self.root / path).read_text()
        after = before.replace(doc["title"], "尚未完成的修改")
        journal = {"base": self.store.head, "message": "incomplete", "before": {path: before}, "after": {path: after}}
        atomic(self.store.pending, json.dumps(journal))
        atomic(self.root / path, after)
        self.assertEqual(self.show(doc)["title"], doc["title"])
        atomic(self.store.pending, json.dumps(journal))
        atomic(self.root / path, "人工额外修改")
        self.assertCode("RECOVERY_REQUIRED", lambda: self.show(doc))
        self.assertEqual((self.root / path).read_text(), "人工额外修改")

    def test_symlinked_document_is_rejected(self):
        doc = self.create()
        path = self.root / "documents" / (doc["id"] + ".md")
        outside = Path(self.tmp.name) / "outside"
        outside.write_text("untouched")
        path.unlink()
        path.symlink_to(outside)
        self.assertCode("STORE_DIRTY", lambda: self.run_op("doc.list"))
        self.assertEqual(outside.read_text(), "untouched")


class MeshTests(LocalCase):
    product = "cosmesh"

    def project_task(self, invite=True):
        project = self.create("project", members=["bob"])
        task = self.create(project=project["id"], invite=["bob"] if invite else [])
        return project, task

    def as_user(self, user):
        # Explicit local test simulation; no claim of authenticated identity.
        with self.store.locked():
            _, files = self.store.snapshot()
            config = json.loads(files[".cosmesh/config.json"])
            config["user"] = user
            self.store.commit({".cosmesh/config.json": json.dumps(config, indent=2) + "\n"}, "test-local-user-" + user)
            client = self.store.client()
            client["user"] = user
            self.store.save_client(client)

    def control(self, action, task, **payload):
        return self.run_op("task." + action, {"id": task["id"], **payload}, expect_revision=self.store.head)

    def test_pending_acceptance_and_member_progress(self):
        project, task = self.project_task()
        self.as_user("bob")
        self.assertCode("AUTH_DENIED", lambda: self.create("update", project=project["id"], task=task["id"]))
        inbox = self.run_op("inbox")[0]
        invitation = inbox["invitations"][0]["invitation"]
        self.control("accept", task, invitation=invitation)
        self.create("update", project=project["id"], task=task["id"])
        self.assertEqual(self.show(task)["derived"]["participants"]["bob"], "accepted")
        self.assertCode("AUTH_DENIED", lambda: self.control("state", task, to="in_progress", reason="成员尝试"))
        self.assertCode("STATE_CONFLICT", lambda: self.control("decline", task, invitation=invitation))

    def test_lead_cannot_accept_for_another_user(self):
        _, task = self.project_task()
        invitation = "initial:" + task["id"] + ":bob"
        self.assertCode("AUTH_DENIED", lambda: self.control("accept", task, invitation=invitation))

    def test_status_and_schedule_are_derived_append_only(self):
        _, task = self.project_task()
        for target in ("in_progress", "review", "done", "in_progress"):
            self.control("state", task, to=target, reason="明确动作")
        self.control("schedule", task, due="2026-11-15", followup="2026-10-30")
        shown = self.show(task)
        self.assertEqual(shown["derived"]["status"], "in_progress")
        self.assertEqual(shown["derived"]["due"], "2026-11-15")
        self.assertEqual(shown["version"], task["version"])
        self.control("schedule", task, followup=None)
        self.assertIsNone(self.show(task)["derived"]["followup"])

    def test_stale_control_context_is_rejected(self):
        _, task = self.project_task()
        revision = self.store.head
        self.control("state", task, to="in_progress", reason="先推进")
        self.assertCode("STATE_CONFLICT", lambda: self.run_op("task.state", {"id": task["id"], "to": "cancelled", "reason": "旧上下文"}, expect_revision=revision))
        self.assertCode("PRECONDITION_REQUIRED", lambda: self.run_op("task.state", {"id": task["id"], "to": "review", "reason": "无前提"}))

    def test_mesh_personal_operations_are_denied(self):
        project, task = self.project_task()
        update = self.create("update", project=project["id"], task=task["id"])
        for action in ("archive", "trash", "convert"):
            self.assertCode("POLICY_DENIED", lambda action=action: self.run_op("doc." + action, {"id": update["id"], "expect": update["version"], "to": "task"}))
        self.assertCode("POLICY_DENIED", lambda: self.edit(update, body="篡改原始进度"))
        self.assertCode("POLICY_DENIED", lambda: self.edit(task, due="2026-12-01"))

    def test_declined_invitation_needs_new_invite(self):
        _, task = self.project_task()
        self.as_user("bob")
        initial = "initial:" + task["id"] + ":bob"
        self.control("decline", task, invitation=initial)
        self.assertCode("STATE_CONFLICT", lambda: self.control("accept", task, invitation=initial))
        self.as_user("alice")
        invite = self.control("invite", task, to="bob")[0]["result"]
        self.as_user("bob")
        self.control("accept", task, invitation=invite["id"])
        self.assertEqual(self.show(task)["derived"]["participants"]["bob"], "accepted")


class ProtocolTests(unittest.TestCase):
    def test_yaml_duplicate_keys_aliases_tags_and_depth_are_rejected(self):
        for payload in ("a: 1\na: 2", "a: &x [1]\nb: *x", "a: !!python/object:os.system {}", "a: " + "[" * 30 + "0" + "]" * 30):
            with self.subTest(payload=payload), self.assertRaises((Error, yaml.YAMLError)):
                yaml.load(payload, Loader=StrictLoader)

    def test_filename_and_schema_are_checked(self):
        doc = {"meta": {"schema_version": 1, "id": new_id("update"), "kind": "update", "title": "标题",
                        "author": "alice", "created_at": "2026-10-27T00:00:00+00:00", "updated_at": "2026-10-27T00:00:00+00:00",
                        "lifecycle": "active"}, "body": "正文"}
        text = encode(doc)
        with self.assertRaises(Error):
            decode(text, new_id("task") + ".md")
        with self.assertRaises(Error):
            decode(text.replace("schema_version: 1", "schema_version: 999"))


class CLITests(LocalCase):
    def cli(self, *args, product="cosbase", expected=0):
        code = "from cosmesh.cli import main; raise SystemExit(main(product=%r))" % product
        result = subprocess.run([sys.executable, "-c", code, "--root", str(self.root), *args],
                                text=True, capture_output=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_json_cli_cross_process_and_arguments_after_subcommands(self):
        created = self.cli("task", "create", "--title", "中文事项", "--followup", "2026-10-30", "--json", "--idempotency-key", "cli-one")
        doc = created["data"]["result"]
        context = self.cli("context", doc["id"], "--json")
        self.assertEqual(context["data"]["document"]["title"], "中文事项")
        self.assertFalse(context["sync"]["identity_verified"])
        self.assertIsNone(context["sync"]["remote_revision"])
        changed = self.cli("doc", "edit", doc["id"], "--expect", doc["version"], "--clear-followup", "--json")
        self.assertIsNone(changed["data"]["result"]["document"]["followup"])
        self.cli("doc", "trash", doc["id"], "--expect", doc["version"], "--json", expected=4)

    def test_parse_errors_and_unavailable_capabilities_are_json(self):
        self.assertEqual(self.cli("unknown", "--json", expected=2)["error"]["code"], "INPUT_INVALID")
        self.assertEqual(self.cli("sync", "--json", expected=2)["error"]["code"], "FRESHNESS_UNAVAILABLE")
        self.assertEqual(self.cli("doc", "list", "--json", "--require-fresh", expected=2)["error"]["code"], "FRESHNESS_UNAVAILABLE")

    def test_managed_application_directory_cannot_hold_documents(self):
        with patch.dict(os.environ, {"COSMESH_INSTALL_PREFIX": self.tmp.name}):
            self.assertEqual(self.cli("doc", "list", "--json", expected=2)["error"]["code"], "PATH_INVALID")

    def test_both_agent_skill_installs_preserve_existing_files(self):
        for agent in ("codex", "opencode"):
            target = Path(self.tmp.name) / agent
            preview = install(agent, ["cosbase", "cosmesh"], target, dry_run=True)
            self.assertFalse(target.exists())
            self.assertEqual(preview["host_discovery"], "not_verified")
            install(agent, ["cosbase", "cosmesh"], target)
            repeated = install(agent, ["cosbase", "cosmesh"], target)
            self.assertTrue(all(x["status"] == "installed_identical" for x in repeated["skills"]))
            existing = target / "cosbase/SKILL.md"
            existing.write_text("用户自己的版本")
            self.assertCode("OVERWRITE_REFUSED", lambda: install(agent, ["cosbase", "cosmesh"], target))
            self.assertEqual(existing.read_text(), "用户自己的版本")

    def test_two_processes_retry_one_logical_create(self):
        code = "from cosmesh.cli import main; raise SystemExit(main(product='cosbase'))"
        command = [sys.executable, "-c", code, "--root", str(self.root), "task", "create",
                   "--title", "并发请求", "--idempotency-key", "same-logical-action", "--json"]
        children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1")) for _ in range(2)]
        replies = []
        for child in children:
            out, err = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, out + err)
            replies.append(json.loads(out)["data"])
        self.assertEqual(replies[0]["result"]["id"], replies[1]["result"]["id"])
        self.assertEqual(sum(reply["replayed"] for reply in replies), 1)
        self.assertEqual(len(self.run_op("doc.list", {"kind": "task"})[0]), 1)


if __name__ == "__main__":
    unittest.main()
