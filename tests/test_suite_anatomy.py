import importlib.util
import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.external_validator import ValidatorUnavailable, run_official_validator


SUITE = Path(__file__).resolve().parents[1]
CORE = SUITE / "skills/short-drama"
VERIFY = SUITE / "tools/verify_suite.py"
SPEC = importlib.util.spec_from_file_location("short_drama_verify_suite", VERIFY)
assert SPEC and SPEC.loader
verify_suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_suite)

EXPECTED_SKILLS = {
    "short-drama",
    "short-drama-develop",
    "short-drama-write",
    "short-drama-assets",
    "short-drama-image-prompts",
    "short-drama-storyboard",
    "short-drama-video-prompts",
    "short-drama-review",
}


def parse_openai_interface(path: Path) -> dict[str, str]:
    """Parse the deliberately tiny openai.yaml surface without a YAML dependency."""

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "interface:":
        raise AssertionError(f"{path}: expected a single top-level interface mapping")
    result: dict[str, str] = {}
    for line in lines[1:]:
        match = re.fullmatch(r'  ([a-z_]+):\s+("(?:[^"\\]|\\.)*")', line)
        if not match:
            raise AssertionError(f"{path}: unsupported metadata line {line!r}")
        result[match.group(1)] = json.loads(match.group(2))
    return result


def local_markdown_targets(markdown: Path) -> list[str]:
    pattern = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
    return [target for target in pattern.findall(markdown.read_text(encoding="utf-8")) if "://" not in target]


