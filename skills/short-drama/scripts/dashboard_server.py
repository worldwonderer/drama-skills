#!/usr/bin/env python3
"""A loopback-only HTTP dashboard for short-drama project text files."""

from __future__ import annotations

import argparse
import contextlib
import errno
import hmac
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import Iterator, Mapping
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, BinaryIO, Union
from urllib.parse import parse_qs, unquote, urlencode, urlsplit


# Creators run these scripts on whatever interpreter their machine provides, so
# an unsupported version must say so instead of failing inside an import.
MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "short-drama needs Python {}.{} or newer; this interpreter is {}.{}".format(
            *MINIMUM_PYTHON, sys.version_info.major, sys.version_info.minor
        )
    )

TEXT_EXTENSIONS = frozenset({".md", ".json", ".jsonl", ".txt", ".srt", ".ass"})
MEDIA_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".mp4",
        ".webm",
        ".mov",
        ".wav",
        ".mp3",
        ".m4a",
        ".aac",
        ".flac",
        ".opus",
    }
)
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
}
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_NODES = 2_000
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_MEDIA_BYTES = 256 * 1024 * 1024
MAX_JSON_EXPANSION = 6
REQUEST_OVERHEAD_BYTES = 64 * 1024
SKILL_ROOT = Path(__file__).resolve().parents[1]
SESSION_SCHEMA = "1.0"
SESSION_RELATIVE = PurePosixPath(".short-drama/dashboard.json")
SESSION_LOCK_SUFFIX = ".lock"
DETACH_TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 10.0
STATIC_ROOT = SKILL_ROOT / "assets/dashboard"

# Windows opens files in text mode unless told otherwise, which would rewrite
# every newline and break the SHA-256 versions the editor round-trips on.
BINARY = getattr(os, "O_BINARY", 0)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
# POSIX pins directories by descriptor, which makes traversal free of races by
# construction. Everywhere else the same guarantee is approximated by checking
# each component and re-checking identity; see ``_PathDirectory``.
SECURE_DIR_FD = (
    os.name != "nt"
    and bool(getattr(os, "O_DIRECTORY", 0))
    and bool(NOFOLLOW)
    and os.open in os.supports_dir_fd
)
# Windows refuses to replace a file another process still holds open, and an
# editor or a search indexer is enough to hold one for a moment.
REPLACE_ATTEMPTS = 6


class DashboardError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_project_tool(root: Path) -> ModuleType:
    """Dynamically load the suite's canonical project status implementation."""
    root = root.resolve()
    installed_script = root / "scripts/project_tool.py"
    repository_script = root / "skills/short-drama/scripts/project_tool.py"
    script = installed_script if installed_script.is_file() else repository_script
    spec = importlib.util.spec_from_file_location("dashboard_project_tool", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load project tool: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_structured_text(path: PurePosixPath, content: str) -> None:
    suffix = path.suffix.casefold()
    if suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            raise DashboardError(
                HTTPStatus.BAD_REQUEST,
                f"JSON is invalid at line {exc.lineno}, column {exc.colno}",
            ) from exc
        return
    if suffix != ".jsonl":
        return
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            raise DashboardError(
                HTTPStatus.BAD_REQUEST,
                f"JSONL line {line_number} is invalid at column {exc.colno}",
            ) from exc


def _is_link_or_reparse(details: os.stat_result) -> bool:
    """Say whether a stat result describes something that must not be traversed.

    ``S_ISLNK`` alone is not enough on Windows: junctions and every other
    reparse point redirect just as well, and need no privilege to create.
    """

    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _entry_is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        if os.name != "nt":
            return False
        return _is_link_or_reparse(entry.stat(follow_symlinks=False))
    except OSError:
        return True


def _identity(details: os.stat_result) -> tuple[int, int]:
    return details.st_dev, details.st_ino


def _same_object(before: tuple[int, int], after: tuple[int, int]) -> bool:
    # A volume that reports no inode gives nothing to compare, so treat the
    # check as inapplicable there instead of failing every read on it.
    if not before[1] or not after[1]:
        return True
    return before == after


class _DescriptorDirectory:
    """A directory pinned by descriptor, the POSIX backend.

    Every operation names a file relative to a descriptor the request already
    holds, so the pin is the directory itself rather than a name that resolves
    to one, and nothing in between can be swapped mid-request.
    """

    __slots__ = ("_fd",)

    contract = (
        "project_status_at",
        "is_protected_project_text",
        "coordinated_project_text_edit_at",
        "project_path_lifecycle_at",
    )

    def __init__(self, descriptor: int) -> None:
        self._fd = descriptor

    @classmethod
    def open_root(cls, path: Path) -> _DescriptorDirectory:
        return cls(os.open(path, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW))

    def reopen(self) -> _DescriptorDirectory:
        # A directory cursor belongs to the open file description, so dup()
        # would still let concurrent requests advance one another's scan.
        # Opening "." gives every caller an independent description.
        return _DescriptorDirectory(
            os.open(".", os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=self._fd)
        )

    def child(self, name: str) -> _DescriptorDirectory:
        return _DescriptorDirectory(
            os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=self._fd
            )
        )

    @contextlib.contextmanager
    def scandir(self) -> Iterator[Iterator[os.DirEntry]]:
        with os.scandir(self._fd) as entries:
            yield entries

    def open_regular(self, name: str, flags: int, mode: int = 0o666) -> int:
        return os.open(name, flags | NOFOLLOW | BINARY, mode, dir_fd=self._fd)

    def replace(self, source: str, target: str) -> None:
        os.replace(source, target, src_dir_fd=self._fd, dst_dir_fd=self._fd)

    def unlink(self, name: str) -> None:
        os.unlink(name, dir_fd=self._fd)

    def sync(self) -> None:
        os.fsync(self._fd)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def status(self, tool: ModuleType, project_root: str) -> dict[str, Any]:
        return tool.project_status_at(self._fd, project_root=project_root)

    def lifecycle(self, tool: ModuleType, relative: str) -> dict[str, str] | None:
        return tool.project_path_lifecycle_at(self._fd, relative)

    def coordinated_edit(
        self, tool: ModuleType, relative: str, expected_version: str
    ) -> Any:
        return tool.coordinated_project_text_edit_at(
            self._fd, relative, expected_version
        )


