from __future__ import annotations

import re
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1] / "skill" / "checkpoint-thread"


class SkillContractTests(unittest.TestCase):
    def test_skill_stays_lean_and_points_to_conditional_references(self) -> None:
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(content.splitlines()), 100)
        self.assertLessEqual(len(content.split()), 650)
        frontmatter = re.match(r"---\n(.*?)\n---", content, re.DOTALL)
        self.assertIsNotNone(frontmatter)
        description = next(
            line.removeprefix("description: ")
            for line in frontmatter.group(1).splitlines()
            if line.startswith("description: ")
        )
        self.assertLessEqual(len(description.split()), 45)
        self.assertIn("A branch is a shared workspace", content)
        self.assertIn("Multiple threads may enter and edit the same", content)
        self.assertIn("semantic goal delta", content)
        self.assertIn("Implicit progression", content)
        self.assertIn("Read-only threads create nothing", content)
        self.assertIn("Same-goal continuation requires no agent CLI", content)
        self.assertIn("ask the user once", content)
        self.assertIn(
            "${CODEX_HOME:-$HOME/.codex}/ledgers/checkpoint-thread/active", content
        )
        self.assertIn("configure --replace", content)
        self.assertIn("PreToolUse", content)
        self.assertIn("not a daemon", content)
        self.assertIn("fails open with a warning", content)
        self.assertIn("fallback `enter`", content)
        self.assertIn("state_oid", content)
        self.assertIn("Prefer lifecycle commands for auditable history", content)
        for reference in [
            "ship.md",
            "safety-snapshot.md",
            "worktree.md",
            "multi-repo.md",
        ]:
            self.assertIn(f"references/{reference}", content)
            self.assertTrue((SKILL_ROOT / "references" / reference).is_file())
        self.assertEqual(4, content.count("references/"))

    def test_skill_folder_contains_no_maintenance_artifacts(self) -> None:
        forbidden = {"README.md", ".DS_Store", "CHANGELOG.md", "audit.html"}
        found = {path.name for path in SKILL_ROOT.rglob("*") if path.is_file()}
        self.assertFalse(forbidden & found)


if __name__ == "__main__":
    unittest.main()
