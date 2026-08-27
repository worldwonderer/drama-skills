#!/usr/bin/env python3
"""Validate provider-neutral, timeline-level short-drama music specs."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("music_spec_check.py requires Python 3.9 or newer")


# ---------------------------------------------------------------------------
# REFERENCE RESOLVER
#
# Each skill carries its own copy of this block. A skill must stay runnable
# after copying only its own directory, so the suite has no shared library and
# this file imports nothing from outside its own skill.
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

SKILL_ROOT = Path(__file__).resolve().parents[1]
HASH_RE = re.compile(r"[0-9a-f]{64}")
VENDOR_FIELDS = {
    "provider",
    "model",
    "endpoint",
    "api_key",
    "task_id",
    "remote_id",
    "callback_url",
}
TOP_LEVEL_FIELDS = {
    "music_id",
    "scope",
    "source_refs",
    "narrative_function",
    "prompt",
    "mode",
    "lyrics",
    "mix_intent",
    "status",
}
SCOPE_FIELDS = {"episode_id", "start_seconds", "end_seconds"}
REFERENCE_FIELDS = {"src", "owner", "artifact", "record_id", "field", "authority"}
SOURCES_HEADER_FIELDS = {"record_type", "schema_version", "sources"}
SOURCE_ENTRY_FIELDS = {"owner", "artifact"}
MIX_FIELDS = {"entry", "exit", "duck_under_dialogue", "loop"}
MUSIC_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
EPISODE_ID_RE = re.compile(r"EP(?:[0-9]{3}|[1-9][0-9]{3,})")


class ValidationError(ValueError):
    """A music spec cannot be handed to production safely."""


def resolve_input(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.exists() or path.is_absolute():
        return path
    return SKILL_ROOT / path


def load_jsonl(value: str | Path) -> list[dict[str, Any]]:
    path = resolve_input(value)
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValidationError(f"{path}:{number}: each record must be an object")
        records.append(record)
    if not records:
        raise ValidationError(f"{path}: no music specs")
    return records


def _text(value: object, label: str, *, maximum: int = 10_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValidationError(f"{label} must be non-empty text up to {maximum} characters")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{label} must be finite")
    return number


def _exact_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValidationError(f"{label} has unsupported fields: {', '.join(unknown)}")


def _reject_vendor_fields(value: object, label: str) -> None:
    if isinstance(value, dict):
        leaked = sorted(str(key) for key in value if str(key).casefold() in VENDOR_FIELDS)
        if leaked:
            raise ValidationError(f"{label}: provider execution fields are forbidden: {', '.join(leaked)}")
        for key, child in value.items():
            _reject_vendor_fields(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_vendor_fields(child, f"{label}[{index}]")


def _validate_ref(value: object, label: str, sources: dict[str, dict[str, Any]]) -> None:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a reference object")
    _exact_fields(value, REFERENCE_FIELDS, label)
    resolved, finding = resolve_ref(value, sources, label)
    if finding is not None:
        raise ValidationError(f"{finding.location}: {finding.code}: {finding.detail}")
    if resolved is None:
        raise ValidationError(f"{label} could not be resolved")
    for key, text in (
        ("owner", resolved.owner),
        ("artifact", resolved.artifact),
    ):
        _text(text, f"{label}.{key}")
    if not isinstance(value.get("record_id") or value.get("field"), str):
        raise ValidationError(f"{label} needs record_id or field")


def split_sources_header(
    records: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Split a leading ``sources`` header off the file's own records.

    The header declares each upstream snapshot once, so a reference only names
    the snapshot key and the record it points at.
    """
    if not records or records[0].get("record_type") != SOURCES_RECORD_TYPE:
        return {}, list(records)
    header, *specs = records
    _exact_fields(header, SOURCES_HEADER_FIELDS, "sources header")
    if header.get("schema_version") != SOURCES_SCHEMA_VERSION:
        raise ValidationError(f"sources header schema_version must be {SOURCES_SCHEMA_VERSION}")
    declared = header.get("sources")
    if not isinstance(declared, dict) or not declared:
        raise ValidationError("sources header must declare at least one source")
    for key, entry in declared.items():
        label = f"sources[{key!r}]"
        if not isinstance(entry, dict):
            raise ValidationError(f"{label} must be an object")
        _exact_fields(entry, SOURCE_ENTRY_FIELDS, label)
        for field in sorted(SOURCE_ENTRY_FIELDS):
            _text(entry.get(field), f"{label}.{field}")
    return load_sources(header), specs


