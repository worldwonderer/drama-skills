#!/usr/bin/env python3
"""Validate standalone image-prompt specs without generating media."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit("image_prompt_check.py requires Python 3.9 or newer")

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

SKILL_ROOT = Path(__file__).resolve().parents[1]
HASH_RE = re.compile(r"[0-9a-f]{64}")
# `common-recipe.md` forbids weight syntax and any one engine's control words: a
# generic prompt that carries provider control syntax has stopped being generic.
# This checks syntax only. Whether the prose leans on generic quality language is
# `IMG-02`, a `craft_default` a creator may override with a reason, so it is a
# reviewer's call citing what the description actually lacks — see
# `common-recipe.md`, which forbids blocking delivery on a fixed word list.
ENGINE_SYNTAX_RE = re.compile(
    r"(?:^|[\s,，(（])--(?:ar|v|q|niji|style|no|seed|cref|sref)\b"
    r"|::-?\d"
    # Weight syntax is written (x:1.2); prose writes "(aperture: 1.8)" with a space.
    r"|:[01]\.\d\s*[)）]",
    re.IGNORECASE,
)
PURPOSES = {
    "character_sheet",
    "location_plate",
    "prop_plate",
    "look_state_variant",
    "edit_delta",
    "lookdev_frame",
}
VENDOR_FIELDS = {
    "authorization",
    "credential",
    "credentials",
    "model",
    "model_id",
    "model_name",
    "provider",
    "provider_id",
    "api_key",
    "task_id",
    "remote_id",
    "access_token",
    "token",
    "secret",
    "password",
}
NORMALIZED_VENDOR_FIELDS = {re.sub(r"[^a-z0-9]", "", key) for key in VENDOR_FIELDS}


# The source policy -> render treatment mapping from `common-recipe.md`. A
# treatment outside its policy's row is a structural defect: the fix is a
# creator override that changes the accepted policy, never a quiet widening here.
ALLOWED_TREATMENTS = {
    "exact_readable": {"readable", "postproduction"},
    "graphic_only": {"symbolic"},
    "no_readable_text": {"blank", "symbolic"},
    "pending_creator_text": {"postproduction"},
}

# A blanket "no text in the image" instruction, however it is phrased. Matching
# two literals (`no text` / `无文字`) let the reference document's own worked
# counter-example through: `画面中不要任何文字` contains neither.
NO_TEXT_CONSTRAINT = re.compile(
    r"(无任何(文字|字|文本)|无文字|不要(任何)?(文字|字|文本)|不出现(任何)?(文字|字)"
    r"|没有(任何)?(文字|字)|不含(任何)?(文字|字)|禁止(出现)?(文字|字)"
    r"|no\s+(visible\s+)?(text|lettering|writing|words|typography)"
    r"|without\s+(any\s+)?text|text[-\s]free)",
    re.IGNORECASE,
)


class ValidationError(ValueError):
    pass


def resolve_input(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.exists() or path.is_absolute():
        return path
    return SKILL_ROOT / path


def load_jsonl(value: str | Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return the file's ``sources`` declaration and its prompt-spec records.

    A leading ``{"record_type": "sources", ...}`` header declares the upstream
    snapshots the specs point at; it is not a spec and never counts as one.
    """
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
    sources: dict[str, dict[str, Any]] = {}
    if records and records[0].get("record_type") == SOURCES_RECORD_TYPE:
        header = records.pop(0)
        if not isinstance(header.get("sources"), dict):
            raise ValidationError(f"{path}: the sources header must declare a sources object")
        sources = load_sources(header)
    if not records:
        raise ValidationError(f"{path}: no prompt specs")
    return sources, records


def text(record: dict[str, Any], key: str, label: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label}: {key} must be non-empty text")
    return value


def nonempty_list(record: dict[str, Any], key: str, label: str) -> list[Any]:
    value = record.get(key)
    if not isinstance(value, list) or not value:
        raise ValidationError(f"{label}: {key} must be a non-empty list")
    return value


