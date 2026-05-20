from __future__ import annotations

import json
import unittest

from tests.production.install_smoke import _classify_doctor, _classify_health


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


if __name__ == "__main__":
    unittest.main()
