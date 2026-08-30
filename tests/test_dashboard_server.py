import contextlib
import concurrent.futures
import hashlib
import http.client
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

SUITE = Path(__file__).resolve().parents[1]
SKILL = SUITE / "skills/short-drama"
SCRIPT = SKILL / "scripts/dashboard_server.py"
SPEC = importlib.util.spec_from_file_location("short_drama_dashboard_server", SCRIPT)
assert SPEC and SPEC.loader
dashboard_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard_server)
DashboardError = dashboard_server.DashboardError
ProjectStore = dashboard_server.ProjectStore
create_server = dashboard_server.create_server


def run_node(script: str) -> "subprocess.CompletedProcess[str]":
    """Run one Node snippet and read its output as UTF-8.

    Without an explicit encoding Windows decodes the child's pipe with the ANSI
    code page. The creator-facing Chinese these assertions are about then comes
    back as mojibake, or fails outright on the bytes cp1252 leaves undefined.
    """

    return subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def redirect_directory(link: Path, target: Path) -> bool:
    """Point ``link`` at ``target``, however this platform can.

    A symlink needs Developer Mode or an elevated shell on Windows; a junction
    needs neither and redirects just as well, which is why the traversal checks
    must reject both.
    """

    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        if os.name != "nt":
            return False
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    return completed.returncode == 0 and link.exists()


def make_project(root: Path, title: str = "测试短剧") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "short-drama.json").write_text(
        json.dumps(
            {"project_id": "test", "title": title, "current_checkpoint": "draft"}
        ),
        encoding="utf-8",
    )


