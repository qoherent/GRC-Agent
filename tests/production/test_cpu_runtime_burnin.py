from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tests.production.cpu_runtime_burnin import (
    CPU_LLAMA_COMMAND,
    build_aggregate,
    secret_scan,
    summarize_live_eval_store,
)


class CpuRuntimeBurninTests(unittest.TestCase):
    def test_cpu_llama_command_rejects_gpu_backend(self) -> None:
        command = " ".join(CPU_LLAMA_COMMAND)

        self.assertIn("--ctx-size 120000", command)
        self.assertIn("--device none", command)
        self.assertIn("--gpu-layers 0", command)
        self.assertIn("--threads 12", command)
        self.assertIn("--threads-batch 12", command)

    def test_secret_scan_detects_forbidden_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.json"
            path.write_text('{"Authorization": "Bearer abc"}\n', encoding="utf-8")

            result = secret_scan([path])

        self.assertEqual(result[str(path)], ["Authorization", "Bearer"])

    def test_summarize_live_eval_requires_no_backend_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.json"
            path.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "status": "PASS",
                                "run_result": {
                                    "status": "PASS",
                                    "backend_restart_count": 1,
                                    "runtime_safety_pass": True,
                                    "model_contract_pass": True,
                                    "semantic_pass": True,
                                    "tool_success_pass": True,
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = summarize_live_eval_store(path)

        self.assertFalse(result["ok"])
        self.assertEqual(result["backend_restarts"], 1)

    def test_build_aggregate_requires_all_runs_pass(self) -> None:
        aggregate = build_aggregate(
            [{"ok": True}, {"ok": False}],
            Path("/tmp/example"),
        )

        self.assertFalse(aggregate["all_passed"])
        self.assertEqual(aggregate["passed_runs"], 1)


if __name__ == "__main__":
    unittest.main()
