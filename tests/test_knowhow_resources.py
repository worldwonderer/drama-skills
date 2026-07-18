import json
import re
import unittest
from pathlib import Path


SUITE = Path(__file__).resolve().parents[1]
SKILLS = SUITE / "skills"
INDEX = SKILLS / "short-drama/references/knowhow-index.md"

CLASSES = {
    "structural_invariant",
    "reviewed_invariant",
    "craft_default",
    "taste_option",
}

PREFIX_OWNERS = {
    "STY": ("short-drama-develop",),
    "SCR": ("short-drama-write",),
    "AST": ("short-drama-assets",),
    "IMG": ("short-drama-image-prompts",),
    "SHT": ("short-drama-storyboard",),
    "VID": ("short-drama-video-prompts",),
    # Assets own inter-scene state deltas; storyboard owns shot boundaries; motion
    # must compare its projection with both, so continuity guidance may live in any.
    "CON": (
        "short-drama-assets",
        "short-drama-storyboard",
        "short-drama-video-prompts",
    ),
    "REV": ("short-drama-review",),
}

LAYER_RESOURCES = {
    "story": {
        "skill": "short-drama-develop",
        "template_dir": "assets",
        "rubric": "rubric-story-script.md",
    },
    "script": {
        "skill": "short-drama-write",
        "template_dir": "assets",
        "rubric": "rubric-story-script.md",
    },
    "asset": {
        "skill": "short-drama-assets",
        "template_dir": "templates",
        "rubric": "rubric-assets-prompts.md",
    },
    "image_prompt": {
        "skill": "short-drama-image-prompts",
        "template_dir": "templates",
        "rubric": "rubric-assets-prompts.md",
    },
    "shot": {
        "skill": "short-drama-storyboard",
        "template_dir": "assets",
        "rubric": "rubric-visual-motion.md",
    },
    "video_prompt": {
        "skill": "short-drama-video-prompts",
        "template_dir": "templates",
        "rubric": "rubric-visual-motion.md",
    },
    "continuity": {
        "skill": "short-drama-assets",
        "template_dir": "templates",
        "template_name": "continuity.example.jsonl",
        "rubric": "rubric-assets-prompts.md",
    },
    "review": {
        "skill": "short-drama-review",
        "template_dir": "assets",
        "rubric": "review-method.md",
    },
}


def parse_index() -> dict[str, str]:
    rules: dict[str, str] = {}
    pattern = re.compile(
        r"^\| ((?:STY|SCR|AST|IMG|SHT|VID|CON|REV)-\d{2}) "
        r"\| ([a-z_]+) \|",
        re.MULTILINE,
    )
    for rule_id, classification in pattern.findall(INDEX.read_text(encoding="utf-8")):
        if rule_id in rules:
            raise AssertionError(f"duplicate know-how ID: {rule_id}")
        rules[rule_id] = classification
    return rules


class KnowHowResourceTests(unittest.TestCase):
    def test_index_has_unique_rules_in_all_eight_layers(self) -> None:
        rules = parse_index()
        self.assertTrue(rules)
        self.assertEqual({rule.split("-", 1)[0] for rule in rules}, set(PREFIX_OWNERS))
        self.assertEqual(set(rules.values()), CLASSES)

    def test_each_layer_ships_reference_template_rubric_and_fixture(self) -> None:
        fixture_root = SUITE / "tests/fixtures"
        fixtures = list(fixture_root.rglob("*.json"))
        covered: set[str] = set()
        for path in fixtures:
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document.get("layer"), str):
                covered.add(document["layer"])
            covered.update(document.get("coverage_layers", []))

        review_root = SKILLS / "short-drama-review/references"
        for layer, matrix in LAYER_RESOURCES.items():
            with self.subTest(layer=layer):
                skill = SKILLS / matrix["skill"]
                self.assertTrue(any((skill / "references").glob("*.md")))
                template_root = skill / matrix["template_dir"]
                if "template_name" in matrix:
                    self.assertTrue((template_root / matrix["template_name"]).is_file())
                else:
                    self.assertTrue(any(path.is_file() for path in template_root.rglob("*")))
                self.assertTrue((review_root / matrix["rubric"]).is_file())
                self.assertIn(layer, covered)

    def test_serial_contract_fields_are_projected_to_writing_templates(self) -> None:
        develop = SKILLS / "short-drama-develop"
        write = SKILLS / "short-drama-write"
        serial_fields = {
            "local_dramatic_result",
            "character_progression",
            "information_release",
            "rhythm_plan",
        }
        release_fields = {
            "visible_carrier",
            "supported_claim",
            "unresolved_inference",
        }

        episode = json.loads(
            (develop / "assets/episode-map.jsonl").read_text(encoding="utf-8").strip()
        )
        self.assertTrue(serial_fields <= episode.keys())
        self.assertTrue(release_fields <= episode["information_release"][0].keys())

        projected = json.loads(
            (write / "assets/episode-card.json").read_text(encoding="utf-8")
        )
        projected_fields = set(projected["contract_authority"]["projected_fields"])
        self.assertTrue({f"/{field}" for field in serial_fields} <= projected_fields)

        standalone = json.loads(
            (write / "assets/episode-card-standalone.json").read_text(encoding="utf-8")
        )
        owned = standalone["owned_contract"]
        self.assertTrue(serial_fields <= owned.keys())
        self.assertTrue(release_fields <= owned["information_release"][0].keys())

        beat = json.loads(
            (write / "assets/beats.jsonl").read_text(encoding="utf-8").strip()
        )
        self.assertTrue({"dramatic_result", "rhythm"} <= beat.keys())

    def test_new_rules_have_reviewed_classification(self) -> None:
        self.assertEqual(parse_index().get("IMG-08"), "reviewed_invariant")
        self.assertEqual(parse_index().get("VID-11"), "reviewed_invariant")


