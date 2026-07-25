#!/usr/bin/env python3
"""Verify one installed, version-consistent short-drama skill suite."""

from __future__ import annotations

import json
import hashlib
import re
import sys
from pathlib import Path
from typing import Any


CHILD_REF_KEYS = {
    "suite",
    "suite_version",
    "contract_version",
    "core_skill",
    "core_manifest",
    "recipe_version",
    "core_manifest_sha256",
}
EXPECTED_TRUST_BOUNDARY = {
    "host_text_inference": True,
    "suite_scripts_outbound_network": False,
    "media_generation": False,
    "provider_api_calls": False,
    "private_source_runtime_access": False,
}
OPENAI_INTERFACE_KEYS = {"display_name", "short_description", "default_prompt"}
REQUIRED_FRONTMATTER_KEYS = {"name", "description"}
OPTIONAL_FRONTMATTER_KEYS = {"license", "allowed-tools", "metadata"}


def load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"expected JSON object: {path}")
    return document


def verify_skill_contract(skill: Path, expected_name: str) -> None:
    """Verify the portable subset of the official Agent Skill contract."""

    skill_md = skill / "SKILL.md"
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if len(lines) > 500:
        raise ValueError(f"{expected_name} SKILL.md exceeds 500 lines")
    if not lines or lines[0] != "---":
        raise ValueError(f"{expected_name} SKILL.md is missing frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ValueError(f"{expected_name} SKILL.md has unclosed frontmatter") from error
    frontmatter: dict[str, str] = {}
    for line in lines[1:closing]:
        match = re.fullmatch(r"([a-z][a-z0-9_-]*):\s+(.+)", line)
        if match is None or match.group(1) in frontmatter:
            raise ValueError(f"{expected_name} SKILL.md has invalid frontmatter")
        frontmatter[match.group(1)] = match.group(2).strip()
    # The official Agent Skill contract requires name and description and allows
    # license, allowed-tools and metadata. Keep the optional set closed so a
    # rebuilt manifest still cannot smuggle an unknown key past the verifier.
    if not REQUIRED_FRONTMATTER_KEYS <= set(frontmatter):
        raise ValueError(f"{expected_name} frontmatter keys must include name and description")
    if not set(frontmatter) <= REQUIRED_FRONTMATTER_KEYS | OPTIONAL_FRONTMATTER_KEYS:
        raise ValueError(f"{expected_name} frontmatter keys are not allowed by the skill contract")
    if "license" in frontmatter and not frontmatter["license"]:
        raise ValueError(f"{expected_name} frontmatter license is empty")
    if "allowed-tools" in frontmatter and not frontmatter["allowed-tools"]:
        raise ValueError(f"{expected_name} frontmatter allowed-tools is empty")
    if "metadata" in frontmatter:
        # metadata is a mapping in the spec; a bare scalar is not a valid value.
        try:
            metadata_value = json.loads(frontmatter["metadata"])
        except json.JSONDecodeError as error:
            raise ValueError(
                f"{expected_name} frontmatter metadata must be an inline JSON object"
            ) from error
        if not isinstance(metadata_value, dict):
            raise ValueError(
                f"{expected_name} frontmatter metadata must be an inline JSON object"
            )
    if frontmatter["name"] != expected_name:
        raise ValueError(f"{expected_name} frontmatter name does not match its directory")
    if not frontmatter["description"] or len(frontmatter["description"]) > 1024:
        raise ValueError(f"{expected_name} description is empty or exceeds 1024 characters")

    metadata_path = skill / "agents/openai.yaml"
    metadata_lines = metadata_path.read_text(encoding="utf-8").splitlines()
    if not metadata_lines or metadata_lines[0] != "interface:":
        raise ValueError(f"{expected_name} openai.yaml must contain one interface mapping")
    interface: dict[str, str] = {}
    for line in metadata_lines[1:]:
        match = re.fullmatch(r'  ([a-z_]+):\s+("(?:[^"\\]|\\.)*")', line)
        if match is None or match.group(1) in interface:
            raise ValueError(f"{expected_name} openai.yaml has unsupported metadata")
        value = json.loads(match.group(2))
        if not isinstance(value, str):
            raise ValueError(f"{expected_name} openai.yaml values must be strings")
        interface[match.group(1)] = value
    if set(interface) != OPENAI_INTERFACE_KEYS:
        raise ValueError(f"{expected_name} openai.yaml interface keys are invalid")
    if not interface["display_name"].strip() or len(interface["display_name"]) > 64:
        raise ValueError(f"{expected_name} openai.yaml display_name is invalid")
    short_description = interface["short_description"]
    if not 25 <= len(short_description) <= 64:
        raise ValueError(f"{expected_name} openai.yaml short_description is invalid")
    default_prompt = interface["default_prompt"]
    if f"${expected_name}" not in default_prompt or "\n" in default_prompt:
        raise ValueError(f"{expected_name} openai.yaml default_prompt is invalid")


def verify_suite(core: Path) -> dict[str, Any]:
    # Preserve the caller-visible installation path. Resolving the core symlink
    # here would silently switch verification back to its source checkout and
    # could miss a sibling skill linked from a different version.
    core = core.expanduser().absolute()
    manifest_path = core / "suite-manifest.json"
    manifest = load_json(manifest_path)
    if manifest.get("trust_boundary") != EXPECTED_TRUST_BOUNDARY:
        raise ValueError("suite-manifest trust_boundary violates the text-only runtime contract")
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    skills_root = core.parent
    expected = manifest.get("public_skills")
    if not isinstance(expected, list) or not all(isinstance(name, str) for name in expected):
        raise ValueError("suite-manifest public_skills must be a string list")
    core_skill = manifest.get("core_skill")
    if not isinstance(core_skill, str) or core_skill not in expected:
        raise ValueError("suite-manifest core_skill must name one public skill")
    child_refs = {
        f"{name}/suite-ref.json" for name in expected if name != core_skill
    }
    files = manifest.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in files.items()
    ):
        raise ValueError("suite-manifest files must map relative paths to SHA-256")

    actual_files: set[str] = set()
    for name in expected:
        installed_skill = skills_root / name
        try:
            skill_source = installed_skill.resolve(strict=True)
        except OSError as error:
            raise FileNotFoundError(f"missing skill: {name}") from error
        if not skill_source.is_dir():
            raise FileNotFoundError(f"missing skill: {name}")
        for path in skill_source.rglob("*"):
            # Local bytecode caches are development noise, never release content;
            # sibling skills outside public_skills are unrelated installations.
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            # Dot-prefixed entries are editor, OS and tool noise (.DS_Store, swap
            # files, lint caches). runtime-preflight only halts on extra
            # executable content, so this noise must not stop the suite;
            # update_suite_manifest applies the same rule so both agree.
            if any(
                part.startswith(".")
                for part in path.relative_to(skill_source).parts
            ):
                continue
            child_relative = path.relative_to(skill_source).as_posix()
            relative = f"{name}/{child_relative}"
            if relative == f"{core_skill}/suite-manifest.json":
                continue
            if relative not in child_refs:
                actual_files.add(relative)
    unexpected = sorted(actual_files - set(files))
    missing = sorted(set(files) - actual_files)
    if unexpected:
        raise ValueError(f"unexpected suite files: {', '.join(unexpected)}")
    if missing:
        raise ValueError(f"missing manifest files: {', '.join(missing)}")
    for relative, expected_hash in files.items():
        actual_hash = hashlib.sha256((skills_root / relative).read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"content hash mismatch: {relative}")

    checked: list[str] = []
    for name in expected:
        skill = skills_root / name
        if not (skill / "SKILL.md").is_file():
            raise FileNotFoundError(f"missing skill: {name}")
        verify_skill_contract(skill, name)
        if name != core_skill:
            reference = load_json(skill / "suite-ref.json")
            if set(reference) != CHILD_REF_KEYS:
                raise ValueError(f"{name} suite-ref keys are invalid")
            for key in (
                "suite",
                "suite_version",
                "contract_version",
                "recipe_version",
                "core_skill",
            ):
                if reference.get(key) != manifest.get(key):
                    raise ValueError(f"{name} mixed {key}: {reference.get(key)!r}")
            if reference.get("core_manifest_sha256") != manifest_hash:
                raise ValueError(f"{name} core manifest hash mismatch")
            resolved = (skill / str(reference.get("core_manifest"))).resolve()
            if resolved != manifest_path.resolve():
                raise ValueError(f"{name} resolves the wrong core manifest")
        checked.append(name)
    return {
        "suite": manifest.get("suite"),
        "suite_version": manifest.get("suite_version"),
        "contract_version": manifest.get("contract_version"),
        "recipe_version": manifest.get("recipe_version"),
        "core_manifest_sha256": manifest_hash,
        "checked_skills": checked,
        "checked_files": len(files),
    }


def main(argv: list[str] | None = None) -> int:
    core = (
        Path(argv[0])
        if argv
        else Path(__file__).resolve().parents[1]
    )
    try:
        print(json.dumps(verify_suite(core), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
