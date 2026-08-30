"""Small regression for the creator-first authoring surface and native example."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
EPISODE = ROOT / "examples/creator-first/EP001"
EXPECTED = {
    "剧本.md",
    "视觉设定.md",
    "分镜.md",
    "图片提示词.md",
    "视频提示词.md",
}
CREATOR_SKILLS = (
    "short-drama",
    "short-drama-write",
    "short-drama-assets",
    "short-drama-image-prompts",
    "short-drama-storyboard",
    "short-drama-video-prompts",
)
ACTIVE_CREATOR_SKILLS = (*CREATOR_SKILLS, "short-drama-review")
CREATOR_DOCUMENTS = ROOT / "skills/short-drama/references/creator-documents.md"
EXPECTED_KNOWHOW = {
    "short-drama": {
        "audience-reveal.md",
        "contract-and-ownership.md",
        "creator-documents.md",
        "creator-workflow.md",
        "knowhow-index.md",
        "look-development.md",
        "pickup-and-alternate.md",
        "production-form-profiles.md",
        "reference-roles.md",
        "runtime-preflight.md",
    },
    "short-drama-write": {
        "dialogue-craft.md",
        "production-format-dialect.md",
        "scene-handoff-capsule.md",
        "scene-sound-dramaturgy.md",
        "screenplay-format.md",
        "script-craft.md",
        "stage-contract.md",
        "substitutable-realization.md",
    },
    "short-drama-assets": {
        "asset-review-checklist.md",
        "character-and-look.md",
        "continuity-delta.md",
        "continuity-lock.md",
        "identity-vs-variant.md",
        "location-and-view.md",
        "occurrence-extraction.md",
        "prop-and-state.md",
        "stage-contract.md",
        "voice-direction.md",
    },
    "short-drama-image-prompts": {
        "character-and-look.md",
        "common-recipe.md",
        "edit-and-revision.md",
        "location-plate.md",
        "look-and-state-variant.md",
        "lookdev-frame.md",
        "production-sheet-recipes.md",
        "prop-plate.md",
        "review-and-fixtures.md",
        "stage-contract.md",
    },
    "short-drama-storyboard": {
        "blocking-playbooks.md",
        "comic-keyframe-lexicon.md",
        "coverage-audition.md",
        "keyframe-craft.md",
        "production-shot-grammar.md",
        "review-and-fixtures.md",
        "scene-visual-plan.md",
        "screenplay-to-keyframe-example.md",
        "shot-craft.md",
        "shot-revision-identity.md",
        "stage-contract.md",
    },
    "short-drama-video-prompts": {
        "camera-audio-continuity.md",
        "delivery-profile.md",
        "generability.md",
        "motion-recipe.md",
        "performance-action-timing.md",
        "production-prompt-grammar.md",
        "review-and-fixtures.md",
        "stage-contract.md",
        "target-model-profile.md",
    },
    "short-drama-review": {
        "anti-template-repair.md",
        "production-quality-gates.md",
        "project-calibration.md",
        "review-method.md",
        "rubric-assets-prompts.md",
        "rubric-source-analysis.md",
        "rubric-story-script.md",
        "rubric-visual-motion.md",
        "stage-contract.md",
    },
}
INDEXER_SPEC = importlib.util.spec_from_file_location(
    "creator_first_screenplay_index",
    ROOT / "skills/short-drama-write/scripts/screenplay_index.py",
)
assert INDEXER_SPEC and INDEXER_SPEC.loader
screenplay_index = importlib.util.module_from_spec(INDEXER_SPEC)
INDEXER_SPEC.loader.exec_module(screenplay_index)
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "creator_markdown_check",
    ROOT / "skills/short-drama/scripts/creator_markdown_check.py",
)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
creator_markdown_check = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(creator_markdown_check)


def text(name: str) -> str:
    return (EPISODE / name).read_text(encoding="utf-8")


def heading_ids(document: str, prefix: str) -> list[str]:
    return re.findall(rf"^## ({prefix}[A-Z0-9-]+)\b", document, flags=re.MULTILINE)


def headings(document: str, level: int) -> list[str]:
    marker = "#" * level
    return re.findall(rf"^{marker} (.+)$", document, flags=re.MULTILINE)


def frozen_prompt(document: str, shot_id: str) -> str:
    section = document.split(f"## {shot_id}", 1)[1].split("\n## ", 1)[0]
    return section.split("### 冻结关键帧提示词", 1)[1]


def sections(document: str, prefix: str) -> dict[str, str]:
    matches = list(re.finditer(rf"^## ({prefix}[A-Z0-9-]+)\b", document, re.MULTILINE))
    return {
        match.group(1): document[
            match.start() : matches[index + 1].start()
            if index + 1 < len(matches)
            else None
        ]
        for index, match in enumerate(matches)
    }


def bullet_fields(document: str) -> dict[str, str]:
    return dict(re.findall(r"^- ([^：\n]+)：(.+)$", document, re.MULTILINE))


def image_prompt_references(value: str) -> list[tuple[str, str, str]]:
    return re.findall(
        r"\b(IMG-[A-Z0-9-]+)《([^》]+)》（控制：([^）]+)）",
        value,
    )


def input_image_references(value: str) -> list[tuple[str, str, str, str, str, str]]:
    return re.findall(
        r"(REF-[A-Z0-9-]+)（顺序：([1-9]\d*)）· "
        r"([^；]+?\.(?:png|jpe?g|webp))《([^》]+)》"
        r"（控制：([^；）]+)；不得控制：([^）]+)）",
        value,
        re.IGNORECASE,
    )


def is_portable_project_relative_path(value: str) -> bool:
    if not value or "\\" in value or re.match(r"^[A-Za-z]:", value):
        return False
    components = value.split("/")
    if any(component in {"", ".", ".."} for component in components):
        return False
    return not PurePosixPath(value).is_absolute()


def reachable_markdown(start: Path, root: Path) -> set[Path]:
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+\.md(?:#[^)]+)?)\)")
    seen: set[Path] = set()
    pending = [start.resolve()]
    root = root.resolve()

    while pending:
        current = pending.pop()
        if current in seen or not current.is_file():
            continue
        seen.add(current)
        for raw_target in link_pattern.findall(current.read_text(encoding="utf-8")):
            target = unquote(raw_target.split("#", 1)[0])
            resolved = (current.parent / target).resolve()
            if resolved == root or root in resolved.parents:
                pending.append(resolved)
    return seen


class CreatorFirstGoldenTests(unittest.TestCase):
    def test_episode_exposes_exactly_five_markdown_documents(self) -> None:
        files = {path.name for path in EPISODE.iterdir() if path.is_file()}
        self.assertEqual(files, EXPECTED)
        self.assertFalse(list(EPISODE.rglob("*.json")))
        self.assertFalse(list(EPISODE.rglob("*.jsonl")))

    def test_documents_follow_the_creator_markdown_contract(self) -> None:
        for name in EXPECTED:
            document = text(name)
            with self.subTest(document=name):
                self.assertEqual(len(headings(document, 1)), 1)
                self.assertGreater(len(headings(document, 2)), 0)

        screenplay = text("剧本.md")
        scene_ids = re.findall(
            r"^## (EP001-SC\d+) (?:内|外|内外) · \S.* · \S.*$",
            screenplay,
            re.MULTILINE,
        )
        self.assertGreaterEqual(len(scene_ids), 2)
        self.assertEqual(len(scene_ids), len(set(scene_ids)))

        visual = text("视觉设定.md")
        visual_entries = [heading.split(" · ", 1) for heading in headings(visual, 2)]
        self.assertTrue(all(len(entry) == 2 and all(entry) for entry in visual_entries))
        self.assertEqual(
            {entry[0] for entry in visual_entries}, {"人物", "地点", "道具"}
        )
        self.assertEqual(
            len(re.findall(r"^- 识别锚点：\S.+$", visual, re.MULTILINE)),
            len(visual_entries),
        )

    def test_storyboard_and_motion_cover_the_same_unique_shots(self) -> None:
        storyboard = text("分镜.md")
        video = text("视频提示词.md")
        shot_ids = heading_ids(storyboard, "SHOT-")
        motion_ids = heading_ids(video, "MOTION-")
        motion_shots = re.findall(r"^- 分镜：(SHOT-[A-Z0-9-]+)$", video, re.MULTILINE)

        self.assertGreater(len(shot_ids), 0)
        self.assertEqual(len(shot_ids), len(set(shot_ids)))
        self.assertEqual(len(motion_ids), len(set(motion_ids)))
        self.assertEqual(motion_shots, shot_ids)
        self.assertEqual(headings(storyboard, 3), ["冻结关键帧提示词"] * len(shot_ids))
        self.assertEqual(headings(video, 3), ["可复制提示词"] * len(shot_ids))

        storyboard_durations = {
            shot_id: int(re.search(r"^- 时长：(\d+)s$", body, re.MULTILINE).group(1))
            for shot_id, body in sections(storyboard, "SHOT-").items()
        }
        motion_durations = {
            re.search(r"^- 分镜：(SHOT-[A-Z0-9-]+)$", body, re.MULTILINE).group(1): int(
                re.search(r"^- 时长：(\d+)s$", body, re.MULTILINE).group(1)
            )
            for body in sections(video, "MOTION-").values()
        }
        self.assertEqual(motion_durations, storyboard_durations)

        screenplay_scenes = set(heading_ids(text("剧本.md"), "EP001-SC"))
        storyboard_scenes = {
            match.group(1)
            for body in sections(storyboard, "SHOT-").values()
            if (match := re.search(r"^- 来源：(EP001-SC\d+)$", body, re.MULTILINE))
        }
        self.assertEqual(storyboard_scenes, screenplay_scenes)

    def test_each_storyboard_image_prompt_id_has_a_matching_heading(self) -> None:
        image_headings = dict(
            re.findall(
                r"^## (IMG-[A-Z0-9-]+) · (.+)$",
                text("图片提示词.md"),
                re.MULTILINE,
            )
        )
        for shot_id, body in sections(text("分镜.md"), "SHOT-").items():
            with self.subTest(shot=shot_id):
                fields = bullet_fields(body)
                references = image_prompt_references(fields["图片提示词项"])
                referenced = {item[0] for item in references}
                self.assertTrue(referenced)
                self.assertLessEqual(referenced, image_headings.keys())
                for image_id, label, _ in references:
                    self.assertEqual(label, image_headings[image_id])

    def test_storyboard_image_prompt_references_explain_labels_and_scope(self) -> None:
        for shot_id, body in sections(text("分镜.md"), "SHOT-").items():
            with self.subTest(shot=shot_id):
                fields = bullet_fields(body)
                value = fields["图片提示词项"]
                references = image_prompt_references(value)
                self.assertEqual(
                    len(references),
                    len(re.findall(r"\bIMG-[A-Z0-9-]+\b", value)),
                )
                for _, label, scope in references:
                    self.assertRegex(label, r"[\u4e00-\u9fff]")
                    self.assertRegex(scope, r"[\u4e00-\u9fff]")

    def test_storyboard_reference_contract_supports_independent_state_axes(
        self,
    ) -> None:
        contract = CREATOR_DOCUMENTS.read_text(encoding="utf-8")
        markdown_examples = re.findall(r"```markdown\n(.*?)```", contract, re.DOTALL)
        reference_states = [
            fields
            for example in markdown_examples
            if {
                "图片提示词项",
                "输入参考图",
            }.issubset(fields := bullet_fields(example))
        ]

        prompt_without_image = [
            state
            for state in reference_states
            if image_prompt_references(state["图片提示词项"])
            and re.fullmatch(r"无(?:（[^）]+）)?。?", state["输入参考图"])
        ]
        self.assertEqual(len(prompt_without_image), 1)

        fallback_states = [
            state
            for state in reference_states
            if state["图片提示词项"] == "无"
            and re.fullmatch(r"无(?:（[^）]+）)?。?", state["输入参考图"])
            and "视觉依据" in state
        ]
        self.assertEqual(len(fallback_states), 1)
        fallback = fallback_states[0]
        self.assertRegex(fallback["输入参考图"], r"^无(?:（[^）]+）)?$")
        self.assertNotRegex(fallback["输入参考图"], r"\bIMG-[A-Z0-9-]+\b")
        self.assertRegex(fallback["视觉依据"], r"《视觉设定\.md》")
        self.assertRegex(fallback["视觉依据"], r"（控制：[^）]+）")

        image_without_prompt = [
            state
            for state in reference_states
            if state["图片提示词项"] == "无"
            and input_image_references(state["输入参考图"])
        ]
        self.assertEqual(len(image_without_prompt), 1)

        prompt_with_image = [
            state
            for state in reference_states
            if image_prompt_references(state["图片提示词项"])
            and input_image_references(state["输入参考图"])
        ]
        self.assertEqual(len(prompt_with_image), 1)

        for state in (*image_without_prompt, *prompt_with_image):
            input_field = state["输入参考图"]
            self.assertNotRegex(input_field, r"\bIMG-[A-Z0-9-]+\b")
            references = input_image_references(input_field)
            slots = [reference[0] for reference in references]
            orders = [int(reference[1]) for reference in references]
            self.assertEqual(len(slots), len(set(slots)))
            self.assertEqual(len(orders), len(set(orders)))
            for _, _, raw_path, label, scope, excluded_scope in references:
                self.assertTrue(is_portable_project_relative_path(raw_path))
                self.assertRegex(label, r"[\u4e00-\u9fff]")
                self.assertRegex(scope, r"[\u4e00-\u9fff]")
                self.assertRegex(excluded_scope, r"[\u4e00-\u9fff]")

        for unsafe_path in (
            "",
            r"..\secret.jpg",
            r"C:\Users\me\portrait.jpg",
            r"\\server\share\portrait.jpg",
            "../secret.jpg",
            "/absolute/portrait.jpg",
            "input//portrait.jpg",
            "input/./portrait.jpg",
            "input/../portrait.jpg",
        ):
            with self.subTest(unsafe_path=unsafe_path):
                self.assertFalse(is_portable_project_relative_path(unsafe_path))

        for shot_id, body in sections(text("分镜.md"), "SHOT-").items():
            with self.subTest(shot=shot_id):
                fields = bullet_fields(body)
                self.assertRegex(fields["输入参考图"], r"^无(?:（[^）]+）)?。?$")

    def test_creator_markdown_validator_accepts_the_golden_episode(self) -> None:
        self.assertEqual(creator_markdown_check.validate_episode(EPISODE, ROOT), [])

    def test_validator_cli_survives_a_non_utf8_stdout_encoding(self) -> None:
        """回归：CLI 用 print(f"...") 直接写 stdout，而诊断与剧集路径都是中文。

        stdout 重定向到文件或管道时 Windows 用 ANSI 代码页，默认的 strict 处理器
        在打印那一步抛 UnicodeEncodeError：一份完全合格的剧集退出码从 0 变成 1，
        不合格的剧集则只剩一段 traceback，创作者看不到到底哪里不对。stderr 早已
        是 backslashreplace，所以只有 stdout 会这样。POSIX 上用 PYTHONIOENCODING
        能走到同一个 TextIOWrapper，这条在开发机上就会红。
        """

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            episode = project / "剧集/EP001"
            episode.parent.mkdir(parents=True)
            shutil.copytree(EPISODE, episode)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills/short-drama/scripts/creator_markdown_check.py"),
                    str(episode),
                    "--project-root",
                    str(project),
                ],
                check=False,
                capture_output=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "ascii"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("OK: "), result.stdout)

    def test_creator_markdown_validator_accepts_a_real_ref_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            episode = project / "剧集/EP001"
            shutil.copytree(EPISODE, episode)
            reference = project / "输入/参考图/江晨定妆.png"
            reference.parent.mkdir(parents=True)
            reference.write_bytes(b"not decoded by the structural validator")
            declaration = (
                "REF-JIANGCHEN-LOOK（顺序：1）· 输入/参考图/江晨定妆.png"
                "《江晨定妆照》（控制：身份、造型；不得控制：构图、动作、表情）"
            )
            for name in ("分镜.md", "视频提示词.md"):
                path = episode / name
                document = path.read_text(encoding="utf-8")
                path.write_text(
                    document.replace(
                        "- 输入参考图：无。", f"- 输入参考图：{declaration}", 1
                    ),
                    encoding="utf-8",
                )
            video = episode / "视频提示词.md"
            video.write_text(
                video.read_text(encoding="utf-8").replace(
                    "- 生成方式：文生视频", "- 生成方式：图生视频", 1
                ),
                encoding="utf-8",
            )
            image_prompts = episode / "图片提示词.md"
            image_prompts.write_text(
                image_prompts.read_text(encoding="utf-8").replace(
                    "- 参考：无外部参考；三视图必须保持同一脸型、身高比例和服装细节。",
                    f"- 参考：{declaration}",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                creator_markdown_check.validate_episode(episode, project), []
            )

    def test_creator_markdown_validator_rejects_cross_document_contract_breaks(
        self,
    ) -> None:
        mutations = {
            "missing IMG": (
                "分镜.md",
                "IMG-JIANGCHEN-SHEET《江晨角色板》",
                "IMG-NOT-DEFINED《江晨角色板》",
                "IMG 标题不存在",
            ),
            "missing motion": (
                "视频提示词.md",
                "- 分镜：SHOT-EP001-001",
                "- 分镜：SHOT-EP001-999",
                "SHOT 与 MOTION 未一一对应",
            ),
            "reference mismatch": (
                "视频提示词.md",
                "- 输入参考图：无。",
                "- 输入参考图：REF-X（顺序：1）· 输入/x.png《参考图》（控制：身份；不得控制：动作）",
                "输入参考图与 SHOT-EP001-001 不一致",
            ),
            "missing static anchor": (
                "视频提示词.md",
                "- 静态视觉锚点：A clean young East Asian man's right hand rests palm-down",
                "- 静态视觉锚点：无\n- 删除字段：A clean young East Asian man's right hand rests palm-down",
                "文生视频缺少静态视觉锚点",
            ),
            "duplicate reference field": (
                "视频提示词.md",
                "- 输入参考图：无。",
                "- 输入参考图：无。\n- 输入参考图：无。",
                "字段重复: 输入参考图",
            ),
            "hidden REF in no-input marker": (
                "视频提示词.md",
                "- 输入参考图：无。",
                "- 输入参考图：无（ref-HERO）",
                "完整 REF 语法",
            ),
            "missing image prompt item": (
                "分镜.md",
                "- 图片提示词项：IMG-JIANGCHEN-SHEET",
                "- 删除字段：IMG-JIANGCHEN-SHEET",
                "缺少图片提示词项字段",
            ),
            "missing IMG copyable prompt": (
                "图片提示词.md",
                "### 可复制提示词",
                "### 普通说明",
                "缺少唯一且非空的可复制提示词",
            ),
            "unquoted IMG prompt content": (
                "图片提示词.md",
                "### 可复制提示词",
                "### 可复制提示词\nTHIS CRITICAL LINE IS NOT QUOTED",
                "缺少唯一且非空的可复制提示词",
            ),
            "invalid image reference declaration": (
                "图片提示词.md",
                "- 参考：无外部参考；三视图必须保持同一脸型、身高比例和服装细节。",
                "- 参考：随便写，不是无，也不是完整 REF",
                "参考必须声明无外部参考或使用完整 REF 语法",
            ),
            "hidden REF after no-reference prefix": (
                "图片提示词.md",
                "- 参考：无外部参考；三视图必须保持同一脸型、身高比例和服装细节。",
                "- 参考：无外部参考；REF-X（顺序：1）· 输入/x.png"
                "《人物参考》（控制：身份；不得控制：动作）",
                "完整 REF 语法",
            ),
            "motion drops a locked surface": (
                "视频提示词.md",
                "> A twenty-two-year-old East Asian man with a lean long face, high brow "
                "ridge, deep-set eyes and short cropped black hair wears buttoned "
                "olive-green stand-collar service dress",
                "> A twenty-two-year-old East Asian man with a lean long face, high brow "
                "ridge, deep-set eyes and short cropped black hair wears a buttoned navy "
                "mandarin-collar tunic",
                "LOCK-JIANGCHEN-DRESS: MOTION-EP001-003 可复制提示词缺少锁面",
            ),
            "keyframe drops a locked surface": (
                "分镜.md",
                "in buttoned olive-green stand-collar service dress",
                "in a buttoned navy mandarin-collar tunic",
                "LOCK-JIANGCHEN-DRESS: SHOT-EP001-003 冻结关键帧提示词缺少锁面",
            ),
            "image plate drops a locked surface": (
                "图片提示词.md",
                "Olive-green stand-collar service dress",
                "Olive-green service dress",
                "LOCK-JIANGCHEN-DRESS: IMG-JIANGCHEN-SHEET 可复制提示词缺少锁面",
            ),
            "a locked surface only inside a negative prompt": (
                "分镜.md",
                "in buttoned olive-green stand-collar service dress",
                "in a buttoned navy mandarin-collar tunic, no olive-green stand-collar service dress",
                "LOCK-JIANGCHEN-DRESS: SHOT-EP001-003 冻结关键帧提示词缺少锁面",
            ),
            "a locked surface glued to a prefix": (
                "分镜.md",
                "in buttoned olive-green stand-collar service dress",
                "in a fake-olive-green stand-collar service dress",
                "LOCK-JIANGCHEN-DRESS: SHOT-EP001-003 冻结关键帧提示词缺少锁面",
            ),
            "a locked surface glued to a suffix": (
                "分镜.md",
                "olive-green stand-collar service dress, lean long face",
                "olive-green stand-collar service dressing-gown, lean long face",
                "LOCK-JIANGCHEN-DRESS: SHOT-EP001-003 冻结关键帧提示词缺少锁面",
            ),
            "a star-bulleted continuity lock is not silently dropped": (
                "视觉设定.md",
                "- 连续性锁：LOCK-JIANGCHEN-DRESS《江晨橄榄绿立领常服》（镜头：SHOT-EP001-002、SHOT-EP001-003、SHOT-EP001-007；图片提示词项：IMG-JIANGCHEN-SHEET）· 锁面：olive-green stand-collar service dress",
                "* 连续性锁：把常服固定住",
                "连续性锁必须使用完整语法",
            ),
            "malformed continuity lock names the offending line": (
                "视觉设定.md",
                "- 连续性锁：LOCK-JIANGCHEN-DRESS《江晨橄榄绿立领常服》（镜头：SHOT-EP001-002、SHOT-EP001-003、SHOT-EP001-007；图片提示词项：IMG-JIANGCHEN-SHEET）· 锁面：olive-green stand-collar service dress",
                "- 连续性锁：把常服的立领固定住，别再变了",
                "连续性锁必须使用完整语法: - 连续性锁：把常服的立领固定住，别再变了",
            ),
            "continuity lock without a surface": (
                "视觉设定.md",
                "）· 锁面：olive-green stand-collar service dress",
                "）· 锁面：",
                "连续性锁必须使用完整语法",
            ),
            "continuity lock naming an unknown shot": (
                "视觉设定.md",
                "（镜头：SHOT-EP001-002、SHOT-EP001-003、SHOT-EP001-007；",
                "（镜头：SHOT-EP001-099；",
                "LOCK-JIANGCHEN-DRESS: 连续性锁指向不存在的镜头: SHOT-EP001-099",
            ),
            "continuity lock naming an unknown image entry": (
                "视觉设定.md",
                "图片提示词项：IMG-JIANGCHEN-SHEET",
                "图片提示词项：IMG-NOT-DEFINED",
                "连续性锁指向不存在的 IMG 条目: IMG-NOT-DEFINED",
            ),
            "duplicate continuity lock id": (
                "视觉设定.md",
                "- 连续性锁：LOCK-JIANGCHEN-DRESS《江晨橄榄绿立领常服》（镜头：SHOT-EP001-002、SHOT-EP001-003、SHOT-EP001-007；图片提示词项：IMG-JIANGCHEN-SHEET）· 锁面：olive-green stand-collar service dress",
                "- 连续性锁：LOCK-JIANGCHEN-DRESS《江晨橄榄绿立领常服》（镜头：SHOT-EP001-002、SHOT-EP001-003、SHOT-EP001-007；图片提示词项：IMG-JIANGCHEN-SHEET）· 锁面：olive-green stand-collar service dress\n- 连续性锁：LOCK-JIANGCHEN-DRESS《江晨橄榄绿立领常服》（镜头：SHOT-EP001-002、SHOT-EP001-003、SHOT-EP001-007；图片提示词项：IMG-JIANGCHEN-SHEET）· 锁面：olive-green stand-collar service dress",
                "LOCK-JIANGCHEN-DRESS: 连续性锁 ID 重复",
            ),
            "continuity lock mixing 全集 with named shots": (
                "视觉设定.md",
                "（镜头：SHOT-EP001-002、SHOT-EP001-003、SHOT-EP001-007；",
                "（镜头：全集、SHOT-EP001-002；",
                "不能把全集与具体镜头混写",
            ),
        }
        for label, (name, old, new, expected) in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                episode = project / "剧集/EP001"
                shutil.copytree(EPISODE, episode)
                path = episode / name
                document = path.read_text(encoding="utf-8")
                self.assertIn(old, document)
                path.write_text(document.replace(old, new, 1), encoding="utf-8")
                errors = creator_markdown_check.validate_episode(episode, project)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_creator_markdown_validator_rejects_missing_and_reordered_ref_files(
        self,
    ) -> None:
        bad_declarations = {
            "missing file": (
                "REF-A（顺序：1）· 输入/不存在.png《人物参考》（控制：身份；不得控制：动作）",
                "REF 文件不存在",
            ),
            "duplicate order": (
                "REF-A（顺序：1）· 输入/a.png《人物参考》（控制：身份；不得控制：动作）；"
                "REF-B（顺序：1）· 输入/b.png《场景参考》（控制：地理；不得控制：人物身份）",
                "REF 顺序必须唯一",
            ),
            "missing separator": (
                "REF-A（顺序：1）· 输入/a.png《人物参考》（控制：身份；不得控制：动作）"
                "REF-B（顺序：2）· 输入/b.png《场景参考》（控制：地理；不得控制：人物身份）",
                "完整 REF 语法",
            ),
            "duplicate path": (
                "REF-A（顺序：1）· 输入/a.png《人物参考》（控制：身份；不得控制：动作）；"
                "REF-B（顺序：2）· 输入/a.png《另一人物参考》（控制：造型；不得控制：构图）",
                "REF 路径重复",
            ),
            "conflicting scope": (
                "REF-A（顺序：1）· 输入/a.png《人物参考》（控制：身份；不得控制：身份）",
                "控制与不得控制范围冲突",
            ),
        }
        for label, (declaration, expected) in bad_declarations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                episode = project / "剧集/EP001"
                shutil.copytree(EPISODE, episode)
                (project / "输入").mkdir()
                (project / "输入/a.png").write_bytes(b"a")
                (project / "输入/b.png").write_bytes(b"b")
                for name in ("分镜.md", "视频提示词.md"):
                    path = episode / name
                    document = path.read_text(encoding="utf-8")
                    path.write_text(
                        document.replace(
                            "- 输入参考图：无。", f"- 输入参考图：{declaration}", 1
                        ),
                        encoding="utf-8",
                    )
                video = episode / "视频提示词.md"
                video.write_text(
                    video.read_text(encoding="utf-8").replace(
                        "- 生成方式：文生视频", "- 生成方式：图生视频", 1
                    ),
                    encoding="utf-8",
                )
                errors = creator_markdown_check.validate_episode(episode, project)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_a_lock_surface_counts_only_when_the_prompt_asserts_it(self) -> None:
        """The surface has to name what is in the picture.

        Plain containment answers "do these bytes occur", which both an affix and
        a negative prompt defeat while looking like a pass. Chinese has no word
        boundaries, so the boundary rule applies to ASCII words only -- otherwise
        a Chinese surface could never be satisfied at all.
        """
        carries = creator_markdown_check._carries_surface
        english = "pale blue chunky knit wool sweater"
        chinese = "浅蓝色粗棒针毛线"
        for prompt, surface, expected in (
            (f"a mother knitting a {english} on bamboo needles", english, True),
            (f"the {english}, half finished.", english, True),
            (f"wearing no jewelry and a {english}", english, True),
            (f"..., no text, no logo. A {english} rests on the sofa", english, True),
            # one physical line break inside the rendered paragraph
            ("A pale blue chunky knit wool\nsweater, half finished", english, True),
            (f"a warm red cardigan, no {english}, no text", english, False),
            (f"without a {english}", english, False),
            ("a pristine unchipped white enamel mug", "chipped white enamel mug", False),
            ("the chipped white enamel mug", "chipped white enamel mug", True),
            (
                "a fake-olive-green stand-collar service dress",
                "olive-green stand-collar service dress",
                False,
            ),
            (
                "olive-green stand-collar service dressing-gown",
                "olive-green stand-collar service dress",
                False,
            ),
            (f"妈妈织着{chinese}，孩子在旁边看书", chinese, True),
            (f"她穿着无袖的{chinese}背心", chinese, True),
            (f"画面里是暗红色开衫，不要{chinese}", chinese, False),
            (f"镜头里没有{chinese}", chinese, False),
        ):
            with self.subTest(surface=surface, prompt=prompt[:40]):
                self.assertEqual(carries(prompt, surface), expected)

    def test_a_lock_written_with_any_list_marker_still_enforces(self) -> None:
        """A lock must never become a no-op because of how its bullet is typed.

        Indenting the line under 识别锚点, using a full-width space, or writing
        `*` instead of `-` all render identically in Markdown; if any of them
        stopped the lock from being enforced, the drift it exists to catch would
        come back silently.
        """
        declaration = "- 连续性锁：LOCK-JIANGCHEN-DRESS《江晨橄榄绿立领常服》（镜头：SHOT-EP001-002、SHOT-EP001-003、SHOT-EP001-007；图片提示词项：IMG-JIANGCHEN-SHEET）· 锁面：olive-green stand-collar service dress"
        for label, written in {
            "indented": "  " + declaration,
            "full-width space": declaration.replace("- 连续性锁", "-\u3000连续性锁", 1),
            "star marker": declaration.replace("- ", "* ", 1),
            "plus marker": declaration.replace("- ", "+ ", 1),
        }.items():
            with self.subTest(marker=label), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                episode = project / "剧集/EP001"
                shutil.copytree(EPISODE, episode)
                visual = episode / "视觉设定.md"
                document = visual.read_text(encoding="utf-8")
                self.assertIn(declaration, document)
                visual.write_text(
                    document.replace(declaration, written, 1), encoding="utf-8"
                )
                # Written this way it must still pass on the correct episode...
                self.assertEqual(
                    creator_markdown_check.validate_episode(episode, project), []
                )
                # ...and still catch the drift.
                storyboard = episode / "分镜.md"
                storyboard.write_text(
                    storyboard.read_text(encoding="utf-8").replace(
                        "in buttoned olive-green stand-collar service dress",
                        "in a buttoned navy mandarin-collar tunic",
                        1,
                    ),
                    encoding="utf-8",
                )
                self.assertIn(
                    "LOCK-JIANGCHEN-DRESS: SHOT-EP001-003 冻结关键帧提示词缺少锁面",
                    creator_markdown_check.validate_episode(episode, project),
                )

    def test_a_locked_surface_survives_a_hard_wrapped_prompt(self) -> None:
        """The copyable prompt renders as one paragraph; its source line breaks
        are not part of the text the creator wrote."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            episode = project / "剧集/EP001"
            shutil.copytree(EPISODE, episode)
            images = episode / "图片提示词.md"
            document = images.read_text(encoding="utf-8")
            self.assertIn("Olive-green stand-collar service dress buttoned", document)
            images.write_text(
                document.replace(
                    "Olive-green stand-collar service dress buttoned",
                    "Olive-green stand-collar\n> service dress buttoned",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                creator_markdown_check.validate_episode(episode, project), []
            )

    def test_continuity_lock_scoped_to_the_whole_episode_covers_every_shot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            episode = project / "剧集/EP001"
            shutil.copytree(EPISODE, episode)
            visual = episode / "视觉设定.md"
            visual.write_text(
                visual.read_text(encoding="utf-8")
                + "\n- 连续性锁：LOCK-ABSENT《不存在的锁面》（镜头：全集）"
                "· 锁面：a surface no prompt in this episode contains\n",
                encoding="utf-8",
            )
            errors = creator_markdown_check.validate_episode(episode, project)
            shots = heading_ids(text("分镜.md"), "SHOT-")
            self.assertEqual(
                sorted(
                    error
                    for error in errors
                    if "冻结关键帧提示词缺少锁面" in error
                ),
                sorted(
                    f"LOCK-ABSENT: {shot_id} 冻结关键帧提示词缺少锁面"
                    for shot_id in shots
                ),
            )
            self.assertEqual(
                len([error for error in errors if "可复制提示词缺少锁面" in error]),
                len(shots),
            )

    def test_continuity_locks_are_optional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            episode = project / "剧集/EP001"
            shutil.copytree(EPISODE, episode)
            visual = episode / "视觉设定.md"
            visual.write_text(
                "\n".join(
                    line
                    for line in visual.read_text(encoding="utf-8").splitlines()
                    if "连续性锁" not in line
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                creator_markdown_check.validate_episode(episode, project), []
            )

    def test_frozen_keyframes_are_copyable_markdown_blocks(self) -> None:
        storyboard = text("分镜.md")
        for shot_id in heading_ids(storyboard, "SHOT-"):
            with self.subTest(shot=shot_id):
                self.assertRegex(frozen_prompt(storyboard, shot_id), r"^\s*>\s*\S")

    def test_screenplay_is_accepted_by_the_documented_indexer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = screenplay_index.build_index(
                EPISODE / "剧本.md",
                Path(directory) / "index.jsonl",
                speakers={"江晨", "周薄森", "系统"},
            )
        self.assertEqual(summary["review_status"], "clean")
        self.assertEqual(summary["source_issue_count"], 0)
        self.assertGreater(summary["block_count"], 0)

    def test_image_prompts_are_copyable_and_bounded(self) -> None:
        prompts = text("图片提示词.md")
        ids = heading_ids(prompts, "IMG-")
        self.assertGreater(len(ids), 0)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(headings(prompts, 3), ["可复制提示词"] * len(ids))
        for image_id, body in sections(prompts, "IMG-").items():
            with self.subTest(prompt=image_id):
                prompt_headings = headings(body, 3)
                self.assertEqual(prompt_headings, ["可复制提示词"])
                self.assertRegex(body.split("### ", 1)[1], r"\n>\s*\S")

    def test_every_creator_knowledge_reference_is_reachable(self) -> None:
        for skill_name in ACTIVE_CREATOR_SKILLS:
            skill_root = ROOT / "skills" / skill_name
            references = {
                path.resolve() for path in (skill_root / "references").rglob("*.md")
            }
            reachable = reachable_markdown(skill_root / "SKILL.md", skill_root)
            with self.subTest(skill=skill_name):
                self.assertEqual(
                    references - reachable,
                    set(),
                    "knowledge kept on disk but unreachable from the skill",
                )

    def test_creator_knowledge_inventory_is_preserved(self) -> None:
        for skill_name, expected in EXPECTED_KNOWHOW.items():
            references = ROOT / "skills" / skill_name / "references"
            actual = {path.name for path in references.glob("*.md")}
            with self.subTest(skill=skill_name):
                self.assertEqual(actual, expected)

    def test_creator_rule_catalogs_keep_every_craft_rule(self) -> None:
        expected = {
            "short-drama-write": {*(f"SCR-{number:02d}" for number in range(1, 18))},
            "short-drama-assets": {
                *(f"AST-{number:02d}" for number in range(1, 13)),
                *(f"CON-{number:02d}" for number in range(1, 8)),
            },
            "short-drama-image-prompts": {
                *(f"IMG-{number:02d}" for number in range(1, 14))
            },
            "short-drama-storyboard": {
                *(f"SHT-{number:02d}" for number in range(1, 22)),
                *(f"CON-{number:02d}" for number in range(1, 8)),
            },
            "short-drama-video-prompts": {
                *(f"VID-{number:02d}" for number in range(1, 24)),
                *(f"CON-{number:02d}" for number in range(1, 8)),
            },
            "short-drama-review": {*(f"REV-{number:02d}" for number in range(1, 12))},
        }
        for skill_name, rule_ids in expected.items():
            contract = (
                ROOT / "skills" / skill_name / "references/stage-contract.md"
            ).read_text(encoding="utf-8")
            actual = set(
                re.findall(r"\b(?:SCR|AST|IMG|SHT|VID|CON|REV)-\d{2}\b", contract)
            )
            with self.subTest(skill=skill_name):
                self.assertEqual(actual, rule_ids)


if __name__ == "__main__":
    unittest.main()
