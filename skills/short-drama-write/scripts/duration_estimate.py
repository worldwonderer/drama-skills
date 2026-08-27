#!/usr/bin/env python3
"""Estimate how long a screenplay runs, using the project's own declared rates.

This reports a number; it never judges one. The suite carries no cross-project
speech rate and no tolerance band, because a 90-second episode of dense argument
and a 90-second episode of silent work do not convert at the same ratio. The
creator declares the two rates their project actually uses, and this script
applies them.

Without declared rates the script still counts dialogue characters and action
paragraphs -- those counts are facts about the text -- and says the seconds
cannot be derived yet. That is the honest output, not a guess from a default.

What counts as a line and what counts as a paragraph is decided by
``screenplay-index.jsonl``, never re-derived here. This script used to carry its
own reader of the screenplay format, and a second reader of one format is a
second set of answers: it timed ``[VO]`` at zero, billed a Markdown comment as
speech, read ``他写下两个字：军宣。`` as dialogue because of the colon, and
counted one multi-line action paragraph once per line. The index already
classifies all four correctly. Reading it means this script cannot disagree with
the artifact the rest of the pipeline cites.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


# Creators run these scripts on whatever interpreter their machine provides, so
# an unsupported version must say so instead of failing inside an import.
MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "short-drama needs Python {}.{} or newer; this interpreter is {}.{}".format(
            *MINIMUM_PYTHON, sys.version_info.major, sys.version_info.minor
        )
    )


# [VO] and [OS] are spoken off-camera. The format contract writes them as
# ``[VO] 角色：台词`` -- a real line. Timing them at zero silently shortens every
# episode that carries its interiority in voice-over, and the estimate then reads
# as a deficit the writer pads to fill.
VOICED_TAGS = {"VO", "OS"}
VOICE_TAG_PREFIX = re.compile(r"^\[(?:VO|OS)\]\s*")
# ``角色（提示）：台词`` -- the speaker label may carry a parenthesised direction.
# Only the spoken half is timed; the direction is a note to the performer.
DIALOGUE = re.compile(r"^(?P<who>[^：:（(\[\]]{1,24})(?:（[^）]*）|\([^)]*\))?[：:](?P<line>.+)$")


class StaleIndex(Exception):
    """The index does not describe the screenplay it was handed."""


def _spoken_characters(line: str) -> int:
    """Count what is actually voiced: no whitespace, no bracketed directions."""
    stripped = re.sub(r"（[^）]*）|\([^)]*\)", "", line)
    return len(re.sub(r"\s", "", stripped))


def _is_voiced(block: Mapping[str, Any]) -> bool:
    """Speech is a dialogue block, or a production tag that is spoken aloud."""
    kind = block.get("kind")
    if kind == "dialogue":
        return True
    return kind == "production_tag" and block.get("tag") in VOICED_TAGS


def _block_text(screenplay: bytes, block: Mapping[str, Any]) -> str | None:
    """Return the block's own bytes, or None when its span does not fit."""
    start, end = block.get("byte_start"), block.get("byte_end")
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= start < end <= len(screenplay)
    ):
        return None
    try:
        return screenplay[start:end].decode("utf-8").strip()
    except UnicodeDecodeError:
        # A span that begins or ends mid-character is a stale offset, not a
        # crash: report it the same way as any other span that does not fit.
        return None


