#!/usr/bin/env python3
"""Refresh the release file inventory and child core-manifest pins."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


# Kept byte-identical to skills/short-drama/scripts/suite_verify.py; a test
# asserts the two agree. Closed on purpose: an unknown dot-path stays visible
# so extra executable content is still reported instead of silently skipped.
NOISE_DIR_NAMES = frozenset({".ruff_cache", ".mypy_cache", ".pytest_cache"})
NOISE_FILE_NAMES = frozenset({".DS_Store"})
NOISE_FILE_SUFFIXES = ("~", ".swp", ".swo")
BYTECODE_SUFFIXES = (".pyc", ".pyo")
EXECUTABLE_SUFFIXES = (
    ".py", ".sh", ".bash", ".zsh", ".fish", ".js", ".mjs", ".cjs",
    ".rb", ".pl", ".php", ".exe", ".dll", ".so", ".dylib", ".command",
)


def is_local_noise(parts: tuple[str, ...]) -> bool:
    """True only for known-noise artifacts, never for arbitrary dot-paths."""

    name = parts[-1]
    # Executable content is never noise, wherever it sits. A payload planted
    # inside a cache directory stays reported.
    if name.endswith(EXECUTABLE_SUFFIXES):
        return False
    # Bytecode caches regenerate at runtime, so the bytecode itself is
    # tolerated; anything else under them is not.
    if "__pycache__" in parts[:-1]:
        return name.endswith(BYTECODE_SUFFIXES)
    if any(part in NOISE_DIR_NAMES for part in parts[:-1]):
        return True
    return name in NOISE_FILE_NAMES or name.endswith(NOISE_FILE_SUFFIXES)


CHILD_REF_KEYS = {
    "suite",
    "suite_version",
    "contract_version",
    "core_skill",
    "core_manifest",
    "recipe_version",
    "core_manifest_sha256",
}


def main(argv: list[str] | None = None) -> int:
    core = (
        Path(argv[0]).resolve()
        if argv
        else Path(__file__).resolve().parents[1] / "skills" / "short-drama"
    )
    skills = core.parent
    manifest_path = core / "suite-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    public_skills = manifest.get("public_skills")
    core_skill = manifest.get("core_skill")
    if (
        not isinstance(public_skills, list)
        or not all(isinstance(name, str) for name in public_skills)
        or not isinstance(core_skill, str)
        or core_skill not in public_skills
    ):
        raise ValueError("manifest public skill inventory is invalid")
    child_refs = {
        skills / name / "suite-ref.json"
        for name in public_skills
        if name != core_skill
    }
    for reference_path in child_refs:
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        if not isinstance(reference, dict) or set(reference) != CHILD_REF_KEYS:
            raise ValueError(f"child suite-ref keys are invalid: {reference_path.parent.name}")
        if reference.get("core_skill") != core_skill:
            raise ValueError(f"child suite-ref core_skill is invalid: {reference_path.parent.name}")
    unexpected_refs = [
        path.relative_to(skills).as_posix()
        for path in skills.rglob("suite-ref.json")
        if path not in child_refs
    ]
    if unexpected_refs:
        raise ValueError("unexpected suite-ref files: " + ", ".join(sorted(unexpected_refs)))
    forbidden_suffixes = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe"}
    files = {
        path.relative_to(skills).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(skills.rglob("*"))
        if path.is_file()
        and path != manifest_path
        and path not in child_refs
        and not is_local_noise(path.relative_to(skills).parts)
        and path.suffix.lower() not in forbidden_suffixes
    }
    manifest["files"] = dict(sorted(files.items()))
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    for child in skills.iterdir():
        reference_path = child / "suite-ref.json"
        if not reference_path.is_file():
            continue
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        reference["core_manifest_sha256"] = manifest_hash
        reference_path.write_text(
            json.dumps(reference, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"files": len(files), "core_manifest_sha256": manifest_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
