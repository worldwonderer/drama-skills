"""Each stage has to stay traceable to the artifact the creator accepted.

Two reports from the same creator, two days apart, are one complaint: a
downstream document does not carry a resolvable reference to the upstream one.
Issue #101 asks for the video prompt to name the pictures a shot sends without
the suite first producing them; issue #100 asks the storyboard to cut the
accepted screenplay rather than re-write it. Both are checked here.
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EPISODE = ROOT / "examples/creator-first/EP001"
EXPLICIT_TEXT_TO_VIDEO = "无（创作者已明确选择文生视频）。"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


creator_markdown_check = load_module(
    "traceability_creator_check",
    ROOT / "skills/short-drama/scripts/creator_markdown_check.py",
)
production_tool = load_module(
    "traceability_production",
    ROOT / "skills/short-drama-produce/scripts/production_tool.py",
)

# The shot this plan belongs to opens on a hand on the desk, so its start frame
# is the shot's own keyframe and its identity anchor is the character board.
CREATOR_SUPPLIED_PLAN = (
    "PLAN-SHOT-START（顺序：1）· SHOT-EP001-001《本镜冻结关键帧》"
    "（用途：起始帧；控制：起始构图、手部位置；不得控制：后续动作、终态）；"
    "PLAN-JIANGCHEN（顺序：2）· IMG-JIANGCHEN-SHEET《江晨角色板》"
    "（用途：身份；控制：脸型、体态、本集常服；不得控制：构图、动作）"
)


class EpisodeFixture(unittest.TestCase):
    """A scratch copy of the shipped episode that individual tests mutate."""

    def episode(self, directory: str) -> tuple[Path, Path]:
        project = Path(directory)
        episode = project / "剧集/EP001"
        shutil.copytree(EPISODE, episode)
        return project, episode

    def edit(self, path: Path, old: str, new: str, count: int = 1) -> None:
        document = path.read_text(encoding="utf-8")
        self.assertIn(old, document)
        path.write_text(document.replace(old, new, count), encoding="utf-8")

    def bind(self, episode: Path, declaration: str) -> None:
        """Put one declaration on the first shot and its motion, as the contract requires."""
        for name in ("分镜.md", "视频提示词.md"):
            self.edit(
                episode / name,
                f"- 输入参考图：{EXPLICIT_TEXT_TO_VIDEO}",
                f"- 输入参考图：{declaration}",
            )
        self.edit(episode / "视频提示词.md", "- 生成方式：文生视频", "- 生成方式：图生视频")


class CreatorSuppliedReferenceTests(EpisodeFixture):
    """Issue #101: pictures made outside the project still have to be nameable."""

    def test_a_plan_reaches_a_final_image_to_video_prompt_with_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.bind(episode, CREATOR_SUPPLIED_PLAN)

            self.assertEqual(creator_markdown_check.validate_episode(episode, project), [])
            self.assertEqual(
                list((project / "剧集/EP001/制作成果").glob("**/*.png")),
                [],
                "the plan must not require a single produced file",
            )

    def test_a_plan_is_image_conditioned_rather_than_text_to_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.bind(episode, CREATOR_SUPPLIED_PLAN)
            self.edit(episode / "视频提示词.md", "- 生成方式：图生视频", "- 生成方式：文生视频")

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(any("生成方式应为图生视频" in error for error in errors), errors)
            self.assertFalse(
                any("静默降级为文生视频" in error for error in errors),
                "a declared plan is a choice, not an unanswered gap",
            )

    def test_a_plan_still_blocks_while_a_real_gap_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.bind(episode, CREATOR_SUPPLIED_PLAN + "；待补参考图：夜间走廊地理")

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(
                any("仍有待补参考图" in error for error in errors), errors
            )

    def test_a_plan_slot_must_locate_an_entry_that_exists(self) -> None:
        cases = {
            "missing image board": (
                CREATOR_SUPPLIED_PLAN.replace("IMG-JIANGCHEN-SHEET", "IMG-NOT-THERE"),
                "PLAN 指向不存在的 IMG 条目",
            ),
            "missing shot": (
                CREATOR_SUPPLIED_PLAN.replace("SHOT-EP001-001《", "SHOT-EP001-404《"),
                "PLAN 指向不存在的 SHOT 条目",
            ),
            "label drifted from the board": (
                CREATOR_SUPPLIED_PLAN.replace("《江晨角色板》", "《另一个名字》"),
                "PLAN 中文名称与 IMG 标题不一致",
            ),
            "file path instead of an entry": (
                "PLAN-X（顺序：1）· 输入/参考图/江晨.png《江晨定妆照》"
                "（用途：身份；控制：脸型；不得控制：构图）",
                "输入参考图必须使用完整 PLAN 语法",
            ),
            "purpose outside the closed set": (
                CREATOR_SUPPLIED_PLAN.replace("（用途：身份；", "（用途：全参考；"),
                "PLAN 用途不在允许集合内",
            ),
            "attach order not contiguous": (
                CREATOR_SUPPLIED_PLAN.replace("（顺序：2）", "（顺序：4）"),
                "PLAN 顺序必须唯一且从 1 连续编号",
            ),
        }
        for label, (declaration, expected) in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                project, episode = self.episode(directory)
                self.bind(episode, declaration)

                errors = creator_markdown_check.validate_episode(episode, project)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_a_plan_and_a_real_file_can_share_one_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            reference = project / "输入/参考图/江晨定妆.png"
            reference.parent.mkdir(parents=True)
            reference.write_bytes(b"structural fixture")
            mixed = (
                "REF-JIANGCHEN-LOOK（顺序：1）· 输入/参考图/江晨定妆.png《江晨定妆照》"
                "（用途：身份；控制：脸型、体态；不得控制：构图、动作）；"
                "PLAN-SHOT-START（顺序：2）· SHOT-EP001-001《本镜冻结关键帧》"
                "（用途：起始帧；控制：起始构图；不得控制：后续动作）"
            )
            self.bind(episode, mixed)

            self.assertEqual(creator_markdown_check.validate_episode(episode, project), [])

    def test_a_plan_cannot_be_sent_to_production(self) -> None:
        """The suite holds no file for these pictures, so a job would be a lie."""
        section = (
            "## SHOT-EP001-001 · 年轻的手\n"
            f"- 输入参考图：{CREATOR_SUPPLIED_PLAN}\n"
        )
        with self.assertRaises(ValueError) as raised:
            production_tool._markdown_reference_bindings(
                section, field_name="输入参考图"
            )
        self.assertIn("PLAN", str(raised.exception))


