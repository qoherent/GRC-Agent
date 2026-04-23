"""Unit tests for run_all.py argument handling, exit codes, and quick-mode wiring."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

from tests.llama_eval import run_all


def _make_report(total: int, passed: int) -> dict:
    return {"summary": {"total": total, "passed": passed}}


def _dummy_cases(names: list[str], category: str = "cat") -> list:
    return [SimpleNamespace(name=n, category=category) for n in names]


def _patch_run_all(phases_spec: list[tuple], server_url=None, model_alias=None):
    """Return patchers for ensure_llama_server and _PHASES."""
    mock_server = mock.patch(
        "tests.llama_eval.run_all.ensure_llama_server",
        return_value=(server_url or "http://mock", model_alias or "mock-model", None),
    )
    mock_phases = mock.patch.object(run_all, "_PHASES", phases_spec)
    return mock_server, mock_phases


class RunAllExitCodeTests(unittest.TestCase):
    def _run(self, argv: list[str], phases_spec: list[tuple]) -> int:
        mock_server, mock_phases = _patch_run_all(phases_spec)
        with (
            mock_server,
            mock_phases,
            mock.patch("sys.argv", ["run_all"] + argv),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            return run_all.main()

    def test_all_phases_pass_returns_0(self) -> None:
        specs = [
            (i + 1, _dummy_cases(["c1", "c2"]), mock.Mock(return_value=_make_report(2, 2)))
            for i in range(6)
        ]
        self.assertEqual(self._run([], specs), 0)

    def test_one_phase_fails_returns_1(self) -> None:
        specs = [
            (1, _dummy_cases(["c1"]), mock.Mock(return_value=_make_report(1, 0))),
            *[
                (i + 2, _dummy_cases(["c1"]), mock.Mock(return_value=_make_report(1, 1)))
                for i in range(5)
            ],
        ]
        self.assertEqual(self._run([], specs), 1)

    def test_no_matching_case_returns_1(self) -> None:
        specs = [
            (1, _dummy_cases(["real_case"]), mock.Mock(return_value=_make_report(1, 1))),
        ]
        result = self._run(["--phases", "1", "--case", "nonexistent"], specs)
        self.assertEqual(result, 1)

    def test_no_matching_category_returns_1(self) -> None:
        specs = [
            (1, _dummy_cases(["c1"], category="search"), mock.Mock(return_value=_make_report(1, 1))),
        ]
        result = self._run(["--phases", "1", "--category", "does_not_exist"], specs)
        self.assertEqual(result, 1)

    def test_no_matching_case_skips_run_eval_and_reports_error(self) -> None:
        run_eval = mock.Mock(return_value=_make_report(1, 1))
        specs = [(1, _dummy_cases(["real_case"]), run_eval)]
        mock_server, mock_phases = _patch_run_all(specs)
        stderr = io.StringIO()
        with (
            mock_server,
            mock_phases,
            mock.patch("sys.argv", ["run_all", "--phases", "1", "--case", "nonexistent"]),
            redirect_stderr(stderr),
        ):
            result = run_all.main()

        self.assertEqual(result, 1)
        run_eval.assert_not_called()
        self.assertIn("no cases matched", stderr.getvalue())


class RunAllPhasesParsingTests(unittest.TestCase):
    def _run_argv(self, argv: list[str]) -> None:
        with (
            mock.patch("sys.argv", ["run_all"] + argv),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            run_all.main()

    def test_invalid_phases_string_raises_system_exit_2(self) -> None:
        mock_server, mock_phases = _patch_run_all([])
        with mock_server, mock_phases:
            with self.assertRaises(SystemExit) as ctx:
                self._run_argv(["--phases", "abc"])
        self.assertEqual(ctx.exception.code, 2)

    def test_out_of_range_phase_number_raises_system_exit_2(self) -> None:
        mock_server, mock_phases = _patch_run_all([])
        with mock_server, mock_phases:
            with self.assertRaises(SystemExit) as ctx:
                self._run_argv(["--phases", "7"])
        self.assertEqual(ctx.exception.code, 2)

    def test_valid_phases_subset_runs_only_those(self) -> None:
        run_eval_1 = mock.Mock(return_value=_make_report(1, 1))
        run_eval_2 = mock.Mock(return_value=_make_report(1, 1))
        specs = [
            (1, _dummy_cases(["c1"]), run_eval_1),
            (2, _dummy_cases(["c1"]), run_eval_2),
        ]
        mock_server, mock_phases = _patch_run_all(specs)
        with (
            mock_server,
            mock_phases,
            mock.patch("sys.argv", ["run_all", "--phases", "1"]),
            redirect_stdout(io.StringIO()),
        ):
            run_all.main()

        run_eval_1.assert_called_once()
        run_eval_2.assert_not_called()

    def test_ensure_server_called_once_and_resolved_values_feed_each_phase(self) -> None:
        captured: list[tuple[str, str, int, int]] = []

        def capture_run_eval(url, model, cases, n_runs):
            captured.append((url, model, len(cases), n_runs))
            return _make_report(len(cases), len(cases))

        specs = [
            (1, _dummy_cases(["c1"]), capture_run_eval),
            (2, _dummy_cases(["c2", "c3"]), capture_run_eval),
        ]
        mock_server, mock_phases = _patch_run_all(
            specs,
            server_url="http://resolved-server",
            model_alias="resolved-model",
        )
        with (
            mock_server as ensure_mock,
            mock_phases,
            mock.patch("sys.argv", ["run_all"]),
            redirect_stdout(io.StringIO()),
        ):
            result = run_all.main()

        self.assertEqual(result, 0)
        ensure_mock.assert_called_once_with(None, None)
        self.assertEqual(
            captured,
            [
                ("http://resolved-server", "resolved-model", 1, 3),
                ("http://resolved-server", "resolved-model", 2, 3),
            ],
        )


class RunAllQuickModeTests(unittest.TestCase):
    def test_quick_flag_passes_n_runs_1_to_run_eval(self) -> None:
        captured: list[int] = []

        def capture_run_eval(url, model, cases, n_runs):
            captured.append(n_runs)
            return _make_report(1, 1)

        specs = [(1, _dummy_cases(["c1"]), capture_run_eval)]
        mock_server, mock_phases = _patch_run_all(specs)
        with (
            mock_server,
            mock_phases,
            mock.patch("sys.argv", ["run_all", "--phases", "1", "--quick"]),
            redirect_stdout(io.StringIO()),
        ):
            run_all.main()

        self.assertEqual(captured, [1])

    def test_without_quick_passes_default_n_runs_3(self) -> None:
        captured: list[int] = []

        def capture_run_eval(url, model, cases, n_runs):
            captured.append(n_runs)
            return _make_report(1, 1)

        specs = [(1, _dummy_cases(["c1"]), capture_run_eval)]
        mock_server, mock_phases = _patch_run_all(specs)
        with (
            mock_server,
            mock_phases,
            mock.patch("sys.argv", ["run_all", "--phases", "1"]),
            redirect_stdout(io.StringIO()),
        ):
            run_all.main()

        self.assertEqual(captured, [3])

    def test_n_runs_override_respected(self) -> None:
        captured: list[int] = []

        def capture_run_eval(url, model, cases, n_runs):
            captured.append(n_runs)
            return _make_report(1, 1)

        specs = [(1, _dummy_cases(["c1"]), capture_run_eval)]
        mock_server, mock_phases = _patch_run_all(specs)
        with (
            mock_server,
            mock_phases,
            mock.patch("sys.argv", ["run_all", "--phases", "1", "--n-runs", "5"]),
            redirect_stdout(io.StringIO()),
        ):
            run_all.main()

        self.assertEqual(captured, [5])

    def test_quick_overrides_n_runs(self) -> None:
        captured: list[int] = []

        def capture_run_eval(url, model, cases, n_runs):
            captured.append(n_runs)
            return _make_report(1, 1)

        specs = [(1, _dummy_cases(["c1"]), capture_run_eval)]
        mock_server, mock_phases = _patch_run_all(specs)
        with mock_server, mock_phases, mock.patch(
            "sys.argv", ["run_all", "--phases", "1", "--quick", "--n-runs", "5"]
        ), redirect_stdout(io.StringIO()):
            run_all.main()

        self.assertEqual(captured, [1])


if __name__ == "__main__":
    unittest.main()
