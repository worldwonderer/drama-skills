import json
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SUITE = Path(__file__).resolve().parents[1]
VERIFY_TOOL = SUITE / "tools/verify_suite.py"
UPDATE_TOOL = SUITE / "tools/update_suite_manifest.py"


def import_module_from_path(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_installed_suite(destination: Path) -> Path:
    installed = destination / "CODEX HOME 空格" / "skills"
    shutil.copytree(
        SUITE / "skills",
        installed,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return installed


class InstallationResolutionTests(unittest.TestCase):
    def test_relocated_suite_verifies_from_arbitrary_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            verify = VERIFY_TOOL
            arbitrary_cwd = temp / "unrelated cwd 空格"
            arbitrary_cwd.mkdir()
            clean_home = temp / "clean codex home"
            clean_home.mkdir()
            env = os.environ.copy()
            env["CODEX_HOME"] = str(clean_home)

            completed = subprocess.run(
                [sys.executable, str(verify), str(skills / "short-drama")],
                cwd=arbitrary_cwd,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(len(result["checked_skills"]), 8)

    def test_verifier_checks_each_exposed_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source_a = temp / "source-a/skills"
            source_b = temp / "source-b/skills"
            shutil.copytree(SUITE / "skills", source_a)
            shutil.copytree(SUITE / "skills", source_b)
            changed = source_b / "short-drama-write/SKILL.md"
            changed.write_text(
                changed.read_text(encoding="utf-8") + "\nchanged through mixed install\n",
                encoding="utf-8",
            )
            installed = temp / "installed/skills"
            installed.mkdir(parents=True)
            for skill in source_a.iterdir():
                target = source_b / skill.name if skill.name == "short-drama-write" else skill
                (installed / skill.name).symlink_to(target, target_is_directory=True)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VERIFY_TOOL),
                    str(installed / "short-drama"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("content hash mismatch: short-drama-write/SKILL.md", completed.stderr)

    def test_verifier_ignores_unrelated_sibling_skills(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            unrelated = skills / "unrelated-skill"
            unrelated.mkdir()
            (unrelated / "SKILL.md").write_text(
                "---\nname: unrelated-skill\ndescription: unrelated\n---\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_child_refs_are_relative_and_resolve_to_the_single_sibling_core(self) -> None:
        manifest = (SUITE / "skills/short-drama/suite-manifest.json").resolve()
        manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
        children = [
            child
            for child in (SUITE / "skills").iterdir()
            if child.is_dir() and child.name != "short-drama"
        ]
        self.assertEqual(len(children), 7)
        for child in children:
            with self.subTest(skill=child.name):
                reference = json.loads((child / "suite-ref.json").read_text(encoding="utf-8"))
                declared = Path(reference["core_manifest"])
                self.assertFalse(declared.is_absolute())
                self.assertEqual((child / declared).resolve(), manifest)
                self.assertEqual(reference["recipe_version"], manifest_document["recipe_version"])
                self.assertEqual(reference["core_manifest_sha256"], manifest_hash)
                self.assertNotIn("cwd", json.dumps(reference).casefold())

    def test_direct_child_invocation_discloses_suite_resolution(self) -> None:
        for child in sorted((SUITE / "skills").iterdir()):
            if not child.is_dir() or child.name == "short-drama":
                continue
            with self.subTest(skill=child.name):
                instructions = (child / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("suite-ref.json", instructions)
                self.assertIn("core_manifest", instructions)

    def test_every_entrypoint_discloses_the_shared_runtime_preflight(self) -> None:
        runtime_preflight = SUITE / "skills/short-drama/references/runtime-preflight.md"
        self.assertTrue(runtime_preflight.is_file())
        guidance = runtime_preflight.read_text(encoding="utf-8")
        for command in (
            "suite_verify.py",
            "project_tool.py recover",
            "project_tool.py status",
            "`publish`",
            "`package`",
        ):
            self.assertIn(command, guidance)
        for skill_md in sorted((SUITE / "skills").glob("*/SKILL.md")):
            with self.subTest(skill=skill_md.parent.name):
                self.assertIn("runtime-preflight.md", skill_md.read_text(encoding="utf-8"))

    def test_installed_core_ships_the_suite_verifier(self) -> None:
        verifier = SUITE / "skills/short-drama/scripts/suite_verify.py"
        self.assertTrue(verifier.is_file())
        self.assertIn("def verify_suite", verifier.read_text(encoding="utf-8"))

    def test_cli_initializes_and_discovers_space_path_from_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            tool = skills / "short-drama/scripts/project_tool.py"
            arbitrary_cwd = temp / "caller cwd 空格"
            arbitrary_cwd.mkdir()
            project = temp / "创作者 项目"
            env = os.environ.copy()
            env["CODEX_HOME"] = str(temp / "empty CODEX_HOME")

            initialized = subprocess.run(
                [
                    sys.executable,
                    str(tool),
                    "init",
                    str(project),
                    "--title",
                    "失物登记",
                ],
                cwd=arbitrary_cwd,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            init_result = json.loads(initialized.stdout)
            self.assertEqual(init_result["project"]["title"], "失物登记")
            self.assertTrue((project / "short-drama.json").is_file())
            self.assertTrue((project / ".short-drama/state.json").is_file())
            self.assertFalse((project / "episodes/EP001/screenplay.md").exists())

            nested = project / "episodes" / "EP001" / "notes"
            nested.mkdir(parents=True)
            status = subprocess.run(
                [sys.executable, str(tool), "status", "."],
                cwd=nested,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["project_root"], str(project.resolve()))

    def test_mixed_child_version_fails_in_relocated_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            child_ref = skills / "short-drama-write/suite-ref.json"
            reference = json.loads(child_ref.read_text(encoding="utf-8"))
            reference["contract_version"] = "mixed-version"
            child_ref.write_text(json.dumps(reference), encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                cwd=temp,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("mixed contract_version", completed.stderr)

    def test_child_content_tamper_and_extra_file_fail_manifest_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            child = skills / "short-drama-write/SKILL.md"
            child.write_text(child.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")

            tampered = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                cwd=temp,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(tampered.returncode, 2)
            self.assertIn("content hash mismatch", tampered.stderr)

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            (skills / "short-drama-write/EXTRA.md").write_text("extra", encoding="utf-8")
            extra = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                cwd=temp,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(extra.returncode, 2)
            self.assertIn("unexpected suite files", extra.stderr)

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            nested = skills / "short-drama-write/references/suite-ref.json"
            nested.write_text('{"unmanifested":"payload"}\n', encoding="utf-8")
            extra_pin = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                cwd=temp,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(extra_pin.returncode, 2)
            self.assertIn("unexpected suite files", extra_pin.stderr)

    def test_child_pin_rejects_extra_fields_and_wrong_core_skill(self) -> None:
        for key, value, expected in (
            ("unmanifested_payload", "payload", "keys are invalid"),
            ("core_skill", "short-drama-write", "mixed core_skill"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                skills = copy_installed_suite(temp)
                child_ref = skills / "short-drama-write/suite-ref.json"
                reference = json.loads(child_ref.read_text(encoding="utf-8"))
                reference[key] = value
                child_ref.write_text(json.dumps(reference), encoding="utf-8")

                completed = subprocess.run(
                    [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                    cwd=temp,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn(expected, completed.stderr)

    def test_manifest_updater_rejects_noncanonical_suite_refs(self) -> None:
        for case in ("nested", "extra_field"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                skills = copy_installed_suite(temp)
                if case == "nested":
                    nested = skills / "short-drama-write/references/suite-ref.json"
                    nested.write_text('{"unmanifested":"payload"}\n', encoding="utf-8")
                    expected = "unexpected suite-ref files"
                else:
                    child_ref = skills / "short-drama-write/suite-ref.json"
                    reference = json.loads(child_ref.read_text(encoding="utf-8"))
                    reference["unmanifested_payload"] = "payload"
                    child_ref.write_text(json.dumps(reference), encoding="utf-8")
                    expected = "suite-ref keys are invalid"

                completed = subprocess.run(
                    [
                        sys.executable,
                        str(UPDATE_TOOL),
                        str(skills / "short-drama"),
                    ],
                    cwd=temp,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(expected, completed.stderr)

    def test_core_manifest_tamper_fails_without_version_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            manifest = skills / "short-drama/suite-manifest.json"
            document = json.loads(manifest.read_text(encoding="utf-8"))
            document["trust_boundary"]["media_generation"] = True
            manifest.write_text(json.dumps(document), encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                cwd=temp,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("trust_boundary", completed.stderr)

    def test_rebuilt_manifest_cannot_enable_forbidden_runtime_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            manifest = skills / "short-drama/suite-manifest.json"
            document = json.loads(manifest.read_text(encoding="utf-8"))
            document["trust_boundary"]["media_generation"] = True
            manifest.write_text(json.dumps(document), encoding="utf-8")
            rebuilt = subprocess.run(
                [sys.executable, str(UPDATE_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)

            completed = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("trust_boundary", completed.stderr)

    def test_rebuilt_manifest_cannot_hide_invalid_skill_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            skills = copy_installed_suite(temp)
            skill_md = skills / "short-drama-write/SKILL.md"
            skill_md.write_text(
                skill_md.read_text(encoding="utf-8").replace(
                    "---\n\n#", "unexpected_key: forbidden\n---\n\n#", 1
                ),
                encoding="utf-8",
            )
            rebuilt = subprocess.run(
                [sys.executable, str(UPDATE_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)

            completed = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("frontmatter keys", completed.stderr)

    def test_shipped_skills_declare_a_license(self) -> None:
        """The MIT license must travel with a symlinked skill directory."""

        for skill_md in sorted((SUITE / "skills").glob("*/SKILL.md")):
            with self.subTest(skill=skill_md.parent.name):
                frontmatter = skill_md.read_text(encoding="utf-8").split("---", 2)[1]
                self.assertRegex(frontmatter, r"(?m)^license: MIT$")

    def test_skill_contract_rejects_allowed_tools(self) -> None:
        """allowed-tools grants tool access the declared trust boundary forbids."""

        with tempfile.TemporaryDirectory() as directory:
            skills = copy_installed_suite(Path(directory))
            skill_md = skills / "short-drama-write/SKILL.md"
            skill_md.write_text(
                skill_md.read_text(encoding="utf-8").replace(
                    "---\n\n#", "allowed-tools: Bash, WebFetch\n---\n\n#", 1
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(UPDATE_TOOL), str(skills / "short-drama")],
                check=True, capture_output=True, text=True,
            )
            completed = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("frontmatter keys", completed.stderr)

    def test_skill_contract_rejects_control_characters_in_frontmatter(self) -> None:
        """splitlines() would split on characters YAML rejects."""

        for smuggled in ("\x1c", "\x0b", " "):
            with self.subTest(smuggled=repr(smuggled)), tempfile.TemporaryDirectory() as d:
                skills = copy_installed_suite(Path(d))
                skill_md = skills / "short-drama-write/SKILL.md"
                skill_md.write_text(
                    skill_md.read_text(encoding="utf-8").replace(
                        "---\n\n#", f"x{smuggled}license: MIT\n---\n\n#", 1
                    ),
                    encoding="utf-8",
                )
                subprocess.run(
                    [sys.executable, str(UPDATE_TOOL), str(skills / "short-drama")],
                    check=True, capture_output=True, text=True,
                )
                completed = subprocess.run(
                    [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                    check=False, capture_output=True, text=True,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("control characters", completed.stderr)

    def test_skill_contract_bounds_metadata_and_rejects_non_json_constants(self) -> None:
        """metadata must stay a bounded, round-trippable JSON object."""

        verifier = import_module_from_path(
            "shipped_verifier_meta", SUITE / "skills/short-drama/scripts/suite_verify.py"
        )
        for label, value in (
            ("oversize", '{"a":"' + "x" * 1100 + '"}'),
            ("NaN", '{"a":NaN}'),
            ("Infinity", '{"a":Infinity}'),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                skill = Path(d) / "short-drama-write"
                shutil.copytree(SUITE / "skills/short-drama-write", skill)
                skill_md = skill / "SKILL.md"
                lines = skill_md.read_text(encoding="utf-8").split("\n")
                lines.insert(2, f"metadata: {value}")
                skill_md.write_text("\n".join(lines), encoding="utf-8")
                with self.assertRaises(ValueError):
                    verifier.verify_skill_contract(skill, "short-drama-write")

    def test_skill_contract_line_budget_counts_real_lines(self) -> None:
        """A trailing newline must not cost a line against the 500-line cap."""

        verifier = import_module_from_path(
            "shipped_verifier_lines", SUITE / "skills/short-drama/scripts/suite_verify.py"
        )
        with tempfile.TemporaryDirectory() as d:
            skill = Path(d) / "short-drama-write"
            shutil.copytree(SUITE / "skills/short-drama-write", skill)
            skill_md = skill / "SKILL.md"
            body = skill_md.read_text(encoding="utf-8").rstrip("\n").split("\n")
            padded = body + ["padding"] * (500 - len(body))
            self.assertEqual(len(padded), 500)
            skill_md.write_text("\n".join(padded) + "\n", encoding="utf-8")
            verifier.verify_skill_contract(skill, "short-drama-write")

            skill_md.write_text("\n".join(padded + ["one too many"]) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                verifier.verify_skill_contract(skill, "short-drama-write")

    def test_skill_contract_rejects_unicode_spaces_after_the_key(self) -> None:
        """A non-ASCII space is plausible in a CJK-authored file and is not YAML."""

        verifier = import_module_from_path(
            "shipped_verifier_space", SUITE / "skills/short-drama/scripts/suite_verify.py"
        )
        # Use a key the shipped file does not already carry, or duplicate-key
        # detection would raise regardless of the separator under test.
        for space in ("\u00a0", "\u2009", "\u3000"):
            with self.subTest(space=repr(space)), tempfile.TemporaryDirectory() as d:
                skill = Path(d) / "short-drama-write"
                shutil.copytree(SUITE / "skills/short-drama-write", skill)
                skill_md = skill / "SKILL.md"
                lines = skill_md.read_text(encoding="utf-8").split("\n")
                lines.insert(2, 'metadata:' + space + '{"a":"b"}')
                skill_md.write_text("\n".join(lines), encoding="utf-8")
                with self.assertRaises(ValueError):
                    verifier.verify_skill_contract(skill, "short-drama-write")

    def test_skill_contract_accepts_the_official_optional_frontmatter_keys(self) -> None:
        """metadata is spec-legal and must pass as an inline JSON object."""

        for extra in ('metadata: {"a":"b"}',):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                skills = copy_installed_suite(Path(directory))
                skill_md = skills / "short-drama-write/SKILL.md"
                skill_md.write_text(
                    skill_md.read_text(encoding="utf-8").replace(
                        "---\n\n#", f"{extra}\n---\n\n#", 1
                    ),
                    encoding="utf-8",
                )
                rebuilt = subprocess.run(
                    [sys.executable, str(UPDATE_TOOL), str(skills / "short-drama")],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
                completed = subprocess.run(
                    [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_skill_contract_rejects_scalar_metadata(self) -> None:
        """metadata is a mapping in the spec; a bare scalar must not pass."""

        with tempfile.TemporaryDirectory() as directory:
            skills = copy_installed_suite(Path(directory))
            skill_md = skills / "short-drama-write/SKILL.md"
            skill_md.write_text(
                skill_md.read_text(encoding="utf-8").replace(
                    "---\n\n#", "metadata: forbidden\n---\n\n#", 1
                ),
                encoding="utf-8",
            )
            rebuilt = subprocess.run(
                [sys.executable, str(UPDATE_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            completed = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("metadata must be an inline JSON object", completed.stderr)

    def test_verifier_still_reports_extra_files_hidden_in_dot_paths(self) -> None:
        """runtime-preflight promises to halt on extra executable content."""

        for relative in (".hidden/payload.py", ".env", "scripts/.bootstrap.sh"):
            with self.subTest(planted=relative), tempfile.TemporaryDirectory() as d:
                skills = copy_installed_suite(Path(d))
                planted = skills / "short-drama-storyboard" / relative
                planted.parent.mkdir(parents=True, exist_ok=True)
                planted.write_text("import os\n", encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                    check=False, capture_output=True, text=True,
                )
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertIn("unexpected suite files", completed.stderr)

    def test_generator_and_verifier_share_one_noise_definition(self) -> None:
        """A divergence would make the published manifest unverifiable."""

        generator = import_module_from_path("update_tool", UPDATE_TOOL)
        verifier = import_module_from_path(
            "shipped_verifier", SUITE / "skills/short-drama/scripts/suite_verify.py"
        )
        # Compare behaviour, not constants: matching sets would still pass if
        # one function body drifted.
        names = [
            "SKILL.md", "payload.py", "evil.sh", "x.pyc", "x.pyo", ".DS_Store",
            ".env", "a.swp", "b~", "note.md", "data.json", ".bootstrap.sh",
            # A file whose own name matches a noise directory: distinguishes a
            # body that scans parts[:-1] from one that scans every component.
            ".ruff_cache", "__pycache__", ".mypy_cache",
        ]
        directories = [
            (), ("references",), ("__pycache__",), (".ruff_cache",),
            (".hidden",), (".ruff_cache", "0.1.2"), ("scripts", "__pycache__"),
            (".DS_Store",), ("__pycache__", "nested"),
        ]
        checked = 0
        for directory in directories:
            for name in names:
                parts = directory + (name,)
                self.assertEqual(
                    generator.is_local_noise(parts),
                    verifier.is_local_noise(parts),
                    parts,
                )
                checked += 1
        self.assertGreaterEqual(checked, 100)

    def test_executable_content_is_never_treated_as_noise(self) -> None:
        """A payload planted inside a cache directory must stay reported."""

        verifier = import_module_from_path(
            "shipped_verifier_exec", SUITE / "skills/short-drama/scripts/suite_verify.py"
        )
        for parts in (
            ("__pycache__", "payload.py"),
            (".ruff_cache", "payload.py"),
            (".mypy_cache", "evil.sh"),
            (".hidden", "payload.py"),
            (".DS_Store", "payload.py"),
        ):
            with self.subTest(parts=parts):
                self.assertFalse(verifier.is_local_noise(parts))
        # Bytecode itself is still tolerated, or every run would fail.
        self.assertTrue(verifier.is_local_noise(("__pycache__", "mod.pyc")))
        self.assertFalse(verifier.is_local_noise(("__pycache__", "notes.txt")))

    def test_manifest_generator_excludes_local_dot_noise(self) -> None:
        """A stray .DS_Store must never be baked into the published manifest."""

        with tempfile.TemporaryDirectory() as directory:
            skills = copy_installed_suite(Path(directory))
            manifest_path = skills / "short-drama/suite-manifest.json"
            before = len(json.loads(manifest_path.read_text(encoding="utf-8"))["files"])
            (skills / "short-drama-storyboard/.DS_Store").write_bytes(b"\x00")
            rebuilt = subprocess.run(
                [sys.executable, str(UPDATE_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            files = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
            self.assertEqual(len(files), before)
            self.assertNotIn("short-drama-storyboard/.DS_Store", files)

            # The verifier must agree with the generator, or preflight halts.
            completed = subprocess.run(
                [sys.executable, str(VERIFY_TOOL), str(skills / "short-drama")],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
