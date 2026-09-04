#!/usr/bin/env python3
"""Prepare and execute explicitly confirmed short-drama media jobs.

A configured adapter receives one bounded JSON job on stdin and returns local
output files on stdout. The adapter is launched without a shell and only after
a confirmation bound to the exact job and current project inputs. Optional
provider adapters can ship with this skill, but credentials and adapter config
remain outside creator projects.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "short-drama-produce needs Python {}.{} or newer".format(*MINIMUM_PYTHON)
    )

PROJECT_FILE = "short-drama.json"
PRODUCTION_ROOT = Path(".short-drama/production")
JOB_SCHEMA = "1.0"
JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
MAX_JOB_BYTES = 256 * 1024
MAX_RUN_RECORD_BYTES = 256 * 1024
MAX_ADAPTER_RESPONSE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_TOTAL_INPUT_BYTES = 200 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 3600
PUBLIC_ERROR_CATEGORIES = {
    "authentication",
    "configuration",
    "contract",
    "invalid_request",
    "network",
    "permission",
    "provider_response",
    "rate_limit",
    "server",
    "timeout",
}
PUBLIC_ERROR_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
ALLOWED_JOB_KEYS = {
    "schema_version",
    "job_id",
    "modality",
    "adapter",
    "prompt",
    "source",
    "source_entry",
    "references",
    "reference_bindings",
    "outputs",
    "parameters",
    "overwrite",
}
STORED_EXECUTION_KEYS = ALLOWED_JOB_KEYS | {"inputs"}
STORED_JOB_KEYS = STORED_EXECUTION_KEYS | {"fingerprint", "prepared_at"}
LEGACY_STORED_EXECUTION_KEYS = STORED_EXECUTION_KEYS - {
    "source_entry",
    "reference_bindings",
}
LEGACY_STORED_JOB_KEYS = LEGACY_STORED_EXECUTION_KEYS | {
    "fingerprint",
    "prepared_at",
}
SECRET_KEYS = {
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "access_token",
    "api_key",
    "apikey",
}
MEDIA_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".mov", ".webm"},
    "tts": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".opus"},
    "music": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".opus"},
}
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
}
CREATOR_SOURCE_NAMES = {
    "图片提示词.md": "image",
    "视频提示词.md": "video",
    # A shot's frozen keyframe is a producible image and lives nowhere else, so
    # without this the start frame a video job wants to bind has no stage that
    # can render it.
    "分镜.md": "image",
}
# Which H2 IDs a creator source may select, and where that entry keeps its
# copyable body.
CREATOR_SOURCE_ENTRIES = {
    "图片提示词.md": ("IMG-", r"可复制(?:通用)?提示词", "参考"),
    "视频提示词.md": ("MOTION-", r"可复制(?:通用)?提示词", "输入参考图"),
    "分镜.md": ("SHOT-", r"冻结关键帧提示词", "输入参考图"),
}

REF_SLOT_RE = re.compile(r"REF-[A-Z0-9][A-Z0-9-]{0,79}")
SOURCE_ENTRY_RE = re.compile(r"[A-Z][A-Z0-9-]{1,99}")
REFERENCE_SUFFIX_RE = r"(?:png|jpe?g|webp)"
# 用途 is the creator-facing question "what does this picture decide here"; the
# job's own `role` is its production translation. The segment is optional so a
# declaration written before that field existed still prepares, and so the
# creator-side diagnostic stays with creator_markdown_check.py.
REFERENCE_LINE_RE = re.compile(
    rf"(REF-[A-Z0-9][A-Z0-9-]{{0,79}})（顺序：([1-9]\d*)）· "
    rf"([^；\n]+?\.{REFERENCE_SUFFIX_RE})《([^》\n]+)》"
    r"（(?:用途：[^；）\n]+；)?控制：([^；）\n]+)；不得控制：([^）\n]+)）",
    re.IGNORECASE,
)


class ConfirmationRequiredError(RuntimeError):
    """The exact current job has not been explicitly confirmed."""


class AdapterError(RuntimeError):
    """A configured media adapter failed or broke its output contract."""

    def __init__(
        self, message: str, *, public_error: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.public_error = dict(public_error) if public_error is not None else None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def find_project(start: Path) -> Path:
    candidate = start.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / PROJECT_FILE).is_file():
            return directory
    raise FileNotFoundError(f"no {PROJECT_FILE} found from {start}")


def _relative_path(value: object, *, output: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("project paths must be strings")
    raw = value.replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"unsafe project-relative path: {value}")
    if pure.parts[0].casefold() == ".short-drama" or pure.name.casefold() == PROJECT_FILE:
        raise ValueError(f"operational project path is not allowed: {value}")
    if output:
        parts = pure.parts
        top_level_production = len(parts) >= 2 and parts[0].casefold() == "production"
        episode_production = (
            len(parts) >= 4
            and parts[0] in {"剧集", "episodes"}
            and re.fullmatch(r"EP\d{3,}", parts[1], re.IGNORECASE) is not None
            and parts[2] in {"制作成果", "production"}
        )
        if not top_level_production and not episode_production:
            raise ValueError(
                "media outputs must use top-level production/ or "
                "剧集|episodes/<EP>/制作成果|production/"
            )
    return pure.as_posix()


def _project_file(root: Path, relative: str, *, create_parent: bool = False) -> Path:
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts[:-1]:
        current /= part
        if current.exists() and (current.is_symlink() or not current.is_dir()):
            raise ValueError(f"unsafe project directory: {part}")
        if create_parent and not current.exists():
            current.mkdir()
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise ValueError(f"unsafe project file: {relative}")
    if not target.parent.resolve().is_relative_to(root):
        raise ValueError(f"path escapes project root: {relative}")
    return target


def _is_link_or_reparse(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


@contextlib.contextmanager
def _open_project_input(root: Path, relative: str) -> Iterator[BinaryIO]:
    """Open one regular project input without following path components on POSIX."""
    parts = PurePosixPath(relative).parts
    if not parts:
        raise ValueError("job input path is empty")
    if os.name != "nt" and os.open in os.supports_dir_fd:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(root, directory_flags)
        file_fd: int | None = None
        try:
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    directory_flags | nofollow,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(
                parts[-1], os.O_RDONLY | nofollow, dir_fd=directory_fd
            )
            details = os.fstat(file_fd)
            if not stat.S_ISREG(details.st_mode):
                raise ValueError(f"job input is not a regular file: {relative}")
            with os.fdopen(file_fd, "rb", closefd=True) as handle:
                file_fd = None
                yield handle
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"job input is missing: {relative}") from exc
        except OSError as exc:
            raise ValueError(f"unsafe job input path: {relative}") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(directory_fd)
        return

    # Windows lacks portable openat/O_NOFOLLOW support. Reject reparse/symlink
    # components, pin the final file handle, and verify its identity before use.
    path = root
    for part in parts:
        path /= part
        try:
            details = path.lstat()
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"job input is missing: {relative}") from exc
        if _is_link_or_reparse(details):
            raise ValueError(f"unsafe job input path: {relative}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"job input escapes project root: {relative}")
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"job input is not a regular file: {relative}")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"job input changed while opening: {relative}")
        yield handle


def _hash_project_file(root: Path, relative: str) -> str:
    digest = hashlib.sha256()
    size = 0
    with _open_project_input(root, relative) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            if size > MAX_INPUT_BYTES:
                raise ValueError(f"job input exceeds the size limit: {relative}")
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_inputs(
    root: Path, job: Mapping[str, Any], snapshot_root: Path
) -> None:
    inputs = job.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ConfirmationRequiredError("stored job inputs are invalid")
    total = 0
    for relative_value, expected_value in inputs.items():
        relative = _relative_path(relative_value)
        if not isinstance(expected_value, str) or re.fullmatch(
            r"[0-9a-f]{64}", expected_value
        ) is None:
            raise ConfirmationRequiredError("stored job input hash is invalid")
        target = snapshot_root.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with _open_project_input(root, relative) as incoming, target.open(
                "xb"
            ) as outgoing:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_INPUT_BYTES or total > MAX_TOTAL_INPUT_BYTES:
                        raise ConfirmationRequiredError(
                            "job inputs exceed the production size limit"
                        )
                    digest.update(chunk)
                    outgoing.write(chunk)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ConfirmationRequiredError(
                "job inputs changed; prepare and confirm again"
            ) from exc
        if digest.hexdigest() != expected_value:
            raise ConfirmationRequiredError(
                "job inputs changed; prepare and confirm again"
            )


def _canonical(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _check_metadata_parts(parts: tuple[str, ...]) -> None:
    if any(
        not part
        or part in {".", ".."}
        or "/" in part
        or "\\" in part
        for part in parts
    ):
        raise ValueError("production metadata path is invalid")


@contextlib.contextmanager
def _metadata_directory(
    root: Path, parts: tuple[str, ...], *, create: bool
) -> Iterator[tuple[Path, int | None]]:
    """Pin one metadata directory without following project-controlled parents."""
    root = root.resolve()
    _check_metadata_parts(parts)
    all_parts = (".short-drama", "production", *parts)
    directory = root.joinpath(*all_parts)
    if os.name != "nt" and os.open in os.supports_dir_fd:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(root, flags)
        try:
            try:
                for part in all_parts:
                    try:
                        next_fd = os.open(
                            part, flags | nofollow, dir_fd=directory_fd
                        )
                    except FileNotFoundError:
                        if not create:
                            raise
                        try:
                            os.mkdir(part, mode=0o700, dir_fd=directory_fd)
                        except FileExistsError:
                            pass
                        next_fd = os.open(
                            part, flags | nofollow, dir_fd=directory_fd
                        )
                    details = os.fstat(next_fd)
                    if not stat.S_ISDIR(details.st_mode):
                        os.close(next_fd)
                        raise ValueError("production metadata directory is unsafe")
                    os.close(directory_fd)
                    directory_fd = next_fd
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise ValueError("production metadata directory is unsafe") from exc
            yield directory, directory_fd
        finally:
            os.close(directory_fd)
        return

    current = root
    for part in all_parts:
        current /= part
        try:
            details = current.lstat()
        except FileNotFoundError:
            if not create:
                raise
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            details = current.lstat()
        if _is_link_or_reparse(details) or not stat.S_ISDIR(details.st_mode):
            raise ValueError("production metadata directory is unsafe")
    if not current.resolve().is_relative_to(root):
        raise ValueError("production metadata directory escapes the project")
    yield current, None


def _metadata_atomic_json(
    root: Path, directory_parts: tuple[str, ...], name: str, document: Mapping[str, Any]
) -> None:
    _check_metadata_parts((name,))
    content = _canonical(document) + b"\n"
    temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
    with _metadata_directory(root, directory_parts, create=True) as (
        directory,
        directory_fd,
    ):
        if directory_fd is not None:
            descriptor = -1
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                with os.fdopen(descriptor, "wb", closefd=True) as handle:
                    descriptor = -1
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(
                    temporary_name,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
                os.fsync(directory_fd)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            return

        before = directory.stat(follow_symlinks=False)
        temporary = directory / temporary_name
        try:
            with temporary.open("xb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise ValueError("production metadata file is unsafe")
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            after = directory.lstat()
            if (
                _is_link_or_reparse(after)
                or not stat.S_ISDIR(after.st_mode)
                or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise ValueError("production metadata directory changed")
            os.replace(temporary, directory / name)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _metadata_read_json(
    root: Path, directory_parts: tuple[str, ...], name: str, *, maximum: int
) -> object:
    _check_metadata_parts((name,))
    with _metadata_directory(root, directory_parts, create=False) as (
        directory,
        directory_fd,
    ):
        if directory_fd is not None:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                details = os.fstat(handle.fileno())
                if not stat.S_ISREG(details.st_mode) or details.st_size > maximum:
                    raise ValueError("production metadata file is unsafe")
                raw = handle.read(maximum + 1)
        else:
            path = directory / name
            before = path.lstat()
            if (
                _is_link_or_reparse(before)
                or not stat.S_ISREG(before.st_mode)
                or before.st_size > maximum
            ):
                raise ValueError("production metadata file is unsafe")
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError("production metadata file changed while opening")
                raw = handle.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError("production metadata file is too large")
        return json.loads(raw.decode("utf-8"))


def _metadata_unlink(
    root: Path, directory_parts: tuple[str, ...], name: str
) -> None:
    _check_metadata_parts((name,))
    with _metadata_directory(root, directory_parts, create=False) as (
        directory,
        directory_fd,
    ):
        if directory_fd is not None:
            os.unlink(name, dir_fd=directory_fd)
        else:
            path = directory / name
            details = path.lstat()
            if _is_link_or_reparse(details) or not stat.S_ISREG(details.st_mode):
                raise ValueError("production metadata file is unsafe")
            path.unlink()


def _metadata_json_names(root: Path, directory_parts: tuple[str, ...]) -> list[str]:
    try:
        with _metadata_directory(root, directory_parts, create=False) as (
            directory,
            directory_fd,
        ):
            names = os.listdir(directory_fd if directory_fd is not None else directory)
    except FileNotFoundError:
        return []
    return sorted(
        name
        for name in names
        if isinstance(name, str)
        and name.endswith(".json")
        and "/" not in name
        and "\\" not in name
    )


@contextlib.contextmanager
def _project_lock(root: Path) -> Iterator[None]:
    with _metadata_directory(root, (), create=True) as (directory, directory_fd):
        if directory_fd is not None:
            descriptor = os.open(
                "lock",
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                os.close(descriptor)
                raise ValueError("production lock is unsafe")
            handle_context = os.fdopen(descriptor, "a+b", closefd=True)
        else:
            lock_path = directory / "lock"
            before: os.stat_result | None
            try:
                before = lock_path.lstat()
            except FileNotFoundError:
                before = None
            else:
                if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode):
                    raise ValueError("production lock is unsafe")
            handle_context = lock_path.open("a+b")
            opened = os.fstat(handle_context.fileno())
            after = lock_path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or _is_link_or_reparse(after)
                or not stat.S_ISREG(after.st_mode)
                or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
                or (
                    before is not None
                    and (before.st_dev, before.st_ino)
                    != (opened.st_dev, opened.st_ino)
                )
            ):
                handle_context.close()
                raise ValueError("production lock changed while opening")
        with handle_context as handle:
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


def _job_key(job_id: str) -> str:
    return sha256_bytes(job_id.encode("utf-8"))[:24]


def _job_path(root: Path, job_id: str) -> Path:
    return root / PRODUCTION_ROOT / "jobs" / f"{_job_key(job_id)}.json"


def _confirmation_path(root: Path, job_id: str) -> Path:
    return root / PRODUCTION_ROOT / "confirmations" / f"{_job_key(job_id)}.json"


def _run_directory(root: Path, job_id: str) -> Path:
    return root / PRODUCTION_ROOT / "runs" / _job_key(job_id)


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in SECRET_KEYS or _contains_secret_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_key(child) for child in value)
    return False


def _string_list(value: object, *, label: str, limit: int = 32) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    if len(value) > limit:
        raise ValueError(f"{label} has too many entries")
    return list(value)


def _scope_list(value: object, *, label: str) -> list[str]:
    items = _string_list(value, label=label, limit=32)
    if not items or any(not item.strip() or len(item) > 200 for item in items):
        raise ValueError(f"{label} must contain non-empty bounded text")
    normalized = [item.strip() for item in items]
    folded = [item.casefold() for item in normalized]
    if len(folded) != len(set(folded)):
        raise ValueError(f"{label} must not contain duplicates")
    return normalized


def _canonical_creator_source_modality(source: str | None) -> str | None:
    if source is None:
        return None
    path = PurePosixPath(source)
    if (
        len(path.parts) != 3
        or path.parts[0] not in {"剧集", "episodes"}
        or not path.parts[1]
    ):
        return None
    return CREATOR_SOURCE_NAMES.get(path.name)


def _normalize_reference_bindings(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("reference_bindings must be a list of at most sixteen entries")
    normalized: list[dict[str, Any]] = []
    for index, binding in enumerate(value, 1):
        label = f"reference_bindings[{index}]"
        if not isinstance(binding, Mapping) or set(binding) != {
            "slot_id",
            "order",
            "path",
            "label",
            "role",
            "may_control",
            "must_not_control",
        }:
            raise ValueError(f"{label} fields are invalid")
        slot_id = binding.get("slot_id")
        order = binding.get("order")
        raw_label = binding.get("label")
        role = binding.get("role")
        if not isinstance(slot_id, str) or REF_SLOT_RE.fullmatch(slot_id) is None:
            raise ValueError(f"{label} slot_id is invalid")
        if not isinstance(order, int) or isinstance(order, bool) or order < 1:
            raise ValueError(f"{label} order is invalid")
        if (
            not isinstance(raw_label, str)
            or not raw_label.strip()
            or len(raw_label) > 200
            or re.search(r"[\u3400-\u9fff]", raw_label) is None
        ):
            raise ValueError(f"{label} label must contain Chinese text")
        if not isinstance(role, str) or not role.strip() or len(role) > 80:
            raise ValueError(f"{label} role is invalid")
        may_control = _scope_list(
            binding.get("may_control"), label=f"{label}.may_control"
        )
        must_not_control = _scope_list(
            binding.get("must_not_control"), label=f"{label}.must_not_control"
        )
        if {item.casefold() for item in may_control} & {
            item.casefold() for item in must_not_control
        }:
            raise ValueError(f"{label} control scopes must not overlap")
        normalized.append(
            {
                "slot_id": slot_id,
                "order": order,
                "path": _relative_path(binding.get("path")),
                "label": raw_label.strip(),
                "role": role.strip(),
                "may_control": may_control,
                "must_not_control": must_not_control,
            }
        )
    normalized.sort(key=lambda item: int(item["order"]))
    slots = [str(item["slot_id"]) for item in normalized]
    orders = [int(item["order"]) for item in normalized]
    paths = [str(item["path"]) for item in normalized]
    if len(slots) != len(set(slots)):
        raise ValueError("reference binding slot_ids must be unique")
    if orders != list(range(1, len(orders) + 1)):
        raise ValueError("reference binding order must be unique and contiguous from 1")
    if len(paths) != len(set(paths)):
        raise ValueError("reference binding paths must be unique")
    return normalized


def _markdown_section(document: str, source_entry: str) -> str:
    heading = re.compile(
        rf"^##\s+`?{re.escape(source_entry)}`?(?:\s+·.*)?\s*$", re.MULTILINE
    )
    matches = list(heading.finditer(document))
    if not matches:
        raise ValueError(f"source entry is missing from Markdown: {source_entry}")
    if len(matches) != 1:
        raise ValueError(f"source entry is duplicated in Markdown: {source_entry}")
    match = matches[0]
    next_heading = re.search(r"^##\s+", document[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading is not None else len(document)
    return document[match.start() : end]


def _copyable_prompt(
    section: str, heading: str = r"可复制(?:通用)?提示词"
) -> str:
    markers = list(
        re.finditer(rf"^###\s+{heading}\s*$", section, re.MULTILINE)
    )
    if not markers:
        raise ValueError("source entry has no copyable prompt")
    if len(markers) != 1:
        raise ValueError("source entry has duplicate copyable prompts")
    marker = markers[0]
    body = section[marker.end() :]
    following = re.search(r"^###\s+|^##\s+", body, re.MULTILINE)
    if following is not None:
        body = body[: following.start()]
    body_lines = [line for line in body.splitlines() if line.strip()]
    if any(not line.startswith(">") for line in body_lines):
        raise ValueError("source entry copyable prompt contains unquoted content")
    lines = [line[1:].lstrip() for line in body_lines]
    if not lines or not "\n".join(lines).strip():
        raise ValueError("source entry copyable prompt is empty")
    return "\n".join(lines).strip()


def _scope_items(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[、,，]", value) if item.strip()]


def _contains_ref_token(value: str) -> bool:
    return "ref-" in value.casefold()


def _contains_plan_token(value: str) -> bool:
    return "plan-" in value.casefold()


def _markdown_reference_bindings(
    section: str, *, field_name: str
) -> list[dict[str, Any]]:
    lines = re.findall(
        rf"^- {re.escape(field_name)}：(.+)$", section, re.MULTILINE
    )
    if not lines:
        raise ValueError(f"source entry has no {field_name} declaration")
    if len(lines) != 1:
        raise ValueError(f"source entry has duplicate {field_name} declarations")
    value = lines[0].strip()
    if (
        field_name == "输入参考图"
        and not _contains_ref_token(value)
        and re.fullmatch(r"无(?:（[^）\n]+）)?。?", value)
    ):
        return []
    if (
        field_name == "参考"
        and not _contains_ref_token(value)
        and re.fullmatch(r"无(?:外部参考)?(?:；[^\n]*)?。?", value)
    ):
        return []
    # A `PLAN-...` slot is a picture the creator attaches in their own tool, so
    # this suite has no file to send. Saying that plainly beats a generic syntax
    # complaint about a declaration that is correctly written for its purpose.
    if _contains_plan_token(value):
        raise ValueError(
            "source entry declares creator-supplied references (PLAN-...); "
            "bind the real files as REF-... before producing"
        )
    matches = list(REFERENCE_LINE_RE.finditer(value))
    if not matches:
        raise ValueError("source entry input-reference declaration is invalid")
    cursor = 0
    for index, match in enumerate(matches):
        separator = value[cursor : match.start()]
        if separator != ("" if index == 0 else "；"):
            raise ValueError("source entry input-reference declaration is invalid")
        cursor = match.end()
    if value[cursor:] not in {"", "。"}:
        raise ValueError("source entry input-reference declaration is invalid")
    return [
        {
            "slot_id": match.group(1),
            "order": int(match.group(2)),
            "path": _relative_path(match.group(3)),
            "label": match.group(4).strip(),
            "may_control": _scope_items(match.group(5)),
            "must_not_control": _scope_items(match.group(6)),
        }
        for match in matches
    ]


def _verify_markdown_source(
    root: Path,
    *,
    source: str,
    source_entry: str,
    prompt: str,
    bindings: list[dict[str, Any]],
) -> None:
    source_path = _project_file(root, source)
    if source_path.suffix.casefold() != ".md":
        raise ValueError("source_entry requires a Markdown source")
    document = source_path.read_text(encoding="utf-8")
    section = _markdown_section(document, source_entry)
    _, heading, field_name = CREATOR_SOURCE_ENTRIES[source_path.name]
    if _copyable_prompt(section, heading) != prompt.strip():
        raise ValueError("job prompt does not match the selected source entry")
    declared = _markdown_reference_bindings(section, field_name=field_name)
    comparable = [
        {key: binding[key] for key in (
            "slot_id",
            "order",
            "path",
            "label",
            "may_control",
            "must_not_control",
        )}
        for binding in bindings
    ]
    if declared != comparable:
        raise ValueError("job reference bindings do not match the selected source entry")


def _normalize_job(root: Path, raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) - ALLOWED_JOB_KEYS:
        raise ValueError("job contains unsupported fields")
    if raw.get("schema_version", JOB_SCHEMA) != JOB_SCHEMA:
        raise ValueError("unsupported job schema")
    job_id = raw.get("job_id")
    if not isinstance(job_id, str) or JOB_ID_RE.fullmatch(job_id) is None:
        raise ValueError("job_id must be a portable 1-80 character identifier")
    modality = raw.get("modality")
    if modality not in MEDIA_EXTENSIONS:
        raise ValueError("modality must be image, video, tts, or music")
    adapter = raw.get("adapter")
    if not isinstance(adapter, str) or JOB_ID_RE.fullmatch(adapter) is None:
        raise ValueError("adapter must be a portable profile name")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
        raise ValueError("prompt must be non-empty and at most 100000 characters")
    source_raw = raw.get("source")
    source = _relative_path(source_raw) if source_raw is not None else None
    source_entry = raw.get("source_entry")
    if source_entry is not None and (
        not isinstance(source_entry, str)
        or SOURCE_ENTRY_RE.fullmatch(source_entry) is None
    ):
        raise ValueError("source_entry must be a visible uppercase Markdown entry ID")
    if source_entry is not None and source is None:
        raise ValueError("source_entry requires source")
    creator_source_modality = _canonical_creator_source_modality(source)
    if creator_source_modality == modality and source_entry is None:
        raise ValueError("creator Markdown source requires source_entry")
    if creator_source_modality is not None and modality != creator_source_modality and not (
        creator_source_modality == "video"
        and modality == "music"
        and source_entry is None
    ):
        raise ValueError("creator Markdown source does not match the job modality")
    expected_entry_prefix = (
        CREATOR_SOURCE_ENTRIES[PurePosixPath(source).name][0]
        if source is not None
        and PurePosixPath(source).name in CREATOR_SOURCE_ENTRIES
        and creator_source_modality == modality
        else None
    )
    if source_entry is not None and creator_source_modality != modality:
        raise ValueError("source_entry requires the canonical creator Markdown path")
    if source_entry is not None and (
        expected_entry_prefix is None
        or not source_entry.startswith(expected_entry_prefix)
    ):
        raise ValueError("source_entry does not match the job modality")
    references_supplied = "references" in raw
    supplied_references = [
        _relative_path(path)
        for path in _string_list(raw.get("references", []), label="references", limit=16)
    ]
    reference_bindings = _normalize_reference_bindings(raw.get("reference_bindings"))
    binding_references = [str(binding["path"]) for binding in reference_bindings]
    if (
        references_supplied
        and (reference_bindings or source_entry is not None)
        and supplied_references != binding_references
    ):
        raise ValueError("references must match reference_bindings order")
    references = binding_references if reference_bindings else supplied_references
    if source_entry is not None:
        _verify_markdown_source(
            root,
            source=str(source),
            source_entry=source_entry,
            prompt=prompt,
            bindings=reference_bindings,
        )
    outputs = [
        _relative_path(path, output=True)
        for path in _string_list(raw.get("outputs"), label="outputs", limit=16)
    ]
    if not outputs or len(set(outputs)) != len(outputs):
        raise ValueError("outputs must contain unique target paths")
    for output_path in outputs:
        if PurePosixPath(output_path).suffix.casefold() not in MEDIA_EXTENSIONS[str(modality)]:
            raise ValueError(f"output extension does not match {modality}: {output_path}")
        _project_file(root, output_path)
    parameters = raw.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters must be an object")
    if _contains_secret_key(parameters):
        raise ValueError("job parameters must not contain credentials or secrets")
    if len(_canonical(parameters)) > 64 * 1024:
        raise ValueError("job parameters are too large")
    input_paths = ([source] if source is not None else []) + references
    if len(set(input_paths)) != len(input_paths):
        raise ValueError("source and references must be unique")
    input_hashes = {path: _hash_project_file(root, path) for path in input_paths}
    execution = {
        "schema_version": JOB_SCHEMA,
        "job_id": job_id,
        "modality": modality,
        "adapter": adapter,
        "prompt": prompt,
        "source": source,
        "source_entry": source_entry,
        "references": references,
        "reference_bindings": reference_bindings,
        "outputs": outputs,
        "parameters": dict(parameters),
        "overwrite": raw.get("overwrite", False),
        "inputs": input_hashes,
    }
    if not isinstance(execution["overwrite"], bool):
        raise ValueError("overwrite must be a boolean")
    execution["fingerprint"] = sha256_bytes(_canonical(execution))
    execution["prepared_at"] = utc_now()
    return execution


def prepare_job(root: Path, job_file: Path) -> dict[str, Any]:
    root = find_project(root)
    if job_file.stat().st_size > MAX_JOB_BYTES:
        raise ValueError("job file is too large")
    raw = json.loads(job_file.read_text(encoding="utf-8"))
    job = _normalize_job(root, raw)
    with _project_lock(root):
        if _active_run(root, str(job["job_id"])) is not None:
            raise RuntimeError("this job is already running")
        job_name = f"{_job_key(str(job['job_id']))}.json"
        _metadata_atomic_json(root, ("jobs",), job_name, job)
        try:
            _metadata_unlink(root, ("confirmations",), job_name)
        except FileNotFoundError:
            pass
    return _preview(job)


def _validate_stored_job(
    root: Path, document: object, *, expected_job_id: str | None = None
) -> dict[str, Any]:
    root = root.resolve()
    if not isinstance(document, dict):
        raise ValueError("stored job fields are invalid")
    stored_keys = set(document)
    legacy = stored_keys == LEGACY_STORED_JOB_KEYS
    if not legacy and stored_keys != STORED_JOB_KEYS:
        raise ValueError("stored job fields are invalid")
    original = document
    document = dict(document)
    if legacy:
        document["source_entry"] = None
        document["reference_bindings"] = []
    job_id = document.get("job_id")
    if (
        not isinstance(job_id, str)
        or JOB_ID_RE.fullmatch(job_id) is None
        or (expected_job_id is not None and job_id != expected_job_id)
    ):
        raise ValueError("stored job id is invalid")
    if document.get("schema_version") != JOB_SCHEMA:
        raise ValueError("stored job schema is invalid")
    modality = document.get("modality")
    if modality not in MEDIA_EXTENSIONS:
        raise ValueError("stored job modality is invalid")
    adapter = document.get("adapter")
    if not isinstance(adapter, str) or JOB_ID_RE.fullmatch(adapter) is None:
        raise ValueError("stored job adapter is invalid")
    prompt = document.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
        raise ValueError("stored job prompt is invalid")
    source = document.get("source")
    if source is not None and _relative_path(source) != source:
        raise ValueError("stored job source is invalid")
    source_entry = document.get("source_entry")
    if source_entry is not None and (
        not isinstance(source_entry, str)
        or SOURCE_ENTRY_RE.fullmatch(source_entry) is None
        or source is None
    ):
        raise ValueError("stored job source entry is invalid")
    expected_entry_prefix = {"image": "IMG-", "video": "MOTION-"}.get(
        str(modality)
    )
    if source_entry is not None and (
        expected_entry_prefix is None
        or not source_entry.startswith(expected_entry_prefix)
    ):
        raise ValueError("stored job source entry has the wrong modality")
    creator_source_modality = _canonical_creator_source_modality(
        str(source) if source is not None else None
    )
    if not legacy and creator_source_modality == modality and source_entry is None:
        raise ValueError("stored creator Markdown job has no source entry")
    if creator_source_modality is not None and modality != creator_source_modality and not (
        creator_source_modality == "video"
        and modality == "music"
        and source_entry is None
    ):
        raise ValueError("stored creator Markdown source has the wrong modality")
    if source_entry is not None and creator_source_modality != modality:
        raise ValueError("stored job creator source path is invalid")
    references = _string_list(
        document.get("references"), label="stored references", limit=16
    )
    if any(_relative_path(reference) != reference for reference in references):
        raise ValueError("stored job references are invalid")
    reference_bindings = _normalize_reference_bindings(
        document.get("reference_bindings")
    )
    binding_references = [binding["path"] for binding in reference_bindings]
    if (
        source_entry is not None or reference_bindings
    ) and binding_references != references:
        raise ValueError("stored job references do not match reference bindings")
    outputs = _string_list(document.get("outputs"), label="stored outputs", limit=16)
    if not outputs or len(outputs) != len(set(outputs)):
        raise ValueError("stored job outputs are invalid")
    for output in outputs:
        if _relative_path(output, output=True) != output:
            raise ValueError("stored job output is invalid")
        if PurePosixPath(output).suffix.casefold() not in MEDIA_EXTENSIONS[str(modality)]:
            raise ValueError("stored job output extension is invalid")
        _project_file(root, output)
    parameters = document.get("parameters")
    if (
        not isinstance(parameters, Mapping)
        or _contains_secret_key(parameters)
        or len(_canonical(parameters)) > 64 * 1024
    ):
        raise ValueError("stored job parameters are invalid")
    if not isinstance(document.get("overwrite"), bool):
        raise ValueError("stored job overwrite flag is invalid")
    input_paths = ([source] if source is not None else []) + references
    if len(input_paths) != len(set(input_paths)):
        raise ValueError("stored job inputs are duplicated")
    inputs = document.get("inputs")
    if not isinstance(inputs, Mapping) or set(inputs) != set(input_paths):
        raise ValueError("stored job input hashes are invalid")
    if any(
        not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for digest in inputs.values()
    ):
        raise ValueError("stored job input hash is invalid")
    fingerprint = document.get("fingerprint")
    execution_keys = (
        LEGACY_STORED_EXECUTION_KEYS if legacy else STORED_EXECUTION_KEYS
    )
    execution = {key: original[key] for key in execution_keys}
    if (
        not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        or sha256_bytes(_canonical(execution)) != fingerprint
    ):
        raise ValueError("stored job fingerprint is invalid")
    _parse_run_timestamp(document.get("prepared_at"), label="prepared_at")
    return document


def _read_job(root: Path, job_id: str) -> dict[str, Any]:
    if JOB_ID_RE.fullmatch(job_id) is None:
        raise ValueError("invalid job_id")
    document = _metadata_read_json(
        root,
        ("jobs",),
        f"{_job_key(job_id)}.json",
        maximum=MAX_JOB_BYTES,
    )
    return _validate_stored_job(root, document, expected_job_id=job_id)


def _preview(job: Mapping[str, Any]) -> dict[str, Any]:
    confirmation = f"CONFIRM {job['job_id']} {str(job['fingerprint'])[:12]}"
    return {
        "job_id": job["job_id"],
        "modality": job["modality"],
        "adapter": job["adapter"],
        "count": len(job["outputs"]),
        "prompt": job["prompt"],
        "source": job["source"],
        "source_entry": job["source_entry"],
        "references": job["references"],
        "reference_bindings": job["reference_bindings"],
        "outputs": job["outputs"],
        "parameters": job["parameters"],
        "overwrite": job["overwrite"],
        "confirmation": confirmation,
        "state": "needs_confirmation",
    }


def confirm_job(root: Path, *, job_id: str, confirmation: str) -> dict[str, Any]:
    root = find_project(root)
    with _project_lock(root):
        if _active_run(root, job_id) is not None:
            raise RuntimeError("this job is already running")
        job = _read_job(root, job_id)
        expected = _preview(job)["confirmation"]
        if confirmation != expected:
            raise ConfirmationRequiredError("confirmation does not match the exact current job")
        receipt = {
            "schema_version": JOB_SCHEMA,
            "job_id": job_id,
            "fingerprint": job["fingerprint"],
            "confirmed_at": utc_now(),
            "consumed_at": None,
            "run_id": None,
        }
        _metadata_atomic_json(
            root, ("confirmations",), f"{_job_key(job_id)}.json", receipt
        )
    return {"job_id": job_id, "state": "confirmed"}


def _inputs_current(root: Path, job: Mapping[str, Any]) -> bool:
    inputs = job.get("inputs")
    if not isinstance(inputs, Mapping):
        return False
    try:
        return all(_hash_project_file(root, str(path)) == digest for path, digest in inputs.items())
    except (FileNotFoundError, OSError, ValueError):
        return False


def _load_adapter(config_path: Path, profile: str, root: Path) -> tuple[list[str], int]:
    resolved = config_path.expanduser().resolve()
    if resolved.is_relative_to(root):
        raise ValueError("adapter config must live outside the project")
    document = json.loads(resolved.read_text(encoding="utf-8"))
    adapters = document.get("adapters") if isinstance(document, Mapping) else None
    selected = adapters.get(profile) if isinstance(adapters, Mapping) else None
    if not isinstance(selected, Mapping) or set(selected) - {"command", "timeout_seconds"}:
        raise ValueError(f"adapter profile is missing or invalid: {profile}")
    command = _string_list(selected.get("command"), label="adapter command", limit=32)
    if not command or any(not part for part in command):
        raise ValueError("adapter command must be a non-empty argv list")
    timeout = selected.get("timeout_seconds", 300)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"adapter timeout must be 1-{MAX_TIMEOUT_SECONDS} seconds")
    return command, timeout


def _generic_adapter_error(
    profile: str,
    *,
    category: str,
    code: str,
    retryable: bool,
) -> dict[str, Any]:
    return {
        "provider": profile,
        "category": category,
        "code": code,
        "retryable": retryable,
    }


def _parse_public_adapter_error(
    raw: bytes, *, profile: str, returncode: int
) -> dict[str, Any]:
    fallback = _generic_adapter_error(
        profile,
        category="provider_response",
        code=f"adapter_exit_{returncode}",
        retryable=False,
    )
    if len(raw) > MAX_ADAPTER_RESPONSE_BYTES:
        return fallback
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return fallback
    error = document.get("error") if isinstance(document, Mapping) else None
    if not isinstance(error, Mapping) or set(error) - {
        "provider",
        "category",
        "code",
        "http_status",
        "request_id",
        "retryable",
    }:
        return fallback
    provider = error.get("provider")
    category = error.get("category")
    code = error.get("code")
    retryable = error.get("retryable")
    if (
        provider != profile
        or category not in PUBLIC_ERROR_CATEGORIES
        or not isinstance(code, str)
        or PUBLIC_ERROR_TOKEN_RE.fullmatch(code) is None
        or not isinstance(retryable, bool)
    ):
        return fallback
    result: dict[str, Any] = {
        "provider": provider,
        "category": category,
        "code": code,
        "retryable": retryable,
    }
    status = error.get("http_status")
    if status is not None:
        if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
            return fallback
        result["http_status"] = status
    request_id = error.get("request_id")
    if request_id is not None:
        if (
            not isinstance(request_id, str)
            or PUBLIC_ERROR_TOKEN_RE.fullmatch(request_id) is None
        ):
            return fallback
        result["request_id"] = request_id
    return result


def _run_adapter(command: list[str], timeout: int, payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            completed = subprocess.run(
                command,
                input=_canonical(payload),
                stdout=stdout,
                stderr=stderr,
                cwd=root,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(
                "adapter timed out; confirmation was consumed",
                public_error=_generic_adapter_error(
                    str(payload["adapter"]),
                    category="timeout",
                    code="adapter_timeout",
                    retryable=True,
                ),
            ) from exc
        except OSError as exc:
            raise AdapterError(
                "adapter could not be started; confirmation was consumed",
                public_error=_generic_adapter_error(
                    str(payload["adapter"]),
                    category="configuration",
                    code="adapter_start_failed",
                    retryable=False,
                ),
            ) from exc
        if completed.returncode != 0:
            size = stdout.tell()
            stdout.seek(0)
            raw_error = stdout.read(MAX_ADAPTER_RESPONSE_BYTES + 1)
            public_error = _parse_public_adapter_error(
                raw_error if size <= MAX_ADAPTER_RESPONSE_BYTES else b"",
                profile=str(payload["adapter"]),
                returncode=completed.returncode,
            )
            raise AdapterError(
                f"adapter exited with code {completed.returncode}; confirmation was consumed",
                public_error=public_error,
            )
        size = stdout.tell()
        if size > MAX_ADAPTER_RESPONSE_BYTES:
            raise AdapterError("adapter response is too large; confirmation was consumed")
        stdout.seek(0)
        try:
            response = json.loads(stdout.read().decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterError("adapter returned invalid JSON; confirmation was consumed") from exc
    if not isinstance(response, dict):
        raise AdapterError("adapter response must be an object; confirmation was consumed")
    return response


def _validate_adapter_outputs(
    job: Mapping[str, Any], response: Mapping[str, Any], output_root: Path
) -> list[tuple[str, Path]]:
    entries = response.get("outputs")
    if not isinstance(entries, list) or not all(isinstance(entry, Mapping) for entry in entries):
        raise AdapterError("adapter outputs are invalid; confirmation was consumed")
    result: list[tuple[str, Path]] = []
    for entry in entries:
        if set(entry) != {"target", "source"}:
            raise AdapterError("adapter output fields are invalid; confirmation was consumed")
        target = entry.get("target")
        source = entry.get("source")
        if not isinstance(target, str) or not isinstance(source, str):
            raise AdapterError("adapter output paths are invalid; confirmation was consumed")
        path = Path(source)
        if not path.is_absolute() or path.parent != output_root or path.name in {"", ".", ".."}:
            raise AdapterError(
                "adapter output must use the run staging directory; confirmation was consumed"
            )
        result.append((target, path))
    expected = list(job["outputs"])
    if [target for target, _ in result] != expected:
        raise AdapterError("adapter outputs do not match the confirmed targets; confirmation was consumed")
    return result


def _copy_output(
    source: Path, target: Path, *, overwrite: bool
) -> tuple[str, int]:
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    descriptor = -1
    try:
        try:
            before = source.lstat()
        except OSError as exc:
            raise AdapterError(
                "adapter output file is missing; confirmation was consumed"
            ) from exc
        if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode):
            raise AdapterError("adapter output file is unsafe; confirmation was consumed")
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise AdapterError("adapter output changed while opening; confirmation was consumed")
        if opened.st_size > MAX_OUTPUT_BYTES:
            raise AdapterError("adapter output file is too large; confirmation was consumed")
        with os.fdopen(descriptor, "rb", closefd=True) as incoming, temporary.open(
            "xb"
        ) as outgoing:
            descriptor = -1
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_OUTPUT_BYTES:
                    raise AdapterError("adapter output exceeded the size limit")
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if overwrite:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError as exc:
                raise FileExistsError(
                    f"output appeared while production was running: {target.name}"
                ) from exc
            temporary.unlink()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return digest.hexdigest(), size


def _latest_run(root: Path, job_id: str) -> dict[str, Any] | None:
    history = _read_run_history(root, job_id)
    return history[-1] if history else None


def _parse_run_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"production run {label} is invalid")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"production run {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"production run {label} is invalid")
    return parsed.astimezone(timezone.utc)


def _completed_run_order(run: Mapping[str, Any]) -> tuple[datetime, str]:
    return (
        _parse_run_timestamp(run.get("finished_at"), label="finished_at"),
        str(run["run_id"]),
    )


def _read_run_history(root: Path, job_id: str) -> list[dict[str, Any]]:
    directory_parts = ("runs", _job_key(job_id))
    history: list[dict[str, Any]] = []
    for name in _metadata_json_names(root, directory_parts):
        document = _metadata_read_json(
            root, directory_parts, name, maximum=MAX_RUN_RECORD_BYTES
        )
        if (
            not isinstance(document, dict)
            or document.get("job_id") != job_id
            or not isinstance(document.get("run_id"), str)
            or document.get("status") not in {"running", "succeeded", "failed"}
            or not isinstance(document.get("fingerprint"), str)
            or re.fullmatch(r"[0-9a-f]{64}", str(document.get("fingerprint"))) is None
        ):
            raise ValueError("production run record is invalid")
        started_at = _parse_run_timestamp(
            document.get("started_at"), label="started_at"
        )
        finished_at = document.get("finished_at")
        if document["status"] == "running":
            if finished_at is not None:
                raise ValueError("running production run has finished_at")
        else:
            completed_at = _parse_run_timestamp(finished_at, label="finished_at")
            if completed_at < started_at:
                raise ValueError("production run finished before it started")
        history.append(document)
    # Completed runs are ordered by completion because that is when their
    # terminal state and output claim become authoritative. Any unresolved
    # running attempt sorts last so status cannot hide it behind a later start.
    history.sort(
        key=lambda run: (
            run["status"] == "running",
            (
                _parse_run_timestamp(run.get("started_at"), label="started_at")
                if run["status"] == "running"
                else _completed_run_order(run)[0]
            ),
            str(run["run_id"]),
        )
    )
    return history


def _active_run(root: Path, job_id: str) -> dict[str, Any] | None:
    running = [
        run for run in _read_run_history(root, job_id) if run["status"] == "running"
    ]
    return running[-1] if running else None


def _write_run(root: Path, job_id: str, run: Mapping[str, Any]) -> None:
    _metadata_atomic_json(
        root,
        ("runs", _job_key(job_id)),
        f"{run['run_id']}.json",
        run,
    )


def run_job(root: Path, *, job_id: str, adapter_config: Path) -> dict[str, Any]:
    root = find_project(root)
    with tempfile.TemporaryDirectory(prefix="short-drama-inputs-") as directory:
        attempt_root = Path(directory)
        snapshot_root = attempt_root / "inputs"
        output_root = attempt_root / "outputs"
        snapshot_root.mkdir()
        output_root.mkdir()
        with _project_lock(root):
            if _active_run(root, job_id) is not None:
                raise RuntimeError("this job is already running")
            job = _read_job(root, job_id)
            command, timeout = _load_adapter(adapter_config, str(job["adapter"]), root)
            try:
                receipt = _metadata_read_json(
                    root,
                    ("confirmations",),
                    f"{_job_key(job_id)}.json",
                    maximum=MAX_RUN_RECORD_BYTES,
                )
            except FileNotFoundError as exc:
                raise ConfirmationRequiredError("job needs explicit confirmation") from exc
            if (
                not isinstance(receipt, dict)
                or receipt.get("fingerprint") != job.get("fingerprint")
                or receipt.get("consumed_at") is not None
            ):
                raise ConfirmationRequiredError("job needs a new explicit confirmation")
            for output in job["outputs"]:
                target = _project_file(root, output)
                if target.exists() and not job["overwrite"]:
                    raise FileExistsError(f"output exists and overwrite is false: {output}")
            # Pin every confirmed input into a private immutable snapshot before
            # consuming confirmation. Provider adapters never reopen live project paths.
            _snapshot_inputs(root, job, snapshot_root)
            run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
            receipt["consumed_at"] = utc_now()
            receipt["run_id"] = run_id
            _metadata_atomic_json(
                root,
                ("confirmations",),
                f"{_job_key(job_id)}.json",
                receipt,
            )
            run = {
                "schema_version": JOB_SCHEMA,
                "run_id": run_id,
                "job_id": job_id,
                "fingerprint": job["fingerprint"],
                "modality": job["modality"],
                "adapter": job["adapter"],
                "status": "running",
                "started_at": utc_now(),
                "finished_at": None,
                "outputs": [],
            }
            _write_run(root, job_id, run)

        payload = {key: job[key] for key in ALLOWED_JOB_KEYS if key in job}
        payload.update(
            {
                "run_id": run_id,
                "project_root": str(snapshot_root),
                "output_root": str(output_root),
            }
        )
        try:
            response = _run_adapter(command, timeout, payload, root)
            adapter_outputs = _validate_adapter_outputs(job, response, output_root)
            written: list[dict[str, Any]] = []
            with _project_lock(root):
                for target_name, source in adapter_outputs:
                    target = _project_file(root, target_name, create_parent=True)
                    digest, size = _copy_output(
                        source, target, overwrite=bool(job["overwrite"])
                    )
                    written.append(
                        {
                            "path": target_name,
                            "media_type": MEDIA_TYPES[PurePosixPath(target_name).suffix.casefold()],
                            "bytes": size,
                            "sha256": digest,
                        }
                    )
                run["status"] = "succeeded"
                run["finished_at"] = utc_now()
                run["outputs"] = written
                provider_job_id = response.get("provider_job_id")
                if isinstance(provider_job_id, str) and len(provider_job_id) <= 200:
                    run["provider_job_id"] = provider_job_id
                _write_run(root, job_id, run)
        except Exception as exc:
            with _project_lock(root):
                run["status"] = "failed"
                run["finished_at"] = utc_now()
                if isinstance(exc, AdapterError) and exc.public_error is not None:
                    run["error"] = exc.public_error
                _write_run(root, job_id, run)
            raise
    return {
        "job_id": job_id,
        "run_id": run_id,
        "state": "succeeded",
        "outputs": run["outputs"],
    }


def job_status(root: Path, *, job_id: str) -> dict[str, Any]:
    root = find_project(root)
    job = _read_job(root, job_id)
    latest = _latest_run(root, job_id)
    if not _inputs_current(root, job):
        state = "needs_reconfirmation"
    else:
        try:
            receipt = _metadata_read_json(
                root,
                ("confirmations",),
                f"{_job_key(job_id)}.json",
                maximum=MAX_RUN_RECORD_BYTES,
            )
        except FileNotFoundError:
            receipt = None
        if latest and latest.get("status") == "running":
            state = "running"
        elif latest and latest.get("status") in {"succeeded", "failed"}:
            state = str(latest["status"])
        elif isinstance(receipt, Mapping) and receipt.get("consumed_at") is None:
            state = "confirmed"
        else:
            state = "needs_confirmation"
    return {
        "job_id": job_id,
        "modality": job["modality"],
        "adapter": job["adapter"],
        "outputs": job["outputs"],
        "state": state,
        "latest_run": latest,
    }


def _hash_production_output(root: Path, relative: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with _open_project_input(root, relative) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            if size > MAX_OUTPUT_BYTES:
                raise ValueError("production output exceeds the size limit")
            digest.update(chunk)
    return digest.hexdigest(), size


def _validated_succeeded_outputs(
    job: Mapping[str, Any], run: Mapping[str, Any]
) -> list[tuple[str, str, int]]:
    expected = job.get("outputs")
    entries = run.get("outputs")
    if (
        not isinstance(expected, list)
        or not isinstance(entries, list)
        or len(entries) != len(expected)
    ):
        raise ValueError("succeeded outputs do not match the current job")
    validated: list[tuple[str, str, int]] = []
    # ``zip(strict=True)`` needs 3.10; the suite's floor is 3.9, so the length
    # agreement is asserted directly rather than by the zip flag.
    if len(expected) != len(entries):
        raise ValueError("expected %d outputs, got %d" % (len(expected), len(entries)))
    for expected_path, output in zip(expected, entries):
        if not isinstance(output, Mapping) or set(output) != {
            "path",
            "media_type",
            "bytes",
            "sha256",
        }:
            raise ValueError("succeeded output record fields are invalid")
        output_path = output.get("path")
        digest = output.get("sha256")
        size = output.get("bytes")
        media_type = output.get("media_type")
        if (
            not isinstance(expected_path, str)
            or output_path != expected_path
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or media_type
            != MEDIA_TYPES.get(PurePosixPath(expected_path).suffix.casefold())
        ):
            raise ValueError("succeeded output record is invalid")
        validated.append((output_path, digest, size))
    return validated


def audit_project(root: Path) -> dict[str, Any]:
    """Reconcile local attempt history and current output bytes, not media quality."""
    root = find_project(root)
    jobs: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for name in _metadata_json_names(root, ("jobs",)):
        try:
            document = _metadata_read_json(
                root, ("jobs",), name, maximum=MAX_JOB_BYTES
            )
            job = _validate_stored_job(root, document)
            job_id = job["job_id"]
            if not isinstance(job_id, str) or name != f"{_job_key(job_id)}.json":
                raise ValueError("stored job is invalid")
            jobs.append(job)
        except (OSError, ValueError, json.JSONDecodeError):
            problems.append(
                {
                    "code": "invalid_job_record",
                    "record": name,
                    "action": "repair_production_metadata",
                }
            )

    attempts_total = 0
    attempts_succeeded = 0
    attempts_failed = 0
    attempts_running = 0
    attempts_superseded = 0
    repeated_content = 0
    recovered_jobs = 0
    running_jobs = 0
    terminal_failed_jobs = 0
    retryable_terminal_failed_jobs = 0
    current_output_claims: dict[
        str, tuple[tuple[datetime, str], str, int, str, str]
    ] = {}

    for job in jobs:
        job_id = str(job["job_id"])
        try:
            history = _read_run_history(root, job_id)
        except (OSError, ValueError, json.JSONDecodeError):
            problems.append(
                {
                    "code": "invalid_run_history",
                    "job_id": job_id,
                    "action": "repair_production_metadata",
                }
            )
            continue
        attempts_total += len(history)
        completed = [run for run in history if run["status"] != "running"]
        running = [run for run in history if run["status"] == "running"]
        current = [
            run for run in history if run["fingerprint"] == job["fingerprint"]
        ]
        current_completed = [run for run in current if run["status"] != "running"]
        current_running = [run for run in current if run["status"] == "running"]
        attempts_succeeded += sum(run["status"] == "succeeded" for run in completed)
        attempts_failed += sum(run["status"] == "failed" for run in completed)
        attempts_running += len(running)
        attempts_superseded += len(history) - len(current)
        if running:
            running_jobs += 1
            for run in running:
                problems.append(
                    {
                        "code": "running_attempt",
                        "job_id": job_id,
                        "run_id": str(run["run_id"]),
                        "action": "wait_or_investigate_running_attempt",
                    }
                )
        fingerprints = [
            str(run.get("fingerprint"))
            for run in history
            if isinstance(run.get("fingerprint"), str)
        ]
        repeated_content += len(fingerprints) - len(set(fingerprints))
        succeeded_indexes = [
            index
            for index, run in enumerate(current_completed)
            if run["status"] == "succeeded"
        ]
        failed_indexes = [
            index
            for index, run in enumerate(current_completed)
            if run["status"] == "failed"
        ]
        if succeeded_indexes and failed_indexes and min(failed_indexes) < max(succeeded_indexes):
            recovered_jobs += 1
        if (
            not current_running
            and current_completed
            and current_completed[-1]["status"] == "failed"
        ):
            terminal_failed_jobs += 1
            error = current_completed[-1].get("error")
            retryable = isinstance(error, Mapping) and error.get("retryable") is True
            if retryable:
                retryable_terminal_failed_jobs += 1
            problems.append(
                {
                    "code": (
                        "terminal_retryable_failure" if retryable else "terminal_failure"
                    ),
                    "job_id": job_id,
                    "run_id": current_completed[-1]["run_id"],
                    "action": (
                        "inspect_then_reconfirm_retry" if retryable else "inspect_failure"
                    ),
                }
            )
        for run in current:
            if run["status"] != "succeeded":
                continue
            try:
                outputs = _validated_succeeded_outputs(job, run)
            except ValueError:
                problems.append(
                    {
                        "code": "invalid_succeeded_output_record",
                        "job_id": job_id,
                        "run_id": str(run["run_id"]),
                        "action": "repair_production_metadata",
                    }
                )
                continue
            for output_path, digest, size in outputs:
                run_id = str(run["run_id"])
                claim_order = _completed_run_order(run)
                claim = current_output_claims.get(output_path)
                if claim is None or claim_order > claim[0]:
                    current_output_claims[output_path] = (
                        claim_order,
                        digest,
                        size,
                        job_id,
                        run_id,
                    )

        if not _inputs_current(root, job):
            problems.append(
                {
                    "code": "job_inputs_changed",
                    "job_id": job_id,
                    "action": "prepare_and_confirm_again",
                }
            )

    output_verified = 0
    output_missing = 0
    output_modified = 0
    for relative, (
        _claim_order,
        expected_digest,
        expected_size,
        job_id,
        run_id,
    ) in sorted(current_output_claims.items()):
        try:
            safe_relative = _relative_path(relative, output=True)
            digest, size = _hash_production_output(root, safe_relative)
        except (FileNotFoundError, OSError, ValueError):
            output_missing += 1
            problems.append(
                {
                    "code": "output_missing_or_unsafe",
                    "job_id": job_id,
                    "run_id": run_id,
                    "path": relative,
                    "action": "restore_or_reproduce_output",
                }
            )
            continue
        if digest != expected_digest or size != expected_size:
            output_modified += 1
            problems.append(
                {
                    "code": "output_digest_mismatch",
                    "job_id": job_id,
                    "run_id": run_id,
                    "path": relative,
                    "action": "review_current_bytes_or_reproduce_output",
                }
            )
        else:
            output_verified += 1

    return {
        "status": "attention" if problems else "pass",
        "scope": "operational_evidence_only",
        "quality_verdict": "not_assessed",
        "jobs": {
            "total": len(jobs),
            "running": running_jobs,
            "recovered": recovered_jobs,
            "terminal_failed": terminal_failed_jobs,
            "retryable_terminal_failed": retryable_terminal_failed_jobs,
        },
        "attempts": {
            "total": attempts_total,
            "succeeded": attempts_succeeded,
            "failed": attempts_failed,
            "running": attempts_running,
            "superseded": attempts_superseded,
            "repeated_content": repeated_content,
        },
        "outputs": {
            "claimed_current": len(current_output_claims),
            "verified": output_verified,
            "missing": output_missing,
            "modified": output_modified,
        },
        "problems": problems,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run confirmed short-drama media jobs.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Validate and preview a media job.")
    prepare.add_argument("project")
    prepare.add_argument("--job", required=True)
    confirm = commands.add_parser("confirm", help="Confirm the exact prepared job.")
    confirm.add_argument("project")
    confirm.add_argument("--job-id", required=True)
    confirm.add_argument("--confirmation", required=True)
    run = commands.add_parser("run", help="Execute a confirmed job through an adapter.")
    run.add_argument("project")
    run.add_argument("--job-id", required=True)
    run.add_argument("--adapter-config", required=True)
    status = commands.add_parser("status", help="Show one media job state.")
    status.add_argument("project")
    status.add_argument("--job-id", required=True)
    audit = commands.add_parser(
        "audit", help="Reconcile attempt history and current output bytes."
    )
    audit.add_argument("project")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_job(Path(args.project), Path(args.job))
        elif args.command == "confirm":
            result = confirm_job(
                Path(args.project), job_id=args.job_id, confirmation=args.confirmation
            )
        elif args.command == "run":
            result = run_job(
                Path(args.project),
                job_id=args.job_id,
                adapter_config=Path(args.adapter_config),
            )
        elif args.command == "status":
            result = job_status(Path(args.project), job_id=args.job_id)
        else:
            result = audit_project(Path(args.project))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
