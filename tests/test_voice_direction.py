import importlib.util
import json
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama/scripts/project_tool.py"
SPEC = importlib.util.spec_from_file_location("sd_project_tool_voice", SCRIPT)
assert SPEC and SPEC.loader
project_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(project_tool)

ASSETS = SUITE / "skills/short-drama-assets"
CORE = SUITE / "skills/short-drama"


class OwnershipTests(unittest.TestCase):
    def test_casting_sheet_belongs_to_the_stage_that_owns_the_identity(self) -> None:
        # The casting sheet and the identity it projects stay with one owner, so
        # the reference binding that defines a character and the check that two
        # characters stay distinguishable cannot drift into different stages.
        for relative in ("设定集/characters.jsonl", "设定集/voice-casting.md"):
            with self.subTest(relative=relative):
                self.assertEqual(
                    project_tool._expected_path_owner(relative), "short-drama-assets"
                )

    def test_per_line_delivery_still_belongs_to_write(self) -> None:
        self.assertEqual(
            project_tool._expected_path_owner("剧集/EP001/voice-record-sheet.jsonl"),
            "short-drama-write",
        )


class RuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = (ASSETS / "references/stage-contract.md").read_text(
            encoding="utf-8"
        )

    def test_voice_rules_live_in_the_stage_that_can_block_them(self) -> None:
        # Each of these has exactly one remedy — change the accepted voice
        # direction — so it belongs where that record is written.
        for rule in ("AST-07", "AST-08", "AST-09", "AST-10", "AST-11", "AST-12"):
            with self.subTest(rule=rule):
                self.assertIn(rule, self.contract)

    def test_one_spelling_per_pronunciation_is_structural(self) -> None:
        # A second spelling is invisible in text review and only audible in the
        # finished cut, so it blocks rather than waiting for judgement.
        self.assertIn("structural_invariant", self._rule_row("AST-10"))

    def test_reference_admission_mirrors_the_image_contract(self) -> None:
        # Binding a recording is not the same as having heard it, exactly as
        # binding a reference image is not the same as having seen it.
        self.assertIn("unverified", self._rule_row("AST-09"))

    def _rule_row(self, rule: str) -> str:
        return next(
            row for row in self.contract.splitlines() if row.startswith(f"| {rule} ")
        )


class RecordShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.direction = json.loads(
            (ASSETS / "assets/character-look.example.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )["voice_direction"]

    def test_timbre_is_carried_by_a_reference_binding(self) -> None:
        reference = self.direction["reference"]
        self.assertIn("artifact_ref", reference)
        self.assertIn("role", reference)
        self.assertIn("admission_status", reference)

    def test_binding_states_both_what_it_controls_and_what_it_must_not(self) -> None:
        reference = self.direction["reference"]
        self.assertTrue(reference["may_control"])
        self.assertTrue(reference["must_not_control"])

    def test_the_take_emotion_and_room_stay_out_of_identity(self) -> None:
        # These are present in every recording and belong to none of them, so
        # the example has to show them excluded rather than merely omitted.
        excluded = "".join(self.direction["reference"]["must_not_control"])
        self.assertIn("情绪", excluded)
        self.assertIn("混响", excluded)

    def test_reference_audio_stays_in_creator_inputs(self) -> None:
        artifact = self.direction["reference"]["artifact_ref"]["artifact"]
        self.assertTrue(artifact.startswith("输入/"), artifact)

    def test_selection_criteria_are_bounded_by_counter_examples(self) -> None:
        self.assertTrue(self.direction["selection_criteria"])
        for criterion in self.direction["selection_criteria"]:
            self.assertTrue(criterion["counter_example"])

    def test_distinction_names_the_nearest_character(self) -> None:
        distinction = self.direction["distinction"]
        self.assertTrue(distinction["nearest_character_id"])
        self.assertTrue(distinction["distinguishing_trait"])

    def test_excluded_and_absent_stay_distinguishable(self) -> None:
        self.assertTrue(self.direction["not_voice_identity"])


class ReferenceDocTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ASSETS / "references/voice-direction.md").read_text(
            encoding="utf-8"
        )

    def test_reference_audio_is_named_as_the_carrier(self) -> None:
        self.assertIn("参考音频是载体", self.text)

    def test_missing_reference_is_recorded_not_described_around(self) -> None:
        self.assertIn("reference: null", self.text)

    def test_listening_admission_states_are_documented(self) -> None:
        for state in ("creator_described", "audibly_inspected", "unverified"):
            with self.subTest(state=state):
                self.assertIn(state, self.text)

    def test_it_points_at_the_shared_reference_discipline(self) -> None:
        # Voice mirrors the suite's existing reference-binding contract rather
        # than inventing a second vocabulary for the same problem.
        self.assertIn("reference-roles.md", self.text)

    def test_casting_sheet_is_documented_as_a_cache(self) -> None:
        self.assertIn("voice-casting.md", self.text)
        self.assertIn("不是第二份权威", self.text)

    def test_suite_still_promises_no_audio(self) -> None:
        self.assertIn("不生成音频", self.text)


class ReviewCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rubric = (
            SUITE / "skills/short-drama-review/references/rubric-assets-prompts.md"
        ).read_text(encoding="utf-8")

    def test_the_assets_rubric_can_answer_for_voice(self) -> None:
        for rule in ("AST-08", "AST-09", "AST-10", "AST-11", "AST-12"):
            with self.subTest(rule=rule):
                self.assertIn(rule, self.rubric)

    def test_findings_route_to_the_record_not_the_cache(self) -> None:
        self.assertIn("cache", self.rubric)


if __name__ == "__main__":
    unittest.main()