class ProjectStoreTests(unittest.TestCase):
    def store(self, workspace: Path, **limits: int) -> ProjectStore:
        canonical = dashboard_server.load_project_tool(SKILL)
        # Expose exactly what the active backend declares it needs, so a
        # backend that quietly reached past its own contract fails here.
        tool = SimpleNamespace(
            **{
                name: getattr(canonical, name)
                for name in dashboard_server.directory_backend().contract
            }
        )
        return ProjectStore(workspace, tool, **limits)

    def require_descriptor_pinning(self) -> None:
        if dashboard_server.directory_backend() is not (
            dashboard_server._DescriptorDirectory
        ):
            self.skipTest("this behaviour is specific to descriptor pinning")

    def test_rejects_a_project_tool_without_the_dashboard_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Dashboard contract"):
                ProjectStore(
                    Path(directory), SimpleNamespace(project_status=lambda _root: {})
                )

    def test_discovers_manifests_without_following_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            make_project(workspace / "alpha", "甲项目")
            make_project(workspace / "nested/beta", "乙项目")
            try:
                (workspace / "linked").symlink_to(
                    workspace / "nested", target_is_directory=True
                )
            except OSError:
                pass

            projects, warnings = self.store(workspace).discover()

            self.assertEqual(
                [item["path"] for item in projects], ["alpha", "nested/beta"]
            )
            self.assertEqual(
                [item["title"] for item in projects], ["甲项目", "乙项目"]
            )
            self.assertEqual(warnings, [])
            self.assertEqual(len({item["id"] for item in projects}), 2)

    def test_concurrent_discovery_has_an_independent_directory_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            make_project(workspace / "show")
            store = self.store(workspace)
            expected = store.discover()[0]

            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                results = list(executor.map(lambda _: store.discover()[0], range(96)))

            self.assertTrue(all(result == expected for result in results))

    def test_discovery_uses_creator_safe_title_for_a_malformed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "internal-folder-name"
            make_project(project)
            (project / "short-drama.json").write_text("[]", encoding="utf-8")

            projects, warnings = self.store(Path(directory)).discover()

            self.assertEqual(projects[0]["title"], "未命名短剧")
            self.assertEqual(warnings, [])

    def test_concurrent_root_project_requests_have_independent_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            make_project(workspace)
            (workspace / "notes.md").write_text("root project", encoding="utf-8")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]
            expected_tree = store.tree(project_id)
            expected_status = store.status(project_id)

            def read(index: int):
                if index % 2:
                    return "status", store.status(project_id)
                return "tree", store.tree(project_id)

            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                results = list(executor.map(read, range(96)))

            for kind, result in results:
                expected = expected_tree if kind == "tree" else expected_status
                self.assertEqual(result, expected)

    def test_tree_enforces_node_depth_and_size_limits_with_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            (project / "large.md").write_text("12345", encoding="utf-8")
            (project / "deep/a/b").mkdir(parents=True)
            (project / "deep/a/b/hidden.md").write_text("x", encoding="utf-8")
            store = self.store(workspace, max_depth=1, max_nodes=20, max_file_bytes=4)
            project_id = store.discover()[0][0]["id"]

            result = store.tree(project_id)

            files = []
            stack = list(result["tree"])
            while stack:
                item = stack.pop()
                files.append(item)
                stack.extend(item.get("children", []))
            large = next(item for item in files if item.get("path") == "large.md")
            self.assertTrue(large["oversize"])
            self.assertTrue(
                any("深度上限" in warning for warning in result["warnings"])
            )
            self.assertTrue(
                any("大小上限" in warning for warning in result["warnings"])
            )
            with self.assertRaises(DashboardError) as caught:
                store.read_text(project_id, "large.md")
            self.assertEqual(caught.exception.status, 413)

    def test_read_write_versions_conflicts_and_atomic_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            script = project / "episodes/EP001/script.md"
            script.parent.mkdir(parents=True)
            script.write_text("第一版", encoding="utf-8")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]

            opened = store.read_text(project_id, "episodes/EP001/script.md")
            self.assertEqual(
                opened["version"], hashlib.sha256("第一版".encode()).hexdigest()
            )
            result = store.write_text(
                project_id, "episodes/EP001/script.md", "第二版", opened["version"]
            )

            self.assertTrue(result["saved"])
            self.assertEqual(script.read_text(encoding="utf-8"), "第二版")
            self.assertFalse(any(script.parent.glob(".script.md.*.tmp")))
            with self.assertRaises(DashboardError) as caught:
                store.write_text(
                    project_id,
                    "episodes/EP001/script.md",
                    "旧客户端",
                    opened["version"],
                )
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(script.read_text(encoding="utf-8"), "第二版")

    def test_reads_and_saves_unicode_project_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "中文工程"
            make_project(project)
            target = project / "设定集/角色.jsonl"
            target.parent.mkdir(parents=True)
            target.write_text('{"姓名":"顾霖"}\n', encoding="utf-8")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]

            opened = store.read_text(project_id, "设定集/角色.jsonl")
            result = store.write_text(
                project_id,
                "设定集/角色.jsonl",
                '{"姓名":"顾霖","身份":"佛子"}\n',
                opened["version"],
            )

            self.assertTrue(result["saved"])
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                '{"姓名":"顾霖","身份":"佛子"}\n',
            )

    def test_subtitle_sources_are_editable_project_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "字幕工程"
            make_project(project)
            target = project / "剧集/EP001/storyboard/预演.srt"
            target.parent.mkdir(parents=True)
            target.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n第一句\n",
                encoding="utf-8",
            )
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]

            opened = store.read_text(project_id, "剧集/EP001/storyboard/预演.srt")
            result = store.write_text(
                project_id,
                "剧集/EP001/storyboard/预演.srt",
                "1\n00:00:00,000 --> 00:00:01,200\n第一句\n",
                opened["version"],
            )

            self.assertTrue(result["saved"])
            self.assertIn("01,200", target.read_text(encoding="utf-8"))

    def test_structured_text_save_rejects_invalid_json_without_modifying_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            json_file = project / "episode.json"
            jsonl_file = project / "shots.jsonl"
            json_file.write_text('{"episode": 1}\n', encoding="utf-8")
            jsonl_file.write_text('{"shot": 1}\n{"shot": 2}\n', encoding="utf-8")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]

            json_version = store.read_text(project_id, "episode.json")["version"]
            with self.assertRaisesRegex(DashboardError, "JSON is invalid") as caught:
                store.write_text(project_id, "episode.json", "{", json_version)
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(json_file.read_text(encoding="utf-8"), '{"episode": 1}\n')

            jsonl_version = store.read_text(project_id, "shots.jsonl")["version"]
            with self.assertRaisesRegex(DashboardError, "JSONL line 2") as caught:
                store.write_text(
                    project_id,
                    "shots.jsonl",
                    '{"shot": 1}\nnot-json\n',
                    jsonl_version,
                )
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(
                jsonl_file.read_text(encoding="utf-8"),
                '{"shot": 1}\n{"shot": 2}\n',
            )

    def test_concurrent_writes_cannot_both_use_one_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            target = project / "notes.txt"
            target.write_text("base", encoding="utf-8")
            target = target.resolve()
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]
            version = store.read_text(project_id, "notes.txt")["version"]
            barrier = threading.Barrier(3)
            outcomes = []

            def write(content: str) -> None:
                barrier.wait()
                try:
                    store.write_text(project_id, "notes.txt", content, version)
                    outcomes.append("saved")
                except DashboardError as exc:
                    outcomes.append(exc.status)

            writers = [
                threading.Thread(target=write, args=(content,))
                for content in ("client-a", "client-b")
            ]
            for writer in writers:
                writer.start()
            barrier.wait()
            for writer in writers:
                writer.join(timeout=2)

            self.assertCountEqual(outcomes, ["saved", 409])

    def test_two_stores_cannot_both_save_one_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            target = project / "notes.txt"
            target.write_text("base", encoding="utf-8")
            target = target.resolve()
            stores = [self.store(workspace), self.store(workspace)]
            project_id = stores[0].discover()[0][0]["id"]
            version = stores[0].read_text(project_id, "notes.txt")["version"]
            # Two stores each load their own project-tool module instance, so the
            # project's file lock must cover the compare-and-replace operation.
            barrier = threading.Barrier(3)
            outcomes = []

            def write(store: ProjectStore, content: str) -> None:
                barrier.wait()
                try:
                    store.write_text(project_id, "notes.txt", content, version)
                    outcomes.append("saved")
                except DashboardError as exc:
                    outcomes.append(exc.status)

            writers = [
                threading.Thread(target=write, args=(store, f"client-{index}"))
                for index, store in enumerate(stores)
            ]
            for writer in writers:
                writer.start()
            barrier.wait()
            for writer in writers:
                writer.join(timeout=5)

            self.assertFalse(any(writer.is_alive() for writer in writers))
            self.assertCountEqual(outcomes, ["saved", 409])
            self.assertIn(target.read_text(encoding="utf-8"), {"client-0", "client-1"})

    def test_rejects_traversal_symlinks_and_protected_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            (project / ".short-drama").mkdir()
            (project / ".short-drama/state.json").write_text("{}", encoding="utf-8")
            (project / "delivery").mkdir()
            (project / "delivery/result.txt").write_text("locked", encoding="utf-8")
            (project / "交付").mkdir()
            (project / "交付/result.txt").write_text("locked", encoding="utf-8")
            (project / "notes.txt").write_text("ok", encoding="utf-8")
            outside = workspace / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]

            for unsafe in ("../outside.txt", "/etc/passwd", "a/../../outside.txt"):
                with self.subTest(unsafe=unsafe), self.assertRaises(DashboardError):
                    store.read_text(project_id, unsafe)
            try:
                (project / "link.txt").symlink_to(outside)
            except OSError:
                pass
            else:
                with self.assertRaises(DashboardError) as caught:
                    store.read_text(project_id, "link.txt")
                self.assertEqual(caught.exception.status, 403)

            for protected in (
                "short-drama.json",
                ".short-drama/state.json",
                "delivery/result.txt",
                "交付/result.txt",
            ):
                opened = store.read_text(project_id, protected)
                self.assertFalse(opened["writable"])
                with self.assertRaises(DashboardError) as caught:
                    store.write_text(
                        project_id, protected, "changed", opened["version"]
                    )
                self.assertEqual(caught.exception.status, 403)

            tree = store.tree(project_id)
            visible_paths = []
            pending = list(tree["tree"])
            while pending:
                item = pending.pop()
                visible_paths.append(item["path"])
                pending.extend(item.get("children", []))
            self.assertFalse(
                any(path.startswith(".short-drama") for path in visible_paths)
            )
            self.assertIn("short-drama.json", visible_paths)
            self.assertIn("delivery/result.txt", visible_paths)
            self.assertIn("交付/result.txt", visible_paths)

    def test_stale_discovery_cannot_resolve_a_project_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            workspace = container / "workspace"
            project = workspace / "show"
            outside = container / "outside"
            make_project(project, "原项目")
            make_project(outside, "外部项目")
            store = self.store(workspace)
            discovered = store.discover()
            project_id = discovered[0][0]["id"]

            shutil.rmtree(project)
            try:
                project.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            with patch.object(store, "discover", return_value=discovered):
                with self.assertRaises(DashboardError) as caught:
                    store.status(project_id)

            self.assertEqual(caught.exception.status, 403)

    def test_status_and_tree_read_from_a_pinned_project_root(self) -> None:
        # A descriptor keeps reading the directory it pinned even after the
        # name is repointed. Path pinning cannot promise that -- it fails the
        # request instead, which the path-backend test below asserts.
        self.require_descriptor_pinning()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            project = workspace / "show"
            outside = Path(directory) / "outside"
            make_project(project, "原项目")
            make_project(outside, "外部项目")
            (project / "inside.md").write_text("inside", encoding="utf-8")
            (outside / "outside-secret.md").write_text("outside", encoding="utf-8")
            digest = hashlib.sha256(b"inside").hexdigest()
            (project / ".short-drama").mkdir()
            (project / ".short-drama/state.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "artifacts": {
                            "inside": {
                                "outputs": ["inside.md"],
                                "inputs": {},
                                "acceptance": {
                                    "decision": "accepted",
                                    "outputs": {"inside.md": digest},
                                },
                                "review": {
                                    "verdict": "approve",
                                    "outputs": {"inside.md": digest},
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = ProjectStore(
                workspace, dashboard_server.load_project_tool(SKILL)
            )
            project_id = store.discover()[0][0]["id"]

            def swap_while(call):
                entered = threading.Event()
                proceed = threading.Event()
                original = call[0]

                def delayed(*args, **kwargs):
                    entered.set()
                    self.assertTrue(proceed.wait(timeout=3))
                    return original(*args, **kwargs)

                call[1](delayed)
                result: list[object] = []
                thread = threading.Thread(target=lambda: result.append(call[2]()))
                thread.start()
                self.assertTrue(entered.wait(timeout=3))
                saved = workspace / "show-original"
                project.rename(saved)
                project.symlink_to(outside, target_is_directory=True)
                proceed.set()
                thread.join(timeout=3)
                project.unlink()
                saved.rename(project)
                self.assertFalse(thread.is_alive())
                return result[0]

            original_status = store.project_tool.project_status_at
            status = swap_while(
                (
                    original_status,
                    lambda replacement: setattr(
                        store.project_tool, "project_status_at", replacement
                    ),
                    lambda: store.status(project_id),
                )
            )
            store.project_tool.project_status_at = original_status
            self.assertEqual(status["title"], "原项目")
            self.assertEqual(status["artifacts"], {"inside": "approved"})

            original_tree = store._tree_from_root
            tree = swap_while(
                (
                    original_tree,
                    lambda replacement: setattr(
                        store, "_tree_from_root", replacement
                    ),
                    lambda: store.tree(project_id),
                )
            )
            store._tree_from_root = original_tree
            visible = json.dumps(tree, ensure_ascii=False)
            self.assertIn("inside.md", visible)
            self.assertNotIn("outside-secret.md", visible)
            store.close()

    def test_dashboard_edit_invalidates_tracked_lifecycle_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            tool = dashboard_server.load_project_tool(SKILL)
            tool.initialize_project(
                project,
                title="状态测试",
                language="zh-CN",
                aspect_ratio="9:16",
                suite_root=SKILL,
            )
            target = project / "剧集/EP001/screenplay.md"
            target.parent.mkdir(parents=True)
            target.write_text("旧版本\n", encoding="utf-8")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            state_path = project / ".short-drama/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["artifacts"]["EP001:script"] = {
                "owner": "short-drama-write",
                "candidate_targets": {"剧集/EP001/screenplay.md": digest},
                "accepted_targets": {"剧集/EP001/screenplay.md": digest},
                "build_state": "materialized",
                "validation_state": "pass",
                "creator_acceptance": "accepted",
                "independent_review": "approve",
                "delivery_gate": "ready",
            }
            tool.atomic_json(state_path, state)
            store = ProjectStore(workspace, tool)
            project_id = store.discover()[0][0]["id"]
            opened = store.read_text(project_id, "剧集/EP001/screenplay.md")

            store.write_text(
                project_id,
                "剧集/EP001/screenplay.md",
                "新版本\n",
                opened["version"],
            )

            status = store.status(project_id)
            self.assertEqual(
                status["lifecycle"]["artifact_state"], {"update_needed": 1}
            )
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["artifacts"]["EP001:script"]["build_state"],
                "materialized",
            )

    def test_status_overlays_live_hash_drift_after_an_external_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            tool = dashboard_server.load_project_tool(SKILL)
            tool.initialize_project(
                project,
                title="状态测试",
                language="zh-CN",
                aspect_ratio="9:16",
                suite_root=SKILL,
            )
            target = project / "剧集/EP001/screenplay.md"
            target.parent.mkdir(parents=True)
            target.write_text("已确认\n", encoding="utf-8")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            state_path = project / ".short-drama/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["artifacts"]["EP001:script"] = {
                "accepted_targets": {"剧集/EP001/screenplay.md": digest},
                "build_state": "materialized",
                "validation_state": "pass",
                "creator_acceptance": "accepted",
                "independent_review": "approve",
                "delivery_gate": "ready",
            }
            tool.atomic_json(state_path, state)
            target.write_text("磁盘外部改动\n", encoding="utf-8")
            store = ProjectStore(workspace, tool)
            project_id = store.discover()[0][0]["id"]

            status = store.status(project_id)

            self.assertEqual(
                status["lifecycle"]["artifact_state"], {"update_needed": 1}
            )

    def test_status_treats_a_tracked_symlink_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            tool = dashboard_server.load_project_tool(SKILL)
            tool.initialize_project(
                project,
                title="状态测试",
                language="zh-CN",
                aspect_ratio="9:16",
                suite_root=SKILL,
            )
            target = project / "剧集/EP001/screenplay.md"
            target.parent.mkdir(parents=True)
            target.write_text("已确认\n", encoding="utf-8")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            state_path = project / ".short-drama/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["artifacts"]["EP001:script"] = {
                "accepted_targets": {"剧集/EP001/screenplay.md": digest},
                "build_state": "materialized",
                "validation_state": "pass",
                "creator_acceptance": "accepted",
                "independent_review": "approve",
                "delivery_gate": "ready",
            }
            tool.atomic_json(state_path, state)
            outside = workspace / "outside.md"
            outside.write_text("外部内容\n", encoding="utf-8")
            target.unlink()
            try:
                target.symlink_to(outside)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            store = ProjectStore(workspace, tool)
            project_id = store.discover()[0][0]["id"]

            status = store.status(project_id)

            self.assertEqual(
                status["lifecycle"]["artifact_state"], {"update_needed": 1}
            )

    def test_parent_directory_swap_cannot_redirect_a_text_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            inside = project / "inside"
            inside.mkdir()
            target = inside / "notes.txt"
            target.write_text("project", encoding="utf-8")
            outside = workspace / "outside"
            outside.mkdir()
            outside_target = outside / "notes.txt"
            outside_target.write_text("outside", encoding="utf-8")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]
            version = store.read_text(project_id, "inside/notes.txt")["version"]
            swapped = False

            original_open_parent = dashboard_server._open_parent_directory

            @contextlib.contextmanager
            def swap_parent(root, relative: PurePosixPath):
                nonlocal swapped
                if relative.as_posix() == "inside/notes.txt" and not swapped:
                    inside.rename(project / "inside-original")
                    if not redirect_directory(inside, outside):
                        (project / "inside-original").rename(inside)
                        self.skipTest("directory redirection is unavailable")
                    swapped = True
                with original_open_parent(root, relative) as opened:
                    yield opened

            with patch.object(
                dashboard_server, "_open_parent_directory", swap_parent
            ):
                with self.assertRaises(DashboardError) as caught:
                    store.write_text(
                        project_id, "inside/notes.txt", "redirected", version
                    )

            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(outside_target.read_text(encoding="utf-8"), "outside")
            self.assertEqual(
                (project / "inside-original/notes.txt").read_text(encoding="utf-8"),
                "project",
            )

    def test_parent_directory_swap_cannot_redirect_media_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            storyboard = project / "episodes/EP001/storyboard"
            storyboard.mkdir(parents=True)
            (storyboard / "clip.mp4").write_bytes(b"PROJECT-MEDIA")
            outside = workspace / "outside"
            outside.mkdir()
            outside_target = outside / "clip.mp4"
            outside_target.write_bytes(b"OUTSIDE-SECRET")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]
            swapped = False

            original_open_parent = dashboard_server._open_parent_directory

            @contextlib.contextmanager
            def swap_parent(root, relative: PurePosixPath):
                nonlocal swapped
                if relative.as_posix() == "episodes/EP001/storyboard/clip.mp4" and not swapped:
                    storyboard.rename(project / "storyboard-original")
                    if not redirect_directory(storyboard, outside):
                        (project / "storyboard-original").rename(storyboard)
                        self.skipTest("directory redirection is unavailable")
                    swapped = True
                with original_open_parent(root, relative) as opened:
                    yield opened

            with patch.object(
                dashboard_server, "_open_parent_directory", swap_parent
            ):
                with self.assertRaises(DashboardError) as caught:
                    store.open_media(project_id, "episodes/EP001/storyboard/clip.mp4")

            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(outside_target.read_bytes(), b"OUTSIDE-SECRET")

    def test_a_platform_without_descriptors_serves_the_same_content(self) -> None:
        # The path backend is a whole dashboard, not a degraded one: same tree,
        # same reads, same saves, same previews.
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            (project / "notes.txt").write_text("文本", encoding="utf-8")
            (project / "clip.mp4").write_bytes(b"media")

            with patch.object(dashboard_server, "SECURE_DIR_FD", False):
                store = self.store(workspace)
                self.assertIs(store.backend, dashboard_server._PathDirectory)
                project_id = store.discover()[0][0]["id"]
                self.assertEqual(store.status(project_id)["title"], "测试短剧")
                names = {node["path"] for node in store.tree(project_id)["tree"]}
                self.assertEqual(
                    names, {"short-drama.json", "notes.txt", "clip.mp4"}
                )

                opened = store.read_text(project_id, "notes.txt")
                self.assertEqual(opened["content"], "文本")
                saved = store.write_text(
                    project_id, "notes.txt", "改写", opened["version"]
                )
                self.assertTrue(saved["saved"])
                self.assertEqual(
                    (project / "notes.txt").read_bytes(), "改写".encode("utf-8")
                )

                handle, _path, content_type, size = store.open_media(
                    project_id, "clip.mp4"
                )
                with handle:
                    self.assertEqual(handle.read(), b"media")
                self.assertEqual(content_type, "video/mp4")
                self.assertEqual(size, 5)
                store.close()

    def test_path_pinning_fails_a_project_root_swapped_mid_request(self) -> None:
        # The descriptor backend keeps reading the directory it pinned; path
        # pinning cannot, so it must fail the request rather than follow the
        # name to wherever it now points.
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            workspace = container / "workspace"
            project = workspace / "show"
            outside = container / "outside"
            make_project(project, "原项目")
            make_project(outside, "外部项目")
            (outside / "outside-secret.md").write_text("secret", encoding="utf-8")

            with patch.object(dashboard_server, "SECURE_DIR_FD", False):
                store = self.store(workspace)
                discovered = store.discover()
                project_id = discovered[0][0]["id"]
                shutil.rmtree(project)
                if not redirect_directory(project, outside):
                    self.skipTest("directory redirection is unavailable")
                with patch.object(store, "discover", return_value=discovered):
                    for call in (
                        lambda: store.status(project_id),
                        lambda: store.tree(project_id),
                        lambda: store.read_text(project_id, "outside-secret.md"),
                    ):
                        with self.assertRaises(DashboardError) as caught:
                            call()
                        self.assertEqual(caught.exception.status, 403)
                store.close()

    def test_both_link_predicates_agree_on_what_must_not_be_walked(self) -> None:
        # The dashboard and the project tool each own a copy of this predicate,
        # and nothing else would notice if they drifted.
        canonical = dashboard_server.load_project_tool(SKILL)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plain_file = root / "plain.txt"
            plain_file.write_text("x", encoding="utf-8")
            plain_dir = root / "dir"
            plain_dir.mkdir()
            expected = {plain_file: False, plain_dir: False}
            link = root / "link"
            if redirect_directory(link, plain_dir):
                expected[link] = True
            for candidate, must_reject in expected.items():
                details = os.lstat(candidate)
                self.assertEqual(
                    dashboard_server._is_link_or_reparse(details),
                    must_reject,
                    candidate.name,
                )
                self.assertEqual(
                    canonical._is_link_or_reparse(details),
                    must_reject,
                    candidate.name,
                )

    def test_a_save_refused_by_a_file_holder_says_so(self) -> None:
        # Windows refuses the replace while anything else holds the file open,
        # the most ordinary way a save fails there. Reporting it as an unsafe
        # path would send a creator after the wrong problem.
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            (project / "notes.txt").write_text("base", encoding="utf-8")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]
            version = store.read_text(project_id, "notes.txt")["version"]

            def refuse(self, source: str, target: str) -> None:
                raise PermissionError(13, "used by another process")

            with patch.object(store.backend, "replace", refuse):
                with self.assertRaises(DashboardError) as caught:
                    store.write_text(project_id, "notes.txt", "改写", version)

            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(
                caught.exception.message, "file is locked or not writable"
            )
            self.assertEqual(
                (project / "notes.txt").read_text(encoding="utf-8"), "base"
            )
            # The refused replace must still take its temporary file with it.
            self.assertEqual(
                [item.name for item in project.iterdir() if ".tmp" in item.name],
                [],
            )

    def test_a_redirected_operations_directory_cannot_hold_the_lock(self) -> None:
        # The project lock is what makes the version check and the replace one
        # operation. Taken through a redirected `.short-drama`, two dashboards
        # would each believe they held it.
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            (project / "notes.txt").write_text("base", encoding="utf-8")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]
            version = store.read_text(project_id, "notes.txt")["version"]
            outside = workspace / "outside"
            outside.mkdir()
            if not redirect_directory(project / ".short-drama", outside):
                self.skipTest("directory redirection is unavailable")

            with self.assertRaises(DashboardError) as caught:
                store.write_text(project_id, "notes.txt", "改写", version)

            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(
                (project / "notes.txt").read_text(encoding="utf-8"), "base"
            )
            self.assertEqual([item.name for item in outside.iterdir()], [])

    def test_media_has_an_independent_preview_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            (project / "poster.png").write_bytes(b"123456")
            (project / "clip.mp4").write_bytes(b"123456789")
            store = self.store(workspace, max_file_bytes=2, max_media_bytes=8)
            project_id = store.discover()[0][0]["id"]

            handle, _, content_type, size = store.open_media(project_id, "poster.png")
            with handle:
                self.assertEqual(handle.read(), b"123456")
            self.assertEqual(content_type, "image/png")
            self.assertEqual(size, 6)
            tree = store.tree(project_id)
            media_nodes = {
                node["path"]: node for node in tree["tree"] if node["type"] == "media"
            }
            self.assertFalse(media_nodes["poster.png"]["oversize"])
            self.assertTrue(media_nodes["clip.mp4"]["oversize"])

            with self.assertRaises(DashboardError) as caught:
                store.open_media(project_id, "clip.mp4")
            self.assertEqual(caught.exception.status, 413)

    def test_audio_outputs_are_browsable_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            voice = project / "剧集/EP001/制作成果/tts/LINE001.wav"
            voice.parent.mkdir(parents=True)
            voice.write_bytes(b"RIFFvoice")
            store = self.store(workspace)
            project_id = store.discover()[0][0]["id"]

            tree = store.tree(project_id)
            files = {}

            def collect(nodes: list[dict]) -> None:
                for node in nodes:
                    if node["type"] == "directory":
                        collect(node["children"])
                    else:
                        files[node["path"]] = node

            collect(tree["tree"])
            self.assertEqual(files["剧集/EP001/制作成果/tts/LINE001.wav"]["type"], "media")
            self.assertEqual(store.media_info(project_id, "剧集/EP001/制作成果/tts/LINE001.wav")["kind"], "audio")