class GovernanceSemanticsTests(unittest.TestCase):
    def test_relationship_templates_separate_same_artifact_ids_from_cross_artifact_refs(self) -> None:
        beat = json.loads(
            (SKILLS / "short-drama-write/assets/beats.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        for field in (
            "because_of_ids",
            "because_of_refs",
            "setup_ids",
            "setup_refs",
            "payoff_ids",
            "payoff_refs",
        ):
            self.assertIn(field, beat)

        episode = json.loads(
            (SKILLS / "short-drama-develop/assets/episode-map.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        self.assertIn("setup_ids", episode)
        self.assertIn("payoff_ids", episode)
        self.assertNotIn("setup_refs", episode)
        self.assertNotIn("payoff_refs", episode)

        coverage = json.loads(
            (SKILLS / "short-drama-storyboard/assets/coverage-template.json")
            .read_text(encoding="utf-8")
        )
        shot_ref = coverage["dispositions"][0]["shot_refs"][0]
        self.assertTrue({"owner", "artifact", "hash", "record_id"}.issubset(shot_ref))

        for name in ("shot-template.jsonl", "keyframe-template.jsonl"):
            document = json.loads(
                (SKILLS / "short-drama-storyboard/assets" / name)
                .read_text(encoding="utf-8")
                .strip()
            )
            text_ref = document["text_treatment_refs"][0]
            self.assertTrue(
                {"owner", "artifact", "hash", "record_id", "field"}.issubset(text_ref)
            )
            self.assertEqual(text_ref["artifact"], "bible/props.jsonl")
            self.assertEqual(text_ref["field"], "/text_policy")

    def test_proposed_asset_decisions_never_publish_accepted_bindings(self) -> None:
        import json

        fixture = SKILLS / "short-drama-assets/templates/decisions.example.jsonl"
        for line_number, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
            document = json.loads(line)
            status = document.get("creator_acceptance", {}).get("status")
            if status != "accepted":
                self.assertNotIn("accepted_binding", document, f"line {line_number}")
                self.assertIn("proposed_binding", document, f"line {line_number}")

    def test_diagnostic_catalogs_declare_complete_enforcement_metadata(self) -> None:
        catalogs = list(SKILLS.glob("*/references/*review*.md"))
        rows_checked = 0
        for catalog in catalogs:
            lines = catalog.read_text(encoding="utf-8").splitlines()
            for line in lines:
                cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
                if len(cells) < 6 or cells[1] not in CLASSES:
                    continue
                code, classification, enforcer, severity, owner = cells[:5]
                with self.subTest(catalog=catalog.name, code=code):
                    self.assertRegex(code, r"^[A-Z][A-Z0-9_]+$")
                    self.assertIn(enforcer, {"validator", "reviewer", "creator"})
                    self.assertTrue(severity)
                    self.assertTrue(owner)
                    if classification == "structural_invariant":
                        self.assertEqual(enforcer, "validator")
                    elif classification == "reviewed_invariant":
                        self.assertEqual(enforcer, "reviewer")
                    else:
                        self.assertNotEqual(enforcer, "validator")
                        self.assertNotIn(severity, {"fatal", "error", "revise"})
                rows_checked += 1
        self.assertGreaterEqual(rows_checked, 8, "no usable diagnostic catalog was found")

if __name__ == "__main__":
    unittest.main()
