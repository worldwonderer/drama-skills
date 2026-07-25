import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama-write/scripts/screenplay_index.py"
SPEC = importlib.util.spec_from_file_location("short_drama_screenplay_index", SCRIPT)
assert SPEC and SPEC.loader
screenplay_index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(screenplay_index)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def blocks(path: Path) -> list[dict[str, Any]]:
    return [row for row in read_jsonl(path) if row["record_type"] == "block"]


class ScreenplayIndexTests(unittest.TestCase):
    def test_speaker_roster_rejects_non_text_and_empty_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "screenplay.md"
            output = root / "screenplay-index.jsonl"
            source.write_text("# EP001\n\n## EP001-SC001 内 · 门厅 · 夜\n", encoding="utf-8")

            for speakers in ([123], ["   "]):
                with self.subTest(speakers=speakers):
                    with self.assertRaisesRegex(ValueError, "speaker names"):
                        screenplay_index.build_index(source, output, speakers=speakers)

    def test_candidate_preview_marks_every_source_ref_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "screenplay.md"
            output = root / "screenplay-index.jsonl"
            source.write_text(
                "# EP001\n\n## EP001-SC001 内 · 门厅 · 夜\n\n她关上门。\n",
                encoding="utf-8",
            )

            screenplay_index.build_index(
                source,
                output,
                source_ref="episodes/EP001/screenplay.md",
                authority="candidate",
            )

            records = read_jsonl(output)
            refs = [record["source_ref"] for record in records if "source_ref" in record]
            self.assertTrue(refs)
            self.assertTrue(all(ref["authority"] == "candidate" for ref in refs))

    def test_stable_rerun_preserves_ids_offsets_hash_and_source_bytes(self) -> None:
        source_bytes = (
            "# EP001 夜班\r\n\r\n"
            "## EP001-SC001 内 · 档案室 · 夜\r\n\r\n"
            "林岚把复检记录压在桌角。\r\n\r\n"
            "林岚（压低声音）：你看见日期了吗？\r\n\r\n"
            "[SFX] 门外脚步停住。\r\n"
        ).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文 项目"
            root.mkdir()
            source = root / "第一集 剧本.md"
            source.write_bytes(source_bytes)
            first = root / "旧 索引.jsonl"
            second = root / "新 索引.jsonl"

            screenplay_index.build_index(source, first, speakers={"林岚"})
            screenplay_index.build_index(
                source,
                second,
                previous_index_path=first,
                previous_source_path=source,
                speakers={"林岚"},
            )

            self.assertEqual(source.read_bytes(), source_bytes)
            first_blocks = blocks(first)
            second_blocks = blocks(second)
            self.assertEqual(
                [row["block_id"] for row in first_blocks],
                [row["block_id"] for row in second_blocks],
            )
            self.assertTrue(
                all(row["mapping"]["status"] == "reused" for row in second_blocks)
            )
            for row in second_blocks:
                selected = source_bytes[row["byte_start"] : row["byte_end"]]
                self.assertEqual(hashlib.sha256(selected).hexdigest(), row["content_sha256"])
            meta = read_jsonl(second)[0]
            self.assertEqual(
                meta["source_ref"],
                {
                    "owner": "short-drama-write",
                    "artifact": "第一集 剧本.md",
                    "hash": hashlib.sha256(source_bytes).hexdigest(),
                },
            )
            self.assertNotIn("source_sha256", meta)
            self.assertNotIn("previous_source_sha256", meta)
            self.assertTrue(all("source_sha256" not in row for row in read_jsonl(second)))
            self.assertEqual(meta["newline_style"], "crlf")

    def test_previous_index_blocks_must_still_match_parsed_source_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "screenplay.md"
            previous_index = root / "previous.jsonl"
            next_index = root / "next.jsonl"
            source.write_text(
                "# EP001\n\n## EP001-SC001 内 · 门厅 · 夜\n\ndoor opens.\n",
                encoding="utf-8",
            )
            screenplay_index.build_index(source, previous_index)
            records = read_jsonl(previous_index)
            action = next(row for row in records if row.get("kind") == "action")
            action["byte_start"] += 1
            selected = source.read_bytes()[action["byte_start"] : action["byte_end"]]
            action["content_sha256"] = hashlib.sha256(selected).hexdigest()
            previous_index.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not resolve against previous source"):
                screenplay_index.build_index(
                    source,
                    next_index,
                    previous_index_path=previous_index,
                    previous_source_path=source,
                )

    def test_nearby_insertion_reuses_unchanged_block_ids(self) -> None:
        old_text = """# EP001 夜班

## EP001-SC001 内 · 档案室 · 夜

林岚把记录压在桌角。

周野：不是我签的。
"""
        new_text = """# EP001 夜班

## EP001-SC001 内 · 档案室 · 夜

门锁在他们身后扣上。

林岚把记录压在桌角。

周野：不是我签的。
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_source = root / "old.md"
            new_source = root / "new.md"
            old_index = root / "old.jsonl"
            new_index = root / "new.jsonl"
            old_source.write_text(old_text, encoding="utf-8", newline="")
            new_source.write_text(new_text, encoding="utf-8", newline="")
            screenplay_index.build_index(old_source, old_index, speakers={"周野"})
            screenplay_index.build_index(
                new_source,
                new_index,
                previous_index_path=old_index,
                previous_source_path=old_source,
                speakers={"周野"},
            )

            old_by_hash = {row["content_sha256"]: row["block_id"] for row in blocks(old_index)}
            new_rows = blocks(new_index)
            reused = [row for row in new_rows if row["mapping"]["status"] == "reused"]
            self.assertEqual(len(reused), 3)
            for row in reused:
                self.assertEqual(row["block_id"], old_by_hash[row["content_sha256"]])
            inserted = [row for row in new_rows if row["mapping"]["status"] == "new"]
            self.assertEqual(len(inserted), 1)
            self.assertNotIn(inserted[0]["block_id"], set(old_by_hash.values()))

    def test_split_and_merge_are_unresolved_instead_of_guessed(self) -> None:
        cases = {
            "split": (
                "她推开门。又把灯关掉。",
                "她推开门。\n\n又把灯关掉。",
            ),
            "merge": (
                "她推开门。\n\n又把灯关掉。",
                "她推开门。又把灯关掉。",
            ),
        }
        for name, (old_action, new_action) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                old_source = root / "old.md"
                new_source = root / "new.md"
                old_index = root / "old.jsonl"
                new_index = root / "new.jsonl"
                shell = "# EP001 夜班\n\n## EP001-SC001 内 · 门厅 · 夜\n\n{}\n"
                old_source.write_text(shell.format(old_action), encoding="utf-8")
                new_source.write_text(shell.format(new_action), encoding="utf-8")
                screenplay_index.build_index(old_source, old_index)
                screenplay_index.build_index(
                    new_source,
                    new_index,
                    previous_index_path=old_index,
                    previous_source_path=old_source,
                )

                old_action_id = next(
                    row["block_id"] for row in blocks(old_index) if row["kind"] == "action"
                )
                new_actions = [row for row in blocks(new_index) if row["kind"] == "action"]
                self.assertTrue(new_actions)
                self.assertTrue(
                    all(row["mapping"]["status"] == "unresolved" for row in new_actions)
                )
                self.assertNotIn(old_action_id, {row["block_id"] for row in new_actions})
                requests = [
                    row
                    for row in read_jsonl(new_index)
                    if row["record_type"] == "mapping_review_request"
                ]
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["reason"], name)
                self.assertIn(old_action_id, requests[0]["previous_block_ids"])

    def test_large_split_and_merge_are_unresolved_without_an_arbitrary_width_cap(self) -> None:
        parts = [f"她推开第{number}扇门。" for number in range(1, 10)]
        cases = {
            "split": ("".join(parts), "\n\n".join(parts)),
            "merge": ("\n\n".join(parts), "".join(parts)),
        }
        for name, (old_action, new_action) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                old_source = root / "old.md"
                new_source = root / "new.md"
                old_index = root / "old.jsonl"
                new_index = root / "new.jsonl"
                shell = "# EP001 夜班\n\n## EP001-SC001 内 · 门厅 · 夜\n\n{}\n"
                old_source.write_text(shell.format(old_action), encoding="utf-8")
                new_source.write_text(shell.format(new_action), encoding="utf-8")
                screenplay_index.build_index(old_source, old_index)

                summary = screenplay_index.build_index(
                    new_source,
                    new_index,
                    previous_index_path=old_index,
                    previous_source_path=old_source,
                )

                requests = [
                    row
                    for row in read_jsonl(new_index)
                    if row["record_type"] == "mapping_review_request"
                ]
                self.assertEqual(summary["review_status"], "review_required")
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["reason"], name)
                current_actions = [
                    row for row in blocks(new_index) if row["kind"] == "action"
                ]
                self.assertTrue(current_actions)
                self.assertTrue(
                    all(row["mapping"]["status"] == "unresolved" for row in current_actions)
                )

    def test_previous_dialogue_is_loaded_independently_of_current_speaker_roster(self) -> None:
        old_text = """# EP001 夜班

