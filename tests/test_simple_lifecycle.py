from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SUITE = Path(__file__).resolve().parents[1]
SKILL = SUITE / "skills/short-drama"
SCRIPT = SKILL / "scripts/project_tool.py"
SPEC = importlib.util.spec_from_file_location("simple_lifecycle", SCRIPT)
assert SPEC and SPEC.loader
project_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(project_tool)


class SimpleLifecycleTests(unittest.TestCase):
    def make_project(self, directory: str, *, chinese: bool = True) -> Path:
        root = Path(directory) / "project"
        project_tool.initialize_project(
            root,
            title="轻量短剧",
            language="zh-CN",
            aspect_ratio="9:16",
            suite_root=SKILL,
        )
        if not chinese:
            state_path = root / ".short-drama/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["project_layout_mode"] = "legacy"
            project_tool.atomic_json(state_path, state)
        return root

    def publish_script(
        self, root: Path, *, input_path: str | None = None, text: str = "# 第一集\n"
    ) -> None:
        project_tool.publish_candidate(
            root,
            owner="short-drama-write",
            artifact_id="EP001:script",
            outputs={"剧集/EP001/screenplay.md": text},
            inputs=[input_path] if input_path else [],
        )

    def approve_script(self, root: Path) -> None:
        project_tool.record_creator_acceptance(
            root, artifact_id="EP001:script", decision="accepted"
        )
        project_tool.record_review(
            root,
            artifact_id="EP001:script",
            verdict="approve",
            reviewer="review pass",
        )

    def test_init_creates_small_state_and_creator_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            state = json.loads((root / ".short-drama/state.json").read_text())
            project = json.loads((root / "short-drama.json").read_text())
            self.assertEqual(state["schema_version"], "2.0")
            self.assertEqual(state["artifacts"], {})
            self.assertNotIn("current_checkpoint", project)
            self.assertNotIn("current_checkpoint", project_tool.project_status(root))
            self.assertNotIn("active_transaction", state)
            self.assertNotIn("blocked_transactions", state)
            for relative in ("输入", "项目开发", "设定集", "剧集", "交付", "创作者决策", "审查"):
                self.assertTrue((root / relative).is_dir(), relative)
            self.assertFalse((root / ".short-drama/transactions").exists())

    def test_cli_exposes_no_recovery_command(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        for command in ("publish", "accept", "review", "package", "verify"):
            self.assertIn(command, result.stdout)
        self.assertNotIn("recover", result.stdout)
        self.assertFalse(hasattr(project_tool, "record_independent_review"))

    def test_publish_accept_review_use_one_creator_facing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            self.assertEqual(
                project_tool.project_status(root)["artifacts"]["EP001:script"],
                "needs_confirmation",
            )
            project_tool.record_creator_acceptance(
                root, artifact_id="EP001:script", decision="accepted"
            )
            self.assertEqual(
                project_tool.project_status(root)["artifacts"]["EP001:script"],
                "accepted",
            )
            project_tool.record_review(
                root, artifact_id="EP001:script", verdict="approve"
            )
            status = project_tool.project_status(root)
            self.assertEqual(status["artifacts"]["EP001:script"], "approved")
            self.assertEqual(status["lifecycle"], {"artifact_state": {"approved": 1}})

    def test_rejection_and_revision_are_plain_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            project_tool.record_creator_acceptance(
                root, artifact_id="EP001:script", decision="rejected", note="重写开场"
            )
            self.assertEqual(
                project_tool.project_status(root)["artifacts"]["EP001:script"],
                "revise",
            )
            self.publish_script(root, text="# 第一集\n新版\n")
            project_tool.record_creator_acceptance(
                root, artifact_id="EP001:script", decision="accepted"
            )
            project_tool.record_review(
                root, artifact_id="EP001:script", verdict="revise", note="动机不足"
            )
            self.assertEqual(
                project_tool.project_status(root)["artifacts"]["EP001:script"],
                "revise",
            )

    def test_direct_input_change_is_detected_without_propagation_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "项目开发/episode-map.jsonl"
            source.write_text('{"episode_id":"EP001"}\n', encoding="utf-8")
            self.publish_script(root, input_path="项目开发/episode-map.jsonl")
            self.approve_script(root)
            source.write_text('{"episode_id":"EP001","changed":true}\n', encoding="utf-8")
            status = project_tool.project_status(root)
            self.assertEqual(status["artifacts"]["EP001:script"], "update_needed")
            persisted = json.loads((root / ".short-drama/state.json").read_text())
            self.assertNotIn("stale", json.dumps(persisted))

    def test_output_edit_invalidates_acceptance_on_read_without_rewriting_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            self.approve_script(root)
            state_before = (root / ".short-drama/state.json").read_bytes()
            (root / "剧集/EP001/screenplay.md").write_text("外部修改\n", encoding="utf-8")
            self.assertEqual(
                project_tool.project_status(root)["artifacts"]["EP001:script"],
                "update_needed",
            )
            self.assertEqual((root / ".short-drama/state.json").read_bytes(), state_before)

    def test_republish_replaces_current_acceptance_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            self.approve_script(root)
            self.publish_script(root, text="# 第一集\n第二版\n")
            state = json.loads((root / ".short-drama/state.json").read_text())
            record = state["artifacts"]["EP001:script"]
            self.assertIsNone(record["acceptance"])
            self.assertIsNone(record["review"])

    def test_review_requires_current_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            with self.assertRaisesRegex(project_tool.ProjectConflictError, "acceptance"):
                project_tool.record_review(
                    root, artifact_id="EP001:script", verdict="approve"
                )

    def test_review_accepts_a_plain_reviewer_label_without_provenance_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            project_tool.record_creator_acceptance(
                root, artifact_id="EP001:script", decision="accepted"
            )
            result = project_tool.record_review(
                root,
                artifact_id="EP001:script",
                verdict="approve_with_notes",
                reviewer="同事复核",
                note="可以投产",
            )
            self.assertEqual(result["state"], "approved")

    def test_acceptance_refuses_a_changed_direct_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "项目开发/episode-map.jsonl"
            source.write_text("{}\n", encoding="utf-8")
            self.publish_script(root, input_path="项目开发/episode-map.jsonl")
            source.write_text('{"changed":true}\n', encoding="utf-8")
            with self.assertRaisesRegex(project_tool.ProjectConflictError, "input changed"):
                project_tool.record_creator_acceptance(
                    root, artifact_id="EP001:script", decision="accepted"
                )

    def test_publish_rejects_invalid_json_and_jsonl_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for relative, content in (
                ("剧集/EP001/episode-card.json", "{"),
                ("剧集/EP001/beats.jsonl", '{}\n{"bad"\n'),
            ):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    project_tool.publish_candidate(
                        root,
                        owner="short-drama-write",
                        artifact_id=f"bad:{relative}",
                        outputs={relative: content},
                    )
                self.assertFalse((root / relative).exists())

    def test_publish_allows_first_writer_and_protects_non_stage_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            published = project_tool.publish_candidate(
                root,
                owner="independent-script-skill",
                artifact_id="independent-script",
                outputs={"剧集/EP001/screenplay.md": "x"},
            )
            self.assertEqual(published["owner"], "independent-script-skill")
            with self.assertRaisesRegex(ValueError, "immutable"):
                project_tool.publish_candidate(
                    root,
                    owner="short-drama-write",
                    artifact_id="input",
                    outputs={"输入/source.md": "x"},
                )
            with self.assertRaisesRegex(ValueError, "packaging gate"):
                project_tool.publish_candidate(
                    root,
                    owner="short-drama-write",
                    artifact_id="delivery",
                    outputs={"交付/EP001/manifest.json": "{}"},
                )

    def test_publish_rejects_path_aliases_and_unsafe_episode_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for relative in (
                "剧集/EP001/storyboard /shots.jsonl",
                "剧集/EP001/storyboard./shots.jsonl",
                "剧集/EP001/CON.json",
                "剧集/EP001/notes\nextra.md",
                "剧集/EP001/a:b.json",
            ):
                with self.subTest(relative=relative):
                    with self.assertRaisesRegex(ValueError, "unsafe project-relative path"):
                        project_tool.publish_candidate(
                            root,
                            owner="short-drama-write",
                            artifact_id="nonportable",
                            outputs={relative: "x"},
                        )

            with self.assertRaises(project_tool.NonPortablePathError):
                project_tool.publish_candidate(
                    root,
                    owner="short-drama-write",
                    artifact_id="aliases",
                    outputs={
                        "剧集/EP001/notes.md": "a",
                        "剧集/EP001/NOTES.md": "b",
                    },
                )

            composed = "项目开发/caf\N{LATIN SMALL LETTER E WITH ACUTE}.md"
            decomposed = "项目开发/cafe\N{COMBINING ACUTE ACCENT}.md"
            with self.assertRaisesRegex(
                project_tool.NonPortablePathError, "portable aliases"
            ):
                project_tool.publish_candidate(
                    root,
                    owner="independent-development-skill",
                    artifact_id="unicode-aliases",
                    outputs={composed: "a", decomposed: "b"},
                )

            with self.assertRaisesRegex(ValueError, "EP001-style"):
                project_tool.publish_candidate(
                    root,
                    owner="short-drama-write",
                    artifact_id="episode",
                    outputs={"剧集/1/screenplay.md": "x"},
                )

    def test_one_output_path_has_one_artifact_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            with self.assertRaisesRegex(ValueError, "already belongs"):
                project_tool.publish_candidate(
                    root,
                    owner="short-drama-write",
                    artifact_id="another",
                    outputs={"剧集/EP001/screenplay.md": "other"},
                )

    def test_legacy_state_is_read_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            target = root / "剧集/EP001/screenplay.md"
            target.parent.mkdir(parents=True)
            target.write_text("legacy", encoding="utf-8")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            state_path = root / ".short-drama/state.json"
            legacy = json.loads(state_path.read_text())
            legacy["schema_version"] = "1.0.0-draft"
            legacy["artifacts"] = {
                "EP001:script": {
                    "owner": "short-drama-write",
                    "candidate_targets": {"剧集/EP001/screenplay.md": digest},
                    "accepted_targets": {"剧集/EP001/screenplay.md": digest},
                    "creator_acceptance": "accepted",
                    "independent_review": "approve",
                }
            }
            project_tool.atomic_json(state_path, legacy)
            before = state_path.read_bytes()
            self.assertEqual(
                project_tool.project_status(root)["artifacts"]["EP001:script"],
                "approved",
            )
            self.assertEqual(state_path.read_bytes(), before)

    def test_publish_migrates_legacy_state_on_the_next_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            state_path = root / ".short-drama/state.json"
            state = json.loads(state_path.read_text())
            state["schema_version"] = "1.0.0-draft"
            project_tool.atomic_json(state_path, state)
            self.publish_script(root)
            migrated = json.loads(state_path.read_text())
            self.assertEqual(migrated["schema_version"], "2.0")
            self.assertNotIn("active_transaction", migrated)

    def test_legacy_migration_keeps_the_current_candidate_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            target = root / "剧集/EP001/screenplay.md"
            target.parent.mkdir(parents=True)
            target.write_text("new candidate", encoding="utf-8")
            direct_input = root / "输入/current.md"
            direct_input.write_text("current input", encoding="utf-8")
            old_input = root / "输入/old.md"
            old_input.write_text("old input", encoding="utf-8")
            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()
            state_path = root / ".short-drama/state.json"
            legacy = json.loads(state_path.read_text())
            legacy["schema_version"] = "1.0.0-draft"
            legacy["artifacts"] = {
                "EP001:script": {
                    "owner": "short-drama-write",
                    "candidate_targets": {str(target.relative_to(root)): digest(target)},
                    "accepted_targets": {str(target.relative_to(root)): "0" * 64},
                    "candidate_inputs": {
                        str(direct_input.relative_to(root)): digest(direct_input)
                    },
                    "accepted_inputs": {str(old_input.relative_to(root)): digest(old_input)},
                    "creator_acceptance": "accepted",
                }
            }
            project_tool.atomic_json(state_path, legacy)

            project_tool.record_creator_acceptance(
                root, artifact_id="EP001:script", decision="accepted"
            )
            direct_input.write_text("changed after accepting candidate", encoding="utf-8")
            self.assertEqual(
                project_tool.project_status(root)["artifacts"]["EP001:script"],
                "update_needed",
            )

    def test_package_requires_current_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            with self.assertRaisesRegex(project_tool.PackageBlockedError, "approved"):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    includes=["剧集/EP001/screenplay.md"],
                )

    def test_package_rejects_cross_episode_includes_and_omissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            project_tool.publish_candidate(
                root,
                owner="short-drama-write",
                artifact_id="EP002:script",
                outputs={"剧集/EP002/screenplay.md": "# 第二集\n"},
            )
            project_tool.record_creator_acceptance(
                root, artifact_id="EP002:script", decision="accepted"
            )
            project_tool.record_review(
                root,
                artifact_id="EP002:script",
                verdict="approve",
                reviewer="reviewer",
            )
            with self.assertRaisesRegex(ValueError, "belong to EP001"):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    includes=["剧集/EP002/screenplay.md"],
                )

            self.publish_script(root)
            self.approve_script(root)
            with self.assertRaisesRegex(ValueError, "belong to EP001"):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    includes=["剧集/EP001/screenplay.md"],
                    omissions={"剧集/EP002/notes.md": "not requested"},
                )

    def test_package_writes_the_same_approved_bytes_it_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            approved = b"# first approved version\n"
            self.publish_script(root, text=approved.decode())
            self.approve_script(root)
            source = root / "剧集/EP001/screenplay.md"
            real_sha256_bytes = project_tool.sha256_bytes

            def mutate_after_hash(content: bytes) -> str:
                digest = real_sha256_bytes(content)
                source.write_bytes(b"unapproved race bytes\n")
                return digest

            with mock.patch.object(
                project_tool, "sha256_bytes", side_effect=mutate_after_hash
            ):
                result = project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    includes=["剧集/EP001/screenplay.md"],
                )
            delivered = root / result["delivery_root"] / "artifacts/剧集/EP001/screenplay.md"
            self.assertEqual(delivered.read_bytes(), approved)

    def test_package_rejects_bytes_changed_after_approval_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root, text="# approved\n")
            self.approve_script(root)
            source = root / "剧集/EP001/screenplay.md"
            real_artifact_state = project_tool._artifact_state

            def mutate_after_check(project_root, record):
                state = real_artifact_state(project_root, record)
                source.write_text("unapproved before snapshot", encoding="utf-8")
                return state

            with mock.patch.object(
                project_tool, "_artifact_state", side_effect=mutate_after_check
            ), self.assertRaisesRegex(
                project_tool.PackageBlockedError, "changed after approval"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    includes=["剧集/EP001/screenplay.md"],
                )
            self.assertFalse((root / "交付/EP001").exists())

    def test_package_and_verify_selected_approved_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            self.approve_script(root)
            result = project_tool.build_delivery_package(
                root,
                episode="EP001",
                includes=["剧集/EP001/screenplay.md"],
            )
            delivery = root / result["delivery_root"]
            self.assertTrue((delivery / "manifest.json").is_file())
            self.assertTrue((delivery / "checksums.sha256").is_file())
            self.assertTrue((delivery / "artifacts/剧集/EP001/screenplay.md").is_file())
            verified = project_tool.verify_delivery_package(root, episode="EP001")
            self.assertEqual(verified, {"episode": "EP001", "status": "verified", "problems": []})

    def test_verify_detects_tamper_and_unlisted_addition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            self.approve_script(root)
            result = project_tool.build_delivery_package(
                root, episode="EP001", includes=["剧集/EP001/screenplay.md"]
            )
            delivery = root / result["delivery_root"]
            (delivery / "artifacts/剧集/EP001/screenplay.md").write_text("tampered")
            (delivery / "extra.md").write_text("extra")
            verified = project_tool.verify_delivery_package(root, episode="EP001")
            self.assertEqual(verified["status"], "tampered")
            self.assertTrue(any("checksum mismatch" in item for item in verified["problems"]))
            self.assertTrue(any("unexpected delivery file" in item for item in verified["problems"]))

    def test_verify_checks_manifest_file_hashes_even_if_checksums_are_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            self.approve_script(root)
            result = project_tool.build_delivery_package(
                root, episode="EP001", includes=["剧集/EP001/screenplay.md"]
            )
            delivery = root / result["delivery_root"]
            artifact = delivery / "artifacts/剧集/EP001/screenplay.md"
            artifact.write_text("laundered bytes", encoding="utf-8")
            members = [delivery / "manifest.json", artifact]
            checksums = "".join(
                f"{project_tool.sha256_file(path)}  {path.relative_to(delivery).as_posix()}\n"
                for path in members
            )
            (delivery / "checksums.sha256").write_text(checksums, encoding="utf-8")

            verified = project_tool.verify_delivery_package(root, episode="EP001")
            self.assertEqual(verified["status"], "tampered")
            self.assertTrue(
                any("manifest hash mismatch" in item for item in verified["problems"])
            )

    def test_verify_rejects_a_symlinked_delivery_parent(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            external = Path(directory) / "external-delivery"
            package = external / "EP001"
            package.mkdir(parents=True)
            (package / "checksums.sha256").write_text("", encoding="utf-8")
            (root / "交付").rmdir()
            try:
                (root / "交付").symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            with self.assertRaisesRegex(
                project_tool.ProjectConflictError, "delivery root"
            ):
                project_tool.verify_delivery_package(root, episode="EP001")

    def test_package_replaces_a_previous_package_as_one_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            self.approve_script(root)
            project_tool.build_delivery_package(
                root, episode="EP001", includes=["剧集/EP001/screenplay.md"]
            )
            self.publish_script(root, text="# 第一集\n第二版\n")
            self.approve_script(root)
            project_tool.build_delivery_package(
                root, episode="EP001", includes=["剧集/EP001/screenplay.md"]
            )
            delivered = root / "交付/EP001/artifacts/剧集/EP001/screenplay.md"
            self.assertIn("第二版", delivered.read_text(encoding="utf-8"))
            self.assertFalse(any((root / "交付").glob(".EP001.*.old")))

    def test_package_records_explicit_omissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root)
            self.approve_script(root)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                includes=["剧集/EP001/screenplay.md"],
                omissions={"剧集/EP001/notes.md": "not requested"},
            )
            manifest = json.loads((root / "交付/EP001/manifest.json").read_text())
            self.assertEqual(
                manifest["omitted"],
                [{"reason": "not requested", "source": "剧集/EP001/notes.md"}],
            )

    def test_status_never_exposes_hashes_or_creative_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.publish_script(root, text="秘密台词")
            serialized = json.dumps(project_tool.project_status(root), ensure_ascii=False)
            self.assertNotIn("秘密台词", serialized)
            self.assertIsNone(__import__("re").search(r"[0-9a-f]{64}", serialized))

    def test_coordinated_edit_rejects_an_old_version(self) -> None:
        if os.name == "nt":
            self.skipTest("dashboard descriptor contract is POSIX-only")
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            target = root / "notes.md"
            target.write_text("one", encoding="utf-8")
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaisesRegex(project_tool.ProjectConflictError, "changed"):
                    with project_tool.coordinated_project_text_edit_at(
                        descriptor, "notes.md", hashlib.sha256(b"old").hexdigest()
                    ):
                        pass
            finally:
                os.close(descriptor)

    def test_cli_runs_publish_accept_review_package_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "draft.md"
            source.write_text("# 第一集\n", encoding="utf-8")
            commands = [
                ["publish", str(root), "--owner", "short-drama-write", "--artifact-id", "EP001:script", "--output", "剧集/EP001/screenplay.md=draft.md"],
                ["accept", str(root), "--artifact-id", "EP001:script", "--decision", "accepted"],
                ["review", str(root), "--artifact-id", "EP001:script", "--verdict", "approve"],
                ["package", str(root), "--episode", "EP001", "--include", "剧集/EP001/screenplay.md"],
                ["verify", str(root), "--episode", "EP001"],
            ]
            for command in commands:
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *command],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsInstance(json.loads(result.stdout), dict)

    def test_pacing_rates_have_a_write_path_and_a_hand_edit_is_reported(self) -> None:
        # A/B Round 2: `duration_estimate.py` requires `format.pacing`, publish
        # refuses `short-drama.json`, and set-authority accepted only
        # `target_seconds_per_episode` — so the rates could reach the manifest
        # only by hand, and no command anywhere reported that it had happened.
        relative = "创作者决策/decisions.jsonl"
        decision = {
            "decision_id": "CD-PACING-001",
            "status": "accepted",
            "accepted_value": {
                "spoken_characters_per_second": 5.0,
                "seconds_per_action_paragraph": 2.5,
            },
            "target_locators": [{"src": "short-drama", "field": "/format/pacing"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            project_tool.publish_candidate(
                root,
                owner="short-drama",
                artifact_id="project:decisions",
                outputs={relative: (json.dumps(decision, ensure_ascii=False) + "\n").encode()},
            )
            project_tool.record_creator_acceptance(
                root, artifact_id="project:decisions", decision="accepted"
            )
            project_tool.set_creator_authority(
                root,
                field="/format/pacing",
                decision_path=relative,
                decision_id="CD-PACING-001",
            )

            manifest = root / "short-drama.json"
            written = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(written["format"]["pacing"]["spoken_characters_per_second"], 5.0)
            self.assertEqual(
                project_tool.project_status(root)["authority"], {"/format/pacing": "bound"}
            )

            edited = json.loads(manifest.read_text(encoding="utf-8"))
            edited["format"]["pacing"]["spoken_characters_per_second"] = 6.5
            project_tool.atomic_json(manifest, edited)
            self.assertEqual(
                project_tool.project_status(root)["authority"],
                {"/format/pacing": "hand_edited"},
            )

    def test_pacing_rejects_rates_that_are_not_positive_numbers(self) -> None:
        # The object slot is merged, not replaced, so a decision that sets only
        # one rate leaves the other at null: the write would report `bound` while
        # `duration_estimate.py` still refuses to produce seconds.
        relative = "创作者决策/decisions.jsonl"
        decision = {
            "decision_id": "CD-PACING-BAD",
            "status": "accepted",
            "accepted_value": {"spoken_characters_per_second": 5.0},
            "target_locators": [{"src": "short-drama", "field": "/format/pacing"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            project_tool.publish_candidate(
                root,
                owner="short-drama",
                artifact_id="project:decisions",
                outputs={relative: (json.dumps(decision, ensure_ascii=False) + "\n").encode()},
            )
            project_tool.record_creator_acceptance(
                root, artifact_id="project:decisions", decision="accepted"
            )
            with self.assertRaisesRegex(ValueError, "positive number"):
                project_tool.set_creator_authority(
                    root,
                    field="/format/pacing",
                    decision_path=relative,
                    decision_id="CD-PACING-BAD",
                )

    def test_set_authority_writes_only_through_an_accepted_decision(self) -> None:
        decisions = [
            {
                "decision_id": "CD-PROFILE-001",
                "status": "accepted",
                "accepted_value": {"form": "live_action"},
                "target_locators": [
                    {"src": "short-drama", "field": "/creator_authority/production_profile"}
                ],
            },
            {
                "decision_id": "CD-LENGTH-001",
                "status": "proposed",
                "accepted_value": 95,
                "target_locators": [
                    {"src": "short-drama", "field": "/format/target_seconds_per_episode"}
                ],
            },
        ]
        relative = "创作者决策/decisions.jsonl"
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "decisions.draft.jsonl"
            source.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in decisions),
                encoding="utf-8",
            )
            set_profile = dict(
                field="/creator_authority/production_profile",
                decision_path=relative,
                decision_id="CD-PROFILE-001",
            )
            with self.assertRaisesRegex(ValueError, "published"):
                project_tool.set_creator_authority(root, **set_profile)

            project_tool.publish_candidate(
                root,
                owner="short-drama",
                artifact_id="project:decisions",
                outputs={relative: source.read_bytes()},
            )
            with self.assertRaisesRegex(ValueError, "accepted and current"):
                project_tool.set_creator_authority(root, **set_profile)

            project_tool.record_creator_acceptance(
                root, artifact_id="project:decisions", decision="accepted"
            )
            project_tool.set_creator_authority(root, **set_profile)
            authority = json.loads((root / "short-drama.json").read_text(encoding="utf-8"))
            self.assertEqual(
                authority["creator_authority"]["production_profile"],
                {"status": "accepted", "choices": {"form": "live_action"}},
            )

            with self.assertRaisesRegex(ValueError, "not an accepted creator decision"):
                project_tool.set_creator_authority(
                    root,
                    field="/format/target_seconds_per_episode",
                    decision_path=relative,
                    decision_id="CD-LENGTH-001",
                )
            with self.assertRaisesRegex(ValueError, "creator_authority"):
                project_tool.set_creator_authority(
                    root, field="/title", decision_path=relative, decision_id="CD-PROFILE-001"
                )
            self.assertIsNone(
                json.loads((root / "short-drama.json").read_text(encoding="utf-8"))["format"][
                    "target_seconds_per_episode"
                ]
            )

    def test_set_authority_defects_found_by_review(self) -> None:
        """One case per defect an independent review reproduced.

        Each of these wrote a value the creator never approved, or a manifest
        another reader rejects, while the whole suite stayed green.
        """
        relative = "创作者决策/decisions.jsonl"
        records = [
            # A retracted decision, and the revision that retracted it.
            {"decision_id": "CD-S1", "status": "accepted", "accepted_value": 180,
             "target_locators": [{"src": "short-drama", "field": "/format/target_seconds_per_episode"}]},
            {"decision_id": "CD-S2", "status": "accepted", "supersedes_decision_id": "CD-S1",
             "accepted_value": 90,
             "target_locators": [{"src": "short-drama", "field": "/format/target_seconds_per_episode"}]},
            # Append-only revision reusing one id: the later line is the decision.
            {"decision_id": "CD-R", "status": "accepted", "accepted_value": 60,
             "target_locators": [{"src": "short-drama", "field": "/format/target_seconds_per_episode"}]},
            {"decision_id": "CD-R", "status": "accepted", "accepted_value": 45,
             "target_locators": [{"src": "short-drama", "field": "/format/target_seconds_per_episode"}]},
            # A choice written deeper than the block that gates it.
            {"decision_id": "CD-LOOK", "status": "accepted", "accepted_value": "水墨",
             "target_locators": [{"src": "short-drama",
                                  "field": "/creator_authority/visual_direction/choices/look_development"}]},
            # Infinity parses in Python and serialises back out, but is not JSON.
            {"decision_id": "CD-INF", "status": "accepted", "accepted_value": float("inf"),
             "target_locators": [{"src": "short-drama", "field": "/format/target_seconds_per_episode"}]},
            {"decision_id": "CD-NEW", "status": "accepted", "accepted_value": "x",
             "target_locators": [{"src": "short-drama", "field": "/creator_authority/新造字段"}]},
            {"decision_id": "CD-TYPE", "status": "accepted", "accepted_value": "一句话",
             "target_locators": [{"src": "short-drama", "field": "/creator_authority/constraints"}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "decisions.draft.jsonl"
            source.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )
            project_tool.publish_candidate(
                root, owner="short-drama", artifact_id="project:decisions",
                outputs={relative: source.read_bytes()},
            )
            project_tool.record_creator_acceptance(
                root, artifact_id="project:decisions", decision="accepted"
            )

            def apply(field: str, decision_id: str) -> None:
                project_tool.set_creator_authority(
                    root, field=field, decision_path=relative, decision_id=decision_id
                )

            def manifest() -> dict:
                return json.loads((root / "short-drama.json").read_text(encoding="utf-8"))

            with self.assertRaisesRegex(ValueError, "superseded"):
                apply("/format/target_seconds_per_episode", "CD-S1")
            with self.assertRaisesRegex(ValueError, "not JSON compliant"):
                apply("/format/target_seconds_per_episode", "CD-INF")
            with self.assertRaisesRegex(ValueError, "declares no"):
                apply("/creator_authority/新造字段", "CD-NEW")
            with self.assertRaisesRegex(ValueError, "accepted_value is str"):
                apply("/creator_authority/constraints", "CD-TYPE")

            apply("/format/target_seconds_per_episode", "CD-R")
            self.assertEqual(manifest()["format"]["target_seconds_per_episode"], 45)

            apply("/creator_authority/visual_direction/choices/look_development", "CD-LOOK")
            direction = manifest()["creator_authority"]["visual_direction"]
            self.assertEqual(direction["choices"]["look_development"], "水墨")
            self.assertEqual(direction["status"], "accepted")

            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            binding = state["authority"]["/format/target_seconds_per_episode"]
            self.assertEqual(binding["decision"], f"{relative}#CD-R")
            self.assertIn("value_sha256", binding)

    def test_set_authority_second_round_defects(self) -> None:
        """A second review round; the first two were introduced by the first round's fixes."""
        relative = "创作者决策/decisions.jsonl"
        records = [
            # 45 is an int, 92.5 a float: comparing Python types made the field one-way.
            {"decision_id": "CD-INT", "status": "accepted", "accepted_value": 90,
             "target_locators": [{"src": "short-drama", "field": "/format/target_seconds_per_episode"}]},
            {"decision_id": "CD-FLOAT", "status": "accepted", "accepted_value": 92.5,
             "target_locators": [{"src": "short-drama", "field": "/format/target_seconds_per_episode"}]},
            # Writing the choices map replaced it, dropping choices already recorded.
            {"decision_id": "CD-LOOK", "status": "accepted", "accepted_value": "水墨",
             "target_locators": [{"src": "short-drama",
                                  "field": "/creator_authority/visual_direction/choices/look_development"}]},
            {"decision_id": "CD-CH", "status": "accepted",
             "accepted_value": {"direction": "克制现实主义"},
             "target_locators": [{"src": "short-drama",
                                  "field": "/creator_authority/visual_direction/choices"}]},
            # Where decisions live is layout: a decision must not move it.
            {"decision_id": "CD-DA", "status": "accepted", "accepted_value": "../../外部目录/",
             "target_locators": [{"src": "short-drama",
                                  "field": "/creator_authority/decisions_artifact"}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "decisions.draft.jsonl"
            source.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )
            project_tool.publish_candidate(
                root, owner="short-drama", artifact_id="project:decisions",
                outputs={relative: source.read_bytes()},
            )
            project_tool.record_creator_acceptance(
                root, artifact_id="project:decisions", decision="accepted"
            )

            def apply(field: str, decision_id: str) -> None:
                project_tool.set_creator_authority(
                    root, field=field, decision_path=relative, decision_id=decision_id
                )

            def manifest() -> dict:
                return json.loads((root / "short-drama.json").read_text(encoding="utf-8"))

            apply("/format/target_seconds_per_episode", "CD-INT")
            apply("/format/target_seconds_per_episode", "CD-FLOAT")
            self.assertEqual(manifest()["format"]["target_seconds_per_episode"], 92.5)

            apply("/creator_authority/visual_direction/choices/look_development", "CD-LOOK")
            apply("/creator_authority/visual_direction/choices", "CD-CH")
            self.assertEqual(
                manifest()["creator_authority"]["visual_direction"]["choices"],
                {"look_development": "水墨", "direction": "克制现实主义"},
            )

            with self.assertRaisesRegex(ValueError, "decisions_artifact"):
                apply("/creator_authority/decisions_artifact", "CD-DA")
            self.assertEqual(
                manifest()["creator_authority"]["decisions_artifact"], "创作者决策/"
            )

    def test_project_discovery_and_languages_remain_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            self.assertEqual(project_tool.find_project(root / "剧集"), root.resolve())
            status = project_tool.project_status(root)
            self.assertEqual(status["language"], "zh-CN")
            self.assertEqual(status["prompt_language"], "en")


if __name__ == "__main__":
    unittest.main()


class CreatorExportTests(unittest.TestCase):
    DOCUMENTS = (
        "剧本.md",
        "视觉设定.md",
        "分镜.md",
        "图片提示词.md",
        "视频提示词.md",
    )

    def make_project(self, directory: str, *, episodes: tuple[str, ...] = ("EP001",)) -> Path:
        root = Path(directory) / "project"
        project_tool.initialize_project(
            root,
            title="导出验证",
            language="zh-CN",
            aspect_ratio="9:16",
            suite_root=SKILL,
        )
        for episode in episodes:
            episode_root = root / "剧集" / episode
            episode_root.mkdir(parents=True)
            for name in self.DOCUMENTS:
                (episode_root / name).write_text(f"# {episode} {name}\n", encoding="utf-8")
            media = episode_root / "制作成果"
            media.mkdir()
            (media / f"SHOT-{episode}-001.mp4").write_bytes(b"rendered bytes")
        inputs = root / "输入"
        inputs.mkdir(exist_ok=True)
        (inputs / "私有素材.txt").write_text("private source\n", encoding="utf-8")
        return root

    def test_export_copies_current_documents_and_produced_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory, episodes=("EP001", "EP002"))
            out = Path(directory) / "handover"
            result = project_tool.build_creator_export(root, out=out)
            self.assertEqual(result["episodes"], ["EP001", "EP002"])
            self.assertEqual(result["missing_documents"], {})
            for episode in ("EP001", "EP002"):
                for name in self.DOCUMENTS:
                    self.assertTrue((out / "剧集" / episode / name).is_file())
                self.assertTrue(
                    (out / "剧集" / episode / "制作成果" / f"SHOT-{episode}-001.mp4").is_file()
                )
            self.assertTrue((out / "short-drama.json").is_file())

    def test_export_leaves_private_inputs_and_operational_state_behind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            out = Path(directory) / "handover"
            project_tool.build_creator_export(root, out=out)
            copied = {path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()}
            self.assertFalse(any(name.startswith("输入/") for name in copied), copied)
            self.assertFalse(any(".short-drama" in name for name in copied), copied)

    def test_export_manifest_does_not_assert_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            out = Path(directory) / "handover"
            project_tool.build_creator_export(root, out=out)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], "creator_export")
            self.assertFalse(manifest["asserts_approval"])
            self.assertEqual(
                manifest["project_id"],
                json.loads((root / "short-drama.json").read_text(encoding="utf-8"))["project_id"],
            )

    def test_export_checksums_cover_every_exported_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            out = Path(directory) / "handover"
            project_tool.build_creator_export(root, out=out)
            recorded = {}
            for line in (out / "checksums.sha256").read_text(encoding="utf-8").splitlines():
                digest, relative = line.split("  ", 1)
                recorded[relative] = digest
            present = {
                path.relative_to(out).as_posix()
                for path in out.rglob("*")
                if path.is_file() and path.name != "checksums.sha256"
            }
            self.assertEqual(set(recorded), present)
            for relative, digest in recorded.items():
                self.assertEqual(
                    hashlib.sha256((out / relative).read_bytes()).hexdigest(), digest
                )

    def test_export_can_select_one_episode_and_skip_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory, episodes=("EP001", "EP002"))
            out = Path(directory) / "handover"
            result = project_tool.build_creator_export(
                root, out=out, episodes=["EP002"], include_media=False
            )
            self.assertEqual(result["episodes"], ["EP002"])
            self.assertFalse((out / "剧集/EP001").exists())
            self.assertFalse((out / "剧集/EP002/制作成果").exists())
            self.assertTrue((out / "剧集/EP002/剧本.md").is_file())

    def test_export_reports_documents_the_project_has_not_written_yet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            (root / "剧集/EP001/视频提示词.md").unlink()
            out = Path(directory) / "handover"
            result = project_tool.build_creator_export(root, out=out)
            self.assertEqual(result["missing_documents"], {"EP001": ["视频提示词.md"]})
            self.assertFalse((out / "剧集/EP001/视频提示词.md").exists())

    def test_export_refuses_a_destination_inside_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for candidate in (root, root / "交付/handover"):
                with self.subTest(destination=str(candidate)):
                    with self.assertRaises(ValueError):
                        project_tool.build_creator_export(root, out=candidate)

    def test_export_refuses_a_destination_that_contains_the_project(self) -> None:
        """The export replaces its destination wholesale, so a destination that
        contains the project would delete the project it is exporting."""
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            keepsake = root.parent / "irreplaceable.txt"
            keepsake.write_text("do not delete\n", encoding="utf-8")
            for destination in (root.parent, root.parent.parent, root):
                with self.subTest(destination=str(destination)):
                    with self.assertRaises(ValueError):
                        project_tool.build_creator_export(
                            root, out=destination, overwrite=True
                        )
            self.assertTrue(keepsake.is_file())
            self.assertTrue((root / "short-drama.json").is_file())
            self.assertTrue((root / "剧集/EP001/剧本.md").is_file())

    def test_export_refuses_a_destination_spelled_as_the_same_directory(self) -> None:
        """A case-insensitive volume and a differently normalised Unicode name
        both spell one directory two ways; a resolved-string comparison alone
        would let either slip past and delete the project."""
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            alias = Path(directory) / "alias"
            os.symlink(root, alias, target_is_directory=True)
            with self.assertRaises(ValueError):
                project_tool.build_creator_export(root, out=alias, overwrite=True)
            self.assertTrue((root / "剧集/EP001/剧本.md").is_file())

    def test_export_only_overwrites_a_directory_it_produced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            documents = Path(directory) / "Documents"
            (documents / "照片").mkdir(parents=True)
            (documents / "报税.pdf").write_text("tax\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                project_tool.build_creator_export(root, out=documents, overwrite=True)
            self.assertTrue((documents / "报税.pdf").is_file())
            self.assertTrue((documents / "照片").is_dir())
            # A real previous export may still be replaced.
            handover = Path(directory) / "handover"
            project_tool.build_creator_export(root, out=handover)
            project_tool.build_creator_export(root, out=handover, overwrite=True)
            self.assertTrue((handover / "manifest.json").is_file())

    def test_export_manifest_names_what_it_actually_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            (root / "设定集").mkdir(exist_ok=True)
            (root / "设定集/角色圣经.md").write_text("# 角色圣经\n", encoding="utf-8")
            out = Path(directory) / "handover"
            result = project_tool.build_creator_export(root, out=out)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("设定集", manifest["excluded"])
            self.assertIn("输入", manifest["excluded"])
            self.assertEqual(result["excluded"], manifest["excluded"])
            self.assertEqual(
                manifest["selection"],
                {"episodes": "all", "available_episodes": ["EP001"], "include_media": True},
            )

    def test_export_records_a_partial_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory, episodes=("EP001", "EP002"))
            out = Path(directory) / "handover"
            project_tool.build_creator_export(
                root, out=out, episodes=["EP002"], include_media=False
            )
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["selection"],
                {
                    "episodes": ["EP002"],
                    "available_episodes": ["EP001", "EP002"],
                    "include_media": False,
                },
            )

    def test_export_fails_closed_on_names_it_cannot_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            try:
                (root / "剧集/EP001/制作成果/bad\nname.mp4").write_bytes(b"v")
            except OSError:
                self.skipTest("this filesystem rejects newlines in filenames")
            out = Path(directory) / "handover"
            with self.assertRaises(project_tool.ProjectConflictError):
                project_tool.build_creator_export(root, out=out)
            self.assertFalse(out.exists())

    def test_export_fails_closed_on_a_symlinked_media_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory, episodes=("EP001", "EP002"))
            media = root / "剧集/EP002/制作成果"
            shutil.rmtree(media)
            try:
                media.symlink_to(root / "剧集/EP001/制作成果", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("this platform cannot create symlinks")
            out = Path(directory) / "handover"
            with self.assertRaises(project_tool.ProjectConflictError):
                project_tool.build_creator_export(root, out=out)
            self.assertFalse(out.exists())

    def test_export_reports_names_that_cannot_be_unpacked_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            (root / "剧集/EP001/制作成果/con.mp4").write_bytes(b"v")
            out = Path(directory) / "handover"
            result = project_tool.build_creator_export(root, out=out)
            self.assertEqual(
                result["windows_unsafe_paths"], ["剧集/EP001/制作成果/con.mp4"]
            )

    def test_export_refuses_an_existing_destination_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            out = Path(directory) / "handover"
            project_tool.build_creator_export(root, out=out)
            with self.assertRaises(FileExistsError):
                project_tool.build_creator_export(root, out=out)
            (out / "剧集/EP001/剧本.md").write_text("# 已改\n", encoding="utf-8")
            project_tool.build_creator_export(root, out=out, overwrite=True)
            self.assertEqual(
                (out / "剧集/EP001/剧本.md").read_text(encoding="utf-8"),
                "# EP001 剧本.md\n",
            )

    def test_export_rejects_unknown_or_malformed_episode_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            out = Path(directory) / "handover"
            with self.assertRaises(FileNotFoundError):
                project_tool.build_creator_export(root, out=out, episodes=["EP404"])
            with self.assertRaises(ValueError):
                project_tool.build_creator_export(root, out=out, episodes=["第一集"])

    def test_export_fails_closed_on_a_symlinked_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            secret = Path(directory) / "outside.md"
            secret.write_text("outside the project\n", encoding="utf-8")
            document = root / "剧集/EP001/剧本.md"
            document.unlink()
            try:
                document.symlink_to(secret)
            except (OSError, NotImplementedError):
                self.skipTest("this platform cannot create symlinks")
            out = Path(directory) / "handover"
            with self.assertRaises(project_tool.ProjectConflictError):
                project_tool.build_creator_export(root, out=out)
            self.assertFalse(out.exists())

    def test_export_command_line_prints_the_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            out = Path(directory) / "handover"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "export",
                    str(root),
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["kind"], "creator_export")
            self.assertEqual(payload["episodes"], ["EP001"])


class AcceptedBytesAreTheAcceptedBytesTests(unittest.TestCase):
    """Editing an accepted artifact behind the tool's back must un-accept it.

    This is the promise the whole publish/accept lifecycle rests on, and it had
    no test in either implementation. Both `_artifact_state` comparisons against
    the live hashes could be deleted and all 321 tests stayed green, which is
    also how the two transcriptions of these rules were free to drift -- the
    dashboard renders the directory-fd one.
    """

    def make_accepted_project(self, directory: str) -> Path:
        root = Path(directory) / "project"
        project_tool.initialize_project(
            root,
            title="轻量短剧",
            language="zh-CN",
            aspect_ratio="9:16",
            suite_root=SKILL,
        )
        project_tool.publish_candidate(
            root,
            owner="short-drama-write",
            artifact_id="EP001:script",
            outputs={"剧集/EP001/screenplay.md": "# 第一集\n"},
            inputs=[],
        )
        project_tool.record_creator_acceptance(
            root, artifact_id="EP001:script", decision="accepted"
        )
        return root

    def states(self, root: Path) -> dict[str, str]:
        return dict(project_tool.project_status(root)["artifacts"])

    def test_editing_an_accepted_output_returns_it_to_update_needed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_accepted_project(directory)
            self.assertEqual(self.states(root), {"EP001:script": "accepted"})

            (root / "剧集/EP001/screenplay.md").write_text(
                "# 第一集\n\n有人在工具之外改了这一行。\n", encoding="utf-8"
            )
            self.assertEqual(self.states(root), {"EP001:script": "update_needed"})

    def test_deleting_an_accepted_output_returns_it_to_update_needed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_accepted_project(directory)
            (root / "剧集/EP001/screenplay.md").unlink()
            self.assertEqual(self.states(root), {"EP001:script": "update_needed"})

    def test_both_implementations_agree_on_every_state(self) -> None:
        """The path reader and the fd reader are one state machine, not two."""

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_accepted_project(directory)
            screenplay = root / "剧集/EP001/screenplay.md"
            state_path = root / ".short-drama/state.json"

            def both() -> tuple[str, str]:
                record = json.loads(state_path.read_text(encoding="utf-8"))[
                    "artifacts"
                ]["EP001:script"]
                directory_fd = os.open(root, os.O_RDONLY)
                try:
                    return (
                        project_tool._artifact_state(root, record),
                        project_tool._artifact_state_at(directory_fd, record),
                    )
                finally:
                    os.close(directory_fd)

            by_path, by_fd = both()
            self.assertEqual((by_path, by_fd), ("accepted", "accepted"))

            screenplay.write_text("# 第一集\n\n改过了。\n", encoding="utf-8")
            by_path, by_fd = both()
            self.assertEqual((by_path, by_fd), ("update_needed", "update_needed"))

            screenplay.unlink()
            by_path, by_fd = both()
            self.assertEqual((by_path, by_fd), ("update_needed", "update_needed"))

    def test_only_the_two_documented_decisions_are_recorded(self) -> None:
        """A decision outside the whitelist would strand the artifact forever.

        It is written into state, matches neither `accepted` nor `rejected`, and
        leaves the artifact in `update_needed` with no way back.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_accepted_project(directory)
            with self.assertRaises(ValueError):
                project_tool.record_creator_acceptance(
                    root, artifact_id="EP001:script", decision="TOTALLY_BOGUS"
                )
            self.assertEqual(self.states(root), {"EP001:script": "accepted"})
