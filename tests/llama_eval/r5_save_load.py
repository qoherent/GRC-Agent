"""Native MVP R5 eval: explicit lifecycle save/load wrappers.

Expected tool surface: inspect_graph, search_blocks, ask_grc_docs, change_graph,
save_graph_explicit, load_graph_explicit.

Run:
    uv run python -m tests.llama_eval.r5_save_load --quick
    uv run python -m tests.llama_eval.r5_save_load --n-runs 3
"""

from __future__ import annotations

from pathlib import Path

from tests.llama_eval.harness import (
    LiveScenario,
    LiveTurnSpec,
    ToolExpectation,
)

_CANONICAL_FIXTURE_PATH = str(
    (Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc").resolve()
)


R5_CASES: list[LiveScenario] = [
    LiveScenario(
        category="lifecycle",
        name="explicit_save_copy",
        description="Save wrapper writes explicit copy path after validation.",
        release_profile="R5_SAVE_LOAD",
        turns=(
            LiveTurnSpec(
                prompt="Save this graph to {save_path}.",
                expected_tool_calls=(
                    ToolExpectation(
                        "save_graph_explicit",
                        arguments={"path": "{save_path}"},
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "tool_result",
                        "tool": "save_graph_explicit",
                        "arguments": {
                            "ok": True,
                            "path": "{save_path}",
                            "validation_result": {"valid": True},
                        },
                    },
                    {
                        "kind": "saved_path_valid",
                        "path": "{save_path}",
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="lifecycle",
        name="explicit_save_overwrite_refused",
        description="Save wrapper refuses overwriting an existing explicit destination without overwrite=true.",
        release_profile="R5_SAVE_LOAD",
        target_fixture_name="random_bit_generator_dual_sink.grc",
        turns=(
            LiveTurnSpec(
                prompt="Save this graph to {target_path}.",
                expected_tool_calls=(
                    ToolExpectation(
                        "save_graph_explicit",
                        arguments={"path": "{target_path}"},
                        require_result_ok=False,
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "tool_result",
                        "tool": "save_graph_explicit",
                        "arguments": {
                            "ok": False,
                            "error_type": "save_refused",
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="lifecycle",
        name="explicit_save_invalid_graph_refused",
        description="Save wrapper refuses writing when the current graph is invalid.",
        release_profile="R5_SAVE_LOAD",
        fixture_name="random_bit_generator_invalid_disconnect.grc",
        turns=(
            LiveTurnSpec(
                prompt="Save this graph to {save_path}.",
                expected_tool_calls=(
                    ToolExpectation(
                        "save_graph_explicit",
                        arguments={"path": "{save_path}"},
                        require_result_ok=False,
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "tool_result",
                        "tool": "save_graph_explicit",
                        "arguments": {
                            "ok": False,
                            "error_type": "save_refused",
                            "validation_result": {"valid": False},
                        },
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="lifecycle",
        name="explicit_load_copy",
        description="Load wrapper opens explicit copied graph and validates session state.",
        release_profile="R5_SAVE_LOAD",
        target_fixture_name="random_bit_generator_dual_sink.grc",
        turns=(
            LiveTurnSpec(
                prompt="Load {target_path}.",
                expected_tool_calls=(
                    ToolExpectation(
                        "load_graph_explicit",
                        arguments={"path": "{target_path}"},
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "tool_result",
                        "tool": "load_graph_explicit",
                        "arguments": {
                            "ok": True,
                            "path": "{target_path}",
                            "valid": True,
                        },
                    },
                    {
                        "kind": "path_equals",
                        "path": "{target_path}",
                    },
                ),
            ),
        ),
    ),
    LiveScenario(
        category="lifecycle",
        name="explicit_load_unsafe_canonical_refused",
        description="Load wrapper rejects canonical/original fixture path in-place mutation risk.",
        release_profile="R5_SAVE_LOAD",
        turns=(
            LiveTurnSpec(
                prompt=f"Load {_CANONICAL_FIXTURE_PATH}.",
                expected_tool_calls=(
                    ToolExpectation(
                        "load_graph_explicit",
                        arguments={"path": _CANONICAL_FIXTURE_PATH},
                        require_result_ok=False,
                    ),
                ),
                semantic_checks=(
                    {
                        "kind": "tool_result",
                        "tool": "load_graph_explicit",
                        "arguments": {
                            "ok": False,
                            "error_type": "file_load_error",
                        },
                    },
                ),
            ),
        ),
    ),
]