def validate_ref(
    value: Any,
    sources: dict[str, dict[str, Any]],
    label: str,
    *,
    field_allowed: bool = True,
) -> None:
    if not isinstance(value, dict):
        raise ValidationError(f"{label}: reference must be an object")
    resolved, finding = resolve_ref(value, sources, label)
    if finding is not None:
        raise ValidationError(f"{label}: {finding.code}: {finding.detail}")
    if resolved is None:
        raise ValidationError(f"{label}: reference could not be resolved")
    if not resolved.owner.strip() or not resolved.artifact.strip():
        raise ValidationError(f"{label}: owner and artifact must be non-empty text")
    if "record_id" not in value and (not field_allowed or "field" not in value):
        raise ValidationError(f"{label}: record_id or field is required")


def validate_reference_bindings(
    record: dict[str, Any], sources: dict[str, dict[str, Any]], label: str
) -> None:
    bindings = record.get("reference_bindings", [])
    if not isinstance(bindings, list):
        raise ValidationError(f"{label}: reference_bindings must be a list")
    slots: set[str] = set()
    orders: set[int] = set()
    for index, binding in enumerate(bindings, 1):
        item = f"{label}.reference_bindings[{index}]"
        if not isinstance(binding, dict):
            raise ValidationError(f"{item}: binding must be an object")
        slot = text(binding, "slot_id", item)
        order = binding.get("order")
        if slot in slots:
            raise ValidationError(f"{item}: duplicate slot_id {slot}")
        if not isinstance(order, int) or order < 1 or order in orders:
            raise ValidationError(f"{item}: order must be a unique positive integer")
        slots.add(slot)
        orders.add(order)
        validate_ref(binding.get("artifact_ref"), sources, f"{item}.artifact_ref")
        text(binding, "role", item)
        nonempty_list(binding, "may_control", item)
        nonempty_list(binding, "must_not_control", item)
        admission = binding.get("admission_status")
        if admission not in {"unverified", "creator_described", "visually_inspected"}:
            raise ValidationError(f"{item}: invalid admission_status")
        if admission == "unverified" and not binding.get("unresolved_risks"):
            raise ValidationError(f"{item}: unverified references need unresolved_risks")