class PathPinnedProjectStoreTests(ProjectStoreTests):
    """Run every store test again through the backend Windows must use.

    The backend is chosen by platform, so without this the path half would be
    exercised only on the Windows runner. Tests that assert descriptor-specific
    behaviour skip themselves through ``require_descriptor_pinning``.
    """

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(dashboard_server, "SECURE_DIR_FD", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_path_backend_is_the_one_under_test(self) -> None:
        self.assertIs(
            dashboard_server.directory_backend(), dashboard_server._PathDirectory
        )


class ArtifactOwnershipTests(unittest.TestCase):
    """Status carries which stage produced each file, so readers stop guessing.

    Grouping by filename means every reader keeps its own list of names, and a
    stage added later shows up as unrecognised until someone remembers to update
    that list. The novel-analysis stage sat unlabelled in the dashboard for two
    releases for exactly that reason.
    """

    def status_for(self, root: Path) -> dict:
        canonical = dashboard_server.load_project_tool(SKILL)
        return canonical.project_status(root)

    def start_project(self, root: Path):
        canonical = dashboard_server.load_project_tool(SKILL)
        canonical.initialize_project(
            root,
            title="测试短剧",
            language="zh-CN",
            aspect_ratio="9:16",
            suite_root=SKILL,
        )
        return canonical

    def test_status_reports_the_owner_of_every_published_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            canonical = self.start_project(root)
            canonical.publish_candidate(
                root,
                owner="short-drama-write",
                artifact_id="EP001:script",
                outputs={"剧集/EP001/screenplay.md": "# 一句正文\n"},
            )
            status = self.status_for(root)

        self.assertEqual(
            status["ownership"].get("剧集/EP001/screenplay.md"),
            "short-drama-write",
        )

    def test_ownership_is_empty_for_a_project_with_nothing_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            self.start_project(root)
            self.assertEqual(self.status_for(root)["ownership"], {})


class DashboardSectionVocabularyTests(unittest.TestCase):
    """Every stage that can own a file must map to a place to show it.

    This is the half that keeps the grouping self-maintaining: add a stage skill
    and this fails until the dashboard knows where its outputs belong, instead of
    quietly dropping them into 其他内容.
    """

    APP = SKILL / "assets/dashboard/app.js"

    def owner_sections(self) -> dict[str, str]:
        source = self.APP.read_text(encoding="utf-8")
        block = source[source.index("const OWNER_SECTIONS = {"):]
        block = block[: block.index("};")]
        return dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block))

    def test_every_stage_skill_has_a_section(self) -> None:
        mapped = self.owner_sections()
        stages = {
            path.name
            for path in (SUITE / "skills").iterdir()
            if path.is_dir() and path.name.startswith("short-drama")
        }
        # The review stage records verdicts rather than owning creative files.
        stages.discard("short-drama-review")
        missing = sorted(stages - set(mapped))
        self.assertEqual(
            missing,
            [],
            f"dashboard OWNER_SECTIONS has no home for {missing}; their outputs "
            f"would fall into 其他内容",
        )

    def test_every_section_it_names_is_a_real_section(self) -> None:
        source = self.APP.read_text(encoding="utf-8")
        order = re.search(r"const SECTION_ORDER = \[(.*?)\];", source, re.S).group(1)
        known = set(re.findall(r'"([^"]+)"', order))
        for owner, section in self.owner_sections().items():
            with self.subTest(owner=owner):
                self.assertIn(section, known)


