#!/usr/bin/env python3
"""Validate the executable cross-document contract of one creator-first episode."""

from __future__ import annotations

import argparse
import json
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
# 用途 is optional in the pattern on purpose: a declaration written before this
# field existed still parses, so the creator gets "REF 缺少用途" instead of the
# generic syntax error that gives no hint about what to add.
REF_RE = re.compile(
    r"(REF-[A-Z0-9-]+)（顺序：([1-9]\d*)）· "
    r"([^；\n]+?\.(?:png|jpe?g|webp))《([^》\n]+)》"
    r"（(?:用途：([^；）\n]+)；)?控制：([^；）]+)；不得控制：([^）]+)）",
    re.IGNORECASE,
)
# The same slot grammar, but the locator is a document entry instead of a file.
# A creator who generates in an external tool never has the file inside the
# project, and the copyable body only ever needed 顺序 and 用途 -- the path is a
# production input, not a prompt input. PLAN says "this picture does not exist
# here; attach it yourself in this order", which is a different claim from
# 「已有图片」 and from 「待补」.
PLAN_RE = re.compile(
    r"(PLAN-[A-Z0-9-]+)（顺序：([1-9]\d*)）· "
    r"((?:IMG|SHOT)-[A-Z0-9-]+)《([^》\n]+)》"
    r"（(?:用途：([^；）\n]+)；)?控制：([^；）]+)；不得控制：([^）]+)）",
    re.IGNORECASE,
)
# One picture answers one question. 「同一张图承担两个作用时分开说明」 in
# references/reference-roles.md is what makes a slot translatable into a
# provider role, so the vocabulary is closed rather than free prose.
REF_PURPOSES = (
    "身份",
    "造型状态",
    "地理",
    "构图",
    "尺度",
    "效果",
    "起始帧",
    "结束帧",
    "风格",
)
# `EP001-SC001` is the documented shape, but a project that scopes ids by season
# writes `S01-EP001-SC001`. Both are one stable scene id, so the pattern takes
# any hyphenated prefix rather than exactly one segment.
SCENE_ID_PATTERN = r"(?:[A-Za-z0-9]+-)+SC[0-9]+"
SCENE_HEADING_RE = re.compile(
    r"^## (" + SCENE_ID_PATTERN + r")\b", re.MULTILINE
)
SCENE_ID_RE = re.compile(SCENE_ID_PATTERN)
# A scene the storyboard decided not to film. SHT-01 has always allowed the
# omission and always required a reason; without a place to write it the
# omission and an oversight look identical in the finished document.
UNFILMED_LINE_RE = re.compile(
    r"^[ \t\u3000]*[-*+][ \t\u3000]*未拍场次[：:].*$", re.MULTILINE
)
UNFILMED_ENTRY_RE = re.compile(
    r"(" + SCENE_ID_PATTERN + r")（理由：([^）\n]+)）"
)
# Dialogue reaches the model as a quoted run, either inside the H3 `<d>` tag or
# inside quotation marks. Runs shorter than four Han characters are in-frame
# labels and interjections far more often than they are lines, so the check
# stays off them rather than inventing an escape hatch for signage.
SPOKEN_SPAN_RE = re.compile(r"<d>([^<\n]*)</d>|[\"“]([^\"”\n]*)[\"”]|「([^」\n]*)」")
SPOKEN_RUN_RE = re.compile(r"[\u3400-\u9fff]{4,}")
EXPLICIT_TEXT_TO_VIDEO = "无（创作者已明确选择文生视频）"
PENDING_REFERENCE_SUFFIX_RE = re.compile(r"；待补参考图：[^；。\n]+。?$")
VISUAL_CATEGORIES = ("人物", "造型", "地点", "道具")
VISUAL_SETTING_LINE_RE = re.compile(
    r"^#{2,4}[ \t　]*(?:" + "|".join(VISUAL_CATEGORIES) + r")[^\n]*$", re.MULTILINE
)
VISUAL_SETTING_HEADING_RE = re.compile(
    r"^## (" + "|".join(VISUAL_CATEGORIES) + r") · (.+?)[ \t　]*$", re.MULTILINE
)
# Any other `## <word> · <name>` heading, so a 视觉依据 entry that fails to
# resolve can say "the entry is there, the section word is not one of the four"
# instead of blaming the storyboard for a `视觉设定.md` problem.
OTHER_SETTING_HEADING_RE = re.compile(r"^## ([^\n·]+?) · (.+?)[ \t　]*$", re.MULTILINE)
# 画面代称 is the one field the coverage check depends on, so — like a
# continuity lock — anything that looks like it has to parse rather than
# silently drop out.
# Anchored at the start of the line so a sentence that merely mentions the
# field is prose, not a malformed declaration.
SCREEN_NAME_LINE_RE = re.compile(
    r"^[ \t　]*[-*+]?[ \t　]*画面代称[ \t　]*[：:][^\n]*$", re.MULTILINE
)
SCREEN_NAME_RE = re.compile(
    r"^[ \t　]*[-*+][ \t　]*画面代称[：:](.+)$", re.MULTILINE
)
VISUAL_BASIS_PREFIX = "《视觉设定.md》·"
VISUAL_BASIS_ENTRY_RE = re.compile(
    r"(" + "|".join(VISUAL_CATEGORIES) + r")「([^」\n]+)」（控制：([^）\n]+)）"
)
# A subject a keyframe names but does not show — an owner's abandoned bag, a
# name on a screen, a homonym of an entry name. SHT-22 excludes these from
# coverage, so the field needs a way to say so instead of forcing the creator
# to declare an absent subject as present.
OFFSCREEN_PREFIX = "；画外："
OFFSCREEN_ENTRY_RE = re.compile(
    r"(" + "|".join(VISUAL_CATEGORIES) + r")「([^」\n]+)」"
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
    r"|(?:不要|不得|不能|不应|不含|不出现|没有|未|避免|禁止|排除|去掉|移除)"
    r"(?:出现|包含|存在|带|有)?[\s]*"
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


def _contains_plan_token(value: str) -> bool:
    return "plan-" in value.casefold()


def _contains_slot_token(value: str) -> bool:
    return _contains_ref_token(value) or _contains_plan_token(value)


def _is_none(value: str) -> bool:
    return not _contains_slot_token(value) and bool(
        re.fullmatch(r"无(?:（[^）]+）)?", _plain(value))
    )


def _is_explicit_text_to_video(value: str) -> bool:
    return _plain(value) == EXPLICIT_TEXT_TO_VIDEO


def _has_pending_references(value: str) -> bool:
    plain = _plain(value)
    return plain.startswith("无（待补参考图：") or bool(
        PENDING_REFERENCE_SUFFIX_RE.search(value.strip())
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


class EntryIndex(NamedTuple):
    """The document entries a `PLAN-...` locator is allowed to name."""

    images: dict[str, str]
    shots: frozenset


def _slot_matches(value: str) -> list:
    """Every `REF-...` and `PLAN-...` slot in `value`, in written order.

    The two patterns cannot match the same span: a REF locator has to be an
    image path and a PLAN locator has to be an `IMG-`/`SHOT-` id.
    """
    found = []
    for kind, pattern in (("REF", REF_RE), ("PLAN", PLAN_RE)):
        for match in pattern.finditer(value):
            found.append((match.start(), match.end(), kind, match.groups()))
    found.sort(key=lambda item: item[0])
    return found


def _references(
    value: str,
    owner: str,
    project_root: Path,
    errors: list[str],
    entries: Optional[EntryIndex] = None,
) -> frozenset:
    """Validate one 输入参考图/参考 declaration; return the slot kinds it uses."""
    if _is_none(value):
        return frozenset()
    reference_value = PENDING_REFERENCE_SUFFIX_RE.sub("", value.strip())
    matches = _slot_matches(reference_value)
    cursor = 0
    separators_are_valid = True
    for index, (start, end, _kind, _groups) in enumerate(matches):
        if reference_value[cursor:start] != ("" if index == 0 else "；"):
            separators_are_valid = False
        cursor = end
    trailing = reference_value[cursor:]
    # Counted on the same text the slots were matched in, so a gap list that
    # happens to mention a slot id cannot turn into a phantom slot.
    declared = len(
        re.findall(r"\b(?:REF|PLAN)-[A-Z0-9-]+\b", reference_value, re.IGNORECASE)
    )
    kinds = frozenset(item[2] for item in matches)
    if (
        len(matches) != declared
        or not matches
        or not separators_are_valid
        or trailing not in {"", "。"}
    ):
        # A gap list joined with ； reads as a fourth REF slot and would otherwise
        # be reported as broken REF syntax, whose obvious repair is deleting the
        # gap record -- the silent downgrade this contract exists to prevent.
        if "待补参考图" in value and not _has_pending_references(value):
            errors.append(
                f"{owner}: 待补参考图必须写在最后一个 REF 槽位之后，"
                "缺口之间只用、分隔"
            )
        elif _contains_plan_token(value) and not _contains_ref_token(value):
            errors.append(f"{owner}: 输入参考图必须使用完整 PLAN 语法")
        else:
            errors.append(f"{owner}: 输入参考图必须使用完整 REF 语法")
        return kinds
    groups = [item[3] for item in matches]
    slot_kinds = [item[2] for item in matches]
    slots = [group[0] for group in groups]
    orders = [int(group[1]) for group in groups]
    locators = [group[2] for group in groups]
    purposes = [(group[4] or "").strip() for group in groups]
    # A declaration made only of PLAN slots is not a REF declaration, so its
    # diagnostics must not send the creator looking for a REF line.
    noun = "PLAN" if kinds == frozenset({"PLAN"}) else "REF"
    if len(slots) != len(set(slots)):
        errors.append(f"{owner}: {noun} 槽位重复")
    if len(orders) != len(set(orders)) or sorted(orders) != list(
        range(1, len(orders) + 1)
    ):
        errors.append(f"{owner}: {noun} 顺序必须唯一且从 1 连续编号")
    locator_purposes = list(zip(locators, purposes))
    if len(locator_purposes) != len(set(locator_purposes)):
        errors.append(f"{owner}: {noun} 路径与用途完全重复")
    for slot, purpose in zip(slots, purposes):
        if not purpose:
            errors.append(
                f"{owner}: {noun} 缺少用途: {slot}；用途只能是"
                f"{'、'.join(REF_PURPOSES)}其中一个"
            )
        elif purpose not in REF_PURPOSES:
            errors.append(
                f"{owner}: {noun} 用途不在允许集合内: {slot}（{purpose}）；"
                f"只能是{'、'.join(REF_PURPOSES)}"
            )
    for role in ("起始帧", "结束帧"):
        if purposes.count(role) > 1:
            errors.append(f"{owner}: 同一条目只能有一张{role}参考图")
    if "结束帧" in purposes and "起始帧" not in purposes:
        errors.append(f"{owner}: 绑定结束帧参考图时必须同时绑定起始帧")
    for kind, group in zip(slot_kinds, groups):
        slot, raw_locator, label = group[0], group[2], group[3]
        may_control, must_not_control = group[5], group[6]
        if kind == "REF":
            if not _portable_path(raw_locator):
                errors.append(
                    f"{owner}: REF 路径不是安全的项目相对路径: {raw_locator}"
                )
            else:
                reference_path = project_root / raw_locator
                if not _inside(reference_path, project_root):
                    errors.append(f"{owner}: REF 路径越出项目根目录: {raw_locator}")
                elif not reference_path.is_file():
                    errors.append(f"{owner}: REF 文件不存在: {raw_locator}")
        else:
            _plan_locator(raw_locator, label, owner, slot, entries, errors)
        if not re.search(r"[\u4e00-\u9fff]", label):
            errors.append(f"{owner}: {kind} 缺少中文名称: {slot}")
        if not may_control.strip() or not must_not_control.strip():
            errors.append(f"{owner}: {kind} 必须同时声明控制与不得控制: {slot}")
        allowed = {item.strip() for item in re.split(r"[、,，]", may_control)}
        prohibited = {item.strip() for item in re.split(r"[、,，]", must_not_control)}
        if "" in allowed or "" in prohibited or allowed & prohibited:
            errors.append(f"{owner}: {kind} 控制与不得控制范围冲突: {slot}")
    return kinds


def _plan_locator(
    locator: str,
    label: str,
    owner: str,
    slot: str,
    entries: Optional[EntryIndex],
    errors: list[str],
) -> None:
    """A planned picture still has to name an entry that exists today.

    The picture itself is the creator's to supply, but what it depicts is
    already decided by an `IMG-...` board or a `SHOT-...` frozen keyframe. A
    locator that resolves to neither is a reference nobody can act on.
    """
    if entries is None:
        return
    if locator.startswith("IMG-"):
        if locator not in entries.images:
            errors.append(f"{owner}: PLAN 指向不存在的 IMG 条目: {locator}")
        elif label != entries.images[locator]:
            errors.append(f"{owner}: PLAN 中文名称与 IMG 标题不一致: {slot}")
    elif locator.startswith("SHOT-"):
        if locator not in entries.shots:
            errors.append(f"{owner}: PLAN 指向不存在的 SHOT 条目: {locator}")
    else:
        errors.append(
            f"{owner}: PLAN 定位符只能是 IMG-... 或 SHOT-...: {locator}"
        )


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


class VisualEntry(NamedTuple):
    """One `视觉设定.md` entry plus every name a prompt body may call it by."""

    category: str
    name: str
    designators: list[str]

    @property
    def key(self) -> tuple[str, str]:
        return (self.category, self.name)


def _visual_entries(document: str, errors: list[str]) -> list[VisualEntry]:
    """Parse the 人物/造型/地点/道具 entries of one `视觉设定.md`.

    A heading that *looks* like an entry but does not parse would silently drop
    out of the coverage check and then reappear as "视觉依据 names an entry that
    does not exist", so it is rejected here where the cause is visible.
    """
    for line in VISUAL_SETTING_LINE_RE.findall(document):
        if VISUAL_SETTING_HEADING_RE.match(line) is None:
            errors.append(
                "视觉设定.md: 条目标题必须写成 `## <人物|造型|地点|道具> · <名称>`: "
                + _excerpt(line)
            )
    for line in SCREEN_NAME_LINE_RE.findall(document):
        if SCREEN_NAME_RE.match(line) is None:
            errors.append(
                "视觉设定.md: 画面代称必须写成 `- 画面代称：<正文里的拼写>`: "
                + _excerpt(line)
            )
    headings = list(VISUAL_SETTING_HEADING_RE.finditer(document))
    entries: list[VisualEntry] = []
    seen: set[tuple[str, str]] = set()
    for index, heading in enumerate(headings):
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(document)
        )
        category, name = heading.group(1), heading.group(2).strip()
        if not name:
            errors.append(f"视觉设定.md: {category}条目缺少名称")
            continue
        if (category, name) in seen:
            errors.append(f"视觉设定.md: 条目重复: {category}「{name}」")
            continue
        seen.add((category, name))
        declared = SCREEN_NAME_RE.findall(document[heading.end() : end])
        if not declared:
            # No declaration: the entry's own name is the designator, which is
            # what a prompt body written in the project's own language calls it.
            designators = [name]
        elif all(_is_none(_plain(raw)) for raw in declared):
            # `画面代称：无` is the deliberate opt-out for a name too common to
            # match reliably in prose (道具「手机」 against 手机店). It removes the
            # derived name too, otherwise the opt-out would do nothing.
            designators = []
        else:
            designators = []
            for raw in declared:
                value = _plain(raw)
                if _is_none(value):
                    continue
                for item in re.split(r"[、,，]", value):
                    item = item.strip()
                    if item and item not in designators:
                        designators.append(item)
        # Only a *declared* designator earns this diagnostic: the creator chose a
        # spelling the matcher cannot honour and deserves to know. A one-character
        # entry name they never declared is simply not name-matched, the same as
        # any entry in a project whose prompt body is written in another language.
        if declared:
            for designator in designators:
                if len(designator.strip()) < 2:
                    errors.append(
                        f"视觉设定.md: {category}「{name}」的画面代称「{designator}」"
                        "过短，无法在正文中可靠识别；请写成至少两个字符，或写「画面代称：无」"
                    )
        entries.append(VisualEntry(category, name, designators))
    return entries


def _named_entries(
    prompt: str, entries: list[VisualEntry], *, fold_case: bool = False
) -> set[tuple[str, str]]:
    """Which declared entries does this frozen keyframe actually call by name?

    Longer designators win: a keyframe that shows 「江晨手机」 names the prop, and
    the 人物「江晨」 substring inside it is not a second, unrelated claim.
    """
    haystack = re.sub(r"\s+", " ", prompt).strip()
    if fold_case:
        haystack = haystack.casefold()
    owners: dict[str, list[VisualEntry]] = {}
    for entry in entries:
        for designator in entry.designators:
            needle = re.sub(r"\s+", " ", designator).strip()
            if fold_case:
                needle = needle.casefold()
            # A one-character designator matches far too much prose to be
            # evidence; _visual_entries already reports it.
            if len(needle) < 2:
                continue
            owners.setdefault(needle, []).append(entry)
    named: set[tuple[str, str]] = set()
    # Spans of every occurrence of a longer designator, whether or not it was
    # credited. 「戒指盒」 occupies its characters even when the sentence is
    # 「没有戒指」, so 道具「戒指」 must not be read out of it.
    taken: list[tuple[int, int]] = []
    for needle in sorted(owners, key=len, reverse=True):
        occurrences: list[tuple[int, int]] = []
        start = haystack.find(needle)
        while start != -1:
            end = start + len(needle)
            occurrences.append((start, end))
            start = haystack.find(needle, start + 1)
        for start, end in occurrences:
            before = haystack[start - 1] if start else ""
            after = haystack[end] if end < len(haystack) else ""
            glued = (
                (_wordish(needle[:1]) and _wordish(before))
                or (_wordish(needle[-1:]) and _wordish(after))
            )
            # Strictly longer, so two entries sharing one designator are both
            # credited instead of the first in document order winning.
            inside_longer = any(
                taken_start <= start
                and end <= taken_end
                and taken_end - taken_start > end - start
                for taken_start, taken_end in taken
            )
            if (
                not glued
                and not inside_longer
                and NEGATION_RE.search(haystack[:start]) is None
            ):
                named.update(entry.key for entry in owners[needle])
        taken.extend(occurrences)
    return named


class VisualBasis(NamedTuple):
    """One shot's parsed 视觉依据: what it declares, and what it excludes."""

    parsed: bool
    declared: set[tuple[str, str]]
    offscreen: set[tuple[str, str]]

    @property
    def accounted(self) -> set[tuple[str, str]]:
        return self.declared | self.offscreen


def _resolve_entry(
    key: tuple[str, str],
    known: set[tuple[str, str]],
    other_headings: dict[str, str],
    owner: str,
    errors: list[str],
) -> None:
    if key in known:
        return
    category, name = key
    section = other_headings.get(name)
    if section is not None:
        errors.append(
            f"{owner}: 《视觉设定.md》里有「{name}」，但它的分节词是「{section}」；"
            "条目标题必须写成 `## <人物|造型|地点|道具> · <名称>`"
        )
    else:
        errors.append(
            f"{owner}: 视觉依据指向不存在的《视觉设定.md》条目: {category}「{name}」"
        )


def _check_named_coverage(
    prompt: str,
    owner: str,
    where: str,
    basis: "VisualBasis",
    entries: list[VisualEntry],
    errors: list[str],
) -> None:
    """Every entry this text calls by name is either in frame or declared 画外."""
    # Matching is case-sensitive so an ordinary English word never impersonates a
    # character called May or Will. A body that writes the name in another case
    # would otherwise fall out of the check silently, so it is reported here.
    named = _named_entries(prompt, entries)
    folded = _named_entries(prompt, entries, fold_case=True)
    for category, name in sorted(folded - named):
        errors.append(
            f"{owner}: {where}里的名字与画面代称大小写不一致: {category}「{name}」；"
            "同一个名字全集只用一个拼写"
        )
    for category, name in sorted(named):
        if (category, name) not in basis.accounted:
            errors.append(
                f"{owner}: {where}写到{category}「{name}」，视觉依据没有覆盖；"
                "本镜确实看不见时在视觉依据末尾加「；画外："
                f"{category}「{name}」」，正文里这个名字不可靠时在《视觉设定.md》"
                "写「画面代称：无」"
            )


def _visual_basis(
    value: str,
    owner: str,
    entries: list[VisualEntry],
    other_headings: dict[str, str],
    errors: list[str],
) -> VisualBasis:
    """Parse one shot's 视觉依据 and resolve it against `视觉设定.md`."""
    empty = VisualBasis(True, set(), set())
    if _is_none(value):
        return empty
    plain = _plain(value)
    offscreen_raw = ""
    if OFFSCREEN_PREFIX in plain:
        plain, _, offscreen_raw = plain.partition(OFFSCREEN_PREFIX)
    prefixed = plain.startswith(VISUAL_BASIS_PREFIX)
    body = plain[len(VISUAL_BASIS_PREFIX) :] if prefixed else ""
    matches = list(VISUAL_BASIS_ENTRY_RE.finditer(body)) if prefixed else []
    cursor = 0
    separators_are_valid = True
    for index, match in enumerate(matches):
        # Repeating 《视觉设定.md》· before each entry says the same thing and
        # reads naturally; rejecting it would cost a round trip over nothing.
        separator = body[cursor : match.start()]
        allowed = (
            {""}
            if index == 0
            else {"；", "；" + VISUAL_BASIS_PREFIX}
        )
        if separator not in allowed:
            separators_are_valid = False
        cursor = match.end()
    if not prefixed or not matches or not separators_are_valid or body[cursor:]:
        errors.append(
            f"{owner}: 视觉依据必须使用完整语法："
            "《视觉设定.md》·<人物|造型|地点|道具>「<名称>」（控制：<范围>），多项用；连接"
        )
        # Coverage is not reported on top of a parse failure: every entry would
        # be listed as uncovered and bury the one error that matters.
        return VisualBasis(False, set(), set())
    known = {entry.key for entry in entries}
    declared: set[tuple[str, str]] = set()
    for match in matches:
        key = (match.group(1), match.group(2).strip())
        if key in declared:
            errors.append(f"{owner}: 视觉依据条目重复: {key[0]}「{key[1]}」")
        declared.add(key)
        _resolve_entry(key, known, other_headings, owner, errors)
        if not match.group(3).strip():
            errors.append(f"{owner}: 视觉依据缺少控制范围: {key[0]}「{key[1]}」")
    offscreen: set[tuple[str, str]] = set()
    if offscreen_raw:
        offscreen_raw = offscreen_raw.replace(VISUAL_BASIS_PREFIX, "")
        remainder = OFFSCREEN_ENTRY_RE.sub("", offscreen_raw).strip("；、 ")
        offscreen_matches = list(OFFSCREEN_ENTRY_RE.finditer(offscreen_raw))
        if not offscreen_matches or remainder:
            errors.append(
                f"{owner}: 画外清单必须写成 `；画外：<人物|造型|地点|道具>「<名称>」`，多项用；连接"
            )
            return VisualBasis(False, set(), set())
        for match in offscreen_matches:
            key = (match.group(1), match.group(2).strip())
            if key in declared:
                errors.append(
                    f"{owner}: {key[0]}「{key[1]}」同时写进视觉依据和画外清单"
                )
            offscreen.add(key)
            _resolve_entry(key, known, other_headings, owner, errors)
    return VisualBasis(True, declared, offscreen)


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


def _prompt_language(project_root: Path) -> str:
    """The language a copyable prompt body is written in.

    Mirrors the skills' own routing: the project's declared prompt language,
    falling back to `en` when there is no `short-drama.json` -- which is what
    the storyboard skill tells the keyframe author to assume.
    """
    configuration = project_root / "short-drama.json"
    if not configuration.is_file():
        return "en"
    try:
        project = json.loads(configuration.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "en"
    if not isinstance(project, dict):
        return "en"
    authority = project.get("creator_authority")
    profile = (
        authority.get("production_profile") if isinstance(authority, dict) else None
    )
    if isinstance(profile, dict) and profile.get("status") == "accepted":
        choices = profile.get("choices")
        if isinstance(choices, dict):
            declared = choices.get("video_prompt_language")
            if isinstance(declared, str) and declared:
                return declared
    formats = project.get("format")
    declared = formats.get("prompt_language") if isinstance(formats, dict) else None
    return declared if isinstance(declared, str) and declared else "en"


def _check_language_designators(
    project_root: Path, *, entries: list[VisualEntry], errors: list[str]
) -> None:
    """Every character must be nameable in the prompt body's language.

    Without this, omitting `画面代称` is a silent opt-out of the coverage check
    for exactly the projects that need it -- the keyframe body defaults to `en`
    while `视觉设定.md` is Chinese, which is the shape issue #94 reported.

    This deliberately does not wait until some shot's 视觉依据 references the
    entry. Gating on that made the two halves of the contract land in different
    rounds: a document missing 视觉依据 got one error, and only after fixing it
    did the missing 画面代称 appear.
    """
    if _prompt_language(project_root).casefold().startswith("zh"):
        return
    for entry in entries:
        if entry.category != "人物":
            continue
        if entry.designators == [entry.name]:
            errors.append(
                f"视觉设定.md: 人物「{entry.name}」缺少画面代称；"
                "提示词正文不是中文时，写「画面代称：<正文里的拼写>」，"
                "正文从不点名时写「画面代称：无」"
            )


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


def _storyboard_head(storyboard: str) -> str:
    """The part of 分镜.md that belongs to the episode rather than to a shot."""
    match = re.search(r"^## SHOT-", storyboard, re.MULTILINE)
    return storyboard if match is None else storyboard[: match.start()]


def _shot_sources(
    value: str, owner: str, scenes: "frozenset", errors: list[str]
) -> list:
    """Resolve one shot's 来源 against the scene ids that exist in 剧本.md."""
    plain = _plain(value)
    if not plain:
        errors.append(f"{owner}: 缺少来源字段")
        return []
    tokens = [item.strip() for item in re.split(r"[、,，；;]", plain) if item.strip()]
    resolved = []
    for token in tokens:
        # 《镜头手艺》 asks for the scene id plus a short quote of the source
        # line, so only the head of each entry is the id; the rest is the quote.
        leading = SCENE_ID_RE.match(token)
        if leading is None:
            errors.append(
                f"{owner}: 来源必须以《剧本.md》的场景 ID 开头: {_excerpt(token, 24)}"
            )
        elif leading.group(0) not in scenes:
            errors.append(f"{owner}: 来源场景不在《剧本.md》中: {leading.group(0)}")
        else:
            resolved.append(leading.group(0))
    return resolved


def _unfilmed_scenes(
    storyboard: str, scenes: "frozenset", errors: list[str]
) -> dict:
    """Scenes 分镜.md declares it deliberately does not film, with reasons."""
    head = _storyboard_head(storyboard)
    declared: dict = {}
    # The line belongs to the episode, not to a shot. Placed inside a SHOT
    # section it would be read as that shot's field and quietly do nothing,
    # which looks exactly like forgetting to write it.
    if len(UNFILMED_LINE_RE.findall(storyboard)) != len(
        UNFILMED_LINE_RE.findall(head)
    ):
        errors.append("分镜.md: 未拍场次要写在第一个 SHOT 之前的正文开头")
    for line in UNFILMED_LINE_RE.findall(head):
        value = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        entries = UNFILMED_ENTRY_RE.findall(value)
        remainder = UNFILMED_ENTRY_RE.sub("", value).strip("；;。 ")
        if not entries or remainder:
            errors.append(
                "分镜.md: 未拍场次必须写成 <场景 ID>（理由：……），多项用；分隔"
            )
            continue
        for scene_id, reason in entries:
            if not reason.strip():
                errors.append(f"分镜.md: 未拍场次缺少理由: {scene_id}")
            elif scene_id not in scenes:
                errors.append(f"分镜.md: 未拍场次不在《剧本.md》中: {scene_id}")
            else:
                declared[scene_id] = reason.strip()
    return declared


def _spoken_key(value: str) -> str:
    """Drop punctuation and spacing so quoting choices do not decide identity."""
    return re.sub(r"[^\u3400-\u9fff0-9A-Za-z]", "", value)


def _quoted_speech(prompt: str) -> list:
    runs: list = []
    for match in SPOKEN_SPAN_RE.finditer(prompt):
        span = next((group for group in match.groups() if group), "")
        runs.extend(SPOKEN_RUN_RE.findall(span))
    return runs


def _check_spoken_lines(
    prompt: str, owner: str, sources: "tuple", errors: list[str]
) -> None:
    """Quoted Chinese in a copyable body has to come from an upstream document.

    The video stage already owes the screenplay verbatim dialogue. Checking it
    here is what stops a prompt from writing the line the shot wishes the
    character had said. 视觉设定.md counts as a source because a location or
    prop entry can legitimately carry the exact text on a sign.
    """
    for run in _quoted_speech(prompt):
        key = _spoken_key(run)
        if key and not any(key in source for source in sources):
            errors.append(
                f"{owner}: 可复制提示词的引文不在《剧本.md》或《视觉设定.md》中: "
                f"{_excerpt(run, 24)}"
            )


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
    screenplay = (episode / "剧本.md").read_text(encoding="utf-8")
    scene_headings = SCENE_HEADING_RE.findall(screenplay)
    scenes = frozenset(scene_headings)
    if not scenes:
        errors.append(
            "剧本.md: 没有可解析的场景标题；场景标题写成 ## <场景 ID> 内|外 · 地点 · 时间"
        )
    if len(scene_headings) != len(scenes):
        errors.append("剧本.md: 场景 ID 重复")
    spoken_sources = (_spoken_key(screenplay), _spoken_key(visual))
    unfilmed = _unfilmed_scenes(storyboard, scenes, errors)
    claimed_scenes: set = set()
    locks = _continuity_locks(visual, errors)
    visual_entries = _visual_entries(visual, errors)
    other_headings = {
        match.group(2).strip(): match.group(1).strip()
        for match in OTHER_SETTING_HEADING_RE.finditer(visual)
        if match.group(1).strip() not in VISUAL_CATEGORIES
    }
    shots = _sections(storyboard, "SHOT")
    image_pairs = re.findall(r"^## (IMG-[A-Z0-9-]+) · (.+)$", images, re.MULTILINE)
    image_headings = dict(image_pairs)
    # A planned picture names the board or keyframe that already decides what it
    # depicts, so both indexes have to exist before any declaration is read.
    entries = EntryIndex(images=image_headings, shots=frozenset(shots))
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
            _references(
                reference_value, match.group(1), project_root, errors, entries
            )
        else:
            errors.append(
                f"{match.group(1)}: 参考必须声明无外部参考或使用完整 REF 语法"
            )
        image_prompt = _copyable_prompt(body)
        image_prompts[match.group(1)] = image_prompt
        if image_prompt is None:
            errors.append(f"{match.group(1)}: 缺少唯一且非空的可复制提示词")

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
        claimed_scenes.update(
            _shot_sources(fields.get("来源", ""), shot_id, scenes, errors)
        )
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
        _references(shot_input, shot_id, project_root, errors, entries)

        basis_value = fields.get("视觉依据", "")
        if not basis_value:
            errors.append(f"{shot_id}: 缺少视觉依据字段")
            basis = VisualBasis(False, set(), set())
        else:
            basis = _visual_basis(
                basis_value, shot_id, visual_entries, other_headings, errors
            )
        # The keyframe is the only place the frame's contents exist as text, so a
        # shot without one would make the coverage check below vacuous.
        keyframe = _copyable_prompt(shot_body, heading=r"冻结关键帧提示词")
        if keyframe is None:
            errors.append(f"{shot_id}: 缺少唯一且非空的冻结关键帧提示词")
        elif basis.parsed:
            _check_named_coverage(
                keyframe, shot_id, "冻结关键帧提示词", basis, visual_entries, errors
            )

        motion = motion_by_shot.get(shot_id)
        if not motion:
            continue
        motion_id, motion_body, copyable_prompt = motion
        motion_fields = _fields(motion_body, owner=motion_id, errors=errors)
        if copyable_prompt is not None:
            _check_spoken_lines(
                copyable_prompt, motion_id, spoken_sources, errors
            )
        motion_input = motion_fields.get("输入参考图", "")
        _references(motion_input, motion_id, project_root, errors, entries)
        if _plain(motion_input) != _plain(shot_input):
            errors.append(f"{motion_id}: 输入参考图与 {shot_id} 不一致")

        if _has_pending_references(shot_input):
            errors.append(f"{motion_id}: 仍有待补参考图，不能生成最终视频提示词")

        # A `PLAN-...` slot is not a file, but the prompt is still written for
        # image-conditioned generation: the creator attaches those pictures at
        # generation time. What decides 生成方式 is whether the shot sends
        # pictures at all, not whether this suite can read them.
        sends_pictures = not _is_none(shot_input)
        expected_mode = "图生视频" if sends_pictures else "文生视频"
        if _plain(motion_fields.get("生成方式", "")) != expected_mode:
            errors.append(f"{motion_id}: 生成方式应为{expected_mode}")
        if not sends_pictures:
            if not _is_explicit_text_to_video(shot_input):
                errors.append(
                    f"{motion_id}: 无真实输入参考图时不能静默降级为文生视频；"
                    "请先绑定已有图片、列出待补图片，或记录创作者已明确选择文生视频"
                )
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
            # In text-to-video the anchor carries the appearance the keyframe
            # would otherwise have carried, so it is the same claim about the
            # same frame and answers to the same visual basis. The rest of the
            # motion body is not checked: it may legitimately name an offscreen
            # speaker, which SHT-22 excludes.
            if anchor and anchor != "无" and basis.parsed:
                _check_named_coverage(
                    anchor, motion_id, "静态视觉锚点", basis, visual_entries, errors
                )

    for scene_id in scene_headings:
        if scene_id in claimed_scenes or scene_id in unfilmed:
            continue
        errors.append(
            f"分镜.md: 没有镜头承载场景 {scene_id}；"
            "拍它就写进某一镜的来源，不拍就写进未拍场次并给出理由"
        )
    for scene_id in sorted(set(unfilmed) & claimed_scenes):
        errors.append(f"分镜.md: 场景 {scene_id} 既被镜头承载又记为未拍")

    _check_language_designators(
        project_root, entries=visual_entries, errors=errors
    )
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