class StoryboardFollowsTheScreenplayTests(EpisodeFixture):
    """Issue #100: the storyboard cuts the accepted screenplay, it does not revise it."""

    def test_a_shot_source_must_name_a_real_scene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(episode / "分镜.md", "- 来源：EP001-SC001", "- 来源：EP001-SC099")

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(
                any("来源场景不在《剧本.md》中: EP001-SC099" in error for error in errors),
                errors,
            )

    def test_a_shot_without_a_source_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(episode / "分镜.md", "- 来源：EP001-SC001\n", "")

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(any("缺少来源字段" in error for error in errors), errors)

    def test_a_scene_nobody_films_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            screenplay = episode / "剧本.md"
            screenplay.write_text(
                screenplay.read_text(encoding="utf-8")
                + "\n## EP001-SC090 内 · 走廊 · 夜\n\n江晨在门口停了一下。\n",
                encoding="utf-8",
            )

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(
                any("没有镜头承载场景 EP001-SC090" in error for error in errors), errors
            )

    def test_a_deliberate_omission_is_recorded_and_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            screenplay = episode / "剧本.md"
            screenplay.write_text(
                screenplay.read_text(encoding="utf-8")
                + "\n## EP001-SC090 内 · 走廊 · 夜\n\n江晨在门口停了一下。\n",
                encoding="utf-8",
            )
            self.edit(
                episode / "分镜.md",
                "# EP001 分镜\n",
                "# EP001 分镜\n\n- 未拍场次：EP001-SC090（理由：纯过场，情绪由上一镜的终点承担）\n",
            )

            self.assertEqual(creator_markdown_check.validate_episode(episode, project), [])

    def test_an_omission_without_a_reason_does_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(
                episode / "分镜.md",
                "# EP001 分镜\n",
                "# EP001 分镜\n\n- 未拍场次：EP001-SC001\n",
            )

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(
                any("未拍场次必须写成" in error for error in errors), errors
            )

    def test_a_source_may_carry_a_short_quote_after_the_scene_id(self) -> None:
        """《镜头手艺》 asks for the id plus a short quote, so the field must take both."""
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(
                episode / "分镜.md",
                "- 来源：EP001-SC001",
                "- 来源：EP001-SC001（“明白。我一定尽最大努力。”）",
            )

            self.assertEqual(creator_markdown_check.validate_episode(episode, project), [])

    def test_an_omission_line_inside_a_shot_does_not_pass_unnoticed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            screenplay = episode / "剧本.md"
            screenplay.write_text(
                screenplay.read_text(encoding="utf-8")
                + "\n## EP001-SC090 内 · 走廊 · 夜\n\n江晨在门口停了一下。\n",
                encoding="utf-8",
            )
            self.edit(
                episode / "分镜.md",
                "- 来源：EP001-SC001",
                "- 未拍场次：EP001-SC090（理由：纯过场）\n- 来源：EP001-SC001",
            )

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(
                any("未拍场次要写在第一个 SHOT 之前" in error for error in errors), errors
            )

    def test_a_scene_cannot_be_both_filmed_and_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(
                episode / "分镜.md",
                "# EP001 分镜\n",
                "# EP001 分镜\n\n- 未拍场次：EP001-SC001（理由：改由旁白交代）\n",
            )

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(
                any("既被镜头承载又记为未拍" in error for error in errors), errors
            )


