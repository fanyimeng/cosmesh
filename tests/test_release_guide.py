"""The human and Agent receipts have the same guide for first install and upgrade."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cosmesh import __version__
from cosmesh.cli import main
from cosmesh.guide import show
from tools import release_installer as release
from tools import remote_bootstrap
from tests.test_portable_release import bootstrap


class ReleaseGuideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.document = show("upgrade")["text"]

    def test_first_upgrade_same_version_and_preview(self):
        prefix = self.root / "app"
        package = self.root / "package"
        package.mkdir()
        (package / "install.py").write_bytes(Path(release.__file__).read_bytes())
        with zipfile.ZipFile(package / "cosmesh.whl", "w") as wheel:
            wheel.writestr("cosmesh/manual/upgrade.md", self.document)
        meta = dict(version=__version__, wheel="cosmesh.whl", manifest_sha256="a" * 64, schema_write=[1, 2, 3])

        def apply(action, dry_run=False):
            return release.apply_release(prefix, package, action, [], self.root / "skills", dry_run)

        with patch.object(release, "bundle", return_value=(package, meta)), patch.object(release, "prepare"), \
                patch.object(release, "base_interpreter", return_value=Path(sys.executable)):
            self.assertNotIn("update_notice", apply("install", True))
            first = apply("install")
            self.assertEqual(first["update_notice"]["kind"], "install")
            same = apply("upgrade")
            self.assertNotIn("update_notice", same)
            output = io.StringIO()
            with redirect_stdout(output):
                release.emit(same, None, False)
            self.assertIn(f"已核验当前版本 CosMesh {__version__}", output.getvalue())
            self.assertNotIn("版本变化", output.getvalue())
            state = release.load_state(prefix)
            state["versions"]["0.7.0"] = state["versions"].pop(__version__)
            state["current"] = "0.7.0"
            release.atomic(prefix / "state.json", release.json_bytes(state))
            self.assertNotIn("update_notice", apply("upgrade", True))
            updated = apply("upgrade")
            self.assertEqual(updated["update_notice"]["kind"], "upgrade")
            for receipt in (first, updated):
                output = io.StringIO()
                with redirect_stdout(output):
                    release.emit(receipt, None, False)
                self.assertEqual(output.getvalue().count(self.document), 1)
                output = io.StringIO()
                with redirect_stdout(output):
                    release.emit(receipt, None, True)
                notice = json.loads(output.getvalue())["data"]["update_notice"]
                self.assertEqual(notice["document"], self.document)
                self.assertEqual(notice["reopen"], "cosmesh guide show upgrade")

    def test_reopen_and_offline_update_in_plain_and_json_modes(self):
        receipt = dict(to=__version__, update_notice=dict(kind="upgrade", version=__version__, document=self.document))
        with patch.dict(os.environ, COSMESH_DATA_DIR=str(self.root)):
            for argv in (["guide", "show", "upgrade"], ["client", "update", "--release", str(self.root)]):
                with patch("cosmesh.client.update_release", return_value=receipt):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(argv), 0)
                    self.assertEqual(output.getvalue(), self.document)
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(argv + ["--json"]), 0)
                    data = json.loads(output.getvalue())["data"]
                    self.assertEqual(data.get("update_notice", data).get("document", data.get("text")), self.document)

    def test_local_bootstrap_prints_once_and_clears_stale_notice(self):
        module = bootstrap()
        prefix = self.root / "app"
        prefix.mkdir()
        # The application manager, gateway and saved client are independent roles.
        first = dict(to=__version__, update_notice=dict(kind="install", document=self.document))
        argv = ["bootstrap", "--prefix", str(prefix), "--skills-dir", str(self.root / "skills"),
                "--ssh", "test-hub", "--source", "/srv/team/agent"]
        check = type("Reply", (), {"stdout": '{"ok":true}', "stderr": "", "returncode": 0})()
        with patch.object(sys, "argv", argv), patch.dict(os.environ, COSMESH_BOOTSTRAP_HUMAN="1"), \
                patch.object(module, "materialize", return_value=self.root / "bundle"), \
                patch.object(module.manager, "apply_release", side_effect=[first, {"to": __version__}]), \
                patch.object(module.subprocess, "run", return_value=check):
            for index in range(2):
                output = io.StringIO()
                with redirect_stdout(output):
                    module.main()
                self.assertEqual(output.getvalue().count(self.document.rstrip()), 1 - index)
                mesh = json.loads((prefix / "client.json").read_text())["mesh_setup"]
                self.assertEqual("update_notice" in mesh, index == 0)
                (prefix / "state.json").write_text("{}")

    def test_remote_ensure_plain_notice_and_default_machine_receipt(self):
        prefix = self.root / "remote"
        command = ["ensure", "--prefix", str(prefix), "--releases-dir", str(self.root)]
        reply = json.dumps({"ok": True, "data": {"update_notice": {"document": self.document}}})
        checked = type("Reply", (), {"stdout": reply, "returncode": 0})()
        with patch.object(remote_bootstrap, "release", return_value=(self.root, __version__)), \
                patch.object(remote_bootstrap.subprocess, "run", return_value=checked):
            for installed in (None, "0.7.0", __version__):
                for human in (False, True):
                    readings = [installed] if installed == __version__ else [installed, __version__]
                    output = io.StringIO()
                    with patch.dict(os.environ, COSMESH_BOOTSTRAP_HUMAN="1" if human else "0"), \
                            patch.object(remote_bootstrap, "installed", side_effect=readings), redirect_stdout(output):
                        self.assertEqual(remote_bootstrap.main(command), 0)
                    if human:
                        self.assertEqual(output.getvalue().count(self.document.rstrip()), int(installed != __version__))
                    else:
                        result = json.loads(output.getvalue())["data"]
                        self.assertEqual("update_notice" in result, installed != __version__)
