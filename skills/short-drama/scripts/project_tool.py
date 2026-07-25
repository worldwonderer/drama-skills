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
import sys
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


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
PROJECT_DIRS = (
    "inputs",
    "development",
    "bible",
    "episodes",
    "delivery",
    ".short-drama/transactions",
    ".short-drama/accepted-snapshots",
    ".short-drama/conflicts",
    ".short-drama/locks",
    ".short-drama/tmp",
)

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


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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


def initialize_project(
    path: Path,
    *,
    title: str,
    language: str,
    aspect_ratio: str,
    suite_root: Path | None = None,
) -> dict[str, Any]:
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

    state = {
        "schema_version": manifest["contract_version"],
        "project_id": project["project_id"],
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


def project_status(path: Path) -> dict[str, Any]:
    root = find_project(path)
    project = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    state_path = root / STATE_FILE
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    raw_artifacts = state.get("artifacts")
    artifacts: dict[str, Any] = raw_artifacts if isinstance(raw_artifacts, dict) else {}
    lifecycle: dict[str, dict[str, int]] = {axis: {} for axis in LIFECYCLE_STATES}
    for record in artifacts.values():
        if not isinstance(record, dict):
            continue
        for axis in LIFECYCLE_STATES:
            value = str(record.get(axis, "unknown"))
            lifecycle[axis][value] = lifecycle[axis].get(value, 0) + 1

    transaction_counts: dict[str, int] = {}
    transactions = root / ".short-drama/transactions"
    if transactions.is_dir():
        for transaction in transactions.iterdir():
            if not transaction.is_dir():
                continue
            status = _transaction_status(transaction)
            transaction_counts[status] = transaction_counts.get(status, 0) + 1
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
        "project_root": str(root),
        "project_id": project.get("project_id"),
        "title": project.get("title"),
        "current_checkpoint": project.get("current_checkpoint"),
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


def _relative_path(value: str | Path, *, allow_operations: bool = False) -> str:
    raw = str(value).replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError(f"unsafe project-relative path: {value!s}")
    relative = pure.as_posix()
    if not allow_operations and pure.parts[0] == ".short-drama":
        raise ValueError("operational state cannot be a publication target")
    return relative


def _project_path(root: Path, relative: str) -> Path:
    target = root / relative
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
) -> list[dict[str, Any]]:
    if read_set is None:
        return []
    entries: list[dict[str, Any]] = []
    items: Iterable[tuple[str, str | None]]
    if isinstance(read_set, Mapping):
        items = ((str(path), expected) for path, expected in read_set.items())
    else:
        items = ((str(path), _live_hash(_project_path(root, _relative_path(path, allow_operations=True)))) for path in read_set)
    for raw, expected in items:
        relative = _relative_path(raw, allow_operations=True)
        if expected is not None and not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"invalid expected read hash for {relative}")
        actual = _live_hash(_project_path(root, relative))
        if actual != expected:
            raise StaleReadSetError(f"read set was stale before prepare: {relative}")
        entries.append({"path": relative, "expected_hash": expected})
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
    fault_injector: FaultInjector | None = None,
    authority: str = "accepted",
    owner: str | None = None,
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
    relative_outputs = {_relative_path(key): value for key, value in outputs.items()}
    if any(PurePosixPath(relative).parts[0] == "inputs" for relative in relative_outputs):
        raise ValueError("creator inputs are immutable publication sources")

    if target_artifacts is None:
        default_artifact = next(iter(validated_changes)) if len(validated_changes) == 1 else stage
        mapped_artifacts = {relative: default_artifact for relative in relative_outputs}
    else:
        mapped_artifacts = {
            _relative_path(relative): str(artifact)
            for relative, artifact in target_artifacts.items()
        }
        missing = sorted(set(relative_outputs) - set(mapped_artifacts))
        extra = sorted(set(mapped_artifacts) - set(relative_outputs))
        if missing or extra:
            raise ValueError(f"target artifact mapping mismatch; missing={missing}, extra={extra}")
    if any(not artifact for artifact in mapped_artifacts.values()):
        raise ValueError("target artifact id cannot be empty")

    with _transaction_lock(root):
        transaction_id = uuid.uuid4().hex
        transaction = root / ".short-drama/transactions" / transaction_id
        staged = transaction / "staged"
        staged.mkdir(parents=True, exist_ok=False)
        if transaction.stat().st_dev != root.stat().st_dev:
            raise TransactionError("transaction directory is not on the project filesystem")

        read_entries = _normalize_read_set(root, read_set)
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
    for entry in read_set:
        if not isinstance(entry, dict) or set(entry) != {"path", "expected_hash"}:
            raise TransactionError("transaction read set entry is invalid")
        relative = _relative_path(
            entry["path"], allow_operations=authority == "accepted"
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
            if (
                record.get("owner") != manifest.get("owner")
                or record.get("candidate_inputs") != expected_inputs
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
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _validate_manifest(manifest, transaction_id)
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
            _apply_intended_lifecycle(root, manifest)
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
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid candidate JSONL at {relative}:{number}"
                ) from error


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

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            owner = value.get("owner")
            artifact = value.get("artifact")
            digest = value.get("hash")
            if (
                isinstance(owner, str)
                and isinstance(artifact, str)
                and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
            ):
                authority = value.get("authority")
                if authority not in {None, "accepted", "candidate"}:
                    raise ValueError(
                        f"structured ref authority is invalid: {_relative_path(artifact)}"
                    )
                references.append((_relative_path(artifact), digest, authority))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for document in documents:
        collect(document)
    return references


def _normalize_hash_mapping(values: Mapping[str, str], *, label: str) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw, value in values.items():
        relative = _relative_path(raw)
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


def _input_bindings(record: Mapping[str, Any], key: str) -> dict[str, str]:
    raw = record.get(key)
    if not isinstance(raw, dict):
        raise ValueError(f"artifact {key} are unavailable")
    normalized: dict[str, str] = {}
    for path, expected in raw.items():
        relative = _relative_path(path)
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
    _verify_live_hashes(root, inputs, label="accepted input")
    for relative, expected in inputs.items():
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
            if (
                isinstance(accepted_targets, dict)
                and accepted_targets.get(relative) == expected
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
    candidate_targets: Mapping[str, str],
) -> dict[str, dict[str, str]]:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return {}
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
            invalidated = any(
                path in affected
                and (affected[path] is None or affected[path] != expected)
                for path, expected in accepted_inputs.items()
            )
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
    fault_injector: FaultInjector | None = None,
) -> dict[str, Any]:
    """Publish a validated candidate without claiming creator or review authority."""

    root = find_project(path)
    if not isinstance(owner, str) or re.fullmatch(r"[A-Za-z0-9._:-]+", owner) is None:
        raise ValueError("owner must be an opaque identifier")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("artifact id cannot be empty")
    normalized_outputs: dict[str, bytes] = {}
    for raw, value in outputs.items():
        relative = _relative_path(raw)
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
    for raw, expected in (input_hashes or {}).items():
        relative = _relative_path(raw)
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError(f"invalid input hash for {relative}")
        if relative in exact_inputs:
            raise ValueError(f"duplicate input path: {relative}")
        exact_inputs[relative] = expected
    exact_inputs = dict(sorted(exact_inputs.items()))
    candidate_hashes = {
        relative: sha256_bytes(content)
        for relative, content in normalized_outputs.items()
    }
    for output, content in normalized_outputs.items():
        for referenced_path, referenced_hash, reference_authority in _structured_candidate_refs(
            output, content
        ):
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
                raise ValueError(
                    f"structured ref requires exact input: {referenced_path}"
                )
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
        ),
    }
    transaction = publish_transaction(
        root,
        stage="candidate",
        outputs=normalized_outputs,
        lifecycle_changes=lifecycle_changes,
        target_artifacts={relative: artifact_id for relative in normalized_outputs},
        read_set=exact_inputs,
        authority="candidate",
        owner=owner,
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
        _validate_input_closure(
            root,
            state,
            artifact_id,
            bindings=candidate_inputs,
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


def build_delivery_package(
    path: Path,
    *,
    episode: str,
    selected_paths: Iterable[str | Path],
    text_exceptions: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    root = find_project(path)
    if not re.fullmatch(r"EP[0-9]{3,}", episode):
        raise ValueError("episode must use an EP001-style identifier")
    exceptions, allowed_urls_by_path = _normalize_text_exceptions(text_exceptions)
    state = _read_state(root)
    files: list[dict[str, Any]] = []
    outputs: dict[str, bytes] = {}
    source_artifacts: set[str] = set()
    for raw in sorted({_relative_path(selected) for selected in selected_paths}):
        pure = PurePosixPath(raw)
        if pure.parts[0] in {"inputs", "delivery", ".short-drama"}:
            raise PackageBlockedError(f"private or operational zone excluded: {raw}")
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
        destination = f"delivery/{episode}/artifacts/{raw}"
        outputs[destination] = content
        source_artifacts.add(artifact_id)
        files.append(
            {
                "artifact_id": artifact_id,
                "source": raw,
                "delivery_path": str(
                    PurePosixPath(destination).relative_to(PurePosixPath("delivery") / episode)
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

    manifest = {
        "schema_version": 1,
        "episode": episode,
        "files": files,
        "text_exceptions": exceptions,
        "exclusions": ["private_inputs", "operational_state", "binaries", "unselected_files"],
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_target = f"delivery/{episode}/manifest.json"
    outputs[manifest_target] = manifest_bytes
    checksum_entries = [
        (entry["sha256"], entry["delivery_path"]) for entry in files
    ] + [(sha256_bytes(manifest_bytes), "manifest.json")]
    checksums = "".join(
        f"{digest}  {relative}\n" for digest, relative in sorted(checksum_entries, key=lambda item: item[1])
    ).encode("utf-8")
    checksum_target = f"delivery/{episode}/checksums.sha256"
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
    )
    return {
        "project_root": str(root),
        "episode": episode,
        "file_count": len(files),
        "transaction_id": transaction["transaction_id"],
        "status": "delivered",
    }


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
        if target == source:
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
    return publish_candidate(
        root,
        owner=args.owner,
        artifact_id=args.artifact_id,
        outputs=outputs,
        input_hashes=inputs,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a short-drama filesystem project.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize a project without creative content.")
    init.add_argument("path", type=Path)
    init.add_argument("--title", default="未命名短剧")
    init.add_argument("--language", default="zh-CN")
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
    package.add_argument("--text-exceptions", type=Path)
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
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
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
