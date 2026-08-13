#!/usr/bin/env python3
"""Build, verify and sample one stable chapter index for a long source novel.

Every stage of source analysis slices the same file. If each stage ran its own
regular expression the slices would disagree, and a chapter analysed under one
boundary would be aggregated under another. This script owns the only slicing
truth: it detects chapter boundaries once, binds each span to a content hash,
and refuses to stay quiet about numbering it cannot explain.

It performs no editorial judgement. Which chapters matter, what they mean and
how they adapt are decisions for the skill workflow, not for this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

# Creators run these scripts on whatever interpreter their machine provides, so
# an unsupported version must say so instead of failing inside an import.
MINIMUM_PYTHON = (3, 10)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "short-drama needs Python {}.{} or newer; this interpreter is {}.{}".format(
            *MINIMUM_PYTHON, sys.version_info.major, sys.version_info.minor
        )
    )

SCHEMA_VERSION = "1.0.0-draft"

# Chapter numbers appear as Arabic digits or Chinese numerals. 千 and 两 are
# included because serialized fiction routinely passes 1000 chapters, and
# 第两百章 is as common as 第二百章 in that range.
CHINESE_NUMERALS = "零一二三四五六七八九十百千两"
# Books number their chapters with 章, 回 or 节 — but a book that uses 章 for
# chapters may also use 节 for subsections inside them. Matching every unit at
# once turns those subsections into chapters, so the unit is captured here and
# a single dominant unit is chosen below.
CHAPTER_UNITS = ("章", "回", "节")
CHAPTER_RE = re.compile(
    r"^[ \t　]*第\s*([0-9]+|[" + CHINESE_NUMERALS + r"]+)\s*([章回节])"
    r"[ \t　]*(.*?)[ \t　]*$"
)
CHINESE_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
# A chapter heading is a short standalone line. A body paragraph that happens to
# open with 第三章 — a character recalling what happened back then — otherwise
# becomes a chapter boundary, and the index silently gains a chapter whose span
# starts mid-scene. Generous enough for the long titles serialized fiction uses.
MAX_HEADING_LINE_WIDTH = 50


def chinese_to_int(text: str) -> int | None:
    """Convert 一 / 十五 / 两百零三 / 一千零一 to an int, or None if malformed.

    Returning None rather than raising matters: a heading whose number cannot be
    read must be reported as an unreadable heading, not crash the whole index
    and leave the creator with no boundary table at all.
    """

    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    section = 0
    digit_seen = False
    for character in text:
        if character in CHINESE_DIGITS:
            section = CHINESE_DIGITS[character]
            digit_seen = True
        elif character in CHINESE_UNITS:
            # 十五 means 15, so a leading 十 carries an implicit one.
            total += (section or 1) * CHINESE_UNITS[character]
            section = 0
            digit_seen = True
        else:
            return None
    if not digit_seen:
        return None
    return total + section


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _visible_width(text: str) -> int:
    """Count characters as a creator counts them, not as bytes."""

    return sum(1 for character in text if not unicodedata.combining(character))


def find_heading_lines(lines: list[str]) -> tuple[list[dict[str, Any]], int]:
    headings: list[dict[str, Any]] = []
    long_lines = 0
    for offset, line in enumerate(lines):
        candidate = line.removeprefix("\ufeff") if offset == 0 else line
        match = CHAPTER_RE.match(candidate)
        if match is None:
            continue
        if _visible_width(candidate.strip()) > MAX_HEADING_LINE_WIDTH:
            long_lines += 1
            continue
        headings.append(
            {
                "line_index": offset,
                "raw": candidate.strip(),
                "number": chinese_to_int(match.group(1)),
                "unit": match.group(2),
                "title": match.group(3).strip(),
            }
        )
    return headings, long_lines


def select_chapter_unit(
    headings: list[dict[str, Any]]
) -> tuple[str | None, dict[str, int]]:
    """Keep one numbering unit and report what the other units cost.

    A book that writes chapters as 第N章 and subsections as 第N节 has two
    interleaved numbering runs. Accepting both produces duplicate numbers, and a
    duplicate used to switch off every numbering check at once — so the very
    file that needed reporting came back clean. Choosing the dominant unit keeps
    books that number chapters as 第N节 working, without letting subsections
    masquerade as chapters.
    """

    counts = {unit: 0 for unit in CHAPTER_UNITS}
    for heading in headings:
        counts[heading["unit"]] += 1
    if not headings:
        return None, counts
    # Ties resolve by the declared order, so the choice never depends on which
    # heading happened to appear first.
    chosen = max(CHAPTER_UNITS, key=lambda unit: (counts[unit], -CHAPTER_UNITS.index(unit)))
    ignored = {unit: count for unit, count in counts.items() if unit != chosen and count}
    return chosen, ignored


# A contents entry is one line; the next follows immediately or after a blank.
# Chapter prose never packs headings this tightly, so the gap is an absolute
# signal and does not need calibrating against the rest of the file.
CONTENTS_MAX_GAP = 3
CONTENTS_MIN_ENTRIES = 3
# A volume shorter than this is not a volume. Treating every restart as a legal
# multi-volume structure is what let a single stray duplicated heading disable
# numbering validation for an entire book.
MIN_VOLUME_CHAPTERS = 3


def drop_leading_table_of_contents(
    headings: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Discard a leading contents block, which repeats every chapter heading.

    Density alone is not enough to decide. A median-based threshold breaks
    exactly when the contents block is a large share of all headings, because
    the block then drags the median into its own range and survives the test.

    So the rule is density plus corroboration: a leading run of headings packed
    within `CONTENTS_MAX_GAP` lines is dropped only if the chapter numbers it
    lists reappear among the headings that follow. A contents block always
    duplicates the chapters it indexes; a genuinely dense opening does not.
    Without this the index carries two "第一章" rows and every later stage
    slices at the contents entry, which holds no prose at all.
    """

    if len(headings) < CONTENTS_MIN_ENTRIES * 2:
        return headings, 0
    numbers = [heading["number"] for heading in headings]
    if numbers[0] is None:
        return headings, 0
    # The cut is where numbering restarts: a contents block is followed by the
    # same chapters again. Anchoring on the gap instead is off by one, because
    # the step from the last contents entry to chapter one is itself small.
    cut = 0
    for index in range(1, len(headings)):
        if numbers[index] is not None and numbers[index] <= numbers[0]:
            cut = index
            break
    if cut < CONTENTS_MIN_ENTRIES or cut > len(headings) - cut:
        return headings, 0
    internal_gaps = [
        headings[index + 1]["line_index"] - headings[index]["line_index"]
        for index in range(cut - 1)
    ]
    if any(gap > CONTENTS_MAX_GAP for gap in internal_gaps):
        # Numbering restarts, but the prefix holds real prose between headings.
        # That is a multi-volume book, not a contents block.
        return headings, 0
    dropped = {number for number in numbers[:cut] if number is not None}
    remaining = {number for number in numbers[cut:] if number is not None}
    if not dropped or not dropped <= remaining:
        # The packed prefix indexes chapters that never appear. It is part of
        # the story, not a contents list, so keep it.
        return headings, 0
    return headings[cut:], cut


