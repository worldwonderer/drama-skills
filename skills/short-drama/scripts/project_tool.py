#!/usr/bin/env python3
"""Small filesystem lifecycle for short-drama projects.

Creative work lives in project files and skills. This module keeps only the
mechanical boundaries that earn their cost: safe publication, exact creator
acceptance, a lightweight review verdict, direct-input freshness, and portable
text delivery. It does not call networks or media services.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "short-drama needs Python {}.{} or newer; this interpreter is {}.{}".format(
            *MINIMUM_PYTHON, sys.version_info.major, sys.version_info.minor
        )
    )

PROJECT_FILE = "short-drama.json"
STATE_FILE = Path(".short-drama/state.json")
STATE_SCHEMA = "2.0"
DEFAULT_PROMPT_LANGUAGE = "en"
LANGUAGE_TAG_RE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
DELIVERY_SUFFIXES = {".md", ".json", ".jsonl"}
CREATOR_DOCUMENTS = (
    "剧本.md",
    "视觉设定.md",
    "分镜.md",
    "图片提示词.md",
    "视频提示词.md",
)
EXPORT_MEDIA_DIRECTORY = "制作成果"
EXPORT_SCHEMA = "1.0"
EPISODE_ID_RE = re.compile(r"EP(?:[0-9]{3}|[1-9][0-9]{3,})")
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

CANONICAL_ROOTS = {
    "inputs": "输入",
    "development": "项目开发",
    "bible": "设定集",
    "episodes": "剧集",
    "delivery": "交付",
    "creator-decisions": "创作者决策",
    "reviews": "审查",
}
LEGACY_ROOTS = {role: role for role in CANONICAL_ROOTS}
ROOT_ROLE_ALIASES: dict[str, str] = {
    name.casefold(): role
    for roots in (CANONICAL_ROOTS, LEGACY_ROOTS)
    for role, name in roots.items()
}
LAYOUT_PINNING_ROLES = frozenset(
    {"development", "bible", "episodes", "delivery", "creator-decisions", "reviews"}
)
PUBLISHABLE_ROOT_ROLES = frozenset(
    {"development", "bible", "episodes", "creator-decisions", "reviews"}
)
PUBLISHABLE_ROOTS = tuple(
    roots[role]
    for roots in (CANONICAL_ROOTS, LEGACY_ROOTS)
    for role in CANONICAL_ROOTS
    if role in PUBLISHABLE_ROOT_ROLES
)
PROJECT_DIRS = (*CANONICAL_ROOTS.values(), ".short-drama")

PROTECTED_PUBLISH_ROLE_REASONS = {
    "inputs": "creator inputs are immutable publication sources",
    "delivery": "the delivery tree is written by the packaging gate, not by publication",
}
PROTECTED_PUBLISH_ROOTS = {
    name.casefold(): reason
    for role, reason in PROTECTED_PUBLISH_ROLE_REASONS.items()
    for name in (CANONICAL_ROOTS[role], LEGACY_ROOTS[role])
} | {".short-drama": "operational state cannot be a publication target"}

AUTHORITY_ROOT_TOKEN = "creator_authority"
EPISODE_LENGTH_POINTER = "/format/target_seconds_per_episode"
PACING_POINTER = "/format/pacing"
# Everything outside /creator_authority/* that a decision may still bind. Both
# are read by the write stage to turn a screenplay into seconds, so leaving them
# out of set-authority left hand-editing short-drama.json as the only way in.
FORMAT_POINTERS = (EPISODE_LENGTH_POINTER, PACING_POINTER)

class ProjectConflictError(RuntimeError):
    """A file changed while a guarded operation was in progress."""


class PackageBlockedError(RuntimeError):
    """A requested delivery contains an artifact that is not approved."""


class NonPortablePathError(ValueError):
    """A path would alias or fail on a supported filesystem."""


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


def _is_link_or_reparse(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _fsync_directory(path: Path) -> None:
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
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    # allow_nan=False: Python reads and writes bare Infinity/NaN, but they are not
    # JSON. Writing one produces a manifest that every other reader rejects.
    encoded = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    _atomic_bytes(path, encoded)


def normalize_language_tag(value: str, *, field: str) -> str:
    tag = value.strip()
    if not tag:
        raise ValueError(f"{field} must not be empty")
    if LANGUAGE_TAG_RE.fullmatch(tag) is None:
        raise ValueError(f"{field} is not a well-formed language tag: {value!r}")
    return tag


def project_languages(project: Mapping[str, Any]) -> dict[str, str]:
    format_block = project.get("format")
    prompt_language = (
        format_block.get("prompt_language")
        if isinstance(format_block, Mapping)
        else None
    )
    authority = project.get("creator_authority")
    production_profile = (
        authority.get("production_profile")
        if isinstance(authority, Mapping)
        else None
    )
    choices = (
        production_profile.get("choices")
        if isinstance(production_profile, Mapping)
        else None
    )
    video_prompt_language = (
        choices.get("video_prompt_language") if isinstance(choices, Mapping) else None
    )
    return {
        "language": str(project.get("language") or "zh-CN"),
        "prompt_language": str(prompt_language or DEFAULT_PROMPT_LANGUAGE),
        "video_prompt_language": str(
            video_prompt_language or prompt_language or DEFAULT_PROMPT_LANGUAGE
        ),
    }


def project_video_model_profile(project: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the accepted prompt-facing model choices without guessing defaults."""
    authority = project.get("creator_authority")
    production_profile = (
        authority.get("production_profile")
        if isinstance(authority, Mapping)
        else None
    )
    if (
        not isinstance(production_profile, Mapping)
        or production_profile.get("status") != "accepted"
    ):
        return {}
    choices = production_profile.get("choices")
    if not isinstance(choices, Mapping):
        return {}
    fields = (
        "target_video_model",
        "video_prompt_dialect",
        "video_prompt_language",
        "native_duration_seconds",
        "supported_generation_modes",
        "audio_generation",
    )
    return {field: choices[field] for field in fields if field in choices}


def initialize_project(
    path: Path,
    *,
    title: str,
    language: str,
    aspect_ratio: str,
    prompt_language: str = DEFAULT_PROMPT_LANGUAGE,
    episode_count: int | None = None,
    target_seconds_per_episode: float | None = None,
    suite_root: Path | None = None,
) -> dict[str, Any]:
    language = normalize_language_tag(language, field="language")
    prompt_language = normalize_language_tag(prompt_language, field="prompt_language")
    if episode_count is not None and (
        isinstance(episode_count, bool)
        or not isinstance(episode_count, int)
        or episode_count <= 0
    ):
        raise ValueError("episode_count must be a positive integer")
    if target_seconds_per_episode is not None and (
        isinstance(target_seconds_per_episode, bool)
        or not isinstance(target_seconds_per_episode, (int, float))
        or not math.isfinite(target_seconds_per_episode)
        or target_seconds_per_episode <= 0
    ):
        raise ValueError("target_seconds_per_episode must be a positive number of seconds")
    root = path.expanduser().resolve()
    project_path = root / PROJECT_FILE
    if project_path.exists():
        raise FileExistsError(f"project already exists: {project_path}")

    root.mkdir(parents=True, exist_ok=True)
    for relative in PROJECT_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)

    core = suite_root or Path(__file__).resolve().parents[1]
    project = json.loads(
        (core / "assets/project-template/short-drama.json").read_text(encoding="utf-8")
    )
    project.update(
        {
            "project_id": f"SD-{uuid.uuid4().hex[:12].upper()}",
            "title": title.strip() or "未命名短剧",
            "language": language,
            "created_at": utc_now(),
        }
    )
    project["format"]["aspect_ratio"] = aspect_ratio
    project["format"]["prompt_language"] = prompt_language
    if episode_count is not None:
        project["format"]["episode_count"] = episode_count
    if target_seconds_per_episode is not None:
        project["format"]["target_seconds_per_episode"] = target_seconds_per_episode
    state = {
        "schema_version": STATE_SCHEMA,
        "project_id": project["project_id"],
        "project_layout_mode": "auto",
        "updated_at": utc_now(),
        "last_action": "initialized",
        "artifacts": {},
    }
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
    if not allow_operations and pure.parts[0].casefold() == ".short-drama":
        raise ValueError("operational state cannot be a publication target")
    return pure.as_posix()


def _portable_path_identity(value: str) -> str:
    return unicodedata.normalize("NFC", value.casefold())


def _validate_existing_path_spelling(root: Path, relative: str, *, label: str) -> None:
    current = root
    prefix: list[str] = []
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        try:
            entries = list(os.scandir(current))
        except (FileNotFoundError, NotADirectoryError):
            return
        identity = _portable_path_identity(part)
        matches = [entry for entry in entries if _portable_path_identity(entry.name) == identity]
        alias = next((entry.name for entry in matches if entry.name != part), None)
        if alias is not None:
            existing = PurePosixPath(*prefix, alias).as_posix()
            raise NonPortablePathError(
                f"{label} path spelling aliases an existing path: {relative} conflicts with {existing}"
            )
        exact = next((entry for entry in matches if entry.name == part), None)
        if exact is None or index == len(parts) - 1:
            return
        if not exact.is_dir(follow_symlinks=False):
            return
        current /= part
        prefix.append(part)


