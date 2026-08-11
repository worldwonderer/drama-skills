import importlib.util
import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

SUITE = Path(__file__).resolve().parents[1]
CORE = SUITE / "skills/short-drama"
VERIFY = SUITE / "tools/verify_suite.py"
SPEC = importlib.util.spec_from_file_location("short_drama_verify_suite", VERIFY)
assert SPEC and SPEC.loader
verify_suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_suite)

EXPECTED_SKILLS = {
    "short-drama",
    "short-drama-novel-analyze",
    "short-drama-develop",
    "short-drama-write",
    "short-drama-assets",
    "short-drama-image-prompts",
    "short-drama-storyboard",
    "short-drama-video-prompts",
    "short-drama-review",
}


def local_markdown_targets(markdown: Path) -> list[str]:
    pattern = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
    return [target for target in pattern.findall(markdown.read_text(encoding="utf-8")) if "://" not in target]


def fenced_json(path: Path) -> dict[str, Any]:
    match = re.search(
        r"```json\n(\{.*?\})\n```", path.read_text(encoding="utf-8"), re.S
    )
    if match is None:
        raise AssertionError(f"{path}: missing fenced JSON object")
    document = json.loads(match.group(1))
    if not isinstance(document, dict):
        raise AssertionError(f"{path}: fenced JSON must be an object")
    return document


def resolve_json_pointer(document: object, pointer: str) -> object:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"not an RFC 6901 pointer: {pointer}")
    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif (
            isinstance(current, list)
            and re.fullmatch(r"(?:0|[1-9][0-9]*)", token) is not None
            and int(token) < len(current)
        ):
            current = current[int(token)]
        else:
            raise KeyError(pointer)
    return current


