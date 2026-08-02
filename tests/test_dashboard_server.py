import contextlib
import concurrent.futures
import hashlib
import http.client
import importlib.util
import json
import shutil
import subprocess
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
        tool = SimpleNamespace(
            project_status_at=canonical.project_status_at,
            is_protected_project_text=canonical.is_protected_project_text,
            coordinated_project_text_edit_at=canonical.coordinated_project_text_edit_at,
            project_path_lifecycle_at=canonical.project_path_lifecycle_at,
        )
        return ProjectStore(workspace, tool, **limits)

    def test_rejects_a_project_tool_without_the_dashboard_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Dashboard contract"):
                ProjectStore(
                    Path(directory), SimpleNamespace(project_status=lambda _root: {})
                )

    def test_discovers_manifests_without_following_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            make_project(workspace / "alpha")
            make_project(workspace / "nested/beta")
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
            # only thing serializing them is the project's own transaction flock
            # held across the compare-and-replace. If that lock were absent both
            # writers would observe the same version and both would save.
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
        if not dashboard_server.SECURE_DIR_FD:
            self.skipTest("secure dir-fd traversal is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            project = workspace / "show"
            outside = Path(directory) / "outside"
            make_project(project, "原项目")
            make_project(outside, "外部项目")
            (project / "inside.md").write_text("inside", encoding="utf-8")
            (outside / "outside-secret.md").write_text("outside", encoding="utf-8")
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
            self.assertEqual(status["lifecycle"]["build_state"], {"stale": 1})
            self.assertEqual(
                status["lifecycle"]["creator_acceptance"], {"not_requested": 1}
            )
            self.assertEqual(status["lifecycle"]["delivery_gate"], {"blocked": 1})
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["artifacts"]["EP001:script"]["build_state"], "stale"
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

            self.assertEqual(status["lifecycle"]["build_state"], {"stale": 1})
            self.assertEqual(status["lifecycle"]["delivery_gate"], {"blocked": 1})

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

            self.assertEqual(status["lifecycle"]["build_state"], {"stale": 1})
            self.assertEqual(status["lifecycle"]["delivery_gate"], {"blocked": 1})

    def test_parent_directory_swap_cannot_redirect_a_text_write(self) -> None:
        if not dashboard_server.SECURE_DIR_FD:
            self.skipTest("secure dir-fd traversal is unavailable on this platform")
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

            original_open_parent = dashboard_server._open_parent_directory_at

            @contextlib.contextmanager
            def swap_parent(root_fd: int, relative: PurePosixPath):
                nonlocal swapped
                if relative.as_posix() == "inside/notes.txt" and not swapped:
                    inside.rename(project / "inside-original")
                    inside.symlink_to(outside, target_is_directory=True)
                    swapped = True
                with original_open_parent(root_fd, relative) as opened:
                    yield opened

            with patch.object(
                dashboard_server, "_open_parent_directory_at", swap_parent
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
        if not dashboard_server.SECURE_DIR_FD:
            self.skipTest("secure dir-fd traversal is unavailable on this platform")
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

            original_open_parent = dashboard_server._open_parent_directory_at

            @contextlib.contextmanager
            def swap_parent(root_fd: int, relative: PurePosixPath):
                nonlocal swapped
                if relative.as_posix() == "episodes/EP001/storyboard/clip.mp4" and not swapped:
                    storyboard.rename(project / "storyboard-original")
                    storyboard.symlink_to(outside, target_is_directory=True)
                    swapped = True
                with original_open_parent(root_fd, relative) as opened:
                    yield opened

            with patch.object(
                dashboard_server, "_open_parent_directory_at", swap_parent
            ):
                with self.assertRaises(DashboardError) as caught:
                    store.open_media(project_id, "episodes/EP001/storyboard/clip.mp4")

            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(outside_target.read_bytes(), b"OUTSIDE-SECRET")

    def test_unsafe_platform_fails_closed_for_project_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "show"
            make_project(project)
            (project / "notes.txt").write_text("text", encoding="utf-8")
            (project / "clip.mp4").write_bytes(b"media")

            # Patched before construction: the store refuses to exist at all
            # without directory descriptors, rather than serving a browse tree
            # whose every file, save and preview answers 501.
            with patch.object(dashboard_server, "SECURE_DIR_FD", False):
                with self.assertRaisesRegex(RuntimeError, "unsupported"):
                    self.store(workspace)

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


class DashboardEntrypointTests(unittest.TestCase):
    def test_router_skill_declares_dashboard_launch_command(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("$short-drama dashboard", skill)
        self.assertIn("scripts/dashboard_server.py", skill)
        self.assertIn("--workspace <workspace> --port 0 --open", skill)

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

    def test_frontend_exposes_safe_creator_facing_review_states(self) -> None:
        html = (dashboard_server.STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        javascript = (dashboard_server.STATIC_ROOT / "app.js").read_text(
            encoding="utf-8"
        )
        stylesheet = (dashboard_server.STATIC_ROOT / "styles.css").read_text(
            encoding="utf-8"
        )

        for element_id in (
            "allCount",
            "developmentCount",
            "writingCount",
            "assetsCount",
            "storyboardCount",
            "reviewCount",
            "fileMeta",
            "projectTitle",
            "axisCount",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn("renderMarkdown", javascript)
        self.assertIn("validateStructuredText", javascript)
        self.assertIn("媒体预览已载入", javascript)
        self.assertIn('development: "项目开发"', javascript)
        self.assertIn('bible: "设定集"', javascript)
        self.assertIn('episodes: "剧集"', javascript)
        self.assertIn('"creator-decisions": "创作者决策"', javascript)
        self.assertIn("sectionOf", javascript)
        self.assertIn("displayPath", javascript)
        self.assertNotIn("宣发素材", html)
        self.assertNotIn('data-domain="promo"', html)
        self.assertNotIn("SHORT DRAMA SUITE", html)
        self.assertNotIn("PROJECT HEALTH", html)
        for forbidden_copy in (
            "不是",
            "而是",
            "不会",
            "不等于",
            "底层设计",
            "安全边界",
        ):
            self.assertNotIn(forbidden_copy, html + javascript)
        self.assertNotIn("innerHTML", javascript)
        self.assertIn("prefers-reduced-motion", stylesheet)

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_status_labels_require_explicit_evidence(self) -> None:
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const result = {{
  filenameOnly: logic.mediaBadge("剧集/EP001/storyboard/final.mp4", "video"),
  accepted: logic.mediaBadge("剧集/EP001/storyboard/final.mp4", "video", {{
    creator_acceptance: "accepted",
    independent_review: "approve",
    delivery_gate: "ready"
  }}),
  emptyDelivery: logic.deliverySummary({{}}, {{needed: false}}),
  pendingDelivery: logic.deliverySummary({{delivery_gate: {{not_evaluated: 1}}}}, {{needed: false}}),
  readyDelivery: logic.deliverySummary({{delivery_gate: {{ready: 1}}}}, {{needed: false}}),
  mixedDelivery: logic.deliverySummary(
    {{delivery_gate: {{ready: 1}}}},
    {{needed: false}},
    {{mode: "mixed"}}
  ),
  typedDuringSave: logic.savedContentIsCurrent("sent", "sent plus more"),
  unknownTone: logic.toneFor({{not_evaluated: 1}}),
  failedTone: logic.toneFor({{fail: 1}}),
  legacyCheckpoint: logic.checkpointLabel("demo-ready"),
  unknownCheckpoint: logic.checkpointLabel("custom-stage"),
  refreshFailure: logic.statusRefreshFailureMessage()
}};
process.stdout.write(JSON.stringify(result));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["filenameOnly"], ["视频素材 · 待审", "warning"])
        self.assertEqual(result["accepted"], ["正式成片", "success"])
        self.assertEqual(result["emptyDelivery"]["value"], "尚无可交付产物")
        self.assertEqual(result["pendingDelivery"]["tone"], "warning")
        self.assertEqual(result["readyDelivery"]["tone"], "success")
        self.assertEqual(result["mixedDelivery"]["tone"], "danger")
        self.assertEqual(result["mixedDelivery"]["value"], "中英文目录重复")
        self.assertFalse(result["typedDuringSave"])
        self.assertEqual(result["unknownTone"], "warning")
        self.assertEqual(result["failedTone"], "danger")
        self.assertEqual(result["legacyCheckpoint"], "分镜制作")
        self.assertEqual(result["unknownCheckpoint"], "custom-stage")
        self.assertIn("状态刷新失败", result["refreshFailure"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_frontend_sections_follow_project_artifact_ownership(self) -> None:
        app = dashboard_server.STATIC_ROOT / "app.js"
        script = f"""
const logic = require({json.dumps(str(app))});
const paths = [
  "short-drama.json",
  "README.md",
  "demo.mp4",
  "输入/original-source.txt",
  "inputs/original-source.txt",
  "references/demo.mp4",
  "参考/demo.png",
  "项目开发/story-engine.md",
  "development/story-engine.md",
  "Development/story-engine.md",
  "剧集/EP001/screenplay.md",
  "episodes/EP001/screenplay.md",
  "Episodes/EP001/screenplay.md",
  "剧集/EP001/notes.md",
  "设定集/characters.jsonl",
  "剧集/EP001/assets/image-prompts.md",
  "剧集/EP001/storyboard/shots.jsonl",
  "剧集/EP001/media/demo.mp4",
  "创作者决策/EP001-script.json",
  "审查/EP001-findings.jsonl",
  "交付/EP001/manifest.json",
  "宣发/campaign.md"
];
process.stdout.write(JSON.stringify(paths.map((path) => logic.sectionOf(path))));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            json.loads(completed.stdout),
            [
                "development",
                "development",
                "other",
                "development",
                "development",
                "other",
                "other",
                "development",
                "development",
                "other",
                "writing",
                "writing",
                "other",
                "other",
                "assets",
                "assets",
                "storyboard",
                "other",
                "review",
                "review",
                "review",
                "other",
            ],
        )

    def test_open_flag_is_opt_in(self) -> None:
        class FakeServer:
            server_address = ("127.0.0.1", 43210)
            access_token = "test-capability"

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
        self.assertIn("项目控制台", body.decode("utf-8"))
        self.assertIn("Content-Security-Policy", headers)
        status, _, body = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        frontend = body.decode("utf-8")
        self.assertIn(
            'document.createElement(info.kind === "video" ? "video" : "img")',
            frontend,
        )
        self.assertIn("cleanupMedia", frontend)
        self.assertIn('file.path.toLowerCase() === "readme.md"', frontend)
        self.assertGreater(len(frontend.splitlines()), 100)

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


if __name__ == "__main__":
    unittest.main()
