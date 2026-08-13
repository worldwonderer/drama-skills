import contextlib
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path


SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama/scripts/project_tool.py"
SPEC = importlib.util.spec_from_file_location("short_drama_public_lifecycle", SCRIPT)
assert SPEC and SPEC.loader
project_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(project_tool)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fresh_reviewer(excluded_owner: str) -> dict[str, object]:
    return {
        "owner": "short-drama-review",
        "kind": "independent_agent",
        "independent": True,
        "excluded_owner_skills": [excluded_owner],
        "provenance": {
            "context_id": "test-fresh-review-context",
            "fresh_context": True,
            "authored_reviewed_artifacts": False,
        },
    }


class PublicLifecycleCliTests(unittest.TestCase):
    def make_project(self, directory: str) -> Path:
        root = Path(directory) / "public lifecycle"
        project_tool.initialize_project(
            root,
            title="公开生命周期",
            language="zh-CN",
            aspect_ratio="9:16",
            suite_root=SUITE / "skills/short-drama",
        )
        return root

    def run_cli(
        self, *arguments: str, expected_code: int = 0
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            expected_code,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        document = json.loads(result.stdout) if result.stdout.strip() else None
        return result, document

    def approve_artifact(
        self,
        root: Path,
        *,
        artifact_id: str,
        owner: str,
        outputs: dict[str, str | bytes],
        input_hashes: dict[str, str] | None = None,
    ) -> dict[str, str]:
        project_tool.publish_candidate(
            root,
            owner=owner,
            artifact_id=artifact_id,
            outputs=outputs,
            input_hashes=input_hashes,
        )
        targets = {relative: digest(root / relative) for relative in outputs}
        slug = artifact_id.replace(":", "-")
        decision_relative = f"creator-decisions/{slug}.json"
        decision = root / decision_relative
        decision.parent.mkdir(parents=True, exist_ok=True)
        decision.write_text(
            json.dumps(
                {
                    "decision_id": f"CD-{slug}",
                    "decision_kind": "artifact_acceptance",
                    "artifact_id": artifact_id,
                    "status": "accepted",
                    "target_hashes": targets,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        project_tool.record_creator_acceptance(
            root,
            artifact_id=artifact_id,
            decision="accepted",
            target_hashes=targets,
            evidence_ref={
                "owner": "creator",
                "artifact": decision_relative,
                "hash": digest(decision),
                "record_id": f"CD-{slug}",
            },
        )
        findings_relative = f"reviews/{slug}-findings.jsonl"
        findings = root / findings_relative
        findings.parent.mkdir(parents=True, exist_ok=True)
        findings.write_text("", encoding="utf-8")
        verdict_relative = f"reviews/{slug}.json"
        verdict = root / verdict_relative
        verdict.write_text(
            json.dumps(
                {
                    "review_id": f"REVIEW-{slug}",
                    "scope": ["story_script"],
                    "reviewed_artifacts": [
                        {"owner": owner, "artifact": relative, "hash": value}
                        for relative, value in targets.items()
                    ],
                    "findings_ref": {
                        "owner": "short-drama-review",
                        "artifact": findings_relative,
                        "hash": digest(findings),
                    },
                    "requested_review_mode": "independent_agent",
                    "effective_review_mode": "fresh_agent",
                    "reviewer": fresh_reviewer(owner),
                    "structural_validation": "pass",
                    "verdict": "APPROVE",
                    "blocking_findings": [],
                    "open_blocker_count": 0,
                    "notes": [],
                    "required_reviewer_independence": True,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        project_tool.record_independent_review(
            root,
            artifact_id=artifact_id,
            verdict="approve",
            reviewed_targets=targets,
            verdict_ref={
                "owner": "short-drama-review",
                "artifact": verdict_relative,
                "hash": digest(verdict),
            },
        )
        return targets

    def test_help_exposes_separate_publish_accept_and_review_commands(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        for command in ("publish", "accept", "review"):
            self.assertIn(command, result.stdout)

    def test_new_projects_use_chinese_creator_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)

            for relative in (
                "输入",
                "项目开发",
                "设定集",
                "剧集",
                "交付",
                "创作者决策",
                "审查",
            ):
                self.assertTrue((root / relative).is_dir(), relative)
            self.assertFalse((root / "宣发").exists())
            for legacy in ("inputs", "development", "bible", "episodes", "delivery"):
                self.assertFalse((root / legacy).exists(), legacy)

    def test_chinese_layout_preserves_ownership_and_protected_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            with self.assertRaisesRegex(
                ValueError, "short-drama-assets owns 设定集/characters.jsonl"
            ):
                project_tool.publish_candidate(
                    root,
                    artifact_id="settings:chars",
                    owner="short-drama-storyboard",
                    outputs={"设定集/characters.jsonl": '{"id":"C1"}\n'},
                )
            with self.assertRaisesRegex(ValueError, "immutable publication sources"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="protected-input",
                    owner="short-drama-write",
                    outputs={"输入/source.md": "x\n"},
                )
            project_tool.publish_candidate(
                root,
                artifact_id="EP001:script-cn",
                owner="short-drama-write",
                outputs={"剧集/EP001/screenplay.md": "# 第一集\n"},
            )
            self.assertTrue((root / "剧集/EP001/screenplay.md").is_file())

    def test_first_stage_publication_pins_one_project_wide_layout(self) -> None:
        for first_root, conflicting_root, expected_mode in (
            ("剧集", "episodes", "canonical"),
            ("episodes", "剧集", "legacy"),
        ):
            with self.subTest(mode=expected_mode), tempfile.TemporaryDirectory() as directory:
                root = self.make_project(directory)
                initial = project_tool.project_status(root)["layout"]
                self.assertFalse(initial["pinned"])
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:script",
                    owner="short-drama-write",
                    outputs={f"{first_root}/EP001/screenplay.md": "# 第一集\n"},
                )

                resolved = project_tool.project_status(root)["layout"]
                self.assertEqual(resolved["mode"], expected_mode)
                self.assertTrue(resolved["pinned"])
                with self.assertRaisesRegex(ValueError, "平行目录"):
                    project_tool.publish_candidate(
                        root,
                        artifact_id="EP002:script",
                        owner="short-drama-write",
                        outputs={
                            f"{conflicting_root}/EP002/screenplay.md": "# 第二集\n"
                        },
                    )

    def test_concurrent_first_publications_cannot_pin_opposite_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            barrier = threading.Barrier(2)
            original_lock = project_tool._transaction_lock
            outcomes: list[tuple[str, str]] = []

            @contextlib.contextmanager
            def synchronized_lock(selected_root: Path):
                barrier.wait(timeout=3)
                with original_lock(selected_root):
                    yield

            def publish(stage_root: str) -> None:
                try:
                    project_tool.publish_candidate(
                        root,
                        artifact_id=f"{stage_root}:script",
                        owner="short-drama-write",
                        outputs={f"{stage_root}/EP001/screenplay.md": "# 第一集\n"},
                    )
                    outcomes.append((stage_root, "committed"))
                except ValueError:
                    outcomes.append((stage_root, "blocked"))

            with patch.object(
                project_tool, "_transaction_lock", synchronized_lock
            ):
                threads = [
                    threading.Thread(target=publish, args=(stage_root,))
                    for stage_root in ("剧集", "episodes")
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(
                sorted(result for _, result in outcomes), ["blocked", "committed"]
            )
            layout = project_tool.project_status(root)["layout"]
            self.assertIn(layout["mode"], {"canonical", "legacy"})
            self.assertNotEqual(
                (root / "剧集/EP001/screenplay.md").exists(),
                (root / "episodes/EP001/screenplay.md").exists(),
            )
            self.assertFalse(project_tool.recover_project(root)["blocked"])

    def test_mixed_layout_is_reported_and_blocks_new_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            project_tool.publish_candidate(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": "# 第一集\n"},
            )
            parallel = root / "剧集/EP002"
            parallel.mkdir(parents=True)
            (parallel / "screenplay.md").write_text("# 第二集\n", encoding="utf-8")

            layout = project_tool.project_status(root)["layout"]

            self.assertEqual(layout["mode"], "mixed")
            with self.assertRaisesRegex(ValueError, "请先迁移并合并"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP003:script",
                    owner="short-drama-write",
                    outputs={"episodes/EP003/screenplay.md": "# 第三集\n"},
                )

    def test_nonstandard_stage_root_casing_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)

            with self.assertRaisesRegex(ValueError, "大小写或拼写"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:script",
                    owner="short-drama-write",
                    outputs={"Episodes/EP001/screenplay.md": "# 第一集\n"},
                )

            parallel = root / "Episodes/EP001"
            parallel.mkdir(parents=True)
            (parallel / "screenplay.md").write_text("# 第一集\n", encoding="utf-8")
            layout = project_tool.project_status(root)["layout"]
            self.assertEqual(layout["mode"], "mixed")
            self.assertEqual(layout["nonstandardRoots"], ["Episodes"])

    def test_a_non_pinning_root_cannot_choose_the_project_layout(self) -> None:
        # Unregistered roots and `输入/` are excluded from layout *detection*, so they must
        # not be able to *pin* one either. A single --allow-unregistered-path
        # write into `publicity/` used to record legacy for the whole project —
        # while no legacy directory existed — and every later Chinese publish
        # was then refused, with no supported way to undo the pin.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)

            project_tool.publish_candidate(
                root,
                artifact_id="promo:1",
                owner="short-drama-develop",
                outputs={"publicity/campaign.md": "# 宣发\n"},
                allow_unregistered_path=True,
            )

            layout = project_tool.project_status(root)["layout"]
            self.assertFalse(layout["pinned"])
            self.assertEqual(layout["mode"], "canonical")
            self.assertNotIn("publicity", layout["roots"])

            project_tool.publish_candidate(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"剧集/EP001/screenplay.md": "# 第一集\n"},
            )
            resolved = project_tool.project_status(root)["layout"]
            self.assertEqual(resolved["mode"], "canonical")
            self.assertTrue(resolved["pinned"])

    def test_unattested_fallback_can_only_record_provisional_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            artifact_id = "EP001:script"
            owner = "short-drama-write"
            output = "episodes/EP001/screenplay.md"
            project_tool.publish_candidate(
                root,
                owner=owner,
                artifact_id=artifact_id,
                outputs={output: "# 第一集\n\n门被推开。\n"},
            )
            targets = {output: digest(root / output)}
            decision = root / "creator-decisions/ep001.json"
            decision.parent.mkdir(parents=True, exist_ok=True)
            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-EP001",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": artifact_id,
                        "status": "accepted",
                        "target_hashes": targets,
                    }
                ),
                encoding="utf-8",
            )
            project_tool.record_creator_acceptance(
                root,
                artifact_id=artifact_id,
                decision="accepted",
                target_hashes=targets,
                evidence_ref={
                    "owner": "creator",
                    "artifact": "creator-decisions/ep001.json",
                    "hash": digest(decision),
                    "record_id": "CD-EP001",
                },
            )
            findings = root / "reviews/ep001-findings.jsonl"
            findings.parent.mkdir(parents=True, exist_ok=True)
            findings.write_text("", encoding="utf-8")
            verdict = root / "reviews/ep001-verdict.json"
            verdict.write_text(
                json.dumps(
                    {
                        "review_id": "REVIEW-EP001-PROVISIONAL",
                        "scope": ["story_script"],
                        "reviewed_artifacts": [
                            {"owner": owner, "artifact": output, "hash": targets[output]}
                        ],
                        "findings_ref": {
                            "owner": "short-drama-review",
                            "artifact": "reviews/ep001-findings.jsonl",
                            "hash": digest(findings),
                        },
                        "requested_review_mode": "independent_agent",
                        "effective_review_mode": "unattested",
                        "reviewer": {
                            "owner": "short-drama-review",
                            "kind": "unattested",
                            "independent": False,
                            "excluded_owner_skills": [owner],
                            "provenance": None,
                        },
                        "structural_validation": "not_run",
                        "verdict": "PROVISIONAL",
                        "blocking_findings": [],
                        "open_blocker_count": 0,
                        "required_reviewer_independence": True,
                    }
                ),
                encoding="utf-8",
            )

            result = project_tool.record_independent_review(
                root,
                artifact_id=artifact_id,
                verdict="provisional",
                reviewed_targets=targets,
                verdict_ref={
                    "owner": "short-drama-review",
                    "artifact": "reviews/ep001-verdict.json",
                    "hash": digest(verdict),
                },
            )

            self.assertEqual(result["independent_review"], "provisional")
            state = json.loads((root / ".short-drama/state.json").read_text())
            record = state["artifacts"][artifact_id]
            self.assertEqual(record["delivery_gate"], "blocked")
            independence = record["review_evidence"]["reviewer_independence"]
            self.assertFalse(independence["attestation_structure_valid"])
            self.assertEqual(
                independence["verification_scope"], "declared_provenance_structure"
            )

    def test_publish_candidate_is_wal_backed_and_cannot_claim_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            sources = {
                "inputs/screenplay.md": "# 第一集\n\n门被推开。\n",
                "inputs/index.json": '{"episode":"EP001"}\n',
                "inputs/beats.jsonl": '{"beat_id":"BEAT-001"}\n',
            }
            for relative, content in sources.items():
                source = root / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(content, encoding="utf-8")
            outputs = {
                "episodes/EP001/screenplay.md": "inputs/screenplay.md",
                "episodes/EP001/screenplay-index.json": "inputs/index.json",
                "episodes/EP001/beats.jsonl": "inputs/beats.jsonl",
            }

            arguments = [
                "publish",
                str(root),
                "--owner",
                "short-drama-write",
                "--artifact-id",
                "EP001:script",
            ]
            for target, output_source in outputs.items():
                arguments.extend(["--output", f"{target}={output_source}"])
            _, result = self.run_cli(*arguments)

            assert result is not None
            transaction = (
                root
                / ".short-drama/transactions"
                / str(result["transaction_id"])
            )
            manifest = json.loads(
                (transaction / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["authority"], "candidate")
            self.assertEqual(manifest["owner"], "short-drama-write")
            self.assertTrue((transaction / "COMMIT").is_file())
            self.assertIn("COMMIT", (transaction / "wal.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(
                {entry["path"]: entry["expected_hash"] for entry in manifest["read_set"]},
                {source: digest(root / source) for source in sources},
            )

            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            record = state["artifacts"]["EP001:script"]
            self.assertEqual(record["owner"], "short-drama-write")
            self.assertEqual(
                record["candidate_targets"],
                {target: digest(root / target) for target in outputs},
            )
            self.assertNotIn("accepted_targets", record)
            self.assertEqual(record["creator_acceptance"], "pending")
            self.assertEqual(record["validation_state"], "not_run")
            self.assertEqual(record["independent_review"], "provisional")
            self.assertEqual(record["delivery_gate"], "blocked")

            rejected = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    *arguments,
                    "--creator-acceptance",
                    "accepted",
                    "--independent-review",
                    "approve",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)

    def test_publish_rejects_invalid_structured_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            invalid = root / "输入/invalid.jsonl"
            invalid.write_text('{"ok":true}\nnot-json\n', encoding="utf-8")

            result, _ = self.run_cli(
                "publish",
                str(root),
                "--owner",
                "short-drama-write",
                "--artifact-id",
                "EP001:invalid",
                "--output",
                "episodes/EP001/invalid.jsonl=输入/invalid.jsonl",
                expected_code=2,
            )

            self.assertIn("invalid candidate JSONL", result.stderr)
            self.assertFalse((root / "episodes/EP001/invalid.jsonl").exists())

    def test_publish_requires_structured_refs_in_exact_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            upstream = self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.json": '{"version":1}\n'},
            )
            upstream_path = "episodes/EP001/screenplay.json"
            target = "episodes/EP001/assets.json"
            content = (
                json.dumps(
                    {
                        "source_ref": {
                            "owner": "short-drama-write",
                            "artifact": upstream_path,
                            "hash": upstream[upstream_path],
                        }
                    }
                )
                + "\n"
            )

            with self.assertRaisesRegex(
                ValueError, "structured ref requires exact input"
            ):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:assets",
                    owner="short-drama-assets",
                    outputs={target: content},
                )

            project_tool.publish_candidate(
                root,
                artifact_id="EP001:assets",
                owner="short-drama-assets",
                outputs={target: content},
                input_hashes={upstream_path: upstream[upstream_path]},
            )
            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                state["artifacts"]["EP001:assets"]["candidate_inputs"],
                {upstream_path: upstream[upstream_path]},
            )

    def test_publish_rejects_unfilled_structured_ref_placeholder(self) -> None:
        # A candidate copied straight from a shipped template used to publish
        # cleanly while contributing zero dependency edges, so the exact-input
        # cross-check never ran on it.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            content = (
                json.dumps(
                    {
                        "source_ref": {
                            "owner": "short-drama-write",
                            "artifact": "episodes/EP001/screenplay.md",
                            "hash": "<sha256>",
                        }
                    }
                )
                + "\n"
            )
            with self.assertRaisesRegex(ValueError, "structured ref hash is unfilled"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:shots",
                    owner="short-drama-storyboard",
                    outputs={"episodes/EP001/storyboard/shots.jsonl": content},
                )

    def test_publish_rejects_incomplete_structured_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            complete = {
                "owner": "short-drama-write",
                "artifact": "episodes/EP001/screenplay.md",
                "hash": "a" * 64,
            }
            cases = {
                "owner": "structured ref owner is missing",
                "artifact": "structured ref artifact is missing",
                "hash": "structured ref hash is unfilled",
            }
            for missing, message in cases.items():
                with self.subTest(missing=missing):
                    reference = {key: value for key, value in complete.items() if key != missing}
                    content = json.dumps({"source_ref": reference}) + "\n"
                    with self.assertRaisesRegex(ValueError, message):
                        project_tool.publish_candidate(
                            root,
                            artifact_id=f"EP001:missing-{missing}",
                            owner="short-drama-storyboard",
                            outputs={"episodes/EP001/storyboard/shots.jsonl": content},
                        )

    def test_publish_does_not_treat_locators_as_artifact_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            content = (
                json.dumps(
                    {
                        "source_locator": {
                            "artifact": "episodes/EP001/screenplay.md",
                            "line_start": 1,
                            "line_end": 2,
                        },
                        "affected_binding_locators": [
                            {
                                "artifact": "bible/characters.jsonl",
                                "record_id": "CHAR-1",
                            }
                        ],
                    }
                )
                + "\n"
            )
            result = project_tool.publish_candidate(
                root,
                artifact_id="EP001:locators",
                owner="short-drama-storyboard",
                outputs={"episodes/EP001/storyboard/locators.jsonl": content},
            )
            self.assertEqual(result["status"], "committed")

    def test_publish_rejects_verbatim_shipped_shot_template(self) -> None:
        template = (
            SUITE / "skills/short-drama-storyboard/assets/shot-template.jsonl"
        ).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            with self.assertRaisesRegex(ValueError, "structured ref hash is unfilled"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:shots",
                    owner="short-drama-storyboard",
                    outputs={"episodes/EP001/storyboard/shots.jsonl": template},
                )

    def test_publish_cannot_overwrite_creator_authority_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            before = (root / "short-drama.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "creator authority file"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:script",
                    owner="short-drama-write",
                    outputs={"short-drama.json": '{"schema_version":1}\n'},
                )
            self.assertEqual((root / "short-drama.json").read_bytes(), before)

    def test_publish_cannot_write_into_delivery_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            with self.assertRaisesRegex(ValueError, "written by the packaging gate"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:script",
                    owner="short-drama-write",
                    outputs={"delivery/EP001/manifest.json": '{"forged":true}\n'},
                )

    def test_publish_guards_are_case_insensitive(self) -> None:
        # Developed and largely run on case-insensitive filesystems, where
        # Delivery/x and delivery/x are the same file on disk. A case-sensitive
        # guard is not a guard there.
        cases = [
            ("Short-Drama.json", "creator authority file"),
            ("Delivery/EP001/manifest.json", "packaging gate"),
            ("Inputs/source.md", "immutable publication sources"),
            (".Short-Drama/state.json", "operational state"),
            ("Episodes/ep1/screenplay.md", "EP001-style identifier"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for index, (target, message) in enumerate(cases):
                with self.subTest(target=target):
                    with self.assertRaisesRegex(ValueError, message):
                        project_tool.publish_candidate(
                            root,
                            artifact_id=f"case-{index}",
                            owner="short-drama-write",
                            outputs={target: "x\n"},
                        )

    def test_ownership_is_enforced_case_insensitively(self) -> None:
        # The layout guards casefold; an ownership check that did not would let
        # `Episodes/EP001/screenplay.md` through and, on a case-insensitive
        # filesystem, overwrite the artifact the check exists to protect.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for target in (
                "Episodes/EP001/screenplay.md",
                "episodes/EP001/Screenplay.md",
                "Development/creative-brief.md",
            ):
                with self.subTest(target=target):
                    with self.assertRaisesRegex(ValueError, "owns"):
                        project_tool.publish_candidate(
                            root,
                            artifact_id="case-owner",
                            owner="short-drama-storyboard",
                            outputs={target: "# x\n"},
                        )

    def test_bible_ledgers_have_a_declared_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            with self.assertRaisesRegex(
                ValueError, "short-drama-assets owns bible/characters.jsonl"
            ):
                project_tool.publish_candidate(
                    root,
                    artifact_id="bible:chars",
                    owner="short-drama-storyboard",
                    outputs={"bible/characters.jsonl": '{"id":"C1"}\n'},
                )

    def test_publish_refuses_a_non_string_structured_ref_hash(self) -> None:
        # `hash: null` was the same silent drop as `<sha256>` under a spelling
        # the first fix did not cover.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for digest in (None, 123):
                with self.subTest(hash=digest):
                    content = (
                        json.dumps(
                            {
                                "source_ref": {
                                    "owner": "short-drama-write",
                                    "artifact": "episodes/EP001/screenplay.md",
                                    "hash": digest,
                                }
                            }
                        )
                        + "\n"
                    )
                    with self.assertRaisesRegex(
                        ValueError, "structured ref hash is unfilled"
                    ):
                        project_tool.publish_candidate(
                            root,
                            artifact_id="EP001:shots",
                            owner="short-drama-storyboard",
                            outputs={
                                "episodes/EP001/storyboard/shots.jsonl": content
                            },
                        )

    def test_publish_refuses_a_nested_project_file_decoy(self) -> None:
        # find_project walks upward, so a planted development/short-drama.json
        # makes that subdirectory answer as its own project root and a creator
        # running `status` from inside it reads the decoy.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            with self.assertRaisesRegex(ValueError, "creator authority file"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="decoy",
                    owner="short-drama-develop",
                    outputs={"development/short-drama.json": '{"schema_version":1}\n'},
                )

    def test_publish_refuses_an_unregistered_root_unless_asked(self) -> None:
        # The mistyped-directory case: `epsiodes/` used to commit and build a
        # parallel tree that `status` never reports.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            with self.assertRaisesRegex(ValueError, "not a project stage directory"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:script",
                    owner="short-drama-write",
                    outputs={"epsiodes/EP001/screenplay.md": "# EP001\n"},
                )
            self.assertFalse((root / "epsiodes").exists())
            project_tool.publish_candidate(
                root,
                artifact_id="EP001:notes",
                owner="short-drama-write",
                outputs={"scratch/notes.md": "# notes\n"},
                allow_unregistered_path=True,
            )
            self.assertTrue((root / "scratch/notes.md").is_file())

    def test_publish_enforces_declared_artifact_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            with self.assertRaisesRegex(
                ValueError, "short-drama-write owns episodes/EP001/screenplay.md"
            ):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:script",
                    owner="short-drama-storyboard",
                    outputs={"episodes/EP001/screenplay.md": "# EP001\n"},
                )
            # Undeclared paths inside a stage root stay owner-unconstrained:
            # the contract names an owner for its own artifacts, not for every
            # file a creator might place there.
            project_tool.publish_candidate(
                root,
                artifact_id="EP001:scratch",
                owner="short-drama-storyboard",
                outputs={"episodes/EP001/scratch.json": '{"a":1}\n'},
            )

    def test_publish_enforces_source_analysis_file_family_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            outputs = {
                "development/source-analysis/_progress.md": "# progress\n",
                "development/source-analysis/chapters/ch-1-extract.md": "# chapter\n",
            }
            for path in outputs:
                with self.subTest(path=path):
                    with self.assertRaisesRegex(
                        ValueError, f"short-drama-novel-analyze owns {re.escape(path)}"
                    ):
                        project_tool.publish_candidate(
                            root,
                            artifact_id=f"wrong-owner:{Path(path).name}",
                            owner="short-drama-write",
                            outputs={path: outputs[path]},
                        )
                    self.assertFalse((root / path).exists())

            project_tool.publish_candidate(
                root,
                artifact_id="source-analysis:batch-1",
                owner="short-drama-novel-analyze",
                outputs=outputs,
            )
            for path, content in outputs.items():
                with self.subTest(published=path):
                    self.assertEqual((root / path).read_text(encoding="utf-8"), content)

    def test_project_paths_reject_cross_platform_aliases(self) -> None:
        invalid = (
            "episodes/EP001/storyboard /shots.jsonl",
            "episodes/EP001/storyboard./shots.jsonl",
            "episodes/EP001/CON.json",
            "episodes/EP001/notes\nextra.md",
            "episodes/EP001/a:b.json",
        )
        for path in invalid:
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "unsafe project-relative path"):
                    project_tool._relative_path(path)
                self.assertTrue(project_tool.is_protected_project_text(path))

        self.assertEqual(
            project_tool._relative_path("剧集/EP001/分镜/第 1 镜.json"),
            "剧集/EP001/分镜/第 1 镜.json",
        )

    def test_project_path_sets_reject_casefold_aliases_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            state_before = (root / ".short-drama/state.json").read_bytes()
            transactions = root / ".short-drama/transactions"

            with self.assertRaisesRegex(ValueError, "aliases"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="casefold:pair",
                    owner="short-drama-write",
                    outputs={
                        "episodes/EP001/Notes.md": "A\n",
                        "episodes/EP001/notes.md": "B\n",
                    },
                )

            self.assertFalse((root / "episodes/EP001/Notes.md").exists())
            self.assertFalse((root / "episodes/EP001/notes.md").exists())
            self.assertEqual(
                [path for path in transactions.iterdir() if path.is_dir()], []
            )
            self.assertEqual((root / ".short-drama/state.json").read_bytes(), state_before)

            project_tool.publish_candidate(
                root,
                artifact_id="casefold:tracked",
                owner="short-drama-write",
                outputs={"episodes/EP001/Notes.md": "first\n"},
            )
            tracked_state = (root / ".short-drama/state.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "aliases"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="casefold:tracked-alias",
                    owner="short-drama-write",
                    outputs={"episodes/EP001/notes.md": "second\n"},
                )
            self.assertEqual(
                (root / "episodes/EP001/Notes.md").read_text(encoding="utf-8"),
                "first\n",
            )
            self.assertEqual((root / ".short-drama/state.json").read_bytes(), tracked_state)

    def test_a_legacy_tracked_spelling_does_not_block_unrelated_publications(
        self,
    ) -> None:
        # 0.3.0 rejects Win32-illegal spellings that 0.2.0 accepted. The
        # tracked-state scan re-parsed every recorded path, so one legacy entry
        # in state.json aborted *every* later publication with a raw
        # `unsafe project-relative path` naming a file the creator was not
        # touching — and the documented migration could not clear it, because
        # renaming on disk leaves state.json untouched.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            state_path = root / ".short-drama/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["artifacts"]["legacy:note"] = {
                "owner": "short-drama-develop",
                "accepted_targets": {"development/legacy:note.md": "a" * 64},
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")

            result = project_tool.publish_candidate(
                root,
                artifact_id="EP001:screenplay",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": "# 第一集\n"},
            )

            self.assertEqual(result["status"], "committed")

    def test_project_path_sets_reject_unicode_normalization_aliases(self) -> None:
        composed = "development/caf\N{LATIN SMALL LETTER E WITH ACUTE}.md"
        decomposed = "development/cafe\N{COMBINING ACUTE ACCENT}.md"
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            state_before = (root / ".short-drama/state.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "portable aliases"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="unicode-alias:pair",
                    owner="short-drama-develop",
                    outputs={composed: "same\n", decomposed: "same\n"},
                )
            self.assertEqual((root / ".short-drama/state.json").read_bytes(), state_before)
            self.assertEqual(
                [
                    path
                    for path in (root / ".short-drama/transactions").iterdir()
                    if path.is_dir()
                ],
                [],
            )

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            existing = root / decomposed
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_text("existing\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "aliases an existing path"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="unicode-alias:disk",
                    owner="short-drama-develop",
                    outputs={composed: "replacement\n"},
                )
            self.assertEqual(existing.read_text(encoding="utf-8"), "existing\n")

    def test_plain_metadata_hash_is_not_mistaken_for_an_artifact_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            path = "development/metadata.json"
            project_tool.publish_candidate(
                root,
                artifact_id="metadata:hash",
                owner="short-drama-develop",
                outputs={path: json.dumps({"metadata": {"hash": "content-address"}})},
            )
            self.assertTrue((root / path).is_file())

    def test_complete_artifact_refs_are_detected_outside_ref_named_keys(self) -> None:
        complete_ref = {
            "owner": "short-drama-write",
            "artifact": "episodes/EP001/screenplay.md",
            "hash": "a" * 64,
        }
        documents = (complete_ref, {"references": [complete_ref]})
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for index, document in enumerate(documents):
                with self.subTest(index=index), self.assertRaisesRegex(
                    ValueError, "structured ref requires exact input"
                ):
                    project_tool.publish_candidate(
                        root,
                        artifact_id=f"root-ref:{index}",
                        owner="short-drama-develop",
                        outputs={
                            f"development/root-ref-{index}.json": json.dumps(document)
                        },
                    )

    def test_structured_ref_rejects_same_publication_case_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            target = "development/Notes.json"
            content = b'{"value":1}\n'
            reference = json.dumps(
                {
                    "source_ref": {
                        "owner": "short-drama-develop",
                        "artifact": "development/notes.json",
                        "hash": hashlib.sha256(content).hexdigest(),
                        "authority": "candidate",
                    }
                }
            )
            with self.assertRaisesRegex(ValueError, "aliases"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="casefold:structured-ref",
                    owner="short-drama-develop",
                    outputs={target: content, "development/ref.json": reference},
                )

    def test_read_sets_and_inputs_reject_casefold_aliases_before_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "development/Data.md"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("source\n", encoding="utf-8")
            source_hash = digest(source)
            transactions = root / ".short-drama/transactions"

            with self.assertRaisesRegex(ValueError, "aliases"):
                project_tool.publish_transaction(
                    root,
                    stage="casefold-read",
                    outputs={"development/data.md": "candidate\n"},
                    lifecycle_changes={"casefold:read": {}},
                    read_set={"development/Data.md": source_hash},
                )
            self.assertEqual(
                [path for path in transactions.iterdir() if path.is_dir()], []
            )
            self.assertEqual(source.read_text(encoding="utf-8"), "source\n")

            with self.assertRaisesRegex(ValueError, "aliases"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="casefold:inputs",
                    owner="short-drama-develop",
                    outputs={"development/output.md": "candidate\n"},
                    input_hashes={
                        "development/Data.md": source_hash,
                        "development/data.md": source_hash,
                    },
                )

    def test_content_effect_artifacts_keep_their_declared_stage_owners(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            cases = (
                (
                    "development/lookdev-prompts.md",
                    "short-drama-image-prompts",
                    "short-drama-storyboard",
                ),
                (
                    "episodes/EP001/storyboard/scene-visual-plans/SC001.jsonl",
                    "short-drama-storyboard",
                    "short-drama-video-prompts",
                ),
                (
                    "episodes/EP001/storyboard/coverage-auditions/SC001.jsonl",
                    "short-drama-storyboard",
                    "short-drama-write",
                ),
            )
            for path, expected_owner, wrong_owner in cases:
                with self.subTest(path=path):
                    with self.assertRaisesRegex(
                        ValueError, rf"{expected_owner} owns {path}"
                    ):
                        project_tool.publish_candidate(
                            root,
                            artifact_id=f"owner-check:{Path(path).name}",
                            owner=wrong_owner,
                            outputs={path: "{}\n"},
                        )

    def test_family_ownership_stops_at_the_two_declared_scene_jsonl_families(
        self,
    ) -> None:
        # The family map claims `<SC>.jsonl` members of two declared directories
        # and nothing else. Without this negative case, widening the lookup to a
        # default owner would annex every 3-deep episode `.jsonl` and start
        # refusing writes the contract never assigned, with the suite still green.
        unconstrained = (
            # Undeclared 3-deep families: the arm the family lookup runs in.
            "episodes/EP001/storyboard/notes/working.jsonl",
            "episodes/EP001/assets/variants/draft.jsonl",
            # Declared family directory, but not a declared member of it.
            "episodes/EP001/storyboard/coverage-auditions/notes.json",
            "episodes/EP001/storyboard/scene-visual-plans/nested/SC001.jsonl",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for path in unconstrained:
                with self.subTest(path=path):
                    self.assertIsNone(project_tool._expected_path_owner(path))
                    # No declared owner means any stage may publish it.
                    project_tool.publish_candidate(
                        root,
                        artifact_id=f"unconstrained:{path}",
                        owner="short-drama-write",
                        outputs={path: '{"a":1}\n'},
                    )

    def test_scene_scoped_directing_file_rejects_a_different_scene_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for family in ("coverage-auditions", "scene-visual-plans"):
                path = f"episodes/EP001/storyboard/{family}/SC001.jsonl"
                record = {
                    "scene_ref": {
                        "record_id": "BLK-EP001-SC002-H01",
                        "field": "/scene_id",
                    }
                }
                with self.subTest(family=family), self.assertRaisesRegex(
                    ValueError, "filename SC001 does not match scene_ref SC002"
                ):
                    project_tool.publish_candidate(
                        root,
                        artifact_id=f"storyboard:EP001:{family}:SC001",
                        owner="short-drama-storyboard",
                        outputs={path: json.dumps(record) + "\n"},
                    )

    def test_scene_scoped_directing_file_rejects_a_noncanonical_scene_stem(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            record = {"scene_ref": {"record_id": "BLK-EP001-SC002-H01"}}
            for family in ("coverage-auditions", "scene-visual-plans"):
                for stem in ("sc001", "notes", "SC001-extra"):
                    path = (
                        f"episodes/EP001/storyboard/{family}/"
                        f"{stem}.jsonl"
                    )
                    with self.subTest(family=family, stem=stem), self.assertRaisesRegex(
                        ValueError,
                        "scene-scoped directing filename must use an SC001-style identifier",
                    ):
                        project_tool.publish_candidate(
                            root,
                            artifact_id=f"storyboard:EP001:{family}:{stem}",
                            owner="short-drama-storyboard",
                            outputs={path: json.dumps(record) + "\n"},
                        )

    def test_scene_scoped_directing_file_accepts_its_own_scene_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            path = "episodes/EP001/storyboard/coverage-auditions/SC001.jsonl"
            record = {
                "scene_ref": {
                    "record_id": "BLK-EP001-SC001-H01",
                    "field": "/scene_id",
                }
            }
            project_tool.publish_candidate(
                root,
                artifact_id="storyboard:EP001:audition:SC001",
                owner="short-drama-storyboard",
                outputs={path: json.dumps(record) + "\n"},
            )
            self.assertTrue((root / path).is_file())

    def test_scene_scoped_directing_file_keeps_blank_jsonl_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            path = "episodes/EP001/storyboard/scene-visual-plans/SC001.jsonl"
            project_tool.publish_candidate(
                root,
                artifact_id="storyboard:EP001:scene-plan:SC001",
                owner="short-drama-storyboard",
                outputs={path: "\n"},
            )
            self.assertEqual((root / path).read_text(encoding="utf-8"), "\n")

    def test_scene_scoped_directing_file_ignores_record_without_scene_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            path = "episodes/EP001/storyboard/scene-visual-plans/SC001.jsonl"
            project_tool.publish_candidate(
                root,
                artifact_id="storyboard:EP001:scene-plan:SC001",
                owner="short-drama-storyboard",
                outputs={path: "{}\n"},
            )
            self.assertEqual((root / path).read_text(encoding="utf-8"), "{}\n")

    def test_accepted_coverage_audition_requires_explicit_package_omission(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            audition = "episodes/EP001/storyboard/coverage-auditions/SC001.jsonl"
            shots = "episodes/EP001/storyboard/shots.jsonl"
            self.approve_artifact(
                root,
                artifact_id="storyboard:EP001:audition:SC001",
                owner="short-drama-storyboard",
                outputs={audition: '{"audition_id":"AUD-EP001-SC001"}\n'},
            )
            self.approve_artifact(
                root,
                artifact_id="storyboard:EP001:shots",
                owner="short-drama-storyboard",
                outputs={shots: '{"shot_id":"SHOT-001"}\n'},
            )

            with self.assertRaisesRegex(
                project_tool.PackageBlockedError,
                "neither selected nor declared omitted",
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[shots],
                )

            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[shots],
                omitted_paths=[audition],
            )
            manifest_path = next(root.glob("*/EP001/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [entry["source"] for entry in manifest["omitted"]],
                [audition],
            )

    def test_episode_identifier_has_exactly_one_spelling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            # EP0001 would be a second, coverage-invisible spelling of EP001.
            with self.assertRaisesRegex(ValueError, "EP001-style identifier"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="pad",
                    owner="short-drama-write",
                    outputs={"episodes/EP0001/screenplay.md": "# EP0001\n"},
                )
            # Beyond EP999 the unpadded form is the only one available.
            project_tool.publish_candidate(
                root,
                artifact_id="long-series",
                owner="short-drama-write",
                outputs={"episodes/EP1000/screenplay.md": "# EP1000\n"},
            )

    def test_publish_refuses_a_file_directly_under_episodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            with self.assertRaisesRegex(ValueError, "episode artifacts live in"):
                project_tool.publish_candidate(
                    root,
                    artifact_id="stray",
                    owner="short-drama-write",
                    outputs={"episodes/index.md": "# index\n"},
                )

    def test_publish_rejects_malformed_episode_directory(self) -> None:
        # episodes/ep1/ used to publish fine and then be skipped by the
        # package completeness gate's prefix match, so the gate passed on an
        # episode whose artifacts it had never enumerated.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            for bad in ("episodes/ep1/screenplay.md", "episodes/EP1/screenplay.md"):
                with self.subTest(path=bad):
                    with self.assertRaisesRegex(ValueError, "EP001-style identifier"):
                        project_tool.publish_candidate(
                            root,
                            artifact_id="EP001:script",
                            owner="short-drama-write",
                            outputs={bad: "# EP001\n"},
                        )
            self.assertFalse((root / "episodes/ep1").exists())
            self.assertFalse((root / "episodes/EP1").exists())

    def test_publish_validates_same_publication_structured_ref_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            upstream_path = "episodes/EP001/spec.json"
            upstream_content = b'{"spec_id":"SPEC-1"}\n'
            upstream_hash = hashlib.sha256(upstream_content).hexdigest()
            projection_path = "episodes/EP001/render.json"
            projection = (
                json.dumps(
                    {
                        "spec_ref": {
                            "owner": "short-drama-image-prompts",
                            "artifact": upstream_path,
                            "hash": upstream_hash,
                            "authority": "candidate",
                        }
                    }
                )
                + "\n"
            )
            project_tool.publish_candidate(
                root,
                artifact_id="EP001:image-prompts",
                owner="short-drama-image-prompts",
                outputs={
                    upstream_path: upstream_content,
                    projection_path: projection,
                },
            )

            bad_projection = (
                json.dumps(
                    {
                        "spec_ref": {
                            "owner": "short-drama-image-prompts",
                            "artifact": upstream_path,
                            "hash": "0" * 64,
                            "authority": "candidate",
                        }
                    }
                )
                + "\n"
            )
            with self.assertRaisesRegex(
                ValueError, "same-publication ref hash does not match"
            ):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:image-prompts",
                    owner="short-drama-image-prompts",
                    outputs={
                        upstream_path: upstream_content,
                        projection_path: bad_projection,
                    },
                )

    def test_candidate_authority_distinguishes_accepted_preview_and_copublished_refs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            upstream_path = "episodes/EP001/screenplay.json"
            upstream = self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={upstream_path: '{"version":1}\n'},
            )
            external_candidate_ref = json.dumps(
                {
                    "source_ref": {
                        "owner": "short-drama-write",
                        "artifact": upstream_path,
                        "hash": upstream[upstream_path],
                        "authority": "candidate",
                    }
                }
            )
            with self.assertRaisesRegex(
                ValueError, "accepted input cannot declare candidate authority"
            ):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:assets",
                    owner="short-drama-assets",
                    outputs={"episodes/EP001/assets.json": external_candidate_ref},
                    input_hashes={upstream_path: upstream[upstream_path]},
                )

            preview_path = "episodes/EP002/screenplay.json"
            preview_content = '{"version":2}\n'
            project_tool.publish_candidate(
                root,
                artifact_id="EP002:script",
                owner="short-drama-write",
                outputs={preview_path: preview_content},
            )
            preview_hash = hashlib.sha256(preview_content.encode()).hexdigest()
            preview_ref = json.dumps(
                {
                    "source_ref": {
                        "owner": "short-drama-write",
                        "artifact": preview_path,
                        "hash": preview_hash,
                        "authority": "candidate",
                    }
                }
            )
            project_tool.publish_candidate(
                root,
                artifact_id="EP002:assets",
                owner="short-drama-assets",
                outputs={"episodes/EP002/assets.json": preview_ref},
                input_hashes={preview_path: preview_hash},
            )

            same_publication_path = "episodes/EP001/spec.json"
            same_publication_content = b'{"spec_id":"SPEC-1"}\n'
            digest = hashlib.sha256(same_publication_content).hexdigest()
            missing_candidate_authority = json.dumps(
                {
                    "spec_ref": {
                        "owner": "short-drama-image-prompts",
                        "artifact": same_publication_path,
                        "hash": digest,
                    }
                }
            )
            with self.assertRaisesRegex(
                ValueError, "same-publication ref must declare candidate authority"
            ):
                project_tool.publish_candidate(
                    root,
                    artifact_id="EP001:image-prompts",
                    owner="short-drama-image-prompts",
                    outputs={
                        same_publication_path: same_publication_content,
                        "episodes/EP001/render.json": missing_candidate_authority,
                    },
                )

    def test_review_findings_use_one_canonical_severity_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            findings = root / "reviews/findings.jsonl"
            findings.parent.mkdir(parents=True)
            findings.write_text(
                '{"finding_id":"FIND-1","severity":"blocker","status":"open"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "severity is invalid"):
                project_tool._open_blocking_finding_ids(
                    root, {"artifact": "reviews/findings.jsonl"}
                )

    def test_committed_candidate_recovery_restores_only_candidate_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "输入/screenplay.md"
            source.write_text("# 第一集\n", encoding="utf-8")
            target = "episodes/EP001/screenplay.md"

            def crash(point: str, _context: dict[str, object]) -> None:
                if point == "after_commit":
                    raise RuntimeError(point)

            with self.assertRaises(RuntimeError):
                project_tool.publish_transaction(
                    root,
                    stage="candidate",
                    outputs={target: source.read_bytes()},
                    lifecycle_changes={
                        "EP001:script": {
                            "build_state": "materialized",
                            "validation_state": "pass",
                            "creator_acceptance": "pending",
                            "independent_review": "provisional",
                            "delivery_gate": "blocked",
                        }
                    },
                    target_artifacts={target: "EP001:script"},
                    read_set={"输入/screenplay.md": digest(source)},
                    authority="candidate",
                    owner="short-drama-write",
                    fault_injector=crash,
                )
            transaction_id = next(
                (root / ".short-drama/transactions").iterdir()
            ).name

            recovered = project_tool.recover_transaction(root, transaction_id)

            self.assertEqual(recovered["direction"], "forward")
            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            record = state["artifacts"]["EP001:script"]
            self.assertEqual(record["owner"], "short-drama-write")
            self.assertEqual(record["candidate_targets"], {target: digest(root / target)})
            self.assertEqual(
                record["candidate_inputs"],
                {"输入/screenplay.md": digest(source)},
            )
            self.assertNotIn("accepted_targets", record)

    def test_upstream_publish_stales_full_closure_and_preserves_unrelated_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            upstream = self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": "version one\n"},
            )
            middle = self.approve_artifact(
                root,
                artifact_id="EP001:assets",
                owner="short-drama-assets",
                outputs={"episodes/EP001/assets/assets.json": '{"version":1}\n'},
                input_hashes=upstream,
            )
            downstream = self.approve_artifact(
                root,
                artifact_id="EP001:shots",
                owner="short-drama-storyboard",
                outputs={"episodes/EP001/storyboard/shots.json": '{"version":1}\n'},
                input_hashes=middle,
            )
            unrelated = self.approve_artifact(
                root,
                artifact_id="EP002:script",
                owner="short-drama-write",
                outputs={"episodes/EP002/screenplay.md": "unrelated\n"},
            )

            project_tool.publish_candidate(
                root,
                owner="short-drama-write",
                artifact_id="EP001:script",
                outputs={"episodes/EP001/screenplay.md": "version two\n"},
            )

            state_path = root / ".short-drama/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state["artifacts"]["EP001:assets"]["accepted_inputs"], upstream
            )
            self.assertEqual(
                state["artifacts"]["EP001:shots"]["accepted_inputs"], middle
            )
            for artifact_id in ("EP001:assets", "EP001:shots"):
                record = state["artifacts"][artifact_id]
                self.assertEqual(record["build_state"], "stale")
                self.assertEqual(record["validation_state"], "not_run")
                self.assertEqual(record["creator_acceptance"], "accepted")
                self.assertEqual(record["independent_review"], "not_requested")
                self.assertEqual(record["delivery_gate"], "blocked")
            self.assertEqual(
                state["artifacts"]["EP002:script"]["build_state"], "materialized"
            )

            for artifact_id in ("EP001:assets", "EP001:shots"):
                record = state["artifacts"][artifact_id]
                record.update(
                    {
                        "build_state": "materialized",
                        "validation_state": "pass",
                        "independent_review": "approve",
                        "delivery_gate": "ready",
                    }
                )
            project_tool.atomic_json(state_path, state)

            assets_record = state["artifacts"]["EP001:assets"]
            with self.assertRaisesRegex(ValueError, "accepted input"):
                project_tool.record_independent_review(
                    root,
                    artifact_id="EP001:assets",
                    verdict="approve",
                    reviewed_targets=assets_record["accepted_targets"],
                    verdict_ref=assets_record["review_evidence"]["verdict_ref"],
                )
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "accepted input"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=list(downstream),
                )
            delivered = project_tool.build_delivery_package(
                root,
                episode="EP002",
                selected_paths=list(unrelated),
            )
            self.assertEqual(delivered["status"], "delivered")

    def test_reduced_target_set_stales_dependents_and_orphans_removed_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            upstream = self.approve_artifact(
                root,
                artifact_id="EP001:upstream",
                owner="short-drama-write",
                outputs={
                    "episodes/EP001/upstream-a.json": '{"version":1}\n',
                    "episodes/EP001/upstream-b.json": '{"stable":true}\n',
                },
            )
            downstream = self.approve_artifact(
                root,
                artifact_id="EP001:downstream",
                owner="short-drama-assets",
                outputs={"episodes/EP001/downstream.json": '{"projection":1}\n'},
                input_hashes={
                    "episodes/EP001/upstream-b.json": upstream[
                        "episodes/EP001/upstream-b.json"
                    ]
                },
            )

            publication = project_tool.publish_candidate(
                root,
                artifact_id="EP001:upstream",
                owner="short-drama-write",
                outputs={"episodes/EP001/upstream-a.json": '{"version":2}\n'},
            )
            transaction = (
                root
                / ".short-drama/transactions"
                / str(publication["transaction_id"])
            )
            manifest = json.loads(
                (transaction / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertIn("EP001:downstream", manifest["lifecycle_changes"])

            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            dependent = state["artifacts"]["EP001:downstream"]
            self.assertEqual(dependent["build_state"], "stale")
            self.assertEqual(dependent["delivery_gate"], "blocked")
            self.assertTrue((root / "episodes/EP001/upstream-b.json").is_file())

            current_target = "episodes/EP001/upstream-a.json"
            current_hash = digest(root / current_target)
            decision_relative = "creator-decisions/EP001-upstream-v2.json"
            decision = root / decision_relative
            decision.parent.mkdir(parents=True, exist_ok=True)
            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-EP001-UPSTREAM-V2",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": "EP001:upstream",
                        "status": "accepted",
                        "target_hashes": {current_target: current_hash},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            project_tool.record_creator_acceptance(
                root,
                artifact_id="EP001:upstream",
                decision="accepted",
                target_hashes={current_target: current_hash},
                evidence_ref={
                    "owner": "creator",
                    "artifact": decision_relative,
                    "hash": digest(decision),
                    "record_id": "CD-EP001-UPSTREAM-V2",
                },
            )

            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(
                "episodes/EP001/upstream-b.json",
                state["artifacts"]["EP001:upstream"]["accepted_targets"],
            )
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "no unique accepted snapshot"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=["episodes/EP001/upstream-b.json"],
                )
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "delivery-ready"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=list(downstream),
                )

    def test_stale_closure_is_applied_when_committed_candidate_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            upstream = self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": "version one\n"},
            )
            middle = self.approve_artifact(
                root,
                artifact_id="EP001:assets",
                owner="short-drama-assets",
                outputs={"episodes/EP001/assets/assets.json": '{"version":1}\n'},
                input_hashes=upstream,
            )
            self.approve_artifact(
                root,
                artifact_id="EP001:shots",
                owner="short-drama-storyboard",
                outputs={"episodes/EP001/storyboard/shots.json": '{"version":1}\n'},
                input_hashes=middle,
            )

            def crash(point: str, _context: dict[str, object]) -> None:
                if point == "after_commit":
                    raise RuntimeError(point)

            before = set((root / ".short-drama/transactions").iterdir())
            with self.assertRaises(RuntimeError):
                project_tool.publish_candidate(
                    root,
                    owner="short-drama-write",
                    artifact_id="EP001:script",
                    outputs={"episodes/EP001/screenplay.md": "version two\n"},
                    fault_injector=crash,
                )
            transaction = next(
                iter(set((root / ".short-drama/transactions").iterdir()) - before)
            )
            manifest = json.loads(
                (transaction / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                set(manifest["lifecycle_changes"]),
                {"EP001:script", "EP001:assets", "EP001:shots"},
            )

            project_tool.recover_transaction(root, transaction.name)

            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["artifacts"]["EP001:assets"]["build_state"], "stale")
            self.assertEqual(state["artifacts"]["EP001:shots"]["build_state"], "stale")

    def test_external_upstream_edit_blocks_transitive_downstream_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            upstream = self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": "version one\n"},
            )
            middle = self.approve_artifact(
                root,
                artifact_id="EP001:assets",
                owner="short-drama-assets",
                outputs={"episodes/EP001/assets/assets.json": '{"version":1}\n'},
                input_hashes=upstream,
            )
            downstream = self.approve_artifact(
                root,
                artifact_id="EP001:shots",
                owner="short-drama-storyboard",
                outputs={"episodes/EP001/storyboard/shots.json": '{"version":1}\n'},
                input_hashes=middle,
            )
            (root / "episodes/EP001/screenplay.md").write_text(
                "external edit\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "accepted input"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=list(downstream),
                )

    def test_dependency_guard_rejects_ambiguous_provider_and_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            upstream = self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": "version one\n"},
            )
            self.approve_artifact(
                root,
                artifact_id="EP001:assets",
                owner="short-drama-assets",
                outputs={"episodes/EP001/assets/assets.json": '{"version":1}\n'},
                input_hashes=upstream,
            )
            state_path = root / ".short-drama/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["artifacts"]["EP001:duplicate-script"] = json.loads(
                json.dumps(state["artifacts"]["EP001:script"])
            )
            project_tool.atomic_json(state_path, state)
            assets = state["artifacts"]["EP001:assets"]

            with self.assertRaisesRegex(ValueError, "provider is ambiguous"):
                project_tool.record_independent_review(
                    root,
                    artifact_id="EP001:assets",
                    verdict="approve",
                    reviewed_targets=assets["accepted_targets"],
                    verdict_ref=assets["review_evidence"]["verdict_ref"],
                )

            del state["artifacts"]["EP001:duplicate-script"]
            state["artifacts"]["EP001:script"]["accepted_inputs"] = assets[
                "accepted_targets"
            ]
            project_tool.atomic_json(state_path, state)
            with self.assertRaisesRegex(ValueError, "dependency cycle"):
                project_tool.record_independent_review(
                    root,
                    artifact_id="EP001:assets",
                    verdict="approve",
                    reviewed_targets=assets["accepted_targets"],
                    verdict_ref=assets["review_evidence"]["verdict_ref"],
                )

    def test_acceptance_and_review_bind_exact_evidence_and_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "输入/screenplay.md"
            source.write_text("# 第一集\n", encoding="utf-8")
            target = "episodes/EP001/screenplay.md"
            self.run_cli(
                "publish",
                str(root),
                "--owner",
                "short-drama-write",
                "--artifact-id",
                "EP001:script",
                "--output",
                f"{target}=输入/screenplay.md",
            )
            target_hash = digest(root / target)

            decision = root / "creator-decisions.jsonl"
            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-EP001",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": "EP001:script",
                        "status": "accepted",
                        "target_hashes": {target: target_hash},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            failed, _ = self.run_cli(
                "accept",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--decision",
                "accepted",
                "--target",
                f"{target}={'0' * 64}",
                "--evidence-artifact",
                "creator-decisions.jsonl",
                "--evidence-hash",
                digest(decision),
                "--evidence-record-id",
                "CD-EP001",
                expected_code=2,
            )
            self.assertIn("candidate targets", failed.stderr)

            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-EP001",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": "EP001:script",
                        "status": "rejected",
                        "target_hashes": {target: target_hash},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            wrong_decision, _ = self.run_cli(
                "accept",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--decision",
                "accepted",
                "--target",
                f"{target}={target_hash}",
                "--evidence-artifact",
                "creator-decisions.jsonl",
                "--evidence-hash",
                digest(decision),
                "--evidence-record-id",
                "CD-EP001",
                expected_code=2,
            )
            self.assertIn("does not match creator decision", wrong_decision.stderr)

            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-EP001",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": "EP999:other",
                        "status": "accepted",
                        "target_hashes": {target: target_hash},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            wrong_artifact, _ = self.run_cli(
                "accept",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--decision",
                "accepted",
                "--target",
                f"{target}={target_hash}",
                "--evidence-artifact",
                "creator-decisions.jsonl",
                "--evidence-hash",
                digest(decision),
                "--evidence-record-id",
                "CD-EP001",
                expected_code=2,
            )
            self.assertIn("artifact_id", wrong_artifact.stderr)

            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-EP001",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": "EP001:script",
                        "status": "accepted",
                        "target_hashes": {target: "e" * 64},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            wrong_evidence_targets, _ = self.run_cli(
                "accept",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--decision",
                "accepted",
                "--target",
                f"{target}={target_hash}",
                "--evidence-artifact",
                "creator-decisions.jsonl",
                "--evidence-hash",
                digest(decision),
                "--evidence-record-id",
                "CD-EP001",
                expected_code=2,
            )
            self.assertIn("target_hashes", wrong_evidence_targets.stderr)

            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-EP001",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": "EP001:script",
                        "status": "accepted",
                        "target_hashes": {target: target_hash},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.run_cli(
                "accept",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--decision",
                "accepted",
                "--target",
                f"{target}={target_hash}",
                "--evidence-artifact",
                "creator-decisions.jsonl",
                "--evidence-hash",
                digest(decision),
                "--evidence-record-id",
                "CD-EP001",
            )

            findings = root / "reviews/EP001-findings.jsonl"
            findings.parent.mkdir(parents=True)
            findings.write_text(
                '{"finding_id":"FIND-OPEN","severity":"error","status":"open"}\n',
                encoding="utf-8",
            )
            verdict = root / "reviews/EP001-verdict.json"
            verdict.parent.mkdir(parents=True, exist_ok=True)
            document = json.loads(
                (
                    SUITE
                    / "skills/short-drama-review/assets/verdict-template.json"
                ).read_text(encoding="utf-8")
            )
            document.update(
                {
                    "review_id": "REVIEW-EP001",
                    "reviewed_artifacts": [
                        {
                            "owner": "short-drama-write",
                            "artifact": target,
                            "hash": target_hash,
                        }
                    ],
                    "findings_ref": {
                        "owner": "short-drama-review",
                        "artifact": "reviews/EP001-findings.jsonl",
                        "hash": digest(findings),
                    },
                    "requested_review_mode": "independent_agent",
                    "effective_review_mode": "fresh_agent",
                    "reviewer": fresh_reviewer("short-drama-write"),
                    "structural_validation": "pass",
                    "verdict": "APPROVE",
                    "blocking_findings": ["FIND-OPEN"],
                    "open_blocker_count": 1,
                }
            )
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            blocked, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "approve",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("open blocking finding", blocked.stderr)

            document["open_blocker_count"] = 0
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            bad_count, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "approve",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("open_blocker_count", bad_count.stderr)

            document["blocking_findings"] = []
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            hidden_blocker, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "approve",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("findings snapshot", hidden_blocker.stderr)

            findings.write_text(
                '{"finding_id":"FIND-OPEN","severity":"error","status":"closed"}\n',
                encoding="utf-8",
            )
            document["findings_ref"]["hash"] = digest(findings)
            document["verdict"] = "REVISE"
            document["blocking_findings"] = ["FIND-OPEN"]
            document["open_blocker_count"] = 1
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            listed_closed, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "revise",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("findings snapshot", listed_closed.stderr)

            document["blocking_findings"] = ["FIND-MISSING"]
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            listed_missing, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "revise",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("findings snapshot", listed_missing.stderr)

            document["verdict"] = "APPROVE"
            document["blocking_findings"] = []
            document["open_blocker_count"] = 0
            document["reviewer"]["excluded_owner_skills"] = ["short-drama-assets"]
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            bad_exclusion, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "approve",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("excluded owner", bad_exclusion.stderr)

            document["reviewer"]["excluded_owner_skills"] = ["short-drama-write"]
            document["findings_ref"]["owner"] = "short-drama-write"
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            bad_findings_owner, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "approve",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("must be short-drama-review", bad_findings_owner.stderr)

            document["findings_ref"]["owner"] = "short-drama-review"
            document["structural_validation"] = "fail"
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            bad_validation, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "approve",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("requires structural validation pass", bad_validation.stderr)

            document["structural_validation"] = "pass"
            verdict.write_text(
                json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            mismatched, _ = self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "approve",
                "--target",
                f"{target}={'f' * 64}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
                expected_code=2,
            )
            self.assertIn("accepted targets", mismatched.stderr)
            self.run_cli(
                "review",
                str(root),
                "--artifact-id",
                "EP001:script",
                "--verdict",
                "approve",
                "--target",
                f"{target}={target_hash}",
                "--verdict-owner",
                "short-drama-review",
                "--verdict-artifact",
                "reviews/EP001-verdict.json",
                "--verdict-hash",
                digest(verdict),
            )

            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            record = state["artifacts"]["EP001:script"]
            self.assertEqual(record["accepted_targets"], {target: target_hash})
            self.assertEqual(
                record["creator_decision"]["evidence_ref"]["hash"], digest(decision)
            )
            self.assertEqual(
                record["review_evidence"]["verdict_ref"]["hash"], digest(verdict)
            )
            independence = record["review_evidence"]["reviewer_independence"]
            self.assertTrue(independence["attestation_structure_valid"])
            self.assertEqual(
                independence["verification_scope"], "declared_provenance_structure"
            )
            self.assertEqual(record["validation_state"], "pass")
            self.assertEqual(
                record["review_evidence"]["reviewer_independence"][
                    "excluded_owner_skills"
                ],
                ["short-drama-write"],
            )

            package = project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[target],
            )
            self.assertEqual(package["status"], "delivered")

            decision_bytes = decision.read_bytes()
            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-EP001",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": "EP001:script",
                        "status": "rejected",
                        "target_hashes": {target: target_hash},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "creator decision evidence"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[target],
                )
            decision.write_bytes(decision_bytes)

            findings_bytes = findings.read_bytes()
            findings.write_text(
                '{"finding_id":"FIND-NEW","status":"open"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "review verdict evidence"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[target],
                )
            findings.write_bytes(findings_bytes)

            verdict.write_text(
                json.dumps({**document, "notes": ["tampered"]}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "review verdict evidence"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[target],
                )

    def test_review_rejects_owner_self_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            source = root / "输入/screenplay.md"
            source.write_text("# 第一集\n", encoding="utf-8")
            target = "episodes/EP001/screenplay.md"
            project_tool.publish_candidate(
                root,
                owner="short-drama-write",
                artifact_id="EP001:script",
                outputs={target: source.read_bytes()},
                input_hashes={"输入/screenplay.md": digest(source)},
            )
            target_hash = digest(root / target)
            decision = root / "creator-decisions.jsonl"
            decision.write_text(
                json.dumps(
                    {
                        "decision_id": "CD-1",
                        "decision_kind": "artifact_acceptance",
                        "artifact_id": "EP001:script",
                        "status": "accepted",
                        "target_hashes": {target: target_hash},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            project_tool.record_creator_acceptance(
                root,
                artifact_id="EP001:script",
                decision="accepted",
                target_hashes={target: target_hash},
                evidence_ref={
                    "owner": "creator",
                    "artifact": "creator-decisions.jsonl",
                    "hash": digest(decision),
                    "record_id": "CD-1",
                },
            )
            verdict = root / "reviews/verdict.json"
            verdict.parent.mkdir(parents=True)
            verdict.write_text(
                json.dumps(
                    {
                        "reviewer": "short-drama-write",
                        "reviewed_artifacts": [
                            {
                                "owner": "short-drama-write",
                                "artifact": target,
                                "hash": target_hash,
                            }
                        ],
                        "verdict": "APPROVE",
                        "blocking_findings": [],
                        "required_reviewer_independence": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "independent"):
                project_tool.record_independent_review(
                    root,
                    artifact_id="EP001:script",
                    verdict="approve",
                    reviewed_targets={target: target_hash},
                    verdict_ref={
                        "owner": "short-drama-write",
                        "artifact": "reviews/verdict.json",
                        "hash": digest(verdict),
                    },
                )

    def test_review_and_package_reject_legacy_reviewer_string(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            targets = self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": "# 第一集\n"},
            )
            verdict_relative = "reviews/EP001-script.json"
            verdict = root / verdict_relative
            document = json.loads(verdict.read_text(encoding="utf-8"))
            document["reviewer"] = "short-drama-review"
            verdict.write_text(json.dumps(document) + "\n", encoding="utf-8")
            verdict_hash = digest(verdict)

            with self.assertRaisesRegex(ValueError, "must be an object"):
                project_tool.record_independent_review(
                    root,
                    artifact_id="EP001:script",
                    verdict="approve",
                    reviewed_targets=targets,
                    verdict_ref={
                        "owner": "short-drama-review",
                        "artifact": verdict_relative,
                        "hash": verdict_hash,
                    },
                )

            state_path = root / ".short-drama/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            evidence = state["artifacts"]["EP001:script"]["review_evidence"]
            evidence["verdict_ref"]["hash"] = verdict_hash
            evidence["reviewer_independence"] = {
                "artifact_owner": "short-drama-write",
                "reviewer_owner": "short-drama-review",
                "kind": "legacy_owner_string",
                "independent": True,
                "excluded_owner_skills": ["short-drama-write"],
                "attestation_structure_valid": True,
                "verification_scope": "declared_provenance_structure",
            }
            project_tool.atomic_json(state_path, state)
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "review verdict evidence"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=list(targets),
                )

    def test_package_revalidates_evidence_files_not_lifecycle_strings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            target = "episodes/EP001/screenplay.md"
            project_tool.publish_transaction(
                root,
                stage="legacy",
                outputs={target: "# 第一集\n"},
                lifecycle_changes={
                    "EP001:script": {
                        "build_state": "materialized",
                        "validation_state": "pass",
                        "creator_acceptance": "accepted",
                        "independent_review": "approve",
                        "delivery_gate": "ready",
                    }
                },
                target_artifacts={target: "EP001:script"},
            )

            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "creator decision evidence"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[target],
                )


if __name__ == "__main__":
    unittest.main()