class SuiteAnatomyTests(unittest.TestCase):
    def test_verifier_rejects_child_content_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "skills"
            shutil.copytree(SUITE / "skills", copied)
            target = copied / "short-drama-write/SKILL.md"
            target.write_text(
                target.read_text(encoding="utf-8") + "\nunauthorized mutation\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "content hash mismatch"):
                verify_suite.verify_suite(copied / "short-drama")

    def test_verifier_rejects_unmanifested_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "skills"
            shutil.copytree(SUITE / "skills", copied)
            stray = copied / "short-drama/scripts/native_helper.so"
            stray.write_bytes(b"not a release artifact")
            with self.assertRaisesRegex(ValueError, "unexpected suite files"):
                verify_suite.verify_suite(copied / "short-drama")

    def test_verifier_tolerates_local_bytecode_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "skills"
            shutil.copytree(SUITE / "skills", copied)
            cache = copied / "short-drama/scripts/__pycache__/project_tool.pyc"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(b"local bytecode cache")
            result = verify_suite.verify_suite(copied / "short-drama")
            self.assertEqual(len(result["checked_skills"]), len(EXPECTED_SKILLS))

    def test_exact_public_skill_set(self) -> None:
        actual = {path.name for path in (SUITE / "skills").iterdir() if path.is_dir()}
        self.assertEqual(actual, EXPECTED_SKILLS)

        manifest = json.loads((CORE / "suite-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["public_skills"]), EXPECTED_SKILLS)
        self.assertEqual(len(manifest["public_skills"]), len(EXPECTED_SKILLS))

    def test_markdown_links_resolve_one_hop(self) -> None:
        for markdown in (SUITE / "skills").glob("*/SKILL.md"):
            for target in local_markdown_targets(markdown):
                self.assertTrue((markdown.parent / target).is_file(), f"{markdown}: {target}")

    def test_deterministic_validators_are_linked_from_their_owning_skills(self) -> None:
        expected = {
            "short-drama-storyboard": {"scripts/storyboard_check.py"},
            "short-drama-video-prompts": {
                "scripts/container_check.py",
                "scripts/motion_timing_check.py",
            },
            "short-drama-write": {"scripts/voice_sheet_check.py"},
            "short-drama-novel-analyze": {"scripts/novel_index.py"},
        }
        for skill, validator_links in expected.items():
            skill_md = SUITE / "skills" / skill / "SKILL.md"
            linked_targets = set(local_markdown_targets(skill_md))
            self.assertTrue(
                validator_links <= linked_targets,
                f"{skill}: unlinked validators {sorted(validator_links - linked_targets)}",
            )

    def test_directly_linked_markdown_has_no_broken_second_hop(self) -> None:
        for skill_md in (SUITE / "skills").glob("*/SKILL.md"):
            for target in local_markdown_targets(skill_md):
                first_hop = (skill_md.parent / target).resolve()
                if first_hop.suffix.lower() != ".md":
                    continue
                for nested_target in local_markdown_targets(first_hop):
                    self.assertTrue(
                        (first_hop.parent / nested_target).is_file(),
                        f"{first_hop}: {nested_target}",
                    )

    def test_shipping_json_is_valid(self) -> None:
        for path in SUITE.rglob("*.json"):
            with self.subTest(path=path):
                json.loads(path.read_text(encoding="utf-8"))

    def test_structured_artifact_refs_use_one_canonical_shape(self) -> None:
        """Cross-layer stale propagation needs one parseable pointer contract."""

        legacy_keys = {"artifact_hash", "field_pointer", "accepted_snapshot_hash"}

        def check(document: object, path: Path, cursor: str = "") -> None:
            if isinstance(document, dict):
                self.assertFalse(
                    legacy_keys.intersection(document),
                    f"{path}:{cursor} uses a legacy reference alias",
                )
                for key, value in document.items():
                    if (
                        key != "boundary_refs"
                        and (key.endswith("_ref") or key.endswith("_refs"))
                        and value is not None
                    ):
                        references = value if isinstance(value, list) else [value]
                        for reference in references:
                            self.assertIsInstance(reference, dict, f"{path}:{cursor}/{key}")
                            self.assertTrue(
                                {"owner", "artifact", "hash"}.issubset(reference),
                                f"{path}:{cursor}/{key} is not a canonical ArtifactRef",
                            )
                    if key == "boundary_refs":
                        self.assertIsInstance(value, dict, f"{path}:{cursor}/{key}")
                        for reference in value.values():
                            self.assertTrue(
                                isinstance(reference, dict)
                                and {"owner", "artifact", "hash"}.issubset(reference),
                                f"{path}:{cursor}/{key} contains a noncanonical ref",
                            )
                    check(value, path, f"{cursor}/{key}")
            elif isinstance(document, list):
                for index, value in enumerate(document):
                    check(value, path, f"{cursor}/{index}")

        for path in (SUITE / "skills").rglob("*"):
            if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
                continue
            if path.suffix == ".jsonl":
                documents = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            else:
                documents = [json.loads(path.read_text(encoding="utf-8"))]
            for document in documents:
                check(document, path)

        for path in sorted((SUITE / "skills").glob("*/assets/*.md")):
            for raw_document in re.findall(
                r"```json\n(\{.*?\})\n```",
                path.read_text(encoding="utf-8"),
                re.S,
            ):
                check(json.loads(raw_document), path)

    def test_cross_template_json_pointers_resolve(self) -> None:
        project = json.loads(
            (CORE / "assets/project-template/short-drama.json").read_text(
                encoding="utf-8"
            )
        )
        shot = json.loads(
            (
                SUITE
                / "skills/short-drama-storyboard/assets/shot-template.jsonl"
            ).read_text(encoding="utf-8")
        )
        documents = {
            "delivery_container": fenced_json(
                SUITE
                / "skills/short-drama-video-prompts/assets/delivery-container.jsonl.md"
            ),
            "motion": fenced_json(
                SUITE
                / "skills/short-drama-video-prompts/assets/motion-spec.jsonl.md"
            ),
            "shot": shot,
            "keyframe": json.loads(
                (
                    SUITE
                    / "skills/short-drama-storyboard/assets/keyframe-template.jsonl"
                ).read_text(encoding="utf-8")
            ),
            "coverage": json.loads(
                (
                    SUITE
                    / "skills/short-drama-storyboard/assets/coverage-template.json"
                ).read_text(encoding="utf-8")
            ),
        }
        targets = {
            "short-drama.json": project,
            "剧集/<EP>/storyboard/shots.jsonl": shot,
        }
        for path in sorted(
            (SUITE / "skills/short-drama-assets/assets").glob("*.jsonl")
        ):
            for line in path.read_text(encoding="utf-8").splitlines():
                example = json.loads(line)
                destination = example.get("destination")
                if isinstance(destination, str):
                    targets.setdefault(destination, example)

        for path in sorted((SUITE / "skills").glob("*/assets/*.md")):
            for index, raw_document in enumerate(
                re.findall(
                    r"```json\n(\{.*?\})\n```",
                    path.read_text(encoding="utf-8"),
                    re.S,
                )
            ):
                documents.setdefault(f"{path.relative_to(SUITE)}:{index}", json.loads(raw_document))

        expected_refs = [
            (
                "delivery_container",
                ("members", 0, "accepted_duration_ref"),
                "short-drama-storyboard",
                "剧集/<EP>/storyboard/shots.jsonl",
                "/duration_seconds",
            ),
            (
                "delivery_container",
                ("members", 0, "location_binding_ref"),
                "short-drama-storyboard",
                "剧集/<EP>/storyboard/shots.jsonl",
                "/location_binding",
            ),
            (
                "delivery_container",
                ("members", 0, "asset_bindings_ref"),
                "short-drama-storyboard",
                "剧集/<EP>/storyboard/shots.jsonl",
                "/asset_bindings",
            ),
            (
                "shot",
                ("delivery_surface_ref",),
                "short-drama",
                "short-drama.json",
                "/creator_authority/delivery_surface",
            ),
            (
                "keyframe",
                ("delivery_surface_ref",),
                "short-drama",
                "short-drama.json",
                "/creator_authority/delivery_surface",
            ),
            (
                "coverage",
                ("episode_duration", "target_ref"),
                "short-drama",
                "short-drama.json",
                "/format/target_seconds_per_episode",
            ),
            (
                "keyframe",
                ("boundary_ref",),
                "short-drama-storyboard",
                "剧集/<EP>/storyboard/shots.jsonl",
                "/start_boundary | /end_boundary",
            ),
        ]

        for source, ref_path, owner, artifact, field in expected_refs:
            value: object = documents[source]
            for token in ref_path:
                if isinstance(token, int):
                    if not isinstance(value, list):
                        self.fail(f"{source}{ref_path}: expected list at {token}")
                    value = value[token]
                else:
                    if not isinstance(value, dict):
                        self.fail(f"{source}{ref_path}: expected object at {token}")
                    value = value[token]
            if not isinstance(value, dict):
                self.fail(f"{source}{ref_path}: expected artifact reference")
            self.assertEqual(value.get("owner"), owner)
            self.assertEqual(value.get("artifact"), artifact)
            self.assertEqual(value.get("field"), field)

        def check(value: object) -> None:
            if isinstance(value, dict):
                artifact = value.get("artifact")
                field = value.get("field")
                if (
                    isinstance(artifact, str)
                    and artifact in targets
                    and isinstance(field, str)
                ):
                    for pointer in (part.strip() for part in field.split("|")):
                        with self.subTest(artifact=artifact, pointer=pointer):
                            resolve_json_pointer(targets[artifact], pointer)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        for document in documents.values():
            check(document)

    def test_delivery_container_and_motion_hash_refs_are_acyclic(self) -> None:
        templates = {
            "剧集/<EP>/storyboard/delivery-containers.jsonl": fenced_json(
                SUITE
                / "skills/short-drama-video-prompts/assets/delivery-container.jsonl.md"
            ),
            "剧集/<EP>/storyboard/motion-specs.jsonl": fenced_json(
                SUITE
                / "skills/short-drama-video-prompts/assets/motion-spec.jsonl.md"
            ),
        }
        edges: dict[str, set[str]] = {source: set() for source in templates}

        def collect(source: str, value: object) -> None:
            if isinstance(value, dict):
                artifact = value.get("artifact")
                digest = value.get("hash")
                if (
                    isinstance(artifact, str)
                    and artifact in templates
                    and isinstance(digest, str)
                ):
                    edges[source].add(artifact)
                for child in value.values():
                    collect(source, child)
            elif isinstance(value, list):
                for child in value:
                    collect(source, child)

        for source, document in templates.items():
            collect(source, document)

        container_file = "剧集/<EP>/storyboard/delivery-containers.jsonl"
        motion_file = "剧集/<EP>/storyboard/motion-specs.jsonl"
        self.assertIn(motion_file, edges[container_file])

        def visit(node: str, active: tuple[str, ...], complete: set[str]) -> None:
            if node in active:
                self.fail("hash-reference cycle: " + " -> ".join((*active, node)))
            if node in complete:
                return
            for target in edges[node]:
                visit(target, (*active, node), complete)
            complete.add(node)

        complete: set[str] = set()
        for source in templates:
            visit(source, (), complete)

    def test_asset_template_reference_graph_has_no_self_ref_or_hash_cycle(self) -> None:
        templates = SUITE / "skills/short-drama-assets/assets"
        edges: set[tuple[str, str]] = set()
        for path in templates.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                document = json.loads(line)
                source = document.get("destination")
                if not isinstance(source, str):
                    continue

                def collect(value: object) -> None:
                    if isinstance(value, dict):
                        artifact = value.get("artifact")
                        digest = value.get("hash")
                        if isinstance(artifact, str) and isinstance(digest, str):
                            self.assertNotEqual(
                                artifact,
                                source,
                                f"{path.name} creates an impossible self-hash ref",
                            )
                            if artifact.startswith(
                                ("设定集/", "剧集/", "bible/", "episodes/")
                            ):
                                edges.add((source, artifact))
                        for child in value.values():
                            collect(child)
                    elif isinstance(value, list):
                        for child in value:
                            collect(child)

                collect(document)

        graph: dict[str, set[str]] = {}
        for source, target in edges:
            graph.setdefault(source, set()).add(target)

        def visit(node: str, active: tuple[str, ...], complete: set[str]) -> None:
            if node in active:
                cycle = " -> ".join((*active, node))
                self.fail(f"cross-artifact hash cycle: {cycle}")
            if node in complete:
                return
            for target in graph.get(node, set()):
                visit(target, (*active, node), complete)
            complete.add(node)

        complete: set[str] = set()
        for node in graph:
            visit(node, (), complete)

    def test_candidate_templates_distinguish_inputs_from_same_publication_refs(self) -> None:
        templates: list[tuple[Path, dict[str, Any]]] = []
        for relative in (
            "skills/short-drama-storyboard/assets/coverage-template.json",
            "skills/short-drama-storyboard/assets/shot-template.jsonl",
            "skills/short-drama-storyboard/assets/keyframe-template.jsonl",
        ):
            path = SUITE / relative
            templates.append((path, json.loads(path.read_text(encoding="utf-8"))))
        for relative in (
            "skills/short-drama-image-prompts/assets/image-prompt-spec.jsonl.md",
            "skills/short-drama-video-prompts/assets/motion-spec.jsonl.md",
        ):
            path = SUITE / relative
            match = re.search(r"```json\n(\{.*?\})\n```", path.read_text(encoding="utf-8"), re.S)
            if match is None:
                self.fail(f"{path}: missing fenced JSON template")
            templates.append((path, json.loads(match.group(1))))

        candidate_ref_owners = {
            "coverage-template.json": {"short-drama-storyboard"},
            "shot-template.jsonl": set(),
            "keyframe-template.jsonl": {"short-drama-storyboard"},
            "image-prompt-spec.jsonl.md": set(),
            "motion-spec.jsonl.md": set(),
        }
        for path, document in templates:
            self.assertEqual(document.get("status"), "candidate", path)

            observed_candidate_owners: set[str] = set()

            def check(value: object, cursor: str = "") -> None:
                if isinstance(value, dict):
                    if {"owner", "artifact", "hash"}.issubset(value):
                        authority = value.get("authority")
                        self.assertIn(authority, {None, "candidate"}, f"{path}:{cursor}")
                        if authority == "candidate":
                            observed_candidate_owners.add(str(value["owner"]))
                    for key, child in value.items():
                        check(child, f"{cursor}/{key}")
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        check(child, f"{cursor}/{index}")

            check(document)

            self.assertEqual(
                observed_candidate_owners,
                candidate_ref_owners[path.name],
                f"{path}: candidate authority is only for co-published targets",
            )

        project = json.loads(
            (SUITE / "skills/short-drama/assets/project-template/short-drama.json")
            .read_text(encoding="utf-8")
        )
        authority = project["creator_authority"]
        self.assertEqual(authority["decisions_artifact"], "创作者决策/")
        self.assertEqual(authority["visual_direction"]["status"], "unset")
        self.assertEqual(authority["production_profile"]["status"], "unset")
        self.assertTrue(
            (SUITE / "skills/short-drama/assets/creator-decision.example.jsonl").is_file()
        )

    def test_review_templates_bind_evidence_targets_and_verdict_snapshot(self) -> None:
        finding = json.loads(
            (SUITE / "skills/short-drama-review/assets/finding-template.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        self.assertGreaterEqual(len(finding["evidence_refs"]), 2)
        self.assertTrue({"owner", "artifact", "hash"}.issubset(finding["target_ref"]))
        verdict = json.loads(
            (SUITE / "skills/short-drama-review/assets/verdict-template.json")
            .read_text(encoding="utf-8")
        )
        self.assertTrue(verdict["reviewed_artifacts"])
        self.assertTrue({"owner", "artifact", "hash"}.issubset(verdict["findings_ref"]))
        self.assertEqual(verdict["open_blocker_count"], 0)
        self.assertFalse(verdict["reviewer"]["independent"])
        self.assertEqual(verdict["reviewer"]["kind"], "unattested")
        self.assertEqual(verdict["requested_review_mode"], "independent_agent")
        self.assertEqual(verdict["effective_review_mode"], "unattested")
        self.assertEqual(verdict["structural_validation"], "not_run")
        self.assertEqual(verdict["verdict"], "PROVISIONAL")

    def test_template_field_refs_resolve_to_owned_example_fields(self) -> None:
        keyframe = json.loads(
            (SUITE / "skills/short-drama-storyboard/assets/keyframe-template.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        view = json.loads(
            (SUITE / "skills/short-drama-assets/assets/location-view.example.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[1]
        )
        light_ref = keyframe["light_projection"]["source_ref"]
        self.assertEqual(light_ref["artifact"], "设定集/location-views.jsonl")
        self.assertEqual(light_ref["field"], "/state_differences/light")
        self.assertIn("light", view["state_differences"])

        image_template = (
            SUITE
            / "skills/short-drama-image-prompts/assets/image-prompt-spec.jsonl.md"
        ).read_text(encoding="utf-8")
        match = re.search(r"```json\n(\{.*?\})\n```", image_template, re.S)
        if match is None:
            self.fail("image prompt template is missing fenced JSON")
        image_spec = json.loads(match.group(1))
        policy_ref = image_spec["text_handling"]["source_policy_ref"]
        prop = json.loads(
            (SUITE / "skills/short-drama-assets/assets/prop-state.example.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        self.assertEqual(policy_ref["artifact"], "设定集/props.jsonl")
        self.assertEqual(policy_ref["field"], "/text_policy")
        self.assertIn("text_policy", prop)

    def test_manifest_covers_every_public_file_and_exact_bytes(self) -> None:
        manifest = json.loads((CORE / "suite-manifest.json").read_text(encoding="utf-8"))
        recorded = manifest["files"]
        child_refs = {
            f"{skill}/suite-ref.json"
            for skill in manifest["public_skills"]
            if skill != manifest["core_skill"]
        }
        actual = {
            path.relative_to(SUITE / "skills").as_posix()
            for path in (SUITE / "skills").rglob("*")
            if path.is_file()
            and path != CORE / "suite-manifest.json"
            and "__pycache__" not in path.parts
            and path.relative_to(SUITE / "skills").as_posix() not in child_refs
        }
        self.assertEqual(set(recorded), actual)
        for relative, expected_hash in recorded.items():
            path = SUITE / "skills" / relative
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)


if __name__ == "__main__":
    unittest.main()
