#!/usr/bin/env python3
"""Check the storyboard invariants that are pure bookkeeping.

`SHT-16` is arithmetic over shot durations, `SHT-17` is a structural claim about
which boundary a keyframe freezes, and a boundary entry that only points back at
an earlier shot states no fact at all. None of these needs a reading of the
drama, so leaving them to a reviewer spends judgment on work a script does
exactly. Everything requiring judgment stays in the reference documents.

The script reads accepted creator files and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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

SCHEMA_VERSION = "1.0.0"
BOUNDARY_FIELD = {"start": "/start_boundary", "end": "/end_boundary"}
PLACEHOLDER = re.compile(r"^<.*>$")

# A boundary entry is read on its own by whoever owns the next stage, so an
# entry whose whole content points back at another shot leaves that field empty
# in practice. Only a wholly referential entry is a defect: "站在柜台东侧（与上一
# 镜相同）" still says where the character is.
BACK_REFERENCE = re.compile(
    r"^(同上一?镜?|同前一?镜?|同上镜|与上一?镜相同|照旧"
    r"|(位置|朝向|目光|手部|状态|持物)?(保持)?不变|无变化"
    r"|same as (above|before|previous( shot)?)|unchanged|no change)$",
    re.IGNORECASE,
)
BACK_REFERENCE_TRIM = " \t　。，、；：（）()【】〔〕「」『』\"'·-—~…!！?？"


class CheckError(ValueError):
    """The inputs cannot be checked at all, as opposed to failing a check."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CheckError(f"unreadable JSON: {path}") from error


