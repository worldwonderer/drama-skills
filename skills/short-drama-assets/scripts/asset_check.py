#!/usr/bin/env python3
"""Validate the structural core of standalone character and look records."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("asset_check.py requires Python 3.9 or newer")

SKILL_ROOT = Path(__file__).resolve().parents[1]
HASH_RE = re.compile(r"[0-9a-f]{64}")
ACCEPTANCE_STATUSES = {"accepted", "proposed", "pending_choice"}


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
                "SOURCE_ENTRY_IS_INCOMPLETE", location, f"sources[{src!r}] needs owner/artifact"
            )
    elif all(isinstance(ref.get(key), str) for key in ("owner", "artifact")):
        owner, artifact = ref["owner"], ref["artifact"]
    else:
        return None, RefFinding(
            "REF_HAS_NO_UPSTREAM_BINDING", location, "needs src, or owner+artifact"
        )
    optional = {
        key: ref[key] for key in ("record_id", "field", "authority") if isinstance(ref.get(key), str)
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


class ValidationError(ValueError):
    pass


class RecordFile(NamedTuple):
    """One JSONL file: its upstream snapshot declaration and its records."""

    sources: dict[str, dict[str, Any]]
    records: list[dict[str, Any]]


def resolve_input(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.exists() or path.is_absolute():
        return path
    return SKILL_ROOT / path


def load_jsonl(value: str | Path) -> RecordFile:
    path = resolve_input(value)
    parsed: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValidationError(f"{path}:{number}: each JSONL record must be an object")
        parsed.append(record)
    sources = load_sources(parsed)
    records = [
        record for record in parsed if record.get("record_type") != SOURCES_RECORD_TYPE
    ]
    if not records:
        raise ValidationError(f"{path}: no records")
    return RecordFile(sources, records)


def require_text(record: dict[str, Any], key: str, label: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label}: {key} must be non-empty text")
    return value


def require_list(record: dict[str, Any], key: str, label: str) -> list[Any]:
    value = record.get(key)
    if not isinstance(value, list) or not value:
        raise ValidationError(f"{label}: {key} must be a non-empty list")
    return value


def validate_ref(
    value: Any, sources: dict[str, dict[str, Any]], label: str
) -> ResolvedRef:
    resolved, finding = resolve_ref(value, sources, label)
    if finding is not None:
        raise ValidationError(f"{label}: {finding.code}: {finding.detail}")
    assert resolved is not None
    if not resolved.record_id or not resolved.record_id.strip():
        raise ValidationError(f"{label}: record_id must be non-empty text")
    return resolved


def validate_acceptance(
    value: Any, sources: dict[str, dict[str, Any]], label: str
) -> None:
    if not isinstance(value, dict) or value.get("status") not in ACCEPTANCE_STATUSES:
        raise ValidationError(f"{label}: invalid creator_acceptance status")
    decision_ref = value.get("decision_ref")
    if value["status"] == "accepted" or decision_ref is not None:
        resolved = validate_ref(decision_ref, sources, f"{label}.decision_ref")
        # A decision_ref pointing at a block or an asset record binds acceptance to
        # something that never recorded a creator decision.
        if not str(resolved.record_id).startswith("CD-"):
            raise ValidationError(
                f"{label}.decision_ref: record_id must be a creator decision starting with CD-"
            )


def validate_records(characters: RecordFile, looks: RecordFile) -> dict[str, Any]:
    character_ids: set[str] = set()
    for index, record in enumerate(characters.records, 1):
        label = f"character[{index}]"
        character_id = require_text(record, "character_id", label)
        if not character_id.startswith("CHAR-"):
            raise ValidationError(f"{label}: character_id must start with CHAR-")
        if character_id in character_ids:
            raise ValidationError(f"{label}: duplicate character_id {character_id}")
        character_ids.add(character_id)
        require_text(record, "display_name", label)
        anchors = require_list(record, "identity_anchors", label)
        if any(not isinstance(item, str) or not item.strip() for item in anchors):
            raise ValidationError(f"{label}: identity_anchors must contain text")
        for ref_index, ref in enumerate(require_list(record, "source_refs", label), 1):
            validate_ref(ref, characters.sources, f"{label}.source_refs[{ref_index}]")
        validate_acceptance(record.get("creator_acceptance"), characters.sources, label)

    look_ids: set[str] = set()
    for index, record in enumerate(looks.records, 1):
        label = f"look[{index}]"
        look_id = require_text(record, "look_id", label)
        if not look_id.startswith("LOOK-"):
            raise ValidationError(f"{label}: look_id must start with LOOK-")
        if look_id in look_ids:
            raise ValidationError(f"{label}: duplicate look_id {look_id}")
        look_ids.add(look_id)
        character_ref = validate_ref(
            record.get("character_ref"), looks.sources, f"{label}.character_ref"
        )
        if character_ref.record_id not in character_ids:
            raise ValidationError(
                f"{label}: character_ref does not resolve: {character_ref.record_id}"
            )
        differences = record.get("differences")
        if not isinstance(differences, dict) or not any(differences.values()):
            raise ValidationError(f"{label}: differences must describe an observable change")
        validity = record.get("validity")
        if not isinstance(validity, dict) or not validity.get("from"):
            raise ValidationError(f"{label}: validity.from is required")
        validate_acceptance(record.get("creator_acceptance"), looks.sources, label)

    return {
        "status": "valid",
        "characters": len(characters.records),
        "looks": len(looks.records),
        # `source_declaration` is this file's own `sources` header resolving;
        # `look_binding` is a real cross-record lookup, and it is one only
        # because both files are handed to this checker.
        "checks": ["unique_ids", "acceptance_shape", "source_declaration", "look_binding"],
    }


def validate_files(characters: str | Path, looks: str | Path) -> dict[str, Any]:
    return validate_records(load_jsonl(characters), load_jsonl(looks))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--characters", required=True)
    parser.add_argument("--looks", required=True)
    args = parser.parse_args()
    try:
        result = validate_files(args.characters, args.looks)
    except (OSError, ValidationError) as exc:
        print(f"asset check failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
