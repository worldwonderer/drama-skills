"""The three invariants that were classified structural but ran on trust.

`SHT-16`, `SHT-17`, and `VID-15` are arithmetic and set comparisons. Leaving
them to a reviewer costs judgment on work a script does exactly, and the
failures they catch are precisely the ones that look fine to a reader: a
coverage list missing one shot ID reads identically to a complete one.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

SUITE = Path(__file__).resolve().parents[1]
SKILLS = SUITE / "skills"


def _module(name: str, relative: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SKILLS / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


storyboard_check = _module(
    "sd_storyboard_check", "short-drama-storyboard/scripts/storyboard_check.py"
)
container_check = _module(
    "sd_container_check", "short-drama-video-prompts/scripts/container_check.py"
)
voice_sheet_check = _module(
    "sd_voice_sheet_check", "short-drama-write/scripts/voice_sheet_check.py"
)
screenplay_index = _module(
    "sd_screenplay_index", "short-drama-write/scripts/screenplay_index.py"
)


def shot_ref(shot_id: str) -> dict[str, Any]:
    return {
        "owner": "short-drama-storyboard",
        "artifact": "episodes/EP001/storyboard/shots.jsonl",
        "hash": "0" * 64,
        "record_id": shot_id,
    }


def coverage(**duration: Any) -> dict[str, Any]:
    return {
        "episode_id": "EP001",
        "dispositions": [
            {
                "block_id": "BLK-1",
                "status": "covered",
                "shot_refs": [shot_ref("SHOT-1"), shot_ref("SHOT-2"), shot_ref("SHOT-3")],
            }
        ],
        "episode_duration": {
            "shot_seconds_total": 6.0,
            "counted_shot_ids": ["SHOT-1", "SHOT-2"],
            "unresolved_durations": ["SHOT-3"],
            "target_seconds": None,
            "delta_seconds": None,
            "disposition": "no_target_declared",
            **duration,
        },
    }


SHOTS = [
    {"shot_id": "SHOT-1", "duration_seconds": 3.5},
    {"shot_id": "SHOT-2", "duration_seconds": 2.5},
    {"shot_id": "SHOT-3"},
]


def codes(findings: list[dict[str, Any]]) -> set[str]:
    return {finding["code"] for finding in findings}


class EpisodeDurationCheckTests(unittest.TestCase):
    def run_check(self, cov: dict[str, Any], target: float | None = None) -> set[str]:
        return codes(storyboard_check.check_episode_duration(cov, SHOTS, target))

    def test_a_correct_ledger_passes_with_a_shot_still_suspended(self) -> None:
        self.assertEqual(self.run_check(coverage()), set())

    def test_a_shot_that_silently_leaves_the_total_is_caught(self) -> None:
        """This is the failure the rule exists for: the ledger still adds up."""

        self.assertIn(
            "SHT16_SHOT_LEFT_THE_TOTAL",
            self.run_check(coverage(unresolved_durations=[])),
        )

    def test_a_wrong_total_is_caught(self) -> None:
        self.assertIn(
            "SHT16_TOTAL_IS_NOT_THE_SUM",
            self.run_check(coverage(shot_seconds_total=7.0)),
        )

    def test_a_shot_cannot_be_counted_and_suspended_at_once(self) -> None:
        self.assertIn(
            "SHT16_SHOT_COUNTED_AND_UNRESOLVED",
            self.run_check(
                coverage(
                    counted_shot_ids=["SHOT-1", "SHOT-2", "SHOT-3"],
                    unresolved_durations=["SHOT-3"],
                )
            ),
        )

    def test_a_counted_shot_must_actually_carry_a_duration(self) -> None:
        self.assertIn(
            "SHT16_COUNTED_SHOT_HAS_NO_DURATION",
            self.run_check(
                coverage(
                    counted_shot_ids=["SHOT-1", "SHOT-2", "SHOT-3"],
                    unresolved_durations=[],
                )
            ),
        )

    def test_a_suspended_shot_that_already_has_a_duration_is_caught(self) -> None:
        self.assertIn(
            "SHT16_SUSPENDED_SHOT_HAS_A_DURATION",
            self.run_check(
                coverage(counted_shot_ids=["SHOT-2"], unresolved_durations=["SHOT-1", "SHOT-3"])
            ),
        )

    def test_a_declared_target_needs_a_correct_delta_and_a_disposition(self) -> None:
        self.assertEqual(
            self.run_check(coverage(), target=5.0),
            {"SHT16_DELTA_MISSING", "SHT16_DISPOSITION_MISSING"},
        )
        self.assertEqual(
            self.run_check(
                coverage(
                    target_seconds=5.0,
                    delta_seconds=1.0,
                    disposition="within_creator_tolerance",
                ),
                target=5.0,
            ),
            set(),
        )
        self.assertIn(
            "SHT16_DELTA_IS_WRONG",
            self.run_check(
                coverage(
                    target_seconds=5.0,
                    delta_seconds=9.0,
                    disposition="within_creator_tolerance",
                ),
                target=5.0,
            ),
        )

    def test_the_delta_alone_never_becomes_a_finding(self) -> None:
        """A target is a plan, not a quality gate; only bad arithmetic fails."""

        self.assertEqual(
            self.run_check(
                coverage(
                    target_seconds=1.0,
                    delta_seconds=5.0,
                    disposition="creator_accepted_overrun",
                ),
                target=1.0,
            ),
            set(),
        )


class KeyframeBoundaryCheckTests(unittest.TestCase):
    def keyframe(self, **overrides: Any) -> dict[str, Any]:
        record = {
            "keyframe_id": "KEY-1",
            "boundary_role": "start",
            "boundary_ref": {
                "owner": "short-drama-storyboard",
                "artifact": "episodes/EP001/storyboard/shots.jsonl",
                "hash": "0" * 64,
                "record_id": "SHOT-1",
                "field": "/start_boundary",
            },
        }
        record.update(overrides)
        return record

    def run_check(self, *keyframes: dict[str, Any]) -> set[str]:
        return codes(storyboard_check.check_keyframe_boundaries(list(keyframes), SHOTS))

    def test_a_start_and_an_end_frame_on_one_shot_are_both_allowed(self) -> None:
        end = self.keyframe(
            keyframe_id="KEY-2",
            boundary_role="end",
            boundary_ref={
                "owner": "short-drama-storyboard",
                "artifact": "episodes/EP001/storyboard/shots.jsonl",
                "hash": "0" * 64,
                "record_id": "SHOT-1",
                "field": "/end_boundary",
            },
        )
        self.assertEqual(self.run_check(self.keyframe(), end), set())

    def test_an_undeclared_role_is_caught(self) -> None:
        record = self.keyframe()
        del record["boundary_role"]
        self.assertIn("SHT17_BOUNDARY_ROLE_MISSING", self.run_check(record))

    def test_a_role_that_disagrees_with_its_field_is_caught(self) -> None:
        """An end frame pointing at /start_boundary is the drift this prevents."""

        self.assertIn(
            "SHT17_BOUNDARY_REF_DISAGREES_WITH_ROLE",
            self.run_check(self.keyframe(boundary_role="end")),
        )

    def test_two_frames_on_the_same_boundary_are_caught(self) -> None:
        self.assertIn(
            "SHT17_DUPLICATE_BOUNDARY_KEYFRAME",
            self.run_check(self.keyframe(), self.keyframe(keyframe_id="KEY-2")),
        )

    def test_a_frame_bound_to_no_real_shot_is_caught(self) -> None:
        record = self.keyframe()
        record["boundary_ref"]["record_id"] = "SHOT-404"
        self.assertIn("SHT17_BOUNDARY_REF_UNRESOLVABLE", self.run_check(record))

    def test_a_shot_with_no_keyframe_is_not_a_finding(self) -> None:
        """One frame per shot is a default, never a quota."""

        self.assertEqual(self.run_check(self.keyframe()), set())


class ContainerReconciliationTests(unittest.TestCase):
    MEASURED = [
        {"shot_id": "SHOT-1", "duration_seconds": 3.5},
        {"shot_id": "SHOT-2", "duration_seconds": 2.5},
        {"shot_id": "SHOT-3", "duration_seconds": 1.0},
    ]

    def container(self, container_id: str, shot_ids: list[str], seconds: float) -> dict[str, Any]:
        return {
            "container_id": container_id,
            "members": [
                {"order": index, "shot_ref": shot_ref(shot_id)}
                for index, shot_id in enumerate(shot_ids, start=1)
            ],
            "container_duration": seconds,
        }

    def test_a_loose_shot_reconciles_and_is_reported_not_blamed(self) -> None:
        result = container_check.reconcile(
            [self.container("CONT-1", ["SHOT-1", "SHOT-2"], 6.0)], self.MEASURED
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["loose_shots"], ["SHOT-3"])
        self.assertEqual(result["packed_seconds"], 6.0)
        self.assertEqual(result["loose_seconds"], 1.0)

    def test_a_shot_packed_into_two_containers_is_caught(self) -> None:
        """Invisible from inside either container; the episode is billed twice."""

        result = container_check.reconcile(
            [
                self.container("CONT-1", ["SHOT-1", "SHOT-2"], 6.0),
                self.container("CONT-2", ["SHOT-2", "SHOT-3"], 3.5),
            ],
            self.MEASURED,
        )
        self.assertIn("VID15_SHOT_PACKED_TWICE", codes(result["findings"]))

    def test_a_container_duration_that_is_not_the_sum_is_caught(self) -> None:
        result = container_check.reconcile(
            [self.container("CONT-1", ["SHOT-1", "SHOT-2"], 9.0)], self.MEASURED
        )
        self.assertIn(
            "VID15_CONTAINER_DURATION_IS_NOT_THE_SUM", codes(result["findings"])
        )

    def test_a_member_from_another_episode_is_caught(self) -> None:
        result = container_check.reconcile(
            [self.container("CONT-1", ["SHOT-1", "SHOT-99"], 3.5)], self.MEASURED
        )
        self.assertIn("VID15_MEMBER_IS_NOT_AN_EPISODE_SHOT", codes(result["findings"]))

    def test_an_open_duration_upstream_is_held_out_rather_than_blamed(self) -> None:
        """Work in progress must not be indistinguishable from work done wrong."""

        result = container_check.reconcile(
            [self.container("CONT-1", ["SHOT-1", "SHOT-2"], 6.0)], SHOTS
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["unmeasured_shots"], ["SHOT-3"])
        self.assertEqual(result["loose_seconds"], 0.0)

    def test_a_packed_shot_must_already_have_a_duration(self) -> None:
        result = container_check.reconcile(
            [self.container("CONT-1", ["SHOT-1", "SHOT-3"], 3.5)], SHOTS
        )
        self.assertIn("VID15_MEMBER_SHOT_HAS_NO_DURATION", codes(result["findings"]))


class VoiceSheetCheckTests(unittest.TestCase):
    SCREENPLAY = (
        "## EP001-SC001 内 · 值班室 · 夜\n\n"
        "他推开门。\n\n"
        "葛晴（压着嗓子）：你把本子放下。\n"
    )

    def build(self, screenplay: str | None = None) -> tuple[list[dict[str, Any]], bytes, str]:
        source = (screenplay or self.SCREENPLAY).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screenplay.md"
            path.write_bytes(source)
            index_path = Path(directory) / "index.jsonl"
            with contextlib.redirect_stdout(io.StringIO()):
                screenplay_index.main(
                    [
                        str(path),
                        "--output",
                        str(index_path),
                        "--source-ref",
                        "episodes/EP001/screenplay.md",
                        "--speaker",
                        "葛晴",
                    ]
                )
            index = [
                json.loads(line)
                for line in index_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        block_id = next(
            record["block_id"] for record in index if record.get("kind") == "dialogue"
        )
        return index, source, block_id

    def line(self, block_id: str, **overrides: Any) -> dict[str, Any]:
        record = {
            "line_id": "VLINE-1",
            "speaker": "CHAR-GE",
            "speaker_display": "葛晴",
            "line_text": "你把本子放下。",
            "channel": "sync",
            "source_ref": {
                "owner": "short-drama-write",
                "artifact": "episodes/EP001/screenplay-index.jsonl",
                "hash": "0" * 64,
                "record_id": block_id,
            },
        }
        record.update(overrides)
        return record

    def test_a_faithful_sheet_passes(self) -> None:
        index, source, block_id = self.build()
        result = voice_sheet_check.check([self.line(block_id)], index, source)
        self.assertEqual(result["status"], "pass")

    def test_a_line_edited_in_the_sheet_is_caught(self) -> None:
        """The sheet is a projection; changing a word here forks the wording."""

        index, source, block_id = self.build()
        result = voice_sheet_check.check(
            [self.line(block_id, line_text="你把本子放下吧。")], index, source
        )
        self.assertIn("VOICE_LINE_TEXT_DIVERGED", codes(result["findings"]))

    def test_a_screenplay_revised_without_rebuilding_the_index_is_caught(self) -> None:
        index, _source, block_id = self.build()
        revised = self.SCREENPLAY.replace("你把本子放下。", "你把本子放下吧。")
        result = voice_sheet_check.check(
            [self.line(block_id)], index, revised.encode("utf-8")
        )
        self.assertIn(
            "VOICE_INDEX_IS_STALE_AGAINST_SCREENPLAY", codes(result["findings"])
        )

    def test_a_speaker_that_disagrees_with_the_index_is_caught(self) -> None:
        index, source, block_id = self.build()
        result = voice_sheet_check.check(
            [self.line(block_id, speaker_display="别人")], index, source
        )
        self.assertIn("VOICE_SPEAKER_DIVERGED", codes(result["findings"]))

    def test_a_line_pointing_at_an_action_block_is_caught(self) -> None:
        index, source, _block_id = self.build()
        action = next(
            record["block_id"] for record in index if record.get("kind") == "action"
        )
        result = voice_sheet_check.check([self.line(action)], index, source)
        self.assertIn("VOICE_SOURCE_IS_NOT_DIALOGUE", codes(result["findings"]))

    def test_a_partial_sheet_reports_coverage_without_failing(self) -> None:
        """Splitting a sheet per actor or per scene is ordinary practice."""

        index, source, _block_id = self.build()
        result = voice_sheet_check.check([], index, source)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(len(result["uncovered_dialogue_blocks"]), 1)


class ValidatorRegistrationTests(unittest.TestCase):
    def test_each_new_diagnostic_code_is_catalogued(self) -> None:
        storyboard = (
            SKILLS / "short-drama-storyboard/references/review-and-fixtures.md"
        ).read_text(encoding="utf-8")
        video = (
            SKILLS / "short-drama-video-prompts/references/review-and-fixtures.md"
        ).read_text(encoding="utf-8")
        for code in ("SHT16_SHOT_LEFT_THE_TOTAL", "SHT17_DUPLICATE_BOUNDARY_KEYFRAME"):
            with self.subTest(code=code):
                self.assertIn(code, storyboard)
        for code in ("VID15_SHOT_PACKED_TWICE", "VID15_EPISODE_TOTAL_DOES_NOT_RECONCILE"):
            with self.subTest(code=code):
                self.assertIn(code, video)

    def test_each_validator_is_invoked_from_its_owning_skill(self) -> None:
        pairs = {
            "short-drama-storyboard": "scripts/storyboard_check.py",
            "short-drama-video-prompts": "scripts/container_check.py",
            "short-drama-write": "scripts/voice_sheet_check.py",
        }
        pairs_extra = {
            "short-drama-video-prompts": "scripts/motion_timing_check.py",
        }
        for skill, script in pairs_extra.items():
            with self.subTest(skill=skill, script=script):
                body = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(script, body)
        for skill, script in pairs.items():
            with self.subTest(skill=skill):
                body = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(script, body)

    def test_no_validator_reaches_into_a_sibling_skill(self) -> None:
        """A validator may read project data from any stage, but must never
        depend on a sibling skill's own files."""

        siblings = {path.name for path in SKILLS.iterdir() if path.is_dir()}
        for skill, script in (
            ("short-drama-storyboard", "scripts/storyboard_check.py"),
            ("short-drama-video-prompts", "scripts/container_check.py"),
            ("short-drama-video-prompts", "scripts/motion_timing_check.py"),
            ("short-drama-write", "scripts/voice_sheet_check.py"),
        ):
            body = (SKILLS / skill / script).read_text(encoding="utf-8")
            for other in sorted(siblings - {skill}):
                # A directory reference, not a bare name: "short-drama" also
                # appears in this suite's own diagnostics text.
                with self.subTest(skill=skill, other=other):
                    self.assertNotIn(
                        f"{other}/", body, f"{script} reaches into {other}"
                    )
            self.assertNotIn("../", body, f"{script} escapes its own tree")


if __name__ == "__main__":
    unittest.main()
