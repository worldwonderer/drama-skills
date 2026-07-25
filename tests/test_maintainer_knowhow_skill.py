import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from tests.private_release_gate import load_private_terms


SUITE = Path(__file__).resolve().parents[1]
PUBLIC_SKILLS = SUITE / "skills"
MAINTAINERS = SUITE / "maintainers"
SKILL = SUITE / "maintainers/skills/short-drama-knowhow"
SKILL_MD = SKILL / "SKILL.md"
OPENAI_YAML = SKILL / "agents/openai.yaml"
REFERENCES = SKILL / "references"
MANIFEST = PUBLIC_SKILLS / "short-drama/suite-manifest.json"


def text_files() -> list[Path]:
    return sorted(
        path
        for path in MAINTAINERS.rglob("*")
        if path.is_file() and path.suffix in {".md", ".yaml", ".yml", ".json"}
    )


def local_forbidden_terms() -> frozenset[str]:
    return load_private_terms(SUITE / "tests/local-terms.txt")


class MaintainerKnowhowSkillTests(unittest.TestCase):
    def test_quick_validate_when_skill_creator_is_available(self) -> None:
        codex_home = Path(
            os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
        )
        validator = (
            codex_home
            / "skills/.system/skill-creator/scripts/quick_validate.py"
        )
        if not validator.is_file():
            self.skipTest("skill-creator quick_validate.py is not installed")

        result = subprocess.run(
            [sys.executable, str(validator), str(SKILL)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"quick_validate failed:\n{result.stdout}\n{result.stderr}",
        )

    def test_frontmatter_and_openai_metadata_follow_skill_contract(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        _, frontmatter, body = text.split("---", 2)
        keys = {
            match.group(1)
            for line in frontmatter.splitlines()
            if (match := re.fullmatch(r"([a-z][a-z0-9_-]*):\s+.+", line))
        }
        self.assertLessEqual({"name", "description"}, keys)
        self.assertLessEqual(keys, {"name", "description", "license", "allowed-tools", "metadata"})
        self.assertRegex(frontmatter, r"(?m)^name: short-drama-knowhow$")
        self.assertIn("维护者专用", frontmatter)
        self.assertNotIn("TODO", text)
        self.assertLess(len(body.splitlines()), 500)

        metadata = OPENAI_YAML.read_text(encoding="utf-8")
        self.assertRegex(metadata, r'(?m)^  display_name: ".+"$')
        self.assertRegex(metadata, r'(?m)^  short_description: ".{25,64}"$')
        self.assertRegex(
            metadata,
            r'(?m)^  default_prompt: ".*\$short-drama-knowhow.*"$',
        )
        self.assertIn("policy:\n  allow_implicit_invocation: false\n", metadata)
        self.assertNotIn("dependencies:", metadata)

    def test_references_are_progressively_disclosed_and_one_hop(self) -> None:
        link_pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
        skill_targets = {
            target.split("#", 1)[0]
            for target in link_pattern.findall(
                SKILL_MD.read_text(encoding="utf-8")
            )
            if not target.startswith("#") and "://" not in target
        }
        reference_targets = {
            path.relative_to(SKILL).as_posix()
            for path in REFERENCES.glob("*.md")
        }
        self.assertEqual(skill_targets, reference_targets)
        for target in skill_targets:
            self.assertTrue((SKILL / target).is_file())

        for reference in REFERENCES.glob("*.md"):
            with self.subTest(reference=reference.name):
                local_links = [
                    target
                    for target in link_pattern.findall(
                        reference.read_text(encoding="utf-8")
                    )
                    if not target.startswith("#") and "://" not in target
                ]
                self.assertEqual(local_links, [])
                lines = reference.read_text(encoding="utf-8").splitlines()
                if len(lines) > 100:
                    self.assertIn("## 内容导航", lines[:12])

    def test_skill_is_maintainer_only_and_outside_public_suite(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            SKILL.parent,
            SUITE / "maintainers/skills",
        )
        self.assertFalse((PUBLIC_SKILLS / SKILL.name).exists())
        self.assertNotIn(SKILL.name, manifest["public_skills"])
        self.assertNotIn(SKILL.name, json.dumps(manifest, ensure_ascii=False))
        self.assertFalse(
            any(
                Path(relative).parts[0] == "maintainers"
                for relative in manifest["files"]
            )
        )

    def test_skill_has_no_scripts_or_runtime_dependency(self) -> None:
        self.assertFalse(any(path.name == "scripts" for path in SKILL.rglob("*")))
        executable_suffixes = {".py", ".sh", ".js", ".ts"}
        self.assertEqual(
            [path for path in SKILL.rglob("*") if path.suffix in executable_suffixes],
            [],
        )
        self.assertEqual(
            {path.name for path in SKILL.iterdir() if path.is_dir()},
            {"agents", "references"},
        )

    def test_skill_carries_no_private_locator_or_credential_material(self) -> None:
        patterns = {
            "IPv4 address": re.compile(
                r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?!\d)"
            ),
            "network URI": re.compile(
                r"\b(?:https?|mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis)://",
                re.IGNORECASE,
            ),
            "machine path": re.compile(
                r"(?<![\w.])/(?:Users|home|private|var|tmp)/|\b[A-Za-z]:[\\/]"
            ),
            "credential assignment": re.compile(
                r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)"
                r"\s*[:=]\s*[^\s<]+",
                re.IGNORECASE,
            ),
            "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        }
        findings: list[str] = []
        forbidden_terms = local_forbidden_terms()
        for path in text_files():
            text = path.read_text(encoding="utf-8")
            for label, pattern in patterns.items():
                if pattern.search(text):
                    findings.append(f"{path.relative_to(SUITE)}: {label}")
            folded = text.casefold()
            for term in forbidden_terms:
                if term.casefold() in folded:
                    findings.append(
                        f"{path.relative_to(SUITE)}: maintainer-local term"
                    )
        self.assertEqual(findings, [])

    def test_workflow_contains_the_full_learning_and_governance_loop(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        ordered_stages = [
            "确立只读授权与隔离工作区",
            "以覆盖缺口选样",
            "定性通读完整项目链",
            "写私有 observation / decision cards",
            "构建题材 × 机制 coverage matrix",
            "用反例、冲突与适用边界压缩结论",
            "去标识与 de-copy",
            "形成公共 reference / rubric / synthetic fixture proposal",
            "做 fresh-agent blind forward eval",
            "交给 independent reviewer",
            "promotion / retire",
        ]
        positions = [text.index(stage) for stage in ordered_stages]
        self.assertEqual(positions, sorted(positions))

        required_meanings = [
            "统计只用于",
            "不得从频次、排名或相关性直接推出创作规则",
            "让 agent 判断题材语境",
            "不要为创作语义写规则匹配脚本",
            "候选作者不得自审放行",
            "公开运行时永不连接非公开来源",
            "不生成、渲染、下载或检查图像、音频或视频",
        ]
        for meaning in required_meanings:
            with self.subTest(meaning=meaning):
                self.assertIn(meaning, text)

    def test_card_schema_and_examples_are_synthetic_and_boundary_aware(self) -> None:
        cards = (REFERENCES / "cards-and-coverage.md").read_text(
            encoding="utf-8"
        )
        required_fields = {
            "observation_card",
            "decision_card",
            "chain_read",
            "direct_observations",
            "agent_interpretation",
            "counterevidence",
            "applies_when",
            "fails_or_changes_when",
            "rejected_generalization",
            "next_sample_intent",
            "version_chain",
            "creator_overrides",
            "source_role",
            "media_observed",
            "outcome_evidence",
        }
        self.assertTrue(required_fields <= set(re.findall(r"[a-z_]+", cards)))
        self.assertIn("source_project_ref: synthetic-only", cards)
        self.assertIn("不得写成", cards)
        self.assertIn("不用计数替代判断", cards)
        self.assertIn("media_observed: false", cards)

    def test_promotion_never_treats_pipeline_activity_as_quality_evidence(self) -> None:
        synthesis = (REFERENCES / "synthesis-and-promotion.md").read_text(
            encoding="utf-8"
        )
        for weak_signal in ("prompt", "task success", "userPrompt", "reSubmit"):
            with self.subTest(weak_signal=weak_signal):
                self.assertIn(weak_signal, synthesis)
        self.assertIn("不能证明创作或媒体结果质量", synthesis)

    def test_forward_eval_is_blind_fresh_independent_and_promotable(self) -> None:
        protocol = (REFERENCES / "blind-forward-eval.md").read_text(
            encoding="utf-8"
        )
        required = [
            "baseline",
            "candidate",
            "干净上下文的 fresh agent",
            "不说明正在测试哪条规则",
            "换新上下文运行对照",
            "在揭盲前完成逐项评审",
            "independent reviewer",
            "creative_variance",
            "privacy_and_scope",
            "promotion",
            "narrow-and-retest",
            "hold",
            "retire",
        ]
        for item in required:
            with self.subTest(item=item):
                self.assertIn(item, protocol)

        for hygiene_rule in (
            "reviewer-facing",
            "绝对路径",
            "arm 身份",
            "协调者单独保存",
            "不能进入盲评正文",
        ):
            with self.subTest(hygiene_rule=hygiene_rule):
                self.assertIn(hygiene_rule, protocol)

    def test_promotion_ledger_is_replayable_without_private_evidence(self) -> None:
        ledger = (REFERENCES / "promotion-ledger.md").read_text(encoding="utf-8")
        for field in (
            "rule_id",
            "public_diff_hash",
            "baseline_skill_hash",
            "candidate_skill_hash",
            "synthetic_task_refs",
            "baseline_output_hashes",
            "candidate_output_hashes",
            "reviewer_context_ref",
            "independent",
            "verdict",
            "rollback",
            "supersedes",
            "retire_trigger",
        ):
            with self.subTest(field=field):
                self.assertIn(field, ledger)

        for boundary in (
            "不得记录私有来源定位",
            "不能用脚本给创作质量打分",
            "fresh agent",
            "independent reviewer",
            "hold",
            "retire",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, ledger)

if __name__ == "__main__":
    unittest.main()