class SuiteAnatomyTests(unittest.TestCase):
    def test_manifest_and_child_refs_resolve_from_skill_paths(self) -> None:
        result = verify_suite.verify_suite(CORE)
        self.assertEqual(len(result["checked_skills"]), 8)

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
            self.assertEqual(len(result["checked_skills"]), 8)

    def test_exact_public_skill_set(self) -> None:
        actual = {path.name for path in (SUITE / "skills").iterdir() if path.is_dir()}
        self.assertEqual(actual, EXPECTED_SKILLS)

        manifest = json.loads((CORE / "suite-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["public_skills"]), EXPECTED_SKILLS)
        self.assertEqual(len(manifest["public_skills"]), len(EXPECTED_SKILLS))

    def test_every_skill_passes_official_quick_validate(self) -> None:
        for name in sorted(EXPECTED_SKILLS):
            with self.subTest(skill=name):
                try:
                    completed = run_official_validator(SUITE / "skills" / name)
                except FileNotFoundError:
                    self.skipTest(
                        "Codex skill-creator quick_validate.py is unavailable on this machine"
                    )
                except ValidatorUnavailable as error:
                    self.skipTest(f"quick_validate.py cannot run here: {error}")
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{name}: {completed.stdout}{completed.stderr}",
                )

    def test_openai_metadata_matches_each_skill(self) -> None:
        required = {"display_name", "short_description", "default_prompt"}
        for name in sorted(EXPECTED_SKILLS):
            with self.subTest(skill=name):
                metadata = parse_openai_interface(
                    SUITE / "skills" / name / "agents/openai.yaml"
                )
                self.assertEqual(set(metadata), required)
                self.assertTrue(metadata["display_name"].strip())
                self.assertLessEqual(len(metadata["display_name"]), 64)
                self.assertGreaterEqual(len(metadata["short_description"]), 25)
                self.assertLessEqual(len(metadata["short_description"]), 64)
                self.assertIn(f"${name}", metadata["default_prompt"])
                self.assertEqual(len(metadata["default_prompt"].splitlines()), 1)

    def test_skill_bodies_have_no_todos_and_remain_bounded(self) -> None:
        for skill in (SUITE / "skills").iterdir():
            body = (skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("TODO", body, skill.name)
            self.assertLessEqual(len(body.splitlines()), 250, skill.name)

    def test_router_description_defers_explicit_craft_requests_to_child_skills(self) -> None:
        lines = (CORE / "SKILL.md").read_text(encoding="utf-8").splitlines()
        description_line = next(
            line for line in lines if line.startswith("description: ")
        )
        description_value = description_line.removeprefix("description: ")
        description = (
            json.loads(description_value)
            if description_value.startswith('"')
            else description_value
        )

        self.assertIn("意图不明确", description)
        for child_trigger in (
            "直接要求写短剧",
            "拆资产",
            "写资产图片提示词",
            "拆分镜",
            "写视频提示词",
            "审查短剧",
        ):
            with self.subTest(child_trigger=child_trigger):
                self.assertNotIn(child_trigger, description)

    def test_readmes_stay_creator_facing(self) -> None:
        disallowed_sections = {
            "## 验证与开发",
            "## 许可证",
            "## Verify & develop",
            "## License",
        }
        for name in ("README.md", "README_EN.md"):
            with self.subTest(readme=name):
                headings = {
                    line.strip()
                    for line in (SUITE / name).read_text(encoding="utf-8").splitlines()
                    if line.startswith("## ")
                }
                self.assertTrue(disallowed_sections.isdisjoint(headings))

        self.assertRegex(
            (SUITE / "README.md").read_text(encoding="utf-8"),
            r"不调用真正的\s*图片、视频或音频生成服务",
        )
        english_readme = re.sub(
            r"\s+",
            " ",
            (SUITE / "README_EN.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "does not call image, video, or audio generation services",
            english_readme,
        )

    def test_markdown_links_resolve_one_hop(self) -> None:
        for markdown in (SUITE / "skills").glob("*/SKILL.md"):
            for target in local_markdown_targets(markdown):
                self.assertTrue((markdown.parent / target).is_file(), f"{markdown}: {target}")

    def test_every_reference_is_named_one_hop_from_owning_skill(self) -> None:
        """Progressive disclosure must not hide craft behind an unlinked manual."""

        for name in sorted(EXPECTED_SKILLS):
            skill = SUITE / "skills" / name
            body = (skill / "SKILL.md").read_text(encoding="utf-8")
            for reference in sorted((skill / "references").glob("*.md")):
                relative = reference.relative_to(skill).as_posix()
                with self.subTest(skill=name, reference=relative):
                    self.assertIn(relative, body)

    def test_long_references_expose_a_contents_navigation_near_the_top(self) -> None:
        """A preview of a long reference must reveal its available sections."""

        navigation_headings = {"## 内容导航", "## 目录", "## Contents"}
        for reference in sorted((SUITE / "skills").glob("*/references/*.md")):
            lines = reference.read_text(encoding="utf-8").splitlines()
            if len(lines) <= 100:
                continue
            with self.subTest(reference=reference.relative_to(SUITE)):
                self.assertTrue(navigation_headings.intersection(lines[:15]))

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

        fenced_templates = [
            SUITE / "skills/short-drama-image-prompts/assets/image-prompt-spec.jsonl.md",
            SUITE / "skills/short-drama-video-prompts/assets/motion-spec.jsonl.md",
        ]
        for path in fenced_templates:
            match = re.search(r"```json\n(\{.*?\})\n```", path.read_text(encoding="utf-8"), re.S)
            if match is None:
                self.fail(f"{path}: missing fenced JSON template")
            check(json.loads(match.group(1)), path)

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
                            if artifact.startswith(("bible/", "episodes/")):
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
        self.assertEqual(authority["decisions_artifact"], "creator-decisions/")
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

    def test_review_requires_fresh_context_or_transparent_provisional_fallback(self) -> None:
        review = (SUITE / "skills/short-drama-review/SKILL.md").read_text(encoding="utf-8")
        router = (SUITE / "skills/short-drama/SKILL.md").read_text(encoding="utf-8")
        for text in (review, router):
            self.assertIn("fresh", text)
            self.assertIn("PROVISIONAL", text)
        self.assertIn("effective_review_mode", review)

    def test_router_separates_template_diagnosis_from_owner_revision(self) -> None:
        router = (SUITE / "skills/short-drama/SKILL.md").read_text(encoding="utf-8")
        examples = (
            SUITE / "skills/short-drama/references/routing-examples.md"
        ).read_text(encoding="utf-8")
        for text in (router, examples):
            self.assertIn("诊断模板感", text)
            self.assertIn("去 AI 味", text)
            self.assertIn("fresh re-review", text)

    def test_script_first_writing_resolves_genre_boundaries_without_a_sibling_skill(
        self,
    ) -> None:
        """Script-first entry still needs the genre boundary, but self-contained."""

        skill_dir = SUITE / "skills/short-drama-write"
        write = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("../short-drama-develop/", write)
        self.assertIn("references/stage-contract.md", write)
        contract = (skill_dir / "references/stage-contract.md").read_text(encoding="utf-8")
        self.assertIn("## 单集契约与题材边界（上游输入）", contract)
        for concept in ("write_standalone", "本阶段执行，不分类", "进入状态", "信息权限"):
            with self.subTest(concept=concept):
                self.assertIn(concept, contract)

    def test_media_generation_boundary_is_a_verified_product_policy(self) -> None:
        manifest = json.loads((CORE / "suite-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["trust_boundary"],
            {
                "host_text_inference": True,
                "suite_scripts_outbound_network": False,
                "media_generation": False,
                "provider_api_calls": False,
                "private_source_runtime_access": False,
            },
        )

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
        self.assertEqual(light_ref["artifact"], "bible/location-views.jsonl")
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
        self.assertEqual(policy_ref["artifact"], "bible/props.jsonl")
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