def measure(screenplay: bytes, blocks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Count the timed material, taking every classification from the index."""
    dialogue_lines = 0
    dialogue_characters = 0
    action_paragraphs = 0
    tag_lines = 0
    unreadable_blocks: list[str] = []

    for block in blocks:
        if block.get("record_type") != "block":
            continue
        kind = block.get("kind")

        if kind == "action":
            # One paragraph is one block however many lines it occupies. The
            # format contract lets an action paragraph run several lines.
            action_paragraphs += 1
            continue

        if not _is_voiced(block):
            # Scene headings, comments and instruction-only tags carry no
            # performed duration. Comments are not production content at all.
            if kind == "production_tag":
                tag_lines += 1
            continue

        text = _block_text(screenplay, block)
        if text is None:
            unreadable_blocks.append(str(block.get("block_id")))
            continue
        spoken = DIALOGUE.match(VOICE_TAG_PREFIX.sub("", text))
        if spoken is None:
            # The index recorded it as speech but it does not use the documented
            # line grammar. Guessing a duration here is how a wrong number gets
            # reported as a fact; the block is named instead.
            unreadable_blocks.append(str(block.get("block_id")))
            continue
        dialogue_lines += 1
        dialogue_characters += _spoken_characters(spoken.group("line"))

    counts: dict[str, Any] = {
        "dialogue_lines": dialogue_lines,
        "dialogue_characters": dialogue_characters,
        "action_paragraphs": action_paragraphs,
        "production_tag_lines": tag_lines,
    }
    if unreadable_blocks:
        counts["unreadable_blocks"] = sorted(unreadable_blocks)
    return counts


def index_blocks(
    index: list[dict[str, Any]], screenplay: bytes
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the index's blocks and its own review state.

    An index built from other bytes is refused rather than measured: its spans
    would land on text it never classified.
    """
    meta = next(
        (row for row in index if row.get("record_type") == "screenplay_index_meta"),
        None,
    )
    if meta is None:
        raise StaleIndex("the index carries no screenplay_index_meta record")
    declared = meta.get("source_byte_length")
    if not isinstance(declared, int):
        raise StaleIndex("the index declares no source_byte_length")
    if declared != len(screenplay):
        raise StaleIndex(
            "the index was built from {} bytes but this screenplay is {}; "
            "rebuild it with screenplay_index.py before estimating".format(
                declared, len(screenplay)
            )
        )
    review = {
        "review_status": meta.get("review_status"),
        "source_issue_count": meta.get("source_issue_count"),
    }
    return [row for row in index if row.get("record_type") == "block"], review


def declared_rates(project: dict[str, Any] | None) -> dict[str, Any]:
    """Read the project's own pacing rates, or report that it declared none."""
    pacing = ((project or {}).get("format") or {}).get("pacing") or {}
    per_second = pacing.get("spoken_characters_per_second")
    per_action = pacing.get("seconds_per_action_paragraph")
    usable = isinstance(per_second, (int, float)) and per_second > 0 and (
        isinstance(per_action, (int, float)) and per_action >= 0
    )
    return {
        "declared": bool(usable),
        "spoken_characters_per_second": per_second if usable else None,
        "seconds_per_action_paragraph": per_action if usable else None,
    }


def estimate(
    screenplay: bytes,
    blocks: Iterable[Mapping[str, Any]],
    project: dict[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    counts = measure(screenplay, blocks)
    rates = declared_rates(project)
    target = ((project or {}).get("format") or {}).get("target_seconds_per_episode")

    result: dict[str, Any] = {"counts": counts, "rates": rates, "seconds": None}
    result["target_seconds"] = target if isinstance(target, (int, float)) else None

    # Material the index could not classify is material this estimate did not
    # time. Reporting the seconds without that fact is how an incomplete number
    # gets read as a complete one.
    issues = (review or {}).get("source_issue_count")
    unreadable = counts.get("unreadable_blocks") or []
    if isinstance(issues, int) and issues > 0 or unreadable:
        result["incomplete"] = {
            "index_review_status": (review or {}).get("review_status"),
            "source_issue_count": issues,
            "unreadable_blocks": unreadable,
            "note": (
                "the index left some of this screenplay unclassified, so the "
                "counts below cover less than the whole text; resolve the index "
                "issues before reading these seconds as the episode's length"
            ),
        }

    if not rates["declared"]:
        result["note"] = (
            "the project declares no format.pacing rates, so seconds cannot be "
            "derived; the counts above are still exact"
            if "incomplete" not in result
            else "the project declares no format.pacing rates, so seconds "
            "cannot be derived; the counts above are exact for the blocks the "
            "index classified, which is not all of this screenplay"
        )
        return result

    seconds = (
        counts["dialogue_characters"] / rates["spoken_characters_per_second"]
        + counts["action_paragraphs"] * rates["seconds_per_action_paragraph"]
    )
    result["seconds"] = round(seconds, 1)
    if result["target_seconds"]:
        delta = seconds - result["target_seconds"]
        result["delta_seconds"] = round(delta, 1)
        result["delta_ratio"] = round(delta / result["target_seconds"], 3)
        result["note"] = (
            "informational only: the suite sets no tolerance band, because the "
            "right spread depends on the project's own scenes"
        )
    else:
        result["note"] = (
            "no format.target_seconds_per_episode is declared, so there is "
            "nothing to compare the estimate against"
        )
    return result


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise SystemExit("{}:{}: {}".format(path, number, error))
        if isinstance(row, dict):
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate screenplay duration from the project's declared rates."
    )
    parser.add_argument("screenplay", type=Path)
    parser.add_argument(
        "--index",
        type=Path,
        required=True,
        help="screenplay-index.jsonl built from this screenplay by screenplay_index.py",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="short-drama.json carrying format.pacing and target_seconds_per_episode",
    )
    args = parser.parse_args(argv)

    if not args.screenplay.is_file():
        raise SystemExit("screenplay not found: {}".format(args.screenplay))
    if not args.index.is_file():
        raise SystemExit("screenplay index not found: {}".format(args.index))

    project = None
    if args.project is not None:
        if not args.project.is_file():
            raise SystemExit("project file not found: {}".format(args.project))
        project = json.loads(args.project.read_text(encoding="utf-8"))

    screenplay = args.screenplay.read_bytes()
    try:
        blocks, review = index_blocks(_load_jsonl(args.index), screenplay)
    except StaleIndex as error:
        raise SystemExit("{}: {}".format(args.index, error))

    report = estimate(screenplay, blocks, project, review)
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    # An estimate is never a verdict, so this exits successfully even when the
    # episode lands far from its target. Blocking here would turn a reported
    # number into the cross-project threshold this suite refuses to carry.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
