#!/usr/bin/env python3
"""Validate the executable cross-document contract of one creator-first episode."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath
from collections.abc import Iterable
from typing import NamedTuple, Optional


MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "creator_markdown_check.py requires Python {}.{}, running {}.{}".format(
            *MINIMUM_PYTHON, sys.version_info.major, sys.version_info.minor
        )
    )


REQUIRED_DOCUMENTS = (
    "剧本.md",
    "视觉设定.md",
    "图片提示词.md",
    "分镜.md",
    "视频提示词.md",
)
SECTION_RE = re.compile(r"^## ((?:SHOT|MOTION)-[A-Z0-9-]+)\b", re.MULTILINE)
IMG_RE = re.compile(r"\b(IMG-[A-Z0-9-]+)《([^》]+)》（控制：([^）]+)）")
REF_RE = re.compile(
    r"(REF-[A-Z0-9-]+)（顺序：([1-9]\d*)）· "
    r"([^；\n]+?\.(?:png|jpe?g|webp))《([^》\n]+)》"
    r"（控制：([^；）]+)；不得控制：([^）]+)）",
    re.IGNORECASE,
)
# A declared lock must never become a no-op. Anything that *looks* like a lock
# line -- any list marker, any leading whitespace -- is captured here and then
# has to parse, so a creator who indents the bullet under 识别锚点 gets an error
# instead of silent non-enforcement.
LOCK_LINE_RE = re.compile(r"^[ \t\u3000]*[-*+][ \t\u3000]*连续性锁[：:].*$", re.MULTILINE)
LOCK_RE = re.compile(
    r"^[ \t\u3000]*[-*+][ \t\u3000]*连续性锁：(LOCK-[A-Z0-9-]+)《([^》\n]+)》"
    r"（镜头：([^；）\n]+)"
    r"(?:；图片提示词项：([^；）\n]+))?）"
    r"· 锁面：(.+)$"
)
# The surface has to name what is in the picture. A match glued to a negation
# ("no pale blue sweater") describes what must be absent, so it cannot be the
# evidence that the fact is present.
# Chinese runs without spaces, so the CJK markers cannot require a preceding
# boundary the way the English ones do. A bare 无 is deliberately not a marker:
# 无袖毛衣 describes the garment rather than excluding it.
NEGATION_RE = re.compile(
    r"(?:"
    r"(?:^|[\s,;:(\[/—-])(?:no|not|non|never|without|avoid|excludes?|excluding|"
    r"free\s+of|--?no)(?:\s+(?:a|an|the|any|some))?[\s-]*"
    r"|(?:不要|不得|不能|不出现|没有|避免|禁止|排除)[\s]*"
    r")$",
    re.IGNORECASE,
)


def _sections(document: str, kind: str) -> dict[str, str]:
    matches = [
        match
        for match in SECTION_RE.finditer(document)
        if match.group(1).startswith(f"{kind}-")
    ]
    return {
        match.group(1): document[
            match.start() : matches[index + 1].start()
            if index + 1 < len(matches)
            else None
        ]
        for index, match in enumerate(matches)
    }


def _fields(section: str, *, owner: str, errors: list[str]) -> dict[str, str]:
    pairs = re.findall(r"^- ([^：\n]+)：(.+)$", section, re.MULTILINE)
    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            errors.append(f"{owner}: 字段重复: {key}")
        fields[key] = value
    return fields


def _plain(value: str) -> str:
    return value.strip().rstrip("。")


def _contains_ref_token(value: str) -> bool:
    return "ref-" in value.casefold()


def _is_none(value: str) -> bool:
    return not _contains_ref_token(value) and bool(
        re.fullmatch(r"无(?:（[^）]+）)?", _plain(value))
    )


def _is_no_external_reference(value: str) -> bool:
    return not _contains_ref_token(value) and bool(
        re.fullmatch(r"无(?:外部参考)?(?:；[^\n]*)?。?", value.strip())
    )


def _copyable_prompt(
    section: str, heading: str = r"可复制(?:通用)?提示词"
) -> Optional[str]:
    markers = list(re.finditer(rf"^### {heading}\s*$", section, re.MULTILINE))
    if len(markers) != 1:
        return None
    body = section[markers[0].end() :]
    following = re.search(r"^###\s+|^##\s+", body, re.MULTILINE)
    if following is not None:
        body = body[: following.start()]
    lines = [line for line in body.splitlines() if line.strip()]
    if not lines or any(not line.startswith(">") for line in lines):
        return None
    prompt = "\n".join(line[1:].lstrip() for line in lines).strip()
    return prompt or None


def _portable_path(value: str) -> bool:
    if not value or "\\" in value or re.match(r"^[A-Za-z]:", value):
        return False
    parts = value.split("/")
    return not PurePosixPath(value).is_absolute() and not any(
        part in {"", ".", ".."} for part in parts
    )


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved == root or root in resolved.parents


def _references(value: str, owner: str, project_root: Path, errors: list[str]) -> None:
    if _is_none(value):
        return
    matches = list(REF_RE.finditer(value))
    cursor = 0
    separators_are_valid = True
    for index, match in enumerate(matches):
        if value[cursor : match.start()] != ("" if index == 0 else "；"):
            separators_are_valid = False
        cursor = match.end()
    trailing = value[cursor:]
    if (
        len(matches) != len(re.findall(r"\bREF-[A-Z0-9-]+\b", value))
        or not matches
        or not separators_are_valid
        or trailing not in {"", "。"}
    ):
        errors.append(f"{owner}: 输入参考图必须使用完整 REF 语法")
        return
    refs = [(match.group(1), int(match.group(2)), match.group(3)) for match in matches]
    slots = [item[0] for item in refs]
    orders = [item[1] for item in refs]
    paths = [item[2] for item in refs]
    if len(slots) != len(set(slots)):
        errors.append(f"{owner}: REF 槽位重复")
    if len(orders) != len(set(orders)) or sorted(orders) != list(
        range(1, len(orders) + 1)
    ):
        errors.append(f"{owner}: REF 顺序必须唯一且从 1 连续编号")
    if len(paths) != len(set(paths)):
        errors.append(f"{owner}: REF 路径重复")
    for match, (_, _, raw_path) in zip(matches, refs):
        label, may_control, must_not_control = (
            match.group(4),
            match.group(5),
            match.group(6),
        )
        if not _portable_path(raw_path):
            errors.append(f"{owner}: REF 路径不是安全的项目相对路径: {raw_path}")
        else:
            reference_path = project_root / raw_path
            if not _inside(reference_path, project_root):
                errors.append(f"{owner}: REF 路径越出项目根目录: {raw_path}")
            elif not reference_path.is_file():
                errors.append(f"{owner}: REF 文件不存在: {raw_path}")
        if not re.search(r"[\u4e00-\u9fff]", label):
            errors.append(f"{owner}: REF 缺少中文名称: {match.group(1)}")
        if not may_control.strip() or not must_not_control.strip():
            errors.append(f"{owner}: REF 必须同时声明控制与不得控制: {match.group(1)}")
        allowed = {item.strip() for item in re.split(r"[、,，]", may_control)}
        prohibited = {item.strip() for item in re.split(r"[、,，]", must_not_control)}
        if "" in allowed or "" in prohibited or allowed & prohibited:
            errors.append(f"{owner}: REF 控制与不得控制范围冲突: {match.group(1)}")


def _excerpt(value: str, limit: int = 60) -> str:
    """A short, single-line quote of an offending line for a diagnostic."""
    collapsed = re.sub(r"\s+", " ", value).strip()
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _unique(values: "Iterable[str]") -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalized(value: str) -> str:
    """Fold case and collapse whitespace so a hard-wrapped prompt still matches.

    A copyable prompt is one rendered paragraph; the line breaks the repo's
    Markdown style puts in it are not part of the text a creator wrote.
    """
    return re.sub(r"\s+", " ", value).strip().casefold()


def _wordish(character: str) -> bool:
    return bool(character) and character.isascii() and (
        character.isalnum() or character == "-"
    )


def _carries_surface(prompt: str, surface: str) -> bool:
    """Is this lock surface actually asserted by this prompt?

    Plain containment is not enough. `chipped white enamel mug` must not be
    satisfied by `unchipped ... mug`, and `no pale blue sweater` in a negative
    tail asserts the opposite of the fact the lock exists to hold.
    """
    haystack = _normalized(prompt)
    needle = _normalized(surface)
    if not needle:
        return False
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before = haystack[start - 1] if start else ""
        after = haystack[end] if end < len(haystack) else ""
        # Only ASCII words have boundaries to glue across. Chinese is written
        # without spaces, so treating an adjacent CJK character as "glued" would
        # make a Chinese surface impossible to satisfy.
        glued = (
            (_wordish(needle[:1]) and _wordish(before))
            or (_wordish(needle[-1:]) and _wordish(after))
        )
        if not glued and NEGATION_RE.search(haystack[:start]) is None:
            return True
        start = haystack.find(needle, start + 1)
    return False


class ContinuityLock(NamedTuple):
    """One declared cross-shot lock: the exact surface and where it applies."""

    lock_id: str
    surface: str
    shots: list[str]
    images: list[str]


def _continuity_locks(document: str, errors: list[str]) -> list[ContinuityLock]:
    """Parse the declared continuity locks of one 视觉设定.md."""
    locks: list[ContinuityLock] = []
    seen: set[str] = set()
    for line in LOCK_LINE_RE.findall(document):
        match = LOCK_RE.match(line)
        if match is None:
            errors.append(
                "视觉设定.md: 连续性锁必须使用完整语法: " + _excerpt(line)
            )
            continue
        lock_id, label, scope, image_scope, surface = match.groups()
        if lock_id in seen:
            errors.append(f"{lock_id}: 连续性锁 ID 重复")
            continue
        seen.add(lock_id)
        if not re.search(r"[\u3400-\u9fff]", label):
            errors.append(f"{lock_id}: 连续性锁缺少中文名称")
        surface = _plain(surface)
        if not surface:
            errors.append(f"{lock_id}: 连续性锁缺少锁面")
            continue
        shots = _unique(
            item.strip() for item in re.split(r"[、,，]", _plain(scope)) if item.strip()
        )
        if not shots:
            errors.append(f"{lock_id}: 连续性锁缺少镜头范围")
            continue
        if "全集" in shots and len(shots) != 1:
            errors.append(f"{lock_id}: 连续性锁的镜头范围不能把全集与具体镜头混写")
            continue
        images: list[str] = []
        if image_scope is not None and not _is_none(image_scope):
            images = _unique(
                item.strip()
                for item in re.split(r"[、,，]", _plain(image_scope))
                if item.strip()
            )
            if any(not item.startswith("IMG-") for item in images):
                errors.append(f"{lock_id}: 连续性锁的图片提示词项必须使用 IMG-  ID")
                continue
        locks.append(ContinuityLock(lock_id, surface, shots, images))
    return locks


def _check_continuity_locks(
    locks: list[ContinuityLock],
    *,
    shots: dict[str, str],
    motion_by_shot: dict[str, tuple[str, str, Optional[str]]],
    image_prompts: dict[str, Optional[str]],
    errors: list[str],
) -> None:
    """Require every declared lock surface to be present where it was scoped."""
    for lock in locks:
        lock_id = lock.lock_id
        surface = lock.surface
        targets = sorted(shots) if lock.shots == ["全集"] else lock.shots
        for shot_id in targets:
            if shot_id not in shots:
                errors.append(f"{lock_id}: 连续性锁指向不存在的镜头: {shot_id}")
                continue
            keyframe = _copyable_prompt(shots[shot_id], heading=r"冻结关键帧提示词")
            if keyframe is None:
                errors.append(f"{lock_id}: {shot_id} 缺少可读的冻结关键帧提示词")
            elif not _carries_surface(keyframe, surface):
                errors.append(f"{lock_id}: {shot_id} 冻结关键帧提示词缺少锁面")
            motion = motion_by_shot.get(shot_id)
            if motion is None:
                continue
            motion_id, _, copyable_prompt = motion
            if copyable_prompt is not None and not _carries_surface(
                copyable_prompt, surface
            ):
                errors.append(f"{lock_id}: {motion_id} 可复制提示词缺少锁面")
        for image_id in lock.images:
            if image_id not in image_prompts:
                errors.append(f"{lock_id}: 连续性锁指向不存在的 IMG 条目: {image_id}")
                continue
            image_prompt = image_prompts[image_id]
            if image_prompt is not None and not _carries_surface(image_prompt, surface):
                errors.append(f"{lock_id}: {image_id} 可复制提示词缺少锁面")


def validate_episode(episode: Path, project_root: Optional[Path] = None) -> list[str]:
    """Return all deterministic contract errors for ``episode``."""
    episode = episode.resolve()
    project_root = (project_root or episode.parent.parent).resolve()
    errors: list[str] = []
    missing = [name for name in REQUIRED_DOCUMENTS if not (episode / name).is_file()]
    if missing:
        return [f"缺少创作文档: {', '.join(missing)}"]

    images = (episode / "图片提示词.md").read_text(encoding="utf-8")
    storyboard = (episode / "分镜.md").read_text(encoding="utf-8")
    video = (episode / "视频提示词.md").read_text(encoding="utf-8")
    visual = (episode / "视觉设定.md").read_text(encoding="utf-8")
    locks = _continuity_locks(visual, errors)
    image_pairs = re.findall(r"^## (IMG-[A-Z0-9-]+) · (.+)$", images, re.MULTILINE)
    image_headings = dict(image_pairs)
    all_image_headings = re.findall(r"^## (IMG-[A-Z0-9-]+)\b", images, re.MULTILINE)
    if len(image_pairs) != len(all_image_headings):
        errors.append("图片提示词.md: IMG 标题必须包含中文名称")
    if len(image_pairs) != len(image_headings):
        errors.append("图片提示词.md: IMG 标题 ID 重复")
    for image_id, label in image_pairs:
        if not re.search(r"[\u3400-\u9fff]", label):
            errors.append(f"{image_id}: IMG 标题缺少中文名称")
    image_matches = list(re.finditer(r"^## (IMG-[A-Z0-9-]+)\b", images, re.MULTILINE))
    image_prompts: dict[str, Optional[str]] = {}
    for index, match in enumerate(image_matches):
        body = images[
            match.start() : image_matches[index + 1].start()
            if index + 1 < len(image_matches)
            else None
        ]
        reference_value = _fields(body, owner=match.group(1), errors=errors).get(
            "参考", ""
        )
        if not reference_value:
            errors.append(f"{match.group(1)}: 缺少参考字段")
        elif _is_no_external_reference(reference_value):
            pass
        elif "REF-" in reference_value:
            _references(reference_value, match.group(1), project_root, errors)
        else:
            errors.append(
                f"{match.group(1)}: 参考必须声明无外部参考或使用完整 REF 语法"
            )
        image_prompt = _copyable_prompt(body)
        image_prompts[match.group(1)] = image_prompt
        if image_prompt is None:
            errors.append(f"{match.group(1)}: 缺少唯一且非空的可复制提示词")

    shots = _sections(storyboard, "SHOT")
    motions = _sections(video, "MOTION")
    shot_ids = re.findall(r"^## (SHOT-[A-Z0-9-]+)\b", storyboard, re.MULTILINE)
    motion_ids = re.findall(r"^## (MOTION-[A-Z0-9-]+)\b", video, re.MULTILINE)
    if len(shot_ids) != len(set(shot_ids)):
        errors.append("分镜.md: SHOT 标题 ID 重复")
    if len(motion_ids) != len(set(motion_ids)):
        errors.append("视频提示词.md: MOTION 标题 ID 重复")
    named_shots = re.findall(
        r"^## (SHOT-[A-Z0-9-]+) · ([^\n]+)$", storyboard, re.MULTILINE
    )
    named_motions = re.findall(
        r"^## (MOTION-[A-Z0-9-]+) · ([^\n]+)$", video, re.MULTILINE
    )
    if len(named_shots) != len(shot_ids):
        errors.append("分镜.md: SHOT 标题必须包含中文名称")
    if len(named_motions) != len(motion_ids):
        errors.append("视频提示词.md: MOTION 标题必须包含中文名称")
    for shot_id, label in named_shots:
        if not re.search(r"[\u3400-\u9fff]", label):
            errors.append(f"{shot_id}: SHOT 标题缺少中文名称")
    for motion_id, label in named_motions:
        if not re.search(r"[\u3400-\u9fff]", label):
            errors.append(f"{motion_id}: MOTION 标题缺少中文名称")
    if not shots:
        errors.append("分镜.md: 没有 SHOT 条目")
    if not motions:
        errors.append("视频提示词.md: 没有 MOTION 条目")

    motion_by_shot: dict[str, tuple[str, str, Optional[str]]] = {}
    for motion_id, body in motions.items():
        fields = _fields(body, owner=motion_id, errors=errors)
        shot_id = _plain(fields.get("分镜", ""))
        copyable_prompt = _copyable_prompt(body)
        if not shot_id:
            errors.append(f"{motion_id}: 缺少分镜字段")
        elif shot_id in motion_by_shot:
            errors.append(f"{motion_id}: 分镜 {shot_id} 被多个 MOTION 引用")
        else:
            motion_by_shot[shot_id] = (motion_id, body, copyable_prompt)
        if motion_id.removeprefix("MOTION-") != shot_id.removeprefix("SHOT-"):
            errors.append(f"{motion_id}: ID 必须与分镜 {shot_id} 一一对应")
        if copyable_prompt is None:
            errors.append(f"{motion_id}: 缺少唯一且非空的可复制提示词")

    if set(motion_by_shot) != set(shots):
        errors.append("分镜.md/视频提示词.md: SHOT 与 MOTION 未一一对应")

    for shot_id, shot_body in shots.items():
        fields = _fields(shot_body, owner=shot_id, errors=errors)
        image_value = fields.get("图片提示词项", "")
        if not image_value:
            errors.append(f"{shot_id}: 缺少图片提示词项字段")
        image_refs = IMG_RE.findall(image_value)
        image_remainder = IMG_RE.sub("", image_value).strip("；。 ")
        if not _is_none(image_value) and (
            len(image_refs) != len(re.findall(r"\bIMG-[A-Z0-9-]+\b", image_value))
            or image_remainder
        ):
            errors.append(f"{shot_id}: 图片提示词项语法不完整")
        for image_id, label, _ in image_refs:
            if image_id not in image_headings:
                errors.append(f"{shot_id}: IMG 标题不存在: {image_id}")
            elif label != image_headings[image_id]:
                errors.append(f"{shot_id}: IMG 中文名称与标题不一致: {image_id}")

        shot_input = fields.get("输入参考图", "")
        if not shot_input:
            errors.append(f"{shot_id}: 缺少输入参考图字段")
        _references(shot_input, shot_id, project_root, errors)

        motion = motion_by_shot.get(shot_id)
        if not motion:
            continue
        motion_id, motion_body, copyable_prompt = motion
        motion_fields = _fields(motion_body, owner=motion_id, errors=errors)
        motion_input = motion_fields.get("输入参考图", "")
        _references(motion_input, motion_id, project_root, errors)
        if _plain(motion_input) != _plain(shot_input):
            errors.append(f"{motion_id}: 输入参考图与 {shot_id} 不一致")

        has_real_image = not _is_none(shot_input)
        expected_mode = "图生视频" if has_real_image else "文生视频"
        if _plain(motion_fields.get("生成方式", "")) != expected_mode:
            errors.append(f"{motion_id}: 生成方式应为{expected_mode}")
        if not has_real_image:
            anchor = _plain(motion_fields.get("静态视觉锚点", ""))
            if not anchor or anchor == "无":
                errors.append(f"{motion_id}: 文生视频缺少静态视觉锚点")
            if (
                copyable_prompt is not None
                and anchor
                and anchor != "无"
                and anchor not in copyable_prompt
            ):
                errors.append(f"{motion_id}: 可复制提示词没有包含静态视觉锚点")

    _check_continuity_locks(
        locks,
        shots=shots,
        motion_by_shot=motion_by_shot,
        image_prompts=image_prompts,
        errors=errors,
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path, help="包含五份 Markdown 的剧集目录")
    parser.add_argument("--project-root", type=Path, help="用于解析 REF 项目相对路径")
    args = parser.parse_args()
    # 诊断与剧集路径都是中文。stdout 重定向时 Windows 用 ANSI 代码页，默认的
    # strict 处理器会在打印这一步抛错；stderr 早就是 backslashreplace，这里让
    # stdout 用同一个处理器：能编码就照常显示中文，不能编码才退成转义。
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")
    errors = validate_episode(args.episode, args.project_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.episode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
