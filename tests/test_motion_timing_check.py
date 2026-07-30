import importlib.util
import unittest
from pathlib import Path
from typing import Any


SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama-video-prompts/scripts/motion_timing_check.py"
SPEC = importlib.util.spec_from_file_location("short_drama_motion_timing", SCRIPT)
assert SPEC and SPEC.loader
motion_timing_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(motion_timing_check)


SHOTS = [
    {"shot_id": "SHOT-1", "status": "accepted", "duration_seconds": 4.0},
    {"shot_id": "SHOT-2", "status": "accepted", "duration_seconds": 6.0},
]


def spec(
    motion_id: str, shot: str, segments: list[str], **plan: Any
) -> dict[str, Any]:
    return {
        "motion_id": motion_id,
        "shot_ref": {"record_id": shot},
        "timing_plan": {"mode": "explicit", **plan},
        "ordered_subject_motion": [
            {"timing": {"mode": "explicit", "value": value}} for value in segments
        ],
    }


def codes(result: dict[str, Any]) -> list[str]:
    return [finding["code"] for finding in result["findings"]]


class MotionTimingCheckTests(unittest.TestCase):
    def check(self, specs: list[dict[str, Any]]) -> dict[str, Any]:
        return motion_timing_check.check(specs, SHOTS)

    def test_segments_summing_to_the_accepted_duration_pass(self) -> None:
        result = self.check([spec("M-1", "SHOT-1", ["0.0-2.0", "2.0-4.0"])])

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["explicit_checked"], 1)

    def test_overflow_is_reported_with_the_truncated_amount(self) -> None:
        result = self.check([spec("M-1", "SHOT-1", ["0.0-3.0", "3.0-5.5"])])

        self.assertEqual(codes(result), ["VID_EXPLICIT_TIMING_OVERFLOW"])
        self.assertAlmostEqual(result["findings"][0]["overflow_seconds"], 1.5)

    def test_a_gap_and_an_overrun_that_cancel_out_are_both_reported(self) -> None:
        # 0.0-2.0 plus 3.0-5.0 occupies exactly 4.0s of a 4.0s shot, so any
        # check that compares one total against the duration calls it clean —
        # while a second is truncated off the end and the 2-3s window gets
        # filled with unsourced motion.
        result = self.check([spec("M-1", "SHOT-1", ["0.0-2.0", "3.0-5.0"])])

        self.assertEqual(
            sorted(codes(result)),
            ["VID_EXPLICIT_TIMING_OVERFLOW", "VID_EXPLICIT_TIMING_SHORTFALL"],
        )
        by_code = {finding["code"]: finding for finding in result["findings"]}
        self.assertAlmostEqual(
            by_code["VID_EXPLICIT_TIMING_OVERFLOW"]["overflow_seconds"], 1.0
        )
        self.assertAlmostEqual(
            by_code["VID_EXPLICIT_TIMING_SHORTFALL"]["unallocated_seconds"], 1.0
        )

    def test_a_mid_plan_gap_is_a_shortfall(self) -> None:
        result = self.check([spec("M-1", "SHOT-1", ["0.0-1.0", "3.0-4.0"])])

        self.assertEqual(codes(result), ["VID_EXPLICIT_TIMING_SHORTFALL"])
        self.assertAlmostEqual(result["findings"][0]["unallocated_seconds"], 2.0)

    def test_shortfall_is_reported_because_the_remainder_is_filled_unsourced(
        self,
    ) -> None:
        # The direction that used to be argued away as harmless: an unallocated
        # remainder does not render as a held frame.
        result = self.check([spec("M-1", "SHOT-2", ["0.0-2.0", "2.0-4.0"])])

        self.assertEqual(codes(result), ["VID_EXPLICIT_TIMING_SHORTFALL"])
        self.assertAlmostEqual(result["findings"][0]["unallocated_seconds"], 2.0)

    def test_a_gap_before_the_first_segment_is_a_shortfall(self) -> None:
        result = self.check([spec("M-1", "SHOT-1", ["1.0-4.0"])])

        self.assertEqual(codes(result), ["VID_EXPLICIT_TIMING_SHORTFALL"])
        self.assertAlmostEqual(result["findings"][0]["unallocated_seconds"], 1.0)

    def test_the_declared_overlap_field_exists_in_the_shipped_template(self) -> None:
        # The validator refuses undeclared overlap, so the field it wants must
        # be findable by a creator working from the template rather than only
        # in this script's source.
        template = (
            SUITE
            / "skills/short-drama-video-prompts/assets/motion-spec.jsonl.md"
        ).read_text(encoding="utf-8")
        recipe = (
            SUITE / "skills/short-drama-video-prompts/references/motion-recipe.md"
        ).read_text(encoding="utf-8")
        self.assertIn("declares_overlap", template)
        self.assertIn("declares_overlap", recipe)

    def test_undeclared_overlap_is_refused_rather_than_summed(self) -> None:
        result = self.check([spec("M-1", "SHOT-1", ["0.0-3.0", "1.0-4.0"])])

        self.assertEqual(codes(result), ["VID_EXPLICIT_TIMING_UNDECLARED_OVERLAP"])

    def test_declared_overlap_is_measured_as_a_union(self) -> None:
        result = self.check(
            [spec("M-1", "SHOT-1", ["0.0-3.0", "1.0-4.0"], declares_overlap=True)]
        )

        self.assertEqual(result["status"], "pass")

    def test_an_enclosing_segment_still_reports_its_overrun(self) -> None:
        # Sorting (start, end) orders by start, so the latest-starting segment
        # can end deep inside an earlier one that encloses it. Reading the
        # overrun off that tuple missed the overflow entirely, and the clipped
        # union is full here so the shortfall branch stays silent too — the
        # plan reported clean while running 1s past the accepted end.
        result = self.check(
            [
                spec(
                    "M-1",
                    "SHOT-1",
                    ["0.0-5.0", "0.5-1.5", "2.0-3.0"],
                    declares_overlap=True,
                )
            ]
        )

        self.assertEqual(codes(result), ["VID_EXPLICIT_TIMING_OVERFLOW"])
        self.assertAlmostEqual(result["findings"][0]["endpoint_seconds"], 5.0)
        self.assertAlmostEqual(result["findings"][0]["overflow_seconds"], 1.0)

    def test_union_length_counts_a_nested_interval_once(self) -> None:
        # Pins _union_length against a plain sum, which agrees with it on every
        # other case in this file.
        self.assertAlmostEqual(
            motion_timing_check._union_length([(0.0, 5.0), (1.0, 2.0)]), 5.0
        )
        self.assertAlmostEqual(
            motion_timing_check._union_length([(0.0, 1.0), (1.0, 2.0)]), 2.0
        )
        self.assertAlmostEqual(
            motion_timing_check._union_length([(0.0, 1.0), (3.0, 4.0)]), 2.0
        )
        self.assertAlmostEqual(motion_timing_check._union_length([]), 0.0)

    def test_a_relative_plan_carrying_explicit_segments_is_a_contradiction(
        self,
    ) -> None:
        # Not silently arithmetic-checked: SKILL.md promises relative plans are
        # reported rather than judged, so the contradiction is the finding.
        candidate = spec("M-1", "SHOT-1", ["0.0-1.0"])
        candidate["timing_plan"]["mode"] = "relative"

        result = self.check([candidate])

        self.assertEqual(codes(result), ["VID_TIMING_MODE_INCONSISTENT"])
        self.assertEqual(result["relative_plans"], [])

    def test_declared_total_accepts_either_documented_reading(self) -> None:
        # The field is `declared_total_or_endpoint_seconds`; with overlap
        # declared the union (4.0) and the endpoint (4.0) can diverge from the
        # naive sum, and insisting on one reading fails a correct plan.
        for declared in (4.0, 3.0):
            with self.subTest(declared=declared):
                result = self.check(
                    [
                        spec(
                            "M-1",
                            "SHOT-1",
                            ["0.0-3.0", "1.0-4.0"],
                            declares_overlap=True,
                            declared_total_or_endpoint_seconds=declared,
                        )
                    ]
                )
                expected = [] if declared == 4.0 else ["VID_DECLARED_TOTAL_MISMATCH"]
                self.assertEqual(codes(result), expected)

    def test_relative_plans_are_reported_not_judged(self) -> None:
        result = motion_timing_check.check(
            [
                {
                    "motion_id": "M-REL",
                    "shot_ref": {"record_id": "SHOT-1"},
                    "timing_plan": {"mode": "relative"},
                    "ordered_subject_motion": [
                        {"timing": {"mode": "relative", "value": "开头"}}
                    ],
                }
            ],
            SHOTS,
        )

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["relative_plans"], ["M-REL"])
        self.assertEqual(result["explicit_checked"], 0)

    def test_explicit_mode_without_a_readable_interval_is_a_finding(self) -> None:
        # Silence here would recreate the exact failure this script removes.
        result = self.check([spec("M-1", "SHOT-1", ["中段"])])

        self.assertEqual(codes(result), ["VID_EXPLICIT_TIMING_UNPARSEABLE"])

    def test_a_stale_duration_projection_is_reported_before_the_arithmetic(
        self,
    ) -> None:
        candidate = spec("M-1", "SHOT-1", ["0.0-9.0"])
        candidate["boundary_refs"] = {
            "duration": {"record_id": "SHOT-1", "value_seconds": 9.0}
        }

        result = self.check([candidate])

        self.assertEqual(codes(result), ["VID_DURATION_PROJECTION_STALE"])

    def test_declared_total_must_match_what_the_segments_cover(self) -> None:
        result = self.check(
            [
                spec(
                    "M-1",
                    "SHOT-1",
                    ["0.0-2.0", "2.0-4.0"],
                    declared_total_or_endpoint_seconds=3.0,
                )
            ]
        )

        self.assertEqual(codes(result), ["VID_DECLARED_TOTAL_MISMATCH"])

    def test_a_shot_without_an_accepted_duration_is_unmeasured_not_passing(
        self,
    ) -> None:
        result = self.check([spec("M-1", "SHOT-MISSING", ["0.0-4.0"])])

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["unmeasured_specs"], ["M-1"])
        self.assertEqual(result["explicit_checked"], 0)

    def test_hand_authored_decimals_do_not_trip_float_noise(self) -> None:
        result = self.check(
            [spec("M-1", "SHOT-1", ["0.0-0.1", "0.1-0.3", "0.3-4.0"])]
        )

        self.assertEqual(result["status"], "pass")

    def test_every_finding_code_is_registered_in_the_diagnostic_catalogue(self) -> None:
        catalogue = (
            SUITE
            / "skills/short-drama-video-prompts/references/review-and-fixtures.md"
        ).read_text(encoding="utf-8")
        emitted = {
            "VID_EXPLICIT_TIMING_OVERFLOW",
            "VID_EXPLICIT_TIMING_SHORTFALL",
            "VID_EXPLICIT_TIMING_UNDECLARED_OVERLAP",
            "VID_EXPLICIT_TIMING_UNPARSEABLE",
            "VID_DECLARED_TOTAL_MISMATCH",
            "VID_DURATION_PROJECTION_STALE",
            "VID_TIMING_MODE_INCONSISTENT",
        }
        for code in sorted(emitted):
            with self.subTest(code=code):
                self.assertIn(code, catalogue)

    def test_a_spec_without_a_motion_id_cannot_be_checked_at_all(self) -> None:
        with self.assertRaises(motion_timing_check.CheckError):
            self.check([{"timing_plan": {"mode": "explicit"}}])


if __name__ == "__main__":
    unittest.main()
