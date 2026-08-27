#!/usr/bin/env python3
"""Validate standalone review findings and their verdict summary."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("review_check.py requires Python 3.9 or newer")

SKILL_ROOT = Path(__file__).resolve().parents[1]
HASH_RE = re.compile(r"[0-9a-f]{64}")
SEVERITIES = {"fatal", "error", "warning", "note"}
STATUSES = {"open", "closed"}
VERDICTS = {"APPROVE", "APPROVE_WITH_NOTES", "REVISE", "PROVISIONAL"}
DISPOSITIONS = {"keep", "post_production", "targeted_edit", "resubmit", "rewrite", "not_applicable"}


class ValidationError(ValueError):
    pass


# ---------------------------------------------------------------------------
# REFERENCE RESOLVER
#
# A file declares each upstream snapshot once, and every reference names the
# declared snapshot plus the record. This skill carries its own copy so it stays
# runnable after copying only this directory; do not import it from elsewhere.
# ---------------------------------------------------------------------------

SOURCES_RECORD_TYPE = "sources"
SOURCES_SCHEMA_VERSION = "1.0.0"


class ResolvedRef(NamedTuple):
    """An upstream reference with its snapshot resolved."""

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


def split_sources(
    records: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Separate a leading ``sources`` header record from the payload records."""
    first = records[0] if records else None
    if isinstance(first, dict) and first.get("record_type") == SOURCES_RECORD_TYPE:
        return load_sources(first), records[1:]
    return {}, records


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
            raise ValidationError(f"{path}:{number}: record must be an object")
        records.append(record)
    return records


def load_object(value: str | Path) -> dict[str, Any]:
    path = resolve_input(value)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: invalid JSON") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{path}: verdict must be an object")
    return document


def text(record: dict[str, Any], key: str, label: str, *, empty_ok: bool = False) -> str:
    value = record.get(key)
    if not isinstance(value, str) or (not empty_ok and not value.strip()):
        qualifier = "text" if empty_ok else "non-empty text"
        raise ValidationError(f"{label}: {key} must be {qualifier}")
    return value


def validate_ref(
    value: Any,
    label: str,
    sources: dict[str, dict[str, Any]],
    *,
    record_optional: bool = False,
) -> None:
    resolved, defect = resolve_ref(value, sources, label)
    if resolved is None:
        detail = f"{defect.code}: {defect.detail}" if defect is not None else "unresolvable reference"
        raise ValidationError(f"{label}: {detail}")
    for key, field_value in (("owner", resolved.owner), ("artifact", resolved.artifact)):
        if not field_value.strip():
            raise ValidationError(f"{label}: {key} must be non-empty text")
    if not record_optional and not resolved.record_id and not resolved.field:
        raise ValidationError(f"{label}: record_id or field is required")


