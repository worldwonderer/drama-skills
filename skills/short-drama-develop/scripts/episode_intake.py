#!/usr/bin/env python3
"""Mechanical intake guards for a multi-episode screenplay source.

This tool deliberately does not interpret story content.  It records exact byte
spans once, verifies them before every read, exposes one episode at a time, and
atomically merges agent-authored episode-map records in small configurable
batches.  The episode map remains the completion truth; a progress checkpoint
is only a reproducible derived cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


MINIMUM_PYTHON = (3, 10)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "episode_intake.py requires Python {}.{} or newer; found {}.{}".format(
            *MINIMUM_PYTHON, sys.version_info.major, sys.version_info.minor
        )
    )

SCHEMA_VERSION = "1.0.0"
CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
CHINESE_NUMBER = "零一二三四五六七八九十百千两"
CHINESE_HEADING_RE = re.compile(
    r"^[ \t　]*第\s*([0-9]+|["
    + CHINESE_NUMBER
    + r"]+)\s*集(?:[ \t　]+\S.*|[：:][ \t　]*\S.*)?[ \t　]*$"
)
EP_HEADING_RE = re.compile(
    r"^[ \t]*#[ \t]+EP[ \t]*([0-9]+)(?:[ \t　]+\S.*)?[ \t　]*$",
    re.IGNORECASE,
)
EPISODE_ID_RE = re.compile(r"^EP([0-9]{3}|[1-9][0-9]{3,})$")
MAX_HEADING_LENGTH = 80


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def chinese_to_int(text: str) -> int | None:
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    pending = 0
    seen = False
    for character in text:
        if character in CHINESE_DIGITS:
            pending = CHINESE_DIGITS[character]
            seen = True
        elif character in CHINESE_UNITS:
            total += (pending or 1) * CHINESE_UNITS[character]
            pending = 0
            seen = True
        else:
            return None
    return total + pending if seen else None


def _line_table(data: bytes) -> tuple[list[bytes], list[int]]:
    lines = data.splitlines(keepends=True)
    if data and (not lines or sum(map(len, lines)) != len(data)):
        raise ValueError("could not construct exact source line table")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return lines, offsets


def _heading_number(line: bytes) -> int | None:
    text = line.decode("utf-8").removeprefix("\ufeff").rstrip("\r\n")
    if len(text.strip()) > MAX_HEADING_LENGTH:
        return None
    match = CHINESE_HEADING_RE.fullmatch(text)
    if match:
        number = chinese_to_int(match.group(1))
        return number if number and number > 0 else None
    match = EP_HEADING_RE.fullmatch(text)
    number = int(match.group(1)) if match else None
    return number if number and number > 0 else None


def _episode_id(number: int) -> str:
    if number <= 0:
        raise ValueError("episode numbers must be positive")
    return f"EP{number:03d}"


def _portable_source_ref(source: Path, source_ref: str | None) -> str:
    value = source_ref or source.name
    candidate = Path(value)
    if (
        not value
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "://" in value
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ValueError("source_ref must be a portable project-relative path")
    return candidate.as_posix()


def _structural_problems(episodes: list[dict[str, Any]], data: bytes) -> list[str]:
    problems: list[str] = []
    numbers: list[int] = []
    previous_end = 0
    for position, row in enumerate(episodes, 1):
        if not isinstance(row, dict):
            problems.append(f"episode row {position} is not an object")
            continue
        match = EPISODE_ID_RE.fullmatch(str(row.get("episode_id", "")))
        if not match:
            problems.append(f"episode row {position} has an invalid episode_id")
            continue
        number = int(match.group(1))
        if number <= 0:
            problems.append(f"episode row {position} has a non-positive episode_id")
            continue
        numbers.append(number)
        if row.get("source_number") != number:
            problems.append(f"{row['episode_id']} source_number does not match episode_id")
        start = row.get("byte_start")
        end = row.get("byte_end")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(data):
            if start < previous_end:
                problems.append(f"episode row {position} overlaps or is out of source order")
            previous_end = max(previous_end, end)
            lines = data[start:end].splitlines(keepends=True)
            if not b"".join(lines[1:]).strip():
                problems.append(f"empty episode: {row['episode_id']}")
        else:
            problems.append(f"episode row {position} has an invalid byte span")
    seen: set[int] = set()
    duplicates: list[int] = []
    for number in numbers:
        if number in seen and number not in duplicates:
            duplicates.append(number)
        seen.add(number)
    if duplicates:
        problems.append("duplicate episode numbers: " + ", ".join(map(str, duplicates)))
    if numbers:
        if numbers != sorted(numbers):
            problems.append("episode numbers are out of source order")
        missing = sorted(set(range(min(numbers), max(numbers) + 1)) - set(numbers))
        if min(numbers) != 1:
            missing = sorted(set(range(1, min(numbers))) | set(missing))
        if missing:
            problems.append("missing episode numbers: " + ", ".join(map(str, missing)))
    else:
        problems.append("no episode headings found")
    return problems


def build_index(
    source_path: str | Path, *, source_ref: str | None = None
) -> dict[str, Any]:
    source = Path(source_path)
    data = source.read_bytes()
    lines, offsets = _line_table(data)
    headings: list[tuple[int, int]] = []
    for line_index, line in enumerate(lines):
        number = _heading_number(line)
        if number is not None:
            headings.append((line_index, number))
    episodes: list[dict[str, Any]] = []
    for position, (line_index, number) in enumerate(headings):
        next_line = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        start = offsets[line_index]
        end = offsets[next_line]
        episodes.append(
            {
                "episode_id": _episode_id(number),
                "source_number": number,
                "line_start": line_index + 1,
                "line_end": next_line,
                "byte_start": start,
                "byte_end": end,
                "byte_length": end - start,
                "content_sha256": sha256(data[start:end]),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "episode_intake_index",
        "source_name": source.name,
        "source_ref": _portable_source_ref(source, source_ref),
        "source_sha256": sha256(data),
        "source_byte_length": len(data),
        "episode_count": len(episodes),
        "episodes": episodes,
        "unmapped_spans": _unmapped_spans(episodes, data, offsets),
        "problems": _structural_problems(episodes, data),
    }


def _line_number(offsets: list[int], byte_offset: int, *, end: bool = False) -> int:
    if byte_offset not in offsets:
        raise ValueError(f"byte offset {byte_offset} is not a line boundary")
    index = offsets.index(byte_offset)
    return index if end else index + 1


def _unmapped_spans(
    episodes: list[dict[str, Any]], data: bytes, offsets: list[int]
) -> list[dict[str, Any]]:
    """Locate meaningful source bytes outside episode spans without copying them."""

    spans: list[dict[str, Any]] = []
    cursor = 0
    ranges = [
        (row.get("byte_start"), row.get("byte_end"))
        for row in episodes
        if isinstance(row, dict)
    ]
    for start, end in ranges:
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start > cursor and data[cursor:start].strip():
            spans.append(
                {
                    "line_start": _line_number(offsets, cursor),
                    "line_end": _line_number(offsets, start, end=True),
                    "byte_start": cursor,
                    "byte_end": start,
                    "byte_length": start - cursor,
                    "content_sha256": sha256(data[cursor:start]),
                }
            )
        cursor = max(cursor, end)
    if cursor < len(data) and data[cursor:].strip():
        spans.append(
            {
                "line_start": _line_number(offsets, cursor),
                "line_end": _line_number(offsets, len(data), end=True),
                "byte_start": cursor,
                "byte_end": len(data),
                "byte_length": len(data) - cursor,
                "content_sha256": sha256(data[cursor:]),
            }
        )
    return spans


def make_manual_index(
    source_path: str | Path,
    spans: Iterable[tuple[str, int, int]],
    *,
    source_ref: str | None = None,
) -> dict[str, Any]:
    """Create a verifiable index from agent-selected, line-aligned byte spans.

    This is the escape hatch for nonstandard headings.  The agent chooses the
    boundaries; this function only binds those choices to exact source bytes.
    """

    source = Path(source_path)
    data = source.read_bytes()
    _, offsets = _line_table(data)
    episodes: list[dict[str, Any]] = []
    for episode_id, start, end in spans:
        match = EPISODE_ID_RE.fullmatch(episode_id)
        if not match or int(match.group(1)) <= 0:
            raise ValueError(f"invalid episode_id: {episode_id}")
        if not (0 <= start < end <= len(data)):
            raise ValueError(f"invalid byte span for {episode_id}")
        episodes.append(
            {
                "episode_id": episode_id,
                "source_number": int(match.group(1)),
                "line_start": _line_number(offsets, start),
                "line_end": _line_number(offsets, end, end=True),
                "byte_start": start,
                "byte_end": end,
                "byte_length": end - start,
                "content_sha256": sha256(data[start:end]),
            }
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "episode_intake_index",
        "source_name": source.name,
        "source_ref": _portable_source_ref(source, source_ref),
        "source_sha256": sha256(data),
        "source_byte_length": len(data),
        "episode_count": len(episodes),
        "episodes": episodes,
        "unmapped_spans": _unmapped_spans(episodes, data, offsets),
        "problems": [],
    }
    document["problems"] = _structural_problems(episodes, data)
    return document


def build_manual_index(
    source_path: str | Path,
    boundaries_path: str | Path,
    *,
    source_ref: str | None = None,
) -> dict[str, Any]:
    """Turn agent-selected one-based line starts into exact verified spans."""

    source = Path(source_path)
    data = source.read_bytes()
    lines, offsets = _line_table(data)
    boundaries = _read_jsonl(Path(boundaries_path))
    if not boundaries:
        raise ValueError("boundaries JSONL must contain at least one episode")
    prepared: list[tuple[str, int, int | None]] = []
    seen: set[str] = set()
    for position, boundary in enumerate(boundaries, 1):
        episode_id = boundary.get("episode_id")
        line_start = boundary.get("line_start")
        line_end = boundary.get("line_end")
        if not isinstance(episode_id, str):
            raise ValueError(f"boundary {position} has an invalid episode_id")
        match = EPISODE_ID_RE.fullmatch(episode_id)
        if match is None or int(match.group(1)) <= 0:
            raise ValueError(f"boundary {position} has an invalid episode_id")
        if episode_id in seen:
            raise ValueError(f"boundaries contain duplicate episode_id {episode_id}")
        seen.add(episode_id)
        if not isinstance(line_start, int) or isinstance(line_start, bool):
            raise ValueError(f"{episode_id} line_start must be an integer")
        if line_end is not None and (
            not isinstance(line_end, int) or isinstance(line_end, bool)
        ):
            raise ValueError(f"{episode_id} line_end must be an integer")
        prepared.append((episode_id, line_start, line_end))
    spans: list[tuple[str, int, int]] = []
    for position, (episode_id, line_start, explicit_end) in enumerate(prepared):
        if not 1 <= line_start <= len(lines):
            raise ValueError(f"{episode_id} line_start is outside the source")
        next_start = prepared[position + 1][1] if position + 1 < len(prepared) else None
        if next_start is not None and next_start <= line_start:
            raise ValueError("boundary line_start values must be strictly increasing")
        line_end = explicit_end if explicit_end is not None else (
            next_start - 1 if next_start is not None else len(lines)
        )
        if not line_start <= line_end <= len(lines):
            raise ValueError(f"{episode_id} line_end is outside its source span")
        if next_start is not None and line_end >= next_start:
            raise ValueError(f"{episode_id} line_end overlaps the next episode")
        spans.append((episode_id, offsets[line_start - 1], offsets[line_end]))
    return make_manual_index(source, spans, source_ref=source_ref)


def _json_bytes(document: Any) -> bytes:
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_index(path: str | Path, document: dict[str, Any]) -> None:
    _atomic_write(Path(path), _json_bytes(document))


def _load_index(index_path: str | Path) -> dict[str, Any]:
    document = json.loads(Path(index_path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("episodes"), list):
        raise ValueError("index must be a JSON object containing an episodes list")
    return document


def verify_index(index_path: str | Path, source_path: str | Path) -> dict[str, Any]:
    problems: list[str] = []
    try:
        document = _load_index(index_path)
        data = Path(source_path).read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        return {"verified": False, "problems": [str(error)]}
    if document.get("schema_version") != SCHEMA_VERSION:
        problems.append("unsupported schema_version")
    if document.get("record_type") != "episode_intake_index":
        problems.append("record_type is not episode_intake_index")
    if document.get("source_sha256") != sha256(data):
        problems.append("source_sha256 does not match the exact source bytes")
    if document.get("source_byte_length") != len(data):
        problems.append("source_byte_length does not match")
    try:
        _portable_source_ref(Path(source_path), document.get("source_ref"))
    except (TypeError, ValueError) as error:
        problems.append(str(error))
    episodes = document["episodes"]
    if document.get("episode_count") != len(episodes):
        problems.append("episode_count does not match episodes")
    _, offsets = _line_table(data)
    previous_end = -1
    for position, row in enumerate(episodes, 1):
        if not isinstance(row, dict):
            problems.append(f"episode row {position} is not an object")
            continue
        label = str(row.get("episode_id", f"row {position}"))
        start, end = row.get("byte_start"), row.get("byte_end")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(data)):
            problems.append(f"{label} has an invalid byte span")
            continue
        if start < previous_end:
            problems.append(f"{label} overlaps or is out of source order")
        previous_end = end
        try:
            expected_line_start = _line_number(offsets, start)
            expected_line_end = _line_number(offsets, end, end=True)
        except ValueError as error:
            problems.append(f"{label}: {error}")
        else:
            if row.get("line_start") != expected_line_start or row.get("line_end") != expected_line_end:
                problems.append(f"{label} line span does not match its byte span")
        if row.get("content_sha256") != sha256(data[start:end]):
            problems.append(f"{label} content_sha256 does not match its exact span")
        if row.get("byte_length") != end - start:
            problems.append(f"{label} byte_length does not match its byte span")
    problems.extend(_structural_problems(episodes, data))
    if not any("byte span" in problem or "overlaps" in problem for problem in problems):
        try:
            expected_unmapped = _unmapped_spans(episodes, data, offsets)
        except ValueError:
            problems.append("unmapped spans cannot be located in the current source")
        else:
            if document.get("unmapped_spans") != expected_unmapped:
                problems.append("unmapped_spans do not match bytes outside episode spans")
    # Avoid multiplying an identical structural message if a hand-edited index
    # already carried it in its advisory problems field.
    problems = list(dict.fromkeys(problems))
    return {"verified": not problems, "problems": problems}


def _require_verified(index_path: str | Path, source_path: str | Path) -> dict[str, Any]:
    result = verify_index(index_path, source_path)
    if not result["verified"]:
        raise ValueError("index verification failed: " + "; ".join(result["problems"]))
    return _load_index(index_path)


def slice_episode(
    index_path: str | Path,
    source_path: str | Path,
    episode_id: str,
    output_path: str | Path | None = None,
) -> bytes:
    if output_path is not None and Path(output_path).resolve() in {
        Path(index_path).resolve(),
        Path(source_path).resolve(),
    }:
        raise ValueError("slice output must not replace its source or index")
    document = _require_verified(index_path, source_path)
    matches = [row for row in document["episodes"] if row.get("episode_id") == episode_id]
    if len(matches) != 1:
        raise ValueError(f"episode_id must identify exactly one span: {episode_id}")
    data = Path(source_path).read_bytes()
    row = matches[0]
    content = data[row["byte_start"] : row["byte_end"]]
    if sha256(content) != row["content_sha256"]:
        raise ValueError(f"episode span changed: {episode_id}")
    if output_path is not None:
        _atomic_write(Path(output_path), content)
    return content


def _read_jsonl(path: Path, *, absent_ok: bool = False) -> list[dict[str, Any]]:
    if absent_ok and not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_bytes().splitlines(), 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{path}: invalid JSONL line {line_number}: {error}") from error
        if not isinstance(record, dict):
            raise ValueError(f"{path}: JSONL line {line_number} is not an object")
        records.append(record)
    return records


def _validated_records(records: list[dict[str, Any]], valid_ids: set[str], label: str) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for position, record in enumerate(records, 1):
        episode_id = record.get("episode_id")
        if not isinstance(episode_id, str) or episode_id not in valid_ids:
            raise ValueError(f"{label} record {position} has an episode_id outside the index")
        if episode_id in by_id:
            raise ValueError(f"{label} contains duplicate episode_id {episode_id}")
        by_id[episode_id] = record
    return by_id


def _jsonl_bytes(records: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(_json_bytes(record) for record in records)


def progress(
    index_path: str | Path,
    source_path: str | Path,
    map_path: str | Path,
    checkpoint_path: str | Path | None = None,
    *,
    batch_size: int,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if checkpoint_path is not None and Path(checkpoint_path).resolve() in {
        Path(index_path).resolve(),
        Path(source_path).resolve(),
        Path(map_path).resolve(),
    }:
        raise ValueError("checkpoint must not replace the index, source, or episode-map")
    document = _require_verified(index_path, source_path)
    ordered_ids = [row["episode_id"] for row in document["episodes"]]
    records = _read_jsonl(Path(map_path), absent_ok=True)
    by_id = _validated_records(records, set(ordered_ids), "episode-map")
    completed = [episode_id for episode_id in ordered_ids if episode_id in by_id]
    pending = [episode_id for episode_id in ordered_ids if episode_id not in by_id]
    canonical_records = [by_id[episode_id] for episode_id in completed]
    map_file = Path(map_path)
    map_content = map_file.read_bytes() if map_file.exists() else b""
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "episode_intake_progress",
        "index_sha256": sha256(Path(index_path).read_bytes()),
        "source_sha256": document["source_sha256"],
        "map_sha256": sha256(map_content),
        "record_hashes": [
            {"episode_id": record["episode_id"], "sha256": sha256(_json_bytes(record))}
            for record in canonical_records
        ],
        "completed": completed,
        "pending": pending,
        "next_batch": pending[:batch_size],
        "complete": not pending,
    }
    if checkpoint_path is not None:
        _atomic_write(Path(checkpoint_path), _json_bytes(checkpoint))
    return checkpoint


def merge_batch(
    index_path: str | Path,
    source_path: str | Path,
    batch_path: str | Path,
    current_map_path: str | Path,
    output_path: str | Path,
    checkpoint_path: str | Path | None = None,
    *,
    batch_size: int,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    protected = {
        Path(index_path).resolve(),
        Path(source_path).resolve(),
        Path(batch_path).resolve(),
    }
    current_map = Path(current_map_path)
    output = Path(output_path)
    if current_map.resolve() in protected:
        raise ValueError("current episode-map must not replace an index or source input")
    if output.resolve() in {*protected, current_map.resolve()}:
        raise ValueError("merged output must be separate from every input")
    if checkpoint_path is not None and Path(checkpoint_path).resolve() in {
        *protected,
        current_map.resolve(),
        output.resolve(),
    }:
        raise ValueError("checkpoint must not replace an input or merged output")
    document = _require_verified(index_path, source_path)
    ordered_ids = [row["episode_id"] for row in document["episodes"]]
    valid_ids = set(ordered_ids)
    batch_records = _read_jsonl(Path(batch_path))
    if len(batch_records) > batch_size:
        raise ValueError(f"batch contains {len(batch_records)} records; maximum is {batch_size}")
    batch = _validated_records(batch_records, valid_ids, "batch")
    existing_records = _read_jsonl(current_map, absent_ok=True)
    existing = _validated_records(existing_records, valid_ids, "episode-map")
    added: list[str] = []
    for episode_id, record in batch.items():
        if episode_id in existing:
            if _json_bytes(existing[episode_id]) != _json_bytes(record):
                raise ValueError(f"conflicting episode-map record: {episode_id}")
        else:
            existing[episode_id] = record
            added.append(episode_id)
    merged_records = [existing[episode_id] for episode_id in ordered_ids if episode_id in existing]
    _atomic_write(output, _jsonl_bytes(merged_records))
    derived = progress(
        index_path,
        source_path,
        output,
        checkpoint_path,
        batch_size=batch_size,
    )
    return {
        "output": str(output),
        "added": [episode_id for episode_id in ordered_ids if episode_id in set(added)],
        "progress": derived,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Byte-accurate multi-episode screenplay intake guard")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index")
    index.add_argument("source", type=Path)
    index.add_argument("--out", type=Path, required=True)
    index.add_argument("--source-ref")
    manual = commands.add_parser("manual-index")
    manual.add_argument("source", type=Path)
    manual.add_argument("boundaries", type=Path)
    manual.add_argument("--out", type=Path, required=True)
    manual.add_argument("--source-ref")
    verify = commands.add_parser("verify")
    verify.add_argument("index", type=Path)
    verify.add_argument("source", type=Path)
    slice_command = commands.add_parser("slice")
    slice_command.add_argument("index", type=Path)
    slice_command.add_argument("source", type=Path)
    slice_command.add_argument("episode_id")
    slice_command.add_argument("--out", type=Path, required=True)
    status = commands.add_parser("progress", aliases=["status"])
    status.add_argument("index", type=Path)
    status.add_argument("source", type=Path)
    status.add_argument("episode_map", type=Path)
    status.add_argument("--checkpoint", type=Path)
    status.add_argument("--batch-size", type=int, required=True)
    merge = commands.add_parser("merge")
    merge.add_argument("index", type=Path)
    merge.add_argument("source", type=Path)
    merge.add_argument("batch", type=Path)
    merge.add_argument("current_episode_map", type=Path)
    merge.add_argument("--out", type=Path, required=True)
    merge.add_argument("--checkpoint", type=Path)
    merge.add_argument("--batch-size", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"index", "manual-index"}:
            if args.source.resolve() == args.out.resolve():
                raise ValueError("index output must not replace its source")
            if args.command == "manual-index" and args.boundaries.resolve() == args.out.resolve():
                raise ValueError("index output must not replace its boundaries input")
            document = (
                build_index(args.source, source_ref=args.source_ref)
                if args.command == "index"
                else build_manual_index(
                    args.source,
                    args.boundaries,
                    source_ref=args.source_ref,
                )
            )
            write_index(args.out, document)
            result: Any = {"output": str(args.out), "episode_count": document["episode_count"]}
            if document["problems"]:
                result["problems"] = document["problems"]
                print(json.dumps(result, sort_keys=True))
                return 2
        elif args.command == "verify":
            result = verify_index(args.index, args.source)
            if not result["verified"]:
                print(json.dumps(result, sort_keys=True), file=sys.stderr)
                return 1
        elif args.command == "slice":
            content = slice_episode(args.index, args.source, args.episode_id, args.out)
            result = {"output": str(args.out), "episode_id": args.episode_id, "byte_length": len(content)}
        elif args.command in {"progress", "status"}:
            result = progress(args.index, args.source, args.episode_map, args.checkpoint, batch_size=args.batch_size)
        else:
            result = merge_batch(
                args.index,
                args.source,
                args.batch,
                args.current_episode_map,
                args.out,
                args.checkpoint,
                batch_size=args.batch_size,
            )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
