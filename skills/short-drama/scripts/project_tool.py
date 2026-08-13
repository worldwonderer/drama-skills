#!/usr/bin/env python3
"""Deterministic project, recovery, and delivery operations.

Creative judgment stays in the skill documents and creator-authored files. This
module owns only filesystem integrity: five independent lifecycle axes,
recoverable multi-file publication, creator-safe status, and text/JSON delivery.
It uses no network or media service.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import stat
import sys
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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

PROJECT_FILE = "short-drama.json"
# A machine-path token is the leading marker plus the rest of the path.
# Delivery scans the token form, so an exception must quote a whole path to
# release it. Declarations are checked against the complete form, which
# requires at least one character after the marker, so a marker on its own can
# never be declared and act as a wildcard over every path sharing it.
# A path token ends at whitespace or at a character that cannot continue a
# path: quotes and braces (so a path inside a JSON string is captured without
# its delimiters) and CJK punctuation (so prose that ends a sentence right
# after a path is captured without the full stop).
_PATH_TAIL = r"[^\s\"'`,;<>)\]}，。；：、！？（）【】「」]"
# The leading guard excludes only ASCII path-continuation characters, so a path
# written straight after a CJK character — the normal case in this product —
# is still detected, while a URL's own path is not double-reported.
MACHINE_PATH_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9_.\-])/(?:Users|home|private|var|tmp)/{_PATH_TAIL}*"
    rf"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]{_PATH_TAIL}*"
)
MACHINE_PATH_COMPLETE_RE = re.compile(
    rf"(?<![A-Za-z0-9_.\-])/(?:Users|home|private|var|tmp)/{_PATH_TAIL}+"
    rf"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]{_PATH_TAIL}+"
)
# On-screen text is a single displayed string, never a document. Bounding it
# stops a whole-file declaration from acting as a blanket release.
MAX_TEXT_EXCEPTION_LENGTH = 200
STATE_FILE = Path(".short-drama/state.json")
OPERATIONS_DIR = Path(".short-drama")
ABSENT_HASH: None = None
# One literal owns the project roots; every other view of them is derived.
# Spelling the set out per view is how an unrelated role ends up in PROJECT_DIRS —
# so `init` creates the directory — but missing from the alias table, so
# `_validate_publication_layout` then refuses every write into it.
CANONICAL_ROOTS = {
    "inputs": "输入",
    "development": "项目开发",
    "bible": "设定集",
    "episodes": "剧集",
    "delivery": "交付",
    "creator-decisions": "创作者决策",
    "reviews": "审查",
}
# Projects created before the Chinese layout name each directory after its role,
# so the legacy view is the identity map.
LEGACY_ROOTS = {role: role for role in CANONICAL_ROOTS}
ROOT_ROLE_ALIASES: dict[str, str] = {
    name.casefold(): role
    for roots in (CANONICAL_ROOTS, LEGACY_ROOTS)
    for role, name in roots.items()
}
LAYOUT_MODES = frozenset({"auto", "canonical", "legacy"})
# Bounded retries for the unserialized create-or-open of the lock file itself.
_LOCK_OPEN_ATTEMPTS = 8
LAYOUT_PINNING_ROLES = frozenset(
    {"development", "bible", "episodes", "delivery", "creator-decisions", "reviews"}
)
PROJECT_DIRS = (
    *CANONICAL_ROOTS.values(),
    ".short-drama/transactions",
    ".short-drama/accepted-snapshots",
    ".short-drama/conflicts",
    ".short-drama/locks",
    ".short-drama/tmp",
)
# Episode directories are the unit the delivery completeness gate enumerates by
# prefix, so a path whose episode segment does not match this form is accepted,
# then silently skipped by _episode_coverage and never reconciled. Exactly one
# spelling per episode number is therefore required: three digits up to EP999,
# then unpadded. EP0001 is refused because it would be a second, invisible
# spelling of EP001 rather than a distinct episode.
EPISODE_ID_RE = re.compile(r"EP(?:[0-9]{3}|[1-9][0-9]{3,})")
SCENE_ID_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9])SC(?:[0-9]{3}|[1-9][0-9]{3,})(?![A-Z0-9])"
)
WINDOWS_FORBIDDEN_PATH_CHARACTERS = frozenset('<>:"|?*')
WINDOWS_RESERVED_PATH_STEMS = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)
# Roots no stage may publish into, each with the reason a creator needs. Matched
# case-insensitively: this suite is developed on case-insensitive filesystems,
# where `Inputs/x.md` and `inputs/x.md` are the same file on disk, so a
# case-sensitive guard is not a guard at all.
PROTECTED_PUBLISH_ROLE_REASONS = {
    "inputs": "creator inputs are immutable publication sources",
    "delivery": "the delivery tree is written by the packaging gate, not by publication",
}
PROTECTED_PUBLISH_ROOTS = {
    name.casefold(): reason
    for role, reason in PROTECTED_PUBLISH_ROLE_REASONS.items()
    for name in (CANONICAL_ROOTS[role], LEGACY_ROOTS[role])
} | {".short-drama": "operational state cannot be a publication target"}
# Roots a stage may publish into. Anything else needs an explicit opt-in, so an
# ad-hoc creator file stays possible but never silent: a typo like `epsiodes/`
# otherwise builds a parallel tree that `status` never reports.
PUBLISHABLE_ROOT_ROLES = frozenset(
    {"development", "bible", "episodes", "creator-decisions", "reviews"}
)
# Both spellings, Chinese first — this is the "expected one of …" error text.
PUBLISHABLE_ROOTS = tuple(
    roots[role]
    for roots in (CANONICAL_ROOTS, LEGACY_ROOTS)
    for role in CANONICAL_ROOTS
    if role in PUBLISHABLE_ROOT_ROLES
)
# Declared artifact -> owning skill, transcribed from each stage SKILL.md's
# owned-output list and the single-owner registry in
# references/contract-and-ownership.md. Keys are casefolded; see
# _expected_path_owner.
#
# General artifacts are deliberately keyed on exact declared names rather than
# stage-directory prefixes. An episode directory holds artifacts from four
# skills, and a creator may legitimately place their own file beside them.
# Only the two explicitly declared per-scene families below use bounded
# subdirectory ownership; everything else unnamed stays owner-unconstrained.
DECLARED_PROJECT_ARTIFACT_OWNERS: dict[str, str] = {
    "development/creative-brief.md": "short-drama-develop",
    "development/story-engine.md": "short-drama-develop",
    "development/director-brief.md": "short-drama-develop",
    "development/adaptation-map.jsonl": "short-drama-develop",
    "development/series-arc.json": "short-drama-develop",
    "development/episode-intake-index.json": "short-drama-develop",
    "development/episode-map.jsonl": "short-drama-develop",
    "development/lookdev-image-prompt-specs.jsonl": "short-drama-image-prompts",
    "development/lookdev-prompts.md": "short-drama-image-prompts",
    # Source analysis is a separate layer from the adaptation contract: analysis
    # can be overturned, an accepted contract cannot.
    "development/source-analysis/_index.json": "short-drama-novel-analyze",
    "development/source-analysis/_progress.md": "short-drama-novel-analyze",
    "development/source-analysis/triage.md": "short-drama-novel-analyze",
    "development/source-analysis/story-units.md": "short-drama-novel-analyze",
    "development/source-analysis/rhythm-and-emotion.md": "short-drama-novel-analyze",
    "development/source-analysis/characters.md": "short-drama-novel-analyze",
    "development/source-analysis/world.md": "short-drama-novel-analyze",
    "development/source-analysis/adaptation-value.md": "short-drama-novel-analyze",
    "development/source-analysis/episode-candidates.jsonl": "short-drama-novel-analyze",
    # Cross-episode identity ledgers. Every skill that names these reads them;
    # `short-drama-assets/SKILL.md:130` is the only declared writer.
    "bible/characters.jsonl": "short-drama-assets",
    "bible/looks.jsonl": "short-drama-assets",
    "bible/locations.jsonl": "short-drama-assets",
    "bible/location-views.jsonl": "short-drama-assets",
    "bible/props.jsonl": "short-drama-assets",
    "bible/prop-states.jsonl": "short-drama-assets",
    # A casting sheet rendered from accepted voice_direction, owned by the same
    # stage that owns the identity it projects. Timbre itself is carried by a
    # reference recording bound in the character record; this file is the
    # derived text a voice director or a cloning operator reads.
    "bible/voice-casting.md": "short-drama-assets",
}
DECLARED_PROJECT_ARTIFACT_FAMILY_OWNERS: dict[str, str] = {
    # Chapter filenames are generated, but the contract declares exactly one
    # Markdown layer below this directory. Claim that bounded family without
    # taking ownership of arbitrary nested or differently typed creator files.
    "development/source-analysis/chapters": "short-drama-novel-analyze",
}
# Same, for the path below `episodes/<EP>/`.
DECLARED_EPISODE_ARTIFACT_OWNERS: dict[str, str] = {
    "episode-card.json": "short-drama-write",
    "beats.jsonl": "short-drama-write",
    "screenplay.md": "short-drama-write",
    "screenplay-index.jsonl": "short-drama-write",
    "voice-record-sheet.jsonl": "short-drama-write",
    "assets/occurrences.jsonl": "short-drama-assets",
    "assets/decisions.jsonl": "short-drama-assets",
    "assets/continuity.jsonl": "short-drama-assets",
    "assets/image-prompt-specs.jsonl": "short-drama-image-prompts",
    "assets/image-prompts.md": "short-drama-image-prompts",
    "storyboard/coverage.json": "short-drama-storyboard",
    "storyboard/shots.jsonl": "short-drama-storyboard",
    "storyboard/keyframes.jsonl": "short-drama-storyboard",
    "storyboard/keyframe-prompts.md": "short-drama-storyboard",
    "storyboard/motion-specs.jsonl": "short-drama-video-prompts",
    "storyboard/delivery-containers.jsonl": "short-drama-video-prompts",
    "storyboard/video-prompts.md": "short-drama-video-prompts",
}
# These two optional layers have one independently accepted file per scene, so
# their `<SC>.jsonl` members are a safe owner namespace: unlike the episode or
# storyboard root, that declared filename carries no creator-defined or
# cross-skill artifact. Ownership is claimed for those members only, not for the
# directory as a whole — a `.json`, `.md`, or more deeply nested file beside them
# stays owner-unconstrained like any other undeclared path, because the contract
# names `<SC>.jsonl` and nothing else.
DECLARED_EPISODE_ARTIFACT_FAMILY_OWNERS: dict[str, str] = {
    "storyboard/coverage-auditions": "short-drama-storyboard",
    "storyboard/scene-visual-plans": "short-drama-storyboard",
}

LIFECYCLE_STATES: dict[str, tuple[str, ...]] = {
    "build_state": ("absent", "in_progress", "materialized", "stale", "failed"),
    "validation_state": ("not_run", "pass", "pass_with_warnings", "fail"),
    "creator_acceptance": ("not_requested", "pending", "accepted", "rejected"),
    "independent_review": (
        "not_requested",
        "provisional",
        "approve",
        "approve_with_notes",
        "revise",
    ),
    "delivery_gate": ("not_evaluated", "blocked", "ready", "delivered"),
}
LIFECYCLE_DEFAULTS = {
    "build_state": "absent",
    "validation_state": "not_run",
    "creator_acceptance": "not_requested",
    "independent_review": "not_requested",
    "delivery_gate": "not_evaluated",
}
DELIVERY_SUFFIXES = {".md", ".json", ".jsonl"}
FaultInjector = Callable[[str, dict[str, object]], None]


class TransactionError(RuntimeError):
    """Base class for a recoverable transaction failure."""


class TransactionConflictError(TransactionError):
    """A live file no longer matches either transaction-owned version."""


class StaleReadSetError(TransactionError):
    """An input changed after the transaction captured its read set."""


class RecoveryMaterialError(TransactionError):
    """A required immutable candidate or prior snapshot is unavailable."""


class PackageBlockedError(RuntimeError):
    """Delivery policy rejected one or more selected artifacts."""


class NonPortablePathError(ValueError):
    """A path spelling Win32 rejects, or one that aliases another spelling."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    # Windows cannot open a directory as a regular file for fsync. The file
    # itself is flushed before os.replace; POSIX additionally persists the
    # parent-directory entry here.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    encoded = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_bytes(path, encoded)


def _append_wal(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _write_marker(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != b"committed\n":
            raise TransactionError(f"invalid commit marker: {path.name}")
        return
    # The marker becomes visible only after its complete bytes are durable. A
    # crash while writing the temporary therefore cannot masquerade as COMMIT.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(b"committed\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def default_lifecycle() -> dict[str, str]:
    return dict(LIFECYCLE_DEFAULTS)


def apply_lifecycle_changes(
    current: Mapping[str, Any], changes: Mapping[str, Any]
) -> dict[str, Any]:
    unknown = sorted(set(changes) - set(LIFECYCLE_STATES))
    if unknown:
        raise ValueError(f"unknown lifecycle axes: {', '.join(unknown)}")
    result = dict(current)
    for axis, default in LIFECYCLE_DEFAULTS.items():
        value = result.get(axis, default)
        if value not in LIFECYCLE_STATES[axis]:
            raise ValueError(f"invalid {axis}: {value!r}")
        result[axis] = value
    for axis, value in changes.items():
        if value not in LIFECYCLE_STATES[axis]:
            raise ValueError(f"invalid {axis}: {value!r}")
        result[axis] = value
    return result


# Creator-facing artifacts follow the project language; prompt bodies follow
# prompt_language, which defaults to English because most image, video and voice
# generators handle English prompt text most reliably. Keeping them as two
# fields is the point: changing the language a creator reads must never silently
# change the language a generator is asked to render, and vice versa.
DEFAULT_PROMPT_LANGUAGE = "en"
# A permissive BCP 47 shape. This validates form, not registry membership: a
# malformed tag is worth refusing at init, because it then propagates into every
# artifact that claims to follow it, and nothing downstream re-checks it.
LANGUAGE_TAG_RE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")


def normalize_language_tag(value: str, *, field: str) -> str:
    tag = value.strip()
    if not tag:
        raise ValueError(f"{field} must not be empty")
    if LANGUAGE_TAG_RE.fullmatch(tag) is None:
        raise ValueError(f"{field} is not a well-formed language tag: {value!r}")
    return tag


def project_languages(project: Mapping[str, Any]) -> dict[str, str]:
    """Resolve both output languages from one project record.

    Every reader goes through here rather than reaching into the mapping, so a
    project written before `prompt_language` existed resolves to the documented
    default in exactly one place instead of once per call site.
    """

    format_block = project.get("format")
    prompt_language = (
        format_block.get("prompt_language")
        if isinstance(format_block, Mapping)
        else None
    )
    return {
        "language": str(project.get("language") or "zh-CN"),
        "prompt_language": str(prompt_language or DEFAULT_PROMPT_LANGUAGE),
    }


def initialize_project(
    path: Path,
    *,
    title: str,
    language: str,
    aspect_ratio: str,
    prompt_language: str = DEFAULT_PROMPT_LANGUAGE,
    suite_root: Path | None = None,
) -> dict[str, Any]:
    language = normalize_language_tag(language, field="language")
    prompt_language = normalize_language_tag(prompt_language, field="prompt_language")
    root = path.expanduser().resolve()
    project_path = root / PROJECT_FILE
    if project_path.exists():
        raise FileExistsError(f"project already exists: {project_path}")

    root.mkdir(parents=True, exist_ok=True)
    for relative in PROJECT_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)

    core = suite_root or Path(__file__).resolve().parents[1]
    manifest = json.loads((core / "suite-manifest.json").read_text(encoding="utf-8"))
    template_path = core / "assets/project-template/short-drama.json"
    project = json.loads(template_path.read_text(encoding="utf-8"))
    project.update(
        {
            "project_id": f"SD-{uuid.uuid4().hex[:12].upper()}",
            "title": title.strip() or "未命名短剧",
            "language": language,
            "suite_version": manifest["suite_version"],
            "contract_version": manifest["contract_version"],
            "created_at": utc_now(),
        }
    )
    project["format"]["aspect_ratio"] = aspect_ratio
    project["format"]["prompt_language"] = prompt_language

    state = {
        "schema_version": manifest["contract_version"],
        "project_id": project["project_id"],
        "project_layout_mode": "auto",
        "updated_at": utc_now(),
        "artifacts": {},
        "blocked_transactions": {},
        "active_transaction": None,
        "last_action": "initialized",
    }

    # The discoverable project marker is last, so minimum state always exists.
    atomic_json(root / STATE_FILE, state)
    atomic_json(project_path, project)
    return {"project_root": str(root), "project": project, "state": state}


def find_project(start: Path) -> Path:
    candidate = start.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / PROJECT_FILE).is_file():
            return directory
    raise FileNotFoundError(f"no {PROJECT_FILE} found from {start}")


def _transaction_status(transaction: Path) -> str:
    if not (transaction / "manifest.json").is_file():
        return "incomplete"
    try:
        events = _read_wal(transaction / "wal.jsonl", tolerate_missing=True)
    except (OSError, UnicodeError, TransactionError):
        return "corrupt"
    names = {event.get("event") for event in events}
    if "BLOCKED" in names:
        return "blocked"
    if "ROLLED_BACK" in names or "STATE_APPLIED" in names:
        return "complete"
    return "needs_rollforward" if _has_commit(transaction) else "needs_rollback"


