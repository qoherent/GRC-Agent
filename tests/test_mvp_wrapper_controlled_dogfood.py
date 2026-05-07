"""Guards for controlled MVP wrapper dogfood runner configuration."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.dogfood import mvp_wrapper_controlled_dogfood as controlled
from tests.dogfood.self_dogfood import GraphInfo


class MvpWrapperControlledDogfoodTests(unittest.TestCase):
    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def test_run_task_forces_mvp_tool_profile(self) -> None:
        graph = GraphInfo(
            source_path=self._fixture_path(),
            relative_path="random_bit_generator.grc",
            family="test",
            variables=(),
            variable_values={},
            blocks=(),
            block_types={},
            connections=(),
        )
        task = controlled.Task(
            graph=graph,
            task_group="inspect_graph",
            task_type="inspect",
            prompt="Summarize this graph.",
            expected="inspect_graph summarize",
        )
        captured_kwargs: dict[str, object] = {}

        def _fake_run_bounded(**kwargs):
            captured_kwargs.update(kwargs)
            return {"ok": True, "assistant_text": "ok"}

        with tempfile.TemporaryDirectory(prefix="mvp-dogfood-test-") as tmpdir:
            intake = Path(tmpdir) / "intake.jsonl"
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            with mock.patch.object(
                controlled, "run_bounded_llama_turn", side_effect=_fake_run_bounded
            ):
                row = controlled.run_task(
                    client=object(),
                    model="dummy-model",
                    task=task,
                    index=1,
                    total=1,
                    workspace=workspace,
                    intake_path=intake,
                    source="user_graph",
                )

        self.assertEqual(captured_kwargs.get("mvp_tool_profile"), True)
        self.assertEqual(captured_kwargs.get("wrapper_eval_telemetry"), True)
        self.assertEqual(row["severity"], "info")


if __name__ == "__main__":
    unittest.main()