class DashboardSessionTests(unittest.TestCase):
    """The dashboard has to outlive the shell that started it, and say so."""

    def make_workspace(self, directory: str) -> Path:
        workspace = Path(directory) / "workspace"
        (workspace / "剧集").mkdir(parents=True)
        (workspace / "short-drama.json").write_text(
            json.dumps({"project_id": "SD-TEST", "title": "面板项目"}),
            encoding="utf-8",
        )
        return workspace

    def run_dashboard(self, *arguments: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_a_detached_dashboard_survives_its_launching_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(directory)
            session_path = workspace / ".short-drama/dashboard.json"
            started = self.run_dashboard(
                "--workspace", str(workspace), "--port", "0", "--detach"
            )
            self.addCleanup(
                self.run_dashboard, "--workspace", str(workspace), "--stop"
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            session = dashboard_server.read_session(session_path)
            self.assertIsNotNone(session)
            assert session is not None
            self.assertNotEqual(session["pid"], os.getpid())
            # The launching process is long gone; the server still holds its lock.
            self.assertTrue(dashboard_server.session_is_live(session_path))
            self.assertIn(session["url"], started.stdout)

            status = self.run_dashboard("--workspace", str(workspace), "--status")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["url"], session["url"])

            again = self.run_dashboard(
                "--workspace", str(workspace), "--port", "0", "--detach"
            )
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn(session["url"], again.stdout)
            self.assertEqual(
                dashboard_server.read_session(session_path)["pid"], session["pid"]
            )

            stopped = self.run_dashboard("--workspace", str(workspace), "--stop")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertFalse(session_path.exists())
            self.assertFalse(dashboard_server.session_is_live(session_path))

    def test_a_detached_start_that_never_records_a_session_reports_the_log(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(directory)
            session_path = workspace / ".short-drama/dashboard.json"
            # Hold the serving lock so the spawned child exits without recording.
            with dashboard_server.hold_session_lock(session_path) as held:
                self.assertTrue(held)
                started = self.run_dashboard(
                    "--workspace", str(workspace), "--port", "0", "--detach"
                )
            self.assertEqual(started.returncode, 1)
            self.assertIn("dashboard.log", started.stderr)
            self.assertNotIn("Traceback", started.stderr)

    def test_an_unwritable_workspace_cannot_record_but_is_not_fatal(self) -> None:
        """Serving never depended on the session record. A workspace the creator
        can read but not write must still open, the way it did before sessions
        existed -- so the failure is a typed signal main() can degrade on, not an
        uncaught OSError, and liveness answers "no" instead of raising."""
        if os.name == "nt" or os.geteuid() == 0:
            self.skipTest("this platform cannot make a directory unwritable here")
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(directory)
            session_path = workspace / ".short-drama/dashboard.json"
            os.chmod(workspace, 0o555)
            try:
                with self.assertRaises(dashboard_server.SessionUnavailable):
                    with dashboard_server.hold_session_lock(session_path):
                        pass
                self.assertFalse(dashboard_server.session_is_live(session_path))
            finally:
                os.chmod(workspace, 0o755)

    def test_a_read_only_workspace_still_serves(self) -> None:
        if os.name == "nt" or os.geteuid() == 0:
            self.skipTest("this platform cannot make a directory unwritable here")
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(directory)
            os.chmod(workspace, 0o555)
            try:
                server = create_server(workspace, port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    connection = http.client.HTTPConnection(
                        *server.server_address[:2], timeout=5
                    )
                    connection.request("GET", "/")
                    self.assertEqual(connection.getresponse().status, 200)
                    connection.close()
                finally:
                    server.shutdown()
                    thread.join(5)
                    server.server_close()
            finally:
                os.chmod(workspace, 0o755)

    def test_a_record_for_another_workspace_is_not_this_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mine = self.make_workspace(directory)
            theirs = Path(directory) / "elsewhere"
            theirs.mkdir()
            record = {
                "schema_version": dashboard_server.SESSION_SCHEMA,
                "fingerprint": dashboard_server.workspace_fingerprint(theirs),
                "workspace": str(theirs),
            }
            self.assertFalse(dashboard_server.session_matches(record, mine))
            self.assertTrue(dashboard_server.session_matches(record, theirs))

    def test_a_missing_workspace_is_refused_instead_of_created_and_served(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "typo"
            result = self.run_dashboard(
                "--workspace", str(missing), "--port", "0", "--detach"
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("not an existing directory", result.stderr)
            self.assertFalse(missing.exists())

    def test_the_server_stops_when_its_workspace_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(directory)
            server = create_server(workspace, port=0)
            self.addCleanup(server.server_close)
            stopped = threading.Event()

            class Recorder:
                def shutdown(self) -> None:
                    stopped.set()

            dashboard_server.watch_workspace(Recorder(), workspace, interval=0.05)
            shutil.rmtree(workspace)
            self.assertTrue(
                stopped.wait(10), "watchdog did not stop a server whose workspace is gone"
            )

    def test_status_reports_nothing_running_without_a_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(directory)
            status = self.run_dashboard("--workspace", str(workspace), "--status")
            self.assertEqual(status.returncode, 1)
            payload = json.loads(status.stdout)
            self.assertFalse(payload["running"])
            self.assertNotIn("url", payload)

    def test_the_session_file_is_only_readable_by_its_owner(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX permission bits do not apply on this platform")
        with tempfile.TemporaryDirectory() as directory:
            session_path = Path(directory) / ".short-drama/dashboard.json"
            dashboard_server.write_session(
                session_path,
                {"schema_version": dashboard_server.SESSION_SCHEMA, "token": "secret"},
            )
            self.assertEqual(session_path.stat().st_mode & 0o777, 0o600)

    def test_a_record_without_a_serving_process_is_not_live(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session_path = Path(directory) / ".short-drama/dashboard.json"
            dashboard_server.write_session(
                session_path,
                {
                    "schema_version": dashboard_server.SESSION_SCHEMA,
                    "host": "127.0.0.1",
                    "port": 8765,
                    "fingerprint": "0" * 16,
                    "token": "irrelevant",
                    "url": "http://127.0.0.1:8765/#irrelevant",
                    "pid": 999999,
                },
            )
            self.assertIsNotNone(dashboard_server.read_session(session_path))
            self.assertFalse(dashboard_server.session_is_live(session_path))

    def test_liveness_follows_the_serving_lock_not_the_recorded_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session_path = Path(directory) / ".short-drama/dashboard.json"
            with dashboard_server.hold_session_lock(session_path) as held:
                self.assertTrue(held)
                self.assertTrue(dashboard_server.session_is_live(session_path))
                with dashboard_server.hold_session_lock(session_path) as second:
                    self.assertFalse(second)
            self.assertFalse(dashboard_server.session_is_live(session_path))

    def test_each_workspace_has_its_own_serving_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mine = Path(directory) / "a/.short-drama/dashboard.json"
            theirs = Path(directory) / "b/.short-drama/dashboard.json"
            with dashboard_server.hold_session_lock(mine) as held:
                self.assertTrue(held)
                self.assertTrue(dashboard_server.session_is_live(mine))
                self.assertFalse(dashboard_server.session_is_live(theirs))

    def test_a_corrupt_or_foreign_session_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(dashboard_server.read_session(path))
            path.write_text(json.dumps({"schema_version": "9.9"}), encoding="utf-8")
            self.assertIsNone(dashboard_server.read_session(path))
            path.write_text(
                json.dumps({"schema_version": dashboard_server.SESSION_SCHEMA}),
                encoding="utf-8",
            )
            self.assertIsNone(dashboard_server.read_session(path))


class DashboardEntrypointTests(unittest.TestCase):
    def test_static_assets_and_project_tool_resolve_inside_installed_skill(
        self,
    ) -> None:
        self.assertEqual(
            dashboard_server.STATIC_ROOT,
            SKILL / "assets/dashboard",
        )
        self.assertTrue((dashboard_server.STATIC_ROOT / "index.html").is_file())
        project_tool = dashboard_server.load_project_tool(SKILL)
        self.assertTrue(callable(project_tool.project_status))

    def test_document_reader_keeps_long_content_in_a_scroll_container(self) -> None:
        styles = (dashboard_server.STATIC_ROOT / "styles.css").read_text(
            encoding="utf-8"
        )

        def declarations(selector: str) -> str:
            match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", styles)
            if match is None:
                self.fail(f"missing Dashboard CSS rule for {selector}")
            return match.group(1)

        creator_desk = declarations(".creator-desk")
        document_pane = declarations(".document-pane")
        content_stage = declarations(".content-stage")
        self.assertRegex(creator_desk, r"\boverflow:\s*hidden\s*;")
        self.assertRegex(document_pane, r"\bmin-height:\s*0\s*;")
        self.assertRegex(document_pane, r"\bdisplay:\s*grid\s*;")
        self.assertRegex(
            document_pane,
            r"\bgrid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)\s+auto\s*;",
        )
        self.assertRegex(content_stage, r"\bmin-height:\s*0\s*;")
        self.assertRegex(content_stage, r"\boverflow:\s*auto\s*;")

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_supports_simple_and_legacy_creator_statuses(self) -> None:
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const result = {{
  draft: logic.creatorStatus(null),
  pending: logic.creatorStatus({{creator_acceptance: "pending"}}),
  pendingBlocked: logic.creatorStatus({{
    creator_acceptance: "pending",
    independent_review: "provisional",
    delivery_gate: "blocked"
  }}),
  rejected: logic.creatorStatus({{creator_acceptance: "rejected"}}),
  revise: logic.creatorStatus({{independent_review: "revise"}}),
  stale: logic.creatorStatus({{build_state: "stale"}}),
  accepted: logic.creatorStatus({{creator_acceptance: "accepted"}}),
  ready: logic.creatorStatus({{
    creator_acceptance: "accepted",
    independent_review: "approve",
    delivery_gate: "ready"
  }}),
  mixedDelivery: logic.creatorStatus({{
    build_state: {{materialized: 3}},
    validation_state: {{pass: 3}},
    creator_acceptance: {{accepted: 3}},
    independent_review: {{approve: 3}},
    delivery_gate: {{ready: 1, blocked: 2}}
  }}),
  failed: logic.creatorStatus({{
    build_state: {{failed: 1}},
    validation_state: {{not_run: 1}},
    creator_acceptance: {{not_requested: 1}},
    independent_review: {{not_requested: 1}},
    delivery_gate: {{blocked: 1}}
  }}),
  recovery: logic.creatorStatus({{
    build_state: "materialized",
    validation_state: "pass",
    creator_acceptance: "accepted",
    independent_review: "approve",
    delivery_gate: "ready"
  }}, {{needed: true}}),
  typedDuringSave: logic.savedContentIsCurrent("sent", "sent plus more"),
  refreshFailure: logic.statusRefreshFailureMessage()
}};
process.stdout.write(JSON.stringify(result));
"""
        completed = run_node(script)
        result = json.loads(completed.stdout)

        self.assertEqual(result["draft"], ["创作中", "neutral"])
        self.assertEqual(result["pending"], ["待你确认", "warning"])
        self.assertEqual(result["pendingBlocked"], ["待你确认", "warning"])
        self.assertEqual(result["rejected"], ["需要修改", "danger"])
        self.assertEqual(result["revise"], ["需要修改", "danger"])
        self.assertEqual(result["stale"], ["需要更新", "warning"])
        self.assertEqual(result["accepted"], ["已采用", "success"])
        self.assertEqual(result["ready"], ["可以导出", "success"])
        self.assertEqual(result["mixedDelivery"], ["已采用", "neutral"])
        self.assertEqual(result["failed"], ["需要修改", "danger"])
        self.assertEqual(result["recovery"], ["需要更新", "warning"])
        self.assertFalse(result["typedDuringSave"])
        self.assertIn("状态刷新失败", result["refreshFailure"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_hides_machine_files_and_groups_creator_content(self) -> None:
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const paths = [
  "short-drama.json",
  "README.md",
  "输入/original-source.txt",
  "inputs/original-source.txt",
  "项目开发/story-engine.md",
  "development/story-engine.md",
  "剧集/EP001/screenplay.md",
  "episodes/EP001/screenplay.md",
  "设定集/characters.jsonl",
  "剧集/EP001/assets/image-prompts.md",
  "剧集/EP001/storyboard/shots.jsonl",
  "剧集/EP001/制作成果/image/SHOT001.png",
  "剧集/EP001/storyboard/coverage.json",
  "剧集/EP001/storyboard/delivery-containers.jsonl",
  "创作者决策/EP001-script.json",
  "审查/EP001-findings.jsonl",
  "交付/EP001/manifest.json"
];
process.stdout.write(JSON.stringify(paths.map((path) => logic.creatorSection(path))));
"""
        completed = run_node(script)

        self.assertEqual(
            json.loads(completed.stdout),
            [
                None,
                "project",
                "sources",
                "sources",
                "project",
                "project",
                "story",
                "story",
                "cast",
                "prompts",
                "storyboard",
                "production",
                None,
                None,
                None,
                None,
                None,
            ],
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_overview_summarizes_without_owning_production(self) -> None:
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const files = [
  {{ path: "剧集/EP001/screenplay.md", type: "text" }},
  {{ path: "剧集/EP001/storyboard/video-prompts.md", type: "text" }},
  {{ path: "剧集/EP001/制作成果/image/SHOT001.png", type: "media", size: 2048 }},
  {{ path: "剧集/EP002/screenplay.md", type: "text" }},
  {{ path: "剧集/EP002/制作成果/tts/LINE001.wav", type: "media", size: 4096 }},
];
const overview = logic.projectOverviewModel(files, {{ title: "逆光告白" }});
process.stdout.write(JSON.stringify({{
  title: overview.title,
  episodes: overview.episodes.length,
  documents: overview.documents,
  media: overview.media.length,
  kinds: overview.media.map(logic.mediaKind),
  stages: overview.episodes.map((episode) => logic.episodePresentation(episode.files).label),
  size: logic.formatBytes(2048),
}}));
"""
        completed = run_node(script)
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "title": "逆光告白",
                "episodes": 2,
                "documents": 3,
                "media": 2,
                "kinds": ["image", "audio"],
                "stages": ["已有媒体", "已有媒体"],
                "size": "2.0 KB",
            },
        )

        index = (dashboard_server.STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        frontend = app.read_text(encoding="utf-8")
        self.assertIn('id="mediaGallery"', index)
        self.assertIn('id="episodeStrip"', index)
        self.assertNotIn("/api/production", frontend)
        self.assertNotIn("adapter-config", frontend)

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_structured_projection_removes_machine_fields(self) -> None:
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const projected = logic.creatorProjection({{
  name: "林夏",
  description: "克制、敏锐",
  sources: {{screenplay: {{owner: "short-drama-assets", artifact: "剧集/EP001/screenplay.md", hash: "a".repeat(64)}}}},
  source_ref: {{src: "screenplay"}},
  owner: "short-drama-assets",
  schema_version: "1.0",
  appearance: {{costume: "浅灰风衣", shape: "窄肩长款", shadow_direction: "右后方", evidence_hash: "b".repeat(64)}},
  empathy: "嘴硬心软",
  notes: [
    "保留旧木盒",
    "参考 剧集/EP001/screenplay.md 第三场",
    {{path: "设定集/props.jsonl", value: "木盒", revision: "c".repeat(40)}}
  ],
  lifecycle: {{status: "candidate"}},
  local_file: "/Users/creator/project/notes",
}});
process.stdout.write(JSON.stringify(projected));
"""
        completed = run_node(script)
        visible = completed.stdout
        projected = json.loads(visible)

        self.assertEqual(projected["name"], "林夏")
        self.assertEqual(projected["description"], "克制、敏锐")
        self.assertEqual(
            projected["appearance"],
            {"costume": "浅灰风衣", "shape": "窄肩长款", "shadow_direction": "右后方"},
        )
        self.assertEqual(projected["empathy"], "嘴硬心软")
        self.assertIn("第三场", projected["notes"][1])
        for forbidden in (
            "source_ref",
            "sources",
            "src",
            "artifact",
            "hash",
            "owner",
            "schema_version",
            "short-drama-assets",
            "剧集/EP001/screenplay.md",
            "设定集/props.jsonl",
            "candidate",
            "/Users/creator/project/notes",
            "c" * 40,
        ):
            self.assertNotIn(forbidden, visible)

    def test_open_flag_is_opt_in(self) -> None:
        class FakeServer:
            server_address = ("127.0.0.1", 43210)
            access_token = "test-capability"
            workspace_fingerprint = "0123456789abcdef"

            def shutdown(self) -> None:
                pass

            def serve_forever(self) -> None:
                raise KeyboardInterrupt

            def server_close(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    dashboard_server, "create_server", return_value=FakeServer()
                ),
                patch.object(dashboard_server.webbrowser, "open") as browser,
            ):
                self.assertEqual(
                    dashboard_server.main(["--workspace", directory]),
                    0,
                )
                browser.assert_not_called()

                self.assertEqual(
                    dashboard_server.main(["--workspace", directory, "--open"]),
                    0,
                )
                browser.assert_called_once_with(
                    "http://127.0.0.1:43210/#test-capability"
                )

    def test_ipv6_loopback_is_rejected_until_the_server_supports_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "IPv4 loopback"):
                create_server(Path(directory), host="::1", port=0)

    def test_each_server_uses_a_distinct_session_path_and_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = create_server(Path(directory), port=0)
            second = create_server(Path(directory), port=0)
            try:
                self.assertNotEqual(first.api_prefix, second.api_prefix)
                self.assertNotEqual(first.session_cookie, second.session_cookie)
            finally:
                first.server_close()
                second.server_close()


    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_preserves_creator_content_the_projection_used_to_delete(self) -> None:
        # A look's validity range reads like a path but is a creator id. Deleting
        # it left the row as "—", so the creator saw no validity range at all.
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const projected = logic.creatorProjection({{
  validity: {{ from: "EP003/SC004/BLK-EP003-SC004-A01", until: "EP003/SC006/BLK-EP003-SC006-A03" }},
  source: "\u5267\u96c6/EP001/storyboard/shots.jsonl",
}});
process.stdout.write(JSON.stringify([
  projected.validity?.from ?? null,
  Object.prototype.hasOwnProperty.call(projected, "source"),
  logic.friendlyKey("look_id") !== logic.friendlyKey("base_look_id"),
]));
"""
        completed = run_node(script)
        self.assertEqual(
            json.loads(completed.stdout),
            ["EP003/SC004/BLK-EP003-SC004-A01", False, True],
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_editable_suffixes_match_the_server(self) -> None:
        # The client must not shadow the server with a narrower allowlist: that
        # silently removed subtitle editing, a shipped feature.
        app = dashboard_server.STATIC_ROOT / "app.js"
        editable = sorted(dashboard_server.TEXT_EXTENSIONS)
        script = f"""
const logic = require({json.dumps(str(app))});
const suffixes = {json.dumps(editable)};
process.stdout.write(JSON.stringify(
  suffixes.map((suffix) => logic.creatorEditable({{ writable: true, path: `a/b${{suffix}}` }})),
));
"""
        completed = run_node(script)
        self.assertEqual(json.loads(completed.stdout), [True] * len(editable))

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_keeps_unexpected_creator_files_reachable(self) -> None:
        # The workspace is the only UI, so a file it refuses to list is a file the
        # creator cannot open at all. Machine and lifecycle roots stay hidden.
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const paths = [
  "\u5907\u5fd8.md",
  "\u53c2\u8003\u8d44\u6599/topic.md",
  "short-drama.json",
  "\u5ba1\u67e5/EP001-findings.jsonl",
  "\u4ea4\u4ed8/EP001/manifest.json",
];
process.stdout.write(JSON.stringify(paths.map((path) => logic.creatorSection(path))));
"""
        completed = run_node(script)
        self.assertEqual(
            json.loads(completed.stdout),
            ["other", "other", None, None, None],
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_preview_survives_one_malformed_record(self) -> None:
        # An interrupted agent run leaves a truncated line. The records around it
        # must still render, or the file becomes unreadable and unrepairable.
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const rows = logic.readJsonLines('{{"a":1}}\\n{{broken\\n{{"c":3}}');
process.stdout.write(JSON.stringify(rows.map((row) => (row.error ? "error" : "record"))));
"""
        completed = run_node(script)
        self.assertEqual(json.loads(completed.stdout), ["record", "error", "record"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_markdown_preserves_structure_and_treats_html_as_text(
        self,
    ) -> None:
        # Generated prompts are meant to be selected and copied as one block, and
        # numbered shot order must not read as unrelated sentences. Creator text
        # must also stay text: project files are untrusted input to this renderer.
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
class N {{
  constructor(tag) {{ this.tagName = (tag || "").toUpperCase(); this.children = []; this.textContent = ""; this.className = ""; this.dataset = {{}}; }}
  set innerHTML(_value) {{ throw new Error("unsafe HTML sink"); }}
  append(...kids) {{ for (const kid of kids) this.children.push(kid); }}
  get text() {{ return this.textContent || this.children.map((kid) => (kid.text !== undefined ? kid.text : String(kid.data ?? ""))).join(""); }}
}}
const logic = require({json.dumps(str(app))});
globalThis.document = {{
  createElement: (tag) => new N(tag),
  createTextNode: (data) => ({{ data, text: String(data) }}),
  createDocumentFragment: () => new N("#fragment"),
  getElementById: () => null,
}};
const rendered = logic.renderMarkdown("```\\nopen the door, 9:16\\n# camera: dolly in\\n```\\n\\n1. near\\n2. mid\\n\\n- note");
const block = rendered.children.find((node) => node.tagName === "PRE");
const attack = `<img src=x onerror="globalThis.projectTextExecuted=true">`;
const escaped = logic.renderMarkdown(attack);
process.stdout.write(JSON.stringify([
  rendered.children.map((node) => node.tagName),
  Boolean(block && block.text.includes("# camera: dolly in")),
  escaped.text === attack && globalThis.projectTextExecuted === undefined,
]));
"""
        completed = run_node(script)
        tags, fence_kept, html_kept_as_text = json.loads(completed.stdout)
        self.assertEqual(tags, ["PRE", "OL", "UL"])
        self.assertTrue(fence_kept)
        self.assertTrue(html_kept_as_text)

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_understands_creator_first_episode_documents(self) -> None:
        # Raw directory order buried the screenplay below every generated prompt.
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const files = [
  {{ path: "\u5267\u96c6/EP001/\u5206\u955c.md", type: "text" }},
  {{ path: "\u5267\u96c6/EP001/\u56fe\u7247\u63d0\u793a\u8bcd.md", type: "text" }},
  {{ path: "\u5267\u96c6/EP001/\u5267\u672c.md", type: "text" }},
  {{ path: "\u5267\u96c6/EP001/\u89c6\u89c9\u8bbe\u5b9a.md", type: "text" }},
  {{ path: "\u5267\u96c6/EP001/\u89c6\u9891\u63d0\u793a\u8bcd.md", type: "text" }},
];
process.stdout.write(JSON.stringify({{
  order: logic.orderedForReading(files).map((file) => file.path.split("/").pop()),
  sections: Object.fromEntries(files.map((file) => [file.path.split("/").pop(), logic.creatorSection(file.path)])),
  presentation: logic.episodePresentation(files),
  reviewSection: logic.creatorSection("审查/EP001-审查.md"),
  legacyReviewSection: logic.creatorSection("审查/EP001-findings.jsonl"),
}}));
"""
        completed = run_node(script)
        result = json.loads(completed.stdout)
        self.assertEqual(result["order"][0], "剧本.md")
        self.assertEqual(
            result["sections"],
            {
                "剧本.md": "story",
                "视觉设定.md": "cast",
                "分镜.md": "storyboard",
                "图片提示词.md": "prompts",
                "视频提示词.md": "prompts",
            },
        )
        self.assertEqual(result["presentation"]["label"], "提示词就绪")
        self.assertEqual(result["reviewSection"], "review")
        self.assertIsNone(result["legacyReviewSection"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_translates_server_protocol_messages(self) -> None:
        # The server's English protocol strings were reaching the creator UI raw.
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
process.stdout.write(JSON.stringify([
  logic.friendlyFailure("file changed since it was opened"),
  logic.friendlyFailure("file is locked or not writable"),
  logic.friendlyFailure("an unmapped message"),
  logic.creatorTitle({{ zh: "\u6d4b\u8bd5" }}),
  logic.creatorTitle("\u9006\u5149\u544a\u767d"),
]));
"""
        completed = run_node(script)
        translated, busy, passthrough, guarded, kept = json.loads(completed.stdout)
        self.assertNotIn("file changed", translated)
        self.assertNotIn("not writable", busy)
        self.assertEqual(passthrough, "an unmapped message")
        self.assertEqual(guarded, "未命名短剧")
        self.assertEqual(kept, "逆光告白")


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.project = self.workspace / "show"
        make_project(self.project, "HTTP 项目")
        (self.project / "notes.md").write_text("hello", encoding="utf-8")
        self.server = create_server(self.workspace, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, method: str, path: str, *, headers=None, body=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=3)
        request_headers = {"Host": f"127.0.0.1:{self.port}"}
        if "/api/" in path.split("?", 1)[0]:
            request_headers["X-Short-Drama-Token"] = self.server.access_token
        request_headers.update(headers or {})
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        return response.status, response_headers, data

    def test_api_requires_a_per_launch_session_capability(self) -> None:
        status, _, _ = self.request(
            "GET", "/api/projects", headers={"X-Short-Drama-Token": ""}
        )
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "GET", "/api/projects", headers={"X-Short-Drama-Token": "wrong"}
        )
        self.assertEqual(status, 401)

        status, headers, body = self.request("POST", "/api/session")
        self.assertEqual(status, 200)
        session = json.loads(body)
        self.assertEqual(session["status"], "ready")
        self.assertEqual(session["apiBase"], self.server.api_prefix)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn(f"Path={self.server.api_prefix}/", headers["Set-Cookie"])
        status, _, _ = self.request(
            "GET",
            f"{session['apiBase']}/api/projects",
            headers={"X-Short-Drama-Token": "", "Cookie": cookie},
        )
        self.assertEqual(status, 200)

    def test_a_non_ascii_capability_is_answered_not_dropped(self) -> None:
        # hmac.compare_digest raises TypeError on a str holding a codepoint
        # above U+007F, and header bytes arrive decoded as iso-8859-1. Raised
        # from _security_ok, which runs outside every handler's try block, that
        # closed the socket with no status line at all — pre-authentication.
        # Only latin-1-encodable values reach the header at all; every byte
        # from 0x80 up decodes to a codepoint compare_digest refused.
        for token in ("\xff\xfe", "é", "\x80"):
            with self.subTest(token=token):
                status, _, _ = self.request(
                    "GET", "/api/projects", headers={"X-Short-Drama-Token": token}
                )
                self.assertEqual(status, 401)

    def test_a_malformed_project_manifest_is_reported_not_dropped(self) -> None:
        # Valid JSON that is not an object reached project.get() and raised
        # AttributeError, which no handler's except tuple listed.
        broken = self.workspace / "broken"
        make_project(broken)
        (broken / "short-drama.json").write_text("[]", encoding="utf-8")
        try:
            status, _, body = self.request("GET", "/api/projects")
            self.assertEqual(status, 200)
            identifier = next(
                item["id"]
                for item in json.loads(body)["projects"]
                if item["path"] == "broken"
            )
            status, _, body = self.request("GET", f"/api/status?project={identifier}")
            self.assertEqual(status, 500)
            self.assertIn("error", json.loads(body))
        finally:
            shutil.rmtree(broken)

    def project_id(self) -> str:
        status, _, body = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        return json.loads(body)["projects"][0]["id"]

    def test_serves_frontend_and_calls_real_project_status(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(body)
        self.assertIn("Content-Security-Policy", headers)
        status, _, body = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(body)

        project_id = self.project_id()
        status, _, body = self.request("GET", f"/api/status?project={project_id}")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["title"], "HTTP 项目")
        self.assertIn("lifecycle", payload)

    def test_rejects_non_loopback_bind_host_and_untrusted_headers(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            create_server(self.workspace, host="0.0.0.0", port=0, suite_root=SUITE)

        status, _, _ = self.request(
            "GET", "/api/projects", headers={"Host": "evil.example"}
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "GET",
            "/api/projects",
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "GET",
            "/api/projects",
            headers={"Host": f"evil@127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 403)

    def test_http_put_requires_version_and_returns_conflict(self) -> None:
        project_id = self.project_id()
        path = f"/api/file?project={project_id}&path=notes.md"
        status, _, body = self.request("GET", path)
        opened = json.loads(body)
        self.assertEqual(status, 200)

        missing_version = json.dumps({"content": "new"}).encode()
        status, _, _ = self.request(
            "PUT",
            path,
            headers={"Content-Type": "application/json"},
            body=missing_version,
        )
        self.assertEqual(status, 400)
        good = json.dumps(
            {"content": "new", "expectedVersion": opened["version"]}
        ).encode()
        status, _, body = self.request(
            "PUT", path, headers={"Content-Type": "application/json"}, body=good
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.project.joinpath("notes.md").read_text(), "new")
        status, _, _ = self.request(
            "PUT", path, headers={"Content-Type": "application/json"}, body=good
        )
        self.assertEqual(status, 409)

    def test_http_put_allows_json_escaping_within_the_file_limit(self) -> None:
        project_id = self.project_id()
        path = f"/api/file?project={project_id}&path=notes.md"
        status, _, body = self.request("GET", path)
        self.assertEqual(status, 200)
        opened = json.loads(body)
        content = "\x00" * 370_000
        payload = json.dumps(
            {"content": content, "expectedVersion": opened["version"]}
        ).encode()
        self.assertGreater(len(payload), 2 * 1024 * 1024 + 64 * 1024)

        status, _, _ = self.request(
            "PUT", path, headers={"Content-Type": "application/json"}, body=payload
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.project.joinpath("notes.md").stat().st_size, len(content))

    def test_media_endpoint_serves_complete_image_with_safe_headers(self) -> None:
        media = self.project / "episodes/EP001/assets/poster.png"
        media.parent.mkdir(parents=True)
        image = b"\x89PNG\r\n\x1a\npreview-bytes"
        media.write_bytes(image)
        project_id = self.project_id()
        status, _, body = self.request(
            "GET", f"/api/media?project={project_id}&path=episodes%2FEP001%2Fassets%2Fposter.png"
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["contentType"], "image/png")
        self.assertIn("/api/media/content?", payload["contentUrl"])

        status, headers, body = self.request("GET", payload["contentUrl"])
        self.assertEqual(status, 200)
        self.assertEqual(body, image)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Content-Length"], str(len(image)))
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

        status, headers, body = self.request("HEAD", payload["contentUrl"])
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers["Content-Length"], str(len(image)))

    def test_video_content_supports_single_byte_ranges(self) -> None:
        media = self.project / "episodes/EP001/storyboard/demo.mp4"
        media.parent.mkdir(parents=True)
        video = b"0123456789"
        media.write_bytes(video)
        project_id = self.project_id()
        path = f"/api/media/content?project={project_id}&path=episodes%2FEP001%2Fstoryboard%2Fdemo.mp4"

        status, headers, body = self.request(
            "GET", path, headers={"Range": "bytes=2-5"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"2345")
        self.assertEqual(headers["Content-Type"], "video/mp4")
        self.assertEqual(headers["Content-Length"], "4")
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")

        status, headers, body = self.request("GET", path, headers={"Range": "bytes=-3"})
        self.assertEqual(status, 206)
        self.assertEqual(body, b"789")
        self.assertEqual(headers["Content-Range"], "bytes 7-9/10")

        status, headers, body = self.request(
            "GET", path, headers={"Range": "bytes=50-60"}
        )
        self.assertEqual(status, 416)
        self.assertEqual(body, b"")
        self.assertEqual(headers["Content-Range"], "bytes */10")

    def test_media_content_reuses_path_and_request_security_boundaries(self) -> None:
        outside = self.workspace / "outside.png"
        outside.write_bytes(b"outside")
        linked = self.project / "episodes/EP001/assets/linked.png"
        linked.parent.mkdir(parents=True)
        try:
            linked.symlink_to(outside)
        except OSError:
            self.skipTest("symbolic links unavailable")
        project_id = self.project_id()

        status, _, _ = self.request(
            "GET",
            f"/api/media/content?project={project_id}&path=episodes%2FEP001%2Fassets%2Flinked.png",
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "GET",
            f"/api/media/content?project={project_id}&path=..%2Foutside.png",
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "GET",
            f"/api/media/content?project={project_id}&path=episodes%2FEP001%2Fassets%2Flinked.png",
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)


class PathPinnedDashboardHTTPTests(DashboardHTTPTests):
    """Serve the same HTTP surface through the backend Windows must use.

    The store tests cover the backend directly; this covers what reaches a
    creator -- byte ranges, conditional saves, media headers -- over a socket.
    """

    def setUp(self) -> None:
        patcher = patch.object(dashboard_server, "SECURE_DIR_FD", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()
        self.assertIs(
            self.server.store.backend, dashboard_server._PathDirectory
        )


if __name__ == "__main__":
    unittest.main()