## EP001-SC001 内 · 门厅 · 夜

林岚：这句话会在修订中删除。
"""
        new_text = """# EP001 夜班

## EP001-SC001 内 · 门厅 · 夜

周野：现在只剩我在场。
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_source = root / "old.md"
            new_source = root / "new.md"
            old_index = root / "old.jsonl"
            new_index = root / "new.jsonl"
            old_source.write_text(old_text, encoding="utf-8")
            new_source.write_text(new_text, encoding="utf-8")
            screenplay_index.build_index(old_source, old_index, speakers={"林岚"})

            summary = screenplay_index.build_index(
                new_source,
                new_index,
                previous_index_path=old_index,
                previous_source_path=old_source,
                speakers={"周野"},
            )

            self.assertEqual(summary["review_status"], "clean")
            dialogue = next(row for row in blocks(new_index) if row["kind"] == "dialogue")
            self.assertEqual(dialogue["speaker"], "周野")

    def test_moved_duplicate_exact_blocks_require_explicit_mapping(self) -> None:
        old_text = """# EP001 夜班

## EP001-SC001 内 · 门厅 · 夜

门外传来两声敲门。

林岚：谁？

门外传来两声敲门。
"""
        new_text = old_text.replace("\n\n门外传来两声敲门。", "\n\n灯先灭了。\n\n门外传来两声敲门。", 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_source = root / "old.md"
            new_source = root / "new.md"
            old_index = root / "old.jsonl"
            new_index = root / "new.jsonl"
            old_source.write_text(old_text, encoding="utf-8")
            new_source.write_text(new_text, encoding="utf-8")
            screenplay_index.build_index(old_source, old_index, speakers={"林岚"})
            screenplay_index.build_index(
                new_source,
                new_index,
                previous_index_path=old_index,
                previous_source_path=old_source,
                speakers={"林岚"},
            )

            repeated_hash = hashlib.sha256("门外传来两声敲门。".encode()).hexdigest()
            repeated = [
                row for row in blocks(new_index) if row["content_sha256"] == repeated_hash
            ]
            self.assertEqual(len(repeated), 2)
            self.assertTrue(
                all(row["mapping"]["status"] == "unresolved" for row in repeated)
            )
            requests = [
                row
                for row in read_jsonl(new_index)
                if row["record_type"] == "mapping_review_request"
            ]
            self.assertEqual(requests[0]["reason"], "duplicate_exact_match")

    def test_invalid_heading_and_labels_are_reported_not_parsed_as_action(self) -> None:
        text = """# EP001 夜班

## EP001-SC001 室内 - 档案室 - 夜

不应归到上一场的文字。

## EP001-SC002 内 · 走廊 · 夜

[BGM] 紧张音乐。

林岚: 这不是受支持的全角对白格式。

[SFX 门外脚步停住。
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "screenplay.md"
            output = root / "screenplay-index.jsonl"
            source.write_text(text, encoding="utf-8")
            summary = screenplay_index.build_index(source, output, speakers={"周野"})

            rows = read_jsonl(output)
            issues = [row for row in rows if row["record_type"] == "source_issue"]
            codes = {row["issue_code"] for row in issues}
            self.assertIn("invalid_scene_heading", codes)
            self.assertIn("content_outside_scene", codes)
            self.assertIn("unsupported_production_tag", codes)
            self.assertIn("invalid_dialogue_syntax", codes)
            self.assertIn("malformed_production_tag", codes)
            self.assertEqual(summary["review_status"], "review_required")
            self.assertEqual(
                [row["kind"] for row in rows if row["record_type"] == "block"],
                ["scene_heading"],
            )

    def test_colon_action_requires_owner_resolution_instead_of_speaker_guessing(self) -> None:
        cases = {
            "single_line": "手机屏幕显示：密码错误。",
            "second_line": "她抬头看向墙面。\n墙上写着：闲人免进。",
        }
        for name, action in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "screenplay.md"
                output = root / "screenplay-index.jsonl"
                source.write_text(
                    "# EP001 夜班\n\n"
                    "## EP001-SC001 内 · 档案室 · 夜\n\n"
                    f"{action}\n",
                    encoding="utf-8",
                )

                summary = screenplay_index.build_index(source, output)
                rows = read_jsonl(output)

                self.assertEqual(summary["review_status"], "review_required")
                self.assertIn(
                    "ambiguous_dialogue_or_action",
                    {
                        row["issue_code"]
                        for row in rows
                        if row["record_type"] == "source_issue"
                    },
                )
                self.assertFalse(
                    any(
                        row.get("kind") == "dialogue"
                        for row in rows
                        if row["record_type"] == "block"
                    )
                )

    def test_missing_separator_before_dialogue_requires_review(self) -> None:
        text = """# EP001 夜班

## EP001-SC001 内 · 档案室 · 夜

林岚把复检记录压在桌角。
周野：日期不是我改的。
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "screenplay.md"
            output = root / "screenplay-index.jsonl"
            source.write_text(text, encoding="utf-8")

            summary = screenplay_index.build_index(source, output, speakers={"周野"})
            rows = read_jsonl(output)

            self.assertEqual(summary["review_status"], "review_required")
            self.assertIn(
                "missing_block_separator",
                {
                    row["issue_code"]
                    for row in rows
                    if row["record_type"] == "source_issue"
                },
            )
            self.assertEqual(
                [row["kind"] for row in rows if row["record_type"] == "block"],
                ["scene_heading"],
            )

    def test_cli_handles_space_paths_without_modifying_source(self) -> None:
        source_bytes = (
            "# EP001 夜班\n\n"
            "## EP001-SC001 内 · 档案室 · 夜\n\n"
            "<!-- 待确认：门锁年份。 -->\n\n"
            "林岚：签字不是我的。\n"
        ).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "项目 有空格"
            root.mkdir()
            source = root / "剧本 源.md"
            output = root / "派生 索引.jsonl"
            source.write_bytes(source_bytes)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(source),
                    "--output",
                    str(output),
                    "--speaker",
                    "林岚",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertEqual(source.read_bytes(), source_bytes)
            parsed = json.loads(result.stdout)
            self.assertEqual(parsed["review_status"], "clean")
            self.assertEqual(
                [row["kind"] for row in blocks(output)],
                ["scene_heading", "comment", "dialogue"],
            )


    def test_fullwidth_dialect_tag_is_diagnosed_without_dropping_its_block(self) -> None:
        """The diagnosis must not renumber block IDs downstream artifacts cite."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "screenplay.md"
            source.write_text(
                "# EP001 测试\n\n"
                "## EP001-SC001 内 · 会议室 · 日\n\n"
                "【特写】墙上的钟停在三点。\n\n"
                "角色甲：这是我的决定。\n",
                encoding="utf-8",
            )
            output = root / "index.jsonl"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(source),
                 "--output", str(output), "--speaker", "角色甲"],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            rows = read_jsonl(output)
            codes = [r["issue_code"] for r in rows if r["record_type"] == "source_issue"]
            self.assertIn("unsupported_production_tag", codes)
            # The paragraph still occupies a block, so later IDs keep their numbers.
            self.assertEqual(
                [row["kind"] for row in blocks(output)],
                ["scene_heading", "action", "dialogue"],
            )
            self.assertEqual(
                [row["block_id"] for row in blocks(output)][1:],
                ["BLK-EP001-SC001-A01", "BLK-EP001-SC001-D01"],
            )

    def test_dialect_dialogue_is_not_mistaken_for_a_production_tag(self) -> None:
        """production-format-dialect writes the cue with ASCII parens."""

        for line in ("【萧尘渊】(目光坚定)：我不会退。", "【周野】（冷）：你来了。"):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "screenplay.md"
                source.write_text(
                    "# EP001 测试\n\n"
                    "## EP001-SC001 内 · 会议室 · 日\n\n"
                    f"{line}\n",
                    encoding="utf-8",
                )
                output = root / "index.jsonl"
                subprocess.run(
                    [sys.executable, str(SCRIPT), str(source), "--output", str(output)],
                    check=True, capture_output=True, text=True,
                )
                codes = [
                    row["issue_code"]
                    for row in read_jsonl(output)
                    if row["record_type"] == "source_issue"
                ]
                self.assertIn("ambiguous_dialogue_or_action", codes)
                self.assertNotIn("unsupported_production_tag", codes)


if __name__ == "__main__":
    unittest.main()
