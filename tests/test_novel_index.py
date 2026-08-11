import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama-novel-analyze/scripts/novel_index.py"
SPEC = importlib.util.spec_from_file_location("short_drama_novel_index", SCRIPT)
assert SPEC and SPEC.loader
novel_index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(novel_index)


def prose(lines: int = 30) -> str:
    return "\n".join("正文内容这一行大约二十来个字符。" for _ in range(lines))


def chapters(numbers: list[str], title: str = "标题", unit: str = "章") -> str:
    return "\n\n".join(f"第{number}{unit} {title}\n{prose()}" for number in numbers)


def build_from(text: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "book.txt"
        source.write_text(text, encoding="utf-8")
        return novel_index.build_index(source)


class ChineseNumeralTests(unittest.TestCase):
    def test_reads_the_forms_serialized_fiction_actually_uses(self) -> None:
        cases = {
            "一": 1,
            "十": 10,
            "十五": 15,
            "二十三": 23,
            "一百零三": 103,
            "两百": 200,
            "两百零三": 203,
            "一千零一": 1001,
            "一千二百三十四": 1234,
            "123": 123,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(novel_index.chinese_to_int(text), expected)

    def test_unreadable_numeral_is_reported_not_raised(self) -> None:
        # A crash here would leave the creator with no boundary table at all,
        # rather than a table with one flagged row.
        self.assertIsNone(novel_index.chinese_to_int("甲"))
        self.assertIsNone(novel_index.chinese_to_int(""))


class ChapterIndexTests(unittest.TestCase):
    def test_plain_source_indexes_every_chapter(self) -> None:
        index = build_from(chapters(["一", "二", "三", "四", "五", "六"]) + "\n")
        self.assertEqual(index["chapter_count"], 6)
        self.assertEqual(index["contents_entries_dropped"], 0)
        self.assertEqual(index["problems"], [])
        self.assertEqual(
            [chapter["source_number"] for chapter in index["chapters"]],
            [1, 2, 3, 4, 5, 6],
        )

    def test_leading_contents_block_is_dropped(self) -> None:
        numbers = ["一", "二", "三", "四", "五", "六"]
        contents = "\n".join(f"第{number}章 目录条目" for number in numbers)
        index = build_from(contents + "\n\n" + chapters(numbers) + "\n")
        self.assertEqual(index["contents_entries_dropped"], 6)
        self.assertEqual(index["chapter_count"], 6)
        # Every surviving chapter must carry real prose; a contents entry would
        # slice to a body of almost nothing and be reported as a problem.
        self.assertEqual(index["problems"], [])
        for chapter in index["chapters"]:
            self.assertGreater(chapter["char_count"], 100)

    def test_multi_volume_restart_is_flagged_not_dropped(self) -> None:
        # Each volume restarting at 第一章 is a legal structure. Treating the
        # first volume as a contents block would delete a sixth of the book.
        text = chapters(["一", "二", "三"]) + "\n\n" + chapters(["一", "二", "三"])
        index = build_from(text + "\n")
        self.assertEqual(index["chapter_count"], 6)
        self.assertEqual(index["contents_entries_dropped"], 0)
        self.assertTrue(index["volume_numbering_restarts"])
        self.assertEqual(index["volume_count"], 2)
        self.assertEqual(index["problems"], [])

    def test_dense_opening_that_is_not_a_contents_block_survives(self) -> None:
        # Short opening chapters whose numbers never reappear are story, not an
        # index, so density alone must not delete them.
        dense = "\n".join(f"第{number}章 短章" for number in ["一", "二", "三"])
        index = build_from(dense + "\n\n" + chapters(["四", "五", "六"]) + "\n")
        self.assertEqual(index["contents_entries_dropped"], 0)
        self.assertEqual(index["chapter_count"], 6)

    def test_numbering_jump_is_reported(self) -> None:
        index = build_from(chapters(["一", "二", "五"]) + "\n")
        self.assertTrue(
            any("jumps" in problem for problem in index["problems"]),
            index["problems"],
        )

    def test_source_with_no_heading_reports_rather_than_inventing_spans(self) -> None:
        index = build_from(prose() + "\n")
        self.assertEqual(index["chapter_count"], 0)
        self.assertTrue(index["problems"])


class NumberingValidationScopeTests(unittest.TestCase):
    """A numbering restart must not switch off validation for the whole book."""

    def test_stray_duplicate_does_not_hide_a_later_gap(self) -> None:
        # Regression: treating any repeated number as a multi-volume book
        # skipped every numbering check, so this 15-chapter hole came back with
        # an empty problem list.
        index = build_from(chapters(["一", "二", "二", "三", "四", "二十"]) + "\n")
        self.assertTrue(
            any("jumps from 4 to 20" in problem for problem in index["problems"]),
            index["problems"],
        )

    def test_restart_too_short_to_be_a_volume_is_reported(self) -> None:
        index = build_from(chapters(["一", "二", "一", "二", "三", "四"]) + "\n")
        self.assertTrue(
            any("restarts after only" in problem for problem in index["problems"]),
            index["problems"],
        )

    def test_gap_inside_a_real_volume_is_still_reported(self) -> None:
        first = chapters(["一", "二", "三"])
        second = chapters(["一", "二", "九"])
        index = build_from(first + "\n\n" + second + "\n")
        self.assertTrue(index["volume_numbering_restarts"])
        self.assertTrue(
            any("volume 2" in problem for problem in index["problems"]),
            index["problems"],
        )


class ChapterUnitTests(unittest.TestCase):
    """Subsections must not be promoted to chapters."""

    def test_subsection_headings_are_not_counted_as_chapters(self) -> None:
        # Regression: 第一节 matched the chapter pattern, produced a duplicate
        # source_number, and the duplicate then disabled every numbering check —
        # so a wrong chapter table was reported as clean.
        text = (
            f"第一章 标题\n{prose()}\n\n第一节 小节\n{prose()}\n\n"
            f"第二章 标题\n{prose()}\n\n第二节 小节\n{prose()}\n"
        )
        index = build_from(text)
        self.assertEqual(index["chapter_unit"], "章")
        self.assertEqual(index["chapter_count"], 2)
        self.assertEqual(
            [chapter["source_number"] for chapter in index["chapters"]], [1, 2]
        )
        self.assertEqual(index["ignored_heading_units"], {"节": 2})
        self.assertEqual(index["problems"], [])

    def test_a_book_numbered_in_jie_still_indexes(self) -> None:
        index = build_from(chapters(["一", "二", "三"], unit="节") + "\n")
        self.assertEqual(index["chapter_unit"], "节")
        self.assertEqual(index["chapter_count"], 3)
        self.assertEqual(index["problems"], [])

    def test_hui_numbering_indexes(self) -> None:
        index = build_from(chapters(["一", "二", "三"], unit="回") + "\n")
        self.assertEqual(index["chapter_unit"], "回")
        self.assertEqual(index["chapter_count"], 3)


class VerifyTests(unittest.TestCase):
    def test_spans_reconstruct_the_source_exactly(self) -> None:
        text = chapters(["一", "二", "三"]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "book.txt"
            source.write_text(text, encoding="utf-8")
            index_path = Path(directory) / "index.json"
            index_path.write_text(
                json.dumps(novel_index.build_index(source), ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertTrue(novel_index.verify_index(index_path, source)["verified"])

            # A source edited after indexing must invalidate the index, because
            # every span and every citation built on it now points elsewhere.
            source.write_text("插入一行\n" + text, encoding="utf-8")
            result = novel_index.verify_index(index_path, source)
            self.assertFalse(result["verified"])
            self.assertTrue(result["problems"])

    def test_hand_written_index_reports_instead_of_crashing(self) -> None:
        # The workflow tells creators to write spans by hand when a source has
        # no headings, so a row missing content_sha256 is an expected input.
        # Regression: it raised KeyError, which reads as a broken tool.
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "book.txt"
            source.write_text(prose() + "\n", encoding="utf-8")
            document = novel_index.build_index(source)
            document["chapters"] = [
                {"sequence": 1, "line_start": 1, "line_end": 15, "title": "手写"}
            ]
            index_path = Path(directory) / "index.json"
            index_path.write_text(
                json.dumps(document, ensure_ascii=False), encoding="utf-8"
            )
            result = novel_index.verify_index(index_path, source)
            self.assertFalse(result["verified"])
            self.assertTrue(
                any("content_sha256" in problem for problem in result["problems"]),
                result["problems"],
            )

    def test_span_outside_the_source_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "book.txt"
            source.write_text(prose(5) + "\n", encoding="utf-8")
            document = novel_index.build_index(source)
            document["chapters"] = [
                {
                    "sequence": 1,
                    "line_start": 1,
                    "line_end": 9999,
                    "content_sha256": "0" * 64,
                }
            ]
            index_path = Path(directory) / "index.json"
            index_path.write_text(
                json.dumps(document, ensure_ascii=False), encoding="utf-8"
            )
            result = novel_index.verify_index(index_path, source)
            self.assertFalse(result["verified"])
            self.assertTrue(
                any("outside the source" in problem for problem in result["problems"]),
                result["problems"],
            )


class SampleTests(unittest.TestCase):
    def _index_of(self, directory: Path, count: int) -> Path:
        source = directory / "book.txt"
        source.write_text(
            chapters([str(number) for number in range(1, count + 1)]) + "\n",
            encoding="utf-8",
        )
        index_path = directory / "index.json"
        index_path.write_text(
            json.dumps(novel_index.build_index(source), ensure_ascii=False),
            encoding="utf-8",
        )
        return index_path

    def test_sample_spans_the_whole_book_not_just_the_opening(self) -> None:
        # The triage answers "is this worth adapting", and that lives in the
        # spine, not in the first chapters.
        with tempfile.TemporaryDirectory() as directory:
            index_path = self._index_of(Path(directory), 100)
            result = novel_index.sample_chapters(index_path, count=10)
            sequences = [chapter["sequence"] for chapter in result["sampled"]]
            self.assertEqual(sequences[0], 1)
            self.assertEqual(sequences[-1], 100)
            self.assertGreater(max(sequences) - min(sequences), 50)

    def test_sample_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index_path = self._index_of(Path(directory), 60)
            first = novel_index.sample_chapters(index_path, count=8)
            second = novel_index.sample_chapters(index_path, count=8)
            self.assertEqual(first, second)

    def test_sample_reports_its_own_coverage(self) -> None:
        # A triage that does not state how little it read is indistinguishable
        # from a full pass.
        with tempfile.TemporaryDirectory() as directory:
            index_path = self._index_of(Path(directory), 200)
            result = novel_index.sample_chapters(index_path, count=12)
            self.assertEqual(result["total_chapters"], 200)
            self.assertEqual(result["sampled_count"], 12)
            self.assertEqual(result["coverage_ratio"], 0.06)

    def test_sample_never_exceeds_the_book(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index_path = self._index_of(Path(directory), 4)
            result = novel_index.sample_chapters(index_path, count=12)
            self.assertEqual(result["sampled_count"], 4)
            self.assertEqual(result["coverage_ratio"], 1.0)


class CoverageTests(unittest.TestCase):
    def _prepare(self, directory: Path, count: int) -> tuple[Path, Path]:
        source = directory / "book.txt"
        source.write_text(
            chapters([str(number) for number in range(1, count + 1)]) + "\n",
            encoding="utf-8",
        )
        index_path = directory / "index.json"
        index_path.write_text(
            json.dumps(novel_index.build_index(source), ensure_ascii=False),
            encoding="utf-8",
        )
        analysis = directory / "章节"
        analysis.mkdir()
        return index_path, analysis

    def test_missing_chapter_blocks_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index_path, analysis = self._prepare(Path(directory), 4)
            for sequence in (1, 2, 4):
                (analysis / f"ch-{sequence}-extract.md").write_text("x", encoding="utf-8")

            result = novel_index.coverage(index_path, analysis)
            self.assertEqual(result["missing"], [3])
            self.assertFalse(result["complete"])
            self.assertEqual(result["present"], 3)

    def test_complete_coverage_reports_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index_path, analysis = self._prepare(Path(directory), 2)
            for sequence in (1, 2):
                (analysis / f"ch-{sequence}-extract.md").write_text("x", encoding="utf-8")

            result = novel_index.coverage(index_path, analysis)
            self.assertTrue(result["complete"])
            self.assertEqual(result["missing"], [])

    def test_another_stage_output_does_not_satisfy_this_gate(self) -> None:
        # Regression: the gate matched the first number in any .md filename, so
        # a triage read-through of chapters 1-3 counted as their extraction and
        # the book was reported complete with three chapters never extracted.
        with tempfile.TemporaryDirectory() as directory:
            index_path, analysis = self._prepare(Path(directory), 5)
            for sequence in (1, 2, 3):
                (analysis / f"ch-{sequence}-triage.md").write_text("x", encoding="utf-8")
            for sequence in (4, 5):
                (analysis / f"ch-{sequence}-extract.md").write_text("x", encoding="utf-8")

            result = novel_index.coverage(index_path, analysis)
            self.assertEqual(result["missing"], [1, 2, 3])
            self.assertFalse(result["complete"])

    def test_a_drifted_filename_is_named_not_silently_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index_path, analysis = self._prepare(Path(directory), 2)
            (analysis / "ch-1-extract.md").write_text("x", encoding="utf-8")
            (analysis / "ch-2 extract.md").write_text("x", encoding="utf-8")

            result = novel_index.coverage(index_path, analysis)
            self.assertEqual(result["missing"], [2])
            self.assertEqual(result["unmatched_files"], ["ch-2 extract.md"])

    def test_stage_is_selectable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index_path, analysis = self._prepare(Path(directory), 2)
            for sequence in (1, 2):
                (analysis / f"ch-{sequence}-triage.md").write_text("x", encoding="utf-8")

            result = novel_index.coverage(index_path, analysis, stage="triage")
            self.assertTrue(result["complete"])


if __name__ == "__main__":
    unittest.main()


class OwnershipTests(unittest.TestCase):
    """The analysis layer proposes; only develop may write the contract."""

    def setUp(self) -> None:
        script = SUITE / "skills/short-drama/scripts/project_tool.py"
        spec = importlib.util.spec_from_file_location("sd_project_tool", script)
        assert spec and spec.loader
        self.project_tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.project_tool)

    def test_declared_analysis_artifacts_are_owned(self) -> None:
        for relative in (
            "项目开发/source-analysis/_index.json",
            "项目开发/source-analysis/triage.md",
            "项目开发/source-analysis/episode-candidates.jsonl",
            "项目开发/source-analysis/adaptation-value.md",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(
                    self.project_tool._expected_path_owner(relative),
                    "short-drama-novel-analyze",
                )

    def test_adaptation_contract_still_belongs_to_develop(self) -> None:
        self.assertEqual(
            self.project_tool._expected_path_owner("项目开发/adaptation-map.jsonl"),
            "short-drama-develop",
        )

    def test_generated_chapter_files_stay_owner_unconstrained(self) -> None:
        # Per-chapter names are generated, so declaring them is impossible;
        # they follow the same rule as every other undeclared path.
        self.assertIsNone(
            self.project_tool._expected_path_owner(
                "项目开发/source-analysis/chapters/ch-1-extract.md"
            )
        )

    def test_review_carries_a_rubric_for_this_stage(self) -> None:
        # Both the skill and the analysis layer route quality verdicts to
        # review, so review must have a scope and a rubric to answer with.
        review = SUITE / "skills/short-drama-review"
        self.assertIn("source_analysis", (review / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue((review / "references/rubric-source-analysis.md").exists())


class CommandLineTests(unittest.TestCase):
    """The CLI is what the workflow actually runs, so its defaults are tested.

    Regression: the coverage stage marker was spelled once in the function
    signature and once in the argparse flag. They drifted, and every chapter
    came back missing while the extraction files sat right there — invisible to
    tests that call the function directly.
    """

    def _book(self, directory: Path, count: int) -> tuple[Path, Path]:
        source = directory / "book.txt"
        source.write_text(
            chapters([str(number) for number in range(1, count + 1)]) + "\n",
            encoding="utf-8",
        )
        index_path = directory / "_index.json"
        self.assertEqual(
            novel_index.main(["index", str(source), "--out", str(index_path)]), 0
        )
        return source, index_path

    def test_coverage_flag_default_matches_the_function_default(self) -> None:
        # Exercised through main() rather than by reading the parser, so the
        # assertion covers the path the workflow actually runs.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, index_path = self._book(root, 3)
            analysis = root / "chapters"
            analysis.mkdir()
            for sequence in (1, 2, 3):
                (analysis / f"ch-{sequence}-extract.md").write_text(
                    "x", encoding="utf-8"
                )
            self.assertEqual(
                novel_index.main(["coverage", str(index_path), str(analysis)]), 0
            )

    def test_index_exits_nonzero_when_it_has_problems(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.txt"
            source.write_text(chapters(["一", "二", "五"]) + "\n", encoding="utf-8")
            index_path = root / "_index.json"
            self.assertEqual(
                novel_index.main(["index", str(source), "--out", str(index_path)]), 1
            )

    def test_verify_and_sample_run_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, index_path = self._book(root, 20)
            self.assertEqual(
                novel_index.main(["verify", str(index_path), str(source)]), 0
            )
            self.assertEqual(
                novel_index.main(["sample", str(index_path), "--count", "5"]), 0
            )

    def test_leading_contents_block_survives_the_cli_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            numbers = [str(number) for number in range(1, 13)]
            contents = "\n".join(f"第{number}章 目录条目" for number in numbers)
            source = root / "book.txt"
            source.write_text(
                contents + "\n\n" + chapters(numbers) + "\n", encoding="utf-8"
            )
            index_path = root / "_index.json"
            self.assertEqual(
                novel_index.main(["index", str(source), "--out", str(index_path)]), 0
            )
            document = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(document["chapter_count"], 12)
            self.assertEqual(document["contents_entries_dropped"], 12)
