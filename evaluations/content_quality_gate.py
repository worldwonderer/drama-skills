#!/usr/bin/env python3
"""Validate sealed, provenance-bound cross-genre screenplay A/B evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


DIMENSIONS = {
    "hook_payoff": 15,
    "causality": 15,
    "character": 15,
    "dialogue_action": 15,
    "visual_drama": 10,
    "pacing_retention": 10,
    "prompt_fidelity": 10,
    "genre_fit": 10,
}
FAMILIES = {"codex", "kimi"}
SPLITS = {"development", "holdout"}
MECHANISMS = {
    "consequential_choice",
    "contested_evidence",
    "literal_precise_deadline",
}
OVERFIT_FLAGS = {
    "forced_choice",
    "forced_evidence_procedure",
    "forced_deadline",
    "case_echo",
    "mechanical_rule_display",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_OBJECT_RE = re.compile(r"[0-9a-f]{40,64}")
MIN_CASES = 12
MIN_GENRES = 10
MIN_SPLIT_CASES = 4
MIN_NEGATIVE_CONTROLS = 3
MIN_CASES_PER_MECHANISM = 3
MIN_LEAKAGE_TERMS = 8
MAX_CASE_REGRESSION = 2.0
MAX_AGGREGATE_SLICE_REGRESSION = 1.0
MAX_DIMENSION_REGRESSION = 1.0
TEXT_SUFFIXES = {".json", ".md", ".py", ".txt", ".yaml", ".yml"}
SOURCE_BUNDLE = Path("skills/short-drama-write")
LEAKAGE_SCAN_ROOTS = (
    Path("skills/short-drama-write"),
    Path("skills/short-drama-review"),
    Path("evaluations"),
)
MANIFEST_SCHEMA = 5
CORPUS_SCHEMA = 3
CONFIG_SCHEMA = 3
RECEIPT_SCHEMA = 2
TRUSTED_SEAL_SCHEMA = 1
GENERATION_WORKSPACE_POLICY = "source-bundle-only"
JUDGE_WORKSPACE_POLICY = "prompt-only"
EMPTY_WORKSPACE_SHA256 = hashlib.sha256(b"").hexdigest()
HOLDOUT_SEAL_KEYS = {
    "seal_id",
    "baseline_commit",
    "baseline_skill_bundle_sha256",
    "candidate_skill_bundle_sha256",
    "corpus_bundle_sha256",
    "corpus_sha256",
    "evaluation_config_sha256",
    "gate_sha256",
    "generation_prompt_template_sha256",
    "judge_prompt_template_sha256",
    "leakage_terms_sha256",
    "rubric_sha256",
}


class GateError(ValueError):
    """The evaluation evidence is malformed or insufficient."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise GateError(f"expected JSON object: {path}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_text_sha256(path: Path) -> str:
    text = unicodedata.normalize("NFC", path.read_text(encoding="utf-8-sig"))
    content = "".join(character for character in text if not character.isspace())
    if not content:
        raise GateError(f"screenplay artifact has no non-whitespace content: {path}")
    return _sha256_bytes(content.encode("utf-8"))


def _valid_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise GateError(f"{label} sha256 is invalid")
    return value