def vendor_field_paths(value: object, prefix: str = "") -> list[str]:
    leaked: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            path = f"{prefix}.{name}" if prefix else name
            normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
            if normalized in NORMALIZED_VENDOR_FIELDS:
                leaked.append(path)
            leaked.extend(vendor_field_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            leaked.extend(vendor_field_paths(child, f"{prefix}[{index}]"))
    return leaked


def validate_asset_spec(
    record: dict[str, Any], sources: dict[str, dict[str, Any]], label: str
) -> None:
    binding = record.get("asset_binding")
    if not isinstance(binding, dict):
        raise ValidationError(f"{label}: asset_binding must be an object")
    validate_ref(binding.get("identity_ref"), sources, f"{label}.asset_binding.identity_ref")
    validate_ref(binding.get("variant_ref"), sources, f"{label}.asset_binding.variant_ref")
    for index, ref in enumerate(nonempty_list(record, "source_refs", label), 1):
        validate_ref(ref, sources, f"{label}.source_refs[{index}]")
    nonempty_list(record, "identity_or_form_anchors", label)

    if record["purpose"] == "edit_delta":
        edit = record.get("edit")
        if not isinstance(edit, dict):
            raise ValidationError(f"{label}: edit_delta requires edit")
        validate_ref(edit.get("target_ref"), sources, f"{label}.edit.target_ref")
        nonempty_list(edit, "changes", f"{label}.edit")
        nonempty_list(edit, "preserve", f"{label}.edit")
        text(edit, "continuity_impact", f"{label}.edit")

    handling = record.get("text_handling")
    if isinstance(handling, dict):
        if "source_policy_ref" in handling:
            validate_ref(
                handling.get("source_policy_ref"),
                sources,
                f"{label}.text_handling.source_policy_ref",
            )
        treatment = handling.get("render_treatment")
        if not isinstance(treatment, dict):
            raise ValidationError(f"{label}: text_handling.render_treatment is required")
        # `common-recipe.md` gives the source policy -> render treatment mapping
        # as a structural check. Nothing implemented it, so a prop whose accepted
        # policy is `no_readable_text` could ask for readable lettering and come
        # back `valid` -- inventing a name or a number the creator never accepted.
        source_mode = handling.get("source_mode")
        allowed = (
            ALLOWED_TREATMENTS.get(source_mode)
            if isinstance(source_mode, str)
            else None
        )
        mode = treatment.get("mode")
        if allowed is not None and mode not in allowed:
            raise ValidationError(
                f"{label}: text policy {source_mode!r} allows "
                f"{' or '.join(sorted(allowed))}, not {mode!r}; a creator "
                f"override must change the policy rather than the treatment"
            )
        if source_mode == "exact_readable" and mode == "readable":
            text(treatment, "exact_text", f"{label}.text_handling.render_treatment")
            negatives = " ".join(str(item) for item in record.get("negative_constraints", []))
            if NO_TEXT_CONSTRAINT.search(negatives):
                raise ValidationError(f"{label}: readable text conflicts with a no-text constraint")


def validate_lookdev_spec(
    record: dict[str, Any], sources: dict[str, dict[str, Any]], label: str
) -> None:
    validate_ref(record.get("direction_ref"), sources, f"{label}.direction_ref")
    validate_ref(record.get("production_profile_ref"), sources, f"{label}.production_profile_ref")
    subjects = nonempty_list(record, "subject_bindings", label)
    for index, subject in enumerate(subjects, 1):
        if not isinstance(subject, dict):
            raise ValidationError(f"{label}.subject_bindings[{index}]: must be an object")
        item = f"{label}.subject_bindings[{index}]"
        validate_ref(subject.get("identity_ref"), sources, f"{item}.identity_ref")
        if "variant_ref" in subject:
            validate_ref(subject.get("variant_ref"), sources, f"{item}.variant_ref")
        text(subject, "role", item)
    text(record, "test_question", label)
    nonempty_list(record, "stable_visual_rules", label)
    if record.get("lookdev_axis") == "high_pressure_scene":
        refs = nonempty_list(record, "story_context_refs", label)
        for index, ref in enumerate(refs, 1):
            validate_ref(ref, sources, f"{label}.story_context_refs[{index}]")


def validate_records(
    records: list[dict[str, Any]], sources: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    sources = sources or {}
    identifiers: set[str] = set()
    for index, record in enumerate(records, 1):
        label = f"spec[{index}]"
        spec_id = text(record, "spec_id", label)
        if spec_id in identifiers:
            raise ValidationError(f"{label}: duplicate spec_id {spec_id}")
        identifiers.add(spec_id)
        purpose = record.get("purpose")
        if purpose not in PURPOSES:
            raise ValidationError(
                f"{label}: invalid purpose {purpose!r}; "
                f"use one of {', '.join(sorted(PURPOSES))}"
            )
        if record.get("status") not in {"candidate", "accepted"}:
            raise ValidationError(f"{label}: status must be candidate or accepted")
        leaked = sorted(vendor_field_paths(record))
        if leaked:
            raise ValidationError(f"{label}: provider execution fields are forbidden: {', '.join(leaked)}")
        prompt = text(record, "generic_prompt", label)
        if HASH_RE.search(prompt) or "<sha256>" in prompt:
            raise ValidationError(f"{label}: generic_prompt leaks internal hashes")
        engine_syntax = ENGINE_SYNTAX_RE.search(prompt)
        if engine_syntax:
            raise ValidationError(
                f"{label}: generic_prompt carries engine-specific syntax "
                f"{engine_syntax.group(0).strip()!r}; keep it in a provider adapter"
            )
        validate_reference_bindings(record, sources, label)
        if purpose == "lookdev_frame":
            validate_lookdev_spec(record, sources, label)
        else:
            validate_asset_spec(record, sources, label)
    return {
        "status": "valid",
        "specs": len(records),
        "sources": len(sources),
        "checks": [
            "unique_ids",
            "accepted_bindings",
            # Named for what it does: every reference names a snapshot this
            # file declares. Whether that record exists in the target artifact
            # is not knowable here -- the checker is handed this file only.
            "source_declaration",
            "reference_slots",
            "prompt_hygiene",
        ],
    }


def validate_file(path: str | Path) -> dict[str, Any]:
    sources, records = load_jsonl(path)
    return validate_records(records, sources)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs")
    args = parser.parse_args()
    try:
        result = validate_file(args.specs)
    except (OSError, ValidationError) as exc:
        print(f"image prompt check failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
