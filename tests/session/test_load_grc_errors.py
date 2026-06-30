"""Tests for session load_grc error handling and edge cases."""

import unittest
from pathlib import Path

from grc_agent.domain_models import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session import load_grc


class LoadGrcErrorTests(unittest.TestCase):
    """Verify structured error payloads on load failures."""

    def test_load_missing_file_returns_error_payload(self) -> None:
        result = load_grc("/nonexistent/path/file.grc")

        self.assertNotIsInstance(result, FlowgraphSession)
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "file_load_error")

    def test_load_malformed_yaml_returns_error_payload(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".grc", mode="w", delete=False) as f:
            f.write("not: valid\n  broken: yaml: [\n")
            malformed_path = f.name

        try:
            result = load_grc(malformed_path)

            self.assertNotIsInstance(result, FlowgraphSession)
            self.assertIsInstance(result, dict)
            self.assertFalse(result["ok"])
            self.assertIn(
                result["error_type"],
                (ErrorCode.INVALID_GRC, ErrorCode.FILE_LOAD_ERROR),
            )
        finally:
            Path(malformed_path).unlink(missing_ok=True)

    def test_load_valid_fixture_still_works(self) -> None:
        fixture_path = Path(__file__).resolve().parents[1] / "data" / "dial_tone.grc"

        session = load_grc(fixture_path)

        self.assertIsInstance(session, FlowgraphSession)
        self.assertIsNotNone(session.flowgraph)


if __name__ == "__main__":
    unittest.main()