class CopyableDialogueTests(EpisodeFixture):
    """A prompt delivers the accepted line; it does not write a better one."""

    ORIGINAL = "No extra hands, no camera shake, no text."

    def test_a_line_the_screenplay_never_wrote_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(
                episode / "视频提示词.md",
                self.ORIGINAL,
                'He says in Chinese, "这条台词剧本里根本没有写过。" No extra hands, no text.',
            )

            errors = creator_markdown_check.validate_episode(episode, project)
            self.assertTrue(
                any("引文不在《剧本.md》或《视觉设定.md》中" in error for error in errors),
                errors,
            )

    def test_a_line_quoted_from_the_screenplay_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(
                episode / "视频提示词.md",
                self.ORIGINAL,
                'He says in Chinese, "我一定尽最大努力。" No extra hands, no text.',
            )

            self.assertEqual(creator_markdown_check.validate_episode(episode, project), [])

    def test_punctuation_choices_do_not_decide_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(
                episode / "视频提示词.md",
                self.ORIGINAL,
                'He says in Chinese, "明白，我一定尽最大努力" No extra hands, no text.',
            )

            self.assertEqual(creator_markdown_check.validate_episode(episode, project), [])

    def test_a_short_in_frame_label_is_not_treated_as_dialogue(self) -> None:
        """Signage of two or three characters is the false-positive case, so it stays out."""
        with tempfile.TemporaryDirectory() as directory:
            project, episode = self.episode(directory)
            self.edit(
                episode / "视频提示词.md",
                self.ORIGINAL,
                'A banner reads "出口". No extra hands, no text.',
            )

            self.assertEqual(creator_markdown_check.validate_episode(episode, project), [])


if __name__ == "__main__":
    unittest.main()