class _PathDirectory:
    """A directory pinned by verified path, for platforms without ``openat``.

    Windows cannot name a file relative to an open directory, so the guarantee
    is rebuilt from two checks: every component is inspected with ``os.lstat``,
    which never follows, and refused if it is a link or any other reparse
    point; then the pinned directory's identity is re-checked before each
    operation and an opened file's identity against what was inspected.

    This is weaker than a descriptor -- a swap timed between the two checks is
    not caught. It is the boundary this suite already accepts for Windows
    ``verify``, and the adversary it admits, a local process running as the
    creator, can open these files directly anyway.
    """

    __slots__ = ("_path", "_identity")

    contract = (
        "project_status_from_root",
        "is_protected_project_text",
        "coordinated_project_text_edit",
        "project_path_lifecycle",
    )

    def __init__(self, path: Path, identity: tuple[int, int]) -> None:
        self._path = path
        self._identity = identity

    @classmethod
    def open_root(cls, path: Path) -> _PathDirectory:
        details = os.lstat(path)
        if _is_link_or_reparse(details) or not stat.S_ISDIR(details.st_mode):
            raise OSError(
                errno.ENOTDIR, "not a plain directory", str(path)
            )
        return cls(path, _identity(details))

    def _pinned(self) -> Path:
        details = os.lstat(self._path)
        if _is_link_or_reparse(details) or not stat.S_ISDIR(details.st_mode):
            raise OSError(
                errno.ENOTDIR, "pinned directory is no longer a directory",
                str(self._path),
            )
        if not _same_object(self._identity, _identity(details)):
            raise OSError(
                errno.ENOENT, "pinned directory was replaced", str(self._path)
            )
        return self._path

    def reopen(self) -> _PathDirectory:
        return _PathDirectory(self._pinned(), self._identity)

    def child(self, name: str) -> _PathDirectory:
        return _PathDirectory.open_root(self._pinned() / name)

    @contextlib.contextmanager
    def scandir(self) -> Iterator[Iterator[os.DirEntry]]:
        with os.scandir(self._pinned()) as entries:
            yield entries

    def open_regular(self, name: str, flags: int, mode: int = 0o666) -> int:
        target = self._pinned() / name
        expected: tuple[int, int] | None = None
        if not flags & os.O_CREAT:
            # O_CREAT|O_EXCL already refuses an existing name, so only an open
            # of something that must already be there needs inspecting first.
            details = os.lstat(target)
            if _is_link_or_reparse(details) or not stat.S_ISREG(details.st_mode):
                raise OSError(errno.EPERM, "not a plain file", str(target))
            expected = _identity(details)
        descriptor = os.open(target, flags | BINARY, mode)
        if expected is None:
            return descriptor
        try:
            if not _same_object(expected, _identity(os.fstat(descriptor))):
                raise OSError(
                    errno.ENOENT, "file was replaced while opening", str(target)
                )
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def replace(self, source: str, target: str) -> None:
        root = self._pinned()
        delay = 0.01
        for remaining in range(REPLACE_ATTEMPTS - 1, -1, -1):
            try:
                os.replace(root / source, root / target)
                return
            except PermissionError:
                if not remaining:
                    raise
                time.sleep(delay)
                delay *= 2

    def unlink(self, name: str) -> None:
        os.unlink(self._pinned() / name)

    def sync(self) -> None:
        # Windows has no directory handle to flush; the file itself was already
        # fsynced before the replace, which is the same guarantee the CLI makes.
        return None

    def close(self) -> None:
        return None

    def status(self, tool: ModuleType, project_root: str) -> dict[str, Any]:
        return tool.project_status_from_root(
            self._pinned(), project_root=project_root
        )

    def lifecycle(self, tool: ModuleType, relative: str) -> dict[str, str] | None:
        return tool.project_path_lifecycle(self._pinned(), relative)

    def coordinated_edit(
        self, tool: ModuleType, relative: str, expected_version: str
    ) -> Any:
        return tool.coordinated_project_text_edit(
            self._pinned(), relative, expected_version
        )


Directory = Union[_DescriptorDirectory, _PathDirectory]


def directory_backend() -> type[_DescriptorDirectory] | type[_PathDirectory]:
    return _DescriptorDirectory if SECURE_DIR_FD else _PathDirectory


@contextlib.contextmanager
def _open_parent_directory(
    root: Directory, relative: PurePosixPath
) -> Iterator[tuple[Directory, str]]:
    current = root.reopen()
    try:
        for part in relative.parts[:-1]:
            child = current.child(part)
            current.close()
            current = child
        yield current, relative.name
    finally:
        current.close()


