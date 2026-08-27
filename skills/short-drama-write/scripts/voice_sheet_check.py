#!/usr/bin/env python3
"""Prove a voice record sheet is still a projection of the screenplay.

The sheet exists to be carried into a recording session, which is exactly the
moment nobody can check it against the script. A line edited in the sheet, or a
script revised after the sheet was built, both read as a perfectly ordinary
sheet — so the comparison has to be mechanical: resolve each line's block in
the derived index, slice those exact bytes out of the screenplay, and compare.

The script reads accepted creator files and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from collections.abc import Mapping
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
CHANNELS = {"sync", "dubbed", "VO", "OS"}
# `[VO]` and `[OS]` are spoken lines that happen to be delivered off-camera, and
# the index already records them with a `tag` and a `speaker`. Projecting only
# `dialogue` blocks made the VO and OS channels above unreachable, so an episode
# that carries its interiority in voice-over reported a clean sheet while the
# recording list was missing every one of those lines.
VOICED_TAGS = {"VO", "OS"}
# `[VO] 角色：台词` — the tag is stripped before the line grammar is applied.
VOICE_TAG_PREFIX = re.compile(r"^\[(?:VO|OS)\]\s*")


def _is_voiced(block: Mapping[str, Any]) -> bool:
    kind = block.get("kind")
    if kind == "dialogue":
        return True
    return kind == "production_tag" and block.get("tag") in VOICED_TAGS
# The resolver speaks the suite-wide reference vocabulary; this checker reports
# in its own.
REF_FINDING_CODES = {
    "REF_IS_NOT_AN_OBJECT": "VOICE_SOURCE_REF_MISSING",
    "REF_HAS_NO_UPSTREAM_BINDING": "VOICE_SOURCE_REF_MISSING",
    "REF_SRC_IS_NOT_DECLARED": "VOICE_SOURCE_REF_UNDECLARED",
    "SOURCE_ENTRY_IS_INCOMPLETE": "VOICE_SOURCE_REF_UNDECLARED",
}
# `角色（可表演提示）：台词` — the cue is optional and never part of the line.
DIALOGUE = re.compile(
    r"^(?P<speaker>[^（(：:]+)(?:[（(](?P<cue>[^）)]*)[）)])?\s*[：:]\s*(?P<line>.*)$",
    re.DOTALL,
)


class CheckError(ValueError):
    """The inputs cannot be checked at all, as opposed to failing a check."""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CheckError(f"unreadable JSONL: {path}") from error
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise CheckError(f"invalid JSONL at {path.name}:{number}") from error
        if not isinstance(record, dict):
            raise CheckError(f"JSONL needs one object per line: {path.name}:{number}")
        records.append(record)
    return records


def _finding(code: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **detail}


def _blocks_by_id(index: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        record["block_id"]: record
        for record in index
        if record.get("record_type") == "block"
        and isinstance(record.get("block_id"), str)
    }


def check(
    sheet: list[dict[str, Any]],
    index: list[dict[str, Any]],
    screenplay: bytes,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    blocks = _blocks_by_id(index)
    seen: set[str] = set()
    # The first record declares the upstream snapshots this sheet references; the
    # rest are voice lines.
    sources = load_sources(sheet)
    lines = [record for record in sheet if record.get("record_type") != SOURCES_RECORD_TYPE]

    for record in lines:
        line_id = record.get("line_id")
        if not isinstance(line_id, str):
            findings.append(_finding("VOICE_LINE_HAS_NO_ID", "a line record has no id"))
            continue
        if line_id in seen:
            findings.append(
                _finding("VOICE_LINE_ID_REPEATS", "line_id must be unique", line_id=line_id)
            )
            continue
        seen.add(line_id)

        channel = record.get("channel")
        if channel not in CHANNELS:
            findings.append(
                _finding(
                    "VOICE_CHANNEL_INVALID",
                    "channel must be sync, dubbed, VO, or OS",
                    line_id=line_id,
                    channel=channel,
                )
            )

        resolved, defect = resolve_ref(record.get("source_ref"), sources, line_id)
        if defect is not None:
            findings.append(
                _finding(
                    REF_FINDING_CODES[defect.code],
                    f"source_ref does not name an upstream snapshot: {defect.detail}",
                    line_id=line_id,
                )
            )
            continue
        if resolved is None or resolved.record_id is None:
            findings.append(
                _finding(
                    "VOICE_SOURCE_REF_MISSING",
                    "a line must bind the screenplay block it projects",
                    line_id=line_id,
                )
            )
            continue
        record_id = resolved.record_id
        block = blocks.get(record_id)
        if block is None:
            findings.append(
                _finding(
                    "VOICE_SOURCE_REF_UNRESOLVABLE",
                    "source_ref names a block that is not in the index",
                    line_id=line_id,
                    record_id=record_id,
                )
            )
            continue
        if not _is_voiced(block):
            findings.append(
                _finding(
                    "VOICE_SOURCE_IS_NOT_DIALOGUE",
                    "a voice line must project a spoken block: dialogue, [VO] or [OS]",
                    line_id=line_id,
                    record_id=record_id,
                    kind=block.get("kind"),
                )
            )
            continue

        start, end = block.get("byte_start"), block.get("byte_end")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or not 0 <= start < end <= len(screenplay)
        ):
            findings.append(
                _finding(
                    "VOICE_BLOCK_SPAN_INVALID",
                    "the indexed block span does not fit this screenplay",
                    line_id=line_id,
                    record_id=record_id,
                )
            )
            continue
        # The channel decides how the line is booked, and off-camera and
        # on-camera carry completely different room for change. It was checked
        # against the enum and never against the block it projects, so a [VO]
        # line could be booked `sync` and go to the booth under lip-sync
        # constraints. `_is_voiced` already reads the block's tag; this compares.
        expected = block.get("tag") if block.get("kind") == "production_tag" else "on_camera"
        if expected in VOICED_TAGS and channel not in {expected, "dubbed"}:
            findings.append(
                _finding(
                    "VOICE_CHANNEL_DISAGREES_WITH_BLOCK",
                    "an off-camera block must be booked on its own channel",
                    line_id=line_id,
                    record_id=record_id,
                    channel=channel,
                    tag=expected,
                )
            )
        elif expected == "on_camera" and channel in VOICED_TAGS:
            findings.append(
                _finding(
                    "VOICE_CHANNEL_DISAGREES_WITH_BLOCK",
                    "an on-camera dialogue block must not be booked as VO or OS",
                    line_id=line_id,
                    record_id=record_id,
                    channel=channel,
                )
            )

        raw = screenplay[start:end]
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            # A span that begins or ends mid-character is a stale offset. It was
            # crashing the whole check instead of reporting the one line.
            findings.append(
                _finding(
                    "VOICE_BLOCK_SPAN_INVALID",
                    "the indexed block span does not start and end on characters",
                    line_id=line_id,
                    record_id=record_id,
                )
            )
            continue
        body = VOICE_TAG_PREFIX.sub("", decoded.strip())
        match = DIALOGUE.match(body)
        if match is None:
            findings.append(
                _finding(
                    "VOICE_BLOCK_IS_UNPARSEABLE",
                    "the dialogue block does not use the documented line grammar",
                    line_id=line_id,
                    record_id=record_id,
                )
            )
            continue
        if record.get("line_text") != match.group("line"):
            findings.append(
                _finding(
                    "VOICE_LINE_TEXT_DIVERGED",
                    "line_text is not the screenplay wording; change the screenplay",
                    line_id=line_id,
                    record_id=record_id,
                )
            )
        indexed_speaker = block.get("speaker")
        if isinstance(indexed_speaker, str) and record.get("speaker_display") not in (
            None,
            indexed_speaker,
        ):
            findings.append(
                _finding(
                    "VOICE_SPEAKER_DIVERGED",
                    "speaker_display disagrees with the indexed speaker",
                    line_id=line_id,
                    record_id=record_id,
                )
            )

    dialogue_blocks = {
        block_id for block_id, block in blocks.items() if _is_voiced(block)
    }
    projected: set[str] = set()
    for record in lines:
        covered, _defect = resolve_ref(record.get("source_ref"), sources, "")
        if covered is not None and covered.record_id is not None:
            projected.add(covered.record_id)
    # Reported, never a finding: a sheet may legitimately cover one actor or one
    # scene, so an incomplete sheet is a scope decision, not a defect.
    uncovered = sorted(dialogue_blocks - projected)

    return {
        "schema_version": SCHEMA_VERSION,
        "lines": len(lines),
        "dialogue_blocks": len(dialogue_blocks),
        "uncovered_dialogue_blocks": uncovered,
        "findings": findings,
        "status": "pass" if not findings else "fail",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check a voice record sheet against its screenplay and index."
    )
    parser.add_argument("sheet", type=Path, help="the voice record sheet JSONL")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--screenplay", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = check(
            _load_jsonl(args.sheet),
            _load_jsonl(args.index),
            args.screenplay.read_bytes(),
        )
    except (CheckError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