def _load_jsonl(path: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return the file's ``sources`` declaration and its data records.

    A leading ``{"record_type": "sources"}`` header declares the upstream
    snapshots of the whole file; it is a declaration, not a data record, so it
    is kept out of the returned list.
    """

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
    sources = load_sources(records)
    if records and records[0].get("record_type") == SOURCES_RECORD_TYPE:
        records = records[1:]
    return sources, records


def _is_template(value: Any) -> bool:
    """Template files ship placeholder strings; they are not project data."""

    return isinstance(value, str) and bool(PLACEHOLDER.match(value.strip()))


def _finding(code: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **detail}


def _duration_of(shot: dict[str, Any]) -> float | None:
    value = shot.get("duration_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _ref_finding(finding: RefFinding, **detail: Any) -> dict[str, Any]:
    return _finding(finding.code, finding.detail, location=finding.location, **detail)


def _covered_shot_ids(coverage: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Return the shot IDs the coverage claims, and any unusable reference."""

    ids: list[str] = []
    findings: list[dict[str, Any]] = []
    sources = load_sources(coverage)
    dispositions = coverage.get("dispositions")
    if not isinstance(dispositions, list):
        return ids, findings
    for index, disposition in enumerate(dispositions):
        if not isinstance(disposition, dict):
            continue
        refs = disposition.get("shot_refs")
        if not isinstance(refs, list):
            continue
        for position, ref in enumerate(refs):
            resolved, finding = resolve_ref(
                ref, sources, f"/dispositions/{index}/shot_refs/{position}"
            )
            if finding is not None:
                findings.append(_ref_finding(finding))
                continue
            if resolved is not None and resolved.record_id is not None:
                ids.append(resolved.record_id)
    return ids, findings


def check_episode_duration(
    coverage: dict[str, Any],
    shots: list[dict[str, Any]],
    target_seconds: float | None,
) -> list[dict[str, Any]]:
    """SHT-16: the total is arithmetic, and no shot may leave it silently."""

    covered_ids, findings = _covered_shot_ids(coverage)
    duration = coverage.get("episode_duration")
    if not isinstance(duration, dict):
        findings.append(_finding("SHT16_RECORD_MISSING", "coverage carries no episode_duration"))
        return findings

    by_id = {
        shot["shot_id"]: shot
        for shot in shots
        if isinstance(shot.get("shot_id"), str)
    }
    counted = duration.get("counted_shot_ids")
    unresolved = duration.get("unresolved_durations")
    if not isinstance(counted, list) or not isinstance(unresolved, list):
        findings.append(
            _finding(
                "SHT16_RECORD_INCOMPLETE",
                "episode_duration needs counted_shot_ids and unresolved_durations",
            )
        )
        return findings
    counted_ids = [value for value in counted if isinstance(value, str)]
    unresolved_ids = [value for value in unresolved if isinstance(value, str)]

    overlap = sorted(set(counted_ids) & set(unresolved_ids))
    if overlap:
        findings.append(
            _finding(
                "SHT16_SHOT_COUNTED_AND_UNRESOLVED",
                "a shot cannot be both counted and unresolved",
                shot_ids=overlap,
            )
        )

    # A shot listed twice is summed twice, and set arithmetic above never sees
    # it. One copy-paste inflates the episode by a whole shot, and the checker
    # then confirms the inflated figure in the coverage file as correct.
    duplicates = sorted(
        {shot_id for shot_id in counted_ids + unresolved_ids
         if (counted_ids + unresolved_ids).count(shot_id) > 1}
    )
    if duplicates:
        findings.append(
            _finding(
                "SHT16_SHOT_LISTED_TWICE",
                "episode_duration names the same shot more than once",
                shot_ids=duplicates,
            )
        )

    accounted = set(counted_ids) | set(unresolved_ids)
    missing = sorted(set(covered_ids) - accounted)
    if missing:
        findings.append(
            _finding(
                "SHT16_SHOT_LEFT_THE_TOTAL",
                "coverage lists shots that neither contribute nor are suspended",
                shot_ids=missing,
            )
        )
    # `covered_ids` comes from the coverage document itself, so the check above
    # only asks whether coverage agrees with coverage. The episode's real shot
    # list is `shots.jsonl`; a shot dropped from both the dispositions and the
    # total is invisible to every check that reads only the one file.
    unaccounted = sorted(set(by_id) - accounted)
    if unaccounted:
        findings.append(
            _finding(
                "SHT16_EPISODE_SHOT_LEFT_THE_TOTAL",
                "the shot file carries shots that episode_duration neither "
                "counts nor suspends",
                shot_ids=unaccounted,
            )
        )
    unknown = sorted(accounted - set(by_id))
    if unknown:
        findings.append(
            _finding(
                "SHT16_SHOT_UNRESOLVABLE",
                "episode_duration names shots that are not in the shot file",
                shot_ids=unknown,
            )
        )

    total = 0.0
    for shot_id in sorted(set(counted_ids)):
        shot = by_id.get(shot_id)
        if shot is None:
            continue
        seconds = _duration_of(shot)
        if seconds is None:
            findings.append(
                _finding(
                    "SHT16_COUNTED_SHOT_HAS_NO_DURATION",
                    "a counted shot carries no numeric duration_seconds",
                    shot_id=shot_id,
                )
            )
            continue
        total += seconds
    for shot_id in unresolved_ids:
        shot = by_id.get(shot_id)
        if shot is not None and _duration_of(shot) is not None:
            findings.append(
                _finding(
                    "SHT16_SUSPENDED_SHOT_HAS_A_DURATION",
                    "a shot listed as unresolved already carries a duration",
                    shot_id=shot_id,
                )
            )

    stated = duration.get("shot_seconds_total")
    if isinstance(stated, bool) or not isinstance(stated, (int, float)):
        findings.append(
            _finding("SHT16_TOTAL_MISSING", "shot_seconds_total is not a number")
        )
    elif abs(float(stated) - total) > 1e-6:
        findings.append(
            _finding(
                "SHT16_TOTAL_IS_NOT_THE_SUM",
                "shot_seconds_total does not equal the sum of the counted shots",
                stated=float(stated),
                computed=round(total, 6),
            )
        )

    if target_seconds is None:
        if duration.get("disposition") not in (None, "no_target_declared"):
            findings.append(
                _finding(
                    "SHT16_DISPOSITION_CLAIMS_A_TARGET",
                    "no target is declared, so the disposition cannot judge one; "
                    "leave it unset or use no_target_declared",
                )
            )
        return findings

    delta = duration.get("delta_seconds")
    expected = total - target_seconds
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        findings.append(
            _finding("SHT16_DELTA_MISSING", "a declared target needs a signed delta")
        )
    elif abs(float(delta) - expected) > 1e-6:
        findings.append(
            _finding(
                "SHT16_DELTA_IS_WRONG",
                "delta_seconds does not equal total minus target",
                stated=float(delta),
                computed=round(expected, 6),
            )
        )
    allowed = ("within_creator_tolerance", "creator_accepted_overrun", "to_revise")
    if duration.get("disposition") not in set(allowed):
        findings.append(
            _finding(
                "SHT16_DISPOSITION_MISSING",
                "a declared target needs a disposition for its delta; "
                f"use one of {', '.join(allowed)}",
                stated=duration.get("disposition"),
            )
        )
    return findings


def check_keyframe_boundaries(
    keyframes: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """SHT-17: say which boundary is frozen, and bind that shot's field."""

    declared = sources or {}
    findings: list[dict[str, Any]] = []
    shot_ids = {
        shot["shot_id"] for shot in shots if isinstance(shot.get("shot_id"), str)
    }
    seen: dict[tuple[str, str], str] = {}
    for keyframe in keyframes:
        keyframe_id = keyframe.get("keyframe_id")
        if not isinstance(keyframe_id, str):
            findings.append(
                _finding("SHT17_KEYFRAME_HAS_NO_ID", "a keyframe record has no id")
            )
            continue
        role = keyframe.get("boundary_role")
        if role not in BOUNDARY_FIELD:
            findings.append(
                _finding(
                    "SHT17_BOUNDARY_ROLE_MISSING",
                    "boundary_role must be start or end",
                    keyframe_id=keyframe_id,
                )
            )
            continue
        ref = keyframe.get("boundary_ref")
        if not isinstance(ref, dict):
            findings.append(
                _finding(
                    "SHT17_BOUNDARY_REF_MISSING",
                    "a keyframe must bind the boundary it projects",
                    keyframe_id=keyframe_id,
                )
            )
            continue
        resolved, ref_finding = resolve_ref(ref, declared, f"{keyframe_id}/boundary_ref")
        if ref_finding is not None or resolved is None:
            if ref_finding is not None:
                findings.append(_ref_finding(ref_finding, keyframe_id=keyframe_id))
            continue
        if resolved.field != BOUNDARY_FIELD[role]:
            findings.append(
                _finding(
                    "SHT17_BOUNDARY_REF_DISAGREES_WITH_ROLE",
                    "boundary_ref.field does not match the declared role",
                    keyframe_id=keyframe_id,
                    role=role,
                    field=resolved.field,
                )
            )
        shot_id = resolved.record_id
        if shot_id is None or shot_id not in shot_ids:
            findings.append(
                _finding(
                    "SHT17_BOUNDARY_REF_UNRESOLVABLE",
                    "boundary_ref does not resolve to a shot in the shot file",
                    keyframe_id=keyframe_id,
                    record_id=shot_id,
                )
            )
            continue
        previous = seen.get((shot_id, role))
        if previous is not None:
            findings.append(
                _finding(
                    "SHT17_DUPLICATE_BOUNDARY_KEYFRAME",
                    "one shot boundary cannot have two keyframes",
                    shot_id=shot_id,
                    role=role,
                    keyframe_ids=[previous, keyframe_id],
                )
            )
            continue
        seen[(shot_id, role)] = keyframe_id
    return findings


def check_boundary_entries(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SHT-05: every boundary entry states a fact readable without the last shot."""

    findings: list[dict[str, Any]] = []
    for shot in shots:
        shot_id = shot.get("shot_id")
        for boundary in ("start_boundary", "end_boundary"):
            fields = shot.get(boundary)
            if isinstance(fields, str):
                # A boundary written as one string carries the same defect. The
                # empty key keeps the reported pointer at /<boundary>/0.
                fields = {"": [fields]}
            if not isinstance(fields, dict):
                continue
            for name, entries in sorted(fields.items()):
                if isinstance(entries, str):
                    entries = [entries]
                if not isinstance(entries, list):
                    continue
                for index, entry in enumerate(entries):
                    if not isinstance(entry, str) or _is_template(entry):
                        continue
                    if BACK_REFERENCE.match(entry.strip(BACK_REFERENCE_TRIM)):
                        findings.append(
                            _finding(
                                "SHT05_BOUNDARY_ENTRY_IS_A_BACK_REFERENCE",
                                "a boundary entry points back instead of stating the fact",
                                shot_id=shot_id,
                                location=f"/{boundary}/{name}/{index}" if name else f"/{boundary}/{index}",
                                entry=entry,
                            )
                        )
    return findings


def _declared_target(project: Path | None) -> float | None:
    if project is None:
        return None
    document = _load_json(project)
    if not isinstance(document, dict):
        raise CheckError("project file must be a JSON object")
    value = document.get("format", {}).get("target_seconds_per_episode")
    if value is None or _is_template(value):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CheckError("target_seconds_per_episode must be a number or null")
    return float(value)


# A screenplay block that no shot claims is a block nobody will film. A block
# two shots claim is a block the edit will show twice. Shots bind to the
# screenplay through the index -- stable block IDs -- not through the prose,
# because the prose is edited constantly and the IDs survive that.
def screenplay_block_ids(index_path: Path) -> tuple[list[str], int]:
    """The indexed blocks, and how much of the screenplay stayed unclassified.

    A line the index could not classify is not a block, so it is invisible to
    every check below: no shot can claim it, and nothing reports that it is
    missing. Coverage measured against a partial index is not coverage of the
    screenplay, so the count comes back with the IDs.
    """

    _sources, records = _load_jsonl(index_path)
    blocks = [
        str(record["block_id"])
        for record in records
        if record.get("record_type") == "block" and isinstance(record.get("block_id"), str)
    ]
    issues = sum(
        1 for record in records if record.get("record_type") == "source_issue"
    )
    return blocks, issues


# SKILL.md gives four dispositions, and three of them are not "one shot claims
# this block": material can be intentionally repeated, omitted for a reason, or
# present only so the reader understands the scene. A claim check that knows
# nothing about them can only ever demand exactly one shot per block, which is
# why marking a block `nonvisual_context` as the workflow instructs used to fail.
DISPOSITIONS = {
    "covered",
    "intentional_repeat",
    "omitted_with_reason",
    "nonvisual_context",
}
# Two of them are a decision, not an observation, so they have to carry the
# reason the decision was made.
DISPOSITIONS_NEEDING_A_REASON = {"intentional_repeat", "omitted_with_reason"}
# Only these expect shots. The other two are records of material that is
# deliberately not on screen.
DISPOSITIONS_EXPECTING_SHOTS = {"covered", "intentional_repeat"}


def block_dispositions(
    coverage: dict[str, Any], wanted: set[str]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Read the disposition table, and check it against the screenplay index.

    Nothing validated this table before: rows could be dropped, given a status
    no rule defines, or point at blocks that do not exist, and the file still
    passed. Coverage and `shots.jsonl` were two independent claims about the
    same fact with no reconciliation between them.
    """

    findings: list[dict[str, Any]] = []
    rows = coverage.get("dispositions")
    if not isinstance(rows, list):
        return {}, [
            _finding("SHT01_DISPOSITIONS_MISSING", "coverage carries no dispositions")
        ]

    status_of: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            findings.append(
                _finding(
                    "SHT01_DISPOSITION_MALFORMED",
                    "a disposition row is not an object",
                    position=index,
                )
            )
            continue
        block_id = row.get("block_id")
        status = row.get("status")
        if not isinstance(block_id, str) or not block_id:
            findings.append(
                _finding(
                    "SHT01_DISPOSITION_MALFORMED",
                    "a disposition row names no block",
                    position=index,
                )
            )
            continue
        if status not in DISPOSITIONS:
            findings.append(
                _finding(
                    "SHT01_DISPOSITION_UNKNOWN",
                    "a disposition must be one of the four the workflow defines",
                    block_id=block_id,
                    status=status,
                )
            )
            continue
        if block_id in status_of:
            findings.append(
                _finding(
                    "SHT01_DISPOSITION_REPEATED",
                    "a block carries more than one disposition",
                    block_id=block_id,
                )
            )
            continue
        if block_id not in wanted:
            findings.append(
                _finding(
                    "SHT01_DISPOSITION_NOT_IN_SCREENPLAY",
                    "a disposition names a block that is not in the screenplay index",
                    block_id=block_id,
                )
            )
            continue
        if status in DISPOSITIONS_NEEDING_A_REASON and not str(
            row.get("reason") or ""
        ).strip():
            findings.append(
                _finding(
                    "SHT01_DISPOSITION_HAS_NO_REASON",
                    "repeating or omitting material is a decision and must say why",
                    block_id=block_id,
                    status=status,
                )
            )
        status_of[block_id] = status

    undecided = sorted(wanted - set(status_of))
    if undecided:
        findings.append(
            _finding(
                "SHT01_BLOCK_HAS_NO_DISPOSITION",
                "every production-relevant block must carry a disposition",
                block_ids=undecided,
            )
        )
    return status_of, findings


def check_screenplay_coverage(
    shots: list[dict[str, Any]],
    shot_sources: dict[str, Any],
    index_path: Path | None,
    coverage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if index_path is None:
        return []
    try:
        wanted, unclassified = screenplay_block_ids(index_path)
    except OSError as error:
        raise CheckError(f"screenplay index cannot be read: {error}") from error

    status_of: dict[str, str] = {}
    disposition_findings: list[dict[str, Any]] = []
    if unclassified:
        disposition_findings.append(
            _finding(
                "SHT01_SCREENPLAY_IS_NOT_FULLY_INDEXED",
                "the screenplay index left lines unclassified, so this coverage "
                "check cannot see all of the screenplay",
                source_issue_count=unclassified,
            )
        )
    if coverage is not None:
        status_of, disposition_findings2 = block_dispositions(coverage, set(wanted))
        disposition_findings.extend(disposition_findings2)

    claims: dict[str, list[str]] = {block_id: [] for block_id in wanted}
    unknown: list[dict[str, Any]] = []
    for shot in shots:
        shot_id = shot.get("shot_id")
        for reference in shot.get("source_refs") or []:
            resolved, _defect = resolve_ref(reference, shot_sources, "source_refs")
            if resolved is None or resolved.record_id is None:
                continue
            record_id = str(resolved.record_id)
            if record_id in claims:
                claims[record_id].append(shot_id)
            elif resolved.artifact.endswith("screenplay-index.jsonl"):
                unknown.append({"shot_id": shot_id, "block_id": record_id})

    findings = list(disposition_findings)
    for block_id, owners in claims.items():
        # With no coverage file to read, every block is treated as `covered`:
        # that is the old behaviour, and the only safe assumption when the
        # creator has not said otherwise.
        status = status_of.get(block_id, "covered") if coverage is not None else "covered"
        if not owners:
            if status in DISPOSITIONS_EXPECTING_SHOTS:
                findings.append(
                    _finding(
                        "SHT01_BLOCK_UNCLAIMED",
                        "no shot claims this screenplay block",
                        block_id=block_id,
                        status=status,
                    )
                )
        elif status not in DISPOSITIONS_EXPECTING_SHOTS:
            findings.append(
                _finding(
                    "SHT01_BLOCK_IS_ON_SCREEN_ANYWAY",
                    "a block recorded as not filmed is claimed by a shot",
                    block_id=block_id,
                    status=status,
                    shot_ids=owners,
                )
            )
        elif len(owners) > 1 and status != "intentional_repeat":
            findings.append(
                _finding(
                    "SHT01_BLOCK_CLAIMED_TWICE",
                    "more than one shot claims the same screenplay block",
                    block_id=block_id,
                    shot_ids=owners,
                )
            )
    for stray in unknown:
        findings.append(
            _finding(
                "SHT01_BLOCK_NOT_IN_SCREENPLAY",
                "a shot claims a block that is not in the screenplay index",
                **stray,
            )
        )
    return findings


def check(
    coverage_path: Path,
    shots_path: Path,
    keyframes_path: Path | None,
    project_path: Path | None,
    screenplay_index_path: Path | None = None,
) -> dict[str, Any]:
    coverage = _load_json(coverage_path)
    if not isinstance(coverage, dict):
        raise CheckError("coverage must be a JSON object")
    shot_sources, shots = _load_jsonl(shots_path)
    findings = check_episode_duration(coverage, shots, _declared_target(project_path))
    findings.extend(check_boundary_entries(shots))
    findings.extend(
        check_screenplay_coverage(
            shots, shot_sources, screenplay_index_path, coverage
        )
    )
    keyframes: list[dict[str, Any]] | None = None
    if keyframes_path is not None:
        keyframe_sources, keyframes = _load_jsonl(keyframes_path)
        findings.extend(check_keyframe_boundaries(keyframes, shots, keyframe_sources))
    return {
        "schema_version": SCHEMA_VERSION,
        "episode_id": coverage.get("episode_id"),
        "checked": {
            "shots": len(shots),
            "keyframes": None if keyframes is None else len(keyframes),
        },
        "findings": findings,
        "status": "pass" if not findings else "fail",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check storyboard duration arithmetic and keyframe boundary roles."
    )
    parser.add_argument("coverage", type=Path, help="the episode coverage JSON")
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--keyframes", type=Path)
    parser.add_argument(
        "--screenplay-index",
        type=Path,
        help="screenplay-index.jsonl, so every block is claimed by exactly one shot",
    )
    parser.add_argument(
        "--project",
        type=Path,
        help="short-drama.json, so a declared per-episode target is compared",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = check(
            args.coverage, args.shots, args.keyframes, args.project, args.screenplay_index
        )
    except CheckError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