class ProjectStore:
    def __init__(
        self,
        workspace: Path,
        project_tool: ModuleType,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_media_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
    ) -> None:
        workspace = workspace.expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise NotADirectoryError(workspace)
        self.workspace = workspace
        self.project_tool = project_tool
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_file_bytes = max_file_bytes
        self.max_media_bytes = max_media_bytes
        # JSON can encode one content byte as a six-byte ``\u00xx`` escape.
        # Size the transport envelope from the configured file limit so a
        # valid text file is never rejected only because of JSON escaping.
        self.max_request_bytes = (
            max_file_bytes * MAX_JSON_EXPANSION + REQUEST_OVERHEAD_BYTES
        )
        # Which four entry points this server invokes depends on how the
        # platform can pin a directory: by descriptor, or by verified path.
        self.backend = directory_backend()
        missing = [
            name
            for name in self.backend.contract
            if not callable(getattr(project_tool, name, None))
        ]
        if missing:
            raise RuntimeError(
                "project tool does not support the Dashboard contract: "
                + ", ".join(missing)
            )
        self._workspace = self.backend.open_root(workspace)
        self._write_lock = threading.Lock()

    def close(self) -> None:
        self._workspace.close()

    @staticmethod
    def _project_id(relative: str) -> str:
        return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]

    def discover(self) -> tuple[list[dict[str, Any]], list[str]]:
        projects: list[dict[str, Any]] = []
        warnings: list[str] = []
        nodes = 0
        depth_truncated = False
        node_truncated = False

        def walk(directory: Directory, parts: tuple[str, ...], depth: int) -> None:
            nonlocal depth_truncated, node_truncated, nodes
            if nodes >= self.max_nodes:
                return
            try:
                with directory.scandir() as iterator:
                    entries = sorted(iterator, key=lambda item: item.name.casefold())
            except OSError as exc:
                warnings.append(f"无法读取 {'/'.join(parts) or '.'}: {exc}")
                return
            for entry in entries:
                if nodes >= self.max_nodes:
                    node_truncated = True
                    return
                nodes += 1
                if _entry_is_link(entry):
                    continue
                if (
                    entry.is_file(follow_symlinks=False)
                    and entry.name == "short-drama.json"
                ):
                    relative = "/".join(parts) or "."
                    title = "未命名短剧"
                    try:
                        raw, _mode = self._read_regular(directory, entry.name)
                        manifest = json.loads(raw.decode("utf-8"))
                        candidate = manifest.get("title") if isinstance(manifest, dict) else None
                        if isinstance(candidate, str) and candidate.strip():
                            title = " ".join(candidate.split())[:200]
                    except (DashboardError, OSError, UnicodeError, json.JSONDecodeError):
                        pass
                    projects.append(
                        {
                            "id": self._project_id(relative),
                            "path": relative,
                            "title": title,
                        }
                    )
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if depth >= self.max_depth:
                    depth_truncated = True
                    continue
                try:
                    child = directory.child(entry.name)
                except OSError:
                    continue
                try:
                    walk(child, (*parts, entry.name), depth + 1)
                finally:
                    child.close()

        # Reopen rather than share: on POSIX a directory cursor belongs to the
        # open file description, so two concurrent discoveries walking one
        # handle would advance each other's scan.
        discovery = self._workspace.reopen()
        try:
            walk(discovery, (), 0)
        finally:
            discovery.close()
        if node_truncated:
            warnings.append(f"项目发现达到节点上限 {self.max_nodes}，结果已截断")
        if depth_truncated:
            warnings.append(f"项目发现达到深度上限 {self.max_depth}，更深目录已截断")
        return projects, warnings

    @contextlib.contextmanager
    def _pinned_project(self, project_id: str) -> Iterator[tuple[Directory, Path]]:
        """Pin the project root and report its creator-facing path.

        Every parent is opened from the pinned workspace with the backend's
        no-follow guarantee, so a directory swapped for a symlink mid-request
        fails instead of redirecting the operation. The yielded path is for
        display only — no caller reads through it.
        """

        selected = next(
            (item for item in self.discover()[0] if item["id"] == project_id), None
        )
        if selected is None:
            raise DashboardError(HTTPStatus.NOT_FOUND, "project not found")
        display = (
            self.workspace
            if selected["path"] == "."
            else self.workspace / selected["path"]
        )
        # Reopen instead of sharing the workspace handle: on POSIX that handle
        # carries a seek position other requests would advance, including when
        # the workspace itself is the selected project.
        directory = self._workspace.reopen()
        marker = -1
        try:
            for part in PurePosixPath(selected["path"]).parts:
                if part == ".":
                    continue
                child = directory.child(part)
                directory.close()
                directory = child
            marker = directory.open_regular("short-drama.json", os.O_RDONLY)
            if not stat.S_ISREG(os.fstat(marker).st_mode):
                raise OSError("project manifest is not a regular file")
        except OSError as exc:
            if marker >= 0:
                os.close(marker)
            directory.close()
            raise DashboardError(
                HTTPStatus.FORBIDDEN, "project root cannot be opened safely"
            ) from exc
        os.close(marker)
        try:
            yield directory, display
        finally:
            directory.close()

    def status(self, project_id: str) -> dict[str, Any]:
        with self._pinned_project(project_id) as (directory, root):
            return directory.status(self.project_tool, str(root))

    @staticmethod
    def _safe_relative(relative: str) -> PurePosixPath:
        raw = unquote(relative).replace("\\", "/")
        pure = PurePosixPath(raw)
        if (
            not raw
            or pure.is_absolute()
            or any(part in ("", ".", "..") for part in pure.parts)
        ):
            raise DashboardError(HTTPStatus.BAD_REQUEST, "unsafe project-relative path")
        return pure

    def _is_protected(self, relative: PurePosixPath) -> bool:
        return bool(
            self.project_tool.is_protected_project_text(relative.as_posix())
        )

    def tree(self, project_id: str) -> dict[str, Any]:
        with self._pinned_project(project_id) as (directory, _root):
            return self._tree_from_root(directory)

    def _tree_from_root(self, directory: Directory) -> dict[str, Any]:
        nodes = 0
        warnings: list[str] = []
        depth_truncated = False
        node_truncated = False
        oversized_text = 0
        oversized_media = 0

        def scan(
            parent: Directory, parts: tuple[str, ...], depth: int
        ) -> list[dict[str, Any]]:
            nonlocal depth_truncated, node_truncated, nodes
            nonlocal oversized_media, oversized_text
            children: list[dict[str, Any]] = []
            try:
                with parent.scandir() as iterator:
                    entries = sorted(
                        iterator,
                        key=lambda item: (
                            not item.is_dir(follow_symlinks=False),
                            item.name.casefold(),
                        ),
                    )
            except OSError as exc:
                warnings.append(f"无法读取 {'/'.join(parts) or '.'}: {exc}")
                return children
            for entry in entries:
                if _entry_is_link(entry):
                    continue
                relative = "/".join((*parts, entry.name))
                if parts == () and entry.name.casefold() == ".short-drama":
                    continue
                if nodes >= self.max_nodes:
                    node_truncated = True
                    break
                nodes += 1
                if entry.is_dir(follow_symlinks=False):
                    node: dict[str, Any] = {
                        "name": entry.name,
                        "path": relative,
                        "type": "directory",
                        "children": [],
                    }
                    if depth < self.max_depth:
                        try:
                            child = parent.child(entry.name)
                        except OSError:
                            continue
                        try:
                            node["children"] = scan(
                                child, (*parts, entry.name), depth + 1
                            )
                        finally:
                            child.close()
                    else:
                        depth_truncated = True
                        node["truncated"] = True
                    children.append(node)
                    continue
                suffix = PurePosixPath(entry.name).suffix.casefold()
                if suffix not in TEXT_EXTENSIONS and suffix not in MEDIA_EXTENSIONS:
                    continue
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                limit = (
                    self.max_file_bytes
                    if suffix in TEXT_EXTENSIONS
                    else self.max_media_bytes
                )
                if size > limit:
                    if suffix in TEXT_EXTENSIONS:
                        oversized_text += 1
                    else:
                        oversized_media += 1
                children.append(
                    {
                        "name": entry.name,
                        "path": relative,
                        "type": "text" if suffix in TEXT_EXTENSIONS else "media",
                        "size": size,
                        "oversize": size > limit,
                        "writable": suffix in TEXT_EXTENSIONS
                        and not self._is_protected(PurePosixPath(relative)),
                    }
                )
            return children

        tree = scan(directory, (), 0)
        if node_truncated:
            warnings.append(f"文件树达到节点上限 {self.max_nodes}，结果已截断")
        if depth_truncated:
            warnings.append(f"文件树达到深度上限 {self.max_depth}，更深目录已截断")
        if oversized_text:
            warnings.append(
                f"{oversized_text} 个文本文件超过大小上限 {self.max_file_bytes} bytes，内容预览已禁用"
            )
        if oversized_media:
            warnings.append(
                f"{oversized_media} 个媒体文件超过大小上限 {self.max_media_bytes} bytes，媒体预览已禁用"
            )
        return {
            "tree": tree,
            "warnings": warnings,
            "limits": {
                "depth": self.max_depth,
                "nodes": self.max_nodes,
                "fileBytes": self.max_file_bytes,
                "mediaBytes": self.max_media_bytes,
            },
        }

    def read_text(self, project_id: str, relative: str) -> dict[str, Any]:
        pure = self._safe_relative(relative)
        if pure.suffix.casefold() not in TEXT_EXTENSIONS:
            raise DashboardError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "file type is not editable text"
            )
        try:
            with self._pinned_project(project_id) as (directory, _root):
                with _open_parent_directory(directory, pure) as (parent, name):
                    data, _ = self._read_regular(parent, name)
        except OSError as exc:
            raise DashboardError(
                HTTPStatus.FORBIDDEN, "text file cannot be opened safely"
            ) from exc
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DashboardError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "text file must be UTF-8"
            ) from exc
        return {
            "path": pure.as_posix(),
            "content": content,
            "version": _version(data),
            "writable": not self._is_protected(pure),
        }

    def _read_regular(self, parent: Directory, name: str) -> tuple[bytes, int]:
        descriptor = parent.open_regular(name, os.O_RDONLY)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise DashboardError(HTTPStatus.BAD_REQUEST, "path is not a file")
            if details.st_size > self.max_file_bytes:
                raise DashboardError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "file exceeds preview limit",
                )
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                data = handle.read(self.max_file_bytes + 1)
            if len(data) > self.max_file_bytes:
                raise DashboardError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "file exceeds preview limit",
                )
            return data, stat.S_IMODE(details.st_mode)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _replace_text(
        self,
        parent: Directory,
        name: str,
        encoded: bytes,
        mode: int,
        expected_version: str,
    ) -> None:
        temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = parent.open_regular(temporary_name, flags, mode)
        replaced = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
                # Windows has no fchmod, and its chmod only moves the read-only
                # bit; the mode the file already carries is the right one there.
                if hasattr(os, "fchmod"):
                    os.fchmod(handle.fileno(), mode)
            latest, _ = self._read_regular(parent, name)
            if _version(latest) != expected_version:
                raise DashboardError(
                    HTTPStatus.CONFLICT, "file changed since it was opened"
                )
            try:
                parent.replace(temporary_name, name)
            except PermissionError as exc:
                # Windows refuses the rename while anything else holds the
                # target open; elsewhere the same errno means the file is
                # write-protected. Neither is an unsafe path, so neither may
                # report as one.
                raise DashboardError(
                    HTTPStatus.CONFLICT, "file is locked or not writable"
                ) from exc
            replaced = True
            parent.sync()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if not replaced:
                # Best effort: the save already failed, and a cleanup that
                # cannot run must not replace that error with its own.
                with contextlib.suppress(OSError):
                    parent.unlink(temporary_name)

    @contextlib.contextmanager
    def _coordinated_edit(
        self, directory: Directory, relative: PurePosixPath, expected_version: str
    ) -> Iterator[None]:
        try:
            with directory.coordinated_edit(
                self.project_tool, relative.as_posix(), expected_version
            ):
                yield
        except Exception as exc:
            if exc.__class__.__name__ == "ProjectConflictError":
                raise DashboardError(HTTPStatus.CONFLICT, str(exc)) from exc
            if isinstance(exc, ValueError):
                raise DashboardError(
                    HTTPStatus.FORBIDDEN, "project path changed during the save"
                ) from exc
            if isinstance(exc, OSError):
                # Both backends refuse to take the project lock through a
                # redirected `.short-drama`. That is a refused path, not the
                # internal error it used to surface as.
                raise DashboardError(
                    HTTPStatus.FORBIDDEN, "text file cannot be replaced safely"
                ) from exc
            raise

    def write_text(
        self, project_id: str, relative: str, content: Any, expected_version: Any
    ) -> dict[str, Any]:
        pure = self._safe_relative(relative)
        if pure.suffix.casefold() not in TEXT_EXTENSIONS:
            raise DashboardError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "file type is not editable text"
            )
        if self._is_protected(pure):
            raise DashboardError(
                HTTPStatus.FORBIDDEN, "file is protected and read-only"
            )
        if not isinstance(content, str) or not isinstance(expected_version, str):
            raise DashboardError(
                HTTPStatus.BAD_REQUEST,
                "content and expectedVersion are required strings",
            )
        if re.fullmatch(r"[0-9a-f]{64}", expected_version) is None:
            raise DashboardError(
                HTTPStatus.BAD_REQUEST, "expectedVersion must be a SHA-256 digest"
            )
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise DashboardError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "content exceeds file limit"
            )
        _validate_structured_text(pure, content)
        with (
            self._write_lock,
            self._pinned_project(project_id) as (directory, _root),
            self._coordinated_edit(directory, pure, expected_version),
        ):
            # The in-process lock and the project's file lock keep the version
            # check and atomic replace together. Pinned parents stop a
            # concurrent symlink swap from redirecting the write.
            try:
                with _open_parent_directory(directory, pure) as (parent, name):
                    current, mode = self._read_regular(parent, name)
                    if _version(current) != expected_version:
                        raise DashboardError(
                            HTTPStatus.CONFLICT,
                            "file changed since it was opened",
                        )
                    self._replace_text(
                        parent, name, encoded, mode, expected_version
                    )
            except OSError as exc:
                raise DashboardError(
                    HTTPStatus.FORBIDDEN, "text file cannot be replaced safely"
                ) from exc
        return {"path": pure.as_posix(), "version": _version(encoded), "saved": True}

    def media_info(self, project_id: str, relative: str) -> dict[str, Any]:
        pure = self._safe_relative(relative)
        with self._pinned_project(project_id) as (directory, _root):
            handle, content_type, size = self._open_media(directory, pure)
            handle.close()
            lifecycle = directory.lifecycle(self.project_tool, pure.as_posix())
        kind = "image"
        if content_type.startswith("video/"):
            kind = "video"
        elif content_type.startswith("audio/"):
            kind = "audio"
        result = {
            "path": pure.as_posix(),
            "kind": kind,
            "contentType": content_type,
            "size": size,
            "readOnly": True,
            "contentUrl": f"/api/media/content?{urlencode({'project': project_id, 'path': pure.as_posix()})}",
            "status": "ready",
        }
        if lifecycle is not None:
            result["lifecycle"] = lifecycle
        return result

    def open_media(
        self, project_id: str, relative: str
    ) -> tuple[BinaryIO, PurePosixPath, str, int]:
        pure = self._safe_relative(relative)
        with self._pinned_project(project_id) as (directory, _root):
            handle, content_type, size = self._open_media(directory, pure)
        return handle, pure, content_type, size

    def _open_media(
        self, directory: Directory, pure: PurePosixPath
    ) -> tuple[BinaryIO, str, int]:
        content_type = MEDIA_TYPES.get(pure.suffix.casefold())
        if content_type is None:
            raise DashboardError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported preview media"
            )
        try:
            with _open_parent_directory(directory, pure) as (parent, name):
                descriptor = parent.open_regular(name, os.O_RDONLY)
        except OSError as exc:
            raise DashboardError(
                HTTPStatus.FORBIDDEN, "media file cannot be opened safely"
            ) from exc
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise DashboardError(HTTPStatus.BAD_REQUEST, "media path is not a file")
            if details.st_size > self.max_media_bytes:
                raise DashboardError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "media exceeds preview limit"
                )
            return os.fdopen(descriptor, "rb"), content_type, details.st_size
        except Exception:
            os.close(descriptor)
            raise


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], store: ProjectStore) -> None:
        self.store = store
        self.workspace_fingerprint = workspace_fingerprint(store.workspace)
        self.access_token = secrets.token_urlsafe(32)
        self.api_prefix = f"/_short_drama/{secrets.token_urlsafe(18)}"
        cookie_suffix = hashlib.sha256(self.api_prefix.encode("utf-8")).hexdigest()[:16]
        self.session_cookie = f"short_drama_{cookie_suffix}"
        super().__init__(server_address, DashboardHandler)

    def allowed_authority(self, authority: str) -> bool:
        parsed = urlsplit(f"//{authority}")
        if (
            not parsed.hostname
            or not _is_loopback(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.path or parsed.query or parsed.fragment)
        ):
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        return port == self.server_address[1]

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.store.close()


