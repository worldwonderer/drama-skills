#!/usr/bin/env python3
"""Build a byte-accurate, derived index for a short-drama Markdown screenplay.

The indexer recognizes only the screenplay grammar documented by the write
skill.  It never rewrites the screenplay and never guesses through a
split/merge revision.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, NamedTuple


# Creators run these scripts on whatever interpreter their machine provides, so
# an unsupported version must say so instead of failing inside an import.
MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "short-drama needs Python {}.{} or newer; this interpreter is {}.{}".format(
            *MINIMUM_PYTHON, sys.version_info.major, sys.version_info.minor
        )
    )

# ---------------------------------------------------------------------------
# REFERENCE RESOLVER -- reference implementation.
#
# Each skill checker carries its own copy of this block. The suite has no shared
# library on purpose: a skill must stay runnable after copying only its own
# directory, so duplicating these few lines across skills is the correct shape.
# Copy the block verbatim; do not import it.
# ---------------------------------------------------------------------------

SOURCES_RECORD_TYPE = "sources"
SOURCES_SCHEMA_VERSION = "1.0.0"
SOURCES_KEY = "sources"


class ResolvedRef(NamedTuple):
    """An upstream reference with its snapshot resolved, whichever form it used."""

    owner: str
    artifact: str
    record_id: str | None
    field: str | None
    authority: str | None


class RefFinding(NamedTuple):
    """A structural defect in a reference object."""

    code: str
    location: str
    detail: str


def load_sources(document: Any) -> dict[str, dict[str, Any]]:
    """Return the ``sources`` declaration of a parsed file, or ``{}`` if absent.

    Accepts a parsed ``.json`` document (a dict) or the parsed record list of a
    ``.jsonl`` file, whose declaration lives on the first record.
    """
    if isinstance(document, list):
        document = document[0] if document else None
    if not isinstance(document, dict):
        return {}
    declared = document.get("sources")
    if not isinstance(declared, dict):
        return {}
    return {key: value for key, value in declared.items() if isinstance(value, dict)}


def resolve_ref(
    ref: Any, sources: dict[str, dict[str, Any]], location: str
) -> tuple[ResolvedRef | None, RefFinding | None]:
    """Resolve a reference object written in either the compact or expanded form."""
    if not isinstance(ref, dict):
        return None, RefFinding("REF_IS_NOT_AN_OBJECT", location, f"got {type(ref).__name__}")
    src = ref.get("src")
    if isinstance(src, str):
        entry = sources.get(src)
        if entry is None:
            return None, RefFinding(
                "REF_SRC_IS_NOT_DECLARED", location, f"src {src!r} has no sources entry"
            )
        owner, artifact = entry.get("owner"), entry.get("artifact")
        if not (isinstance(owner, str) and isinstance(artifact, str)):
            return None, RefFinding(
                "SOURCE_ENTRY_IS_INCOMPLETE",
                location,
                f"sources[{src!r}] needs owner/artifact",
            )
    elif all(isinstance(ref.get(key), str) for key in ("owner", "artifact")):
        owner, artifact = ref["owner"], ref["artifact"]
    else:
        return None, RefFinding(
            "REF_HAS_NO_UPSTREAM_BINDING", location, "needs src, or owner+artifact"
        )
    optional = {
        key: ref[key]
        for key in ("record_id", "field", "authority")
        if isinstance(ref.get(key), str)
    }
    return (
        ResolvedRef(
        owner,
        artifact,
            optional.get("record_id"),
            optional.get("field"),
            optional.get("authority"),
        ),
        None,
    )


# ---------------------------------------------------------------------------
# END REFERENCE RESOLVER
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"

# An index declares its upstream screenplay once, on the meta record, and every
# reference in the file names it by key. A rebuild against a previous index adds
# a second key only when the previous screenplay is a different snapshot.
SCREENPLAY_SOURCE_KEY = "screenplay"
PREVIOUS_SCREENPLAY_SOURCE_KEY = "previous-screenplay"

SUPPORTED_TAGS = ("VO", "OS", "SFX", "画面文字", "连续性", "转场")
KIND_CODES = {
    "scene_heading": "H",
    "action": "A",
    "dialogue": "D",
    "production_tag": "P",
    "comment": "C",
}

# Three digits through EP999, then unpadded — the same single spelling per
# episode number the lifecycle tool enforces on `episodes/<EP>/`. A fixed three
# digits would have made EP1000 a legal directory but an illegal heading.
_EPISODE = r"EP(?:[0-9]{3}|[1-9][0-9]{3,})"
EPISODE_HEADING_RE = re.compile(rf"^# (?P<episode>{_EPISODE})(?: .+)?$")
SCENE_HEADING_RE = re.compile(
    rf"^## (?P<scene>{_EPISODE}-SC[0-9]{{3}}) "
    r"(?P<space>内外|内|外) · (?P<location>\S(?:.*\S)?) · "
    r"(?P<time>\S(?:.*\S)?)$"
)
DIALOGUE_RE = re.compile(
    r"^(?P<speaker>[^\s：（）:\[\]#]{1,40})"
    r"(?:（(?P<cue>[^（）\r\n]+)）)?：(?P<text>\S[\s\S]*)$"
)
ASCII_DIALOGUE_RE = re.compile(
    r"^[^\s：（）:\[\]#]{1,40}(?:（[^（）\r\n]+）)?:\s*\S"
)
TAG_RE = re.compile(r"^\[(?P<tag>VO|OS|SFX|画面文字|连续性|转场)\]\s*(?P<body>\S[\s\S]*)$")
ANY_TAG_RE = re.compile(r"^\[(?P<tag>[^\]\r\n]+)\]")
# Production dialects write tags with full-width brackets (【特写】/【闪回】).
# Without this the paragraph matches nothing and is emitted as a plain action
# block with no issue, so the owner never learns the source needs
# normalization. The block is still emitted (see below) because block IDs are
# the durable cross-artifact reference; only the diagnosis is added.
# The lookahead keeps dialect dialogue on the dialogue path. The dialect writes
# the performance cue with ASCII parens and a full-width colon
# (【角色名】(表演括注)：台词, production-format-dialect.md), so both paren
# styles must be exempted here.
FULLWIDTH_TAG_RE = re.compile(
    r"^【(?P<tag>[^】\r\n]+)】"
    r"(?!\s*(?:（[^（）\r\n]*）|\([^()\r\n]*\))?\s*[：:])"
)
MALFORMED_TAG_RE = re.compile(r"^\[(?:VO|OS|SFX|画面文字|连续性|转场)(?:\s|：|:)")
VOICE_TAG_BODY_RE = re.compile(r"^(?P<speaker>[^\s：（）:\[\]#]{1,40})：(?P<text>\S[\s\S]*)$")
BLOCK_ID_RE = re.compile(r"^BLK-(?P<scope>.+)-(?P<code>[HADPC])(?P<number>\d+)$")


def _newline_style(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    bare_lf = data.count(b"\n") - crlf
    bare_cr = data.count(b"\r") - crlf
    styles = sum(bool(count) for count in (crlf, bare_lf, bare_cr))
    if styles > 1:
        return "mixed"
    if crlf:
        return "crlf"
    if bare_lf:
        return "lf"
    if bare_cr:
        return "cr"
    return "none"


def _line_table(data: bytes) -> list[dict[str, Any]]:
    raw_lines = data.splitlines(keepends=True)
    if not raw_lines and data == b"":
        return []
    if raw_lines and sum(map(len, raw_lines)) < len(data):
        raw_lines.append(data[sum(map(len, raw_lines)) :])
    lines: list[dict[str, Any]] = []
    offset = 0
    for number, raw in enumerate(raw_lines, 1):
        if raw.endswith(b"\r\n"):
            content = raw[:-2]
        elif raw.endswith((b"\n", b"\r")):
            content = raw[:-1]
        else:
            content = raw
        lines.append(
            {
                "number": number,
                "start": offset,
                "content_end": offset + len(content),
                "end": offset + len(raw),
                "raw": raw,
                "content": content,
                "text": content.decode("utf-8"),
            }
        )
        offset += len(raw)
    return lines


def _span(
    data: bytes,
    lines: list[dict[str, Any]],
    start_index: int,
    end_index: int,
) -> dict[str, Any]:
    start = lines[start_index]["start"]
    end = lines[end_index]["content_end"]
    raw = data[start:end]
    return {
        "byte_start": start,
        "byte_end": end,
        "line_start": lines[start_index]["number"],
        "line_end": lines[end_index]["number"],
        "_raw": raw,
        "_text": raw.decode("utf-8"),
    }


def _issue(
    span: dict[str, Any],
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        **{key: span[key] for key in ("byte_start", "byte_end", "line_start", "line_end")},
        "issue_code": code,
        "severity": "review_required",
        "message": message,
    }


def _looks_like_markdown(text: str) -> bool:
    stripped = text.lstrip()
    return bool(
        stripped.startswith(("#", "```", "~~~", ">", "- ", "* ", "+ "))
        or re.match(r"\d+[.)]\s", stripped)
        or re.fullmatch(r"[-*_]{3,}", stripped)
    )


def _parse_screenplay(
    data: bytes,
    speaker_names: frozenset[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Decode once up front so invalid UTF-8 fails before any output is written.
    data.decode("utf-8")
    lines = _line_table(data)
    blocks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    episode_id: str | None = None
    scene_id: str | None = None
    index = 0

    def append_block(kind: str, span: dict[str, Any], **fields: Any) -> None:
        blocks.append(
            {
                "kind": kind,
                "episode_id": episode_id,
                "scene_id": scene_id,
                **span,
                **fields,
                "_order": len(blocks),
            }
        )

    while index < len(lines):
        text = lines[index]["text"]
        stripped = text.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("<!--"):
            end_index = index
            while end_index < len(lines) and "-->" not in lines[end_index]["text"]:
                end_index += 1
            if end_index >= len(lines):
                span = _span(data, lines, index, len(lines) - 1)
                issues.append(_issue(span, "malformed_comment", "Markdown 注释缺少闭合的 -->。"))
                break
            span = _span(data, lines, index, end_index)
            if not span["_text"].strip().endswith("-->"):
                issues.append(
                    _issue(span, "mixed_comment_content", "注释闭合符后还有内容，不能机械分块。")
                )
            else:
                append_block("comment", span, production=False)
            index = end_index + 1
            continue

        if stripped.startswith("##"):
            span = _span(data, lines, index, index)
            match = SCENE_HEADING_RE.fullmatch(stripped)
            if not match:
                scene_id = None
                issues.append(
                    _issue(
                        span,
                        "invalid_scene_heading",
                        "场景标题必须为 ## <EP001-SC001> <内|外|内外> · <地点> · <时间/天气>。",
                    )
                )
            else:
                scene_id = match.group("scene")
                heading_episode = scene_id.split("-", 1)[0]
                if episode_id and episode_id != heading_episode:
                    issues.append(
                        _issue(span, "episode_scene_mismatch", "场景 ID 与当前集标题的集 ID 不一致。")
                    )
                    scene_id = None
                else:
                    episode_id = heading_episode
                    append_block(
                        "scene_heading",
                        span,
                        space=match.group("space"),
                        location=match.group("location"),
                        time_weather=match.group("time"),
                    )
            index += 1
            continue

        if stripped.startswith("#"):
            span = _span(data, lines, index, index)
            match = EPISODE_HEADING_RE.fullmatch(stripped)
            if match:
                episode_id = match.group("episode")
                scene_id = None
            else:
                scene_id = None
                issues.append(_issue(span, "invalid_episode_heading", "集标题必须以 # EP001 开头（EP 加三位数字，超过 EP999 后不补零）。"))
            index += 1
            continue

        end_index = index
        while end_index + 1 < len(lines):
            next_stripped = lines[end_index + 1]["text"].strip()
            if not next_stripped or next_stripped.startswith(("#", "<!--")):
                break
            end_index += 1
        span = _span(data, lines, index, end_index)
        paragraph = span["_text"].strip()

        if scene_id is None:
            issues.append(
                _issue(span, "content_outside_scene", "动作、对白和生产标签只能位于合法场景标题之后。")
            )
            index = end_index + 1
            continue

        paragraph_lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if len(paragraph_lines) > 1:
            later_dialogues = [
                match
                for line in paragraph_lines[1:]
                if (match := DIALOGUE_RE.fullmatch(line)) is not None
            ]
            if any(match.group("speaker") in speaker_names for match in later_dialogues) or any(
                ASCII_DIALOGUE_RE.match(line)
                or TAG_RE.fullmatch(line)
                or ANY_TAG_RE.match(line)
                or MALFORMED_TAG_RE.match(line)
                for line in paragraph_lines[1:]
            ):
                issues.append(
                    _issue(
                        span,
                        "missing_block_separator",
                        "动作、对白与生产标签之间需要空行，不能把不同语法块合成动作段落。",
                    )
                )
                index = end_index + 1
                continue
            if later_dialogues:
                issues.append(
                    _issue(
                        span,
                        "ambiguous_dialogue_or_action",
                        "冒号前缀不在本次说话者清单中；由 write owner 判断是动作、画面文字还是对白。",
                    )
                )
                index = end_index + 1
                continue

        tag_match = TAG_RE.fullmatch(paragraph)
        if tag_match:
            tag = tag_match.group("tag")
            body = tag_match.group("body")
            fields: dict[str, Any] = {"tag": tag, "production": True}
            if tag in {"VO", "OS"}:
                voice_match = VOICE_TAG_BODY_RE.fullmatch(body)
                if not voice_match:
                    issues.append(
                        _issue(span, "invalid_voice_tag_syntax", f"[{tag}] 必须明确说话者并使用全角冒号。")
                    )
                    index = end_index + 1
                    continue
                fields["speaker"] = voice_match.group("speaker")
            append_block("production_tag", span, **fields)
            index = end_index + 1
            continue

        any_tag = ANY_TAG_RE.match(paragraph)
        if any_tag:
            issues.append(
                _issue(
                    span,
                    "unsupported_production_tag",
                    f"不支持生产标签 [{any_tag.group('tag')}]；仅支持 {', '.join(SUPPORTED_TAGS)}。",
                )
            )
            index = end_index + 1
            continue
        if MALFORMED_TAG_RE.match(paragraph):
            issues.append(_issue(span, "malformed_production_tag", "生产标签缺少闭合的 ]。"))
            index = end_index + 1
            continue

        dialogue_match = DIALOGUE_RE.fullmatch(paragraph)
        if dialogue_match:
            if dialogue_match.group("speaker") not in speaker_names:
                issues.append(
                    _issue(
                        span,
                        "ambiguous_dialogue_or_action",
                        "冒号前缀不在本次说话者清单中；由 write owner 判断是动作、画面文字还是对白。",
                    )
                )
                index = end_index + 1
                continue
            append_block(
                "dialogue",
                span,
                speaker=dialogue_match.group("speaker"),
                performance_cue=dialogue_match.group("cue"),
                production=False,
            )
            index = end_index + 1
            continue
        if ASCII_DIALOGUE_RE.match(paragraph):
            issues.append(
                _issue(span, "invalid_dialogue_syntax", "对白说话者后必须使用全角冒号 ：。")
            )
            index = end_index + 1
            continue
        if _looks_like_markdown(paragraph):
            issues.append(
                _issue(span, "unsupported_markdown", "该 Markdown 结构不属于受支持的剧本块。")
            )
            index = end_index + 1
            continue

        # Diagnosed last, so only paragraphs that would already be plain action
        # are reported. Anything a dialect writes with a colon (【音效：…】,
        # 【角色名】(括注)：台词) has been claimed by the dialogue paths above
        # and keeps main's disposition, and the block is still emitted here, so
        # no block ID moves in either direction.
        fullwidth_tag = FULLWIDTH_TAG_RE.match(paragraph)
        if fullwidth_tag:
            issues.append(
                _issue(
                    span,
                    "unsupported_production_tag",
                    f"【{fullwidth_tag.group('tag')}】 是生产方言写法，不是本套件的生产标签；"
                    f"受支持的标签是半角 [ ] 加 {', '.join(SUPPORTED_TAGS)}。"
                    "先经规范化入口判断它应当成为动作、画面文字还是转场，再发布。",
                )
            )

        append_block("action", span, production=False)
        index = end_index + 1

    return blocks, issues


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}:{line_number}: invalid JSONL: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number}: JSONL record must be an object")
        records.append(value)
    return records


def _load_previous(
    records: list[dict[str, Any]],
    previous_source_path: Path,
) -> list[dict[str, Any]]:
    # Without the previous screenplay's bytes a block's identity is its content
    # digest and nothing more: exact-content reuse still holds, split/merge
    # detection does not. A rewritten body block then loses its ID instead of
    # exception by design — they are reused on a stable scene_id, so an edited
    # heading keeps its ID; the scene is the same scene, and the blocks under it
    # are what carry the content.
    source_data = previous_source_path.read_bytes()
    sources = load_sources(records)
    body = [record for record in records if record.get("record_type") != SOURCES_RECORD_TYPE]
    if not body or body[0].get("record_type") != "screenplay_index_meta":
        raise ValueError("previous index is missing screenplay_index_meta")
    # The previous index must name the screenplay it was built from. It no
    # longer carries that screenplay's bytes, so a resume can confirm identity
    # but not that the file is unchanged; a screenplay edited between runs is
    # caught by re-parsing below, not by a stored digest.
    resolved, _defect = resolve_ref(body[0].get("source_ref"), sources, "previous_index")
    if resolved is None:
        raise ValueError("previous index does not name its screenplay")
    previous_speakers = previous_index_speakers(records)
    parsed_blocks, _ = _parse_screenplay(source_data, previous_speakers)
    parsed_by_span = {
        (block["byte_start"], block["byte_end"], block["kind"]): block
        for block in parsed_blocks
    }
    previous: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in records:
        if record.get("record_type") != "block":
            continue
        block_id = record.get("block_id")
        if not isinstance(block_id, str) or not BLOCK_ID_RE.fullmatch(block_id):
            raise ValueError("previous index contains an invalid block_id")
        if block_id in seen_ids:
            raise ValueError("previous index contains duplicate block_id values")
        seen_ids.add(block_id)
        byte_start = record.get("byte_start")
        byte_end = record.get("byte_end")
        kind = record.get("kind")
        if (
            not isinstance(byte_start, int)
            or not isinstance(byte_end, int)
            or isinstance(byte_start, bool)
            or isinstance(byte_end, bool)
            or not 0 <= byte_start < byte_end <= len(source_data)
            or kind not in KIND_CODES
        ):
            raise ValueError(f"previous block {block_id} does not resolve against previous source")
        parsed = parsed_by_span.get((byte_start, byte_end, kind))
        if parsed is None:
            raise ValueError(f"previous block {block_id} does not resolve against previous source")
        previous.append({**record, "_text": parsed["_text"], "_order": parsed["_order"]})
    return previous


def previous_index_speakers(records: list[dict[str, Any]]) -> frozenset[str]:
    """Recover the dialogue speaker roster an earlier index already resolved."""
    return frozenset(
        speaker.strip()
        for record in records
        if record.get("record_type") == "block"
        and record.get("kind") == "dialogue"
        and isinstance((speaker := record.get("speaker")), str)
        and speaker.strip()
    )


def _fingerprint(block: dict[str, Any]) -> tuple[Any, ...]:
    # Content identity comes from the prose, re-read from the previous screenplay.
    # No digest is stored in the index, so a rebuild without those bytes has
    # nothing to compare and renumbers by position.
    return (
        block["kind"],
        block.get("episode_id"),
        block.get("scene_id"),
        _normalized(block["_text"]),
    )


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _mark_revision_mappings(
    current: list[dict[str, Any]],
    previous: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matched_current: set[int] = set()
    matched_previous: set[int] = set()

    old_headings: dict[tuple[str, str], list[int]] = defaultdict(list)
    new_headings: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, block in enumerate(previous):
        if block["kind"] == "scene_heading" and block.get("scene_id"):
            old_headings[(block["kind"], block["scene_id"])].append(index)
    for index, block in enumerate(current):
        if block["kind"] == "scene_heading" and block.get("scene_id"):
            new_headings[(block["kind"], block["scene_id"])].append(index)
    for key, new_indices in new_headings.items():
        old_indices = old_headings.get(key, [])
        if len(old_indices) == len(new_indices) == 1:
            old_index, new_index = old_indices[0], new_indices[0]
            current[new_index]["block_id"] = previous[old_index]["block_id"]
            current[new_index]["mapping"] = {
                "status": "reused",
                "reason": "stable_scene_id",
                "previous_block_ids": [previous[old_index]["block_id"]],
            }
            matched_current.add(new_index)
            matched_previous.add(old_index)

    old_groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    new_groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for index, block in enumerate(previous):
        if index not in matched_previous:
            old_groups[_fingerprint(block)].append(index)
    for index, block in enumerate(current):
        if index not in matched_current:
            new_groups[_fingerprint(block)].append(index)
    duplicate_requests: list[dict[str, Any]] = []
    for fingerprint, new_indices in new_groups.items():
        old_indices = old_groups.get(fingerprint, [])
        pairs: list[tuple[int, int]] = []
        if len(old_indices) == len(new_indices) == 1:
            pairs = [(old_indices[0], new_indices[0])]
        elif len(old_indices) == len(new_indices) and old_indices:
            # Reuse repeated identical blocks only when their exact spans did not move.
            # Once a duplicate group moves, occurrence identity is ambiguous.
            by_start = {previous[old]["byte_start"]: old for old in old_indices}
            if all(current[new]["byte_start"] in by_start for new in new_indices):
                pairs = [(by_start[current[new]["byte_start"]], new) for new in new_indices]
        for old_index, new_index in pairs:
            current[new_index]["block_id"] = previous[old_index]["block_id"]
            current[new_index]["mapping"] = {
                "status": "reused",
                "reason": "exact_content",
                "previous_block_ids": [previous[old_index]["block_id"]],
            }
            matched_current.add(new_index)
            matched_previous.add(old_index)

        if old_indices and new_indices and not pairs:
            duplicate_requests.append(
                {
                    "reason": "duplicate_exact_match",
                    "previous_indices": old_indices,
                    "current_indices": new_indices,
                }
            )
            matched_current.update(new_indices)
            matched_previous.update(old_indices)

    return duplicate_requests + _find_split_merge_requests(
        current, previous, matched_current, matched_previous
    )


def _find_split_merge_requests(
    current: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    matched_current: set[int],
    matched_previous: set[int],
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    claimed_new: set[int] = set()
    claimed_old: set[int] = set()

    def compatible(blocks: list[dict[str, Any]]) -> bool:
        return bool(blocks) and all(
            block["kind"] == blocks[0]["kind"]
            and block.get("scene_id") == blocks[0].get("scene_id")
            and block["kind"] in {"action", "dialogue", "production_tag", "comment"}
            for block in blocks
        )

    for old_index, old in enumerate(previous):
        if old_index in matched_previous:
            continue
        old_text = _normalized(old["_text"])
        for start in range(len(current)):
            new_indices: list[int] = []
            new_parts: list[str] = []
            for item in range(start, len(current)):
                if item in matched_current or item in claimed_new:
                    break
                block = current[item]
                if not compatible([old, block]):
                    break
                new_indices.append(item)
                new_parts.append(_normalized(block["_text"]))
                combined = "".join(new_parts)
                if not old_text.startswith(combined):
                    break
                if len(new_indices) < 2:
                    continue
                if combined != old_text:
                    continue
                requests.append(
                    {
                        "reason": "split",
                        "previous_indices": [old_index],
                        "current_indices": new_indices,
                    }
                )
                claimed_old.add(old_index)
                claimed_new.update(new_indices)
                break
            if old_index in claimed_old:
                break

    for new_index, new in enumerate(current):
        if new_index in matched_current or new_index in claimed_new:
            continue
        new_text = _normalized(new["_text"])
        for start in range(len(previous)):
            old_indices: list[int] = []
            old_parts: list[str] = []
            for item in range(start, len(previous)):
                if item in matched_previous or item in claimed_old:
                    break
                block = previous[item]
                if not compatible([new, block]):
                    break
                old_indices.append(item)
                old_parts.append(_normalized(block["_text"]))
                combined = "".join(old_parts)
                if not new_text.startswith(combined):
                    break
                if len(old_indices) < 2:
                    continue
                if combined != new_text:
                    continue
                requests.append(
                    {
                        "reason": "merge",
                        "previous_indices": old_indices,
                        "current_indices": [new_index],
                    }
                )
                claimed_old.update(old_indices)
                claimed_new.add(new_index)
                break
            if new_index in claimed_new:
                break
    return requests


def _scope(block: dict[str, Any]) -> str:
    if block.get("scene_id"):
        return str(block["scene_id"])
    if block.get("episode_id"):
        return f"{block['episode_id']}-GLOBAL"
    return "GLOBAL"


def _assign_new_ids(
    current: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    high_water: dict[str, int] | None = None,
) -> dict[str, int]:
    """Give every unmatched block an ID no earlier revision has used.

    Retirement has to outlive the revision that retired an ID. Seeding the
    counter from the previous index alone makes it one generation deep: once
    every block of one kind in a scene is gone, the counter restarts at zero and
    the next new block is handed a retired ID. A downstream artifact still
    citing it resolves cleanly and silently means something else -- which is
    exactly what resolving cleanly is supposed to rule out.

    So the highest number ever issued per (scene, kind) rides along in the index
    meta and is carried forward here.
    """

    reserved = {str(block["block_id"]) for block in previous}
    counters: dict[tuple[str, str], int] = defaultdict(int)
    for name, number in (high_water or {}).items():
        scope, _, code = name.rpartition("|")
        if scope and isinstance(number, int):
            counters[(scope, code)] = max(counters[(scope, code)], number)
    for block_id in reserved:
        match = BLOCK_ID_RE.fullmatch(block_id)
        assert match
        key = (match.group("scope"), match.group("code"))
        counters[key] = max(counters[key], int(match.group("number")))
    for block in current:
        if "block_id" in block:
            continue
        scope = _scope(block)
        code = KIND_CODES[block["kind"]]
        key = (scope, code)
        while True:
            counters[key] += 1
            candidate = f"BLK-{scope}-{code}{counters[key]:02d}"
            if candidate not in reserved:
                break
        reserved.add(candidate)
        block["block_id"] = candidate
        block["mapping"] = {
            "status": "new",
            "reason": "no_previous_exact_match",
            "previous_block_ids": [],
        }
    # Every ID this revision kept also counts, so a block reused from the
    # previous index cannot be reissued after it later disappears.
    for block in current:
        match = BLOCK_ID_RE.fullmatch(str(block.get("block_id", "")))
        if match:
            key = (match.group("scope"), match.group("code"))
            counters[key] = max(counters[key], int(match.group("number")))
    return {f"{scope}|{code}": number for (scope, code), number in counters.items()}


def _public_block(block: dict[str, Any], source_ref: dict[str, str]) -> dict[str, Any]:
    omitted = {"_raw", "_text", "_order"}
    return {
        "record_type": "block",
        "schema_version": SCHEMA_VERSION,
        **{key: value for key, value in block.items() if key not in omitted},
        "source_ref": source_ref,
    }


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _portable_source_ref(source: Path, source_ref: str | None) -> str:
    value = source_ref or source.name
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or "://" in value:
        raise ValueError("source_ref must be a portable project-relative path")
    return candidate.as_posix()


def build_index(
    source_path: str | Path,
    output_path: str | Path,
    *,
    previous_index_path: str | Path | None = None,
    previous_source_path: str | Path | None = None,
    source_ref: str | None = None,
    authority: str = "accepted",
    speakers: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
    no_previous: bool = False,
) -> dict[str, Any]:
    """Parse ``source_path`` and atomically publish its derived JSONL index."""
    source = Path(source_path)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ValueError("output path must not be the screenplay source path")
    if (previous_index_path is None) != (previous_source_path is None):
        raise ValueError("--previous-index and --previous-source must be supplied together")
    # Rebuilding over an existing index without naming a previous version
    # renumbers every block from scratch. Surviving text keeps whatever ID its
    # position now yields, so a rewritten block can reclaim the retired ID and
    # every downstream reference to it silently resolves to different content --
    # the exact failure the content-stable path exists to prevent. Refuse, and
    # name the file, rather than produce that index quietly.
    if previous_index_path is None and not no_previous and output.exists():
        raise ValueError(
            f"{output} already holds an index; pass --previous-index {output} "
            f"--previous-source <the screenplay before this revision> to keep block "
            f"IDs stable by content, or --no-previous to renumber from scratch"
        )
    if authority not in {"accepted", "candidate"}:
        raise ValueError("authority must be accepted or candidate")
    raw_speakers = tuple(speakers or ())
    if any(not isinstance(speaker, str) or not speaker.strip() for speaker in raw_speakers):
        raise ValueError("speaker names must be non-empty text")
    speaker_names = frozenset(speaker.strip() for speaker in raw_speakers)
    if any(
        len(speaker) > 40
        or DIALOGUE_RE.fullmatch(f"{speaker}：占位") is None
        for speaker in speaker_names
    ):
        raise ValueError("speaker names must match the screenplay dialogue label grammar")

    source_data = source.read_bytes()
    declared_sources: dict[str, dict[str, str]] = {
        SCREENPLAY_SOURCE_KEY: {
            "owner": "short-drama-write",
            "artifact": _portable_source_ref(source, source_ref),
        }
    }
    source_artifact_ref: dict[str, str] = {"src": SCREENPLAY_SOURCE_KEY}
    if authority == "candidate":
        source_artifact_ref["authority"] = "candidate"
    previous_records: list[dict[str, Any]] = []
    if previous_index_path is not None:
        previous_records = _read_jsonl(Path(previous_index_path))
        # Union, not replacement: a reviser who adds one new speaker passes only
        # that name, and dropping the rest would turn every other dialogue line
        # back into an ambiguous block and change its ID.
        speaker_names |= previous_index_speakers(previous_records)
    current, source_issues = _parse_screenplay(source_data, speaker_names)
    previous: list[dict[str, Any]] = []
    raw_requests: list[dict[str, Any]] = []
    previous_source_ref: dict[str, str] | None = None
    # Carried from the previous index so a retired ID stays retired even after
    # every block that was using it has gone.
    previous_high_water: dict[str, int] = {}
    for record in previous_records:
        if record.get("record_type") == "screenplay_index_meta":
            declared = record.get("block_id_high_water")
            if isinstance(declared, dict):
                previous_high_water = {
                    str(name): number
                    for name, number in declared.items()
                    if isinstance(number, int) and not isinstance(number, bool)
                }
            break
    if previous_index_path is not None and previous_source_path is not None:
        previous_source = Path(previous_source_path)
        previous_snapshot = {
            "owner": "short-drama-write",
            "artifact": _portable_source_ref(previous_source, source_ref),
        }
        previous_key = SCREENPLAY_SOURCE_KEY
        if previous_snapshot != declared_sources[SCREENPLAY_SOURCE_KEY]:
            previous_key = PREVIOUS_SCREENPLAY_SOURCE_KEY
            declared_sources[previous_key] = previous_snapshot
        previous_source_ref = {"src": previous_key}
        if authority == "candidate":
            previous_source_ref["authority"] = "candidate"
        previous = _load_previous(previous_records, previous_source)
        raw_requests = _mark_revision_mappings(current, previous)
    block_id_high_water = _assign_new_ids(
        current, previous, previous_high_water
    )

    review_requests: list[dict[str, Any]] = []
    for number, request in enumerate(raw_requests, 1):
        old_ids = [previous[index]["block_id"] for index in request["previous_indices"]]
        new_ids = [current[index]["block_id"] for index in request["current_indices"]]
        for index in request["current_indices"]:
            current[index]["mapping"] = {
                "status": "unresolved",
                "reason": request["reason"],
                "previous_block_ids": old_ids,
            }
        review_requests.append(
            {
                "record_type": "mapping_review_request",
                "schema_version": SCHEMA_VERSION,
                "request_id": f"MAP-REVIEW-{number:03d}",
                "reason": request["reason"],
                "status": "unresolved",
                "previous_block_ids": old_ids,
                "current_block_ids": new_ids,
                "instruction": "创作者或 write owner 必须显式选择重映射；索引器不会猜测。",
            }
        )

    issue_records = [
        {
            "record_type": "source_issue",
            "schema_version": SCHEMA_VERSION,
            "issue_id": f"SOURCE-ISSUE-{number:03d}",
            **issue,
            "source_ref": source_artifact_ref,
        }
        for number, issue in enumerate(source_issues, 1)
    ]
    review_status = "review_required" if issue_records or review_requests else "clean"
    meta = {
        "record_type": "screenplay_index_meta",
        "schema_version": SCHEMA_VERSION,
        "source_ref": source_artifact_ref,
        "source_byte_length": len(source_data),
        "newline_style": _newline_style(source_data),
        "previous_source_ref": previous_source_ref,
        "block_count": len(current),
        "source_issue_count": len(issue_records),
        "mapping_review_count": len(review_requests),
        "review_status": review_status,
        # The highest block number ever issued per scene and kind. Carried
        # forward so a retired ID stays retired after the blocks that were
        # using it are gone.
        "block_id_high_water": dict(sorted(block_id_high_water.items())),
        SOURCES_KEY: declared_sources,
    }
    records = [meta]
    records.extend(_public_block(block, source_artifact_ref) for block in current)
    records.extend(issue_records)
    records.extend(review_requests)
    _atomic_jsonl(output, records)
    return {
        "output": str(output),
        "block_count": len(current),
        "source_issue_count": len(issue_records),
        "mapping_review_count": len(review_requests),
        "review_status": review_status,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a byte-accurate derived screenplay-index.jsonl without editing screenplay.md."
    )
    parser.add_argument("source", type=Path, help="UTF-8 Markdown screenplay source")
    parser.add_argument("--output", type=Path, help="output JSONL path")
    parser.add_argument("--previous-index", type=Path)
    parser.add_argument("--previous-source", type=Path)
    parser.add_argument(
        "--no-previous",
        action="store_true",
        help="renumber block IDs from scratch, abandoning the IDs downstream artifacts cite",
    )
    parser.add_argument(
        "--source-ref",
        help="portable project-relative source reference; defaults to the source basename",
    )
    parser.add_argument(
        "--authority",
        choices=("accepted", "candidate"),
        default="accepted",
        help="mark source refs candidate for a provisional normalization preview",
    )
    parser.add_argument(
        "--speaker",
        action="append",
        default=[],
        help="repeat for each agent-resolved dialogue speaker label",
    )
    parser.add_argument(
        "--fail-on-review",
        action="store_true",
        help="return exit code 2 after writing when source or mapping review is required",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output or args.source.with_name("screenplay-index.jsonl")
    try:
        summary = build_index(
            args.source,
            output,
            previous_index_path=args.previous_index,
            previous_source_path=args.previous_source,
            source_ref=args.source_ref,
            authority=args.authority,
            speakers=args.speaker,
            no_previous=args.no_previous,
        )
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    if args.fail_on_review and summary["review_status"] != "clean":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