def validate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    sources, specs = split_sources_header(records)
    if not specs:
        raise ValidationError("no music specs")
    identifiers: set[str] = set()
    for index, record in enumerate(specs, 1):
        label = f"music[{index}]"
        _reject_vendor_fields(record, label)
        _exact_fields(record, TOP_LEVEL_FIELDS, label)
        music_id = _text(record.get("music_id"), f"{label}.music_id", maximum=80)
        if MUSIC_ID_RE.fullmatch(music_id) is None:
            raise ValidationError(f"{label}.music_id must be a portable identifier")
        if music_id in identifiers:
            raise ValidationError(f"duplicate music_id: {music_id}")
        identifiers.add(music_id)

        scope = record.get("scope")
        if not isinstance(scope, dict):
            raise ValidationError(f"{label}.scope must be an object")
        _exact_fields(scope, SCOPE_FIELDS, f"{label}.scope")
        episode_id = _text(
            scope.get("episode_id"), f"{label}.scope.episode_id", maximum=40
        )
        if EPISODE_ID_RE.fullmatch(episode_id) is None:
            raise ValidationError(f"{label}.scope.episode_id is invalid")
        start = _number(scope.get("start_seconds"), f"{label}.scope.start_seconds")
        end = _number(scope.get("end_seconds"), f"{label}.scope.end_seconds")
        if start < 0 or end <= start:
            raise ValidationError(f"{label}.scope must have 0 <= start < end")

        refs = record.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ValidationError(f"{label}.source_refs must be a non-empty list")
        for ref_index, ref in enumerate(refs, 1):
            _validate_ref(ref, f"{label}.source_refs[{ref_index}]", sources)

        _text(record.get("narrative_function"), f"{label}.narrative_function")
        _text(record.get("prompt"), f"{label}.prompt", maximum=2_000)
        mode = record.get("mode")
        if mode not in {"instrumental", "song"}:
            raise ValidationError(f"{label}.mode must be instrumental or song")
        lyrics = record.get("lyrics")
        if mode == "instrumental" and lyrics not in {None, ""}:
            raise ValidationError(f"{label}: instrumental music must not carry lyrics")
        if mode == "song":
            _text(lyrics, f"{label}.lyrics", maximum=3_500)

        mix = record.get("mix_intent")
        if not isinstance(mix, dict):
            raise ValidationError(f"{label}.mix_intent must be an object")
        _exact_fields(mix, MIX_FIELDS, f"{label}.mix_intent")
        if not isinstance(mix.get("duck_under_dialogue"), bool):
            raise ValidationError(f"{label}.mix_intent.duck_under_dialogue must be boolean")
        if not isinstance(mix.get("loop"), bool):
            raise ValidationError(f"{label}.mix_intent.loop must be boolean")
        _text(mix.get("entry"), f"{label}.mix_intent.entry")
        _text(mix.get("exit"), f"{label}.mix_intent.exit")
        if record.get("status") not in {"candidate", "accepted", "revise"}:
            raise ValidationError(f"{label}.status must be candidate, accepted, or revise")

    return {"status": "valid", "music_specs": len(specs)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs")
    args = parser.parse_args(argv)
    try:
        result = validate_records(load_jsonl(args.specs))
    except (OSError, UnicodeError, ValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