class DashboardHandler(SimpleHTTPRequestHandler):
    server: DashboardHTTPServer

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; media-src 'self'",
        )
        super().end_headers()

    def _request_token(self) -> str:
        header = self.headers.get("X-Short-Drama-Token")
        if header:
            return header
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookies.get(self.server.session_cookie)
        return morsel.value if morsel is not None else ""

    def _security_ok(self, *, require_token: bool = False) -> bool:
        host = self.headers.get("Host", "")
        if not host or not self.server.allowed_authority(host):
            self._json(HTTPStatus.FORBIDDEN, {"error": "invalid Host header"})
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "http"
                or not parsed.netloc
                or bool(parsed.path not in {"", "/"} or parsed.query or parsed.fragment)
                or not self.server.allowed_authority(parsed.netloc)
            ):
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid Origin header"})
                return False
        # Compare bytes, not str: `hmac.compare_digest` raises TypeError on a
        # str operand holding any codepoint above U+007F, and http.client
        # decodes header bytes as iso-8859-1, so a single 0x80+ byte in the
        # header would escape this pre-auth check and drop the connection with
        # no response at all.
        if require_token and not hmac.compare_digest(
            self._request_token().encode("utf-8", "surrogateescape"),
            self.server.access_token.encode("utf-8"),
        ):
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "dashboard session required"})
            return False
        return True

    def _json(
        self, status: int, value: Any, *, headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, content in (headers or {}).items():
            self.send_header(name, content)
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path.startswith(f"{self.server.api_prefix}/api/"):
            path = path[len(self.server.api_prefix) :]
        return path, parse_qs(parsed.query, keep_blank_values=True)

    @staticmethod
    def _one(query: dict[str, list[str]], name: str) -> str:
        values = query.get(name)
        if not values or not values[0]:
            raise DashboardError(
                HTTPStatus.BAD_REQUEST, f"missing query parameter: {name}"
            )
        return values[0]

    @staticmethod
    def _byte_range(value: str | None, size: int) -> tuple[int, int] | None:
        if value is None:
            return None
        if not value.startswith("bytes=") or "," in value or size <= 0:
            raise DashboardError(
                HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "invalid byte range"
            )
        bounds = value[6:].strip()
        if "-" not in bounds:
            raise DashboardError(
                HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "invalid byte range"
            )
        first, last = bounds.split("-", 1)
        try:
            if first:
                start = int(first)
                end = int(last) if last else size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
            else:
                suffix = int(last)
                if suffix <= 0:
                    raise ValueError
                start = max(0, size - suffix)
                end = size - 1
        except ValueError as exc:
            raise DashboardError(
                HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "invalid byte range"
            ) from exc
        return start, end

    def _serve_media(self, query: dict[str, list[str]], *, send_body: bool) -> None:
        handle, _, content_type, size = self.server.store.open_media(
            self._one(query, "project"), self._one(query, "path")
        )
        try:
            try:
                selected = self._byte_range(self.headers.get("Range"), size)
            except DashboardError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return
            start, end = selected if selected is not None else (0, size - 1)
            length = 0 if size == 0 else end - start + 1
            self.send_response(
                HTTPStatus.PARTIAL_CONTENT if selected is not None else HTTPStatus.OK
            )
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if selected is not None:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not send_body or length == 0:
                return
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            handle.close()

    def do_GET(self) -> None:
        path, query = self._query()
        if not self._security_ok(require_token=path.startswith("/api/")):
            return
        try:
            if path == "/api/projects":
                projects, warnings = self.server.store.discover()
                self._json(HTTPStatus.OK, {"projects": projects, "warnings": warnings})
                return
            if path == "/api/status":
                self._json(
                    HTTPStatus.OK, self.server.store.status(self._one(query, "project"))
                )
                return
            if path == "/api/tree":
                self._json(
                    HTTPStatus.OK, self.server.store.tree(self._one(query, "project"))
                )
                return
            if path == "/api/file":
                self._json(
                    HTTPStatus.OK,
                    self.server.store.read_text(
                        self._one(query, "project"), self._one(query, "path")
                    ),
                )
                return
            if path == "/api/media":
                info = self.server.store.media_info(
                    self._one(query, "project"), self._one(query, "path")
                )
                info["contentUrl"] = (
                    f"{self.server.api_prefix}{info['contentUrl']}"
                )
                self._json(
                    HTTPStatus.OK,
                    info,
                )
                return
            if path == "/api/media/content":
                self._serve_media(query, send_body=True)
                return
            if path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "API endpoint not found"})
                return
            if path == "/":
                self.path = "/index.html"
            super().do_GET()
        except DashboardError as exc:
            self._json(exc.status, {"error": exc.message})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except Exception:
            # A handler that lets anything escape closes the socket without a
            # status line, so the browser reports a network failure it cannot
            # act on. Every unexpected type still becomes an answered request.
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal dashboard error"}
            )

    def do_HEAD(self) -> None:
        path, query = self._query()
        if not self._security_ok(require_token=path.startswith("/api/")):
            return
        if path == "/api/media/content":
            try:
                self._serve_media(query, send_body=False)
            except DashboardError as exc:
                self._json(exc.status, {"error": exc.message})
            except OSError as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            except Exception:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal dashboard error"},
                )
            return
        if path.startswith("/api/"):
            self._json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "HEAD is not supported for API endpoints"},
            )
            return
        if path == "/":
            self.path = "/index.html"
        super().do_HEAD()

    def do_PUT(self) -> None:
        path, query = self._query()
        if not self._security_ok(require_token=path.startswith("/api/")):
            return
        if path != "/api/file":
            self._json(HTTPStatus.NOT_FOUND, {"error": "API endpoint not found"})
            return
        try:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise DashboardError(
                    HTTPStatus.LENGTH_REQUIRED, "Content-Length is required"
                )
            length = int(raw_length)
            if length < 0 or length > self.server.store.max_request_bytes:
                raise DashboardError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large"
                )
            if self.headers.get_content_type() != "application/json":
                raise DashboardError(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "Content-Type must be application/json",
                )
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise DashboardError(
                    HTTPStatus.BAD_REQUEST, "request body must be an object"
                )
            result = self.server.store.write_text(
                self._one(query, "project"),
                self._one(query, "path"),
                payload.get("content"),
                payload.get("expectedVersion"),
            )
            self._json(HTTPStatus.OK, result)
        except DashboardError as exc:
            self._json(exc.status, {"error": exc.message})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON request"})
        except OSError as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except Exception:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal dashboard error"}
            )

    def do_POST(self) -> None:
        path, _ = self._query()
        if not self._security_ok(require_token=path.startswith("/api/")):
            return
        if path != "/api/session":
            self._json(HTTPStatus.NOT_FOUND, {"error": "API endpoint not found"})
            return
        self._json(
            HTTPStatus.OK,
            {"status": "ready", "apiBase": self.server.api_prefix},
            headers={
                "Set-Cookie": (
                    f"{self.server.session_cookie}={self.server.access_token}; "
                    f"HttpOnly; SameSite=Strict; Path={self.server.api_prefix}/"
                )
            },
        )