def _validate_path_set(root: Path, relatives: Iterable[str], *, label: str) -> list[str]:
    normalized: list[str] = []
    seen: dict[str, str] = {}
    for value in relatives:
        relative = _relative_path(value)
        identity = _portable_path_identity(relative)
        previous = seen.get(identity)
        if previous is not None and previous != relative:
            raise NonPortablePathError(
                f"{label} paths are not portable aliases: {previous} and {relative}"
            )
        if previous is None:
            seen[identity] = relative
            normalized.append(relative)
        _validate_existing_path_spelling(root, relative, label=label)
    return sorted(normalized)


def _root_role(name: str) -> str | None:
    return ROOT_ROLE_ALIASES.get(name.casefold())


def _root_layout_mode(name: str) -> str | None:
    role = _root_role(name)
    if role is None:
        return None
    if name == CANONICAL_ROOTS[role]:
        return "canonical"
    if name == LEGACY_ROOTS[role]:
        return "legacy"
    return None


def is_protected_project_text(value: str | Path) -> bool:
    raw = str(value).replace("\\", "/")
    pure = PurePosixPath(raw)
    if (
        not raw
        or pure.is_absolute()
        or any(part in ("", ".", "..") for part in pure.parts)
        or _has_nonportable_path_component(pure.parts)
    ):
        return True
    return (
        pure.name.casefold() == PROJECT_FILE
        or pure.parts[0].casefold() == ".short-drama"
        or _root_role(pure.parts[0]) == "delivery"
    )


def _directory_has_content(path: Path) -> bool:
    try:
        details = os.lstat(path)
        return stat.S_ISLNK(details.st_mode) or (
            stat.S_ISDIR(details.st_mode) and any(path.iterdir())
        )
    except FileNotFoundError:
        return False


def _read_state(root: Path) -> dict[str, Any]:
    state_path = root / STATE_FILE
    if not state_path.is_file() or state_path.is_symlink():
        raise ValueError("project state is missing or unsafe")
    document = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("project state must be an object")
    return _normalize_state(document)


def _legacy_artifact(record: Mapping[str, Any]) -> dict[str, Any]:
    candidate = record.get("candidate_targets")
    accepted_targets = record.get("accepted_targets")
    reviewed_targets = record.get("reviewed_targets")
    output_map = (
        candidate
        if isinstance(candidate, Mapping)
        else accepted_targets
        if isinstance(accepted_targets, Mapping)
        else {}
    )
    outputs = sorted(str(path) for path in output_map if isinstance(path, str))
    input_map = (
        record.get("candidate_inputs")
        if isinstance(candidate, Mapping)
        else record.get("accepted_inputs")
    )
    if not isinstance(input_map, Mapping):
        input_map = {}
    inputs = {
        str(path): str(value)
        for path, value in (input_map.items() if isinstance(input_map, Mapping) else [])
        if isinstance(path, str) and isinstance(value, str)
    }
    acceptance: dict[str, Any] | None = None
    if record.get("creator_acceptance") in {"accepted", "rejected"}:
        decision = str(record["creator_acceptance"])
        acceptance = {
            "decision": decision,
            "at": None,
            "outputs": {
                str(path): str(value)
                for path, value in (
                    accepted_targets.items()
                    if isinstance(accepted_targets, Mapping)
                    else []
                )
                if isinstance(path, str) and isinstance(value, str)
            },
            "note": "migrated from v0.3 state",
        }
    review: dict[str, Any] | None = None
    legacy_verdict = record.get("independent_review")
    if legacy_verdict in {"approve", "approve_with_notes", "revise", "provisional"}:
        review_outputs = (
            reviewed_targets
            if isinstance(reviewed_targets, Mapping)
            else accepted_targets
            if isinstance(accepted_targets, Mapping)
            else {}
        )
        review = {
            "verdict": legacy_verdict,
            "at": None,
            "outputs": {
                str(path): str(value)
                for path, value in review_outputs.items()
                if isinstance(path, str) and isinstance(value, str)
            },
            "reviewer": "migrated",
            "note": "migrated from v0.3 state",
        }
    return {
        "owner": str(record.get("owner") or "unknown"),
        "outputs": outputs,
        "inputs": inputs,
        "acceptance": acceptance,
        "review": review,
        "updated_at": None,
    }


def _normalize_state(document: Mapping[str, Any]) -> dict[str, Any]:
    artifacts_raw = document.get("artifacts", {})
    if not isinstance(artifacts_raw, Mapping):
        raise ValueError("state.artifacts must be an object")
    artifacts: dict[str, Any] = {}
    modern = document.get("schema_version") == STATE_SCHEMA
    for artifact_id, value in artifacts_raw.items():
        if not isinstance(artifact_id, str) or not isinstance(value, Mapping):
            raise ValueError("artifact state entries must be objects")
        if modern and isinstance(value.get("outputs"), list):
            outputs = [
                _relative_path(item)
                for item in value["outputs"]
                if isinstance(item, str)
            ]
            inputs_raw = value.get("inputs", {})
            if not isinstance(inputs_raw, Mapping):
                raise ValueError(f"{artifact_id}.inputs must be an object")
            inputs = {
                _relative_path(path, allow_operations=True): str(digest)
                for path, digest in inputs_raw.items()
                if isinstance(path, str) and isinstance(digest, str)
            }
            artifacts[artifact_id] = {
                "owner": str(value.get("owner") or "unknown"),
                "outputs": sorted(set(outputs)),
                "inputs": inputs,
                "acceptance": value.get("acceptance") if isinstance(value.get("acceptance"), Mapping) else None,
                "review": value.get("review") if isinstance(value.get("review"), Mapping) else None,
                "updated_at": value.get("updated_at"),
            }
        else:
            artifacts[artifact_id] = _legacy_artifact(value)
    mode = document.get("project_layout_mode", "auto")
    if mode not in {"auto", "canonical", "legacy"}:
        mode = "auto"
    bindings_raw = document.get("authority")
    bindings = {
        str(field): dict(binding)
        for field, binding in (bindings_raw.items() if isinstance(bindings_raw, Mapping) else [])
        if isinstance(field, str) and isinstance(binding, Mapping)
    }
    return {
        "schema_version": STATE_SCHEMA,
        "project_id": document.get("project_id"),
        "project_layout_mode": mode,
        "updated_at": document.get("updated_at"),
        "last_action": document.get("last_action") or "loaded",
        "artifacts": artifacts,
        "authority": bindings,
    }


def _save_state(root: Path, state: dict[str, Any], *, action: str) -> None:
    state["schema_version"] = STATE_SCHEMA
    state["updated_at"] = utc_now()
    state["last_action"] = action
    atomic_json(root / STATE_FILE, state)


