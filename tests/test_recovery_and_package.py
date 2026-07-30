import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path


SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama/scripts/project_tool.py"
SPEC = importlib.util.spec_from_file_location("short_drama_recovery_tool", SCRIPT)
assert SPEC and SPEC.loader
project_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(project_tool)


class InjectedCrash(RuntimeError):
    pass


def ready_axes() -> dict[str, str]:
    return {
        "build_state": "materialized",
        "validation_state": "pass",
        "creator_acceptance": "accepted",
        "independent_review": "approve",
        "delivery_gate": "ready",
    }


class RecoveryTests(unittest.TestCase):
    def make_project(self, directory: str) -> Path:
        root = Path(directory) / "短剧 项目"
        project_tool.initialize_project(
            root,
            title="恢复矩阵",
            language="zh-CN",
            aspect_ratio="9:16",
            suite_root=SUITE / "skills/short-drama",
        )
        first = root / "episodes/EP001/screenplay.md"
        second = root / "episodes/EP001/beats.jsonl"
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("旧剧本\n", encoding="utf-8")
        second.write_text('{"beat":"旧"}\n', encoding="utf-8")
        return root

    def publish_with_crash(self, root: Path, point: str) -> str:
        def fail(selected: str, _context: dict[str, object]) -> None:
            if selected == point:
                raise InjectedCrash(selected)

        with self.assertRaises(InjectedCrash):
            project_tool.publish_transaction(
                root,
                stage="write",
                outputs={
                    "episodes/EP001/screenplay.md": "新剧本\n",
                    "episodes/EP001/beats.jsonl": '{"beat":"新"}\n',
                },
                lifecycle_changes={"EP001:script": ready_axes()},
                target_artifacts={
                    "episodes/EP001/screenplay.md": "EP001:script",
                    "episodes/EP001/beats.jsonl": "EP001:script",
                },
                fault_injector=fail,
            )
        transactions = sorted((root / ".short-drama/transactions").iterdir())
        self.assertEqual(len(transactions), 1)
        return transactions[0].name

    def test_crash_matrix_converges_and_second_recovery_is_a_noop(self) -> None:
        rollback_points = [
            "after_manifest",
            "after_prepared",
            "before_replace:0",
            "after_replace:0",
            "after_applied:0",
            "before_replace:1",
            "after_replace:1",
            "after_applied:1",
            "before_commit",
        ]
        forward_points = [
            "after_commit_marker",
            "after_commit",
            "after_pointer_state",
            "after_pointers",
            "after_lifecycle_state",
            "after_state",
        ]
        for point in rollback_points + forward_points:
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = self.make_project(directory)
                txid = self.publish_with_crash(root, point)

                first = project_tool.recover_transaction(root, txid)
                state_before = (root / ".short-drama/state.json").read_bytes()
                wal_before = (
                    root / ".short-drama/transactions" / txid / "wal.jsonl"
                ).read_bytes()
                second = project_tool.recover_transaction(root, txid)

                expect_new = point in forward_points
                self.assertEqual(
                    (root / "episodes/EP001/screenplay.md").read_text(encoding="utf-8"),
                    "新剧本\n" if expect_new else "旧剧本\n",
                )
                self.assertEqual(
                    (root / "episodes/EP001/beats.jsonl").read_text(encoding="utf-8"),
                    '{"beat":"新"}\n' if expect_new else '{"beat":"旧"}\n',
                )
                self.assertEqual(first["direction"], "forward" if expect_new else "rollback")
                self.assertTrue(second["already_recovered"])
                self.assertEqual((root / ".short-drama/state.json").read_bytes(), state_before)
                self.assertEqual(
                    (root / ".short-drama/transactions" / txid / "wal.jsonl").read_bytes(),
                    wal_before,
                )

    def test_recovery_itself_resumes_at_each_mutation_boundary(self) -> None:
        scenarios = {
            "rollback": (
                "after_applied:1",
                [
                    "recovery:before_replace:0",
                    "recovery:after_replace:0",
                    "recovery:before_replace:1",
                    "recovery:after_replace:1",
                ],
            ),
            "forward": (
                "after_commit",
                [
                    "recovery:before_replace:0",
                    "recovery:after_replace:0",
                    "recovery:before_replace:1",
                    "recovery:after_replace:1",
                    "recovery:after_pointers",
                    "recovery:after_lifecycle",
                ],
            ),
        }
        for direction, (publish_point, recovery_points) in scenarios.items():
            for recovery_point in recovery_points:
                with (
                    self.subTest(direction=direction, point=recovery_point),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = self.make_project(directory)
                    txid = self.publish_with_crash(root, publish_point)
                    if direction == "forward":
                        # Model a crash after the durable marker followed by a
                        # partial filesystem restore to the expected priors.
                        (root / "episodes/EP001/screenplay.md").write_text(
                            "旧剧本\n", encoding="utf-8"
                        )
                        (root / "episodes/EP001/beats.jsonl").write_text(
                            '{"beat":"旧"}\n', encoding="utf-8"
                        )

                    def fail(point: str, _context: dict[str, object]) -> None:
                        if point == recovery_point:
                            raise InjectedCrash(point)

                    with self.assertRaises(InjectedCrash):
                        project_tool.recover_transaction(
                            root,
                            txid,
                            fault_injector=fail,
                        )
                    result = project_tool.recover_transaction(root, txid)
                    self.assertEqual(result["direction"], direction)
                    final = project_tool.recover_transaction(root, txid)
                    self.assertTrue(final["already_recovered"])

    def test_recovery_ignores_layout_policy_a_legacy_manifest_predates(self) -> None:
        # Layout rules apply where a path is minted, never to a path already
        # recorded in a write-ahead log. Applying today's policy to yesterday's
        # manifest would raise during rollback instead of restoring the
        # creator's prior bytes, and every later recover would re-report the
        # same block, leaving the project permanently wedged.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            legacy = root / "episodes/ep1/screenplay.md"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text("旧剧本\n", encoding="utf-8")

            def fail(selected: str, _context: dict[str, object]) -> None:
                if selected == "after_replace:0":
                    raise InjectedCrash(selected)

            # Model a manifest written by a build that predates the layout
            # rule: the WAL legitimately holds a path today's policy refuses.
            with (
                unittest.mock.patch.object(
                    project_tool, "_validate_publication_layout", lambda *a, **k: None
                ),
                self.assertRaises(InjectedCrash),
            ):
                project_tool.publish_transaction(
                    root,
                    stage="write",
                    outputs={"episodes/ep1/screenplay.md": "新剧本\n"},
                    lifecycle_changes={"legacy:script": ready_axes()},
                    target_artifacts={"episodes/ep1/screenplay.md": "legacy:script"},
                    fault_injector=fail,
                )
            self.assertEqual(legacy.read_text(encoding="utf-8"), "新剧本\n")

            txid = sorted((root / ".short-drama/transactions").iterdir())[-1].name
            result = project_tool.recover_transaction(root, txid)

            self.assertEqual(result["direction"], "rollback")
            self.assertEqual(legacy.read_text(encoding="utf-8"), "旧剧本\n")
            self.assertTrue(
                project_tool.recover_transaction(root, txid)["already_recovered"]
            )

    def test_external_edit_is_preserved_exactly_and_blocks_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            txid = self.publish_with_crash(root, "after_replace:0")
            target = root / "episodes/EP001/screenplay.md"
            external = b"\xffexternal creator bytes\x00"
            target.write_bytes(external)

            result = project_tool.recover_transaction(root, txid)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(target.read_bytes(), external)
            conflicts = list((root / ".short-drama/conflicts" / txid).glob("*"))
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0].read_bytes(), external)
            state = json.loads((root / ".short-drama/state.json").read_text(encoding="utf-8"))
            self.assertIn(txid, state["blocked_transactions"])
            self.assertEqual(
                state["artifacts"]["EP001:script"]["delivery_gate"], "blocked"
            )

    def test_committed_recovery_never_overwrites_post_commit_external_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            txid = self.publish_with_crash(root, "after_commit")
            target = root / "episodes/EP001/beats.jsonl"
            external = b'{"beat":"creator edit"}\n'
            target.write_bytes(external)

            result = project_tool.recover_transaction(root, txid)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(target.read_bytes(), external)

    def test_read_set_change_aborts_before_any_target_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            dependency = root / "development/creative-brief.md"
            dependency.parent.mkdir(parents=True, exist_ok=True)
            dependency.write_text("v1\n", encoding="utf-8")

            def mutate(point: str, _context: dict[str, object]) -> None:
                if point == "after_prepared":
                    dependency.write_text("v2\n", encoding="utf-8")

            with self.assertRaises(project_tool.StaleReadSetError):
                project_tool.publish_transaction(
                    root,
                    stage="write",
                    outputs={"episodes/EP001/screenplay.md": "新剧本\n"},
                    lifecycle_changes={"EP001:script": ready_axes()},
                    read_set=["development/creative-brief.md"],
                    fault_injector=mutate,
                )
            self.assertEqual(
                (root / "episodes/EP001/screenplay.md").read_text(encoding="utf-8"),
                "旧剧本\n",
            )

    def test_live_candidate_hash_is_safe_not_misclassified_as_external(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)

            def converge(point: str, _context: dict[str, object]) -> None:
                if point == "after_prepared":
                    (root / "episodes/EP001/screenplay.md").write_text(
                        "新剧本\n", encoding="utf-8"
                    )

            result = project_tool.publish_transaction(
                root,
                stage="write",
                outputs={"episodes/EP001/screenplay.md": "新剧本\n"},
                lifecycle_changes={"EP001:script": ready_axes()},
                fault_injector=converge,
            )

            self.assertEqual(result["status"], "committed")
            self.assertEqual(
                (root / "episodes/EP001/screenplay.md").read_text(encoding="utf-8"),
                "新剧本\n",
            )

    def test_corrupt_wal_blocks_without_mutating_creator_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            txid = self.publish_with_crash(root, "after_replace:0")
            tx_dir = root / ".short-drama/transactions" / txid
            before = {
                relative: (root / relative).read_bytes()
                for relative in (
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/beats.jsonl",
                )
            }
            (tx_dir / "wal.jsonl").write_bytes(b"not-json\n")

            result = project_tool.recover_transaction(root, txid)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(
                {
                    relative: (root / relative).read_bytes()
                    for relative in before
                },
                before,
            )
            self.assertEqual(
                project_tool.project_status(root)["recovery"]["transaction_counts"],
                {"corrupt": 1},
            )

    def test_manifestless_transaction_is_quarantined_and_marked_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            txid = "a" * 32
            orphan = root / ".short-drama/transactions" / txid
            orphan.mkdir()
            (orphan / "staged-fragment").write_bytes(b"partial")

            result = project_tool.recover_transaction(root, txid)

            self.assertEqual(result["status"], "blocked")
            self.assertFalse(orphan.exists())
            quarantined = (
                root
                / ".short-drama/conflicts/orphaned-transactions"
                / txid
                / "staged-fragment"
            )
            self.assertEqual(quarantined.read_bytes(), b"partial")
            status = project_tool.project_status(root)["recovery"]
            self.assertEqual(status["transaction_counts"], {})
            self.assertEqual(status["next_action"], "resolve_conflict")

    def test_conflict_blocks_target_artifact_even_without_lifecycle_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)

            def interfere(point: str, _context: dict[str, object]) -> None:
                if point == "after_prepared":
                    (root / "episodes/EP001/screenplay.md").write_text(
                        "外部改动\n", encoding="utf-8"
                    )

            with self.assertRaises(project_tool.TransactionConflictError):
                project_tool.publish_transaction(
                    root,
                    stage="write",
                    outputs={"episodes/EP001/screenplay.md": "新剧本\n"},
                    lifecycle_changes={},
                    target_artifacts={
                        "episodes/EP001/screenplay.md": "EP001:mapped-only"
                    },
                    fault_injector=interfere,
                )

            state = json.loads(
                (root / ".short-drama/state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                state["artifacts"]["EP001:mapped-only"]["delivery_gate"],
                "blocked",
            )

    def test_missing_rollback_snapshot_blocks_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(directory)
            txid = self.publish_with_crash(root, "after_replace:0")
            tx_dir = root / ".short-drama/transactions" / txid
            manifest = json.loads((tx_dir / "manifest.json").read_text(encoding="utf-8"))
            snapshot = root / manifest["targets"][0]["prior_snapshot"]
            snapshot.unlink()

            result = project_tool.recover_transaction(root, txid)

            self.assertEqual(result["status"], "blocked")
            applied = root / manifest["targets"][0]["path"]
            self.assertEqual(
                project_tool.sha256_file(applied),
                manifest["targets"][0]["candidate_hash"],
            )


class PackageTests(unittest.TestCase):
    def approve_artifact(
        self,
        root: Path,
        *,
        artifact_id: str,
        owner: str,
        outputs: dict[str, str],
    ) -> None:
        project_tool.publish_candidate(
            root,
            owner=owner,
            artifact_id=artifact_id,
            outputs=outputs,
        )
        targets = {
            relative: project_tool.sha256_file(root / relative)
            for relative in outputs
        }
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
                    "decision": "accepted",
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
                "hash": project_tool.sha256_file(decision),
                "record_id": f"CD-{slug}",
            },
        )
        verdict_relative = f"reviews/{slug}.json"
        findings_relative = f"reviews/{slug}-findings.jsonl"
        findings = root / findings_relative
        findings.parent.mkdir(parents=True, exist_ok=True)
        findings.write_text("", encoding="utf-8")
        verdict = root / verdict_relative
        verdict.parent.mkdir(parents=True, exist_ok=True)
        verdict.write_text(
            json.dumps(
                {
                    "review_id": f"REVIEW-{slug}",
                    "requested_review_mode": "independent_agent",
                    "effective_review_mode": "fresh_agent",
                    "reviewer": {
                        "owner": "short-drama-review",
                        "kind": "independent_agent",
                        "independent": True,
                        "excluded_owner_skills": [owner],
                        "provenance": {
                            "context_id": "test-fresh-review-context",
                            "fresh_context": True,
                            "authored_reviewed_artifacts": False,
                        },
                    },
                    "reviewed_artifacts": [
                        {
                            "owner": owner,
                            "artifact": relative,
                            "hash": digest,
                        }
                        for relative, digest in targets.items()
                    ],
                    "findings_ref": {
                        "owner": "short-drama-review",
                        "artifact": findings_relative,
                        "hash": project_tool.sha256_file(findings),
                    },
                    "structural_validation": "pass",
                    "verdict": "APPROVE",
                    "blocking_findings": [],
                    "open_blocker_count": 0,
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
                "hash": project_tool.sha256_file(verdict),
            },
        )

    def make_approved_project(self, directory: str) -> Path:
        root = Path(directory) / "package project"
        project_tool.initialize_project(
            root,
            title="交付测试",
            language="zh-CN",
            aspect_ratio="9:16",
            suite_root=SUITE / "skills/short-drama",
        )
        self.approve_artifact(
            root,
            artifact_id="EP001:script",
            owner="short-drama-write",
            outputs={
                "episodes/EP001/screenplay.md": "# 第一集\n\n办公室里，门被推开。\n",
            },
        )
        self.approve_artifact(
            root,
            artifact_id="EP001:image-prompts",
            owner="short-drama-image-prompts",
            outputs={
                "episodes/EP001/assets/image-prompt-specs.jsonl": (
                    '{"id":"IMG-001","prompt":"冷白办公室"}\n'
                ),
            },
        )
        return root

    def test_delivery_tree_is_writable_only_through_the_packaging_gate(self) -> None:
        # `delivery/` is excluded as a package source but used to be a legal
        # publish target, so a delivered manifest could be replaced after the
        # fact. build_delivery_package opts back in through an internal
        # argument rather than through `stage`, which the creator supplies.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )
            manifest = root / "delivery/EP001/manifest.json"
            self.assertTrue(manifest.is_file())
            before = manifest.read_bytes()
            for stage in ("short-drama-write", "delivery"):
                with self.subTest(stage=stage):
                    with self.assertRaisesRegex(ValueError, "written by the packaging gate"):
                        project_tool.publish_transaction(
                            root,
                            stage=stage,
                            outputs={"delivery/EP001/manifest.json": '{"forged":true}\n'},
                            lifecycle_changes={"EP001:script": {"build_state": "materialized"}},
                        )
            self.assertEqual(manifest.read_bytes(), before)

    def test_verify_detects_tampering_the_checksum_file_alone_cannot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )
            self.assertEqual(
                project_tool.verify_delivery_package(root, episode="EP001")["status"],
                "intact",
            )

            delivered = root / "delivery/EP001/artifacts/episodes/EP001/screenplay.md"
            delivered.write_text("# 被改过的交付稿\n", encoding="utf-8")
            tampered = project_tool.verify_delivery_package(root, episode="EP001")
            self.assertEqual(tampered["status"], "tampered")
            self.assertEqual(
                tampered["mismatched"], ["artifacts/episodes/EP001/screenplay.md"]
            )

    def test_verify_reports_an_addition_the_checksum_list_is_blind_to(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )
            planted = root / "delivery/EP001/artifacts/episodes/EP001/extra.md"
            planted.write_text("# 未登记\n", encoding="utf-8")

            result = project_tool.verify_delivery_package(root, episode="EP001")

            self.assertEqual(result["status"], "tampered")
            self.assertEqual(
                result["unlisted"], ["artifacts/episodes/EP001/extra.md"]
            )
            self.assertEqual(result["mismatched"], [])

    def test_verify_rewriting_the_checksum_list_does_not_launder_a_change(
        self,
    ) -> None:
        # Anyone able to edit a delivered artifact can also recompute the list
        # to match it, so the list has to be authenticated against the hash
        # recorded when `package` published the tree.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )
            delivery = root / "delivery/EP001"
            target = delivery / "artifacts/episodes/EP001/screenplay.md"
            target.write_text("# 被改过的交付稿\n", encoding="utf-8")

            checksums = delivery / "checksums.sha256"
            rewritten = []
            for line in checksums.read_text(encoding="utf-8").splitlines():
                _, _, relative = line.partition("  ")
                member = delivery / relative
                rewritten.append(f"{project_tool.sha256_file(member)}  {relative}")
            checksums.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

            result = project_tool.verify_delivery_package(root, episode="EP001")

            self.assertEqual(result["mismatched"], [])
            self.assertFalse(result["checksum_list_authentic"])
            self.assertEqual(result["status"], "tampered")

    def test_verify_cli_exits_nonzero_on_a_tampered_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )
            command = [sys.executable, str(SCRIPT), "verify", str(root), "--episode", "EP001"]

            intact = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(intact.returncode, 0, msg=intact.stderr)
            self.assertEqual(json.loads(intact.stdout)["status"], "intact")

            (root / "delivery/EP001/artifacts/episodes/EP001/screenplay.md").write_text(
                "# 被改过\n", encoding="utf-8"
            )
            tampered = subprocess.run(command, check=False, capture_output=True, text=True)

            # A verdict in the payload with exit 0 cannot gate a CI step.
            self.assertEqual(tampered.returncode, 1, msg=tampered.stdout)
            self.assertEqual(json.loads(tampered.stdout)["status"], "tampered")

    def test_verify_sees_content_hidden_behind_a_symlinked_directory(self) -> None:
        # `rglob` does not descend into a symlinked directory and `is_file()`
        # resolves the link away, so one `ln -s` smuggled a whole subtree past
        # the delivery gate, the privacy scan, creator acceptance and
        # independent review into a tree this command certified as intact.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )
            outside = Path(directory) / "outside"
            outside.mkdir()
            (outside / "private-notes.md").write_text("非公开备注\n", encoding="utf-8")
            (root / "delivery/EP001/extras").symlink_to(outside, target_is_directory=True)

            result = project_tool.verify_delivery_package(root, episode="EP001")

            self.assertEqual(result["status"], "tampered")
            self.assertEqual(result["unlisted"], ["extras"])

    def test_verify_refuses_to_hash_through_a_symlinked_listed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )
            listed = "artifacts/episodes/EP001/screenplay.md"
            target = root / "delivery/EP001" / listed
            elsewhere = Path(directory) / "elsewhere.md"
            elsewhere.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            target.unlink()
            target.symlink_to(elsewhere)

            result = project_tool.verify_delivery_package(root, episode="EP001")

            # Byte-identical through the link, and still refused: the bytes
            # live outside the delivered tree.
            self.assertEqual(result["missing"], [listed])
            self.assertEqual(result["status"], "tampered")

    def test_verify_refuses_an_episode_that_was_never_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "no delivered package"
            ):
                project_tool.verify_delivery_package(root, episode="EP002")

    def test_package_rejects_malformed_episode_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError, "episode selection must use"
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[
                        "episodes/EP001/screenplay.md",
                        "episodes/ep1/storyboard/shots.jsonl",
                    ],
                )

    def test_package_contains_only_selected_approved_text_json_and_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            result = project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )
            delivery = root / "delivery/EP001"
            self.assertEqual(result["file_count"], 2)
            self.assertTrue((delivery / "manifest.json").is_file())
            self.assertTrue((delivery / "checksums.sha256").is_file())
            self.assertFalse((delivery / ".short-drama").exists())
            manifest = json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["files"]), 2)
            for line in (delivery / "checksums.sha256").read_text(encoding="utf-8").splitlines():
                digest, relative = line.split("  ", 1)
                self.assertEqual(
                    hashlib.sha256((delivery / relative).read_bytes()).hexdigest(),
                    digest,
                )

    def test_package_rejects_private_binary_unapproved_and_url_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            private = root / "inputs/private-references.json"
            private.parent.mkdir(exist_ok=True)
            private.write_text("{}\n", encoding="utf-8")
            binary = root / "episodes/EP001/frame.png"
            binary.write_bytes(b"PNG")
            url_text = root / "episodes/EP001/screenplay.md"
            url_text.write_text("屏幕显示：https://private.invalid/item\n", encoding="utf-8")

            for selected in (
                ["inputs/private-references.json"],
                ["episodes/EP001/frame.png"],
                ["episodes/EP001/screenplay.md"],
            ):
                with self.subTest(selected=selected), self.assertRaises(
                    project_tool.PackageBlockedError
                ):
                    project_tool.build_delivery_package(
                        root,
                        episode="EP001",
                        selected_paths=selected,
                    )

    def test_package_distinguishes_story_text_from_structured_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            self.approve_artifact(
                root,
                artifact_id="EP001:fictional-password-scene",
                owner="short-drama-write",
                outputs={
                    "episodes/EP001/fictional-password-scene.md": (
                        "[画面文字] 旧门禁提示：password: fictional123\n"
                    )
                },
            )
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=["episodes/EP001/fictional-password-scene.md"],
                omitted_paths=["episodes/EP001/screenplay.md", "episodes/EP001/assets/image-prompt-specs.jsonl"],
            )

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            self.approve_artifact(
                root,
                artifact_id="EP001:unsafe-config",
                owner="short-drama-image-prompts",
                outputs={
                    "episodes/EP001/assets/unsafe-config.json": (
                        '{"api_key":"abcdefgh","prompt":"办公室"}\n'
                    )
                },
            )
            with self.assertRaisesRegex(
                project_tool.PackageBlockedError,
                "credential field is excluded",
            ):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=["episodes/EP001/assets/unsafe-config.json"],
                )

    def test_explicit_on_screen_url_exception_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            text = "https://fiction.example/notice"
            self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": f"屏幕显示：{text}\n"},
            )

            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=["episodes/EP001/screenplay.md"],
                omitted_paths=["episodes/EP001/assets/image-prompt-specs.jsonl"],
                text_exceptions=[
                    {
                        "exact_text": text,
                        "path": "episodes/EP001/screenplay.md",
                        "field": "screenplay.visible_text",
                        "purpose": "on_screen_text",
                        "provenance": "creator_supplied",
                        "text_policy": "visible_on_screen",
                        "allow_delivery": True,
                    }
                ],
            )

            manifest = json.loads(
                (root / "delivery/EP001/manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["text_exceptions"][0]["exact_text"], text)

    def test_on_screen_machine_path_is_blocked_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={
                    "episodes/EP001/screenplay.md": "[画面文字] 屏幕显示：/var/log/auth.log\n"
                },
            )

            with self.assertRaises(project_tool.PackageBlockedError):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=["episodes/EP001/screenplay.md"],
                )

    def test_explicit_on_screen_machine_path_exception_is_reported(self) -> None:
        """A story that shows a path on screen needs an appeal channel."""

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            text = "/var/log/auth.log"
            self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": f"[画面文字] 屏幕显示：{text}\n"},
            )

            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=["episodes/EP001/screenplay.md"],
                omitted_paths=["episodes/EP001/assets/image-prompt-specs.jsonl"],
                text_exceptions=[
                    {
                        "exact_text": text,
                        "path": "episodes/EP001/screenplay.md",
                        "field": "screenplay.visible_text",
                        "purpose": "on_screen_text",
                        "provenance": "story_world_authored",
                        "text_policy": "fictional_interface_text",
                        "allow_delivery": True,
                    }
                ],
            )

            manifest = json.loads(
                (root / "delivery/EP001/manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["text_exceptions"][0]["exact_text"], text)

    def test_bare_path_marker_cannot_be_declared_as_an_exception(self) -> None:
        """A marker alone would act as a wildcard over every path sharing it."""

        markers = ["/Users" + "/", "/var" + "/", "/tmp" + "/", "C" + ":/", "C" + ":\\"]
        for marker in markers:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                root = self.make_approved_project(directory)
                self.approve_artifact(
                    root,
                    artifact_id="EP001:script",
                    owner="short-drama-write",
                    outputs={
                        "episodes/EP001/screenplay.md": (
                            f"[画面文字] A：{marker}alpha/one.md\n\n"
                            f"[画面文字] B：{marker}beta/secret.key\n"
                        )
                    },
                )
                with self.assertRaises(project_tool.PackageBlockedError):
                    project_tool.build_delivery_package(
                        root,
                        episode="EP001",
                        selected_paths=["episodes/EP001/screenplay.md"],
                        text_exceptions=[
                            {
                                "exact_text": marker,
                                "path": "episodes/EP001/screenplay.md",
                                "field": "screenplay.visible_text",
                                "purpose": "on_screen_text",
                                "provenance": "story_world_authored",
                                "text_policy": "fictional_interface_text",
                                "allow_delivery": True,
                            }
                        ],
                    )

    def test_declared_path_must_be_a_whole_token_not_a_prefix(self) -> None:
        """Guards MACHINE_PATH_TOKEN_RE on its own: prefix-only would leak."""

        prefix = "/var" + "/log"
        longer = prefix + "extra/secret.key"
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={"episodes/EP001/screenplay.md": f"[画面文字] A：{longer}\n"},
            )
            with self.assertRaises(project_tool.PackageBlockedError):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=["episodes/EP001/screenplay.md"],
                    text_exceptions=[
                        {
                            "exact_text": prefix,
                            "path": "episodes/EP001/screenplay.md",
                            "field": "screenplay.visible_text",
                            "purpose": "on_screen_text",
                            "provenance": "story_world_authored",
                            "text_policy": "fictional_interface_text",
                            "allow_delivery": True,
                        }
                    ],
                )

    def test_declaration_gate_rejects_markers_oversize_and_line_breaks(self) -> None:
        """Each declaration defense asserted on its own, so none masks another."""

        shown = "/var" + "/log/auth.log"

        def declare(exact: str) -> None:
            project_tool._normalize_text_exceptions(
                [
                    {
                        "exact_text": exact,
                        "path": "episodes/EP001/screenplay.md",
                        "field": "screenplay.visible_text",
                        "purpose": "on_screen_text",
                        "provenance": "story_world_authored",
                        "text_policy": "fictional_interface_text",
                        "allow_delivery": True,
                    }
                ]
            )

        # A complete path is the only accepted shape.
        declare(shown)

        # A complete URL is exempt from the length bound; a long free-form
        # on-screen string is not.
        declare("https://example.invalid/" + "a" * 240)

        rejected = {
            "bare unix marker": "/var" + "/",
            "bare windows marker": "C" + ":\\",
            # A marker plus a delimiter is still only a marker.
            "marker plus comma": "/var" + "/,",
            "marker plus quote": "/var" + '/"',
            "marker plus paren": "/var" + "/)",
            "oversize": shown + " " + "x" * 300,
            "newline": shown + "\n\u7b2c\u4e8c\u884c",
            "carriage return": shown + "\r\u7b2c\u4e8c\u884c",
            "line separator": shown + "\u2028\u7b2c\u4e8c\u884c",
            "next line": shown + "\x85\u7b2c\u4e8c\u884c",
            "vertical tab": shown + "\x0b\u7b2c\u4e8c\u884c",
        }
        for label, exact in rejected.items():
            with self.subTest(label=label):
                with self.assertRaises(project_tool.PackageBlockedError):
                    declare(exact)

    def test_on_screen_path_is_detected_next_to_chinese_text(self) -> None:
        """CJK is \\w, so a lookbehind on word characters would miss this."""

        for shown in ("显示/Users" + "/somebody/secret.key", "显示C" + ":\\somebody\\secret.key"):
            with self.subTest(shown=shown), tempfile.TemporaryDirectory() as directory:
                root = self.make_approved_project(directory)
                self.approve_artifact(
                    root,
                    artifact_id="EP001:script",
                    owner="short-drama-write",
                    outputs={"episodes/EP001/screenplay.md": f"[画面文字] {shown}\n"},
                )
                with self.assertRaises(project_tool.PackageBlockedError):
                    project_tool.build_delivery_package(
                        root,
                        episode="EP001",
                        selected_paths=["episodes/EP001/screenplay.md"],
                    )

    def test_structured_field_path_can_still_be_released_by_exception(self) -> None:
        """A path inside a JSON string must not be blocked by its own quotes."""

        shown = "/var" + "/log/auth.log"
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={
                    "episodes/EP001/beats.jsonl": json.dumps(
                        {"exact_text": shown}, ensure_ascii=False
                    )
                    + "\n"
                },
            )
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=["episodes/EP001/beats.jsonl"],
                # screenplay.md left this artifact's target set above, so it no
                # longer has an accepted owner and cannot be omitted either.
                omitted_paths=["episodes/EP001/assets/image-prompt-specs.jsonl"],
                text_exceptions=[
                    {
                        "exact_text": shown,
                        "path": "episodes/EP001/beats.jsonl",
                        "field": "beats.exact_text",
                        "purpose": "on_screen_text",
                        "provenance": "story_world_authored",
                        "text_policy": "fictional_interface_text",
                        "allow_delivery": True,
                    }
                ],
            )
            manifest = json.loads(
                (root / "delivery/EP001/manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["text_exceptions"][0]["exact_text"], shown)

    def test_text_exception_only_applies_to_the_file_it_declares(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            shown = "/var" + "/log/auth.log"
            self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={
                    "episodes/EP001/screenplay.md": f"[画面文字] A：{shown}\n",
                    "episodes/EP001/beats.jsonl": json.dumps(
                        {"note": f"B：{shown}"}, ensure_ascii=False
                    )
                    + "\n",
                },
            )

            with self.assertRaises(project_tool.PackageBlockedError):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[
                        "episodes/EP001/screenplay.md",
                        "episodes/EP001/beats.jsonl",
                    ],
                    text_exceptions=[
                        {
                            "exact_text": shown,
                            "path": "episodes/EP001/screenplay.md",
                            "field": "screenplay.visible_text",
                            "purpose": "on_screen_text",
                            "provenance": "story_world_authored",
                            "text_policy": "fictional_interface_text",
                            "allow_delivery": True,
                        }
                    ],
                )

    def test_machine_path_exception_does_not_release_other_paths(self) -> None:
        """An exception releases only the exact string it declared."""

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            declared = "/var/log/auth.log"
            self.approve_artifact(
                root,
                artifact_id="EP001:script",
                owner="short-drama-write",
                outputs={
                    "episodes/EP001/screenplay.md": (
                        f"[画面文字] 屏幕显示：{declared}\n\n"
                        "[画面文字] 另一处：/Users/someone/secret\n"
                    )
                },
            )

            with self.assertRaises(project_tool.PackageBlockedError):
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=["episodes/EP001/screenplay.md"],
                    text_exceptions=[
                        {
                            "exact_text": declared,
                            "path": "episodes/EP001/screenplay.md",
                            "field": "screenplay.visible_text",
                            "purpose": "on_screen_text",
                            "provenance": "story_world_authored",
                            "text_policy": "fictional_interface_text",
                            "allow_delivery": True,
                        }
                    ],
                )

    def test_forgetting_an_approved_episode_file_blocks_the_package(self) -> None:
        """A hand-written include list looks equally complete whether or not it
        forgot something, so the tool enumerates the episode instead."""

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            with self.assertRaises(project_tool.PackageBlockedError) as raised:
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=["episodes/EP001/screenplay.md"],
                )
            message = str(raised.exception)
            self.assertIn("episodes/EP001/assets/image-prompt-specs.jsonl", message)
            self.assertIn("--omit", message)

    def test_an_acknowledged_omission_is_recorded_in_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=["episodes/EP001/screenplay.md"],
                omitted_paths=["episodes/EP001/assets/image-prompt-specs.jsonl"],
            )
            manifest = json.loads(
                (root / "delivery/EP001/manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["omitted"],
                [
                    {
                        "source": "episodes/EP001/assets/image-prompt-specs.jsonl",
                        "artifact_id": "EP001:image-prompts",
                        "reason": "delivery_ready_but_omitted",
                    }
                ],
            )

    def test_an_unfinished_episode_file_must_also_be_acknowledged(self) -> None:
        """An artifact still in rework is the easiest thing to ship around
        silently; the package must say the episode is not fully covered."""

        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            self.approve_artifact(
                root,
                artifact_id="EP001:storyboard",
                owner="short-drama-storyboard",
                outputs={
                    "episodes/EP001/storyboard/shots.jsonl": '{"shot_id":"SHOT-001"}\n'
                },
            )
            project_tool.publish_candidate(
                root,
                owner="short-drama-storyboard",
                artifact_id="EP001:storyboard",
                outputs={
                    "episodes/EP001/storyboard/shots.jsonl": '{"shot_id":"SHOT-002"}\n'
                },
            )
            with self.assertRaises(project_tool.PackageBlockedError) as raised:
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[
                        "episodes/EP001/screenplay.md",
                        "episodes/EP001/assets/image-prompt-specs.jsonl",
                    ],
                )
            self.assertIn("not yet delivery-ready", str(raised.exception))
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
                omitted_paths=["episodes/EP001/storyboard/shots.jsonl"],
            )
            manifest = json.loads(
                (root / "delivery/EP001/manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [entry["reason"] for entry in manifest["omitted"]],
                ["not_delivery_ready"],
            )

    def test_a_path_cannot_be_both_selected_and_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            with self.assertRaises(project_tool.PackageBlockedError) as raised:
                project_tool.build_delivery_package(
                    root,
                    episode="EP001",
                    selected_paths=[
                        "episodes/EP001/screenplay.md",
                        "episodes/EP001/assets/image-prompt-specs.jsonl",
                    ],
                    omitted_paths=["episodes/EP001/screenplay.md"],
                )
            self.assertIn("both selected and omitted", str(raised.exception))

    def test_another_episode_never_enters_this_episode_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_approved_project(directory)
            self.approve_artifact(
                root,
                artifact_id="EP002:script",
                owner="short-drama-write",
                outputs={"episodes/EP002/screenplay.md": "# 第二集\n\n窗外下雨。\n"},
            )
            project_tool.build_delivery_package(
                root,
                episode="EP001",
                selected_paths=[
                    "episodes/EP001/screenplay.md",
                    "episodes/EP001/assets/image-prompt-specs.jsonl",
                ],
            )


if __name__ == "__main__":
    unittest.main()