def _open_directory_at(directory_fd: int, parts: Iterable[str]) -> int:
    descriptor = os.dup(directory_fd)
    try:
        for part in parts:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_regular_at(directory_fd: int, relative: str | Path) -> bytes:
    pure = PurePosixPath(relative)
    parent_fd = _open_directory_at(directory_fd, pure.parts[:-1])
    descriptor = -1
    try:
        descriptor = os.open(
            pure.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TransactionConflictError(f"project path is not a regular file: {relative}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _live_hash_at(directory_fd: int, relative: str) -> str | None:
    try:
        content = _read_regular_at(directory_fd, relative)
    except FileNotFoundError:
        return ABSENT_HASH
    return sha256_bytes(content)


def _project_layout_at(directory_fd: int, state: Mapping[str, Any]) -> dict[str, Any]:
    recorded = state.get("project_layout_mode", "auto")
    if recorded not in LAYOUT_MODES:
        recorded = "auto"
    canonical_roles: set[str] = set()
    legacy_roles: set[str] = set()
    nonstandard_roots: list[str] = []
    unsafe_roots: list[str] = []
    with os.scandir(directory_fd) as iterator:
        entries = list(iterator)
    for entry in entries:
        role = _root_role(entry.name)
        if role not in LAYOUT_PINNING_ROLES:
            continue
        if entry.is_symlink():
            unsafe_roots.append(entry.name)
            continue
        if not entry.is_dir(follow_symlinks=False):
            continue
        child_fd = os.open(
            entry.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        try:
            with os.scandir(child_fd) as children:
                has_content = next(children, None) is not None
        finally:
            os.close(child_fd)
        if not has_content:
            continue
        if entry.name == CANONICAL_ROOTS[role]:
            canonical_roles.add(role)
        elif entry.name == LEGACY_ROOTS[role]:
            legacy_roles.add(role)
        else:
            nonstandard_roots.append(entry.name)
    detected_modes = {
        mode
        for mode, roles in (
            ("canonical", canonical_roles),
            ("legacy", legacy_roles),
        )
        if roles
    }
    conflict = bool(nonstandard_roots or unsafe_roots) or len(detected_modes) > 1 or (
        recorded in {"canonical", "legacy"}
        and detected_modes
        and detected_modes != {recorded}
    )
    if conflict:
        mode = "mixed"
    elif recorded in {"canonical", "legacy"}:
        mode = recorded
    elif detected_modes:
        mode = next(iter(detected_modes))
    else:
        mode = "canonical"
    roots = CANONICAL_ROOTS if mode != "legacy" else LEGACY_ROOTS
    return {
        "mode": mode,
        "pinned": recorded != "auto" or bool(detected_modes),
        "roots": dict(roots),
        # The two lists below are the only answer to "why is my project mixed",
        # so they stay; the per-family role sets and the raw recorded mode had
        # no reader anywhere and shipped to the browser on every status refresh.
        "nonstandardRoots": sorted(nonstandard_roots),
        "unsafeRoots": sorted(unsafe_roots),
    }


def _effective_lifecycle_records_at(
    directory_fd: int, artifacts: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    effective = {
        str(artifact_id): dict(record)
        for artifact_id, record in artifacts.items()
        if isinstance(artifact_id, str) and isinstance(record, dict)
    }
    direct_stale: list[tuple[str, dict[str, str | None]]] = []
    for artifact_id, record in effective.items():
        changed: dict[str, str | None] = {}
        for relative, expected in _current_record_targets(record).items():
            try:
                actual = _live_hash_at(directory_fd, _relative_path(relative))
            except (OSError, ValueError, TransactionConflictError):
                actual = None
            if actual != expected:
                changed[relative] = actual
        if changed:
            direct_stale.append((artifact_id, changed))
    stale_changes = _stale_lifecycle_changes()
    for artifact_id, changed in direct_stale:
        effective[artifact_id] = apply_lifecycle_changes(
            effective[artifact_id], stale_changes
        )
        downstream = _downstream_stale_changes(
            {"artifacts": artifacts},
            publishing_artifact=artifact_id,
            candidate_targets=changed,
        )
        for dependent in downstream:
            if dependent in effective:
                effective[dependent] = apply_lifecycle_changes(
                    effective[dependent], stale_changes
                )
    return effective


def _transaction_status_at(transaction_fd: int) -> str:
    try:
        manifest = os.stat("manifest.json", dir_fd=transaction_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "incomplete"
    if not stat.S_ISREG(manifest.st_mode):
        return "corrupt"
    try:
        content = _read_regular_at(transaction_fd, "wal.jsonl")
    except FileNotFoundError:
        content = b""
    except (OSError, TransactionError):
        return "corrupt"
    events: list[dict[str, Any]] = []
    try:
        for line in content.decode("utf-8").splitlines():
            if line.strip():
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("event"), str):
                    return "corrupt"
                events.append(event)
    except (UnicodeError, json.JSONDecodeError):
        return "corrupt"
    names = {event.get("event") for event in events}
    if "BLOCKED" in names:
        return "blocked"
    if "ROLLED_BACK" in names or "STATE_APPLIED" in names:
        return "complete"
    try:
        marker = os.stat("COMMIT", dir_fd=transaction_fd, follow_symlinks=False)
        committed = stat.S_ISREG(marker.st_mode)
    except FileNotFoundError:
        committed = False
    return "needs_rollforward" if committed else "needs_rollback"


def _build_project_status(
    *,
    project: Mapping[str, Any],
    state: Mapping[str, Any],
    effective_artifacts: Mapping[str, Any],
    transaction_counts: dict[str, int],
    layout: Mapping[str, Any],
    project_root: str,
) -> dict[str, Any]:
    lifecycle: dict[str, dict[str, int]] = {axis: {} for axis in LIFECYCLE_STATES}
    for record in effective_artifacts.values():
        if not isinstance(record, dict):
            continue
        for axis in LIFECYCLE_STATES:
            value = str(record.get(axis, "unknown"))
            lifecycle[axis][value] = lifecycle[axis].get(value, 0) + 1

    blocked = state.get("blocked_transactions", {})
    blocked_count = len(blocked) if isinstance(blocked, dict) else 0
    needs_recovery = any(
        transaction_counts.get(name, 0)
        for name in (
            "incomplete",
            "corrupt",
            "needs_rollback",
            "needs_rollforward",
            "blocked",
        )
    ) or bool(blocked_count)
    pending_transactions = any(
        transaction_counts.get(name, 0)
        for name in ("incomplete", "corrupt", "needs_rollback", "needs_rollforward")
    )
    return {
        "project_root": project_root,
        "project_id": project.get("project_id"),
        "title": project.get("title"),
        # Both languages are surfaced here so a skill reads them from status
        # rather than re-opening the project file and guessing a default.
        **project_languages(project),
        "current_checkpoint": project.get("current_checkpoint"),
        "layout": dict(layout),
        "artifact_build_states": lifecycle["build_state"],
        "lifecycle": lifecycle,
        "active_transaction": state.get("active_transaction"),
        "last_action": state.get("last_action"),
        "recovery": {
            "needed": bool(needs_recovery),
            "transaction_counts": transaction_counts,
            "blocked_count": blocked_count,
            "next_action": (
                "recover"
                if pending_transactions
                else "resolve_conflict"
                if blocked_count
                else "continue"
            ),
        },
    }


def _project_status_from_root(
    root: Path, *, project_root: str | None = None
) -> dict[str, Any]:
    project = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    state_path = root / STATE_FILE
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    raw_artifacts = state.get("artifacts")
    artifacts: dict[str, Any] = raw_artifacts if isinstance(raw_artifacts, dict) else {}
    transaction_counts: dict[str, int] = {}
    blocked = state.get("blocked_transactions")
    blocked_ids = set(blocked) if isinstance(blocked, dict) else set()
    transactions = root / ".short-drama/transactions"
    if transactions.is_dir():
        for transaction in transactions.iterdir():
            if not transaction.is_dir():
                continue
            status = (
                "blocked"
                if transaction.name in blocked_ids
                else _transaction_status(transaction)
            )
            transaction_counts[status] = transaction_counts.get(status, 0) + 1
    return _build_project_status(
        project=project,
        state=state,
        effective_artifacts=_effective_lifecycle_records(root, artifacts),
        transaction_counts=transaction_counts,
        layout=_project_layout_from_root(root),
        project_root=project_root or str(root),
    )


def project_status(path: Path) -> dict[str, Any]:
    root = find_project(path)
    return _project_status_from_root(root)


def project_status_at(
    directory_fd: int, *, project_root: str | None = None
) -> dict[str, Any]:
    """Read project status relative to a caller-pinned directory descriptor."""

    if not isinstance(directory_fd, int) or directory_fd < 0:
        raise ValueError("directory_fd must be an open directory descriptor")
    details = os.fstat(directory_fd)
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError("directory_fd must reference a directory")
    # `_read_state` already refuses a non-object state on the path lane; match
    # it here. Valid JSON that is not an object otherwise reaches
    # `_build_project_status` and dies on `.get`, which is an AttributeError
    # no caller expects — the dashboard drops the connection without a status
    # line rather than reporting a malformed project.
    project = json.loads(_read_regular_at(directory_fd, PROJECT_FILE).decode("utf-8"))
    if not isinstance(project, dict):
        raise ValueError("short-drama.json must contain a JSON object")
    try:
        state = json.loads(_read_regular_at(directory_fd, STATE_FILE).decode("utf-8"))
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict):
        raise ValueError("project state must contain a JSON object")
    raw_artifacts = state.get("artifacts")
    artifacts: dict[str, Any] = raw_artifacts if isinstance(raw_artifacts, dict) else {}
    transaction_counts: dict[str, int] = {}
    blocked = state.get("blocked_transactions")
    blocked_ids = set(blocked) if isinstance(blocked, dict) else set()
    try:
        transactions_fd = _open_directory_at(
            directory_fd, (".short-drama", "transactions")
        )
    except FileNotFoundError:
        transactions_fd = -1
    except OSError:
        transaction_counts["corrupt"] = 1
        transactions_fd = -1
    if transactions_fd >= 0:
        try:
            with os.scandir(transactions_fd) as iterator:
                entries = list(iterator)
            for entry in entries:
                if entry.is_symlink():
                    transaction_counts["corrupt"] = (
                        transaction_counts.get("corrupt", 0) + 1
                    )
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    transaction_fd = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=transactions_fd,
                    )
                except OSError:
                    transaction_counts["corrupt"] = (
                        transaction_counts.get("corrupt", 0) + 1
                    )
                    continue
                try:
                    status = (
                        "blocked"
                        if entry.name in blocked_ids
                        else _transaction_status_at(transaction_fd)
                    )
                finally:
                    os.close(transaction_fd)
                transaction_counts[status] = transaction_counts.get(status, 0) + 1
        finally:
            os.close(transactions_fd)
    return _build_project_status(
        project=project,
        state=state,
        effective_artifacts=_effective_lifecycle_records_at(
            directory_fd, artifacts
        ),
        transaction_counts=transaction_counts,
        layout=_project_layout_at(directory_fd, state),
        project_root=project_root or "pinned-project",
    )


def _has_nonportable_path_component(parts: tuple[str, ...]) -> bool:
    for part in parts:
        stem = part.split(".", 1)[0].casefold()
        if (
            part.endswith((" ", "."))
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
            or any(character in WINDOWS_FORBIDDEN_PATH_CHARACTERS for character in part)
            or stem in WINDOWS_RESERVED_PATH_STEMS
        ):
            return True
    return False


def _relative_path(value: str | Path, *, allow_operations: bool = False) -> str:
    raw = str(value).replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError(f"unsafe project-relative path: {value!s}")
    if _has_nonportable_path_component(pure.parts):
        raise NonPortablePathError(f"unsafe project-relative path: {value!s}")
    relative = pure.as_posix()
    if not allow_operations and pure.parts[0].casefold() == ".short-drama":
        raise ValueError("operational state cannot be a publication target")
    return relative


def _portable_path_identity(value: str) -> str:
    """Return the path identity shared by supported case/Unicode-folding volumes."""

    return unicodedata.normalize("NFC", value.casefold())


def _register_portable_path(
    seen: dict[str, str],
    relative: str,
    *,
    label: str,
    allow_exact_duplicate: bool = False,
) -> None:
    """Reject spellings that collapse on case-insensitive supported filesystems."""

    identity = _portable_path_identity(relative)
    previous = seen.get(identity)
    if previous is not None:
        if allow_exact_duplicate and previous == relative:
            return
        raise NonPortablePathError(
            f"{label} paths are not portable aliases: {previous} and {relative}"
        )
    seen[identity] = relative


def _validate_existing_path_spelling(root: Path, relative: str, *, label: str) -> None:
    """Require every existing component to use its on-disk spelling exactly."""

    current = root
    prefix: list[str] = []
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        try:
            entries = list(os.scandir(current))
        except (FileNotFoundError, NotADirectoryError):
            return
        part_identity = _portable_path_identity(part)
        matches = [
            entry
            for entry in entries
            if _portable_path_identity(entry.name) == part_identity
        ]
        aliases = sorted(entry.name for entry in matches if entry.name != part)
        if aliases:
            existing = PurePosixPath(*prefix, aliases[0]).as_posix()
            raise NonPortablePathError(
                f"{label} path spelling aliases an existing path: "
                f"{relative} conflicts with {existing}"
            )
        exact = next((entry for entry in matches if entry.name == part), None)
        if exact is None or index == len(parts) - 1:
            return
        if not exact.is_dir(follow_symlinks=False):
            return
        prefix.append(part)
        current /= part


def _validate_new_path_set(
    root: Path,
    relatives: Iterable[str],
    *,
    label: str,
    allow_exact_duplicate: bool = False,
) -> None:
    seen: dict[str, str] = {}
    for relative in relatives:
        _register_portable_path(
            seen,
            relative,
            label=label,
            allow_exact_duplicate=allow_exact_duplicate,
        )
        _validate_existing_path_spelling(root, relative, label=label)


def _validate_paths_against_tracked_state(
    state: Mapping[str, Any], relatives: Iterable[str]
) -> None:
    tracked: dict[str, str] = {}
    artifacts = state.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise ValueError("state.artifacts must be an object")
    path_keys = (
        "candidate_targets",
        "accepted_targets",
        "candidate_inputs",
        "accepted_inputs",
        "candidate_input_records",
        "accepted_input_records",
    )
    for record in artifacts.values():
        if not isinstance(record, dict):
            continue
        for key in path_keys:
            values = record.get(key)
            if not isinstance(values, dict):
                continue
            for raw in values:
                if not isinstance(raw, str):
                    raise ValueError(f"tracked {key} path is invalid")
                try:
                    relative = _relative_path(
                        raw,
                        allow_operations=key
                        in {
                            "candidate_inputs",
                            "accepted_inputs",
                            "candidate_input_records",
                            "accepted_input_records",
                        },
                    )
                except NonPortablePathError:
                    # A project tracked by 0.2.0 may hold a spelling 0.3.0 no
                    # longer accepts. Scanning it here used to abort every
                    # publication in that project with a raw ValueError naming
                    # a path the creator was not touching, and the documented
                    # migration could not clear it: the spelling lives in
                    # state.json, so renaming the file changes nothing. Skip it
                    # instead. No coverage is lost — a new path must itself pass
                    # `_relative_path`, so it can never fold onto a spelling
                    # that failed here.
                    continue
                _register_portable_path(
                    tracked,
                    relative,
                    label="tracked project",
                    allow_exact_duplicate=True,
                )
    for relative in relatives:
        previous = tracked.get(_portable_path_identity(relative))
        if previous is not None and previous != relative:
            raise NonPortablePathError(
                "new path spelling aliases a tracked project path: "
                f"{relative} conflicts with {previous}"
            )


def _normalize_portable_path_values(
    root: Path,
    values: Iterable[str | Path],
    *,
    label: str,
    allow_operations: bool = False,
) -> list[str]:
    normalized: list[str] = []
    seen: dict[str, str] = {}
    exact: set[str] = set()
    for value in values:
        relative = _relative_path(value, allow_operations=allow_operations)
        _register_portable_path(
            seen,
            relative,
            label=label,
            allow_exact_duplicate=True,
        )
        _validate_existing_path_spelling(root, relative, label=label)
        if relative not in exact:
            exact.add(relative)
            normalized.append(relative)
    return sorted(normalized)


def _root_role(name: str) -> str | None:
    """Return one stable machine role for either Chinese or legacy root names."""

    return ROOT_ROLE_ALIASES.get(name.casefold())


def is_protected_project_text(value: str | Path) -> bool:
    """Return the shared Dashboard protection policy for project text paths."""

    raw = str(value).replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or any(
        part in ("", ".", "..") for part in pure.parts
    ) or _has_nonportable_path_component(pure.parts):
        return True
    return (
        pure.name.casefold() == PROJECT_FILE
        or pure.parts[0].casefold() == ".short-drama"
        or _root_role(pure.parts[0]) == "delivery"
    )


def _root_layout_mode(name: str) -> str | None:
    role = _root_role(name)
    if role is None:
        return None
    if name == CANONICAL_ROOTS[role]:
        return "canonical"
    if name == LEGACY_ROOTS[role]:
        return "legacy"
    return None


def _directory_has_content(path: Path) -> bool:
    try:
        details = os.lstat(path)
        if stat.S_ISLNK(details.st_mode):
            return True
        if not stat.S_ISDIR(details.st_mode):
            return False
        return any(path.iterdir())
    except FileNotFoundError:
        return False


def _project_layout_from_root(root: Path) -> dict[str, Any]:
    state = _read_state(root) if (root / STATE_FILE).is_file() else {}
    recorded = state.get("project_layout_mode", "auto")
    if recorded not in LAYOUT_MODES:
        recorded = "auto"
    canonical_roles = sorted(
        role
        for role, name in CANONICAL_ROOTS.items()
        if role in LAYOUT_PINNING_ROLES
        if _directory_has_content(root / name)
    )
    legacy_roles = sorted(
        role
        for role, name in LEGACY_ROOTS.items()
        if role in LAYOUT_PINNING_ROLES
        if _directory_has_content(root / name)
    )
    nonstandard_roots = sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_symlink() or entry.is_dir()
        if (role := _root_role(entry.name)) in LAYOUT_PINNING_ROLES
        if entry.name not in {CANONICAL_ROOTS[role], LEGACY_ROOTS[role]}
        if _directory_has_content(entry)
    )
    unsafe_roots = sorted(
        entry.name
        for entry in root.iterdir()
        if _root_role(entry.name) in LAYOUT_PINNING_ROLES
        if entry.is_symlink()
    )
    detected_modes = {
        mode
        for mode, roles in (
            ("canonical", canonical_roles),
            ("legacy", legacy_roles),
        )
        if roles
    }
    conflict = bool(nonstandard_roots or unsafe_roots) or len(detected_modes) > 1 or (
        recorded in {"canonical", "legacy"}
        and detected_modes
        and detected_modes != {recorded}
    )
    if conflict:
        mode = "mixed"
    elif recorded in {"canonical", "legacy"}:
        mode = recorded
    elif detected_modes:
        mode = next(iter(detected_modes))
    else:
        mode = "canonical"
    roots = CANONICAL_ROOTS if mode != "legacy" else LEGACY_ROOTS
    return {
        "mode": mode,
        "pinned": recorded != "auto" or bool(detected_modes),
        "roots": dict(roots),
        "nonstandardRoots": nonstandard_roots,
        "unsafeRoots": unsafe_roots,
    }


def project_layout(path: Path) -> dict[str, Any]:
    """Resolve one project-wide root layout while exposing mixed trees safely."""

    return _project_layout_from_root(find_project(path))


def _validate_project_output_layout(root: Path, relatives: Iterable[str]) -> str | None:
    # Only the roots that can *prove* a layout may choose one. `输入/` and
    # unregistered roots are excluded from detection (LAYOUT_PINNING_ROLES), so
    # letting them pin would record a family no stage directory on disk supports —
    # one `--allow-unregistered-path` write into an ad-hoc English directory
    # would lock a brand-new all-Chinese project into legacy and refuse every
    # later Chinese publish, with no supported way to undo it.
    families = {
        family
        for relative in relatives
        if (part := PurePosixPath(relative).parts[0])
        if _root_role(part) in LAYOUT_PINNING_ROLES
        if (family := _root_layout_mode(part)) is not None
    }
    if len(families) > 1:
        raise ValueError("不能在同一事务中混用中文与旧版英文目录")
    family = next(iter(families), None)
    layout = project_layout(root)
    if layout["mode"] == "mixed":
        raise ValueError("项目同时包含中文与旧版英文阶段目录，请先迁移并合并")
    if family is not None and layout["pinned"] and family != layout["mode"]:
        expected = "中文" if layout["mode"] == "canonical" else "旧版英文"
        raise ValueError(f"项目已使用{expected}目录布局，不能创建另一套平行目录")
    return family


def _layout_root_for_source(root: Path, role: str, source_root: str | None = None) -> str:
    """Choose a matching output root without mixing layouts implicitly."""

    layout = project_layout(root)
    if layout["mode"] == "mixed":
        raise ValueError("项目同时包含中文与旧版英文阶段目录，请先迁移并合并")
    if source_root is not None and _root_role(source_root) == role:
        family = _root_layout_mode(source_root)
        if layout["pinned"] and family != layout["mode"]:
            raise ValueError("源目录与项目布局不一致")
        if family == "canonical":
            return CANONICAL_ROOTS[role]
        if family == "legacy":
            return LEGACY_ROOTS[role]
    if layout["pinned"]:
        return str(layout["roots"][role])
    canonical = CANONICAL_ROOTS[role]
    legacy = LEGACY_ROOTS[role]
    canonical_exists = (root / canonical).exists()
    legacy_exists = (root / legacy).exists()
    if canonical_exists and legacy_exists:
        canonical_has_content = any((root / canonical).iterdir())
        legacy_has_content = any((root / legacy).iterdir())
        if canonical_has_content and legacy_has_content:
            raise ValueError(
                f"同一目录职责同时存在 {canonical}/ 与 {legacy}/，请先合并后再继续"
            )
        return canonical if canonical_has_content or not legacy_has_content else legacy
    if canonical_exists:
        return canonical
    if legacy_exists:
        return legacy
    return canonical


def _validate_publication_layout(
    relative: str, *, owner: str | None = None, allow_unregistered: bool = False
) -> None:
    """Reject a publication target that breaks the project layout contract.

    Deliberately NOT part of _relative_path. That function also normalizes
    paths already recorded in a write-ahead log, and applying today's layout
    policy to yesterday's manifest would make an interrupted transaction
    unrecoverable: rollback would raise instead of restoring the creator's
    prior bytes, and every later `recover` would re-report the same block.
    Layout is therefore checked only where a new path is minted.
    """

    pure = PurePosixPath(relative)
    first = pure.parts[0].casefold()
    role = _root_role(pure.parts[0])
    reason = PROTECTED_PUBLISH_ROOTS.get(first)
    if reason is not None:
        raise ValueError(reason)
    # Compared by basename, not by full path: a planted development/short-drama.json
    # makes find_project treat that subdirectory as its own project root, so a
    # creator running `status` from inside it reads the decoy.
    if pure.name.casefold() == PROJECT_FILE:
        raise ValueError("creator authority file cannot be a publication target")
    if role == "episodes":
        if len(pure.parts) < 3:
            raise ValueError(
                "episode artifacts live in 剧集/<EP>/"
                f"（兼容 episodes/<EP>/）：{relative}"
            )
        if EPISODE_ID_RE.fullmatch(pure.parts[1]) is None:
            raise ValueError(
                f"episode directory must use an EP001-style identifier: {pure.parts[1]}"
            )
    if not allow_unregistered and role not in PUBLISHABLE_ROOT_ROLES:
        raise ValueError(
            f"{pure.parts[0]} is not a project stage directory; "
            f"expected one of {', '.join(PUBLISHABLE_ROOTS)}"
        )
    if owner is not None:
        expected = _expected_path_owner(relative)
        if expected is not None and owner != expected:
            raise ValueError(f"{expected} owns {relative}, not {owner}")
    if role is not None and pure.parts[0] not in {
        CANONICAL_ROOTS[role],
        LEGACY_ROOTS[role],
    }:
        raise ValueError(f"阶段目录大小写或拼写不规范：{pure.parts[0]}")


def _expected_path_owner(relative: str) -> str | None:
    # Casefolded like every other path guard here. A case-sensitive lookup
    # would let `Episodes/EP001/screenplay.md` past the ownership check and,
    # on a case-insensitive filesystem, overwrite the very artifact the check
    # protects.
    pure = PurePosixPath(relative)
    role = _root_role(pure.parts[0])
    folded_parts = tuple(part.casefold() for part in pure.parts)
    if role == "episodes" and len(pure.parts) >= 3:
        remainder = PurePosixPath(*folded_parts[2:]).as_posix()
        exact = DECLARED_EPISODE_ARTIFACT_OWNERS.get(remainder)
        if exact is not None:
            return exact
        remainder_parts = PurePosixPath(remainder).parts
        if len(remainder_parts) == 3 and remainder_parts[-1].endswith(".jsonl"):
            family = PurePosixPath(*remainder_parts[:2]).as_posix()
            return DECLARED_EPISODE_ARTIFACT_FAMILY_OWNERS.get(family)
        return None
    if role is None:
        return None
    normalized = PurePosixPath(role, *folded_parts[1:]).as_posix()
    exact = DECLARED_PROJECT_ARTIFACT_OWNERS.get(normalized)
    if exact is not None:
        return exact
    normalized_parts = PurePosixPath(normalized).parts
    if len(normalized_parts) == 4 and normalized_parts[-1].endswith(".md"):
        family = PurePosixPath(*normalized_parts[:3]).as_posix()
        return DECLARED_PROJECT_ARTIFACT_FAMILY_OWNERS.get(family)
    return None


def _project_path(root: Path, relative: str) -> Path:
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise TransactionConflictError(
                f"publication parent cannot be a symlink: {part}"
            )
    resolved_parent = target.parent.resolve()
    if not resolved_parent.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes project root: {relative}")
    return target


def _live_hash(path: Path) -> str | None:
    if not path.exists():
        return ABSENT_HASH
    if path.is_symlink() or not path.is_file():
        raise TransactionConflictError(f"target is not a regular file: {path.name}")
    return sha256_file(path)


def _artifact_directory(artifact_id: str) -> str:
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", artifact_id).strip("-.") or "artifact"
    return f"{label[:48]}-{sha256_bytes(artifact_id.encode('utf-8'))[:12]}"


def _snapshot_file(root: Path, artifact_id: str, digest: str) -> Path:
    return (
        root
        / ".short-drama/accepted-snapshots"
        / _artifact_directory(artifact_id)
        / digest
        / "content"
    )


def _preserve_snapshot(root: Path, artifact_id: str, content: bytes) -> str:
    digest = sha256_bytes(content)
    snapshot = _snapshot_file(root, artifact_id, digest)
    if snapshot.exists():
        if not snapshot.is_file() or sha256_file(snapshot) != digest:
            raise RecoveryMaterialError("immutable snapshot hash mismatch")
    else:
        _atomic_bytes(snapshot, content)
    return snapshot.relative_to(root).as_posix()


@contextlib.contextmanager
def _transaction_lock(root: Path):
    lock = root / ".short-drama/locks/transaction.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle:
        if os.name == "nt":
            locking = importlib.import_module("msvcrt")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            locking.locking(handle.fileno(), locking.LK_LOCK, 1)
        else:
            locking = importlib.import_module("fcntl")
            locking.flock(handle.fileno(), locking.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                locking.locking(handle.fileno(), locking.LK_UNLCK, 1)
            else:
                locking.flock(handle.fileno(), locking.LOCK_UN)


def _fault(injector: FaultInjector | None, point: str, txid: str) -> None:
    if injector is not None:
        injector(point, {"transaction_id": txid})


def _normalize_read_set(
    root: Path,
    read_set: Mapping[str, str | None] | Iterable[str] | None,
    read_records: Mapping[str, Mapping[str, str]] | None = None,
) -> list[dict[str, Any]]:
    if read_set is None:
        return []
    entries: list[dict[str, Any]] = []
    items: Iterable[tuple[str, str | None]]
    if isinstance(read_set, Mapping):
        items = ((str(path), expected) for path, expected in read_set.items())
    else:
        items = ((str(path), None) for path in read_set)
    normalized_items: list[tuple[str, str | None]] = []
    seen: dict[str, str] = {}
    for raw, expected in items:
        relative = _relative_path(raw, allow_operations=True)
        _register_portable_path(seen, relative, label="read set")
        _validate_existing_path_spelling(root, relative, label="read set")
        normalized_items.append((relative, expected))
    records: dict[str, Mapping[str, str]] = {}
    record_paths: dict[str, str] = {}
    for raw, bindings in (read_records or {}).items():
        relative = _relative_path(raw, allow_operations=True)
        _register_portable_path(
            record_paths, relative, label="read record binding"
        )
        _validate_existing_path_spelling(root, relative, label="read record binding")
        records[relative] = bindings
    for relative, expected in normalized_items:
        if not isinstance(read_set, Mapping):
            expected = _live_hash(_project_path(root, relative))
        if expected is not None and not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"invalid expected read hash for {relative}")
        actual = _live_hash(_project_path(root, relative))
        if actual != expected:
            raise StaleReadSetError(f"read set was stale before prepare: {relative}")
        entry: dict[str, Any] = {"path": relative, "expected_hash": expected}
        bound = records.pop(relative, None)
        if bound:
            entry["records"] = dict(sorted(bound.items()))
        entries.append(entry)
    if records:
        raise ValueError(
            "record binding has no matching read set path: " + ", ".join(sorted(records))
        )
    return sorted(entries, key=lambda entry: entry["path"])


def _validate_read_set(root: Path, entries: list[dict[str, Any]]) -> None:
    stale = [
        entry["path"]
        for entry in entries
        if _live_hash(_project_path(root, entry["path"])) != entry["expected_hash"]
    ]
    if stale:
        raise StaleReadSetError("read set changed after prepare: " + ", ".join(stale))


def _replace_from_file(source: Path, target: Path, expected_hash: str | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_dev != target.parent.stat().st_dev:
        raise TransactionError("transaction staging and target must share a filesystem")
    temporary = target.parent / f".{target.name}.apply-{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if _live_hash(target) != expected_hash:
            raise TransactionConflictError("target changed immediately before replace")
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_state(root: Path) -> dict[str, Any]:
    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("state must be a JSON object")
    if not isinstance(state.get("artifacts", {}), dict):
        raise ValueError("state.artifacts must be an object")
    return state


def _apply_snapshot_pointers(root: Path, manifest: dict[str, Any]) -> bool:
    state = _read_state(root)
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    artifacts = state.setdefault("artifacts", {})
    grouped: dict[str, list[dict[str, Any]]] = {}
    for target in manifest["targets"]:
        grouped.setdefault(target["artifact_id"], []).append(target)
    for artifact_id, targets in grouped.items():
        existing = artifacts.get(artifact_id, {})
        if not isinstance(existing, dict):
            existing = {}
        record = apply_lifecycle_changes(existing, {})
        authority = manifest.get("authority", "accepted")
        if authority == "candidate":
            candidate_targets = {
                target["path"]: target["candidate_hash"] for target in targets
            }
            candidate_snapshots = {
                target["path"]: target["candidate_snapshot"] for target in targets
            }
            record["owner"] = manifest["owner"]
            record["candidate_targets"] = dict(sorted(candidate_targets.items()))
            record["candidate_snapshots"] = dict(sorted(candidate_snapshots.items()))
            record["candidate_inputs"] = {
                entry["path"]: entry["expected_hash"]
                for entry in manifest.get("read_set", [])
            }
            candidate_input_records = {
                entry["path"]: entry["records"]
                for entry in manifest.get("read_set", [])
                if entry.get("records")
            }
            if candidate_input_records:
                record["candidate_input_records"] = candidate_input_records
            else:
                record.pop("candidate_input_records", None)
            record["candidate_source_transaction"] = manifest["transaction_id"]
            record.pop("creator_decision", None)
            record.pop("review_evidence", None)
            pointer_targets = record["candidate_targets"]
            pointer_name = "candidate_snapshot"
        else:
            accepted_targets = record.get("accepted_targets", {})
            if not isinstance(accepted_targets, dict):
                accepted_targets = {}
            snapshots = record.get("accepted_snapshots", {})
            if not isinstance(snapshots, dict):
                snapshots = {}
            for target in targets:
                accepted_targets[target["path"]] = target["candidate_hash"]
                snapshots[target["path"]] = target["candidate_snapshot"]
            record["accepted_targets"] = dict(sorted(accepted_targets.items()))
            record["accepted_snapshots"] = dict(sorted(snapshots.items()))
            record["source_transaction"] = manifest["transaction_id"]
            pointer_targets = record["accepted_targets"]
            pointer_name = "accepted_snapshot"
        pointer_material = json.dumps(
            pointer_targets, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        record[pointer_name] = sha256_bytes(pointer_material)
        artifacts[artifact_id] = record
    after = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if after == before:
        return False
    state["updated_at"] = utc_now()
    state["last_action"] = "snapshot_pointers_applied"
    atomic_json(root / STATE_FILE, state)
    return True


def _apply_intended_lifecycle(root: Path, manifest: dict[str, Any]) -> bool:
    state = _read_state(root)
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    artifacts = state.setdefault("artifacts", {})
    for artifact_id, changes in manifest["lifecycle_changes"].items():
        existing = artifacts.get(artifact_id, {})
        if not isinstance(existing, dict):
            existing = {}
        artifacts[artifact_id] = apply_lifecycle_changes(existing, changes)
    layout_mode = manifest.get("project_layout_mode")
    if layout_mode in {"canonical", "legacy"}:
        recorded = state.get("project_layout_mode", "auto")
        if recorded not in {"auto", layout_mode}:
            raise TransactionConflictError("transaction layout conflicts with project layout")
        state["project_layout_mode"] = layout_mode
    blocked = state.setdefault("blocked_transactions", {})
    if isinstance(blocked, dict):
        blocked.pop(manifest["transaction_id"], None)
    after = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if after == before:
        return False
    state["updated_at"] = utc_now()
    state["last_action"] = "transaction_committed"
    atomic_json(root / STATE_FILE, state)
    return True


def _block_transaction(
    root: Path,
    manifest: dict[str, Any],
    *,
    code: str,
    append_event: bool = True,
) -> None:
    state = _read_state(root)
    blocked = state.setdefault("blocked_transactions", {})
    artifact_ids = sorted(
        set(manifest["lifecycle_changes"])
        | {target["artifact_id"] for target in manifest["targets"]}
    )
    value = {
        "code": code,
        "artifact_ids": artifact_ids,
        "resolution": ["adopt", "restore", "merge"],
    }
    changed = not isinstance(blocked, dict) or blocked.get(manifest["transaction_id"]) != value
    if not isinstance(blocked, dict):
        blocked = {}
        state["blocked_transactions"] = blocked
    blocked[manifest["transaction_id"]] = value
    artifacts = state.setdefault("artifacts", {})
    for artifact_id in artifact_ids:
        existing = artifacts.get(artifact_id, {})
        if not isinstance(existing, dict):
            existing = {}
        failed = apply_lifecycle_changes(
            existing, {"build_state": "failed", "delivery_gate": "blocked"}
        )
        if failed != existing:
            changed = True
        artifacts[artifact_id] = failed
    if changed:
        state["updated_at"] = utc_now()
        state["last_action"] = "transaction_blocked"
        atomic_json(root / STATE_FILE, state)
    wal = root / ".short-drama/transactions" / manifest["transaction_id"] / "wal.jsonl"
    if append_event and "BLOCKED" not in _event_names(_read_wal(wal, tolerate_missing=True)):
        _append_wal(wal, {"event": "BLOCKED", "code": code})


def _block_untrusted_transaction(root: Path, transaction_id: str, *, code: str) -> None:
    """Persist a blocker without trusting fields from an invalid manifest."""

    state = _read_state(root)
    blocked = state.setdefault("blocked_transactions", {})
    if not isinstance(blocked, dict):
        blocked = {}
        state["blocked_transactions"] = blocked
    value = {
        "code": code,
        "artifact_ids": [],
        "resolution": ["recover_with_previous_version", "manual_migration"],
    }
    if blocked.get(transaction_id) == value:
        return
    blocked[transaction_id] = value
    state["updated_at"] = utc_now()
    state["last_action"] = "transaction_blocked"
    atomic_json(root / STATE_FILE, state)


def _quarantine_manifestless_transaction(root: Path, transaction_id: str) -> Path:
    transaction = root / ".short-drama/transactions" / transaction_id
    quarantine = (
        root
        / ".short-drama/conflicts/orphaned-transactions"
        / transaction_id
    )
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    if transaction.exists() and not quarantine.exists():
        os.replace(transaction, quarantine)
        _fsync_directory(transaction.parent)
        _fsync_directory(quarantine.parent)
    elif transaction.exists() and quarantine.exists():
        raise TransactionError("manifest-less transaction quarantine already exists")

    state = _read_state(root)
    blocked = state.setdefault("blocked_transactions", {})
    record = {
        "code": "MANIFEST_MISSING",
        "artifact_ids": [],
        "resolution": ["inspect", "restore_from_known_good"],
    }
    if not isinstance(blocked, dict):
        blocked = {}
        state["blocked_transactions"] = blocked
    if blocked.get(transaction_id) != record:
        blocked[transaction_id] = record
        state["updated_at"] = utc_now()
        state["last_action"] = "transaction_quarantined"
        atomic_json(root / STATE_FILE, state)
    return quarantine


def _preserve_conflict(
    root: Path, manifest: dict[str, Any], target: dict[str, Any], content: bytes
) -> Path:
    digest = sha256_bytes(content)
    conflict = (
        root
        / ".short-drama/conflicts"
        / manifest["transaction_id"]
        / f"{target['index']:04d}-{digest}.bin"
    )
    if conflict.exists():
        if conflict.read_bytes() != content:
            raise RecoveryMaterialError("conflict copy is not immutable")
    else:
        _atomic_bytes(conflict, content)
    return conflict


def publish_transaction(
    path: Path,
    *,
    stage: str,
    outputs: Mapping[str, str | bytes],
    lifecycle_changes: Mapping[str, Mapping[str, Any]],
    target_artifacts: Mapping[str, str] | None = None,
    read_set: Mapping[str, str | None] | Iterable[str] | None = None,
    read_records: Mapping[str, Mapping[str, str]] | None = None,
    fault_injector: FaultInjector | None = None,
    authority: str = "accepted",
    owner: str | None = None,
    allow_unregistered_path: bool = False,
    _delivery_gate: bool = False,
) -> dict[str, Any]:
    """Publish multiple files with deterministic crash recovery.

    The COMMIT marker is the sole recovery-direction decision. Before it,
    recovery restores every expected prior version. After it, recovery restores
    every candidate version and completes missing pointer/lifecycle state.
    """

    root = find_project(path)
    if not outputs:
        raise ValueError("a transaction needs at least one output")
    if not stage or not re.fullmatch(r"[A-Za-z0-9._:-]+", stage):
        raise ValueError("stage must be an opaque identifier")
    if authority not in {"accepted", "candidate"}:
        raise ValueError("authority must be accepted or candidate")
    if authority == "candidate":
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9._:-]+", owner):
            raise ValueError("candidate owner must be an opaque identifier")
    elif owner is not None:
        raise ValueError("owner metadata is only valid for candidate publication")
    validated_changes: dict[str, dict[str, Any]] = {}
    for artifact_id, changes in lifecycle_changes.items():
        if not artifact_id:
            raise ValueError("artifact id cannot be empty")
        apply_lifecycle_changes({}, changes)
        validated_changes[str(artifact_id)] = dict(changes)
    # Output spellings are checked once, under the lock, together with the read
    # set and tracked state.
    relative_outputs = {_relative_path(key): value for key, value in outputs.items()}
    # `_delivery_gate` is an internal argument, not a stage name: `stage` is
    # creator-supplied, so gating on stage == "delivery" would let any caller
    # unlock the packaged tree by naming itself after it. It skips the layout
    # contract entirely because build_delivery_package constructs every one of
    # its output keys itself from an already-validated episode id.
    if not _delivery_gate:
        for relative in relative_outputs:
            _validate_publication_layout(
                relative, owner=owner, allow_unregistered=allow_unregistered_path
            )

    if target_artifacts is None:
        default_artifact = next(iter(validated_changes)) if len(validated_changes) == 1 else stage
        mapped_artifacts = {relative: default_artifact for relative in relative_outputs}
    else:
        normalized_artifact_items = [
            (_relative_path(relative), str(artifact))
            for relative, artifact in target_artifacts.items()
        ]
        _validate_new_path_set(
            root,
            (relative for relative, _ in normalized_artifact_items),
            label="target artifact",
        )
        mapped_artifacts = dict(normalized_artifact_items)
        missing = sorted(set(relative_outputs) - set(mapped_artifacts))
        extra = sorted(set(mapped_artifacts) - set(relative_outputs))
        if missing or extra:
            raise ValueError(f"target artifact mapping mismatch; missing={missing}, extra={extra}")
    if any(not artifact for artifact in mapped_artifacts.values()):
        raise ValueError("target artifact id cannot be empty")

    with _transaction_lock(root):
        # Layout selection is project-wide state. Validate it while holding the
        # same lock that covers target replacement and state application so two
        # first publications cannot commit opposite directory families.
        state = _read_state(root)
        _validate_new_path_set(root, relative_outputs, label="publication output")
        layout_family = _validate_project_output_layout(root, relative_outputs)
        read_entries = _normalize_read_set(root, read_set, read_records)
        transaction_paths = [
            *relative_outputs,
            *(entry["path"] for entry in read_entries),
        ]
        _validate_new_path_set(
            root,
            transaction_paths,
            label="transaction",
            allow_exact_duplicate=True,
        )
        _validate_paths_against_tracked_state(state, transaction_paths)
        transaction_id = uuid.uuid4().hex
        transaction = root / ".short-drama/transactions" / transaction_id
        staged = transaction / "staged"
        staged.mkdir(parents=True, exist_ok=False)
        if transaction.stat().st_dev != root.stat().st_dev:
            raise TransactionError("transaction directory is not on the project filesystem")

        targets: list[dict[str, Any]] = []
        for index, relative in enumerate(sorted(relative_outputs)):
            content_value = relative_outputs[relative]
            content = content_value.encode("utf-8") if isinstance(content_value, str) else bytes(content_value)
            target_path = _project_path(root, relative)
            if target_path.exists() and target_path.is_symlink():
                raise TransactionConflictError("publication target cannot be a symlink")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.parent.stat().st_dev != transaction.stat().st_dev:
                raise TransactionError("target and transaction staging must share a filesystem")
            expected_prior = _live_hash(target_path)
            prior_snapshot = None
            if expected_prior is not None:
                prior_snapshot = _preserve_snapshot(
                    root, mapped_artifacts[relative], target_path.read_bytes()
                )
            staged_file = staged / f"{index:04d}.candidate"
            _atomic_bytes(staged_file, content)
            candidate_hash = sha256_bytes(content)
            candidate_snapshot = _preserve_snapshot(
                root, mapped_artifacts[relative], content
            )
            targets.append(
                {
                    "index": index,
                    "path": relative,
                    "artifact_id": mapped_artifacts[relative],
                    "expected_prior": expected_prior,
                    "prior_snapshot": prior_snapshot,
                    "candidate_hash": candidate_hash,
                    "candidate_snapshot": candidate_snapshot,
                    "staged": staged_file.relative_to(root).as_posix(),
                }
            )

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "stage": stage,
            "authority": authority,
            "owner": owner,
            "read_set": read_entries,
            "targets": targets,
            "lifecycle_changes": validated_changes,
        }
        if layout_family is not None:
            manifest["project_layout_mode"] = layout_family
        atomic_json(transaction / "manifest.json", manifest)
        _fault(fault_injector, "after_manifest", transaction_id)
        _append_wal(transaction / "wal.jsonl", {"event": "PREPARED"})
        _fault(fault_injector, "after_prepared", transaction_id)
        _validate_read_set(root, read_entries)

        for target in targets:
            index = target["index"]
            destination = _project_path(root, target["path"])
            _fault(fault_injector, f"before_replace:{index}", transaction_id)
            actual = _live_hash(destination)
            if actual == target["candidate_hash"]:
                pass
            elif actual != target["expected_prior"]:
                if destination.is_file():
                    _preserve_conflict(root, manifest, target, destination.read_bytes())
                _block_transaction(root, manifest, code="EXTERNAL_EDIT_CONFLICT")
                raise TransactionConflictError(
                    f"target changed before replace at index {index}"
                )
            else:
                try:
                    _replace_from_file(
                        _project_path(root, target["staged"]),
                        destination,
                        target["expected_prior"],
                    )
                except TransactionConflictError:
                    latest = _live_hash(destination)
                    if latest not in (
                        target["expected_prior"],
                        target["candidate_hash"],
                    ) and destination.is_file():
                        _preserve_conflict(
                            root, manifest, target, destination.read_bytes()
                        )
                    _block_transaction(root, manifest, code="EXTERNAL_EDIT_CONFLICT")
                    raise
            _fault(fault_injector, f"after_replace:{index}", transaction_id)
            _append_wal(
                transaction / "wal.jsonl", {"event": "APPLIED", "index": index}
            )
            _fault(fault_injector, f"after_applied:{index}", transaction_id)

        for target in targets:
            actual = _live_hash(_project_path(root, target["path"]))
            if actual != target["candidate_hash"]:
                if actual not in (target["expected_prior"], target["candidate_hash"]):
                    destination = _project_path(root, target["path"])
                    if destination.is_file():
                        _preserve_conflict(root, manifest, target, destination.read_bytes())
                    _block_transaction(root, manifest, code="EXTERNAL_EDIT_CONFLICT")
                    raise TransactionConflictError("target changed before commit")
                raise TransactionError("candidate verification failed before commit")

        _fault(fault_injector, "before_commit", transaction_id)
        _write_marker(transaction / "COMMIT")
        _fault(fault_injector, "after_commit_marker", transaction_id)
        _append_wal(transaction / "wal.jsonl", {"event": "COMMIT"})
        _fault(fault_injector, "after_commit", transaction_id)
        _apply_snapshot_pointers(root, manifest)
        _fault(fault_injector, "after_pointer_state", transaction_id)
        _append_wal(transaction / "wal.jsonl", {"event": "POINTERS_APPLIED"})
        _fault(fault_injector, "after_pointers", transaction_id)
        _apply_intended_lifecycle(root, manifest)
        _fault(fault_injector, "after_lifecycle_state", transaction_id)
        _append_wal(transaction / "wal.jsonl", {"event": "STATE_APPLIED"})
        _fault(fault_injector, "after_state", transaction_id)
        return {
            "transaction_id": transaction_id,
            "status": "committed",
            "target_count": len(targets),
        }


def _read_wal(path: Path, *, tolerate_missing: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        if tolerate_missing:
            return []
        raise TransactionError("transaction WAL is missing")
    events: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise TransactionError(f"invalid WAL line {number}") from error
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise TransactionError(f"invalid WAL event at line {number}")
        events.append(event)
    return events


def _event_names(events: list[dict[str, Any]]) -> set[str]:
    return {str(event.get("event")) for event in events}


def _validate_manifest(manifest: dict[str, Any], txid: str) -> None:
    if manifest.get("transaction_id") != txid:
        raise TransactionError("transaction id does not match its directory")
    if not isinstance(manifest.get("targets"), list) or not manifest["targets"]:
        raise TransactionError("transaction manifest has no targets")
    if not isinstance(manifest.get("lifecycle_changes"), dict):
        raise TransactionError("transaction lifecycle changes are missing")
    authority = manifest.get("authority", "accepted")
    if authority not in {"accepted", "candidate"}:
        raise TransactionError("transaction authority is invalid")
    owner = manifest.get("owner")
    if authority == "candidate" and (
        not isinstance(owner, str)
        or re.fullmatch(r"[A-Za-z0-9._:-]+", owner) is None
    ):
        raise TransactionError("candidate transaction owner is invalid")
    if authority == "accepted" and owner is not None:
        raise TransactionError("accepted transaction cannot claim candidate ownership")
    read_set = manifest.get("read_set")
    if not isinstance(read_set, list):
        raise TransactionError("transaction read set is invalid")
    read_paths: set[str] = set()
    portable_read_paths: dict[str, str] = {}
    for entry in read_set:
        if not isinstance(entry, dict) or not {"path", "expected_hash"} <= set(entry):
            raise TransactionError("transaction read set entry is invalid")
        if set(entry) - {"path", "expected_hash", "records"}:
            raise TransactionError("transaction read set entry is invalid")
        bound = entry.get("records")
        if "records" in entry:
            if not isinstance(bound, dict) or not bound:
                raise TransactionError("transaction read set records are invalid")
            for selector, digest in bound.items():
                if (
                    not isinstance(selector, str)
                    or not selector
                    or not isinstance(digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                ):
                    raise TransactionError("transaction read set records are invalid")
        relative = _relative_path(
            entry["path"], allow_operations=authority == "accepted"
        )
        _register_portable_path(
            portable_read_paths, relative, label="transaction read set"
        )
        if relative in read_paths:
            raise TransactionError("transaction read set paths are duplicated")
        read_paths.add(relative)
        expected = entry["expected_hash"]
        if expected is not None and (
            not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            raise TransactionError("transaction read set hash is invalid")
        if authority == "candidate" and expected is None:
            raise TransactionError("candidate read set hash must be exact")
    indices: set[int] = set()
    paths: set[str] = set()
    portable_target_paths: dict[str, str] = {}
    portable_snapshot_paths: dict[str, str] = {}
    for target in manifest["targets"]:
        required = {
            "index",
            "path",
            "artifact_id",
            "expected_prior",
            "prior_snapshot",
            "candidate_hash",
            "candidate_snapshot",
        }
        if not isinstance(target, dict) or not required.issubset(target):
            raise TransactionError("transaction target record is incomplete")
        if not isinstance(target["artifact_id"], str) or not target["artifact_id"]:
            raise TransactionError("transaction artifact id is invalid")
        if not isinstance(target["index"], int) or target["index"] < 0:
            raise TransactionError("transaction target index is invalid")
        relative = _relative_path(target["path"])
        _register_portable_path(
            portable_target_paths, relative, label="transaction target"
        )
        if target["index"] in indices or relative in paths:
            raise TransactionError("transaction target records are duplicated")
        indices.add(target["index"])
        paths.add(relative)
        for key in ("candidate_hash", "expected_prior"):
            value = target[key]
            if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise TransactionError(f"invalid {key} in transaction manifest")
        for key in ("candidate_snapshot", "prior_snapshot"):
            pointer = target[key]
            if pointer is None and key == "prior_snapshot":
                continue
            if not isinstance(pointer, str):
                raise TransactionError(f"invalid {key} in transaction manifest")
            snapshot = PurePosixPath(_relative_path(pointer, allow_operations=True))
            _register_portable_path(
                portable_snapshot_paths,
                snapshot.as_posix(),
                label="transaction snapshot",
                allow_exact_duplicate=True,
            )
            if snapshot.parts[:2] != (".short-drama", "accepted-snapshots"):
                raise TransactionError(f"invalid {key} zone in transaction manifest")
    if indices != set(range(len(manifest["targets"]))):
        raise TransactionError("transaction target indices are not contiguous")


def _has_commit(transaction: Path) -> bool:
    marker = transaction / "COMMIT"
    return marker.is_file() and marker.read_bytes() == b"committed\n"


def _material_for(root: Path, target: dict[str, Any], direction: str) -> Path | None:
    relative = target["candidate_snapshot"] if direction == "forward" else target["prior_snapshot"]
    expected = target["candidate_hash"] if direction == "forward" else target["expected_prior"]
    if expected is None:
        return None
    if not relative:
        raise RecoveryMaterialError("required snapshot pointer is absent")
    snapshot = _project_path(root, _relative_path(relative, allow_operations=True))
    if not snapshot.is_file() or sha256_file(snapshot) != expected:
        raise RecoveryMaterialError("required immutable snapshot is missing or corrupt")
    return snapshot


def _observe_targets(root: Path, manifest: dict[str, Any]) -> list[str | None]:
    return [_live_hash(_project_path(root, target["path"])) for target in manifest["targets"]]


def _state_satisfies_manifest(root: Path, manifest: dict[str, Any]) -> bool:
    state = _read_state(root)
    artifacts = state.get("artifacts", {})
    pointer_key = (
        "candidate_targets"
        if manifest.get("authority", "accepted") == "candidate"
        else "accepted_targets"
    )
    expected_candidates: dict[str, dict[str, str]] = {}
    if pointer_key == "candidate_targets":
        for target in manifest["targets"]:
            expected_candidates.setdefault(target["artifact_id"], {})[
                target["path"]
            ] = target["candidate_hash"]
    for target in manifest["targets"]:
        record = artifacts.get(target["artifact_id"], {})
        if not isinstance(record, dict):
            return False
        pointers = record.get(pointer_key, {})
        if not isinstance(pointers, dict) or pointers.get(target["path"]) != target["candidate_hash"]:
            return False
        if pointer_key == "candidate_targets":
            expected_inputs = {
                entry["path"]: entry["expected_hash"]
                for entry in manifest.get("read_set", [])
            }
            expected_input_records = {
                entry["path"]: entry["records"]
                for entry in manifest.get("read_set", [])
                if entry.get("records")
            }
            if (
                record.get("owner") != manifest.get("owner")
                or record.get("candidate_inputs") != expected_inputs
                or record.get("candidate_input_records", {}) != expected_input_records
                or pointers != expected_candidates[target["artifact_id"]]
            ):
                return False
    for artifact_id, changes in manifest["lifecycle_changes"].items():
        record = artifacts.get(artifact_id, {})
        if not isinstance(record, dict) or any(record.get(axis) != value for axis, value in changes.items()):
            return False
    return True


def recover_transaction(
    path: Path,
    transaction_id: str,
    *,
    fault_injector: FaultInjector | None = None,
) -> dict[str, Any]:
    root = find_project(path)
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise ValueError("invalid transaction id")
    with _transaction_lock(root):
        transaction = root / ".short-drama/transactions" / transaction_id
        manifest_path = transaction / "manifest.json"
        if not manifest_path.is_file():
            _quarantine_manifestless_transaction(root, transaction_id)
            return {
                "transaction_id": transaction_id,
                "status": "blocked",
                "direction": "unknown",
                "already_recovered": False,
                "code": "MANIFEST_MISSING",
            }
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            _block_untrusted_transaction(root, transaction_id, code="MANIFEST_INVALID")
            return {
                "transaction_id": transaction_id,
                "status": "blocked",
                "direction": "forward" if _has_commit(transaction) else "rollback",
                "already_recovered": False,
                "code": "MANIFEST_INVALID",
            }
        try:
            _validate_manifest(manifest, transaction_id)
        except (TransactionError, ValueError, TypeError, KeyError) as error:
            # 0.3.0 rejects path spellings that Win32 aliases to another file.
            # A transaction prepared by an older release may still contain one;
            # never replay such an unauthenticated manifest under relaxed rules,
            # but persist a stable blocker instead of raising forever on recover.
            code = (
                "NONPORTABLE_LEGACY_PATH"
                if isinstance(error, NonPortablePathError)
                else "MANIFEST_INVALID"
            )
            _block_untrusted_transaction(root, transaction_id, code=code)
            return {
                "transaction_id": transaction_id,
                "status": "blocked",
                "direction": "forward" if _has_commit(transaction) else "rollback",
                "already_recovered": False,
                "code": code,
            }
        try:
            events = _read_wal(transaction / "wal.jsonl", tolerate_missing=True)
        except (OSError, UnicodeError, TransactionError):
            _block_transaction(
                root,
                manifest,
                code="WAL_CORRUPT",
                append_event=False,
            )
            return {
                "transaction_id": transaction_id,
                "status": "blocked",
                "direction": "forward" if _has_commit(transaction) else "rollback",
                "already_recovered": False,
            }
        names = _event_names(events)
        committed = _has_commit(transaction)
        direction = "forward" if committed else "rollback"

        observations = _observe_targets(root, manifest)
        conflicts: list[tuple[dict[str, Any], Path]] = []
        for target, actual in zip(manifest["targets"], observations, strict=True):
            allowed = {target["expected_prior"], target["candidate_hash"]}
            if actual not in allowed:
                destination = _project_path(root, target["path"])
                if destination.is_file():
                    conflicts.append((target, destination))
                else:
                    conflicts.append((target, destination))
        if conflicts:
            for target, destination in conflicts:
                if destination.is_file():
                    _preserve_conflict(root, manifest, target, destination.read_bytes())
            _block_transaction(root, manifest, code="EXTERNAL_EDIT_CONFLICT")
            return {
                "transaction_id": transaction_id,
                "status": "blocked",
                "direction": direction,
                "already_recovered": "BLOCKED" in names,
            }

        terminal = "STATE_APPLIED" if committed else "ROLLED_BACK"
        final_hashes = [
            target["candidate_hash"] if committed else target["expected_prior"]
            for target in manifest["targets"]
        ]
        state_complete = not committed or _state_satisfies_manifest(root, manifest)
        if terminal in names and observations == final_hashes and state_complete:
            return {
                "transaction_id": transaction_id,
                "status": "recovered",
                "direction": direction,
                "already_recovered": True,
            }

        materials: list[Path | None] = []
        try:
            for target, actual, final_hash in zip(
                manifest["targets"], observations, final_hashes, strict=True
            ):
                materials.append(
                    None if actual == final_hash else _material_for(root, target, direction)
                )
        except RecoveryMaterialError:
            _block_transaction(root, manifest, code="RECOVERY_MATERIAL_MISSING")
            return {
                "transaction_id": transaction_id,
                "status": "blocked",
                "direction": direction,
                "already_recovered": False,
            }

        for target, observed, final_hash, material in zip(
            manifest["targets"], observations, final_hashes, materials, strict=True
        ):
            if observed == final_hash:
                continue
            index = target["index"]
            destination = _project_path(root, target["path"])
            _fault(fault_injector, f"recovery:before_replace:{index}", transaction_id)
            try:
                if final_hash is None:
                    if _live_hash(destination) != observed:
                        raise TransactionConflictError("target changed during recovery")
                    destination.unlink()
                    _fsync_directory(destination.parent)
                else:
                    assert material is not None
                    _replace_from_file(material, destination, observed)
            except TransactionConflictError:
                if destination.is_file():
                    _preserve_conflict(root, manifest, target, destination.read_bytes())
                _block_transaction(root, manifest, code="EXTERNAL_EDIT_CONFLICT")
                return {
                    "transaction_id": transaction_id,
                    "status": "blocked",
                    "direction": direction,
                    "already_recovered": False,
                }
            _fault(fault_injector, f"recovery:after_replace:{index}", transaction_id)

        if committed:
            if "COMMIT" not in names:
                _append_wal(transaction / "wal.jsonl", {"event": "COMMIT"})
            _apply_snapshot_pointers(root, manifest)
            _fault(fault_injector, "recovery:after_pointers", transaction_id)
            if "POINTERS_APPLIED" not in names:
                _append_wal(transaction / "wal.jsonl", {"event": "POINTERS_APPLIED"})
            try:
                _apply_intended_lifecycle(root, manifest)
            except TransactionConflictError:
                # Every other conflict here becomes a blocked transaction. Left
                # bare, a layout clash raises past `recover_project`'s generic
                # handler without ever writing STATE_APPLIED, so the transaction
                # stays `needs_rollforward` and each later `recover` fails the
                # same way — with `blocked_transactions` empty, so no resolution
                # path is ever offered.
                _block_transaction(root, manifest, code="LAYOUT_CONFLICT")
                return {
                    "transaction_id": transaction_id,
                    "status": "blocked",
                    "direction": direction,
                    "already_recovered": False,
                }
            _fault(fault_injector, "recovery:after_lifecycle", transaction_id)
            if "STATE_APPLIED" not in names:
                _append_wal(transaction / "wal.jsonl", {"event": "STATE_APPLIED"})
        elif "ROLLED_BACK" not in names:
            _append_wal(transaction / "wal.jsonl", {"event": "ROLLED_BACK"})
        return {
            "transaction_id": transaction_id,
            "status": "recovered",
            "direction": direction,
            "already_recovered": False,
        }


def recover_project(path: Path) -> dict[str, Any]:
    root = find_project(path)
    transactions = root / ".short-drama/transactions"
    results = []
    if transactions.is_dir():
        for transaction in sorted(transactions.iterdir()):
            if transaction.is_dir() and re.fullmatch(r"[0-9a-f]{32}", transaction.name):
                status = _transaction_status(transaction)
                if status == "complete":
                    continue
                try:
                    results.append(recover_transaction(root, transaction.name))
                except (OSError, UnicodeError, ValueError, TransactionError):
                    results.append(
                        {
                            "transaction_id": transaction.name,
                            "status": "blocked",
                            "direction": "unknown",
                            "already_recovered": False,
                            "code": "TRANSACTION_METADATA_CORRUPT",
                        }
                    )
    return {
        "project_root": str(root),
        "checked": len(results),
        "blocked": sum(result["status"] == "blocked" for result in results),
        "results": results,
    }


def _validate_scene_scoped_record_path(relative: str, record: Any) -> None:
    """Keep the two per-scene directing layers attached to their filename.

    This is deliberately a narrow path/ref consistency check, not a schema
    validator. Blank JSONL files, non-object records, and records without a
    usable ``scene_ref`` keep their existing behavior.
    """

    pure = PurePosixPath(relative)
    if _root_role(pure.parts[0]) != "episodes" or len(pure.parts) != 5:
        return
    family = PurePosixPath(*[part.casefold() for part in pure.parts[2:4]]).as_posix()
    if family not in DECLARED_EPISODE_ARTIFACT_FAMILY_OWNERS:
        return
    expected_scene = pure.stem
    if SCENE_ID_TOKEN_RE.fullmatch(expected_scene) is None:
        raise ValueError(
            "scene-scoped directing filename must use an SC001-style identifier: "
            f"{relative}"
        )
    if not isinstance(record, dict):
        return
    scene_ref = record.get("scene_ref")
    values: list[str] = []
    if isinstance(scene_ref, str):
        values.append(scene_ref)
    elif isinstance(scene_ref, dict):
        values.extend(
            value
            for key in ("scene_id", "record_id")
            if isinstance((value := scene_ref.get(key)), str)
        )
    referenced_scenes = {
        match.group(0)
        for value in values
        for match in SCENE_ID_TOKEN_RE.finditer(value)
    }
    mismatches = sorted(referenced_scenes - {expected_scene})
    if mismatches:
        raise ValueError(
            f"filename {expected_scene} does not match scene_ref {mismatches[0]}: "
            f"{relative}"
        )


def _validate_candidate_content(relative: str, content: bytes) -> None:
    suffix = PurePosixPath(relative).suffix.lower()
    if suffix not in DELIVERY_SUFFIXES:
        raise ValueError(f"candidate must be Markdown, JSON, or JSONL: {relative}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"candidate must be UTF-8 text: {relative}") from error
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid candidate JSON: {relative}") from error
    elif suffix == ".jsonl":
        # Validate the path even when the JSONL is intentionally blank.
        _validate_scene_scoped_record_path(relative, None)
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid candidate JSONL at {relative}:{number}"
                ) from error
            _validate_scene_scoped_record_path(relative, record)


def _structured_candidate_refs(
    relative: str, content: bytes
) -> list[tuple[str, str, str | None]]:
    suffix = PurePosixPath(relative).suffix.lower()
    if suffix == ".md":
        return []
    text = content.decode("utf-8")
    if suffix == ".json":
        documents = [json.loads(text)]
    else:
        documents = [json.loads(line) for line in text.splitlines() if line.strip()]

    references: list[tuple[str, str, str | None]] = []

    def collect(value: Any, context: str | None = None) -> None:
        if isinstance(value, dict):
            owner = value.get("owner")
            artifact = value.get("artifact")
            digest = value.get("hash")
            context_is_ref = (
                isinstance(context, str)
                and context.casefold().endswith(("_ref", "_refs"))
                and not context.casefold().endswith(("_locator", "_locators"))
            )
            context_is_locator = isinstance(context, str) and context.casefold().endswith(
                ("_locator", "_locators")
            )
            has_artifact_ref_field = any(
                field in value for field in ("owner", "artifact", "hash")
            )
            complete_ref_shape = all(
                field in value for field in ("owner", "artifact", "hash")
            )
            ref_like = not context_is_locator and (
                (context_is_ref and has_artifact_ref_field) or complete_ref_shape
            )
            if ref_like:
                # A ref carrying an unfilled placeholder used to be skipped
                # here, so a candidate published straight from a template
                # contributed no dependency edges at all and the exact-input
                # cross-check below never ran: the less that was filled in, the
                # cleaner the publish looked. _normalize_artifact_ref already
                # rejects the same shape for lifecycle evidence refs.
                #
                # A `*_ref` / `*_refs` context carrying any ArtifactRef field
                # fails closed; a complete owner/artifact/hash object remains a
                # ref even at the document root or in a neutral container.
                # An arbitrary metadata `hash` alone is not a dependency, and
                # `*_locator` objects stay explicitly excluded.
                if not isinstance(owner, str) or not owner:
                    raise ValueError("structured ref owner is missing or invalid")
                if not isinstance(artifact, str) or not artifact:
                    raise ValueError("structured ref artifact is missing or invalid")
                if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                    raise ValueError(f"structured ref hash is unfilled or invalid: {artifact}")
                authority = value.get("authority")
                if authority not in {None, "accepted", "candidate"}:
                    raise ValueError(
                        f"structured ref authority is invalid: {_relative_path(artifact)}"
                    )
                references.append((_relative_path(artifact), digest, authority))
            for key, child in value.items():
                collect(child, key)
        elif isinstance(value, list):
            for child in value:
                collect(child, context)

    for document in documents:
        collect(document)
    return references


def _normalize_hash_mapping(values: Mapping[str, str], *, label: str) -> dict[str, str]:
    normalized: dict[str, str] = {}
    seen: dict[str, str] = {}
    for raw, value in values.items():
        relative = _relative_path(raw)
        _register_portable_path(seen, relative, label=label)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"invalid {label} hash for {relative}")
        if relative in normalized:
            raise ValueError(f"duplicate {label} path: {relative}")
        normalized[relative] = value
    if not normalized:
        raise ValueError(f"{label} cannot be empty")
    return dict(sorted(normalized.items()))


def _verify_live_hashes(root: Path, values: Mapping[str, str], *, label: str) -> None:
    for relative, expected in values.items():
        if _live_hash(_project_path(root, relative)) != expected:
            raise ValueError(f"{label} hash does not match live file: {relative}")


def _canonical_record_bytes(value: Any) -> bytes:
    """Serialize one record so key order and whitespace cannot change its hash."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _jsonl_records(content: bytes, relative: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(content.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ValueError(
                f"record binding needs parseable JSONL: {relative} line {number}"
            ) from error
        if not isinstance(record, dict):
            raise ValueError(
                f"record binding needs one object per line: {relative} line {number}"
            )
        records.append(record)
    return records


def _resolve_json_pointer(document: Any, pointer: str, relative: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(
            f"JSON record selector must be an RFC 6901 pointer: {relative} {pointer}"
        )
    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"record selector does not resolve: {relative} {pointer}")
            current = current[token]
        elif isinstance(current, list):
            if re.fullmatch(r"(?:0|[1-9][0-9]*)", token) is None or int(token) >= len(
                current
            ):
                raise ValueError(
                    f"record selector does not resolve: {relative} {pointer}"
                )
            current = current[int(token)]
        else:
            raise ValueError(f"record selector does not resolve: {relative} {pointer}")
    return current


def _record_digests(
    content: bytes,
    relative: str,
    selectors: Iterable[str],
    *,
    missing_ok: bool = False,
) -> dict[str, str | None]:
    """Hash the selected records inside one structured artifact.

    A JSONL selector is a record ID: the value of some top-level ``*_id`` field
    that occurs exactly once in the file, so no per-artifact schema is needed
    and an ambiguous ID is reported instead of guessed. A JSON selector is an
    RFC 6901 pointer. With ``missing_ok`` an unresolvable selector yields None
    rather than raising, which is what staleness narrowing needs: a record that
    vanished or turned ambiguous must invalidate its consumers, not crash.
    """

    wanted = list(selectors)
    suffix = PurePosixPath(relative).suffix.lower()
    if suffix not in {".json", ".jsonl"}:
        raise ValueError(
            f"record-level input binding needs a .json or .jsonl input: {relative}"
        )
    digests: dict[str, str | None] = {}
    try:
        if suffix == ".jsonl":
            records = _jsonl_records(content, relative)
            for selector in wanted:
                matches = [
                    record
                    for record in records
                    if any(
                        key.endswith("_id") and value == selector
                        for key, value in record.items()
                        if isinstance(value, str)
                    )
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"record selector must resolve exactly once: {relative} {selector}"
                    )
                digests[selector] = sha256_bytes(_canonical_record_bytes(matches[0]))
        else:
            try:
                document = json.loads(content.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeError) as error:
                raise ValueError(
                    f"record binding needs parseable JSON: {relative}"
                ) from error
            for selector in wanted:
                digests[selector] = sha256_bytes(
                    _canonical_record_bytes(
                        _resolve_json_pointer(document, selector, relative)
                    )
                )
    except ValueError:
        if not missing_ok:
            raise
        return {selector: digests.get(selector) for selector in wanted}
    return digests


def _normalize_record_selectors(
    values: Mapping[str, Iterable[str]] | None,
) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    seen: dict[str, str] = {}
    for raw, selectors in (values or {}).items():
        relative = _relative_path(raw)
        _register_portable_path(seen, relative, label="record binding")
        if relative in normalized:
            raise ValueError(f"duplicate record binding path: {relative}")
        unique: list[str] = []
        for selector in selectors:
            if not isinstance(selector, str) or not selector:
                raise ValueError(f"record selector is invalid: {relative}")
            if selector in unique:
                raise ValueError(f"duplicate record selector: {relative} {selector}")
            unique.append(selector)
        if not unique:
            raise ValueError(f"record binding needs at least one selector: {relative}")
        normalized[relative] = sorted(unique)
    return dict(sorted(normalized.items()))


def _input_record_bindings(record: Mapping[str, Any], key: str) -> dict[str, dict[str, str]]:
    raw = record.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"artifact {key} are invalid")
    normalized: dict[str, dict[str, str]] = {}
    seen: dict[str, str] = {}
    for path, bindings in raw.items():
        relative = _relative_path(path)
        _register_portable_path(seen, relative, label=f"artifact {key}")
        if not isinstance(bindings, dict) or not bindings:
            raise ValueError(f"artifact {key} entry is invalid: {relative}")
        if relative in normalized:
            raise ValueError(f"artifact {key} paths are duplicated: {relative}")
        selectors: dict[str, str] = {}
        for selector, digest in bindings.items():
            if not isinstance(selector, str) or not selector:
                raise ValueError(f"artifact {key} selector is invalid: {relative}")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError(
                    f"artifact {key} record hash is invalid: {relative} {selector}"
                )
            selectors[selector] = digest
        normalized[relative] = dict(sorted(selectors.items()))
    return dict(sorted(normalized.items()))


def _verify_live_records(
    root: Path,
    bindings: Mapping[str, Mapping[str, str]],
    *,
    label: str,
) -> None:
    for relative, selectors in bindings.items():
        path = _project_path(root, relative)
        if not path.is_file():
            raise ValueError(f"{label} record source is unavailable: {relative}")
        digests = _record_digests(path.read_bytes(), relative, selectors)
        for selector, expected in selectors.items():
            if digests.get(selector) != expected:
                raise ValueError(
                    f"{label} record hash does not match live file: {relative} {selector}"
                )


def _input_bindings(record: Mapping[str, Any], key: str) -> dict[str, str]:
    raw = record.get(key)
    if not isinstance(raw, dict):
        raise ValueError(f"artifact {key} are unavailable")
    normalized: dict[str, str] = {}
    seen: dict[str, str] = {}
    for path, expected in raw.items():
        relative = _relative_path(path)
        _register_portable_path(seen, relative, label=f"artifact {key}")
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError(f"artifact {key} hash is invalid: {relative}")
        if relative in normalized:
            raise ValueError(f"artifact {key} paths are duplicated: {relative}")
        normalized[relative] = expected
    return dict(sorted(normalized.items()))


def _validate_input_closure(
    root: Path,
    state: Mapping[str, Any],
    artifact_id: str,
    *,
    bindings: Mapping[str, str] | None = None,
    record_bindings: Mapping[str, Mapping[str, str]] | None = None,
    active: tuple[str, ...] = (),
) -> None:
    if artifact_id in active:
        raise ValueError("accepted input dependency cycle: " + " -> ".join((*active, artifact_id)))
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("accepted input artifact registry is unavailable")
    record = artifacts.get(artifact_id)
    if not isinstance(record, dict):
        raise ValueError(f"accepted input artifact is unavailable: {artifact_id}")
    inputs = dict(bindings) if bindings is not None else _input_bindings(record, "accepted_inputs")
    records = (
        {path: dict(selectors) for path, selectors in record_bindings.items()}
        if record_bindings is not None
        else _input_record_bindings(record, "accepted_input_records")
    )
    unknown_records = sorted(set(records) - set(inputs))
    if unknown_records:
        raise ValueError(
            "record binding has no matching input: " + ", ".join(unknown_records)
        )
    # A record-bound input is judged by its bound records, so an unrelated
    # append to the same shared file leaves this consumer current. The file
    # hash stays in accepted_inputs as the binding-time snapshot.
    _verify_live_hashes(
        root,
        {path: digest for path, digest in inputs.items() if path not in records},
        label="accepted input",
    )
    _verify_live_records(root, records, label="accepted input")
    for relative, expected in inputs.items():
        record_bound = relative in records
        path_owners: list[str] = []
        providers: list[str] = []
        for provider_id, provider in artifacts.items():
            if not isinstance(provider_id, str) or not isinstance(provider, dict):
                continue
            candidate_targets = provider.get("candidate_targets")
            accepted_targets = provider.get("accepted_targets")
            if (
                isinstance(candidate_targets, dict) and relative in candidate_targets
            ) or (isinstance(accepted_targets, dict) and relative in accepted_targets):
                path_owners.append(provider_id)
            if isinstance(accepted_targets, dict) and (
                relative in accepted_targets
                if record_bound
                else accepted_targets.get(relative) == expected
            ):
                providers.append(provider_id)
        if len(set(path_owners)) > 1 or len(providers) > 1:
            raise ValueError(f"accepted input provider is ambiguous: {relative}")
        if path_owners and not providers:
            raise ValueError(f"accepted input has no matching accepted provider: {relative}")
        if not providers:
            continue
        provider_id = providers[0]
        if provider_id == artifact_id:
            raise ValueError(f"accepted input dependency cycle at: {relative}")
        provider = artifacts[provider_id]
        if (
            provider.get("build_state") != "materialized"
            or provider.get("creator_acceptance") != "accepted"
        ):
            raise ValueError(f"accepted input provider is not current: {provider_id}")
        _validate_input_closure(
            root,
            state,
            provider_id,
            active=(*active, artifact_id),
        )


def _downstream_stale_changes(
    state: Mapping[str, Any],
    *,
    publishing_artifact: str,
    candidate_targets: Mapping[str, str | None],
    candidate_contents: Mapping[str, bytes] | None = None,
) -> dict[str, dict[str, str]]:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return {}
    contents = dict(candidate_contents or {})
    resolved: dict[str, dict[str, str | None]] = {}

    def records_survive(path: str, bound: Mapping[str, str]) -> bool:
        """True when every record this consumer bound is byte-identical in the
        new bytes. Without the new bytes — a removed path, or a target reached
        transitively — survival cannot be proven and the consumer goes stale."""

        content = contents.get(path)
        if content is None:
            return False
        cached = resolved.setdefault(path, {})
        missing = [selector for selector in bound if selector not in cached]
        if missing:
            cached.update(_record_digests(content, path, missing, missing_ok=True))
        return all(cached.get(selector) == digest for selector, digest in bound.items())

    affected: dict[str, str | None] = dict(candidate_targets)
    publishing_record = artifacts.get(publishing_artifact)
    if isinstance(publishing_record, dict):
        for key in ("accepted_targets", "candidate_targets"):
            previous_targets = publishing_record.get(key)
            if not isinstance(previous_targets, dict):
                continue
            for path in previous_targets:
                if path not in candidate_targets:
                    affected[path] = None
    stale: set[str] = set()
    changed = True
    while changed:
        changed = False
        for artifact_id in sorted(artifacts):
            if artifact_id == publishing_artifact or artifact_id in stale:
                continue
            record = artifacts.get(artifact_id)
            if not isinstance(record, dict):
                continue
            accepted_inputs = record.get("accepted_inputs")
            if not isinstance(accepted_inputs, dict):
                continue
            try:
                bound_records = _input_record_bindings(record, "accepted_input_records")
            except ValueError:
                bound_records = {}
            invalidated = False
            for path, expected in accepted_inputs.items():
                if path not in affected:
                    continue
                if affected[path] is not None and affected[path] == expected:
                    continue
                bound = bound_records.get(path)
                if bound and records_survive(path, bound):
                    continue
                invalidated = True
                break
            if not invalidated:
                continue
            stale.add(artifact_id)
            changed = True
            accepted_targets = record.get("accepted_targets")
            if isinstance(accepted_targets, dict):
                for path in accepted_targets:
                    affected[path] = None
    changes = {
        "build_state": "stale",
        "validation_state": "not_run",
        "independent_review": "not_requested",
        "delivery_gate": "blocked",
    }
    return {artifact_id: dict(changes) for artifact_id in sorted(stale)}


def _stale_lifecycle_changes() -> dict[str, str]:
    return {
        "build_state": "stale",
        "validation_state": "not_run",
        "creator_acceptance": "not_requested",
        "independent_review": "not_requested",
        "delivery_gate": "blocked",
    }


def _current_record_targets(record: Mapping[str, Any]) -> dict[str, str]:
    """Return the snapshot whose bytes should currently be materialized."""

    candidates = record.get("candidate_targets")
    if isinstance(candidates, dict) and candidates:
        return {
            str(path): str(digest)
            for path, digest in candidates.items()
            if isinstance(path, str) and isinstance(digest, str)
        }
    accepted = record.get("accepted_targets")
    if isinstance(accepted, dict):
        return {
            str(path): str(digest)
            for path, digest in accepted.items()
            if isinstance(path, str) and isinstance(digest, str)
        }
    return {}


def _effective_lifecycle_records(
    root: Path, artifacts: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Overlay live-hash drift so status never reports stale evidence as ready."""

    effective = {
        str(artifact_id): dict(record)
        for artifact_id, record in artifacts.items()
        if isinstance(artifact_id, str) and isinstance(record, dict)
    }
    direct_stale: list[tuple[str, dict[str, str | None]]] = []
    for artifact_id, record in effective.items():
        targets = _current_record_targets(record)
        changed: dict[str, str | None] = {}
        for relative, expected in targets.items():
            try:
                actual = _live_hash(_project_path(root, _relative_path(relative)))
            except (OSError, ValueError, TransactionConflictError):
                actual = None
            if actual != expected:
                changed[relative] = actual
        if changed:
            direct_stale.append((artifact_id, changed))

    stale_changes = _stale_lifecycle_changes()
    for artifact_id, changed in direct_stale:
        effective[artifact_id] = apply_lifecycle_changes(
            effective[artifact_id], stale_changes
        )
        downstream = _downstream_stale_changes(
            {"artifacts": artifacts},
            publishing_artifact=artifact_id,
            candidate_targets=changed,
        )
        for dependent in downstream:
            if dependent in effective:
                effective[dependent] = apply_lifecycle_changes(
                    effective[dependent], stale_changes
                )
    return effective
def project_path_lifecycle_at(
    directory_fd: int, relative: str | Path
) -> dict[str, Any] | None:
    """Return lifecycle evidence relative to a pinned project directory."""

    normalized = _relative_path(relative)
    try:
        state = json.loads(_read_regular_at(directory_fd, STATE_FILE).decode("utf-8"))
    except FileNotFoundError:
        return None
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    effective = _effective_lifecycle_records_at(directory_fd, artifacts)
    matches = [
        (artifact_id, record)
        for artifact_id, record in effective.items()
        if any(
            isinstance(record.get(key), dict) and normalized in record[key]
            for key in ("candidate_targets", "accepted_targets")
        )
    ]
    if len(matches) != 1:
        return None
    artifact_id, record = matches[0]
    return {
        "artifactId": artifact_id,
        **{axis: record.get(axis, LIFECYCLE_DEFAULTS[axis]) for axis in LIFECYCLE_STATES},
    }


def _open_or_create_directory_at(parent_fd: int, name: str) -> int:
    """Open a directory, creating it if absent, tolerating a concurrent creator.

    This runs *before* any lock is held — it is how the lock directory itself
    comes into being — so two callers can both find the directory missing and
    both try to create it. The loser of that race must open what the winner
    made, not fail.
    """

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    return os.open(name, flags, dir_fd=parent_fd)


def _open_or_create_lock_file_at(parent_fd: int, name: str) -> int:
    """Open the lock file, creating it if absent, tolerating a lost create race.

    On macOS an ``openat`` with ``O_CREAT | O_NOFOLLOW`` that loses a creation
    race against another opener returns ENOENT rather than opening the file the
    winner just made. This is the very first step of acquiring the lock, so it
    runs unserialized by construction: retry instead of surfacing a spurious
    "no such file" from a path that plainly exists.
    """

    for _ in range(_LOCK_OPEN_ATTEMPTS):
        try:
            return os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            continue
        except FileExistsError:
            continue
    raise TransactionConflictError(f"transaction lock is unavailable: {name}")


@contextlib.contextmanager
def _transaction_lock_at(directory_fd: int):
    operational_fd = _open_or_create_directory_at(directory_fd, ".short-drama")
    locks_fd = -1
    lock_fd = -1
    try:
        locks_fd = _open_or_create_directory_at(operational_fd, "locks")
        lock_fd = _open_or_create_lock_file_at(locks_fd, "transaction.lock")
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise TransactionConflictError("transaction lock is not a regular file")
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)
        if locks_fd >= 0:
            os.close(locks_fd)
        os.close(operational_fd)


def _atomic_json_at(
    directory_fd: int, relative: str | Path, document: Mapping[str, Any]
) -> None:
    pure = PurePosixPath(relative)
    parent_fd = _open_directory_at(directory_fd, pure.parts[:-1])
    temporary = f".{pure.name}.{uuid.uuid4().hex}.tmp"
    descriptor = -1
    replaced = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        encoded = (
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary, pure.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd
        )
        replaced = True
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not replaced:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent_fd)
        os.close(parent_fd)


def _record_working_text_edit_at(
    directory_fd: int, relative: str, digest: str
) -> None:
    try:
        state = json.loads(_read_regular_at(directory_fd, STATE_FILE).decode("utf-8"))
    except FileNotFoundError:
        return
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return
    owners = [
        artifact_id
        for artifact_id, record in artifacts.items()
        if isinstance(artifact_id, str)
        and isinstance(record, dict)
        and any(
            isinstance(record.get(key), dict) and relative in record[key]
            for key in ("candidate_targets", "accepted_targets")
        )
    ]
    if len(owners) > 1:
        raise TransactionConflictError(
            f"project path has multiple lifecycle owners: {relative}"
        )
    if not owners:
        return
    artifact_id = owners[0]
    record = artifacts[artifact_id]
    assert isinstance(record, dict)
    updated = apply_lifecycle_changes(record, _stale_lifecycle_changes())
    updated.pop("creator_decision", None)
    updated.pop("review_evidence", None)
    artifacts[artifact_id] = updated
    for dependent, changes in _downstream_stale_changes(
        state,
        publishing_artifact=artifact_id,
        candidate_targets={relative: digest},
    ).items():
        existing = artifacts.get(dependent)
        if isinstance(existing, dict):
            artifacts[dependent] = apply_lifecycle_changes(existing, changes)
    state["updated_at"] = utc_now()
    state["last_action"] = "working_text_edited"
    _atomic_json_at(directory_fd, STATE_FILE, state)
@contextlib.contextmanager
def coordinated_project_text_edit_at(
    directory_fd: int, relative: str | Path, expected_hash: str
):
    """Coordinate a Dashboard edit relative to a pinned project root."""

    normalized = _relative_path(relative)
    if not isinstance(expected_hash, str) or re.fullmatch(
        r"[0-9a-f]{64}", expected_hash
    ) is None:
        raise ValueError("expected hash must be a SHA-256 digest")
    with _transaction_lock_at(directory_fd):
        if _live_hash_at(directory_fd, normalized) != expected_hash:
            raise StaleReadSetError("file changed since it was opened")
        yield
        actual = _live_hash_at(directory_fd, normalized)
        if actual is None:
            raise TransactionConflictError("edited project file disappeared")
        if actual != expected_hash:
            _record_working_text_edit_at(directory_fd, normalized, actual)


def _normalize_artifact_ref(
    root: Path,
    reference: Mapping[str, Any],
    *,
    expected_owner: str | None = None,
) -> dict[str, Any]:
    owner = reference.get("owner")
    artifact = reference.get("artifact")
    digest = reference.get("hash")
    if not isinstance(owner, str) or re.fullmatch(r"[A-Za-z0-9._:-]+", owner) is None:
        raise ValueError("evidence ref owner is invalid")
    if expected_owner is not None and owner != expected_owner:
        raise ValueError(f"evidence ref owner must be {expected_owner}")
    if not isinstance(artifact, str):
        raise ValueError("evidence ref artifact is invalid")
    relative = _relative_path(artifact)
    _validate_existing_path_spelling(root, relative, label="evidence ref")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("evidence ref hash is invalid")
    if _live_hash(_project_path(root, relative)) != digest:
        raise ValueError(f"evidence ref hash does not match live file: {relative}")
    normalized: dict[str, Any] = {
        "owner": owner,
        "artifact": relative,
        "hash": digest,
    }
    for optional in ("record_id", "field"):
        value = reference.get(optional)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise ValueError(f"evidence ref {optional} is invalid")
            normalized[optional] = value
    if reference.get("authority") is not None:
        raise ValueError("lifecycle evidence must reference published authority")
    return normalized


def _validate_creator_decision_evidence(
    root: Path,
    reference: Mapping[str, Any],
    *,
    decision: str,
    artifact_id: str,
    target_hashes: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = _normalize_artifact_ref(root, reference, expected_owner="creator")
    evidence_path = _project_path(root, normalized["artifact"])
    suffix = evidence_path.suffix.lower()
    record: dict[str, Any]
    if suffix == ".jsonl":
        record_id = normalized.get("record_id")
        if not isinstance(record_id, str):
            raise ValueError("creator JSONL evidence requires a decision record_id")
        matches: list[dict[str, Any]] = []
        for number, line in enumerate(
            evidence_path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid creator decision JSONL at line {number}"
                ) from error
            if not isinstance(candidate, dict):
                raise ValueError("creator decision JSONL records must be objects")
            if candidate.get("decision_id") == record_id:
                matches.append(candidate)
        if len(matches) != 1:
            raise ValueError("creator decision record_id must resolve exactly once")
        record = matches[0]
    elif suffix == ".json":
        try:
            candidate = json.loads(evidence_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("invalid creator decision JSON evidence") from error
        if not isinstance(candidate, dict):
            raise ValueError("creator decision JSON evidence must be an object")
        record = candidate
        record_id = normalized.get("record_id")
        if record_id is not None and record.get("decision_id") != record_id:
            raise ValueError("creator decision record_id does not match JSON evidence")
    else:
        raise ValueError("creator decision evidence must be JSON or JSONL")

    evidence_decisions = [
        record[key].casefold()
        for key in ("status", "decision")
        if isinstance(record.get(key), str)
    ]
    if not evidence_decisions:
        raise ValueError("creator decision evidence has no status or decision")
    if any(value != decision for value in evidence_decisions):
        raise ValueError("creator evidence does not match creator decision")
    if record.get("decision_kind") != "artifact_acceptance":
        raise ValueError("creator evidence decision_kind must be artifact_acceptance")
    if record.get("artifact_id") != artifact_id:
        raise ValueError("creator evidence artifact_id does not match artifact")
    raw_targets = record.get("target_hashes")
    if not isinstance(raw_targets, dict):
        raise ValueError("creator evidence target_hashes must be an object")
    evidence_targets = _normalize_hash_mapping(
        raw_targets, label="creator evidence target"
    )
    if evidence_targets != dict(target_hashes):
        raise ValueError("creator evidence target_hashes do not match candidate targets")
    return normalized, record


def publish_candidate(
    path: Path,
    *,
    owner: str,
    artifact_id: str,
    outputs: Mapping[str, str | bytes],
    input_hashes: Mapping[str, str] | None = None,
    input_records: Mapping[str, Iterable[str]] | None = None,
    fault_injector: FaultInjector | None = None,
    allow_unregistered_path: bool = False,
) -> dict[str, Any]:
    """Publish a validated candidate without claiming creator or review authority.

    ``input_records`` narrows an input binding from the whole file to the
    records this candidate actually consumed, so an unrelated append to a
    shared setting record or project file no longer invalidates it.
    """

    root = find_project(path)
    if not isinstance(owner, str) or re.fullmatch(r"[A-Za-z0-9._:-]+", owner) is None:
        raise ValueError("owner must be an opaque identifier")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("artifact id cannot be empty")
    normalized_outputs: dict[str, bytes] = {}
    output_paths: dict[str, str] = {}
    for raw, value in outputs.items():
        relative = _relative_path(raw)
        _register_portable_path(output_paths, relative, label="candidate output")
        # Layout before content: a target that will be refused anyway should
        # say so, rather than first reporting that a file the creator never
        # meant to put there is not valid JSON.
        _validate_publication_layout(
            relative, owner=owner, allow_unregistered=allow_unregistered_path
        )
        _validate_existing_path_spelling(root, relative, label="candidate output")
        content = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        _validate_candidate_content(relative, content)
        normalized_outputs[relative] = content
    if not normalized_outputs:
        raise ValueError("a candidate publication needs at least one output")
    state = _read_state(root)
    artifacts = state["artifacts"]
    existing = artifacts.get(artifact_id, {})
    if isinstance(existing, dict) and existing.get("owner") not in (None, owner):
        raise ValueError("artifact owner cannot change during candidate publication")
    exact_inputs: dict[str, str] = {}
    input_paths: dict[str, str] = {}
    for raw, expected in (input_hashes or {}).items():
        relative = _relative_path(raw)
        _register_portable_path(input_paths, relative, label="input")
        _validate_existing_path_spelling(root, relative, label="input")
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError(f"invalid input hash for {relative}")
        if relative in exact_inputs:
            raise ValueError(f"duplicate input path: {relative}")
        exact_inputs[relative] = expected
    exact_inputs = dict(sorted(exact_inputs.items()))
    selectors = _normalize_record_selectors(input_records)
    _validate_new_path_set(root, selectors, label="record binding")
    unbound = sorted(set(selectors) - set(exact_inputs))
    if unbound:
        raise ValueError(
            "record binding needs an exact input: " + ", ".join(unbound)
        )
    read_records: dict[str, dict[str, str]] = {}
    for relative, wanted in selectors.items():
        source = _project_path(root, relative)
        if not source.is_file():
            raise ValueError(f"record binding source is unavailable: {relative}")
        content = source.read_bytes()
        if sha256_bytes(content) != exact_inputs[relative]:
            raise ValueError(f"record binding source does not match input: {relative}")
        digests = _record_digests(content, relative, wanted)
        read_records[relative] = {
            selector: digest
            for selector, digest in digests.items()
            if digest is not None
        }
    candidate_hashes = {
        relative: sha256_bytes(content)
        for relative, content in normalized_outputs.items()
    }
    structured_refs: list[tuple[str, str, str | None]] = []
    for output, content in normalized_outputs.items():
        structured_refs.extend(_structured_candidate_refs(output, content))
    ref_paths: dict[str, tuple[str, str, str | None]] = {}
    for referenced_path, referenced_hash, reference_authority in structured_refs:
        identity = _portable_path_identity(referenced_path)
        previous = ref_paths.get(identity)
        if previous is not None:
            if previous != (
                referenced_path,
                referenced_hash,
                reference_authority,
            ):
                raise ValueError(
                    "structured refs conflict or use nonportable path aliases: "
                    f"{previous[0]} and {referenced_path}"
                )
        else:
            ref_paths[identity] = (
                referenced_path,
                referenced_hash,
                reference_authority,
            )
        _validate_existing_path_spelling(root, referenced_path, label="structured ref")
    _validate_paths_against_tracked_state(
        state,
        [*candidate_hashes, *exact_inputs, *(ref[0] for ref in ref_paths.values())],
    )
    candidate_identities = {
        _portable_path_identity(path): path for path in candidate_hashes
    }
    input_identities = {_portable_path_identity(path): path for path in exact_inputs}
    for referenced_path, referenced_hash, reference_authority in ref_paths.values():
        reference_identity = _portable_path_identity(referenced_path)
        candidate_spelling = candidate_identities.get(reference_identity)
        if candidate_spelling is not None and candidate_spelling != referenced_path:
            raise ValueError(
                "structured ref aliases a same-publication output: "
                f"{referenced_path} conflicts with {candidate_spelling}"
            )
        input_spelling = input_identities.get(reference_identity)
        if input_spelling is not None and input_spelling != referenced_path:
            raise ValueError(
                "structured ref aliases an exact input: "
                f"{referenced_path} conflicts with {input_spelling}"
            )
        if referenced_path in candidate_hashes:
            if reference_authority != "candidate":
                raise ValueError(
                    "same-publication ref must declare candidate authority: "
                    f"{referenced_path}"
                )
            if candidate_hashes[referenced_path] != referenced_hash:
                raise ValueError(
                    "same-publication ref hash does not match candidate output: "
                    f"{referenced_path}"
                )
            continue
        if reference_authority == "candidate":
            accepted_provider = any(
                isinstance(record, dict)
                and isinstance(record.get("accepted_targets"), dict)
                and record["accepted_targets"].get(referenced_path)
                == referenced_hash
                for record in artifacts.values()
            )
            candidate_provider = any(
                isinstance(record, dict)
                and isinstance(record.get("candidate_targets"), dict)
                and record["candidate_targets"].get(referenced_path)
                == referenced_hash
                for record in artifacts.values()
            )
            if accepted_provider:
                raise ValueError(
                    "accepted input cannot declare candidate authority: "
                    f"{referenced_path}"
                )
            if not candidate_provider:
                raise ValueError(
                    "candidate input has no matching candidate provider: "
                    f"{referenced_path}"
                )
        if referenced_path not in exact_inputs:
            raise ValueError(f"structured ref requires exact input: {referenced_path}")
        if exact_inputs[referenced_path] != referenced_hash:
            raise ValueError(
                f"structured ref input hash does not match: {referenced_path}"
            )
    lifecycle_changes = {
        artifact_id: {
            "build_state": "materialized",
            "validation_state": "not_run",
            "creator_acceptance": "pending",
            "independent_review": "provisional",
            "delivery_gate": "blocked",
        },
        **_downstream_stale_changes(
            state,
            publishing_artifact=artifact_id,
            candidate_targets=candidate_hashes,
            candidate_contents=normalized_outputs,
        ),
    }
    transaction = publish_transaction(
        root,
        stage="candidate",
        outputs=normalized_outputs,
        lifecycle_changes=lifecycle_changes,
        target_artifacts={relative: artifact_id for relative in normalized_outputs},
        read_set=exact_inputs,
        read_records=read_records,
        authority="candidate",
        owner=owner,
        allow_unregistered_path=allow_unregistered_path,
        fault_injector=fault_injector,
    )
    return {
        **transaction,
        "authority": "candidate",
        "owner": owner,
        "artifact_id": artifact_id,
    }


def record_creator_acceptance(
    path: Path,
    *,
    artifact_id: str,
    decision: str,
    target_hashes: Mapping[str, str],
    evidence_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Record a creator decision against one exact candidate snapshot."""

    root = find_project(path)
    normalized_decision = decision.casefold()
    if normalized_decision not in {"accepted", "rejected"}:
        raise ValueError("creator decision must be accepted or rejected")
    targets = _normalize_hash_mapping(target_hashes, label="creator decision target")
    with _transaction_lock(root):
        state = _read_state(root)
        artifacts = state.setdefault("artifacts", {})
        record = artifacts.get(artifact_id)
        if not isinstance(record, dict):
            raise ValueError(f"unknown candidate artifact: {artifact_id}")
        candidates = record.get("candidate_targets")
        if not isinstance(candidates, dict) or candidates != targets:
            raise ValueError("creator decision does not match exact candidate targets")
        _verify_live_hashes(root, targets, label="candidate target")
        candidate_inputs = _input_bindings(record, "candidate_inputs")
        candidate_input_records = _input_record_bindings(record, "candidate_input_records")
        _validate_input_closure(
            root,
            state,
            artifact_id,
            bindings=candidate_inputs,
            record_bindings=candidate_input_records,
        )
        evidence, _decision_record = _validate_creator_decision_evidence(
            root,
            evidence_ref,
            decision=normalized_decision,
            artifact_id=artifact_id,
            target_hashes=targets,
        )
        if evidence["artifact"] in targets:
            raise ValueError("creator decision evidence must be separate from its target")
        updated = apply_lifecycle_changes(
            record,
            {
                "creator_acceptance": normalized_decision,
                "independent_review": "not_requested",
                "delivery_gate": "blocked",
            },
        )
        updated["creator_decision"] = {
            "decision": normalized_decision,
            "target_hashes": targets,
            "evidence_ref": evidence,
        }
        updated.pop("review_evidence", None)
        if normalized_decision == "accepted":
            snapshots = updated.get("candidate_snapshots")
            if not isinstance(snapshots, dict) or set(snapshots) != set(targets):
                raise RecoveryMaterialError("candidate snapshots are incomplete")
            for relative, digest in targets.items():
                snapshot = _project_path(
                    root, _relative_path(snapshots[relative], allow_operations=True)
                )
                if not snapshot.is_file() or sha256_file(snapshot) != digest:
                    raise RecoveryMaterialError("candidate snapshot is missing or corrupt")
            updated["accepted_targets"] = targets
            updated["accepted_snapshots"] = dict(sorted(snapshots.items()))
            updated["accepted_inputs"] = candidate_inputs
            if candidate_input_records:
                updated["accepted_input_records"] = candidate_input_records
            else:
                updated.pop("accepted_input_records", None)
            material = json.dumps(
                targets, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            updated["accepted_snapshot"] = sha256_bytes(material)
        artifacts[artifact_id] = updated
        state["updated_at"] = utc_now()
        state["last_action"] = "creator_acceptance_recorded"
        atomic_json(root / STATE_FILE, state)
    return {
        "artifact_id": artifact_id,
        "creator_acceptance": normalized_decision,
        "target_count": len(targets),
        "status": "recorded",
    }


def _normalize_review_verdict(value: str) -> str:
    normalized = value.casefold().replace("-", "_")
    if normalized not in {"approve", "approve_with_notes", "revise", "provisional"}:
        raise ValueError("invalid independent review verdict")
    return normalized


def _normalize_reviewer_evidence(
    raw_reviewer: Any,
    *,
    verdict_owner: str,
    artifact_owner: str,
    require_independent: bool = True,
) -> dict[str, Any]:
    if not isinstance(raw_reviewer, dict):
        raise ValueError("reviewer evidence must be an object")
    reviewer_owner = raw_reviewer.get("owner")
    reviewer_kind = raw_reviewer.get("kind")
    excluded = raw_reviewer.get("excluded_owner_skills")
    if reviewer_owner != verdict_owner:
        raise ValueError("reviewer owner does not match verdict owner")
    if (
        not isinstance(excluded, list)
        or any(not isinstance(owner, str) or not owner for owner in excluded)
        or len(excluded) != len(set(excluded))
        or set(excluded) != {artifact_owner}
    ):
        raise ValueError("reviewer excluded owner must match artifact owner")
    if not require_independent:
        if reviewer_kind not in {"self_check", "unattested"}:
            raise ValueError("provisional reviewer kind must be self_check or unattested")
        if raw_reviewer.get("independent") is not False:
            raise ValueError("provisional reviewer must not assert independence")
        if raw_reviewer.get("provenance") is not None:
            raise ValueError("provisional reviewer must not claim fresh provenance")
        return {
            "owner": reviewer_owner,
            "kind": reviewer_kind,
            "independent": False,
            "excluded_owner_skills": list(excluded),
            "provenance": None,
        }

    provenance = raw_reviewer.get("provenance")
    if reviewer_kind != "independent_agent":
        raise ValueError("reviewer kind must be independent_agent")
    if raw_reviewer.get("independent") is not True:
        raise ValueError("reviewer does not assert independence")
    if not isinstance(provenance, dict):
        raise ValueError("reviewer fresh-context provenance is missing")
    context_id = provenance.get("context_id")
    if not isinstance(context_id, str) or not context_id.strip():
        raise ValueError("reviewer fresh-context provenance has no context_id")
    if provenance.get("fresh_context") is not True:
        raise ValueError("reviewer context is not fresh")
    if provenance.get("authored_reviewed_artifacts") is not False:
        raise ValueError("reviewer authored a reviewed artifact")
    return {
        "owner": reviewer_owner,
        "kind": reviewer_kind,
        "independent": True,
        "excluded_owner_skills": list(excluded),
        "provenance": {
            "context_id": context_id,
            "fresh_context": True,
            "authored_reviewed_artifacts": False,
        },
    }


def _open_blocking_finding_ids(root: Path, findings_ref: Mapping[str, Any]) -> set[str]:
    relative = str(findings_ref["artifact"])
    if PurePosixPath(relative).suffix.lower() != ".jsonl":
        raise ValueError("verdict findings_ref must reference JSONL")
    findings: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(
        _project_path(root, relative).read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            finding = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid findings JSONL at line {number}") from error
        if not isinstance(finding, dict):
            raise ValueError("findings JSONL records must be objects")
        finding_id = finding.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id:
            raise ValueError("findings JSONL record has no finding_id")
        if finding_id in findings:
            raise ValueError(f"findings JSONL duplicates finding_id: {finding_id}")
        status = finding.get("status")
        severity = finding.get("severity")
        if not isinstance(status, str) or status.casefold() not in {
            "open",
            "closed",
            "superseded",
        }:
            raise ValueError(f"findings JSONL status is invalid: {finding_id}")
        if not isinstance(severity, str) or severity.casefold() not in {
            "fatal",
            "error",
            "warning",
            "note",
        }:
            raise ValueError(f"findings JSONL severity is invalid: {finding_id}")
        findings[finding_id] = finding
    return {
        finding_id
        for finding_id, finding in findings.items()
        if str(finding.get("status", "")).casefold() == "open"
        and (
            str(finding.get("severity", "")).casefold()
            in {"fatal", "error"}
            or finding.get("blocking") is True
        )
    }


def _validate_review_verdict_evidence(
    root: Path,
    *,
    artifact_owner: str,
    verdict: str,
    reviewed_targets: Mapping[str, str],
    verdict_ref: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    reference = _normalize_artifact_ref(root, verdict_ref)
    if reference["owner"] == artifact_owner:
        raise ValueError("independent review owner must differ from artifact owner")
    if PurePosixPath(reference["artifact"]).suffix.lower() != ".json":
        raise ValueError("independent review verdict must be a JSON artifact")
    try:
        document = json.loads(
            _project_path(root, reference["artifact"]).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("independent review verdict artifact is invalid") from error
    if not isinstance(document, dict):
        raise ValueError("independent review verdict must be a JSON object")
    if document.get("requested_review_mode") != "independent_agent":
        raise ValueError("verdict did not request an independent agent")
    effective_review_mode = document.get("effective_review_mode")
    provisional = verdict == "provisional"
    allowed_effective_modes = (
        {"self_check", "unattested"} if provisional else {"fresh_agent"}
    )
    if effective_review_mode not in allowed_effective_modes:
        raise ValueError("verdict effective review mode is incompatible with verdict")
    if _normalize_review_verdict(str(document.get("verdict", ""))) != verdict:
        raise ValueError("review verdict does not match its evidence artifact")
    reviewer = _normalize_reviewer_evidence(
        document.get("reviewer"),
        verdict_owner=reference["owner"],
        artifact_owner=artifact_owner,
        require_independent=not provisional,
    )
    if document.get("required_reviewer_independence") is not True:
        raise ValueError("verdict does not assert required reviewer independence")
    structural_validation = document.get("structural_validation")
    allowed_validation = {"pass", "pass_with_warnings", "fail"}
    if provisional:
        allowed_validation.add("not_run")
    if structural_validation not in allowed_validation:
        raise ValueError("verdict structural_validation is invalid")
    if (
        verdict in {"approve", "approve_with_notes"}
        and structural_validation not in {"pass", "pass_with_warnings"}
    ):
        raise ValueError("approval verdict requires structural validation pass")
    raw_findings_ref = document.get("findings_ref")
    if not isinstance(raw_findings_ref, dict):
        raise ValueError("verdict findings_ref is missing")
    findings_ref = _normalize_artifact_ref(
        root, raw_findings_ref, expected_owner=reference["owner"]
    )
    if findings_ref["artifact"] == reference["artifact"]:
        raise ValueError("verdict findings_ref must reference a separate artifact")
    reviewed = document.get("reviewed_artifacts")
    if not isinstance(reviewed, list) or not reviewed:
        raise ValueError("verdict reviewed_artifacts must be a non-empty array")
    evidence_targets: dict[str, str] = {}
    for raw_reference in reviewed:
        if not isinstance(raw_reference, dict):
            raise ValueError("verdict reviewed artifact ref is invalid")
        target_reference = _normalize_artifact_ref(
            root, raw_reference, expected_owner=artifact_owner
        )
        artifact = target_reference["artifact"]
        if artifact in evidence_targets:
            raise ValueError("verdict reviewed artifact refs are duplicated")
        evidence_targets[artifact] = target_reference["hash"]
    if dict(sorted(evidence_targets.items())) != dict(reviewed_targets):
        raise ValueError("verdict does not bind the exact reviewed target hashes")
    blockers = document.get("blocking_findings")
    if (
        not isinstance(blockers, list)
        or any(not isinstance(finding_id, str) or not finding_id for finding_id in blockers)
        or len(blockers) != len(set(blockers))
    ):
        raise ValueError("verdict blocking_findings must be an array")
    open_blockers = _open_blocking_finding_ids(root, findings_ref)
    if set(blockers) != open_blockers:
        raise ValueError("verdict blocking_findings do not match findings snapshot")
    blocker_count = document.get("open_blocker_count")
    if type(blocker_count) is not int or blocker_count != len(blockers):
        raise ValueError("verdict open_blocker_count does not match blocking findings")
    if verdict in {"approve", "approve_with_notes"} and blocker_count != 0:
        raise ValueError("approval verdict has an open blocking finding")
    return reference, document, reviewer, findings_ref


def record_independent_review(
    path: Path,
    *,
    artifact_id: str,
    verdict: str,
    reviewed_targets: Mapping[str, str],
    verdict_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Record an independent verdict bound to accepted bytes and its JSON proof."""

    root = find_project(path)
    normalized_verdict = _normalize_review_verdict(verdict)
    targets = _normalize_hash_mapping(reviewed_targets, label="review target")
    with _transaction_lock(root):
        state = _read_state(root)
        artifacts = state.setdefault("artifacts", {})
        record = artifacts.get(artifact_id)
        if not isinstance(record, dict):
            raise ValueError(f"unknown accepted artifact: {artifact_id}")
        owner = record.get("owner")
        if not isinstance(owner, str):
            raise ValueError("accepted artifact owner is unavailable")
        accepted = record.get("accepted_targets")
        if not isinstance(accepted, dict) or accepted != targets:
            raise ValueError("review does not match exact accepted targets")
        creator_decision = record.get("creator_decision")
        if (
            not isinstance(creator_decision, dict)
            or creator_decision.get("decision") != "accepted"
            or creator_decision.get("target_hashes") != targets
        ):
            raise ValueError("review requires exact creator acceptance evidence")
        _validate_creator_decision_evidence(
            root,
            creator_decision.get("evidence_ref", {}),
            decision="accepted",
            artifact_id=artifact_id,
            target_hashes=targets,
        )
        _validate_input_closure(root, state, artifact_id)
        _verify_live_hashes(root, targets, label="review target")
        reference, _document, reviewer, findings_ref = _validate_review_verdict_evidence(
            root,
            artifact_owner=owner,
            verdict=normalized_verdict,
            reviewed_targets=targets,
            verdict_ref=verdict_ref,
        )
        gate = (
            "ready"
            if normalized_verdict in {"approve", "approve_with_notes"}
            else "blocked"
        )
        updated = apply_lifecycle_changes(
            record,
            {
                "validation_state": _document["structural_validation"],
                "independent_review": normalized_verdict,
                "delivery_gate": gate,
            },
        )
        updated["review_evidence"] = {
            "verdict": normalized_verdict,
            "structural_validation": _document["structural_validation"],
            "reviewed_targets": targets,
            "verdict_ref": reference,
            "findings_ref": findings_ref,
            "reviewer_independence": {
                "artifact_owner": owner,
                "reviewer_owner": reference["owner"],
                "kind": reviewer["kind"],
                "independent": reviewer["independent"],
                "excluded_owner_skills": reviewer["excluded_owner_skills"],
                "provenance": reviewer["provenance"],
                "requested_review_mode": _document["requested_review_mode"],
                "effective_review_mode": _document["effective_review_mode"],
                "attestation_structure_valid": reviewer["independent"],
                "verification_scope": "declared_provenance_structure",
            },
        }
        artifacts[artifact_id] = updated
        state["updated_at"] = utc_now()
        state["last_action"] = "independent_review_recorded"
        atomic_json(root / STATE_FILE, state)
    return {
        "artifact_id": artifact_id,
        "independent_review": normalized_verdict,
        "target_count": len(targets),
        "status": "recorded",
    }


def _validate_delivery_text(
    content: bytes,
    suffix: str,
    relative: str,
    allowed_urls: set[str],
) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageBlockedError(f"delivery file is not UTF-8 text: {relative}") from error
    structured_documents: list[Any] = []
    if suffix == ".json":
        try:
            structured_documents.append(json.loads(text))
        except json.JSONDecodeError as error:
            raise PackageBlockedError(f"invalid delivery JSON: {relative}") from error
    elif suffix == ".jsonl":
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                structured_documents.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise PackageBlockedError(
                    f"invalid delivery JSONL at {relative}:{number}"
                ) from error

    credential_fields = {
        "apikey",
        "accesstoken",
        "authtoken",
        "bearertoken",
        "clientsecret",
        "password",
        "privatekey",
        "secretkey",
    }

    def reject_structured_credentials(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                if normalized in credential_fields:
                    raise PackageBlockedError(
                        f"credential field is excluded from delivery: {relative}"
                    )
                reject_structured_credentials(child)
        elif isinstance(value, list):
            for child in value:
                reject_structured_credentials(child)

    for document in structured_documents:
        reject_structured_credentials(document)

    # file URLs and private keys have no legitimate on-screen use, so they stay
    # unconditional blocks. A machine path can be genuine story content (a
    # hacking or investigation episode showing a path on screen), so it keeps
    # the same declared-exception channel the URL rule uses: default blocked,
    # released only for an exact text the creator bound to a path and field.
    unsafe_patterns = {
        "file URL": re.compile(r"\bfile://", re.IGNORECASE),
        "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    }
    for label, pattern in unsafe_patterns.items():
        if pattern.search(text):
            raise PackageBlockedError(f"{label} is excluded from delivery: {relative}")
    # Match the whole path token, not just its prefix. Containment is checked
    # against the full token, so declaring a bare prefix cannot release every
    # path that happens to share it.
    exempt_spans = [
        (match.start(), match.end())
        for allowed in allowed_urls
        for match in re.finditer(re.escape(allowed), text)
    ]
    for match in MACHINE_PATH_TOKEN_RE.finditer(text):
        covered = any(
            start <= match.start() and match.end() <= end
            for start, end in exempt_spans
        )
        if not covered:
            raise PackageBlockedError(
                f"machine path is excluded from delivery: {relative}"
            )
    url_pattern = re.compile(r"https?://[^\s<>\"'\])}，。；]+", re.IGNORECASE)
    disallowed = sorted(set(url_pattern.findall(text)) - allowed_urls)
    if disallowed:
        raise PackageBlockedError(f"URL-like text needs an explicit exception: {relative}")


def _normalize_text_exceptions(
    text_exceptions: Iterable[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    normalized: list[dict[str, Any]] = []
    allowed: dict[str, set[str]] = {}
    provenance_allowlist = {"creator_supplied", "story_world_authored"}
    text_policy_allowlist = {"visible_on_screen", "fictional_interface_text"}
    url_pattern = re.compile(r"https?://[^\s<>\"'\])}，。；]+", re.IGNORECASE)
    path_spellings: dict[str, str] = {}
    # An exception releases either a complete URL or an exact on-screen string
    # whose machine paths are quoted in full. A declaration that is only a path
    # prefix (or that carries no complete path token) is rejected, so it cannot
    # act as a wildcard over every path sharing that prefix.
    for exception in text_exceptions or []:
        exact = exception.get("exact_text")
        bound_path = exception.get("path")
        field = exception.get("field")
        if (
            not isinstance(exact, str)
            or not exact
            # A complete URL is inherently a single token, so the length bound
            # only needs to constrain free-form on-screen strings.
            or (
                len(exact) > MAX_TEXT_EXCEPTION_LENGTH
                and url_pattern.fullmatch(exact) is None
            )
            # Any line break or control character, not just \n: a declaration
            # spanning lines is a document, not a single on-screen string.
            or any(character < " " or character in "\x7f\x85  " for character in exact)
            or (
                url_pattern.fullmatch(exact) is None
                and MACHINE_PATH_COMPLETE_RE.search(exact) is None
            )
            or not isinstance(bound_path, str)
            or not isinstance(field, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", field)
            or exception.get("purpose") != "on_screen_text"
            or exception.get("provenance") not in provenance_allowlist
            or exception.get("text_policy") not in text_policy_allowlist
            or exception.get("allow_delivery") is not True
        ):
            raise PackageBlockedError("invalid on-screen text delivery exception")
        relative = _relative_path(bound_path)
        _register_portable_path(
            path_spellings,
            relative,
            label="text exception",
            allow_exact_duplicate=True,
        )
        record = {
            "exact_text": exact,
            "path": relative,
            "field": field,
            "purpose": "on_screen_text",
            "provenance": exception["provenance"],
            "text_policy": exception["text_policy"],
            "allow_delivery": True,
        }
        normalized.append(record)
        allowed.setdefault(relative, set()).add(exact)
    normalized.sort(key=lambda item: (item["path"], item["field"], item["exact_text"]))
    return normalized, allowed


def _validate_delivery_evidence(
    root: Path,
    state: Mapping[str, Any],
    artifact_id: str,
    record: Mapping[str, Any],
) -> None:
    accepted = record.get("accepted_targets")
    if not isinstance(accepted, dict) or not accepted:
        raise PackageBlockedError(
            f"creator decision evidence has no accepted targets: {artifact_id}"
        )
    creator_decision = record.get("creator_decision")
    if (
        not isinstance(creator_decision, dict)
        or creator_decision.get("decision") != "accepted"
        or creator_decision.get("target_hashes") != accepted
    ):
        raise PackageBlockedError(
            f"creator decision evidence is missing or stale: {artifact_id}"
        )
    try:
        _validate_creator_decision_evidence(
            root,
            creator_decision.get("evidence_ref", {}),
            decision="accepted",
            artifact_id=artifact_id,
            target_hashes=accepted,
        )
    except ValueError as error:
        raise PackageBlockedError(
            f"creator decision evidence is invalid: {artifact_id}"
        ) from error
    try:
        _validate_input_closure(root, state, artifact_id)
    except ValueError as error:
        raise PackageBlockedError(
            f"accepted input evidence is stale: {artifact_id}"
        ) from error

    review = record.get("review_evidence")
    verdict = record.get("independent_review")
    owner = record.get("owner")
    if (
        not isinstance(review, dict)
        or verdict not in {"approve", "approve_with_notes"}
        or review.get("verdict") != verdict
        or review.get("structural_validation") != record.get("validation_state")
        or review.get("reviewed_targets") != accepted
        or not isinstance(owner, str)
    ):
        raise PackageBlockedError(
            f"independent review evidence is missing or stale: {artifact_id}"
        )
    independence = review.get("reviewer_independence")
    if (
        not isinstance(independence, dict)
        or independence.get("attestation_structure_valid") is not True
        or independence.get("verification_scope")
        != "declared_provenance_structure"
        or independence.get("artifact_owner") != owner
        or independence.get("reviewer_owner") == owner
        or independence.get("independent") is not True
        or independence.get("excluded_owner_skills") != [owner]
    ):
        raise PackageBlockedError(
            f"reviewer independence evidence is invalid: {artifact_id}"
        )
    try:
        reference, _document, reviewer, findings_ref = _validate_review_verdict_evidence(
            root,
            artifact_owner=owner,
            verdict=verdict,
            reviewed_targets=accepted,
            verdict_ref=review.get("verdict_ref", {}),
        )
    except ValueError as error:
        raise PackageBlockedError(
            f"independent review verdict evidence is invalid: {artifact_id}"
        ) from error
    if independence.get("reviewer_owner") != reference["owner"]:
        raise PackageBlockedError(
            f"reviewer independence evidence is stale: {artifact_id}"
        )
    if (
        independence.get("kind") != reviewer["kind"]
        or independence.get("excluded_owner_skills")
        != reviewer["excluded_owner_skills"]
        or review.get("findings_ref") != findings_ref
        or review.get("structural_validation")
        != _document["structural_validation"]
    ):
        raise PackageBlockedError(
            f"reviewer or findings evidence is stale: {artifact_id}"
        )


def _approved_artifact_for_path(
    root: Path, state: dict[str, Any], relative: str, current_hash: str
) -> str:
    matches: list[str] = []
    for artifact_id, record in state.get("artifacts", {}).items():
        if not isinstance(record, dict):
            continue
        accepted = record.get("accepted_targets", {})
        if isinstance(accepted, dict) and accepted.get(relative) == current_hash:
            matches.append(artifact_id)
    if len(matches) != 1:
        raise PackageBlockedError(f"selected file has no unique accepted snapshot: {relative}")
    artifact_id = matches[0]
    record = state["artifacts"][artifact_id]
    required = {
        "build_state": {"materialized"},
        "validation_state": {"pass", "pass_with_warnings"},
        "creator_acceptance": {"accepted"},
        "independent_review": {"approve", "approve_with_notes"},
        "delivery_gate": {"ready", "delivered"},
    }
    failures = [axis for axis, values in required.items() if record.get(axis) not in values]
    if failures:
        raise PackageBlockedError(
            f"selected artifact is not delivery-ready ({', '.join(failures)}): {relative}"
        )
    _validate_delivery_evidence(root, state, artifact_id, record)
    return artifact_id


DELIVERY_READY = {
    "build_state": {"materialized"},
    "validation_state": {"pass", "pass_with_warnings"},
    "creator_acceptance": {"accepted"},
    "independent_review": {"approve", "approve_with_notes"},
    "delivery_gate": {"ready", "delivered"},
}


def _episode_coverage(
    state: Mapping[str, Any], episode: str
) -> dict[str, dict[str, Any]]:
    """Enumerate every accepted file this episode already has.

    Completeness cannot be judged from the selection alone: a hand-written
    include list looks equally complete whether or not it forgot the keyframe
    prompts. The project state already knows which files exist under the
    episode, so the enumeration belongs here rather than in someone's memory.
    """

    # Casefolded: on a case-insensitive filesystem an artifact accepted as
    # `Episodes/EP001/…` is the same file as `episodes/EP001/…`, and a
    # case-sensitive prefix would skip it — leaving nothing to reconcile and
    # passing the completeness gate on an episode it never enumerated.
    coverage: dict[str, dict[str, Any]] = {}
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return coverage
    for artifact_id, record in artifacts.items():
        if not isinstance(artifact_id, str) or not isinstance(record, dict):
            continue
        if artifact_id.startswith("delivery:"):
            continue
        accepted = record.get("accepted_targets")
        if not isinstance(accepted, dict):
            continue
        ready = all(
            record.get(axis) in values for axis, values in DELIVERY_READY.items()
        )
        for relative in accepted:
            if not isinstance(relative, str):
                continue
            pure = PurePosixPath(relative)
            if (
                len(pure.parts) < 3
                or _root_role(pure.parts[0]) != "episodes"
                or pure.parts[1].casefold() != episode.casefold()
            ):
                continue
            coverage[relative] = {"artifact_id": artifact_id, "ready": ready}
    return dict(sorted(coverage.items()))


def build_delivery_package(
    path: Path,
    *,
    episode: str,
    selected_paths: Iterable[str | Path],
    text_exceptions: Iterable[Mapping[str, Any]] | None = None,
    omitted_paths: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    root = find_project(path)
    if EPISODE_ID_RE.fullmatch(episode) is None:
        raise ValueError("episode must use an EP001-style identifier")
    try:
        exceptions, allowed_urls_by_path = _normalize_text_exceptions(text_exceptions)
    except ValueError as error:
        raise PackageBlockedError(str(error)) from error
    state = _read_state(root)
    files: list[dict[str, Any]] = []
    outputs: dict[str, bytes] = {}
    source_artifacts: set[str] = set()
    try:
        normalized_selected = _normalize_portable_path_values(
            root, selected_paths, label="delivery selection"
        )
        _validate_paths_against_tracked_state(
            state, [*normalized_selected, *allowed_urls_by_path]
        )
    except ValueError as error:
        raise PackageBlockedError(str(error)) from error
    selected_episode_roots = {
        PurePosixPath(relative).parts[0]
        for relative in normalized_selected
        if _root_role(PurePosixPath(relative).parts[0]) == "episodes"
    }
    if len(selected_episode_roots) > 1:
        raise PackageBlockedError("不能在同一交付包中混用中文与旧版分集目录")
    source_episode_root = next(iter(selected_episode_roots), None)
    delivery_root = _layout_root_for_source(
        root,
        "delivery",
        CANONICAL_ROOTS["delivery"]
        if source_episode_root == CANONICAL_ROOTS["episodes"]
        else LEGACY_ROOTS["delivery"]
        if source_episode_root is not None
        else None,
    )
    for raw in normalized_selected:
        pure = PurePosixPath(raw)
        if pure.parts[0].casefold() in PROTECTED_PUBLISH_ROOTS:
            raise PackageBlockedError(f"private or operational zone excluded: {raw}")
        # A selection naming episodes/ep1/ would be prefix-skipped by
        # _episode_coverage, so the completeness reconciliation below would see
        # nothing to reconcile and pass on an episode it never enumerated.
        if _root_role(pure.parts[0]) == "episodes" and (
            len(pure.parts) < 3 or EPISODE_ID_RE.fullmatch(pure.parts[1]) is None
        ):
            raise PackageBlockedError(
                f"分集选择必须使用 剧集/<EP>/（兼容 episodes/<EP>/）：{raw}"
            )
        lowered_parts = {part.casefold() for part in pure.parts}
        if "research" in lowered_parts or "research-notes.md" in lowered_parts:
            raise PackageBlockedError(f"optional research notes are excluded: {raw}")
        source = _project_path(root, raw)
        if source.is_symlink() or not source.is_file():
            raise PackageBlockedError(f"selected delivery file is unavailable: {raw}")
        suffix = source.suffix.lower()
        if suffix not in DELIVERY_SUFFIXES:
            raise PackageBlockedError(f"only Markdown, JSON, and JSONL may be delivered: {raw}")
        content = source.read_bytes()
        digest = sha256_bytes(content)
        artifact_id = _approved_artifact_for_path(root, state, raw, digest)
        _validate_delivery_text(content, suffix, raw, allowed_urls_by_path.get(raw, set()))
        destination = f"{delivery_root}/{episode}/artifacts/{raw}"
        outputs[destination] = content
        source_artifacts.add(artifact_id)
        files.append(
            {
                "artifact_id": artifact_id,
                "source": raw,
                "delivery_path": str(
                    PurePosixPath(destination).relative_to(
                        PurePosixPath(delivery_root) / episode
                    )
                ),
                "sha256": digest,
            }
        )
    if not files:
        raise PackageBlockedError("delivery selection is empty")
    selected = {entry["source"] for entry in files}
    unused_exception_paths = sorted(set(allowed_urls_by_path) - selected)
    if unused_exception_paths:
        raise PackageBlockedError(
            "text exception path is not selected for delivery: "
            + ", ".join(unused_exception_paths)
        )

    coverage = _episode_coverage(state, episode)
    try:
        declared_omissions = set(
            _normalize_portable_path_values(
                root, omitted_paths or (), label="delivery omission"
            )
        )
        _validate_paths_against_tracked_state(state, declared_omissions)
    except ValueError as error:
        raise PackageBlockedError(str(error)) from error
    selected_identities = {
        _portable_path_identity(relative): relative for relative in selected
    }
    omission_identities = {
        _portable_path_identity(relative): relative for relative in declared_omissions
    }
    alias_contradictions = sorted(
        f"{selected_identities[identity]} / {omission_identities[identity]}"
        for identity in set(selected_identities) & set(omission_identities)
        if selected_identities[identity] != omission_identities[identity]
    )
    if alias_contradictions:
        raise PackageBlockedError(
            "path cannot be selected and omitted with different spelling: "
            + ", ".join(alias_contradictions)
        )
    unknown_omissions = sorted(declared_omissions - set(coverage))
    if unknown_omissions:
        raise PackageBlockedError(
            "omitted path is not an accepted artifact of this episode: "
            + ", ".join(unknown_omissions)
        )
    contradictory = sorted(declared_omissions & selected)
    if contradictory:
        raise PackageBlockedError(
            "path cannot be both selected and omitted: " + ", ".join(contradictory)
        )
    unaccounted = sorted(set(coverage) - selected - declared_omissions)
    if unaccounted:
        ready = [relative for relative in unaccounted if coverage[relative]["ready"]]
        pending = [relative for relative in unaccounted if not coverage[relative]["ready"]]
        detail = []
        if ready:
            detail.append("delivery-ready and unselected: " + ", ".join(ready))
        if pending:
            detail.append("not yet delivery-ready: " + ", ".join(pending))
        raise PackageBlockedError(
            "episode artifacts are neither selected nor declared omitted ("
            + "; ".join(detail)
            + "); add each to --include or --omit"
        )
    omitted = [
        {
            "source": relative,
            "artifact_id": coverage[relative]["artifact_id"],
            "reason": "delivery_ready_but_omitted"
            if coverage[relative]["ready"]
            else "not_delivery_ready",
        }
        for relative in sorted(declared_omissions)
    ]

    manifest = {
        "schema_version": 1,
        "episode": episode,
        "files": files,
        "omitted": omitted,
        "text_exceptions": exceptions,
        "exclusions": ["private_inputs", "operational_state", "binaries", "unselected_files"],
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_target = f"{delivery_root}/{episode}/manifest.json"
    outputs[manifest_target] = manifest_bytes
    checksum_entries = [
        (entry["sha256"], entry["delivery_path"]) for entry in files
    ] + [(sha256_bytes(manifest_bytes), "manifest.json")]
    checksums = "".join(
        f"{digest}  {relative}\n" for digest, relative in sorted(checksum_entries, key=lambda item: item[1])
    ).encode("utf-8")
    checksum_target = f"{delivery_root}/{episode}/checksums.sha256"
    outputs[checksum_target] = checksums

    delivery_artifact = f"delivery:{episode}"
    lifecycle_changes = {
        artifact_id: {"delivery_gate": "delivered"}
        for artifact_id in sorted(source_artifacts)
    }
    lifecycle_changes[delivery_artifact] = {
        "build_state": "materialized",
        "validation_state": "pass",
        "creator_acceptance": "accepted",
        "delivery_gate": "delivered",
    }
    transaction = publish_transaction(
        root,
        stage="delivery",
        outputs=outputs,
        lifecycle_changes=lifecycle_changes,
        target_artifacts={target: delivery_artifact for target in outputs},
        read_set={entry["source"]: entry["sha256"] for entry in files},
        _delivery_gate=True,
    )
    return {
        "project_root": str(root),
        "episode": episode,
        "file_count": len(files),
        "transaction_id": transaction["transaction_id"],
        "status": "delivered",
    }


def _delivery_root_for_verification(
    layout: Mapping[str, Any], episode: str, package_exists: Callable[[str], bool]
) -> str:
    if layout["mode"] == "mixed":
        available = [
            name
            for name in (
                CANONICAL_ROOTS["delivery"],
                LEGACY_ROOTS["delivery"],
            )
            if package_exists(name)
        ]
        if len(available) > 1:
            raise PackageBlockedError(f"{episode} 同时存在中文与旧版英文交付包")
        return available[0] if available else CANONICAL_ROOTS["delivery"]
    # No cross-root fallback here: a package under the other family gives that
    # root content, which puts its family in detected_modes and selects `mixed`.
    return str(layout["roots"]["delivery"])


def _verify_delivery_contents(
    *,
    root: Path,
    episode: str,
    delivery_root: str,
    state: Mapping[str, Any],
    checksums_content: bytes,
    live_hash: Callable[[str], str | None],
    present_members: Callable[[], set[str]],
) -> dict[str, Any]:
    # Authenticate the list before trusting any path inside it. A modified
    # unauthenticated list is reported as tampered without traversing its
    # entries, preventing it from becoming a hash oracle for outside files.
    checksums_relative = f"{delivery_root}/{episode}/checksums.sha256"
    artifacts = state.get("artifacts")
    recorded: str | None = None
    if isinstance(artifacts, dict):
        record = artifacts.get(f"delivery:{episode}")
        accepted = record.get("accepted_targets") if isinstance(record, dict) else None
        if isinstance(accepted, dict) and isinstance(
            accepted.get(checksums_relative), str
        ):
            recorded = accepted[checksums_relative]
    checksum_list_authentic = (
        recorded is not None and recorded == sha256_bytes(checksums_content)
    )
    if not checksum_list_authentic:
        return {
            "project_root": str(root),
            "episode": episode,
            "file_count": 0,
            "mismatched": [],
            "missing": [],
            "unlisted": [],
            "checksum_list_authentic": False,
            "status": "tampered",
        }

    expected: dict[str, str] = {}
    expected_paths: dict[str, str] = {}
    for number, line in enumerate(
        checksums_content.decode("utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        digest, separator, relative = line.partition("  ")
        if not separator or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise PackageBlockedError(f"checksum line {number} is malformed")
        try:
            normalized = _relative_path(relative)
        except ValueError as error:
            raise PackageBlockedError(
                f"checksum line {number} has an unsafe path"
            ) from error
        if normalized != relative or normalized == "checksums.sha256":
            raise PackageBlockedError(f"checksum line {number} has an unsafe path")
        try:
            _register_portable_path(
                expected_paths, normalized, label="checksum entry"
            )
        except ValueError as error:
            raise PackageBlockedError(
                f"checksum line {number} repeats a portable path alias"
            ) from error
        if normalized in expected:
            raise PackageBlockedError(f"checksum line {number} repeats {normalized}")
        expected[normalized] = digest

    mismatched: list[str] = []
    missing: list[str] = []
    for relative, digest in sorted(expected.items()):
        actual = live_hash(relative)
        if actual is None:
            missing.append(relative)
        elif actual != digest:
            mismatched.append(relative)

    unlisted = sorted(present_members() - set(expected) - {"checksums.sha256"})
    intact = not (mismatched or missing or unlisted)
    return {
        "project_root": str(root),
        "episode": episode,
        "file_count": len(expected),
        "mismatched": mismatched,
        "missing": missing,
        "unlisted": unlisted,
        "checksum_list_authentic": True,
        "status": "intact" if intact else "tampered",
    }


def _is_link_like(details: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _portable_regular_bytes(root: Path, relative: str | Path) -> bytes:
    pure = PurePosixPath(relative)
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        details = os.lstat(current)
        if _is_link_like(details) or not stat.S_ISDIR(details.st_mode):
            raise TransactionConflictError(f"unsafe directory component: {current}")
    target = current / pure.name
    details = os.lstat(target)
    if _is_link_like(details) or not stat.S_ISREG(details.st_mode):
        raise TransactionConflictError(f"unsafe regular file: {target}")
    return target.read_bytes()


def _portable_package_directory(root: Path, delivery_root: str, episode: str) -> Path:
    current = root
    for part in (delivery_root, episode):
        current = current / part
        details = os.lstat(current)
        if _is_link_like(details) or not stat.S_ISDIR(details.st_mode):
            raise TransactionConflictError(f"unsafe package directory: {current}")
    return current


def _portable_package_members(package: Path) -> set[str]:
    present: set[str] = set()

    def collect(parent: Path, parts: tuple[str, ...]) -> None:
        try:
            entries = list(os.scandir(parent))
        except OSError:
            if parts:
                present.add(PurePosixPath(*parts).as_posix())
            return
        for entry in entries:
            relative = PurePosixPath(*parts, entry.name).as_posix()
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError:
                present.add(relative)
                continue
            if _is_link_like(details):
                present.add(relative)
            elif stat.S_ISDIR(details.st_mode):
                collect(Path(entry.path), (*parts, entry.name))
            else:
                present.add(relative)

    collect(package, ())
    return present


def _verify_delivery_package_portable(root: Path, episode: str) -> dict[str, Any]:
    try:
        state = json.loads(_portable_regular_bytes(root, STATE_FILE).decode("utf-8"))
    except FileNotFoundError:
        state = {}
    layout = _project_layout_from_root(root)

    def package_exists(name: str) -> bool:
        try:
            _portable_package_directory(root, name, episode)
        except FileNotFoundError:
            return False
        except (OSError, TransactionConflictError) as error:
            raise PackageBlockedError(
                f"unsafe delivered package path for {episode}"
            ) from error
        return True

    delivery_root = _delivery_root_for_verification(layout, episode, package_exists)
    try:
        package = _portable_package_directory(root, delivery_root, episode)
        checksums_content = _portable_regular_bytes(package, "checksums.sha256")
    except FileNotFoundError as error:
        raise PackageBlockedError(f"no delivered package for {episode}") from error
    except (OSError, TransactionConflictError) as error:
        raise PackageBlockedError(f"no safe delivered package for {episode}") from error

    def live_hash(relative: str) -> str | None:
        try:
            return sha256_bytes(_portable_regular_bytes(package, relative))
        except (OSError, TransactionConflictError):
            return None

    return _verify_delivery_contents(
        root=root,
        episode=episode,
        delivery_root=delivery_root,
        state=state,
        checksums_content=checksums_content,
        live_hash=live_hash,
        present_members=lambda: _portable_package_members(package),
    )


def verify_delivery_package(path: Path, *, episode: str) -> dict[str, Any]:
    """Re-read a delivered package and check it against its own checksums."""

    root = find_project(path)
    if EPISODE_ID_RE.fullmatch(episode) is None:
        raise ValueError("episode must use an EP001-style identifier")
    if os.name == "nt":
        return _verify_delivery_package_portable(root, episode)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(root, flags)
    except OSError as error:
        raise PackageBlockedError("project root cannot be opened safely") from error
    delivery_fd = -1
    try:
        try:
            state = json.loads(_read_regular_at(root_fd, STATE_FILE).decode("utf-8"))
        except FileNotFoundError:
            state = {}
        layout = _project_layout_at(root_fd, state)

        def package_exists(name: str) -> bool:
            try:
                descriptor = _open_directory_at(root_fd, (name, episode))
            except FileNotFoundError:
                return False
            except OSError as error:
                raise PackageBlockedError(
                    f"unsafe delivered package path for {episode}"
                ) from error
            os.close(descriptor)
            return True

        delivery_root = _delivery_root_for_verification(
            layout, episode, package_exists
        )
        try:
            delivery_fd = _open_directory_at(root_fd, (delivery_root, episode))
            checksums_content = _read_regular_at(delivery_fd, "checksums.sha256")
        except FileNotFoundError as error:
            # FileNotFoundError is an OSError, so the ordinary "this episode was
            # never packaged" case must be separated out before the hostile-path
            # branch or it is reported as though the tree were unsafe.
            raise PackageBlockedError(f"no delivered package for {episode}") from error
        except (OSError, TransactionConflictError) as error:
            raise PackageBlockedError(f"no safe delivered package for {episode}") from error

        def live_hash(relative: str) -> str | None:
            try:
                return _live_hash_at(delivery_fd, relative)
            except (OSError, TransactionConflictError):
                return None

        def present_members() -> set[str]:
            present: set[str] = set()

            def collect(parent_fd: int, parts: tuple[str, ...]) -> None:
                with os.scandir(parent_fd) as iterator:
                    entries = list(iterator)
                for entry in entries:
                    relative = PurePosixPath(*parts, entry.name).as_posix()
                    if entry.is_symlink():
                        present.add(relative)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        try:
                            child_fd = os.open(
                                entry.name,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parent_fd,
                            )
                        except OSError:
                            present.add(relative)
                            continue
                        try:
                            collect(child_fd, (*parts, entry.name))
                        finally:
                            os.close(child_fd)
                    else:
                        present.add(relative)

            collect(delivery_fd, ())
            return present

        return _verify_delivery_contents(
            root=root,
            episode=episode,
            delivery_root=delivery_root,
            state=state,
            checksums_content=checksums_content,
            live_hash=live_hash,
            present_members=present_members,
        )
    finally:
        if delivery_fd >= 0:
            os.close(delivery_fd)
        os.close(root_fd)


def _parse_cli_pairs(values: Iterable[str], *, label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item:
            raise ValueError(f"{label} must use PATH=VALUE")
        if key in parsed:
            raise ValueError(f"duplicate {label} path: {key}")
        parsed[key] = item
    return parsed


def _publish_from_cli(args: argparse.Namespace) -> dict[str, Any]:
    root = find_project(args.path)
    bindings = _parse_cli_pairs(args.outputs, label="output")
    outputs: dict[str, bytes] = {}
    inputs = _parse_cli_pairs(args.inputs or [], label="input")
    for raw_target, raw_source in bindings.items():
        target = _relative_path(raw_target)
        source = _relative_path(raw_source)
        if _portable_path_identity(target) == _portable_path_identity(source):
            raise ValueError("candidate source and publication target must differ")
        source_path = _project_path(root, source)
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError(f"candidate source is unavailable: {source}")
        source_hash = sha256_file(source_path)
        previous = inputs.get(source)
        if previous is not None and previous != source_hash:
            raise ValueError(f"input hash does not match candidate source: {source}")
        inputs[source] = source_hash
        outputs[target] = source_path.read_bytes()
    records: dict[str, list[str]] = {}
    for value in args.input_records or []:
        key, separator, selector = value.partition("=")
        if not separator or not key or not selector:
            raise ValueError("input record must use PATH=SELECTOR")
        records.setdefault(_relative_path(key), []).append(selector)
    return publish_candidate(
        root,
        owner=args.owner,
        artifact_id=args.artifact_id,
        allow_unregistered_path=bool(getattr(args, "allow_unregistered_path", False)),
        outputs=outputs,
        input_hashes=inputs,
        input_records=records or None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a short-drama filesystem project.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize a project without creative content.")
    init.add_argument("path", type=Path)
    init.add_argument("--title", default="未命名短剧")
    init.add_argument("--language", default="zh-CN")
    init.add_argument("--prompt-language", default=DEFAULT_PROMPT_LANGUAGE)
    init.add_argument("--aspect-ratio", default="9:16")

    status = subparsers.add_parser("status", help="Print a creator-safe project summary.")
    status.add_argument("path", type=Path, nargs="?", default=Path.cwd())

    recover = subparsers.add_parser("recover", help="Recover interrupted publications.")
    recover.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    recover.add_argument("--transaction")

    publish = subparsers.add_parser(
        "publish", help="Publish a text/JSON candidate through the recovery WAL."
    )
    publish.add_argument("path", type=Path)
    publish.add_argument("--owner", required=True)
    publish.add_argument("--artifact-id", required=True)
    publish.add_argument(
        "--output",
        action="append",
        required=True,
        dest="outputs",
        help="Bind PROJECT_TARGET=PROJECT_SOURCE; repeat for multiple files.",
    )
    publish.add_argument(
        "--input",
        action="append",
        dest="inputs",
        help="Bind an additional exact project input as PATH=SHA256.",
    )
    publish.add_argument(
        "--allow-unregistered-path",
        action="store_true",
        help=(
            "Publish outside the standard stage directories. Ad-hoc creator "
            "files stay possible; without this flag a mistyped stage directory "
            "is refused instead of building a parallel tree status never reports."
        ),
    )
    publish.add_argument(
        "--input-record",
        action="append",
        dest="input_records",
        help=(
            "Narrow one input to the records actually used, as PATH=SELECTOR; "
            "repeat per record. A selector is a JSONL record ID or a JSON "
            "RFC 6901 pointer. Unrelated edits to the rest of that file then "
            "leave this artifact current."
        ),
    )

    accept = subparsers.add_parser(
        "accept", help="Record creator acceptance for exact candidate hashes."
    )
    accept.add_argument("path", type=Path)
    accept.add_argument("--artifact-id", required=True)
    accept.add_argument("--decision", required=True, choices=("accepted", "rejected"))
    accept.add_argument("--target", action="append", required=True, dest="targets")
    accept.add_argument("--evidence-artifact", required=True)
    accept.add_argument("--evidence-hash", required=True)
    accept.add_argument("--evidence-record-id")
    accept.add_argument("--evidence-field")

    review = subparsers.add_parser(
        "review", help="Record an independent verdict for exact accepted hashes."
    )
    review.add_argument("path", type=Path)
    review.add_argument("--artifact-id", required=True)
    review.add_argument(
        "--verdict",
        required=True,
        choices=("approve", "approve_with_notes", "revise", "provisional"),
    )
    review.add_argument("--target", action="append", required=True, dest="targets")
    review.add_argument("--verdict-owner", required=True)
    review.add_argument("--verdict-artifact", required=True)
    review.add_argument("--verdict-hash", required=True)
    review.add_argument("--verdict-record-id")

    package = subparsers.add_parser("package", help="Package approved text/JSON artifacts.")
    package.add_argument("path", type=Path)
    package.add_argument("--episode", required=True)
    package.add_argument("--include", action="append", required=True, dest="includes")
    package.add_argument(
        "--omit",
        action="append",
        dest="omissions",
        help=(
            "Acknowledge one accepted episode file that is deliberately left out; "
            "repeat per file. The manifest records it and why."
        ),
    )
    package.add_argument("--text-exceptions", type=Path)

    verify = subparsers.add_parser(
        "verify", help="Re-check a delivered package against its own checksums."
    )
    verify.add_argument("path", type=Path)
    verify.add_argument("--episode", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_project(
                args.path,
                title=args.title,
                language=args.language,
                aspect_ratio=args.aspect_ratio,
                prompt_language=args.prompt_language,
            )
        elif args.command == "status":
            result = project_status(args.path)
        elif args.command == "recover":
            result = (
                recover_transaction(args.path, args.transaction)
                if args.transaction
                else recover_project(args.path)
            )
        elif args.command == "publish":
            result = _publish_from_cli(args)
        elif args.command == "accept":
            evidence_ref = {
                "owner": "creator",
                "artifact": args.evidence_artifact,
                "hash": args.evidence_hash,
            }
            if args.evidence_record_id:
                evidence_ref["record_id"] = args.evidence_record_id
            if args.evidence_field:
                evidence_ref["field"] = args.evidence_field
            result = record_creator_acceptance(
                args.path,
                artifact_id=args.artifact_id,
                decision=args.decision,
                target_hashes=_parse_cli_pairs(args.targets, label="target"),
                evidence_ref=evidence_ref,
            )
        elif args.command == "review":
            verdict_ref = {
                "owner": args.verdict_owner,
                "artifact": args.verdict_artifact,
                "hash": args.verdict_hash,
            }
            if args.verdict_record_id:
                verdict_ref["record_id"] = args.verdict_record_id
            result = record_independent_review(
                args.path,
                artifact_id=args.artifact_id,
                verdict=args.verdict,
                reviewed_targets=_parse_cli_pairs(args.targets, label="target"),
                verdict_ref=verdict_ref,
            )
        elif args.command == "verify":
            result = verify_delivery_package(args.path, episode=args.episode)
        else:
            exceptions = None
            if args.text_exceptions:
                exceptions = json.loads(args.text_exceptions.read_text(encoding="utf-8"))
                if not isinstance(exceptions, list):
                    raise ValueError("text exceptions file must contain a JSON array")
            result = build_delivery_package(
                args.path,
                episode=args.episode,
                selected_paths=args.includes,
                text_exceptions=exceptions,
                omitted_paths=args.omissions,
            )
        # Keep machine-readable CLI output ASCII-safe. On Windows, redirected
        # stdout may still use a legacy code page even when project paths and
        # creator-facing JSON contain Unicode; JSON escapes round-trip exactly.
        print(json.dumps(result, sort_keys=True))
        # `verify` is the only subcommand that reports a verdict in its payload
        # instead of raising, so it needs the same exit convention the check
        # scripts use: a tampered package must fail a CI step or an && chain.
        if result.get("status") == "tampered":
            return 1
        return 0
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        TransactionError,
        PackageBlockedError,
    ) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
