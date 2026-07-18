import json
import re
import unittest
from pathlib import Path
from typing import Any


SUITE = Path(__file__).resolve().parents[1]
FIXTURES = SUITE / "tests/fixtures"
JOURNEYS = FIXTURES / "journeys"
INVALID = FIXTURES / "invalid"

EXPECTED_JOURNEYS = {
    "JOURNEY-URBAN-001",
    "JOURNEY-FANTASY-001",
    "JOURNEY-DIALOGUE-001",
    "JOURNEY-SERIAL-001",
}
EXPECTED_LAYERS = {
    "story",
    "script",
    "asset",
    "image_prompt",
    "shot",
    "video_prompt",
    "continuity",
    "review",
}
ANSWER_KEYS = {
    "answer",
    "answer_key",
    "expected",
    "expected_output",
    "golden_output",
    "solution",
    "suggested_fix",
}


def json_documents(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    return [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(root.glob("*.json"))
    ]


def all_mapping_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in all_mapping_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in all_mapping_keys(child)}
    return set()


class FixtureContractTests(unittest.TestCase):
    def test_blind_journeys_are_input_only(self) -> None:
        documents = json_documents(JOURNEYS)
        self.assertEqual({document["fixture_id"] for _, document in documents}, EXPECTED_JOURNEYS)
        for path, document in documents:
            with self.subTest(path=path.name):
                self.assertEqual(document["fixture_kind"], "blind_journey_input")
                self.assertEqual(document["provenance"], "synthetic")
                self.assertIn(document["entry_mode"], {"idea", "episode_outline", "script_first"})
                self.assertTrue(document["creator_input"])
                self.assertEqual(all_mapping_keys(document) & ANSWER_KEYS, set())
                self.assertNotIn("private_backup", json.dumps(document, ensure_ascii=False))

    def test_invalid_craft_inputs_cover_all_layers_without_corrections(self) -> None:
        documents = json_documents(INVALID)
        self.assertEqual({document["layer"] for _, document in documents}, EXPECTED_LAYERS)
        self.assertEqual(len(documents), len(EXPECTED_LAYERS))
        for path, document in documents:
            with self.subTest(path=path.name):
                self.assertEqual(document["fixture_kind"], "invalid_craft_input")
                self.assertEqual(document["provenance"], "synthetic")
                self.assertTrue(document["input_artifact"])
                self.assertEqual(all_mapping_keys(document) & ANSWER_KEYS, set())

    def test_fixtures_have_no_paths_urls_credentials_or_source_runtime_terms(self) -> None:
        unix_absolute = re.compile(r"(?<![\w.])/(?:Users|home|private|var|tmp)/")
        windows_absolute = re.compile(r"\b[A-Za-z]:[\\/]")
        url = re.compile(r"https?://", re.IGNORECASE)
        forbidden = {
            "api" + "_key",
            "access" + "_token",
            "mongo" + "db",
            "project" + "token",
            "private" + "_backup",
        }
        local = Path(__file__).resolve().parent / "local-terms.txt"
        if local.is_file():
            forbidden |= {
                stripped
                for line in local.read_text(encoding="utf-8").splitlines()
                if (stripped := line.strip()) and not stripped.startswith("#")
            }
        for path in FIXTURES.rglob("*.json"):
            text = path.read_text(encoding="utf-8")
            folded = text.casefold()
            with self.subTest(path=path.relative_to(FIXTURES)):
                self.assertIsNone(unix_absolute.search(text))
                self.assertIsNone(windows_absolute.search(text))
                self.assertIsNone(url.search(text))
                for term in forbidden:
                    self.assertNotIn(term.casefold(), folded)


if __name__ == "__main__":
    unittest.main()
