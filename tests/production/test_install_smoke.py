from __future__ import annotations

import json
import unittest

from tests.production.install_smoke import (
    SYSTEM_SITE_VENV_MODE,
    _build_readiness_summary,
    _classify_doctor,
    _classify_health,
    _smoke_ok,
)


class InstallSmokeClassificationTests(unittest.TestCase):
    def test_classifies_missing_gnuradio_python_bindings(self) -> None:
        step = {
            "stdout_tail": json.dumps(
                {
                    "checks": [
                        {
                            "name": "GNU Radio import/version",
                            "ok": False,
                            "detail": "No module named 'gnuradio'",
                        }
                    ]
                }
            )
        }

        self.assertEqual(
            _classify_doctor(step),
            ["missing_gnuradio_python_bindings"],
        )

    def test_classifies_health_status_reasons(self) -> None:
        step = {
            "stdout_tail": json.dumps(
                {
                    "status": "not_ready",
                    "status_reasons": ["llama_unreachable"],
                }
            )
        }

        self.assertEqual(_classify_health(step), ["llama_unreachable"])

    def test_readiness_summary_reports_package_ready_runtime_not_ready(self) -> None:
        steps = {
            "uv_sync": {"returncode": 0},
            "help": {"returncode": 0},
            "production_tests": {"returncode": 0},
            "vector_stats": {"returncode": 1},
            "doctor": {
                "stdout_tail": json.dumps(
                    {
                        "checks": [
                            {
                                "name": "GNU Radio import/version",
                                "ok": True,
                                "detail": "3.10.9.2",
                            },
                            {
                                "name": "grcc on PATH",
                                "ok": True,
                                "path": "/usr/bin/grcc",
                            },
                            {
                                "name": "Retrieval readiness",
                                "ok": True,
                                "detail": "/usr/share/gnuradio/grc/blocks",
                            },
                        ]
                    }
                )
            },
            "health": {
                "stdout_tail": json.dumps(
                    {
                        "status": "not_ready",
                        "status_reasons": ["llama_unreachable"],
                        "llama_context_verified": False,
                    }
                )
            },
        }

        readiness = _build_readiness_summary(steps)

        self.assertTrue(readiness["package_ready"])
        self.assertTrue(readiness["gnu_radio_ready"])
        self.assertTrue(readiness["grcc_ready"])
        self.assertTrue(readiness["retrieval_ready"])
        self.assertTrue(readiness["retrieval_catalog_ready"])
        self.assertFalse(readiness["llama_ready"])
        self.assertFalse(readiness["vector_index_ready"])
        self.assertFalse(readiness["model_runtime_ready"])
        self.assertFalse(readiness["end_to_end_ready"])
        self.assertEqual(
            readiness["overall_environment_classification"],
            "package_ready_runtime_not_ready",
        )

    def test_vector_index_requirement_is_explicit(self) -> None:
        steps = {
            "uv_sync": {"returncode": 0},
            "help": {"returncode": 0},
            "production_tests": {"returncode": 0},
        }
        readiness = {"vector_index_ready": False}

        self.assertTrue(
            _smoke_ok(
                steps=steps,
                mode="default-uv",
                readiness=readiness,
                require_vector_index=False,
            )
        )
        self.assertFalse(
            _smoke_ok(
                steps=steps,
                mode="default-uv",
                readiness=readiness,
                require_vector_index=True,
            )
        )

    def test_system_site_mode_constant_is_public_cli_value(self) -> None:
        self.assertEqual(SYSTEM_SITE_VENV_MODE, "system-site-venv")


if __name__ == "__main__":
    unittest.main()