def validate_findings(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sources, findings = split_sources(records)
    indexed: dict[str, dict[str, Any]] = {}
    for index, finding in enumerate(findings, 1):
        label = f"finding[{index}]"
        finding_id = text(finding, "finding_id", label)
        if finding_id in indexed:
            raise ValidationError(f"{label}: duplicate finding_id {finding_id}")
        indexed[finding_id] = finding
        for key in (
            "diagnostic_code",
            "scope",
            "classification",
            "enforcer",
            "evidence",
            "impact",
            "disposition_rationale",
            "owner_skill",
        ):
            text(finding, key, label)
        text(finding, "required_change", label, empty_ok=True)
        if finding.get("severity") not in SEVERITIES:
            raise ValidationError(f"{label}: invalid severity")
        if finding.get("status") not in STATUSES:
            raise ValidationError(f"{label}: invalid status")
        disposition = finding.get("disposition")
        if disposition not in DISPOSITIONS:
            raise ValidationError(
                f"{label}: invalid disposition {disposition!r}; "
                f"use one of {', '.join(sorted(DISPOSITIONS))}"
            )
        # A finding that blocks delivery has to say what to do about it,
        # whatever its disposition. REV-11 exempts dispositions that call for no
        # change, and routes every non-calibration finding to `not_applicable`
        # -- which is one of the exempt three. So the common case was exempt,
        # and an open `fatal` that told its owner nothing was stamped valid.
        # REV-02 requires the fix; this is where that requirement lands.
        if (
            finding.get("status") == "open"
            and finding.get("severity") in {"fatal", "error"}
            and not str(finding.get("required_change") or "").strip()
        ):
            raise ValidationError(
                f"{label}: an open {finding.get('severity')} finding blocks "
                f"delivery and must state its required change"
            )
        if disposition in {"targeted_edit", "resubmit", "rewrite"} and not finding["required_change"].strip():
            raise ValidationError(f"{label}: disposition requires required_change")
        refs = finding.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValidationError(f"{label}: evidence_refs must be a non-empty list")
        for ref_index, ref in enumerate(refs, 1):
            validate_ref(ref, f"{label}.evidence_refs[{ref_index}]", sources)
        validate_ref(finding.get("target_ref"), f"{label}.target_ref", sources)
    return indexed


def validate_records(records: list[dict[str, Any]], verdict: dict[str, Any]) -> dict[str, Any]:
    indexed = validate_findings(records)
    _, findings = split_sources(records)
    verdict_sources = load_sources(verdict)
    text(verdict, "review_id", "verdict")
    scopes = verdict.get("scope")
    if not isinstance(scopes, list) or not scopes or any(not isinstance(item, str) or not item for item in scopes):
        raise ValidationError("verdict: scope must be a non-empty text list")
    artifacts = verdict.get("reviewed_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValidationError("verdict: reviewed_artifacts must be non-empty")
    for index, artifact in enumerate(artifacts, 1):
        validate_ref(
            artifact,
            f"verdict.reviewed_artifacts[{index}]",
            verdict_sources,
            record_optional=True,
        )
    validate_ref(
        verdict.get("findings_ref"), "verdict.findings_ref", verdict_sources, record_optional=True
    )
    if verdict.get("review_method") not in {"uninvolved_reviewer", "self_check"}:
        raise ValidationError("verdict: review_method must be uninvolved_reviewer or self_check")
    text(verdict, "reviewer", "verdict")
    if verdict.get("verdict") not in VERDICTS:
        raise ValidationError("verdict: invalid verdict")

    blockers = sorted(
        finding_id
        for finding_id, finding in indexed.items()
        if finding["status"] == "open" and finding["severity"] in {"fatal", "error"}
    )
    declared = verdict.get("blocking_findings")
    if not isinstance(declared, list) or sorted(declared) != blockers:
        raise ValidationError("verdict: blocking_findings must equal the open fatal/error findings")
    if verdict.get("open_blocker_count") != len(blockers):
        raise ValidationError("verdict: open_blocker_count does not match blocking_findings")
    decision = verdict["verdict"]
    if blockers and decision != "REVISE":
        raise ValidationError("verdict: open blockers require REVISE")
    if not blockers and decision == "REVISE":
        raise ValidationError("verdict: REVISE requires an open blocker")

    return {
        "status": "valid",
        "findings": len(findings),
        "open_blockers": len(blockers),
        "verdict": decision,
        # `evidence_declaration`: each evidence reference names a snapshot the
        # findings file declares. The records it points at live in artifacts
        # this checker never opens.
        "checks": [
            "finding_shape",
            "evidence_declaration",
            "blocker_count",
            "verdict_consistency",
        ],
    }


def validate_files(findings: str | Path, verdict: str | Path) -> dict[str, Any]:
    return validate_records(load_jsonl(findings), load_object(verdict))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", required=True)
    parser.add_argument("--verdict", required=True)
    args = parser.parse_args()
    try:
        result = validate_files(args.findings, args.verdict)
    except (OSError, ValidationError) as exc:
        print(f"review check failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