def build_chapters(
    lines: list[str], headings: list[dict[str, Any]], text: str
) -> list[dict[str, Any]]:
    line_offsets = _line_offsets(lines)
    chapters: list[dict[str, Any]] = []
    for position, heading in enumerate(headings):
        start_line = heading["line_index"]
        end_line = (
            headings[position + 1]["line_index"]
            if position + 1 < len(headings)
            else len(lines)
        )
        body = text[
            line_offsets[start_line] : (
                line_offsets[end_line] if end_line < len(line_offsets) else len(text)
            )
        ]
        chapters.append(
            {
                "sequence": position + 1,
                "source_number": heading["number"],
                "unit": heading["unit"],
                "title": heading["title"],
                "heading": heading["raw"],
                "line_start": start_line + 1,
                "line_end": end_line,
                "char_count": _visible_width(body),
                "content_sha256": sha256_bytes(body.encode("utf-8")),
            }
        )
    return chapters


def _line_offsets(lines: list[str]) -> list[int]:
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1
    return offsets


def segment_by_restart(chapters: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split the chapter list wherever source numbering stops increasing.

    Each segment is a candidate volume. Validation then runs *inside* every
    segment, so a book whose volumes restart at 第一章 stays legal while a
    missing chapter inside any one volume is still reported.
    """

    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous: int | None = None
    for chapter in chapters:
        number = chapter["source_number"]
        if number is not None and previous is not None and number <= previous:
            segments.append(current)
            current = []
        if number is not None:
            previous = number
        current.append(chapter)
    if current:
        segments.append(current)
    return segments


def validate_chapters(
    chapters: list[dict[str, Any]], segments: list[list[dict[str, Any]]]
) -> list[str]:
    problems: list[str] = []
    if not chapters:
        problems.append("no chapter heading matched; the source may need manual spans")
        return problems
    unreadable = [
        chapter["heading"] for chapter in chapters if chapter["source_number"] is None
    ]
    if unreadable:
        problems.append("unreadable chapter numbers: " + ", ".join(unreadable[:5]))
    for position, segment in enumerate(segments, start=1):
        label = "" if len(segments) == 1 else f" in volume {position}"
        if len(segments) > 1 and len(segment) < MIN_VOLUME_CHAPTERS:
            # Too short to be a volume, so the restart is far more likely to be
            # a duplicated or stray heading than a legal structure.
            problems.append(
                f"numbering restarts after only {len(segment)} chapter(s) at "
                f"sequence {segment[0]['sequence']}; expected a volume boundary, "
                "check for a stray or duplicated heading"
            )
        numbers = [
            chapter["source_number"]
            for chapter in segment
            if chapter["source_number"] is not None
        ]
        for previous, current in zip(numbers, numbers[1:]):
            if current == previous:
                problems.append(f"duplicate chapter number {current}{label}")
                break
            if current != previous + 1:
                problems.append(
                    f"chapter numbering jumps from {previous} to {current}{label}"
                )
                break
    empty = [chapter["sequence"] for chapter in chapters if chapter["char_count"] < 50]
    if empty:
        problems.append(
            "chapters with almost no body text at sequence "
            + ", ".join(str(item) for item in empty[:5])
        )
    return problems


def build_index(source: Path) -> dict[str, Any]:
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    lines = text.split("\n")
    headings, long_heading_lines = find_heading_lines(lines)
    unit, ignored_units = select_chapter_unit(headings)
    headings = [heading for heading in headings if heading["unit"] == unit]
    headings, dropped = drop_leading_table_of_contents(headings)
    chapters = build_chapters(lines, headings, text)
    segments = segment_by_restart(chapters)
    problems = validate_chapters(chapters, segments)
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "novel_chapter_index",
        "source": {
            "artifact": source.name,
            "sha256": sha256_bytes(raw),
            "char_count": _visible_width(text),
            "line_count": len(lines),
        },
        "chapter_unit": unit,
        # Kept in the record rather than only warned about: a creator whose book
        # numbers chapters as 第N节 needs to see that 章 headings were skipped.
        "ignored_heading_units": ignored_units,
        # Body paragraphs that merely open with a chapter number. Reported rather
        # than silently dropped: if a book genuinely titles its chapters this
        # long, the count is how a creator finds out why chapters went missing.
        "long_heading_lines_skipped": long_heading_lines,
        "contents_entries_dropped": dropped,
        "volume_numbering_restarts": len(segments) > 1,
        "volume_count": len(segments),
        "chapter_count": len(chapters),
        "chapters": chapters,
        "problems": problems,
    }


REQUIRED_CHAPTER_FIELDS = ("sequence", "line_start", "line_end", "content_sha256")


def verify_index(index_path: Path, source: Path) -> dict[str, Any]:
    """Re-check an index against its source without trusting either.

    The workflow tells creators to write spans by hand when a source carries no
    headings at all, so this must report a malformed row rather than raise on
    it: an exception here reads as a broken tool, not as a fixable index.
    """

    index = json.loads(index_path.read_text(encoding="utf-8"))
    raw = source.read_bytes()
    problems: list[str] = []
    if not isinstance(index, dict):
        return {"verified": False, "problems": ["index must be a JSON object"]}
    source_record = index.get("source")
    if not isinstance(source_record, dict) or source_record.get("sha256") != sha256_bytes(raw):
        return {
            "verified": False,
            "problems": [
                "source bytes changed since the index was built; rebuild the index"
            ],
        }
    text = raw.decode("utf-8")
    lines = text.split("\n")
    line_offsets = _line_offsets(lines)
    chapters = index.get("chapters", [])
    if not isinstance(chapters, list) or not chapters:
        return {"verified": False, "problems": ["index carries no chapters"]}
    chapter_count = index.get("chapter_count")
    if (
        isinstance(chapter_count, bool)
        or not isinstance(chapter_count, int)
        or chapter_count != len(chapters)
    ):
        problems.append("chapter_count does not match the chapter rows")
    previous_end: int | None = None
    for position, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, dict):
            problems.append(f"chapter row {position} is not an object")
            continue
        missing = [field for field in REQUIRED_CHAPTER_FIELDS if field not in chapter]
        if missing:
            problems.append(
                f"chapter row {position} is missing {', '.join(missing)}; "
                "a hand-written span must carry the same fields the script writes"
            )
            continue
        sequence = chapter["sequence"]
        start_line, end_line = chapter["line_start"], chapter["line_end"]
        digest = chapter["content_sha256"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence != position
        ):
            problems.append(
                f"chapter row {position} sequence must be the contiguous value {position}"
            )
            continue
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (start_line, end_line)
        ):
            problems.append(f"chapter {sequence} line span must use integer line numbers")
            continue
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            problems.append(f"chapter {sequence} content_sha256 must be a 64-character hex hash")
            continue
        if not (1 <= start_line <= len(lines)) or not (
            start_line <= end_line <= len(lines)
        ):
            problems.append(
                f"chapter {chapter['sequence']} spans lines "
                f"{start_line}-{end_line}, outside the source's {len(lines)} lines"
            )
            continue
        if previous_end is not None and start_line != previous_end + 1:
            problems.append(
                f"chapter {sequence} must start at line {previous_end + 1}; "
                f"found {start_line}"
            )
            continue
        previous_end = end_line
        start = line_offsets[start_line - 1]
        end = line_offsets[end_line] if end_line < len(line_offsets) else len(text)
        if sha256_bytes(text[start:end].encode("utf-8")) != digest:
            problems.append(
                f"chapter {chapter['sequence']} span no longer matches its hash"
            )
    if previous_end is not None and previous_end != len(lines):
        problems.append(
            f"chapter spans end at line {previous_end}; source ends at line {len(lines)}"
        )
    return {"verified": not problems, "problems": problems}


# The triage stage reads a fraction of the book. A fixed, explainable count beats
# a formula nobody can predict, and the reported ratio is what stops a triage
# from being mistaken for a full pass.
DEFAULT_SAMPLE_COUNT = 12
# One literal for the stage marker, shared by the function default and the CLI
# flag. Spelling it twice is how the two drift apart, and a drifted default
# reports every chapter as missing while the files sit right there.
DEFAULT_COVERAGE_STAGE = "extract"


def sample_chapters(index_path: Path, count: int = DEFAULT_SAMPLE_COUNT) -> dict[str, Any]:
    """Choose a reproducible spread of chapters for the adaptation triage.

    Evenly spaced and always including the first and last chapter: the triage
    asks whether a book is worth adapting, and that question is answered by how
    the whole spine behaves, not by how well the opening performs. The selection
    is deterministic so a second run cites the same chapters as the first.
    """

    index = json.loads(index_path.read_text(encoding="utf-8"))
    chapters = index.get("chapters", [])
    total = len(chapters)
    if total == 0:
        return {"total_chapters": 0, "sampled": [], "coverage_ratio": 0.0}
    count = max(1, min(count, total))
    if count == 1:
        positions = [0]
    else:
        positions = sorted(
            {round(step * (total - 1) / (count - 1)) for step in range(count)}
        )
    sampled = [
        {
            "sequence": chapters[position]["sequence"],
            "source_number": chapters[position].get("source_number"),
            "title": chapters[position].get("title", ""),
            "line_start": chapters[position]["line_start"],
            "line_end": chapters[position]["line_end"],
            "content_sha256": chapters[position]["content_sha256"],
        }
        for position in positions
    ]
    return {
        "total_chapters": total,
        "sampled_count": len(sampled),
        "coverage_ratio": round(len(sampled) / total, 4),
        "sampled": sampled,
    }


def coverage(
    index_path: Path, analysis_dir: Path, stage: str = DEFAULT_COVERAGE_STAGE
) -> dict[str, Any]:
    """Report which chapters have no analysis file for one named stage.

    The stage marker is required, not cosmetic. Chapter files for different
    stages live in the same directory, so matching any file that merely contains
    a chapter number lets one stage's output satisfy another stage's gate — and
    the gate that exists to stop a silently skipped chapter then reports a book
    as complete.
    """

    index = json.loads(index_path.read_text(encoding="utf-8"))
    expected = {chapter["sequence"] for chapter in index.get("chapters", [])}
    pattern = re.compile(rf"^ch-(\d+)-{re.escape(stage)}$")
    found: set[int] = set()
    unmatched: list[str] = []
    for path in sorted(analysis_dir.glob("*.md")):
        match = pattern.fullmatch(path.stem)
        if match is None:
            unmatched.append(path.name)
        else:
            found.add(int(match.group(1)))
    missing = sorted(expected - found)
    return {
        "stage": stage,
        "expected": len(expected),
        "present": len(expected & found),
        "missing": missing,
        "unexpected": sorted(found - expected),
        # Named rather than counted: a file whose name drifted by one character
        # is invisible to the gate, and looks identical to a missing chapter.
        "unmatched_files": unmatched,
        "complete": not missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("index", help="build the chapter index for one source")
    build.add_argument("source", type=Path)
    build.add_argument("--out", type=Path, default=None)

    check = sub.add_parser("verify", help="re-check an index against its source")
    check.add_argument("index", type=Path)
    check.add_argument("source", type=Path)

    pick = sub.add_parser("sample", help="choose triage chapters spread across the book")
    pick.add_argument("index", type=Path)
    pick.add_argument("--count", type=int, default=DEFAULT_SAMPLE_COUNT)

    cover = sub.add_parser("coverage", help="report chapters with no analysis file")
    cover.add_argument("index", type=Path)
    cover.add_argument("analysis_dir", type=Path)
    cover.add_argument("--stage", default=DEFAULT_COVERAGE_STAGE)

    args = parser.parse_args(argv)

    if args.command == "index":
        document = build_index(args.source)
        payload = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True)
        if args.out is not None:
            _atomic_write_text(args.out, payload + "\n")
            print(
                json.dumps(
                    {
                        "chapter_count": document["chapter_count"],
                        "chapter_unit": document["chapter_unit"],
                        "ignored_heading_units": document["ignored_heading_units"],
                        "problems": document["problems"],
                        "out": str(args.out),
                    },
                    ensure_ascii=True,
                    indent=2,
                )
            )
        else:
            print(payload)
        return 1 if document["problems"] else 0

    if args.command == "verify":
        result = verify_index(args.index, args.source)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result["verified"] else 1

    if args.command == "sample":
        result = sample_chapters(args.index, args.count)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result["sampled"] else 1

    result = coverage(args.index, args.analysis_dir, args.stage)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