def _project_layout_from_root(root: Path, state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    current = state or _read_state(root)
    recorded = current.get("project_layout_mode", "auto")
    canonical_roles = sorted(
        role
        for role, name in CANONICAL_ROOTS.items()
        if role in LAYOUT_PINNING_ROLES and _directory_has_content(root / name)
    )
    legacy_roles = sorted(
        role
        for role, name in LEGACY_ROOTS.items()
        if role in LAYOUT_PINNING_ROLES and _directory_has_content(root / name)
    )
    nonstandard_roots = sorted(
        entry.name
        for entry in root.iterdir()
        if (entry.is_symlink() or entry.is_dir())
        if (role := _root_role(entry.name)) in LAYOUT_PINNING_ROLES
        if entry.name not in {CANONICAL_ROOTS[role], LEGACY_ROOTS[role]}
        if _directory_has_content(entry)
    )
    unsafe_roots = sorted(
        entry.name
        for entry in root.iterdir()
        if _root_role(entry.name) in LAYOUT_PINNING_ROLES and entry.is_symlink()
    )
    detected = {
        mode
        for mode, roles in (("canonical", canonical_roles), ("legacy", legacy_roles))
        if roles
    }
    conflict = bool(nonstandard_roots or unsafe_roots) or len(detected) > 1 or (
        recorded in {"canonical", "legacy"} and detected and detected != {recorded}
    )
    if conflict:
        mode = "mixed"
    elif recorded in {"canonical", "legacy"}:
        mode = str(recorded)
    elif detected:
        mode = next(iter(detected))
    else:
        mode = "canonical"
    roots = LEGACY_ROOTS if mode == "legacy" else CANONICAL_ROOTS
    return {
        "mode": mode,
        "pinned": recorded != "auto" or bool(detected),
        "roots": dict(roots),
        "nonstandardRoots": nonstandard_roots,
        "unsafeRoots": unsafe_roots,
    }


def project_layout(path: Path) -> dict[str, Any]:
    root = find_project(path)
    return _project_layout_from_root(root)


def _validate_project_output_layout(root: Path, relatives: Iterable[str]) -> str | None:
    families = {
        family
        for relative in relatives
        if (part := PurePosixPath(relative).parts[0])
        if _root_role(part) in LAYOUT_PINNING_ROLES
        if (family := _root_layout_mode(part)) is not None
    }
    if len(families) > 1:
        raise ValueError("不能在同一次发布中混用中文与旧版英文目录")
    family = next(iter(families), None)
    layout = _project_layout_from_root(root)
    if layout["mode"] == "mixed":
        raise ValueError("项目同时包含中文与旧版英文阶段目录，请先迁移并合并")
    if family is not None and layout["pinned"] and family != layout["mode"]:
        expected = "中文" if layout["mode"] == "canonical" else "旧版英文"
        raise ValueError(f"项目已使用{expected}目录布局，不能创建另一套平行目录")
    return family


def _validate_publication_layout(
    relative: str, *, allow_unregistered: bool
) -> None:
    pure = PurePosixPath(relative)
    first = pure.parts[0].casefold()
    role = _root_role(pure.parts[0])
    reason = PROTECTED_PUBLISH_ROOTS.get(first)
    if reason is not None:
        raise ValueError(reason)
    if pure.name.casefold() == PROJECT_FILE:
        raise ValueError("creator authority file cannot be a publication target")
    if role == "episodes":
        if len(pure.parts) < 3:
            raise ValueError("episode artifacts live in 剧集/<EP>/（兼容 episodes/<EP>/）")
        if EPISODE_ID_RE.fullmatch(pure.parts[1]) is None:
            raise ValueError(f"episode directory must use an EP001-style identifier: {pure.parts[1]}")
    if not allow_unregistered and role not in PUBLISHABLE_ROOT_ROLES:
        raise ValueError(
            f"{pure.parts[0]} is not a project stage directory; expected one of {', '.join(PUBLISHABLE_ROOTS)}"
        )
    if role is not None and pure.parts[0] not in {CANONICAL_ROOTS[role], LEGACY_ROOTS[role]}:
        raise ValueError(f"阶段目录大小写或拼写不规范：{pure.parts[0]}")


def _project_path(root: Path, relative: str, *, create_parent: bool = False) -> Path:
    root = root.resolve()
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current /= part
        # ``is_symlink`` misses the reparse points that are not name
        # surrogates, and a Windows creator can make those without any extra
        # privilege. Ask about the attribute directly instead.
        try:
            details = os.lstat(current)
        except FileNotFoundError:
            details = None
        if details is not None and _is_link_or_reparse(details):
            raise ProjectConflictError(f"project parent cannot be a symlink: {part}")
        if details is not None and not stat.S_ISDIR(details.st_mode):
            raise ProjectConflictError(f"project parent is not a directory: {part}")
        if create_parent and details is None:
            current.mkdir()
    try:
        target_details = os.lstat(target)
    except (FileNotFoundError, NotADirectoryError):
        target_details = None
    if target_details is not None and (
        _is_link_or_reparse(target_details) or not stat.S_ISREG(target_details.st_mode)
    ):
        raise ProjectConflictError(f"project target is not a regular file: {relative}")
    if not target.parent.resolve().is_relative_to(root):
        raise ValueError(f"path escapes project root: {relative}")
    return target


def _live_hash(root: Path, relative: str) -> str | None:
    try:
        target = _project_path(root, relative)
    except (OSError, ProjectConflictError, ValueError):
        return None
    if not target.is_file():
        return None
    return sha256_file(target)


@contextlib.contextmanager
def _lock_handle(handle: Any) -> Iterator[None]:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        locking = getattr(msvcrt, "locking")
        lock = getattr(msvcrt, "LK_LOCK")
        unlock = getattr(msvcrt, "LK_UNLCK")
        locking(handle.fileno(), lock, 1)
        try:
            yield
        finally:
            handle.seek(0)
            locking(handle.fileno(), unlock, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _project_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".short-drama/project.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle, _lock_handle(handle):
        yield


def _hash_mapping(root: Path, relatives: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in relatives:
        digest = _live_hash(root, relative)
        if digest is None:
            raise ProjectConflictError(f"project file is missing or unsafe: {relative}")
        result[relative] = digest
    return result


def _inputs_current(root: Path, record: Mapping[str, Any]) -> bool:
    inputs = record.get("inputs", {})
    return isinstance(inputs, Mapping) and all(
        isinstance(path, str)
        and isinstance(expected, str)
        and _live_hash(root, path) == expected
        for path, expected in inputs.items()
    )


def _artifact_state_from(
    record: Mapping[str, Any], live_hash: Callable[[str], str | None]
) -> str:
    """Decide one artifact's lifecycle state from the bytes on disk right now.

    The whole lifecycle rests on the two `!= live` comparisons below: an
    accepted artifact whose bytes were edited behind the tool's back has not
    been accepted in its current form, and must fall back to `update_needed`.

    This is written once and reached by two callers -- path-based and
    directory-fd-based -- which differ only in how they hash a file. It used to
    be two transcriptions of the same rules, with nothing asserting they agreed,
    and the dashboard renders the fd one.
    """

    outputs = record.get("outputs", [])
    if not isinstance(outputs, list) or not outputs:
        return "draft"
    live = {path: live_hash(path) for path in outputs if isinstance(path, str)}
    if len(live) != len(outputs) or any(value is None for value in live.values()):
        return "update_needed"
    inputs = record.get("inputs", {})
    if not isinstance(inputs, Mapping) or any(
        not isinstance(path, str)
        or not isinstance(expected, str)
        or live_hash(path) != expected
        for path, expected in inputs.items()
    ):
        return "update_needed"
    acceptance = record.get("acceptance")
    if not isinstance(acceptance, Mapping):
        return "needs_confirmation"
    if acceptance.get("decision") == "rejected":
        return "revise"
    if acceptance.get("decision") != "accepted" or acceptance.get("outputs") != live:
        return "update_needed"
    review = record.get("review")
    if not isinstance(review, Mapping):
        return "accepted"
    if review.get("outputs") != live:
        return "update_needed"
    verdict = review.get("verdict")
    if verdict in {"approve", "approve_with_notes"}:
        return "approved"
    if verdict == "revise":
        return "revise"
    return "accepted"


def _artifact_state(root: Path, record: Mapping[str, Any]) -> str:
    return _artifact_state_from(record, lambda path: _live_hash(root, path))


def _authority_report(
    project: Mapping[str, Any], state: Mapping[str, Any]
) -> dict[str, str]:
    """Say whether the manifest still holds what each bound decision wrote.

    `set-authority` is the only sanctioned way into `short-drama.json`, and it
    already records the digest of what it wrote. Comparing that against the file
    is what turns a hand edit from invisible into reported.
    """
    bindings = state.get("authority")
    if not isinstance(bindings, Mapping):
        return {}
    report: dict[str, str] = {}
    for field, binding in bindings.items():
        if not isinstance(field, str) or not isinstance(binding, Mapping):
            continue
        try:
            tokens = _authority_tokens(field)
        except ValueError:
            report[field] = "not_authority_field"
            continue
        missing = object()
        cursor: Any = project
        for token in tokens:
            cursor = cursor.get(token, missing) if isinstance(cursor, Mapping) else missing
            if cursor is missing:
                break
        if cursor is missing:
            report[field] = "missing"
            continue
        digest = hashlib.sha256(
            json.dumps(cursor, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        report[field] = "bound" if digest == binding.get("value_sha256") else "hand_edited"
    return report


def _build_status(
    *,
    project: Mapping[str, Any],
    state: Mapping[str, Any],
    layout: Mapping[str, Any],
    project_root: str,
    artifact_state: Callable[[Mapping[str, Any]], str],
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    artifacts: dict[str, str] = {}
    # Which stage produced each file. The state already knows; without it every
    # reader has to guess from the path, and a stage added later reads as
    # "unrecognised" until someone updates that reader's own list of names.
    ownership: dict[str, str] = {}
    records = state.get("artifacts", {})
    if isinstance(records, Mapping):
        for artifact_id, record in records.items():
            if not isinstance(artifact_id, str) or not isinstance(record, Mapping):
                continue
            value = artifact_state(record)
            artifacts[artifact_id] = value
            counts[value] = counts.get(value, 0) + 1
            owner = record.get("owner")
            if not isinstance(owner, str) or not owner:
                continue
            for output in record.get("outputs", []) or []:
                if isinstance(output, str) and output:
                    ownership[output] = owner
    languages = project_languages(project)
    video_model_profile = project_video_model_profile(project)
    return {
        "project_id": project.get("project_id"),
        "title": project.get("title"),
        "language": languages["language"],
        "prompt_language": languages["prompt_language"],
        "video_prompt_language": languages["video_prompt_language"],
        "video_model_profile": video_model_profile,
        "project_root": project_root,
        "last_action": state.get("last_action"),
        "layout": dict(layout),
        "artifact_states": counts,
        "artifacts": artifacts,
        "ownership": ownership,
        "authority": _authority_report(project, state),
        "lifecycle": {"artifact_state": counts},
    }


def project_status(path: Path) -> dict[str, Any]:
    root = find_project(path)
    project = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    if not isinstance(project, dict):
        raise ValueError("project manifest must be an object")
    state = _read_state(root)
    return _build_status(
        project=project,
        state=state,
        layout=_project_layout_from_root(root, state),
        project_root=str(root),
        artifact_state=lambda record: _artifact_state(root, record),
    )


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


def _read_regular_at(directory_fd: int, relative: str) -> bytes:
    pure = PurePosixPath(relative)
    parent = _open_directory_at(directory_fd, pure.parts[:-1])
    descriptor = -1
    try:
        descriptor = os.open(
            pure.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent
        )
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise OSError("not a regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _directory_has_content_at(directory_fd: int, name: str) -> tuple[bool, bool]:
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
        )
    except FileNotFoundError:
        return False, False
    except OSError:
        return True, True
    try:
        with os.scandir(descriptor) as entries:
            return any(True for _ in entries), False
    finally:
        os.close(descriptor)


def _project_layout_at(directory_fd: int, state: Mapping[str, Any]) -> dict[str, Any]:
    recorded = state.get("project_layout_mode", "auto")
    canonical: list[str] = []
    legacy: list[str] = []
    unsafe: list[str] = []
    nonstandard: list[str] = []
    with os.scandir(directory_fd) as entries:
        root_entries = list(entries)
    for entry in root_entries:
        role = _root_role(entry.name)
        if role not in LAYOUT_PINNING_ROLES:
            continue
        has_content, unsafe_entry = _directory_has_content_at(directory_fd, entry.name)
        if not has_content:
            continue
        if unsafe_entry or entry.is_symlink():
            unsafe.append(entry.name)
            continue
        if entry.name == CANONICAL_ROOTS[role]:
            canonical.append(role)
        elif entry.name == LEGACY_ROOTS[role]:
            legacy.append(role)
        else:
            nonstandard.append(entry.name)
    detected = {mode for mode, values in (("canonical", canonical), ("legacy", legacy)) if values}
    conflict = bool(unsafe or nonstandard) or len(detected) > 1 or (
        recorded in {"canonical", "legacy"} and detected and detected != {recorded}
    )
    if conflict:
        mode = "mixed"
    elif recorded in {"canonical", "legacy"}:
        mode = str(recorded)
    elif detected:
        mode = next(iter(detected))
    else:
        mode = "canonical"
    roots = LEGACY_ROOTS if mode == "legacy" else CANONICAL_ROOTS
    return {
        "mode": mode,
        "pinned": recorded != "auto" or bool(detected),
        "roots": dict(roots),
        "nonstandardRoots": sorted(nonstandard),
        "unsafeRoots": sorted(unsafe),
    }


def project_status_at(directory_fd: int, *, project_root: str) -> dict[str, Any]:
    project = json.loads(_read_regular_at(directory_fd, PROJECT_FILE).decode("utf-8"))
    try:
        raw_state = json.loads(
            _read_regular_at(directory_fd, STATE_FILE.as_posix()).decode("utf-8")
        )
    except FileNotFoundError:
        raw_state = {
            "schema_version": STATE_SCHEMA,
            "project_id": project.get("project_id"),
            "project_layout_mode": "auto",
            "last_action": "untracked",
            "artifacts": {},
        }
    if not isinstance(project, dict) or not isinstance(raw_state, dict):
        raise ValueError("project files must contain objects")
    state = _normalize_state(raw_state)
    return _build_status(
        project=project,
        state=state,
        layout=_project_layout_at(directory_fd, state),
        project_root=project_root,
        artifact_state=lambda record: _artifact_state_at(directory_fd, record),
    )


def _live_hash_at(directory_fd: int, relative: str) -> str | None:
    try:
        return sha256_bytes(_read_regular_at(directory_fd, relative))
    except (OSError, ValueError):
        return None


def _artifact_state_at(directory_fd: int, record: Mapping[str, Any]) -> str:
    return _artifact_state_from(record, lambda path: _live_hash_at(directory_fd, path))


def project_path_lifecycle_at(
    directory_fd: int, relative: str
) -> dict[str, str] | None:
    """Return the one creator-facing state for a tracked path."""

    normalized = _relative_path(relative, allow_operations=True)
    try:
        raw_state = json.loads(
            _read_regular_at(directory_fd, STATE_FILE.as_posix()).decode("utf-8")
        )
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw_state, Mapping):
        return None
    state = _normalize_state(raw_state)
    for record in state["artifacts"].values():
        if isinstance(record, Mapping) and normalized in record.get("outputs", []):
            return {"artifact_state": _artifact_state_at(directory_fd, record)}
    return None


# The three functions below are the path-based half of the dashboard contract.
# Windows has no ``openat``, so the dashboard pins a project root by verified
# path there instead of by directory descriptor and calls these. They compose
# the same rules the descriptor twins do -- ``_build_status``,
# ``_artifact_state_from``, ``_normalize_state`` -- so the two halves cannot
# drift on what a status or a lifecycle state means; they differ only in how a
# file is reached.


def _read_regular(root: Path, relative: str) -> bytes:
    """Read one project file, refusing every link and reparse point on the way.

    ``os.lstat`` never follows, so a component swapped for a symlink or a
    junction is rejected rather than traversed. This is the Windows stand-in
    for opening each component with ``O_NOFOLLOW``.
    """

    pure = PurePosixPath(relative)
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        details = os.lstat(current)
        if _is_link_or_reparse(details) or not stat.S_ISDIR(details.st_mode):
            raise ProjectConflictError(f"project parent is unsafe: {part}")
    target = current / pure.name
    details = os.lstat(target)
    if _is_link_or_reparse(details) or not stat.S_ISREG(details.st_mode):
        raise ProjectConflictError(f"project file is unsafe: {relative}")
    with open(target, "rb") as handle:
        return handle.read()


def project_status_from_root(root: Path, *, project_root: str) -> dict[str, Any]:
    """Report status for an already-pinned project root.

    ``project_status`` locates the project first and requires recorded state;
    this twin takes the root the caller pinned and tolerates a project that has
    never been tracked, matching ``project_status_at``.
    """

    project = json.loads(_read_regular(root, PROJECT_FILE).decode("utf-8"))
    try:
        raw_state = json.loads(
            _read_regular(root, STATE_FILE.as_posix()).decode("utf-8")
        )
    except FileNotFoundError:
        raw_state = {
            "schema_version": STATE_SCHEMA,
            "project_id": project.get("project_id"),
            "project_layout_mode": "auto",
            "last_action": "untracked",
            "artifacts": {},
        }
    if not isinstance(project, dict) or not isinstance(raw_state, dict):
        raise ValueError("project files must contain objects")
    state = _normalize_state(raw_state)
    return _build_status(
        project=project,
        state=state,
        layout=_project_layout_from_root(root, state),
        project_root=project_root,
        artifact_state=lambda record: _artifact_state(root, record),
    )


def project_path_lifecycle(root: Path, relative: str) -> dict[str, str] | None:
    """Return the one creator-facing state for a tracked path."""

    normalized = _relative_path(relative, allow_operations=True)
    try:
        raw_state = json.loads(
            _read_regular(root, STATE_FILE.as_posix()).decode("utf-8")
        )
    except (OSError, ProjectConflictError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw_state, Mapping):
        return None
    state = _normalize_state(raw_state)
    for record in state["artifacts"].values():
        if isinstance(record, Mapping) and normalized in record.get("outputs", []):
            return {"artifact_state": _artifact_state(root, record)}
    return None


@contextlib.contextmanager
def coordinated_project_text_edit(
    root: Path, relative: str, expected_version: str
) -> Iterator[None]:
    normalized = _relative_path(relative)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_version):
        raise ValueError("expected version must be a SHA-256 digest")
    # The descriptor twin opens the operations directory with O_NOFOLLOW. Match
    # it: a lock taken through a redirected `.short-drama` would leave two
    # dashboards each believing they hold the project.
    try:
        operations = os.lstat(root / ".short-drama")
    except FileNotFoundError:
        operations = None
    if operations is not None and _is_link_or_reparse(operations):
        raise OSError("project operations directory is unsafe")
    with _project_lock(root):
        current = sha256_bytes(_read_regular(root, normalized))
        if current != expected_version:
            raise ProjectConflictError("file changed since it was opened")
        yield


def _validate_structured_content(relative: str, content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"publication text must be UTF-8: {relative}") from exc
    suffix = PurePosixPath(relative).suffix.casefold()
    if suffix not in DELIVERY_SUFFIXES:
        raise ValueError(f"publication supports Markdown, JSON, and JSONL only: {relative}")
    if suffix == ".json":
        json.loads(text)
    elif suffix == ".jsonl":
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {relative}:{number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL records must be objects: {relative}:{number}")


def _check_output_ownership(state: Mapping[str, Any], artifact_id: str, outputs: Iterable[str]) -> None:
    wanted = set(outputs)
    artifacts = state.get("artifacts", {})
    if not isinstance(artifacts, Mapping):
        return
    for other_id, other in artifacts.items():
        if other_id == artifact_id or not isinstance(other, Mapping):
            continue
        existing = other.get("outputs", [])
        overlap = wanted.intersection(existing if isinstance(existing, list) else [])
        if overlap:
            raise ValueError(
                f"project path already belongs to {other_id}: {', '.join(sorted(overlap))}"
            )


def publish_candidate(
    root: Path,
    *,
    owner: str,
    artifact_id: str,
    outputs: Mapping[str, str | bytes],
    inputs: Iterable[str] | None = None,
    allow_unregistered_path: bool = False,
) -> dict[str, Any]:
    root = find_project(root)
    if not owner.strip() or not artifact_id.strip():
        raise ValueError("owner and artifact_id are required")
    if not outputs:
        raise ValueError("publish needs at least one output")
    normalized_outputs = _validate_path_set(root, outputs, label="publication")
    prepared: dict[str, bytes] = {}
    for relative in normalized_outputs:
        _validate_publication_layout(
            relative, allow_unregistered=allow_unregistered_path
        )
        content = outputs[relative]
        encoded = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        _validate_structured_content(relative, encoded)
        prepared[relative] = encoded

    input_paths = list(inputs or [])
    normalized_inputs = _validate_path_set(root, input_paths, label="input")
    if set(normalized_inputs).intersection(normalized_outputs):
        raise ValueError("an output cannot also be its own direct input")

    with _project_lock(root):
        state = _read_state(root)
        family = _validate_project_output_layout(root, normalized_outputs)
        _check_output_ownership(state, artifact_id, normalized_outputs)
        current_inputs = _hash_mapping(root, normalized_inputs)
        for relative, content in prepared.items():
            target = _project_path(root, relative, create_parent=True)
            _atomic_bytes(target, content)
        state["artifacts"][artifact_id] = {
            "owner": owner,
            "outputs": normalized_outputs,
            "inputs": current_inputs,
            "acceptance": None,
            "review": None,
            "updated_at": utc_now(),
        }
        if state.get("project_layout_mode") == "auto" and family is not None:
            state["project_layout_mode"] = family
        _save_state(root, state, action="published")
    return {
        "artifact_id": artifact_id,
        "owner": owner,
        "outputs": normalized_outputs,
        "state": "needs_confirmation",
    }


def record_creator_acceptance(
    root: Path,
    *,
    artifact_id: str,
    decision: str,
    note: str = "",
) -> dict[str, Any]:
    if decision not in {"accepted", "rejected"}:
        raise ValueError("decision must be accepted or rejected")
    root = find_project(root)
    with _project_lock(root):
        state = _read_state(root)
        record = state["artifacts"].get(artifact_id)
        if not isinstance(record, dict):
            raise KeyError(f"unknown artifact: {artifact_id}")
        outputs = _hash_mapping(root, record.get("outputs", []))
        if not _inputs_current(root, record):
            raise ProjectConflictError("direct input changed; republish before acceptance")
        record["acceptance"] = {
            "decision": decision,
            "at": utc_now(),
            "outputs": outputs,
            "note": note.strip(),
        }
        record["review"] = None
        record["updated_at"] = utc_now()
        _save_state(root, state, action="accepted" if decision == "accepted" else "rejected")
    return {"artifact_id": artifact_id, "decision": decision, "state": _artifact_state(root, record)}


def record_review(
    root: Path,
    *,
    artifact_id: str,
    verdict: str,
    reviewer: str = "",
    note: str = "",
) -> dict[str, Any]:
    if verdict not in {"approve", "approve_with_notes", "revise", "provisional"}:
        raise ValueError("unsupported review verdict")
    root = find_project(root)
    with _project_lock(root):
        state = _read_state(root)
        record = state["artifacts"].get(artifact_id)
        if not isinstance(record, dict):
            raise KeyError(f"unknown artifact: {artifact_id}")
        outputs = _hash_mapping(root, record.get("outputs", []))
        acceptance = record.get("acceptance")
        if (
            not isinstance(acceptance, Mapping)
            or acceptance.get("decision") != "accepted"
            or acceptance.get("outputs") != outputs
        ):
            raise ProjectConflictError("review requires current creator acceptance")
        if not _inputs_current(root, record):
            raise ProjectConflictError("direct input changed; republish before review")
        record["review"] = {
            "verdict": verdict,
            "at": utc_now(),
            "outputs": outputs,
            "reviewer": reviewer.strip(),
            "note": note.strip(),
        }
        record["updated_at"] = utc_now()
        _save_state(root, state, action="reviewed")
    return {"artifact_id": artifact_id, "verdict": verdict, "state": _artifact_state(root, record)}


def _authority_tokens(field: str) -> list[str]:
    if not field.startswith("/"):
        raise ValueError(f"--field must be a JSON pointer starting with /: {field}")
    tokens = [
        token.replace("~1", "/").replace("~0", "~") for token in field[1:].split("/")
    ]
    if any(not token for token in tokens):
        raise ValueError(f"--field has an empty pointer segment: {field}")
    if field not in FORMAT_POINTERS and (
        tokens[0] != AUTHORITY_ROOT_TOKEN or len(tokens) < 2
    ):
        raise ValueError(
            f"set-authority writes /{AUTHORITY_ROOT_TOKEN}/* and "
            f"{', '.join(FORMAT_POINTERS)} only"
        )
    if tokens[:2] == [AUTHORITY_ROOT_TOKEN, "decisions_artifact"]:
        # Where decisions are kept is project layout, not a creative choice; a
        # decision record must not move the place its own successors are read from.
        raise ValueError("decisions_artifact is project layout, not a creator choice")
    return tokens


def _accepted_decision_value(
    root: Path,
    state: Mapping[str, Any],
    *,
    decision_path: str,
    decision_id: str,
    field: str,
) -> Any:
    relative = _relative_path(decision_path)
    if _root_role(PurePosixPath(relative).parts[0]) != "creator-decisions":
        expected = CANONICAL_ROOTS["creator-decisions"]
        raise ValueError(f"creator decisions live in {expected}/: {relative}")
    try:
        _, record = _artifact_for_path(state, relative)
    except PackageBlockedError as exc:
        raise ValueError(f"creator decision file is not a published artifact: {relative}") from exc
    if _artifact_state(root, record) not in {"accepted", "approved"}:
        raise ValueError(f"creator decision file is not accepted and current: {relative}")
    text = _project_path(root, relative).read_text(encoding="utf-8")
    latest: tuple[int, Mapping[str, Any]] | None = None
    superseded_by: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            decision = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{relative}:{number} is not a creator decision record: {exc}") from exc
        if not isinstance(decision, Mapping):
            continue
        # The file is append-only, so a revision arrives as a later line. Read the
        # whole file before deciding: the first match may already be retracted.
        if decision.get("decision_id") == decision_id:
            latest = (number, decision)
        elif (
            decision.get("supersedes_decision_id") == decision_id
            and decision.get("status") == "accepted"
        ):
            superseded_by = str(decision.get("decision_id"))
    if latest is None:
        raise KeyError(f"unknown creator decision: {decision_id}")
    if superseded_by is not None:
        raise ValueError(f"{decision_id} was superseded by {superseded_by}")
    number, decision = latest
    if decision.get("status") != "accepted":
        raise ValueError(f"{decision_id} is not an accepted creator decision")
    locators = decision.get("target_locators")
    if not isinstance(locators, list) or not any(
        isinstance(locator, Mapping)
        and locator.get("src") == "short-drama"
        and locator.get("field") == field
        for locator in locators
    ):
        raise ValueError(f"{decision_id} does not target {field}")
    if "accepted_value" not in decision:
        raise ValueError(f"{decision_id} carries no accepted_value at {relative}:{number}")
    return decision["accepted_value"]


def _json_kind(value: Any) -> str:
    """The JSON type of a value. 90 and 92.5 are both numbers."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return "null" if value is None else type(value).__name__


def _write_authority_value(project: dict[str, Any], tokens: list[str], value: Any) -> Any:
    cursor: Any = project
    blocks: list[dict[str, Any]] = []
    walked: list[str] = []
    for token in tokens[:-1]:
        cursor = cursor.get(token) if isinstance(cursor, dict) else None
        walked.append(token)
        if not isinstance(cursor, dict):
            raise ValueError(f"project manifest has no object at /{'/'.join(walked)}")
        if "status" in cursor:
            blocks.append(cursor)
    leaf = tokens[-1]
    if leaf not in cursor:
        # The manifest shape is declared by the project template. A decision may
        # fill a declared slot; inventing one would put a field downstream cannot
        # know to read.
        raise ValueError(f"project manifest declares no /{'/'.join([*walked, leaf])}")
    current = cursor.get(leaf)
    if isinstance(current, Mapping) and not isinstance(value, Mapping) and "status" not in current:
        # A choices map is merged, never replaced wholesale: replacing it would
        # silently drop the choices a previous decision already recorded.
        raise ValueError(f"/{'/'.join([*walked, leaf])} needs an object accepted_value")
    if isinstance(current, Mapping) and isinstance(value, Mapping) and "status" not in current:
        merged = {**current, **value}
        cursor[leaf] = merged
        for block in blocks:
            block["status"] = "accepted"
        return merged
    if not (isinstance(current, dict) and "status" in current):
        if current is not None and _json_kind(value) != _json_kind(current):
            raise ValueError(
                f"/{'/'.join([*walked, leaf])} is {_json_kind(current)}; "
                f"accepted_value is {_json_kind(value)}"
            )
        cursor[leaf] = value
        # Writing one choice inside an authority block accepts that block: a
        # downstream stage gates on the block's status before reading the choice.
        for block in blocks:
            block["status"] = "accepted"
        return value
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"/{'/'.join(tokens)} needs a non-empty object accepted_value")
    if "status" in value:
        raise ValueError("accepted_value must not carry its own status")
    block = dict(current)
    choices = block.get("choices")
    if isinstance(choices, Mapping):
        block["choices"] = {**choices, **value}
    else:
        block.update(value)
    block["status"] = "accepted"
    cursor[leaf] = block
    return block


def set_creator_authority(
    root: Path,
    *,
    field: str,
    decision_path: str,
    decision_id: str,
) -> dict[str, Any]:
    tokens = _authority_tokens(field)
    root = find_project(root)
    with _project_lock(root):
        state = _read_state(root)
        value = _accepted_decision_value(
            root,
            state,
            decision_path=decision_path,
            decision_id=decision_id,
            field=field,
        )
        if field == EPISODE_LENGTH_POINTER and not (
            isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        ):
            raise ValueError("target_seconds_per_episode must be a positive number of seconds")
        project_path = root / PROJECT_FILE
        project = json.loads(project_path.read_text(encoding="utf-8"))
        if not isinstance(project, dict):
            raise ValueError("project manifest must be an object")
        written = _write_authority_value(project, tokens, value)
        if field == PACING_POINTER:
            # Check what the manifest will hold, not what the decision said: an
            # object slot is merged, so a half decision leaves the other rate at
            # null and the estimate stays unusable while the write reports bound.
            # Type only — which rates make an estimate usable is the write
            # stage's call, and it stays in one place.
            if not isinstance(written, Mapping) or not written:
                raise ValueError("pacing must be an object of named rates")
            for name, rate in written.items():
                if not (
                    isinstance(rate, (int, float))
                    and not isinstance(rate, bool)
                    and rate > 0
                ):
                    raise ValueError(
                        f"pacing rate {name} must be a positive number; "
                        f"a decision that sets only some rates leaves the rest unset"
                    )
        atomic_json(project_path, project)
        # Record which decision produced the value, so a later reader can tell a
        # bound write from a hand edit and see what a re-bind replaced.
        bindings = state.get("authority")
        if not isinstance(bindings, dict):
            bindings = {}
            state["authority"] = bindings
        bindings[field] = {
            "decision": f"{_relative_path(decision_path)}#{decision_id}",
            # The written value, so a later reader can tell the manifest still
            # holds what the decision said rather than a hand edit made since.
            "value_sha256": hashlib.sha256(
                json.dumps(written, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "set_at": utc_now(),
        }
        _save_state(root, state, action="authority_set")
    return {"field": field, "decision_id": decision_id, "value": written}


def _artifact_for_path(state: Mapping[str, Any], relative: str) -> tuple[str, Mapping[str, Any]]:
    found: list[tuple[str, Mapping[str, Any]]] = []
    artifacts = state.get("artifacts", {})
    if isinstance(artifacts, Mapping):
        for artifact_id, record in artifacts.items():
            if (
                isinstance(artifact_id, str)
                and isinstance(record, Mapping)
                and relative in record.get("outputs", [])
            ):
                found.append((artifact_id, record))
    if len(found) != 1:
        raise PackageBlockedError(f"delivery source has no unique artifact owner: {relative}")
    return found[0]


def _replace_directory(source: Path, target: Path) -> None:
    backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.old")
    moved_old = False
    try:
        if target.exists():
            details = os.lstat(target)
            if _is_link_or_reparse(details) or not stat.S_ISDIR(details.st_mode):
                raise ProjectConflictError(f"delivery target is unsafe: {target.name}")
            os.replace(target, backup)
            moved_old = True
        os.replace(source, target)
        _fsync_directory(target.parent)
        if moved_old:
            shutil.rmtree(backup)
    except Exception:
        if moved_old and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise


def _require_episode_delivery_path(relative: str, episode: str) -> None:
    parts = PurePosixPath(relative).parts
    if (
        len(parts) < 3
        or _root_role(parts[0]) != "episodes"
        or parts[1] != episode
    ):
        raise ValueError(f"delivery paths must belong to {episode}: {relative}")


def build_delivery_package(
    root: Path,
    *,
    episode: str,
    includes: Iterable[str],
    omissions: Iterable[str] | Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if EPISODE_ID_RE.fullmatch(episode) is None:
        raise ValueError("episode must use an EP001-style identifier")
    root = find_project(root)
    include_paths = _validate_path_set(root, includes, label="delivery")
    if not include_paths:
        raise ValueError("delivery needs at least one included file")
    omission_values = omissions.keys() if isinstance(omissions, Mapping) else omissions or []
    omitted = _validate_path_set(root, omission_values, label="omission")
    if set(include_paths).intersection(omitted):
        raise ValueError("a delivery path cannot be both included and omitted")
    for relative in (*include_paths, *omitted):
        _require_episode_delivery_path(relative, episode)

    with _project_lock(root):
        state = _read_state(root)
        entries: list[dict[str, str]] = []
        snapshots: dict[str, bytes] = {}
        for relative in include_paths:
            if PurePosixPath(relative).suffix.casefold() not in DELIVERY_SUFFIXES:
                raise PackageBlockedError(f"delivery source is not text/JSON: {relative}")
            artifact_id, record = _artifact_for_path(state, relative)
            if _artifact_state(root, record) != "approved":
                raise PackageBlockedError(f"delivery source is not approved and current: {relative}")
            source = _project_path(root, relative)
            data = source.read_bytes()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PackageBlockedError(f"delivery source must be UTF-8: {relative}") from exc
            digest = sha256_bytes(data)
            acceptance = record.get("acceptance")
            review = record.get("review")
            accepted_outputs = (
                acceptance.get("outputs") if isinstance(acceptance, Mapping) else None
            )
            reviewed_outputs = (
                review.get("outputs") if isinstance(review, Mapping) else None
            )
            if (
                not isinstance(accepted_outputs, Mapping)
                or not isinstance(reviewed_outputs, Mapping)
                or accepted_outputs.get(relative) != digest
                or reviewed_outputs.get(relative) != digest
                or not _inputs_current(root, record)
            ):
                raise PackageBlockedError(
                    f"delivery source changed after approval: {relative}"
                )
            snapshots[relative] = data
            entries.append(
                {
                    "artifact_id": artifact_id,
                    "source": relative,
                    "path": f"artifacts/{relative}",
                    "sha256": digest,
                }
            )

        layout = _project_layout_from_root(root, state)
        if layout["mode"] == "mixed":
            raise PackageBlockedError("mixed project layouts cannot be packaged")
        delivery_root = str(layout["roots"]["delivery"])
        parent = root / delivery_root
        try:
            parent_details = os.lstat(parent)
        except FileNotFoundError:
            parent.mkdir(parents=True)
        else:
            if _is_link_or_reparse(parent_details) or not stat.S_ISDIR(
                parent_details.st_mode
            ):
                raise ProjectConflictError("delivery root is unsafe")
        temporary = parent / f".{episode}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir()
        try:
            for entry in entries:
                destination = temporary / entry["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                _atomic_bytes(destination, snapshots[entry["source"]])
            manifest = {
                "schema_version": "1.0",
                "project_id": state.get("project_id"),
                "episode": episode,
                "created_at": utc_now(),
                "files": entries,
                "omitted": [
                    {
                        "source": path,
                        "reason": (
                            omissions[path]
                            if isinstance(omissions, Mapping) and path in omissions
                            else "creator_omitted"
                        ),
                    }
                    for path in omitted
                ],
            }
            atomic_json(temporary / "manifest.json", manifest)
            checksum_members = ["manifest.json", *(entry["path"] for entry in entries)]
            checksums = "".join(
                f"{sha256_file(temporary / relative)}  {relative}\n"
                for relative in sorted(checksum_members)
            )
            _atomic_bytes(temporary / "checksums.sha256", checksums.encode("utf-8"))
            target = parent / episode
            _replace_directory(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return {
        "episode": episode,
        "delivery_root": f"{delivery_root}/{episode}",
        "files": entries,
        "omitted": omitted,
    }


def _delivery_directory(root: Path, episode: str) -> Path:
    matches: list[Path] = []
    for name in {CANONICAL_ROOTS["delivery"], LEGACY_ROOTS["delivery"]}:
        parent = root / name
        try:
            parent_details = os.lstat(parent)
        except FileNotFoundError:
            continue
        if _is_link_or_reparse(parent_details) or not stat.S_ISDIR(
            parent_details.st_mode
        ):
            raise ProjectConflictError("delivery root is unsafe")
        delivery = parent / episode
        try:
            details = os.lstat(delivery)
        except FileNotFoundError:
            continue
        if _is_link_or_reparse(details) or not stat.S_ISDIR(details.st_mode):
            raise ProjectConflictError("delivery package is not a regular directory")
        matches.append(delivery)
    if len(matches) != 1:
        raise FileNotFoundError(f"expected exactly one delivered package for {episode}")
    return matches[0]


def _manifest_problems(
    delivery: Path,
    manifest: object,
    *,
    episode: str,
    checksum_paths: set[str],
) -> list[str]:
    problems: list[str] = []
    if not isinstance(manifest, Mapping):
        return ["manifest must be an object"]
    if manifest.get("schema_version") != "1.0":
        problems.append("manifest schema is invalid")
    if manifest.get("episode") != episode:
        problems.append("manifest does not describe this episode")
    files = manifest.get("files")
    if not isinstance(files, list):
        return [*problems, "manifest files must be a list"]
    manifest_paths: set[str] = set()
    sources: set[str] = set()
    for number, entry in enumerate(files, 1):
        if not isinstance(entry, Mapping) or set(entry) != {
            "artifact_id",
            "source",
            "path",
            "sha256",
        }:
            problems.append(f"manifest file {number} has invalid fields")
            continue
        artifact_id = entry.get("artifact_id")
        source_raw = entry.get("source")
        path_raw = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(artifact_id, str) or not artifact_id:
            problems.append(f"manifest file {number} has no artifact id")
        if not isinstance(source_raw, str) or not isinstance(path_raw, str):
            problems.append(f"manifest file {number} has an unsafe path")
            continue
        try:
            source = _relative_path(source_raw)
            relative = _relative_path(path_raw)
        except ValueError:
            problems.append(f"manifest file {number} has an unsafe path")
            continue
        try:
            _require_episode_delivery_path(source, episode)
        except ValueError:
            problems.append(f"manifest source does not belong to {episode}: {source}")
        if relative != f"artifacts/{source}":
            problems.append(f"manifest path does not match its source: {relative}")
        if relative in manifest_paths:
            problems.append(f"duplicate manifest path: {relative}")
        if source in sources:
            problems.append(f"duplicate manifest source: {source}")
        manifest_paths.add(relative)
        sources.add(source)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            problems.append(f"manifest file {number} has an invalid hash")
            continue
        member = delivery / relative
        if member.is_file() and not member.is_symlink() and sha256_file(member) != digest:
            problems.append(f"manifest hash mismatch: {relative}")
    expected_manifest_paths = checksum_paths - {"manifest.json"}
    if manifest_paths != expected_manifest_paths:
        problems.append("manifest files do not match checksum members")

    omitted = manifest.get("omitted")
    if not isinstance(omitted, list):
        problems.append("manifest omissions must be a list")
    else:
        for number, entry in enumerate(omitted, 1):
            if (
                not isinstance(entry, Mapping)
                or set(entry) != {"source", "reason"}
                or not isinstance(entry.get("source"), str)
                or not isinstance(entry.get("reason"), str)
                or not str(entry.get("reason")).strip()
            ):
                problems.append(f"manifest omission {number} is invalid")
                continue
            try:
                source = _relative_path(str(entry["source"]))
                _require_episode_delivery_path(source, episode)
            except ValueError:
                problems.append(f"manifest omission {number} has an unsafe source")
    return problems


def _same_directory(left: Path, right: Path) -> bool:
    """Do these two paths name the same directory on disk?

    String comparison is not enough: a case-insensitive volume and a filesystem
    that stores a different Unicode normalisation both spell one directory two
    ways, and either spelling would otherwise slip past a containment check.
    """
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _require_safe_export_destination(destination: Path, root: Path) -> None:
    """Refuse any destination that would take the project down with it.

    The export replaces its destination directory wholesale, so a destination
    that is -- or contains -- the project being exported would delete the
    source. Ancestors matter as much as descendants here.
    """
    if destination == root or root in destination.parents:
        raise ValueError("export destination must live outside the project root")
    for ancestor in (root, *root.parents):
        if destination == ancestor or _same_directory(destination, ancestor):
            raise ValueError(
                "export destination must not be the project root or any directory containing it"
            )


def _require_replaceable_export(destination: Path) -> None:
    """Only ever overwrite a directory this command itself produced.

    `--overwrite` removes the destination tree. Without this check a mistyped
    path turns an ordinary directory into a deletion, and nothing else in this
    tool can erase a directory outside the project.
    """
    if not destination.exists():
        return
    details = os.lstat(destination)
    if _is_link_or_reparse(details) or not stat.S_ISDIR(details.st_mode):
        raise ProjectConflictError("export destination is not a regular directory")
    manifest = destination / "manifest.json"
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            "refusing to overwrite a directory that is not a previous export: "
            f"{destination}"
        ) from exc
    if not isinstance(document, Mapping) or document.get("kind") != "creator_export":
        raise ValueError(
            "refusing to overwrite a directory that is not a previous export: "
            f"{destination}"
        )


def _export_regular_file(source: Path, destination: Path) -> str:
    """Copy one regular project file into the export tree and return its digest."""
    details = os.lstat(source)
    if _is_link_or_reparse(details) or not stat.S_ISREG(details.st_mode):
        raise ProjectConflictError(f"export source is not a regular file: {source.name}")
    if "\n" in source.name or "\r" in source.name:
        # checksums.sha256 is one record per line; such a name cannot be written
        # in a form any verifier could read back.
        raise ProjectConflictError(
            "export source name contains a line break and cannot be checksummed"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return sha256_file(destination)


def _export_episode(
    episode_directory: Path, target: Path, *, include_media: bool
) -> tuple[list[dict[str, str]], list[str]]:
    files: list[dict[str, str]] = []
    missing: list[str] = []
    for name in CREATOR_DOCUMENTS:
        source = episode_directory / name
        if not source.exists():
            missing.append(name)
            continue
        digest = _export_regular_file(source, target / name)
        files.append({"path": name, "sha256": digest})
    media_root = episode_directory / EXPORT_MEDIA_DIRECTORY
    if include_media and media_root.exists():
        media_details = os.lstat(media_root)
        if _is_link_or_reparse(media_details) or not stat.S_ISDIR(media_details.st_mode):
            # Silently exporting no media reads as "this episode has none".
            raise ProjectConflictError(
                f"{EXPORT_MEDIA_DIRECTORY} is not a regular directory: {episode_directory.name}"
            )
        for source in sorted(media_root.rglob("*")):
            if source.is_dir() and not source.is_symlink():
                continue
            relative = source.relative_to(episode_directory).as_posix()
            digest = _export_regular_file(source, target / relative)
            files.append({"path": relative, "sha256": digest})
    return files, missing


def _export_exclusions(root: Path, episodes_name: str) -> list[str]:
    """Name every top-level project directory this export did not copy.

    A fixed list lies twice over: it names roots a legacy-layout project does
    not have, and it stays silent about a published 设定集/ that the handover is
    missing.
    """
    excluded: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name == episodes_name or entry.name == PROJECT_FILE:
            continue
        if entry.is_dir() or entry.is_symlink():
            excluded.append(entry.name)
    return excluded


def _is_portable_windows_path(relative: str) -> bool:
    for part in PurePosixPath(relative).parts:
        stem = part.split(".")[0].casefold()
        if stem in WINDOWS_RESERVED_PATH_STEMS:
            return False
        if set(part) & WINDOWS_FORBIDDEN_PATH_CHARACTERS:
            return False
        if part.endswith((" ", ".")):
            return False
    return True


def build_creator_export(
    root: Path,
    *,
    out: Path,
    episodes: Sequence[str] | None = None,
    include_media: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy the current creator documents and produced media into a handover tree.

    This is a snapshot of what the project holds right now.  It deliberately
    asserts nothing about review or creator acceptance; `package` remains the
    only command that speaks for an approved delivery.
    """
    root = find_project(root)
    destination = out.expanduser().resolve()
    _require_safe_export_destination(destination, root)
    layout = _project_layout_from_root(root)
    if layout["mode"] == "mixed":
        raise PackageBlockedError("mixed project layouts cannot be exported")
    episodes_root = root / str(layout["roots"]["episodes"])
    available = sorted(
        entry.name
        for entry in episodes_root.iterdir()
        if entry.is_dir() and not entry.is_symlink()
        if EPISODE_ID_RE.fullmatch(entry.name)
    ) if episodes_root.is_dir() and not episodes_root.is_symlink() else []
    if episodes:
        selected = []
        for episode in episodes:
            if EPISODE_ID_RE.fullmatch(episode) is None:
                raise ValueError("episode must use an EP001-style identifier")
            if episode not in available:
                raise FileNotFoundError(f"episode directory not found: {episode}")
            if episode not in selected:
                selected.append(episode)
    else:
        selected = available
    if not selected:
        raise FileNotFoundError("no episode directory to export")
    if destination.exists():
        if not overwrite:
            raise FileExistsError(f"export destination already exists: {destination}")
        _require_replaceable_export(destination)

    project = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    try:
        exported: list[dict[str, Any]] = []
        members: list[dict[str, str]] = []
        episodes_name = str(layout["roots"]["episodes"])
        for episode in selected:
            target = temporary / episodes_name / episode
            target.mkdir(parents=True)
            files, missing = _export_episode(
                episodes_root / episode, target, include_media=include_media
            )
            for entry in files:
                members.append(
                    {
                        "path": f"{episodes_name}/{episode}/{entry['path']}",
                        "sha256": entry["sha256"],
                    }
                )
            exported.append(
                {"episode": episode, "files": files, "missing_documents": missing}
            )
        config_digest = _export_regular_file(
            root / PROJECT_FILE, temporary / PROJECT_FILE
        )
        members.append({"path": PROJECT_FILE, "sha256": config_digest})
        manifest = {
            "schema_version": EXPORT_SCHEMA,
            "kind": "creator_export",
            "asserts_approval": False,
            "project_id": project.get("project_id"),
            "title": project.get("title"),
            "created_at": utc_now(),
            "episodes": exported,
            "selection": {
                "episodes": "all" if not episodes else list(selected),
                "available_episodes": list(available),
                "include_media": bool(include_media),
            },
            "excluded": _export_exclusions(root, episodes_name),
            "windows_unsafe_paths": sorted(
                entry["path"]
                for entry in members
                if not _is_portable_windows_path(entry["path"])
            ),
        }
        atomic_json(temporary / "manifest.json", manifest)
        checksum_members = ["manifest.json", *(entry["path"] for entry in members)]
        checksums = "".join(
            f"{sha256_file(temporary / relative)}  {relative}\n"
            for relative in sorted(checksum_members)
        )
        _atomic_bytes(temporary / "checksums.sha256", checksums.encode("utf-8"))
        _replace_directory(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "kind": "creator_export",
        "out": str(destination),
        "episodes": [entry["episode"] for entry in exported],
        "files": [entry["path"] for entry in members],
        "excluded": manifest["excluded"],
        "include_media": bool(include_media),
        "missing_documents": {
            entry["episode"]: entry["missing_documents"]
            for entry in exported
            if entry["missing_documents"]
        },
        "windows_unsafe_paths": manifest["windows_unsafe_paths"],
    }


def verify_delivery_package(root: Path, *, episode: str) -> dict[str, Any]:
    if EPISODE_ID_RE.fullmatch(episode) is None:
        raise ValueError("episode must use an EP001-style identifier")
    root = find_project(root)
    delivery = _delivery_directory(root, episode)
    problems: list[str] = []
    actual: set[str] = set()
    for path in delivery.rglob("*"):
        relative = path.relative_to(delivery).as_posix()
        if path.is_symlink():
            problems.append(f"symlink is not allowed: {relative}")
        elif path.is_file():
            actual.add(relative)
        elif not path.is_dir():
            problems.append(f"unsupported delivery member: {relative}")
    checksums_path = delivery / "checksums.sha256"
    if checksums_path.is_symlink() or not checksums_path.is_file():
        problems.append("checksums.sha256 is missing or unsafe")
        return {"episode": episode, "status": "tampered", "problems": problems}
    expected: dict[str, str] = {}
    for number, line in enumerate(checksums_path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            problems.append(f"invalid checksum line {number}")
            continue
        digest, raw = match.groups()
        try:
            relative = _relative_path(raw)
        except ValueError:
            problems.append(f"unsafe checksum path: {raw}")
            continue
        if relative in expected:
            problems.append(f"duplicate checksum path: {relative}")
        expected[relative] = digest
    expected_members = set(expected) | {"checksums.sha256"}
    for extra in sorted(actual - expected_members):
        problems.append(f"unexpected delivery file: {extra}")
    for missing in sorted(expected_members - actual):
        problems.append(f"missing delivery file: {missing}")
    for relative, digest in expected.items():
        path = delivery / relative
        if not path.is_file() or path.is_symlink():
            continue
        if sha256_file(path) != digest:
            problems.append(f"checksum mismatch: {relative}")
    try:
        manifest = json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))
        problems.extend(
            _manifest_problems(
                delivery,
                manifest,
                episode=episode,
                checksum_paths=set(expected),
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        problems.append("manifest.json is unreadable")
    return {
        "episode": episode,
        "status": "verified" if not problems else "tampered",
        "problems": problems,
    }


def _lock_at(directory_fd: int) -> Any:
    try:
        operations = os.open(
            ".short-drama",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        try:
            os.mkdir(".short-drama", 0o700, dir_fd=directory_fd)
        except FileExistsError:
            pass
        operations = os.open(
            ".short-drama",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
    try:
        try:
            descriptor = os.open(
                "project.lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=operations
            )
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    "project.lock",
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=operations,
                )
            except FileExistsError:
                descriptor = os.open(
                    "project.lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=operations
                )
    finally:
        os.close(operations)
    return os.fdopen(descriptor, "a+b", buffering=0)


@contextlib.contextmanager
def coordinated_project_text_edit_at(
    directory_fd: int, relative: str, expected_version: str
) -> Iterator[None]:
    normalized = _relative_path(relative)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_version):
        raise ValueError("expected version must be a SHA-256 digest")
    with _lock_at(directory_fd) as handle, _lock_handle(handle):
        current = sha256_bytes(_read_regular_at(directory_fd, normalized))
        if current != expected_version:
            raise ProjectConflictError("file changed since it was opened")
        yield


def _parse_output_bindings(root: Path, values: Iterable[str]) -> dict[str, bytes]:
    outputs: dict[str, bytes] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--output uses PROJECT_TARGET=PROJECT_SOURCE")
        target, source_value = value.split("=", 1)
        relative = _relative_path(target)
        source_relative = _relative_path(source_value, allow_operations=True)
        source = _project_path(root, source_relative)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"publication source is missing or unsafe: {source_relative}")
        outputs[relative] = source.read_bytes()
    return outputs


def _parse_decision_ref(value: str) -> tuple[str, str]:
    path, separator, decision_id = value.rpartition("#")
    if not separator or not path.strip() or not decision_id.strip():
        raise ValueError("--decision-ref uses 创作者决策/<file>.jsonl#<decision-id>")
    return path.strip(), decision_id.strip()


def _parse_omissions(values: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        path, separator, reason = value.partition("=")
        result[_relative_path(path)] = reason.strip() if separator and reason.strip() else "creator_omitted"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a short-drama filesystem project.")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize a project without creative content.")
    init.add_argument("path")
    init.add_argument("--title", required=True)
    init.add_argument("--language", default="zh-CN")
    init.add_argument("--prompt-language", default=DEFAULT_PROMPT_LANGUAGE)
    init.add_argument("--aspect-ratio", default="9:16")
    init.add_argument("--episode-count", type=int)
    init.add_argument("--target-seconds", type=float, dest="target_seconds_per_episode")

    status = commands.add_parser("status", help="Print a creator-safe project summary.")
    status.add_argument("path", nargs="?", default=".")

    publish = commands.add_parser("publish", help="Atomically publish text/JSON outputs.")
    publish.add_argument("path")
    publish.add_argument("--owner", required=True)
    publish.add_argument("--artifact-id", required=True)
    publish.add_argument(
        "--output", action="append", required=True, dest="outputs",
        help="Bind PROJECT_TARGET=PROJECT_SOURCE; repeat for multiple files.",
    )
    publish.add_argument(
        "--input", action="append", default=[], dest="inputs",
        help="Record one direct project input path; repeat as needed.",
    )
    publish.add_argument("--allow-unregistered-path", action="store_true")

    accept = commands.add_parser("accept", help="Accept or reject the current artifact outputs.")
    accept.add_argument("path")
    accept.add_argument("--artifact-id", required=True)
    accept.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    accept.add_argument("--note", default="")

    review = commands.add_parser("review", help="Record a lightweight verdict for an accepted artifact.")
    review.add_argument("path")
    review.add_argument("--artifact-id", required=True)
    review.add_argument(
        "--verdict",
        choices=("approve", "approve_with_notes", "revise", "provisional"),
        required=True,
    )
    review.add_argument("--reviewer", default="")
    review.add_argument("--note", default="")

    authority = commands.add_parser(
        "set-authority", help="Write an accepted creator decision into project authority."
    )
    authority.add_argument("path")
    authority.add_argument(
        "--field",
        required=True,
        help=(
            f"JSON pointer under /{AUTHORITY_ROOT_TOKEN}/ "
            f"or one of {', '.join(FORMAT_POINTERS)}."
        ),
    )
    authority.add_argument(
        "--decision-ref",
        required=True,
        dest="decision_ref",
        help="Bind 创作者决策/<file>.jsonl#<decision-id>.",
    )

    package = commands.add_parser("package", help="Package approved text/JSON artifacts.")
    package.add_argument("path")
    package.add_argument("--episode", required=True)
    package.add_argument("--include", action="append", required=True, dest="includes")
    package.add_argument(
        "--omit", action="append", default=[], dest="omissions",
        help="Record PATH or PATH=REASON as deliberately omitted.",
    )

    verify = commands.add_parser("verify", help="Re-check a delivered package checksums.")
    verify.add_argument("path")
    verify.add_argument("--episode", required=True)

    export = commands.add_parser(
        "export",
        help="Copy the current creator documents and produced media for handover.",
    )
    export.add_argument("path")
    export.add_argument("--out", required=True, type=Path)
    export.add_argument(
        "--episode",
        action="append",
        dest="episodes",
        help="Export only this episode; repeat for several. Default: every episode.",
    )
    export.add_argument(
        "--no-media",
        action="store_true",
        help=f"Skip 剧集/<EP>/{EXPORT_MEDIA_DIRECTORY}/ and export text only.",
    )
    export.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing export directory at --out.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_project(
                Path(args.path),
                title=args.title,
                language=args.language,
                prompt_language=args.prompt_language,
                aspect_ratio=args.aspect_ratio,
                episode_count=args.episode_count,
                target_seconds_per_episode=args.target_seconds_per_episode,
            )
        elif args.command == "status":
            result = project_status(Path(args.path))
        elif args.command == "publish":
            root = find_project(Path(args.path))
            result = publish_candidate(
                root,
                owner=args.owner,
                artifact_id=args.artifact_id,
                outputs=_parse_output_bindings(root, args.outputs),
                inputs=args.inputs,
                allow_unregistered_path=args.allow_unregistered_path,
            )
        elif args.command == "accept":
            result = record_creator_acceptance(
                Path(args.path),
                artifact_id=args.artifact_id,
                decision=args.decision,
                note=args.note,
            )
        elif args.command == "review":
            result = record_review(
                Path(args.path),
                artifact_id=args.artifact_id,
                verdict=args.verdict,
                reviewer=args.reviewer,
                note=args.note,
            )
        elif args.command == "set-authority":
            decision_path, decision_id = _parse_decision_ref(args.decision_ref)
            result = set_creator_authority(
                Path(args.path),
                field=args.field,
                decision_path=decision_path,
                decision_id=decision_id,
            )
        elif args.command == "package":
            result = build_delivery_package(
                Path(args.path),
                episode=args.episode,
                includes=args.includes,
                omissions=_parse_omissions(args.omissions),
            )
        elif args.command == "export":
            result = build_creator_export(
                Path(args.path),
                out=args.out,
                episodes=args.episodes,
                include_media=not args.no_media,
                overwrite=args.overwrite,
            )
        else:
            result = verify_delivery_package(Path(args.path), episode=args.episode)
            if result["status"] != "verified":
                print(json.dumps(result, ensure_ascii=True, sort_keys=True))
                return 1
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (KeyError, OSError, ValueError, ProjectConflictError, PackageBlockedError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
