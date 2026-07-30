import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
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
            invalid = root / "inputs/invalid.jsonl"
            invalid.write_text('{"ok":true}\nnot-json\n', encoding="utf-8")

            result, _ = self.run_cli(
                "publish",
                str(root),
                "--owner",
                "short-drama-write",
                "--artifact-id",
                "EP001:invalid",
                "--output",
                "episodes/EP001/invalid.jsonl=inputs/invalid.jsonl",
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
            source = root / "inputs/screenplay.md"
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
                    read_set={"inputs/screenplay.md": digest(source)},
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
                {"inputs/screenplay.md": digest(source)},
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
            source = root / "inputs/screenplay.md"
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
                f"{target}=inputs/screenplay.md",
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
            source = root / "inputs/screenplay.md"
            source.write_text("# 第一集\n", encoding="utf-8")
            target = "episodes/EP001/screenplay.md"
            project_tool.publish_candidate(
                root,
                owner="short-drama-write",
                artifact_id="EP001:script",
                outputs={target: source.read_bytes()},
                input_hashes={"inputs/screenplay.md": digest(source)},
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
