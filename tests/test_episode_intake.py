import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama-develop/scripts/episode_intake.py"
SPEC = importlib.util.spec_from_file_location("episode_intake", SCRIPT)
assert SPEC and SPEC.loader
episode_intake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(episode_intake)

PROJECT_TOOL_SCRIPT = SUITE / "skills/short-drama/scripts/project_tool.py"
PROJECT_TOOL_SPEC = importlib.util.spec_from_file_location(
    "episode_intake_project_tool", PROJECT_TOOL_SCRIPT
)
assert PROJECT_TOOL_SPEC and PROJECT_TOOL_SPEC.loader
project_tool = importlib.util.module_from_spec(PROJECT_TOOL_SPEC)
PROJECT_TOOL_SPEC.loader.exec_module(project_tool)


def serial(count: int, newline: bytes = b"\n", body_lines: int = 2) -> bytes:
    return newline.join(
        (
            f"第{number}集\n"
            + "\n".join(
                f"本集正文 {number}-{line}: 人物在场景中做出选择并承担后果。"
                for line in range(1, body_lines + 1)
            )
        )
        .encode("utf-8")
        .replace(b"\n", newline)
        for number in range(1, count + 1)
    ) + newline


def run_cli(*args: str | Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


class EpisodeIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "用户 输入 完整剧本.txt"
        self.index_path = self.root / "分集 index.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self, data: bytes | None = None) -> dict:
        self.source.write_bytes(data if data is not None else serial(54))
        document = episode_intake.build_index(self.source)
        episode_intake.write_index(self.index_path, document)
        return document

    def test_indexes_54_episodes_without_embedding_body(self) -> None:
        document = self.build(serial(54, body_lines=44))
        self.assertEqual(document["episode_count"], 54)
        self.assertEqual(document["problems"], [])
        self.assertEqual(document["source_byte_length"], len(self.source.read_bytes()))
        self.assertGreater(document["source_byte_length"], 80_000)
        self.assertEqual([e["episode_id"] for e in document["episodes"]], [f"EP{i:03d}" for i in range(1, 55)])
        encoded = json.dumps(document, ensure_ascii=False)
        self.assertNotIn("本集正文", encoded)
        for row in document["episodes"]:
            self.assertIn("byte_start", row)
            self.assertIn("byte_end", row)
            self.assertIn("content_sha256", row)
            self.assertEqual(row["byte_length"], row["byte_end"] - row["byte_start"])
        self.assertEqual(document["source_ref"], self.source.name)

    def test_chinese_numbers_and_markdown_ep_headings(self) -> None:
        document = self.build("第一集 起点\n甲\n第二集：转折\n乙\n# EP 3 收束\n丙\n".encode())
        self.assertEqual([e["episode_id"] for e in document["episodes"]], ["EP001", "EP002", "EP003"])
        self.assertEqual(document["problems"], [])

    def test_manual_episode_ids_use_the_project_canonical_spelling(self) -> None:
        self.source.write_text("第一幕\n甲\n", encoding="utf-8")
        for episode_id in ("EP000", "EP0001"):
            with self.subTest(episode_id=episode_id):
                with self.assertRaisesRegex(ValueError, "invalid episode_id"):
                    episode_intake.make_manual_index(
                        self.source,
                        [(episode_id, 0, len(self.source.read_bytes()))],
                    )

    def test_utf8_bom_is_preserved_inside_the_first_exact_slice(self) -> None:
        raw = b"\xef\xbb\xbf" + serial(2, b"\r\n")
        document = self.build(raw)
        self.assertEqual(document["problems"], [])
        first = episode_intake.slice_episode(self.index_path, self.source, "EP001")
        self.assertTrue(first.startswith(b"\xef\xbb\xbf"))

    def test_crlf_offsets_are_exact_and_slice_is_isolated(self) -> None:
        document = self.build(serial(3, b"\r\n"))
        row = document["episodes"][1]
        raw = self.source.read_bytes()
        self.assertEqual(raw[row["byte_start"]:row["byte_end"]], episode_intake.slice_episode(self.index_path, self.source, "EP002"))
        sliced = episode_intake.slice_episode(self.index_path, self.source, "EP002")
        self.assertIn("本集正文 2".encode(), sliced)
        self.assertNotIn("本集正文 1".encode(), sliced)
        self.assertNotIn("本集正文 3".encode(), sliced)
        self.assertIn(b"\r\n", sliced)

    def test_verify_rejects_changed_source_and_slice_does_not_overwrite(self) -> None:
        self.build(serial(3))
        destination = self.root / "EP002.txt"
        destination.write_bytes(b"keep")
        self.source.write_bytes(b"changed\n" + self.source.read_bytes())
        result = episode_intake.verify_index(self.index_path, self.source)
        self.assertFalse(result["verified"])
        with self.assertRaises(ValueError):
            episode_intake.slice_episode(self.index_path, self.source, "EP002", destination)
        self.assertEqual(destination.read_bytes(), b"keep")

    def test_holes_duplicates_and_empty_episodes_are_detected(self) -> None:
        for data, fragment in (
            ("第一集\n甲\n第三集\n丙\n".encode(), "missing"),
            ("第一集\n甲\n第一集\n乙\n".encode(), "duplicate"),
            ("第一集\n第二集\n乙\n".encode(), "empty"),
        ):
            with self.subTest(fragment=fragment):
                self.source.write_bytes(data)
                result = episode_intake.build_index(self.source)
                self.assertTrue(any(fragment in p for p in result["problems"]), result["problems"])

    def test_agent_authored_index_with_nonstandard_boundaries_verifies_and_slices(self) -> None:
        raw = "开篇\n【上集】\n甲\n【下集】\n乙\n".encode()
        self.source.write_bytes(raw)
        first = raw.index("【上集】".encode())
        second = raw.index("【下集】".encode())
        document = episode_intake.make_manual_index(
            self.source,
            [("EP001", first, second), ("EP002", second, len(raw))],
        )
        episode_intake.write_index(self.index_path, document)
        self.assertTrue(episode_intake.verify_index(self.index_path, self.source)["verified"])
        self.assertEqual(episode_intake.slice_episode(self.index_path, self.source, "EP002"), raw[second:])

    def test_agent_can_supply_line_boundaries_without_calculating_bytes(self) -> None:
        raw = "前言\r\n【上集】\r\n甲\r\n【下集】\r\n乙\r\n".encode()
        self.source.write_bytes(raw)
        boundaries = self.root / "agent boundaries.jsonl"
        boundaries.write_text(
            '{"episode_id":"EP001","line_start":2}\n'
            '{"episode_id":"EP002","line_start":4,"line_end":5}\n',
            encoding="utf-8",
        )
        document = episode_intake.build_manual_index(
            self.source,
            boundaries,
            source_ref="素材/完整 剧本.txt",
        )
        episode_intake.write_index(self.index_path, document)
        self.assertEqual(document["source_ref"], "素材/完整 剧本.txt")
        self.assertEqual(document["episodes"][0]["line_end"], 3)
        self.assertEqual(document["episodes"][0]["byte_start"], len("前言\r\n".encode()))
        self.assertEqual(document["unmapped_spans"][0]["byte_start"], 0)
        self.assertEqual(
            document["unmapped_spans"][0]["byte_end"],
            len("前言\r\n".encode()),
        )
        self.assertTrue(episode_intake.verify_index(self.index_path, self.source)["verified"])


class ProgressAndMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "full.txt"
        self.source.write_bytes(serial(8))
        self.index = self.root / "index.json"
        episode_intake.write_index(self.index, episode_intake.build_index(self.source))
        self.map = self.root / "episode-map.jsonl"
        self.next_map = self.root / "episode-map.next.jsonl"
        self.checkpoint = self.root / "checkpoint.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def batch(self, name: str, records: list[dict]) -> Path:
        path = self.root / name
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        return path

    def test_progress_handles_noncontiguous_completion_and_is_byte_idempotent(self) -> None:
        self.map.write_text('{"episode_id":"EP001"}\n{"episode_id":"EP004"}\n', encoding="utf-8")
        first = episode_intake.progress(self.index, self.source, self.map, self.checkpoint, batch_size=3)
        before = self.checkpoint.read_bytes()
        second = episode_intake.progress(self.index, self.source, self.map, self.checkpoint, batch_size=3)
        self.assertEqual(before, self.checkpoint.read_bytes())
        self.assertEqual(first, second)
        self.assertEqual(first["completed"], ["EP001", "EP004"])
        self.assertEqual(first["next_batch"], ["EP002", "EP003", "EP005"])
        self.assertEqual(first["pending"], ["EP002", "EP003", "EP005", "EP006", "EP007", "EP008"])
        self.assertEqual(first["source_sha256"], episode_intake.sha256(self.source.read_bytes()))
        self.assertIn("index_sha256", first)
        self.assertIn("map_sha256", first)
        self.assertEqual(len(first["record_hashes"]), 2)

    def test_merge_is_atomic_sorted_and_replay_is_noop(self) -> None:
        initial = self.batch("initial.jsonl", [{"episode_id": "EP004", "beat": "丁"}])
        episode_intake.merge_batch(
            self.index,
            self.source,
            initial,
            self.map,
            self.next_map,
            self.checkpoint,
            batch_size=3,
        )
        self.map.write_bytes(self.next_map.read_bytes())
        batch = self.batch("batch.jsonl", [{"episode_id": "EP002", "beat": "乙"}, {"episode_id": "EP001", "beat": "甲"}])
        episode_intake.merge_batch(
            self.index,
            self.source,
            batch,
            self.map,
            self.next_map,
            self.checkpoint,
            batch_size=3,
        )
        map_before = self.next_map.read_bytes()
        checkpoint_before = self.checkpoint.read_bytes()
        replay = self.root / "episode-map.replay.jsonl"
        result = episode_intake.merge_batch(
            self.index,
            self.source,
            batch,
            self.next_map,
            replay,
            self.checkpoint,
            batch_size=3,
        )
        self.assertEqual(map_before, replay.read_bytes())
        self.assertEqual(checkpoint_before, self.checkpoint.read_bytes())
        self.assertEqual(result["added"], [])
        self.assertEqual(
            [
                json.loads(line)["episode_id"]
                for line in replay.read_text(encoding="utf-8").splitlines()
            ],
            ["EP001", "EP002", "EP004"],
        )

    def test_merge_conflict_or_oversized_batch_does_not_overwrite(self) -> None:
        initial = self.batch("one.jsonl", [{"episode_id": "EP001", "beat": "old"}])
        episode_intake.merge_batch(
            self.index,
            self.source,
            initial,
            self.map,
            self.next_map,
            self.checkpoint,
            batch_size=2,
        )
        before_map = self.next_map.read_bytes()
        before_checkpoint = self.checkpoint.read_bytes()
        conflict = self.batch("conflict.jsonl", [{"episode_id": "EP001", "beat": "new"}])
        with self.assertRaises(ValueError):
            episode_intake.merge_batch(
                self.index,
                self.source,
                conflict,
                self.next_map,
                self.map,
                self.checkpoint,
                batch_size=2,
            )
        self.assertFalse(self.map.exists())
        self.assertEqual(self.next_map.read_bytes(), before_map)
        self.assertEqual(self.checkpoint.read_bytes(), before_checkpoint)
        too_many = self.batch("large.jsonl", [{"episode_id": f"EP{i:03d}"} for i in range(2, 8)])
        with self.assertRaises(ValueError):
            episode_intake.merge_batch(
                self.index,
                self.source,
                too_many,
                self.next_map,
                self.map,
                self.checkpoint,
                batch_size=5,
            )
        self.assertFalse(self.map.exists())
        self.assertEqual(self.next_map.read_bytes(), before_map)


class CliTests(unittest.TestCase):
    def test_unicode_space_paths_end_to_end_and_error_preserves_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "完整 剧本 中文.txt"
            index = root / "索引 文件.json"
            output = root / "分集 二.txt"
            source.write_bytes(serial(3, b"\r\n"))
            result = run_cli("index", source, "--out", index)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run_cli("slice", index, source, "EP002", "--out", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("本集正文 2".encode(), output.read_bytes())
            bad = root / "bad.txt"
            bad.write_text("第一集\n甲\n第三集\n丙\n", encoding="utf-8")
            result = run_cli("index", bad, "--out", index)
            self.assertNotEqual(result.returncode, 0)
            candidate = json.loads(index.read_text(encoding="utf-8"))
            self.assertTrue(any("missing" in problem for problem in candidate["problems"]))

    def test_manual_index_cli_accepts_agent_line_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "非标准 剧本.txt"
            source.write_text("说明\n第一幕\n甲\n第二幕\n乙\n", encoding="utf-8")
            boundaries = root / "边界.jsonl"
            boundaries.write_text(
                '{"episode_id":"EP001","line_start":2}\n'
                '{"episode_id":"EP002","line_start":4}\n',
                encoding="utf-8",
            )
            index = root / "手工 index.json"
            result = run_cli(
                "manual-index",
                source,
                boundaries,
                "--out",
                index,
                "--source-ref",
                "输入/非标准 剧本.txt",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            document = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(document["source_ref"], "输入/非标准 剧本.txt")
            self.assertEqual(document["episodes"][1]["line_start"], 4)

    def test_unreadable_source_does_not_replace_existing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bad.txt"
            source.write_bytes(b"\xff\xfe")
            index = root / "index.json"
            index.write_bytes(b"keep")
            result = run_cli("index", source, "--out", index)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(index.read_bytes(), b"keep")

    def test_cli_requires_explicit_context_and_output_bounds(self) -> None:
        commands = (
            ["progress", "index.json", "source.txt", "map.jsonl"],
            [
                "merge",
                "index.json",
                "source.txt",
                "batch.jsonl",
                "map.jsonl",
                "--out",
                "next.jsonl",
            ],
            ["slice", "index.json", "source.txt", "EP001"],
        )
        for command in commands:
            with self.subTest(command=command[0]):
                result = run_cli(*command)
                self.assertNotEqual(result.returncode, 0)
                required = "--out" if command[0] == "slice" else "--batch-size"
                self.assertIn(required, result.stderr)


class WorkflowContractTests(unittest.TestCase):
    def test_router_and_develop_skill_route_complete_scripts_to_agent_led_intake(self) -> None:
        router = (SUITE / "skills/short-drama/SKILL.md").read_text(encoding="utf-8")
        develop = (SUITE / "skills/short-drama-develop/SKILL.md").read_text(
            encoding="utf-8"
        )
        workflow = (
            SUITE
            / "skills/short-drama-develop/references/multi-episode-intake.md"
        ).read_text(encoding="utf-8")

        self.assertIn("已有多集完整剧本/散稿", router)
        self.assertIn("multi-episode-intake.md", develop)
        for command in (" index ", " manual-index ", " slice ", " progress ", " merge "):
            with self.subTest(command=command):
                self.assertIn(f"episode_intake.py{command}", workflow)
        self.assertIn("Agent 判断这份文件实际怎样分集", workflow)
        self.assertIn("批次没有通用固定值", workflow)
        self.assertIn("不生成转折、回报、钩子等创作字段", workflow)

    def test_episode_intake_index_and_map_belong_to_develop(self) -> None:
        for relative in (
            "项目开发/episode-intake-index.json",
            "项目开发/episode-map.jsonl",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(
                    project_tool._expected_path_owner(relative),
                    "short-drama-develop",
                )


if __name__ == "__main__":
    unittest.main()
