from __future__ import annotations

import re
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1] / "skill" / "checkpoint-thread"


class SkillContractTests(unittest.TestCase):
    def test_skill_stays_lean_and_points_to_conditional_references(self) -> None:
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(content.splitlines()), 110)
        frontmatter = re.match(r"---\n(.*?)\n---", content, re.DOTALL)
        self.assertIsNotNone(frontmatter)
        description = next(
            line.removeprefix("description: ")
            for line in frontmatter.group(1).splitlines()
            if line.startswith("description: ")
        )
        self.assertLessEqual(len(description.split()), 45)
        self.assertIn("A thread is the scope", content)
        self.assertIn("semantic goal delta", content)
        self.assertIn("implicit progression", content)
        for reference in [
            "ship.md",
            "safety-snapshot.md",
            "worktree.md",
            "multi-repo.md",
        ]:
            self.assertIn(f"references/{reference}", content)
            self.assertTrue((SKILL_ROOT / "references" / reference).is_file())

    def test_skill_folder_contains_no_maintenance_artifacts(self) -> None:
        forbidden = {"README.md", ".DS_Store", "CHANGELOG.md", "audit.html"}
        found = {path.name for path in SKILL_ROOT.rglob("*") if path.is_file()}
        self.assertFalse(forbidden & found)


if __name__ == "__main__":
    unittest.main()
