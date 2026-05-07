"""Regression tests for retrieval eval gate locking."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

from tests.retrieval_eval._eval_gate_lock import acquire_retrieval_eval_lock


class RetrievalEvalGateLockTests(unittest.TestCase):
    def test_parallel_lock_attempt_fails_fast(self) -> None:
        child_code = """
import json
import sys
from tests.retrieval_eval._eval_gate_lock import acquire_retrieval_eval_lock
try:
    with acquire_retrieval_eval_lock("child_gate"):
        pass
except RuntimeError as exc:
    print(json.dumps({"ok": False, "message": str(exc)}))
    raise SystemExit(3)
print(json.dumps({"ok": True}))
"""
        with acquire_retrieval_eval_lock("parent_gate"):
            completed = subprocess.run(
                [sys.executable, "-c", child_code],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(Path.cwd()),
            )
        self.assertEqual(completed.returncode, 3, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertFalse(payload["ok"])
        self.assertIn("sequentially", payload["message"])

    def test_lock_can_be_reacquired_after_release(self) -> None:
        with acquire_retrieval_eval_lock("first_gate"):
            pass
        with acquire_retrieval_eval_lock("second_gate"):
            pass


if __name__ == "__main__":
    unittest.main()