def _resolve_file(path_value: object, root: Path, label: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise GateError(f"{label} path is required")
    path = (root / path_value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise GateError(f"{label} is missing or escapes its trusted root")
    return path


def _artifact(
    path_value: object,
    root: Path,
    expected_sha: object,
    label: str,
) -> tuple[Path, str]:
    expected = _valid_sha(expected_sha, label)
    path = _resolve_file(path_value, root, label)
    actual = _sha256(path)
    if actual != expected:
        raise GateError(f"{label} digest changed")
    return path, actual


def _framed_digest(entries: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    count = 0
    for name, content in entries:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        count += 1
    if count == 0:
        raise GateError("source bundle is empty")
    return digest.hexdigest()


def working_source_bundle_sha256(repo_root: Path) -> str:
    bundle_root = repo_root / SOURCE_BUNDLE
    if not bundle_root.is_dir():
        raise GateError(f"candidate source bundle is missing: {bundle_root}")
    paths = sorted(
        path
        for path in bundle_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    return _framed_digest(
        (path.relative_to(repo_root).as_posix(), path.read_bytes()) for path in paths
    )


def git_source_bundle_sha256(repo_root: Path, commit: str) -> str:
    if GIT_OBJECT_RE.fullmatch(commit) is None:
        raise GateError("baseline_commit is not a full Git object id")
    try:
        listing = subprocess.run(
            [
                "git",
                "ls-tree",
                "-r",
                "-z",
                "--name-only",
                commit,
                "--",
                SOURCE_BUNDLE.as_posix(),
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as exc:
        raise GateError("cannot resolve baseline source bundle") from exc
    names = sorted(name for name in listing.split("\0") if name)
    entries: list[tuple[str, bytes]] = []
    for name in names:
        try:
            content = subprocess.run(
                ["git", "show", f"{commit}:{name}"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise GateError("cannot read baseline source bundle") from exc
        entries.append((name, content))
    return _framed_digest(entries)


def _validated_model_config(value: object, label: str) -> dict[str, str]:
    required = {"cli", "model", "reasoning_effort", "session_policy"}
    if (
        not isinstance(value, dict)
        or set(value) != required
        or not all(isinstance(item, str) and item for item in value.values())
    ):
        raise GateError(f"{label} model configuration is invalid")
    return value


def _load_config(path: Path) -> dict[str, Any]:
    config = _load_object(path)
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise GateError(f"evaluation config schema must be {CONFIG_SCHEMA}")
    if set(config) != {
        "schema_version",
        "generation_replicates",
        "generation_workspace_policy",
        "judge_workspace_policy",
        "generator",
        "judges",
    }:
        raise GateError("evaluation config has an invalid shape")
    replicates = config.get("generation_replicates")
    if isinstance(replicates, bool) or not isinstance(replicates, int) or replicates < 3:
        raise GateError("evaluation config requires at least 3 generation replicates")
    if config.get("generation_workspace_policy") != GENERATION_WORKSPACE_POLICY:
        raise GateError("generation workspace must expose only the bound source bundle")
    if config.get("judge_workspace_policy") != JUDGE_WORKSPACE_POLICY:
        raise GateError("judge workspace must be empty and receive only the bound prompt")
    _validated_model_config(config.get("generator"), "generator")
    judges = config.get("judges")
    if not isinstance(judges, dict) or set(judges) != FAMILIES:
        raise GateError("evaluation config must declare both judge families")
    for family in FAMILIES:
        _validated_model_config(judges[family], f"{family} judge")
    return config


def _validated_case_metadata(case: object, label: str) -> dict[str, Any]:
    required = {
        "case_id",
        "split",
        "genre",
        "negative_control",
        "mechanisms",
        "case_spec",
    }
    if not isinstance(case, dict) or not required.issubset(case):
        raise GateError(f"{label} case metadata is incomplete")
    case_id = case.get("case_id")
    split = case.get("split")
    genre = case.get("genre")
    negative_control = case.get("negative_control")
    mechanisms = case.get("mechanisms")
    if not isinstance(case_id, str) or not case_id:
        raise GateError(f"{label} case_id must be non-empty")
    if split not in SPLITS:
        raise GateError(f"{case_id}: split must be development or holdout")
    if not isinstance(genre, str) or not genre.strip():
        raise GateError(f"{case_id}: genre must be non-empty")
    if not isinstance(negative_control, bool):
        raise GateError(f"{case_id}: negative_control must be boolean")
    if (
        not isinstance(mechanisms, dict)
        or set(mechanisms) != MECHANISMS
        or not all(isinstance(enabled, bool) for enabled in mechanisms.values())
    ):
        raise GateError(f"{case_id}: mechanisms must declare every fixed mechanism")
    if negative_control and any(mechanisms.values()):
        raise GateError(f"{case_id}: a negative control cannot require a target mechanism")
    if not isinstance(case.get("case_spec"), str) or not case["case_spec"]:
        raise GateError(f"{case_id}: case_spec must be non-empty")
    return case


def _load_corpus(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    corpus = _load_object(path)
    if corpus.get("schema_version") != CORPUS_SCHEMA:
        raise GateError(f"corpus schema must be {CORPUS_SCHEMA}")
    if set(corpus) != {"schema_version", "corpus_id", "holdout_policy", "cases"}:
        raise GateError("corpus registry has an invalid shape")
    if not isinstance(corpus.get("corpus_id"), str) or not corpus["corpus_id"]:
        raise GateError("corpus_id must be non-empty")
    holdout_policy = corpus.get("holdout_policy")
    if (
        not isinstance(holdout_policy, dict)
        or set(holdout_policy) != SPLITS
        or not all(isinstance(value, str) and value for value in holdout_policy.values())
    ):
        raise GateError("corpus holdout policy is invalid")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or len(cases) < MIN_CASES:
        raise GateError(f"trusted corpus needs at least {MIN_CASES} cases")
    registry: dict[str, dict[str, Any]] = {}
    spec_hashes: set[str] = set()
    for raw_case in cases:
        case = _validated_case_metadata(raw_case, "corpus")
        case_id = case["case_id"]
        if case_id in registry:
            raise GateError("corpus case_id values must be unique")
        spec = _resolve_file(case["case_spec"], path.parent, f"{case_id} corpus spec")
        spec_sha = _sha256(spec)
        if spec_sha in spec_hashes:
            raise GateError("corpus case specs must be content-distinct")
        spec_hashes.add(spec_sha)
        registry[case_id] = {
            **{key: case[key] for key in case if key != "case_spec"},
            "case_spec": case["case_spec"],
            "case_spec_path": spec,
            "case_spec_sha256": spec_sha,
        }
    return corpus, registry


def _corpus_bundle_sha256(
    corpus_path: Path,
    registry: dict[str, dict[str, Any]],
) -> str:
    entries = [(corpus_path.name, corpus_path.read_bytes())]
    entries.extend(
        (
            registry[case_id]["case_spec"],
            registry[case_id]["case_spec_path"].read_bytes(),
        )
        for case_id in sorted(registry)
    )
    return _framed_digest(entries)


def corpus_bundle_sha256(corpus_path: Path) -> str:
    corpus_path = corpus_path.expanduser().resolve(strict=True)
    _, registry = _load_corpus(corpus_path)
    return _corpus_bundle_sha256(corpus_path, registry)


def _substitute(template: str, replacements: dict[str, str], label: str) -> str:
    rendered = template
    for name, value in replacements.items():
        token = "{{" + name + "}}"
        if rendered.count(token) != 1:
            raise GateError(f"{label} must contain {token} exactly once")
        rendered = rendered.replace(token, value)
    if re.search(r"\{\{[A-Z_]+\}\}", rendered):
        raise GateError(f"{label} has unresolved placeholders")
    return rendered


def render_generation_prompt(template: str, case_spec: str) -> str:
    return _substitute(
        template,
        {"CASE_SPEC": case_spec.strip()},
        "generation prompt template",
    )


def _report_template(
    case_id: str,
    replicate_id: str,
    judge_id: str,
    family: str,
    case_sha: str,
    artifacts: dict[str, str],
) -> dict[str, Any]:
    zero = {dimension: 0 for dimension in DIMENSIONS}
    evidence = {dimension: "具体证据" for dimension in DIMENSIONS}
    return {
        "case_id": case_id,
        "replicate_id": replicate_id,
        "judge_id": judge_id,
        "family": family,
        "case_spec_sha256": case_sha,
        "artifact_sha256": artifacts,
        "scores": {"A": dict(zero), "B": dict(zero)},
        "evidence": {"A": dict(evidence), "B": dict(evidence)},
        "diagnostics": {
            "A": {"overfit_flags": [], "overfit_evidence": {}},
            "B": {"overfit_flags": [], "overfit_evidence": {}},
        },
        "preference": "A",
    }


def render_judge_prompt(
    template: str,
    rubric: str,
    case_spec: str,
    artifact_a: str,
    artifact_b: str,
    report_template: dict[str, Any],
) -> str:
    return _substitute(
        template,
        {
            "RUBRIC": rubric.strip(),
            "CASE_SPEC": case_spec.strip(),
            "ARTIFACT_A": artifact_a.strip(),
            "ARTIFACT_B": artifact_b.strip(),
            "REPORT_TEMPLATE": json.dumps(
                report_template,
                ensure_ascii=False,
                indent=2,
            ),
        },
        "judge prompt template",
    )


def _load_leakage_terms(path: Path) -> tuple[str, ...]:
    terms = tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if len(terms) < MIN_LEAKAGE_TERMS or len(terms) != len(set(terms)):
        raise GateError(
            f"trusted leakage set needs at least {MIN_LEAKAGE_TERMS} unique entries"
        )
    return terms


def _validated_seal(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != HOLDOUT_SEAL_KEYS:
        raise GateError(f"{label} has an invalid shape")
    if not isinstance(value.get("seal_id"), str) or not value["seal_id"]:
        raise GateError(f"{label} seal_id must be non-empty")
    baseline_commit = value.get("baseline_commit")
    if not isinstance(baseline_commit, str) or GIT_OBJECT_RE.fullmatch(
        baseline_commit
    ) is None:
        raise GateError(f"{label} baseline_commit is invalid")
    for name in HOLDOUT_SEAL_KEYS - {"seal_id", "baseline_commit"}:
        _valid_sha(value.get(name), f"{label} {name}")
    return value


def _load_trusted_seal(path: Path) -> dict[str, str]:
    document = _load_object(path)
    if (
        set(document) != {"schema_version", "holdout_seal"}
        or document.get("schema_version") != TRUSTED_SEAL_SCHEMA
    ):
        raise GateError("trusted seal document has an invalid shape")
    return _validated_seal(document.get("holdout_seal"), "trusted holdout seal")


def _text_leaks(path: Path, terms: tuple[str, ...]) -> bool:
    text = path.read_text(encoding="utf-8").casefold()
    return any(term.casefold() in text for term in terms)


def _release_text_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_root in LEAKAGE_SCAN_ROOTS:
        root = repo_root / relative_root
        if not root.is_dir():
            raise GateError(f"release leakage scan root is missing: {relative_root}")
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in TEXT_SUFFIXES
            and "__pycache__" not in path.parts
        )
    return sorted(set(files))


def _validated_scores(value: object, *, report: Path, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(DIMENSIONS):
        raise GateError(f"{report}: {label} must score every fixed dimension")
    validated: dict[str, int] = {}
    for dimension, maximum in DIMENSIONS.items():
        score = value[dimension]
        if isinstance(score, bool) or not isinstance(score, int):
            raise GateError(f"{report}: {label}.{dimension} must be an integer")
        if score < 0 or score > maximum:
            raise GateError(
                f"{report}: {label}.{dimension} must be between 0 and {maximum}"
            )
        validated[dimension] = score
    return validated


def _validated_diagnostics(value: object, *, report: Path, label: str) -> int:
    if not isinstance(value, dict) or set(value) != {
        "overfit_flags",
        "overfit_evidence",
    }:
        raise GateError(f"{report}: {label} diagnostics have an invalid shape")
    flags = value["overfit_flags"]
    evidence = value["overfit_evidence"]
    if (
        not isinstance(flags, list)
        or len(flags) != len(set(flags))
        or any(flag not in OVERFIT_FLAGS for flag in flags)
    ):
        raise GateError(f"{report}: {label} has invalid overfit flags")
    if not isinstance(evidence, dict) or set(evidence) != set(flags):
        raise GateError(f"{report}: {label} overfit evidence must match its flags")
    if not all(isinstance(text, str) and text.strip() for text in evidence.values()):
        raise GateError(f"{report}: {label} overfit evidence must be non-empty")
    return len(flags)


def _validated_receipt(
    path: Path,
    *,
    expected: dict[str, Any],
    label: str,
) -> None:
    receipt = _load_object(path)
    required = set(expected) | {"schema_version", "cli_version"}
    if set(receipt) != required or receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise GateError(f"{label} receipt has an invalid shape")
    if not isinstance(receipt.get("cli_version"), str) or not receipt["cli_version"]:
        raise GateError(f"{label} receipt needs a CLI version")
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise GateError(f"{label} receipt does not match {key}")


def _mean(values: list[float]) -> float:
    if not values:
        raise GateError("cannot aggregate an empty score slice")
    return sum(values) / len(values)


def _score_summary(baseline: list[float], candidate: list[float]) -> dict[str, Any]:
    baseline_mean = _mean(baseline)
    candidate_mean = _mean(candidate)
    return {
        "baseline_mean": round(baseline_mean, 4),
        "candidate_mean": round(candidate_mean, 4),
        "score_change": round(candidate_mean - baseline_mean, 4),
        "non_regression": candidate_mean
        >= baseline_mean - MAX_AGGREGATE_SLICE_REGRESSION,
    }


def evaluate(
    manifest_path: Path,
    *,
    leakage_terms_path: Path,
    trusted_seal_path: Path,
    repo_root: Path | None = None,
    corpus_path: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve(strict=True)
    root = manifest_path.parent
    repo_root = (repo_root or Path(__file__).resolve().parents[1]).resolve(strict=True)
    corpus_path = (
        corpus_path or repo_root / "evaluations/content-quality-corpus.json"
    ).resolve(strict=True)
    config_path = (
        config_path or repo_root / "evaluations/content-quality-config.json"
    ).resolve(strict=True)
    rubric_path = (repo_root / "evaluations/content-quality-rubric.md").resolve(
        strict=True
    )
    generation_template_path = (
        repo_root / "evaluations/content-quality-generation-prompt.md"
    ).resolve(strict=True)
    judge_template_path = (
        repo_root / "evaluations/content-quality-judge-prompt.md"
    ).resolve(strict=True)
    leakage_terms_path = leakage_terms_path.expanduser().resolve(strict=True)
    trusted_seal_path = trusted_seal_path.expanduser().resolve(strict=True)
    if trusted_seal_path.is_relative_to(root):
        raise GateError("trusted seal must live outside the evaluation run directory")
    trusted_seal = _load_trusted_seal(trusted_seal_path)

    manifest = _load_object(manifest_path)
    if set(manifest) != {
        "schema_version",
        "corpus_id",
        "provenance",
        "holdout_seal",
        "cases",
    }:
        raise GateError("manifest has an invalid shape")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise GateError(f"schema_version must be {MANIFEST_SCHEMA}")

    corpus, registry = _load_corpus(corpus_path)
    config = _load_config(config_path)
    trusted_hashes = {
        "corpus_bundle_sha256": _corpus_bundle_sha256(corpus_path, registry),
        "corpus_sha256": _sha256(corpus_path),
        "evaluation_config_sha256": _sha256(config_path),
        "gate_sha256": _sha256(Path(__file__).resolve()),
        "generation_prompt_template_sha256": _sha256(generation_template_path),
        "judge_prompt_template_sha256": _sha256(judge_template_path),
        "rubric_sha256": _sha256(rubric_path),
        "leakage_terms_sha256": _sha256(leakage_terms_path),
    }
    leakage_terms = _load_leakage_terms(leakage_terms_path)
    if manifest.get("corpus_id") != corpus["corpus_id"]:
        raise GateError("manifest corpus_id does not match the trusted corpus")

    provenance = manifest.get("provenance")
    provenance_keys = {
        "baseline_commit",
        "baseline_skill_bundle_sha256",
        "candidate_skill_bundle_sha256",
        *trusted_hashes,
    }
    if not isinstance(provenance, dict) or set(provenance) != provenance_keys:
        raise GateError("manifest provenance has an invalid shape")
    for name, trusted_sha in trusted_hashes.items():
        if _valid_sha(provenance.get(name), name) != trusted_sha:
            raise GateError(f"{name} does not match the trusted release input")
    baseline_commit = provenance.get("baseline_commit")
    if not isinstance(baseline_commit, str):
        raise GateError("baseline_commit is required")
    baseline_bundle_sha = git_source_bundle_sha256(repo_root, baseline_commit)
    candidate_bundle_sha = working_source_bundle_sha256(repo_root)
    if _valid_sha(
        provenance.get("baseline_skill_bundle_sha256"),
        "baseline skill bundle",
    ) != baseline_bundle_sha:
        raise GateError("baseline source bundle does not match baseline_commit")
    if _valid_sha(
        provenance.get("candidate_skill_bundle_sha256"),
        "candidate skill bundle",
    ) != candidate_bundle_sha:
        raise GateError("candidate source bundle changed after evaluation")

    seal = _validated_seal(manifest.get("holdout_seal"), "manifest holdout seal")
    if seal != trusted_seal:
        raise GateError("manifest holdout seal does not match the trusted seal")
    for name in HOLDOUT_SEAL_KEYS - {"seal_id"}:
        if seal.get(name) != provenance.get(name):
            raise GateError(f"holdout seal does not bind {name}")

    release_leakage_files = [
        path.relative_to(repo_root).as_posix()
        for path in _release_text_files(repo_root)
        if _text_leaks(path, leakage_terms)
    ]
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != len(registry):
        raise GateError("manifest must contain every trusted corpus case exactly once")

    seen_case_ids: set[str] = set()
    seen_case_spec_shas: set[str] = set()
    seen_artifact_shas: set[str] = set()
    seen_artifact_content_shas: set[str] = set()
    seen_generation_run_ids: set[str] = set()
    seen_judge_ids: set[str] = set()
    candidate_leakage: dict[str, bool] = {}
    case_results: list[dict[str, Any]] = []
    all_baseline_scores: list[float] = []
    all_candidate_scores: list[float] = []
    aggregate_scores: dict[str, dict[str, list[float]]] = {
        "family": defaultdict(list),
        "family_candidate": defaultdict(list),
        "position": defaultdict(list),
        "position_candidate": defaultdict(list),
    }
    dimension_baseline: dict[str, list[float]] = defaultdict(list)
    dimension_candidate: dict[str, list[float]] = defaultdict(list)
    baseline_flags_total = 0
    candidate_flags_total = 0

    generation_template = generation_template_path.read_text(encoding="utf-8")
    judge_template = judge_template_path.read_text(encoding="utf-8")
    rubric = rubric_path.read_text(encoding="utf-8")

    for raw_case in cases:
        case = _validated_case_metadata(raw_case, "manifest")
        expected_case_keys = {
            "case_id",
            "split",
            "genre",
            "negative_control",
            "mechanisms",
            "case_spec",
            "case_spec_sha256",
            "generation_prompt",
            "generation_prompt_sha256",
            "replicates",
        }
        if set(case) != expected_case_keys:
            raise GateError("manifest case has an invalid shape")
        case_id = case["case_id"]
        if case_id in seen_case_ids or case_id not in registry:
            raise GateError("manifest case IDs must exactly match the trusted corpus")
        seen_case_ids.add(case_id)
        trusted_case = registry[case_id]
        for name in ("split", "genre", "negative_control", "mechanisms"):
            if case.get(name) != trusted_case[name]:
                raise GateError(f"{case_id}: {name} does not match the trusted corpus")

        case_spec_path, case_spec_sha = _artifact(
            case.get("case_spec"),
            root,
            case.get("case_spec_sha256"),
            f"{case_id} case spec",
        )
        if case_spec_sha != trusted_case["case_spec_sha256"]:
            raise GateError(f"{case_id}: case spec differs from the trusted corpus")
        if case_spec_sha in seen_case_spec_shas:
            raise GateError("case specs must be content-distinct across cases")
        seen_case_spec_shas.add(case_spec_sha)

        generation_prompt_path, generation_prompt_sha = _artifact(
            case.get("generation_prompt"),
            root,
            case.get("generation_prompt_sha256"),
            f"{case_id} generation prompt",
        )
        expected_generation_prompt = render_generation_prompt(
            generation_template,
            case_spec_path.read_text(encoding="utf-8"),
        )
        if generation_prompt_path.read_text(encoding="utf-8") != expected_generation_prompt:
            raise GateError(f"{case_id}: generation prompt is not the neutral template")

        replicates = case.get("replicates")
        replicate_count = config["generation_replicates"]
        if not isinstance(replicates, list) or len(replicates) != replicate_count:
            raise GateError(
                f"{case_id}: exactly {replicate_count} generation replicates are required"
            )
        expected_replicate_ids = {
            f"r{index:02d}" for index in range(1, replicate_count + 1)
        }
        actual_replicate_ids = {
            replicate.get("replicate_id")
            for replicate in replicates
            if isinstance(replicate, dict)
        }
        if actual_replicate_ids != expected_replicate_ids:
            raise GateError(f"{case_id}: replicate IDs must be the canonical frozen set")

        case_baseline_scores: list[float] = []
        case_candidate_scores: list[float] = []
        case_baseline_dimensions: dict[str, list[float]] = defaultdict(list)
        case_candidate_dimensions: dict[str, list[float]] = defaultdict(list)
        case_family_baseline: dict[str, list[float]] = defaultdict(list)
        case_family_candidate: dict[str, list[float]] = defaultdict(list)
        case_position_baseline: dict[str, list[float]] = defaultdict(list)
        case_position_candidate: dict[str, list[float]] = defaultdict(list)
        replicate_results: list[dict[str, Any]] = []
        baseline_flags = 0
        candidate_flags = 0

        for replicate in sorted(replicates, key=lambda item: item["replicate_id"]):
            if set(replicate) != {
                "replicate_id",
                "baseline_artifact",
                "baseline_sha256",
                "candidate_artifact",
                "candidate_sha256",
                "generation_runs",
                "judge_runs",
            }:
                raise GateError(f"{case_id}: replicate has an invalid shape")
            replicate_id = replicate["replicate_id"]
            baseline_path, baseline_sha = _artifact(
                replicate.get("baseline_artifact"),
                root,
                replicate.get("baseline_sha256"),
                f"{case_id} {replicate_id} baseline artifact",
            )
            candidate_path, candidate_sha = _artifact(
                replicate.get("candidate_artifact"),
                root,
                replicate.get("candidate_sha256"),
                f"{case_id} {replicate_id} candidate artifact",
            )
            if baseline_sha == candidate_sha:
                raise GateError(
                    f"{case_id} {replicate_id}: baseline and candidate artifacts are identical"
                )
            artifact_identities = (
                (baseline_sha, _canonical_text_sha256(baseline_path)),
                (candidate_sha, _canonical_text_sha256(candidate_path)),
            )
            for artifact_sha, content_sha in artifact_identities:
                if artifact_sha in seen_artifact_shas:
                    raise GateError(
                        "artifacts must be content-distinct across cases, arms, and replicates"
                    )
                if content_sha in seen_artifact_content_shas:
                    raise GateError(
                        "artifacts must be substantively content-distinct across cases, "
                        "arms, and replicates"
                    )
                seen_artifact_shas.add(artifact_sha)
                seen_artifact_content_shas.add(content_sha)
            if _text_leaks(candidate_path, leakage_terms):
                candidate_leakage[case_id] = True

            generation_runs = replicate.get("generation_runs")
            if not isinstance(generation_runs, dict) or set(generation_runs) != {
                "baseline",
                "candidate",
            }:
                raise GateError(
                    f"{case_id} {replicate_id}: both generation receipts are required"
                )
            for arm, artifact_sha, source_sha in (
                ("baseline", baseline_sha, baseline_bundle_sha),
                ("candidate", candidate_sha, candidate_bundle_sha),
            ):
                generation_run = generation_runs[arm]
                if not isinstance(generation_run, dict) or set(generation_run) != {
                    "run_id",
                    "receipt",
                    "receipt_sha256",
                }:
                    raise GateError(
                        f"{case_id} {replicate_id}: {arm} generation run is invalid"
                    )
                run_id = generation_run.get("run_id")
                if (
                    not isinstance(run_id, str)
                    or not run_id
                    or run_id in seen_generation_run_ids
                ):
                    raise GateError("generation run IDs must be globally unique")
                seen_generation_run_ids.add(run_id)
                receipt_path, _ = _artifact(
                    generation_run.get("receipt"),
                    root,
                    generation_run.get("receipt_sha256"),
                    f"{case_id} {replicate_id} {arm} generation receipt",
                )
                _validated_receipt(
                    receipt_path,
                    expected={
                        "run_id": run_id,
                        "case_id": case_id,
                        "replicate_id": replicate_id,
                        "arm": arm,
                        "model_config": config["generator"],
                        "prompt_sha256": generation_prompt_sha,
                        "artifact_sha256": artifact_sha,
                        "source_bundle_sha256": source_sha,
                        "workspace_policy": config["generation_workspace_policy"],
                        "workspace_bundle_sha256": source_sha,
                    },
                    label=f"{case_id} {replicate_id} {arm} generation",
                )

            runs = replicate.get("judge_runs")
            expected_run_count = len(FAMILIES) * 2
            if not isinstance(runs, list) or len(runs) != expected_run_count:
                raise GateError(
                    f"{case_id} {replicate_id}: exactly {expected_run_count} balanced judge runs are required"
                )
            family_counts: Counter[str] = Counter()
            family_positions: dict[str, Counter[str]] = {
                family: Counter() for family in FAMILIES
            }
            replicate_baseline_scores: list[float] = []
            replicate_candidate_scores: list[float] = []
            replicate_baseline_dimensions: dict[str, list[float]] = defaultdict(list)
            replicate_candidate_dimensions: dict[str, list[float]] = defaultdict(list)
            replicate_family_baseline: dict[str, list[float]] = defaultdict(list)
            replicate_family_candidate: dict[str, list[float]] = defaultdict(list)
            replicate_position_baseline: dict[str, list[float]] = defaultdict(list)
            replicate_position_candidate: dict[str, list[float]] = defaultdict(list)
            replicate_baseline_flags = 0
            replicate_candidate_flags = 0

            for run in runs:
                if not isinstance(run, dict) or set(run) != {
                    "judge_id",
                    "family",
                    "baseline_label",
                    "prompt",
                    "prompt_sha256",
                    "report",
                    "report_sha256",
                    "receipt",
                    "receipt_sha256",
                }:
                    raise GateError(
                        f"{case_id} {replicate_id}: judge run has an invalid shape"
                    )
                judge_id = run.get("judge_id")
                family = run.get("family")
                baseline_label = run.get("baseline_label")
                if (
                    not isinstance(judge_id, str)
                    or not judge_id
                    or judge_id in seen_judge_ids
                ):
                    raise GateError("judge_id must be globally unique and non-empty")
                seen_judge_ids.add(judge_id)
                if family not in FAMILIES:
                    raise GateError(
                        f"{case_id} {replicate_id}: unsupported judge family {family!r}"
                    )
                if baseline_label not in {"A", "B"}:
                    raise GateError(
                        f"{case_id} {replicate_id}: baseline_label must be A or B"
                    )
                candidate_label = "B" if baseline_label == "A" else "A"
                artifact_paths = {
                    baseline_label: baseline_path,
                    candidate_label: candidate_path,
                }
                artifact_shas = {
                    baseline_label: baseline_sha,
                    candidate_label: candidate_sha,
                }
                expected_report_template = _report_template(
                    case_id,
                    replicate_id,
                    judge_id,
                    family,
                    case_spec_sha,
                    artifact_shas,
                )
                expected_judge_prompt = render_judge_prompt(
                    judge_template,
                    rubric,
                    case_spec_path.read_text(encoding="utf-8"),
                    artifact_paths["A"].read_text(encoding="utf-8"),
                    artifact_paths["B"].read_text(encoding="utf-8"),
                    expected_report_template,
                )
                prompt_path, prompt_sha = _artifact(
                    run.get("prompt"),
                    root,
                    run.get("prompt_sha256"),
                    f"{case_id} {replicate_id} judge prompt",
                )
                if prompt_path.read_text(encoding="utf-8") != expected_judge_prompt:
                    raise GateError(
                        f"{case_id} {replicate_id}: judge prompt differs from the trusted template"
                    )
                report_path, report_sha = _artifact(
                    run.get("report"),
                    root,
                    run.get("report_sha256"),
                    f"{case_id} {replicate_id} judge report",
                )
                receipt_path, _ = _artifact(
                    run.get("receipt"),
                    root,
                    run.get("receipt_sha256"),
                    f"{case_id} {replicate_id} judge receipt",
                )
                _validated_receipt(
                    receipt_path,
                    expected={
                        "run_id": judge_id,
                        "case_id": case_id,
                        "replicate_id": replicate_id,
                        "judge_id": judge_id,
                        "family": family,
                        "model_config": config["judges"][family],
                        "prompt_sha256": prompt_sha,
                        "report_sha256": report_sha,
                        "workspace_policy": config["judge_workspace_policy"],
                        "workspace_bundle_sha256": EMPTY_WORKSPACE_SHA256,
                    },
                    label=f"{case_id} {replicate_id} {judge_id}",
                )

                report = _load_object(report_path)
                if set(report) != {
                    "case_id",
                    "replicate_id",
                    "judge_id",
                    "family",
                    "case_spec_sha256",
                    "artifact_sha256",
                    "scores",
                    "evidence",
                    "diagnostics",
                    "preference",
                }:
                    raise GateError(f"{report_path}: report has an invalid shape")
                if (
                    report.get("case_id") != case_id
                    or report.get("replicate_id") != replicate_id
                    or report.get("judge_id") != judge_id
                ):
                    raise GateError(
                        f"{report_path}: report identity does not match manifest"
                    )
                if report.get("family") != family:
                    raise GateError(f"{report_path}: report family does not match manifest")
                if report.get("case_spec_sha256") != case_spec_sha:
                    raise GateError(
                        f"{report_path}: case spec digest does not match manifest"
                    )
                if report.get("artifact_sha256") != artifact_shas:
                    raise GateError(
                        f"{report_path}: artifact labels do not match manifest"
                    )

                scores = report.get("scores")
                evidence = report.get("evidence")
                diagnostics = report.get("diagnostics")
                if not isinstance(scores, dict) or set(scores) != {"A", "B"}:
                    raise GateError(f"{report_path}: scores must contain A and B")
                if not isinstance(evidence, dict) or set(evidence) != {"A", "B"}:
                    raise GateError(f"{report_path}: evidence must contain A and B")
                if not isinstance(diagnostics, dict) or set(diagnostics) != {"A", "B"}:
                    raise GateError(f"{report_path}: diagnostics must contain A and B")
                for label in ("A", "B"):
                    per_dimension = evidence[label]
                    if (
                        not isinstance(per_dimension, dict)
                        or set(per_dimension) != set(DIMENSIONS)
                        or not all(
                            isinstance(text, str) and text.strip()
                            for text in per_dimension.values()
                        )
                    ):
                        raise GateError(
                            f"{report_path}: {label} needs evidence for every dimension"
                        )

                score_a = _validated_scores(scores["A"], report=report_path, label="A")
                score_b = _validated_scores(scores["B"], report=report_path, label="B")
                total_a = sum(score_a.values())
                total_b = sum(score_b.values())
                flags_a = _validated_diagnostics(
                    diagnostics["A"], report=report_path, label="A"
                )
                flags_b = _validated_diagnostics(
                    diagnostics["B"], report=report_path, label="B"
                )
                expected_preference = (
                    "TIE" if total_a == total_b else ("A" if total_a > total_b else "B")
                )
                if report.get("preference") != expected_preference:
                    raise GateError(
                        f"{report_path}: preference conflicts with total scores"
                    )

                baseline_score = score_a if baseline_label == "A" else score_b
                candidate_score = score_b if baseline_label == "A" else score_a
                baseline_total = sum(baseline_score.values())
                candidate_total = sum(candidate_score.values())
                family_counts[family] += 1
                family_positions[family][baseline_label] += 1
                replicate_baseline_scores.append(baseline_total)
                replicate_candidate_scores.append(candidate_total)
                replicate_family_baseline[family].append(baseline_total)
                replicate_family_candidate[family].append(candidate_total)
                replicate_position_baseline[baseline_label].append(baseline_total)
                replicate_position_candidate[baseline_label].append(candidate_total)
                for dimension in DIMENSIONS:
                    replicate_baseline_dimensions[dimension].append(
                        baseline_score[dimension]
                    )
                    replicate_candidate_dimensions[dimension].append(
                        candidate_score[dimension]
                    )
                replicate_baseline_flags += (
                    flags_a if baseline_label == "A" else flags_b
                )
                replicate_candidate_flags += (
                    flags_b if baseline_label == "A" else flags_a
                )

            if family_counts != Counter({family: 2 for family in FAMILIES}):
                raise GateError(
                    f"{case_id} {replicate_id}: each judge family must run exactly twice"
                )
            if any(
                positions != Counter({"A": 1, "B": 1})
                for positions in family_positions.values()
            ):
                raise GateError(
                    f"{case_id} {replicate_id}: each family must swap baseline A/B position"
                )

            replicate_baseline_mean = _mean(replicate_baseline_scores)
            replicate_candidate_mean = _mean(replicate_candidate_scores)
            case_baseline_scores.append(replicate_baseline_mean)
            case_candidate_scores.append(replicate_candidate_mean)
            for dimension in DIMENSIONS:
                case_baseline_dimensions[dimension].append(
                    _mean(replicate_baseline_dimensions[dimension])
                )
                case_candidate_dimensions[dimension].append(
                    _mean(replicate_candidate_dimensions[dimension])
                )
            for family in FAMILIES:
                case_family_baseline[family].append(
                    _mean(replicate_family_baseline[family])
                )
                case_family_candidate[family].append(
                    _mean(replicate_family_candidate[family])
                )
            for label in ("A", "B"):
                case_position_baseline[label].append(
                    _mean(replicate_position_baseline[label])
                )
                case_position_candidate[label].append(
                    _mean(replicate_position_candidate[label])
                )
            baseline_flags += replicate_baseline_flags
            candidate_flags += replicate_candidate_flags
            replicate_results.append(
                {
                    "replicate_id": replicate_id,
                    "baseline_mean": round(replicate_baseline_mean, 4),
                    "candidate_mean": round(replicate_candidate_mean, 4),
                    "score_change": round(
                        replicate_candidate_mean - replicate_baseline_mean, 4
                    ),
                    "baseline_overfit_flags": replicate_baseline_flags,
                    "candidate_overfit_flags": replicate_candidate_flags,
                }
            )

        baseline_mean = _mean(case_baseline_scores)
        candidate_mean = _mean(case_candidate_scores)
        all_baseline_scores.append(baseline_mean)
        all_candidate_scores.append(candidate_mean)
        for family in FAMILIES:
            family_baseline_mean = _mean(case_family_baseline[family])
            family_candidate_mean = _mean(case_family_candidate[family])
            aggregate_scores["family"][family].append(family_baseline_mean)
            aggregate_scores["family_candidate"][family].append(
                family_candidate_mean
            )
        for label in ("A", "B"):
            position_baseline_mean = _mean(case_position_baseline[label])
            position_candidate_mean = _mean(case_position_candidate[label])
            aggregate_scores["position"][label].append(position_baseline_mean)
            aggregate_scores["position_candidate"][label].append(
                position_candidate_mean
            )
        for dimension in DIMENSIONS:
            dimension_baseline[dimension].append(
                _mean(case_baseline_dimensions[dimension])
            )
            dimension_candidate[dimension].append(
                _mean(case_candidate_dimensions[dimension])
            )

        dimension_changes = {
            dimension: round(
                _mean(case_candidate_dimensions[dimension])
                - _mean(case_baseline_dimensions[dimension]),
                4,
            )
            for dimension in DIMENSIONS
        }
        family_deltas = {
            family: round(
                _mean(case_family_candidate[family])
                - _mean(case_family_baseline[family]),
                4,
            )
            for family in sorted(FAMILIES)
        }
        case_results.append(
            {
                "case_id": case_id,
                "split": case["split"],
                "genre": case["genre"],
                "negative_control": case["negative_control"],
                "replicate_count": replicate_count,
                "replicates": replicate_results,
                "baseline_mean": round(baseline_mean, 4),
                "candidate_mean": round(candidate_mean, 4),
                "score_change": round(candidate_mean - baseline_mean, 4),
                "non_material_regression": candidate_mean
                >= baseline_mean - MAX_CASE_REGRESSION,
                "dimension_changes": dimension_changes,
                "family_score_changes": family_deltas,
                "family_direction_agreement": family_deltas["codex"]
                * family_deltas["kimi"]
                >= 0,
                "position_score_changes": {
                    label: round(
                        _mean(case_position_candidate[label])
                        - _mean(case_position_baseline[label]),
                        4,
                    )
                    for label in ("A", "B")
                },
                "baseline_overfit_flags": baseline_flags,
                "candidate_overfit_flags": candidate_flags,
            }
        )
        baseline_flags_total += baseline_flags
        candidate_flags_total += candidate_flags

    if seen_case_ids != set(registry):
        raise GateError("manifest case IDs do not match the trusted corpus")
    genres = {case["genre"] for case in registry.values()}
    split_counts = Counter(case["split"] for case in registry.values())
    mechanism_counts = Counter(
        mechanism
        for case in registry.values()
        for mechanism, enabled in case["mechanisms"].items()
        if enabled
    )
    negative_control_count = sum(
        1 for case in registry.values() if case["negative_control"]
    )
    if len(genres) < MIN_GENRES:
        raise GateError(f"at least {MIN_GENRES} distinct genres are required")
    if any(split_counts[split] < MIN_SPLIT_CASES for split in SPLITS):
        raise GateError(f"each split requires at least {MIN_SPLIT_CASES} cases")
    if negative_control_count < MIN_NEGATIVE_CONTROLS:
        raise GateError(f"at least {MIN_NEGATIVE_CONTROLS} negative controls are required")
    if any(
        mechanism_counts[mechanism] < MIN_CASES_PER_MECHANISM
        for mechanism in MECHANISMS
    ):
        raise GateError(
            f"each target mechanism requires at least {MIN_CASES_PER_MECHANISM} cases"
        )

    split_results: dict[str, dict[str, Any]] = {}
    for split in sorted(SPLITS):
        split_cases = [item for item in case_results if item["split"] == split]
        baseline = [item["baseline_mean"] for item in split_cases]
        candidate = [item["candidate_mean"] for item in split_cases]
        summary = _score_summary(baseline, candidate)
        summary["case_count"] = len(split_cases)
        summary["strict_score_non_regression"] = _mean(candidate) >= _mean(baseline)
        summary["baseline_overfit_flags"] = sum(
            item["baseline_overfit_flags"] for item in split_cases
        )
        summary["candidate_overfit_flags"] = sum(
            item["candidate_overfit_flags"] for item in split_cases
        )
        summary["overfit_non_increase"] = (
            summary["candidate_overfit_flags"] <= summary["baseline_overfit_flags"]
        )
        split_results[split] = summary

    family_results = {
        family: _score_summary(
            aggregate_scores["family"][family],
            aggregate_scores["family_candidate"][family],
        )
        for family in sorted(FAMILIES)
    }
    position_results = {
        label: _score_summary(
            aggregate_scores["position"][label],
            aggregate_scores["position_candidate"][label],
        )
        for label in ("A", "B")
    }
    dimension_results: dict[str, dict[str, Any]] = {}
    for dimension in DIMENSIONS:
        baseline_mean = _mean(dimension_baseline[dimension])
        candidate_mean = _mean(dimension_candidate[dimension])
        dimension_results[dimension] = {
            "baseline_mean": round(baseline_mean, 4),
            "candidate_mean": round(candidate_mean, 4),
            "score_change": round(candidate_mean - baseline_mean, 4),
            "non_regression": candidate_mean
            >= baseline_mean - MAX_DIMENSION_REGRESSION,
        }

    baseline_macro = _mean(all_baseline_scores)
    candidate_macro = _mean(all_candidate_scores)
    negative_control_candidate_flags = sum(
        item["candidate_overfit_flags"]
        for item in case_results
        if item["negative_control"]
    )
    passed = (
        candidate_macro >= baseline_macro
        and all(item["non_material_regression"] for item in case_results)
        and all(
            item["strict_score_non_regression"] for item in split_results.values()
        )
        and all(item["non_regression"] for item in family_results.values())
        and all(item["non_regression"] for item in position_results.values())
        and all(item["non_regression"] for item in dimension_results.values())
        and candidate_flags_total <= baseline_flags_total
        and all(item["overfit_non_increase"] for item in split_results.values())
        and negative_control_candidate_flags == 0
        and not candidate_leakage
        and not release_leakage_files
    )
    return {
        "passed": passed,
        "schema_version": MANIFEST_SCHEMA,
        "corpus_id": manifest["corpus_id"],
        "seal_id": seal["seal_id"],
        "case_count": len(case_results),
        "genre_count": len(genres),
        "negative_control_count": negative_control_count,
        "generation_replicates": config["generation_replicates"],
        "baseline_macro_mean": round(baseline_macro, 4),
        "candidate_macro_mean": round(candidate_macro, 4),
        "score_change": round(candidate_macro - baseline_macro, 4),
        "baseline_overfit_flags": baseline_flags_total,
        "candidate_overfit_flags": candidate_flags_total,
        "negative_control_candidate_overfit_flags": negative_control_candidate_flags,
        "candidate_leakage_cases": sorted(candidate_leakage),
        "release_leakage_files": release_leakage_files,
        "splits": split_results,
        "judge_families": family_results,
        "baseline_positions": position_results,
        "dimensions": dimension_results,
        "cases": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--trusted-leakage-terms",
        required=True,
        type=Path,
        help="maintainer-controlled exact-term file, supplied outside the manifest",
    )
    parser.add_argument(
        "--trusted-seal",
        required=True,
        type=Path,
        help="maintainer-controlled frozen seal, supplied outside the manifest",
    )
    args = parser.parse_args()
    try:
        result = evaluate(
            args.manifest,
            leakage_terms_path=args.trusted_leakage_terms,
            trusted_seal_path=args.trusted_seal,
        )
    except (GateError, FileNotFoundError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