def workspace_fingerprint(workspace: Path) -> str:
    """Identify one workspace without exposing its path in a response header."""
    return hashlib.sha256(
        str(workspace).encode("utf-8", "surrogateescape")
    ).hexdigest()[:16]


def session_file_for(workspace: Path, override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    return workspace / Path(str(SESSION_RELATIVE))


def read_session(path: Path) -> dict[str, Any] | None:
    """Return the recorded session, or ``None`` when there is nothing usable."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("schema_version") != SESSION_SCHEMA:
        return None
    required = ("host", "port", "token", "fingerprint", "url", "pid")
    if any(key not in document for key in required):
        return None
    return document


def write_session(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def session_lock_path(session_path: Path) -> Path:
    return session_path.with_name(session_path.name + SESSION_LOCK_SUFFIX)


def _try_exclusive_lock(handle: Any) -> bool:
    """Take the serving lock without waiting. False means someone else holds it."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        # Reached through getattr for the same reason project_tool.py does: the
        # POSIX stubs mypy checks against do not declare these Windows names.
        locking = getattr(msvcrt, "locking")
        non_blocking = getattr(msvcrt, "LK_NBLCK")
        try:
            locking(handle.fileno(), non_blocking, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


class SessionUnavailable(RuntimeError):
    """This location cannot hold a session record (read-only workspace, ...).

    Serving does not depend on the record: it exists so --detach can report a
    URL and --status/--stop can find the server later. A workspace the creator
    can read but not write must still open, the way it did before sessions
    existed.
    """


@contextlib.contextmanager
def hold_session_lock(session_path: Path) -> Iterator[bool]:
    """Hold the serving lock, yielding whether WE hold it (vs. another process).

    Liveness is answered by this lock rather than by probing the recorded port.
    A PID can be reused and a port can be inherited by something unrelated; a
    lock is released by the kernel exactly when the serving process ends. It
    also keeps the dashboard free of any outbound network client.
    """
    lock_path = session_lock_path(session_path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise SessionUnavailable(
            f"cannot use a dashboard session file at {lock_path}: {exc}"
        ) from exc
    if not _try_exclusive_lock(handle):
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        handle.close()


def session_is_live(session_path: Path) -> bool:
    """Answer whether a dashboard is still serving this workspace."""
    try:
        with hold_session_lock(session_path) as held:
            return not held
    except SessionUnavailable:
        return False


def session_matches(session: Mapping[str, Any], workspace: Path) -> bool:
    """Is this record about the workspace the caller asked for?

    A --session-file shared between two workspaces would otherwise hand the
    second creator a link that serves the first one's project.
    """
    return session.get("fingerprint") == workspace_fingerprint(workspace)


def watch_workspace(server: Any, workspace: Path, interval: float = 5.0) -> None:
    """Shut the server down if its workspace stops existing.

    A detached dashboard used to die with its shell. Now that it does not, a
    creator who deletes or moves the project would otherwise leave a server
    holding a port and answering on the old token URL for a tree that is gone --
    and its session record went with the directory, so nothing could stop it.
    """
    try:
        expected = os.stat(workspace)
    except OSError:
        return
    identity = (expected.st_dev, expected.st_ino)
    misses = 0

    def loop() -> None:
        nonlocal misses
        while True:
            time.sleep(interval)
            try:
                current = os.stat(workspace)
                gone = (current.st_dev, current.st_ino) != identity
            except OSError:
                gone = True
            misses = misses + 1 if gone else 0
            if misses >= 2:  # tolerate one transient stat failure
                threading.Thread(target=server.shutdown, daemon=True).start()
                return

    threading.Thread(target=loop, daemon=True).start()


def stop_session(path: Path) -> bool:
    """Stop the recorded dashboard and forget it. Returns whether one was live."""
    session = read_session(path)
    live = session is not None and session_is_live(path)
    if live:
        pid = session.get("pid") if session else None
        if isinstance(pid, int) and pid > 0:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
            while time.monotonic() < deadline and session_is_live(path):
                time.sleep(0.1)
    with contextlib.suppress(OSError):
        path.unlink()
    return live


def _detached_child(
    workspace: Path, *, host: str, port: int, session_path: Path
) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "--workspace",
        str(workspace),
        "--host",
        host,
        "--port",
        str(port),
        "--session-file",
        str(session_path),
    ]
    log_path = session_path.with_name(f"{session_path.stem}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")
    options: dict[str, Any] = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console to inherit and
        # no Ctrl-C from the launching shell.
        options["creationflags"] = 0x00000008 | 0x00000200
    else:
        options["start_new_session"] = True
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            cwd=str(workspace),
            **options,
        )
    finally:
        log.close()


def start_detached(
    workspace: Path, *, host: str, port: int, session_path: Path
) -> dict[str, Any]:
    """Start a dashboard that outlives the shell that asked for it."""
    process = _detached_child(workspace, host=host, port=port, session_path=session_path)
    deadline = time.monotonic() + DETACH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        session = read_session(session_path)
        if session is not None and session.get("pid") == process.pid:
            return session
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if process.poll() is None:
        with contextlib.suppress(OSError):
            process.kill()
    log_path = session_path.with_name(f"{session_path.stem}.log")
    raise RuntimeError(f"dashboard did not start; see {log_path}")


def create_server(
    workspace: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    suite_root: Path | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_media_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
) -> DashboardHTTPServer:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if not _is_loopback(host) or (address is not None and address.version != 4):
        raise ValueError("dashboard may only bind to an IPv4 loopback address")
    suite = (suite_root or SKILL_ROOT).resolve()
    store = ProjectStore(
        workspace,
        load_project_tool(suite),
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_file_bytes=max_file_bytes,
        max_media_bytes=max_media_bytes,
    )
    return DashboardHTTPServer((host, port), store)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the local short-drama project dashboard"
    )
    parser.add_argument(
        "--workspace", required=True, type=Path, help="explicit workspace root to scan"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="loopback address (default: 127.0.0.1)"
    )
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the dashboard in the default browser after binding",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="serve in a background process that outlives this shell",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="stop a dashboard already serving this workspace before starting",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print the recorded dashboard for this workspace and exit",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="stop the dashboard recorded for this workspace and exit",
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        help=f"where to record the running dashboard (default: {SESSION_RELATIVE})",
    )
    args = parser.parse_args(argv)
    if args.status and args.stop:
        parser.error("--status and --stop cannot be combined")

    workspace = args.workspace.expanduser().resolve()
    session_path = session_file_for(workspace, args.session_file)
    if not (args.status or args.stop) and not workspace.is_dir():
        # Creating a mistyped --workspace and serving it as an empty dashboard
        # leaves a long-lived server for a directory that never existed.
        print(f"workspace is not an existing directory: {workspace}", file=sys.stderr)
        return 2

    if args.status:
        session = read_session(session_path)
        holding = session is not None and session_is_live(session_path)
        mine = holding and session is not None and session_matches(session, workspace)
        status: dict[str, Any] = {
            "running": bool(mine),
            "workspace": str(workspace),
            "session_file": str(session_path),
        }
        if holding and session is not None:
            status["url"] = session["url"]
            status["pid"] = session["pid"]
            recorded = session.get("workspace")
            if not mine:
                # The record is live but describes a different workspace.
                status["serves_workspace"] = recorded
        print(json.dumps(status, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if mine else 1

    if args.stop:
        stopped = stop_session(session_path)
        print("Dashboard stopped" if stopped else "No dashboard was running", flush=True)
        return 0

    existing = read_session(session_path)
    if (
        existing is not None
        and session_matches(existing, workspace)
        and session_is_live(session_path)
    ):
        if args.restart:
            stop_session(session_path)
        else:
            url = str(existing["url"])
            print(f"Dashboard: {url}", flush=True)
            print("Reusing the dashboard already serving this workspace", flush=True)
            if args.open:
                webbrowser.open(url)
            return 0

    if args.detach:
        try:
            session = start_detached(
                workspace, host=args.host, port=args.port, session_path=session_path
            )
        except SessionUnavailable as exc:
            print(f"{exc}", file=sys.stderr)
            return 2
        except RuntimeError as exc:
            # Losing the race to another start lands here: the winner took the
            # serving lock and this child exited without recording anything.
            # A dashboard IS serving this workspace, so report its URL.
            winner = read_session(session_path)
            live = winner is not None and session_is_live(session_path)
            if live and winner is not None and session_matches(winner, workspace):
                url = str(winner["url"])
                print(f"Dashboard: {url}", flush=True)
                print("Reusing the dashboard already serving this workspace", flush=True)
                if args.open:
                    webbrowser.open(url)
                return 0
            if live and winner is not None:
                print(
                    f"{session_path} already records a dashboard serving "
                    f"{winner.get('workspace')}; give this workspace its own "
                    "--session-file",
                    file=sys.stderr,
                )
                return 1
            print(str(exc), file=sys.stderr)
            return 1
        url = str(session["url"])
        print(f"Dashboard: {url}", flush=True)
        print(f"Serving in the background as pid {session['pid']}", flush=True)
        if args.open:
            webbrowser.open(url)
        return 0

    with contextlib.ExitStack() as stack:
        try:
            held = stack.enter_context(hold_session_lock(session_path))
            recordable = True
        except SessionUnavailable as exc:
            # A read-only workspace still opens. The record is what --detach and
            # --status need; serving never depended on it.
            print(f"{exc}", file=sys.stderr)
            print(
                "serving without a session record; --status and --stop cannot find it",
                file=sys.stderr,
            )
            held, recordable = True, False
        if not held:
            reused = read_session(session_path)
            if reused is not None and session_matches(reused, workspace):
                print(f"Dashboard: {reused['url']}", flush=True)
                print("Reusing the dashboard already serving this workspace", flush=True)
                return 0
            print(
                "another dashboard is already starting for this workspace",
                file=sys.stderr,
            )
            return 1
        server = create_server(workspace, host=args.host, port=args.port)
        raw_host, port = server.server_address[:2]
        host = raw_host.decode("ascii") if isinstance(raw_host, bytes) else raw_host
        display_host = f"[{host}]" if ":" in host else host
        scheme = "http"
        url = f"{scheme}://{display_host}:{port}/#{server.access_token}"
        if recordable:
            try:
                write_session(
                    session_path,
                    {
                        "schema_version": SESSION_SCHEMA,
                        "workspace": str(workspace),
                        "fingerprint": server.workspace_fingerprint,
                        "host": host,
                        "port": port,
                        "pid": os.getpid(),
                        "token": server.access_token,
                        "url": url,
                        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                )
            except OSError as exc:
                print(f"cannot write the session record: {exc}", file=sys.stderr)
                recordable = False
        # A detached server is stopped with SIGTERM. Handle it so the recorded
        # session is removed instead of being left behind. `shutdown` blocks
        # until `serve_forever` returns, so it cannot run on the thread that is
        # inside `serve_forever`.
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(
                signal.SIGTERM,
                lambda *_: threading.Thread(target=server.shutdown, daemon=True).start(),
            )
        watch_workspace(server, workspace)
        print(f"Dashboard: {url}", flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            if recordable:
                current = read_session(session_path)
                if current is not None and current.get("pid") == os.getpid():
                    with contextlib.suppress(OSError):
                        session_path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
