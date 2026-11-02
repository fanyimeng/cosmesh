"""Run an actual CLI round trip in disposable local stores."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def invoke(product, root, *args):
    code = "from cosmesh.cli import main; raise SystemExit(main(product=%r))" % product
    process = subprocess.run([sys.executable, "-c", code, "--root", str(root), *args, "--json"],
                             capture_output=True, text=True,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if process.returncode:
        raise RuntimeError(process.stdout + process.stderr)
    return json.loads(process.stdout)


def main():
    with tempfile.TemporaryDirectory(prefix="cosmesh-roundtrip-", dir=str(Path(tempfile.gettempdir()).resolve())) as folder:
        base, mesh = Path(folder) / "personal", Path(folder) / "collaboration"
        invoke("cosbase", base, "init", "--user", "alice")
        record = invoke("cosbase", base, "update", "add", "--body", "先用三句话说明项目目标。", "--idempotency-key", "capture-1")["data"]["result"]
        converted = invoke("cosbase", base, "doc", "convert", record["id"], "--to", "task", "--expect", record["version"], "--idempotency-key", "convert-1")["data"]["result"]
        task = converted["target"]
        edited = invoke("cosbase", base, "doc", "edit", task["id"], "--expect", task["version"], "--next", "写出核心用户需求", "--followup", "2026-10-30")["data"]["result"]["document"]
        trashed = invoke("cosbase", base, "doc", "trash", task["id"], "--expect", edited["version"])["data"]["result"]["document"]
        restored = invoke("cosbase", base, "doc", "restore", task["id"], "--expect", trashed["version"])["data"]["result"]["document"]
        context = invoke("cosbase", base, "context", task["id"])
        assert restored["lifecycle"] == "active" and restored["sources"] == [record["id"]]
        assert context["data"]["document"]["next_action"] == "写出核心用户需求"
        print("CosBase：记录 → 转为任务 → 修改下一步 → 回收 → 恢复，来源与 ID 保留。")
        invoke("cosmesh", mesh, "init", "--user", "alice")
        project = invoke("cosmesh", mesh, "project", "create", "--title", "协作演示", "--member", "bob")["data"]["result"]
        task = invoke("cosmesh", mesh, "task", "create", "--project", project["id"], "--title", "功能检查", "--invite", "bob")["data"]["result"]
        source = invoke("cosmesh", mesh, "context", task["id"])["sync"]["source_revision"]
        invoke("cosmesh", mesh, "task", "state", task["id"], "--to", "in_progress", "--reason", "开始本地方法检查", "--expect-revision", source)
        invoke("cosmesh", mesh, "update", "add", "--project", project["id"], "--task", task["id"], "--body", "演示进展：已整理检查步骤，尚未运行真实分析。")
        state = invoke("cosmesh", mesh, "context", task["id"])
        assert state["data"]["document"]["derived"]["status"] == "in_progress"
        assert state["data"]["document"]["derived"]["participants"]["bob"] == "pending"
        print("CosMesh：项目 → 任务 / pending 邀请 → 状态动作 → 进展，上下文可重建。")
        print("回执状态：local_only；身份核验：false；全部数据为临时示例。")
    print("临时空间已清理。")


if __name__ == "__main__":
    main()
