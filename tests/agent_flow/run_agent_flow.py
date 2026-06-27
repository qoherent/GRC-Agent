"""Run the agent autonomously on a GRC fixture and log every turn step.

Each run:
  1. Loads a fixture into a temp copy (so committed changes don't leak).
  2. Sends a user task to the agent.
  3. Streams the full tool loop — model thinking, tool calls, results.
  4. Saves a complete MD transcript to results/.

Reusable: ``_fresh_agent()`` and ``_run_scenario(name, title, prompt)`` are
imported by the test module ``tests/test_agent_flow_live.py``.

Run::

    uv run python tests/agent_flow/run_agent_flow.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.model_context import build_system_prompt
from grc_agent.toolagents_runtime import (
    ToolAgentsLlamaProviderConfig,
    ToolAgentsRunner,
)

WORKSPACE = Path(__file__).resolve().parents[2]
# Results land in the gitignored tests/output/agent_flow/ dir (regenerated on
# each run, never committed) — shared with the gated live test.
RESULTS = WORKSPACE / "tests" / "output" / "agent_flow"
RESULTS.mkdir(parents=True, exist_ok=True)

FIXTURE = WORKSPACE / "tests" / "data" / "dial_tone.grc"
FM_RX_FIXTURE = WORKSPACE / "tests" / "data" / "fm_rx.grc"
EMPTY_FIXTURE = WORKSPACE / "tests" / "data" / "empty.grc"
BROKEN_SINK_FIXTURE = WORKSPACE / "tests" / "data" / "broken_unconnected_sink.grc"
OLLAMA_URL = "http://localhost:11434"
# Custom Modelfile model: maxwell1500/ornith-9b:q4_K_M with PARAMETER num_ctx
# 120000 baked in (Ollama's /v1 endpoint ignores per-request num_ctx — see
# docs/AGENT_FLOW_FINDINGS.md). ornith-9b is a thinking model; the provider
# sends think:false (enable_thinking=False) so it answers in `content`.
MODEL = "gemma4:e4b-it-qat-120k"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)


def _load_dotenv() -> None:
    """Read .env at the workspace root into os.environ (no override).

    Sets only keys not already present in the environment, so explicit
    shell exports win. Used by the ``openrouter`` provider factory.
    """
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _default_ollama_provider() -> ToolAgentsLlamaProviderConfig:
    """The original local-Ollama provider (unchanged harness behavior)."""
    return ToolAgentsLlamaProviderConfig(
        base_url=OLLAMA_URL,
        model=MODEL,
        timeout_seconds=180.0,
        max_tokens=2048,
        temperature=0.0,
    )


def _make_provider(provider: str) -> ToolAgentsLlamaProviderConfig:
    """Return the provider config for the given provider name.

    - ``ollama`` (default): local Ollama — the original harness behavior, so
      the gated live test (which calls ``_run_scenario`` with no provider) is
      unaffected.
    - ``openrouter``: loads .env, targets OpenRouter
      (``base_url=https://openrouter.ai/api``) with the model named by
      ``OPENROUTER_MODEL``. ``create_settings`` already detects the
      ``openrouter`` host and forwards ``OPENROUTER_PROVIDER_ORDER`` /
      ``OPENROUTER_ALLOW_FALLBACKS`` via ``extra_body.provider``.

    Never overrides ``OPENROUTER_MODEL`` — whatever .env says is authoritative.
    """
    if provider == "ollama":
        return _default_ollama_provider()
    if provider == "openrouter":
        _load_dotenv()
        model = os.environ.get("OPENROUTER_MODEL")
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not model or not api_key:
            raise RuntimeError(
                "OPENROUTER_MODEL / OPENROUTER_API_KEY missing — check .env"
            )
        return ToolAgentsLlamaProviderConfig(
            base_url="https://openrouter.ai/api",
            model=model,
            api_key=api_key,
            timeout_seconds=300.0,  # cloud can be slower than local
            max_tokens=2048,
            temperature=0.0,
        )
    raise ValueError(
        f"unknown provider: {provider!r} (expected 'ollama' or 'openrouter')"
    )

SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "01_add_throttle",
        "title": "Add a throttle block inline",
        "prompt": (
            "Inspect the current flowgraph, then add a `blocks_throttle` block"
            " between `analog_sig_source_x_0` and `blocks_add_xx`."
            " Name the new block `mid_throttle`, set `type` to `float`,"
            " and use `samp_rate` for `samples_per_second`."
            " Re-wire the connections so the throttle sits inline."
            " After the changes, inspect the result to confirm."
        ),
        "expect": {"mode": "edit", "blocks_present": ["mid_throttle"], "valid": True},
    },
    {
        "name": "02_update_sample_rate",
        "title": "Update the sample rate variable",
        "prompt": (
            "Inspect the current flowgraph. Then update the `samp_rate`"
            " variable to `48000`. Confirm the change by inspecting again."
        ),
        "expect": {
            "mode": "edit",
            "params": {"samp_rate": {"value": "48000"}},
            "valid": True,
        },
    },
    {
        "name": "03_disable_and_enable",
        "title": "Disable a block, inspect, then re-enable it",
        "prompt": (
            "Inspect the current flowgraph. Then disable the block"
            " `analog_noise_source_x_0` and inspect the result."
            " Finally, re-enable it and confirm."
        ),
        "expect": {"mode": "edit", "valid": True},
    },
    {
        "name": "04_add_and_remove_variable",
        "title": "Add a variable, use it, then remove it",
        "prompt": (
            "Inspect the current flowgraph. Add a new `variable` block"
            " named `gain_value` with `value` set to `2.0`."
            " Then update the `analog_sig_source_x_0` block's `amp`"
            " parameter to use `gain_value`."
            " Finally, inspect the result to confirm both changes."
        ),
        "expect": {
            "mode": "edit",
            "blocks_present": ["gain_value"],
            "params": {"analog_sig_source_x_0": {"amp": "gain_value"}},
            "valid": True,
        },
    },
    {
        "name": "05_full_rewire",
        "title": "Remove a block and rewire around it",
        "prompt": (
            "Inspect the current flowgraph. Remove the `analog_noise_source_x_0`"
            " block. Then add a new `analog_const_source_x` block named"
            " `dc_offset` with `const` set to `0.0`. Connect `dc_offset`"
            " port 0 to `blocks_add_xx` port 2 (replacing the noise path)."
            " Inspect the final result to confirm the changes."
        ),
        "expect": {
            "mode": "edit",
            "blocks_absent": ["analog_noise_source_x_0"],
            "blocks_present": ["dc_offset"],
            "valid": True,
        },
    },
    {
        "name": "06_query_knowledge_multiply",
        "title": "Discover an unknown block via query_knowledge (multiply)",
        "prompt": (
            "Inspect the current flowgraph. I want to multiply the two"
            " sinusoid sources together instead of adding them. The exact"
            " GNU Radio block_id for a signal multiplier is not something"
            " to guess: use query_knowledge (domain catalog) to look it up"
            " first, then add the block named `multiplier` with `type` set"
            " to `float`. Connect the two existing `analog_sig_source_x`"
            " outputs into the multiplier, remove the old `blocks_add_xx`,"
            " and inspect the result to confirm."
        ),
        "expect": {
            "mode": "edit",
            "blocks_present": ["multiplier"],
            "blocks_absent": ["blocks_add_xx"],
            "valid": True,
        },
    },
    {
        "name": "07_force_disabled_connected_block",
        "title": "Disable a connected block and force-commit if invalid",
        "prompt": (
            "Inspect the current flowgraph. Disable the block"
            " `analog_sig_source_x_0`, which is connected into the adder."
            " If disabling a connected block makes the graph fail"
            " validation, use force to commit the change anyway. Then"
            " inspect the result to confirm the block is disabled."
        ),
        # Success = the block is actually disabled (the task's intent). Graph
        # validity is a conditional side effect (only invalid if the disable
        # orphans a port), so we assert state, not validity.
        "expect": {
            "mode": "edit",
            "states": {"analog_sig_source_x_0": "disabled"},
        },
    },
    {
        "name": "08_fm_rx_insert_throttle",
        "title": "Insert a throttle on a larger FM receiver graph",
        "fixture": str(FM_RX_FIXTURE),
        "prompt": (
            "Inspect the current flowgraph (this is an FM receiver). Add a"
            " `blocks_throttle` block named `audio_throttle` with `type` set"
            " to `float` and `samples_per_second` set to `audio_rate`."
            " Insert it inline on the connection from"
            " `pfb_arb_resampler_xxx_0` to `audio_sink_0`: remove that"
            " connection, then route the resampler output through the"
            " throttle into the audio sink. Inspect the result to confirm."
        ),
        "expect": {
            "mode": "edit",
            "blocks_present": ["audio_throttle"],
            "valid": True,
        },
    },
    # --- coverage expansion: docs domain, scoped inspect, multiblock, bypass ---
    {
        "name": "09_docs_stream_tags_concept",
        "title": "Pure docs-domain Q&A (read-only)",
        "prompt": (
            "I'm learning GNU Radio. Use `query_knowledge` with the **docs**"
            " domain to explain what a 'stream tag' is and how tags move"
            " through a flowgraph. Summarize what the documentation says."
            " Don't change the graph."
        ),
        # Read-only: success = a read/answer tool used + a non-empty answer.
        "expect": {"mode": "read"},
    },
    {
        "name": "10_bypass_source_block",
        "title": "Set a block to the bypass state",
        "prompt": (
            "Inspect the flowgraph. Put the `analog_sig_source_x_0` block"
            " into the `bypass` state (use update_states with state set to"
            " `bypass`). Inspect again to confirm its state is now `bypass`."
        ),
        # GRC canonicalizes the model-friendly "bypass" alias to "bypassed"
        # (adapter alias in set_block_state). Assert the canonical stored form.
        "expect": {"mode": "edit", "states": {"analog_sig_source_x_0": "bypassed"}},
    },
    {
        "name": "11_scoped_inspect_and_update",
        "title": "Targets-scoped inspect, then a param update",
        "prompt": (
            "This flowgraph has several blocks. Inspect ONLY the blocks"
            " `samp_rate` and `analog_sig_source_x_0` by passing them as the"
            " `targets` argument to `inspect_graph` (not the full overview)."
            " Then change the `samp_rate` variable's `value` to `96000`."
            " Inspect those same two blocks again to confirm."
        ),
        "expect": {
            "mode": "edit",
            "params": {"samp_rate": {"value": "96000"}},
            "valid": True,
        },
    },
    {
        "name": "12_multiblock_batch_chain",
        "title": "Add two blocks in a single change_graph call",
        "prompt": (
            "Inspect the flowgraph. In a SINGLE `change_graph` call, add two"
            " throttle blocks: `pre_throttle` and `post_throttle` (both"
            " `blocks_throttle`, `type`=`float`,"
            " `samples_per_second`=`samp_rate`). Wire them inline between"
            " `analog_sig_source_x_0` and `blocks_add_xx`: remove"
            " `analog_sig_source_x_0:0->blocks_add_xx:0`, then add"
            " `analog_sig_source_x_0:0->pre_throttle:0`,"
            " `pre_throttle:0->post_throttle:0`,"
            " `post_throttle:0->blocks_add_xx:0`. Inspect to confirm."
        ),
        "expect": {
            "mode": "edit",
            "blocks_present": ["pre_throttle", "post_throttle"],
            "valid": True,
        },
    },
    {
        "name": "13_docs_informed_param_edit",
        "title": "Docs-informed parameter edit",
        "prompt": (
            "Inspect the flowgraph. First use `query_knowledge` with the"
            " **docs** domain to read how the `analog_sig_source_x` block's"
            " `freq` parameter works. Then set `analog_sig_source_x_0`'s"
            " `freq` to `1000`. Inspect to confirm."
        ),
        "expect": {
            "mode": "edit",
            "params": {"analog_sig_source_x_0": {"freq": "1000"}},
            "valid": True,
        },
    },
    # --- realistic-workflow expansion (creation, diagnosis, param->port, expr, taps) ---
    {
        "name": "14_build_chain_from_scratch",
        "title": "Build a signal->throttle->sink chain on an empty graph",
        "fixture": str(EMPTY_FIXTURE),
        "prompt": (
            "Inspect the current flowgraph. It's empty except for `samp_rate`."
            " Build a minimal signal generator chain: add an"
            " `analog_sig_source_x` named `sig` (`type`=`float`, `freq`=`1000`,"
            " `amp`=`0.5`, `samp_rate`=`samp_rate`), a `blocks_throttle` named"
            " `throttle` (`type`=`float`, `samples_per_second`=`samp_rate`), and"
            " a `blocks_null_sink` named `sink` (`type`=`float`). Wire"
            " `sig:0->throttle:0` and `throttle:0->sink:0`. Inspect to confirm"
            " the chain is valid."
        ),
        "expect": {
            "mode": "edit",
            "blocks_present": ["sig", "throttle", "sink"],
            "valid": True,
        },
    },
    {
        "name": "15_broken_graph_diagnose_fix",
        "title": "Diagnose a pre-broken (invalid) graph, then fix it",
        "fixture": str(BROKEN_SINK_FIXTURE),
        "prompt": (
            "This graph has a problem — GRC is showing a validation error and"
            " it won't generate. Inspect it, read the validation errors, and"
            " figure out exactly what's wrong. Then fix it so the signal"
            " actually reaches the `audio_sink`. Inspect again to confirm the"
            " graph is valid."
        ),
        # Fix = connect the dangling throttle output to the audio_sink input.
        # valid:true is the sound proxy (the dangling port fails native validation).
        "expect": {"mode": "edit", "valid": True},
    },
    {
        "name": "16_expand_adder_input",
        "title": "Add a 3rd tone by expanding the adder's num_inputs",
        "prompt": (
            "Inspect the flowgraph. The `blocks_add_xx` mixer has 3 inputs and"
            " they're all taken. Add a third musical tone: a new"
            " `analog_sig_source_x` named `third_tone` (`type`=`float`,"
            " `freq`=`550`, `amp`=`ampl`, `samp_rate`=`samp_rate`). Give the"
            " adder a 4th input and connect `third_tone:0` into it. Inspect to"
            " confirm."
        ),
        "expect": {
            "mode": "edit",
            "blocks_present": ["third_tone"],
            "params": {"blocks_add_xx": {"num_inputs": "4"}},
            "valid": True,
        },
    },
    {
        "name": "17_expression_variables_chain",
        "title": "Variables referencing variables/math, used across blocks",
        "prompt": (
            "Inspect the flowgraph. Make the two tones a musical interval. Add"
            " a `variable` named `base_freq` with `value`=`220.0`, and a second"
            " `variable` named `fifth` with `value`=`base_freq * 1.5` (a perfect"
            " fifth). Then point `analog_sig_source_x_0`'s `freq` at `base_freq`"
            " and `analog_sig_source_x_1`'s `freq` at `fifth`. Inspect to confirm"
            " both sources now reference the new variables."
        ),
        "expect": {
            "mode": "edit",
            "blocks_present": ["base_freq", "fifth"],
            "params": {
                "analog_sig_source_x_0": {"freq": "base_freq"},
                "analog_sig_source_x_1": {"freq": "fifth"},
            },
            "valid": True,
        },
    },
    {
        "name": "18_fm_rx_bypass_deemph_stage",
        "title": "Bypass an intermediate FM stage with a 3-branch fan-out",
        "fixture": str(FM_RX_FIXTURE),
        "prompt": (
            "Inspect this FM receiver. I want to hear the raw demodulated"
            " signal with no de-emphasis. Remove `analog_fm_deemph_0` from the"
            " signal path entirely and patch the chain so"
            " `analog_quadrature_demod_cf_0:0` feeds everything the deemph block"
            " used to feed: connect it to `pfb_arb_resampler_xxx_0:0`,"
            " `qtgui_freq_sink_x_0:0`, and `qtgui_time_sink_x_0_0_0:0`. Remove"
            " the old edges from `analog_fm_deemph_0` and then delete the"
            " `analog_fm_deemph_0` block. Inspect to confirm the graph is still"
            " valid."
        ),
        "expect": {
            "mode": "edit",
            "blocks_absent": ["analog_fm_deemph_0"],
            "valid": True,
        },
    },
    {
        "name": "19_fm_rx_add_demod_probe",
        "title": "Add a QT GUI time-sink tap to visualize a stage",
        "fixture": str(FM_RX_FIXTURE),
        "prompt": (
            "Inspect this FM receiver. I want to watch the demodulated signal"
            " right after the quadrature demod, before de-emphasis. Add a"
            " `qtgui_time_sink_x` named `demod_probe` (`type`=`float`,"
            " `srate`=`in_rate`) and tap it onto `analog_quadrature_demod_cf_0:0`"
            " (connect `analog_quadrature_demod_cf_0:0->demod_probe:0`). Inspect"
            " to confirm."
        ),
        # A qtgui sink left unwired fails validation, so valid:true implies the
        # fan-out tap actually landed.
        "expect": {
            "mode": "edit",
            "blocks_present": ["demod_probe"],
            "valid": True,
        },
    },
]


def _fresh_agent(
    fixture: str | Path | None = None,
    model: str = MODEL,
) -> tuple[GrcAgent, Path]:
    """Create a fresh agent from a temp copy of the fixture.

    Returns the agent and the path to the temp fixture file (for reading
    its .grc content before/after the scenario for the MD transcript).
    """
    src = Path(fixture) if fixture is not None else FIXTURE
    tmp = tempfile.mkdtemp(prefix="grc_agent_flow_")
    tmp_fixture = Path(tmp) / src.name
    shutil.copy2(src, tmp_fixture)
    session = FlowgraphSession()
    session.load(str(tmp_fixture))
    return GrcAgent(session=session, llama_model=model), tmp_fixture


def _graph_state(fixture_path: Path) -> dict[str, Any]:
    """Reload the (mutated) fixture and capture post-run topology truth.

    Validity comes from a FRESH ``validate()`` (rewrite + validate + is_valid),
    NEVER from a tool's ``ok`` flag and NEVER from a stale ``is_valid()`` on a
    freshly-loaded graph (which would report valid=True for an actually-invalid
    graph if the model never edited it — a false positive). Per AGENTS.md
    "evidence before assertions" + "prefer native methods".
    """
    from grc_agent.grc_native_adapter import load_flow_graph, render_flow_graph, validate

    fg = load_flow_graph(fixture_path)
    valid = bool(validate(fg).native_ok)  # fresh: rewrite + validate + is_valid
    snap = render_flow_graph(fg)
    return {
        "valid": valid,
        "instance_names": sorted(b.instance_name for b in snap.blocks),
        "params": {b.instance_name: dict(b.params) for b in snap.blocks},
        "states": {b.instance_name: b.state for b in snap.blocks},
    }


def _run_scenario(
    name: str,
    title: str,
    prompt: str,
    fixture: str | Path | None = None,
    expect: dict[str, Any] | None = None,
    provider_config: ToolAgentsLlamaProviderConfig | None = None,
) -> dict[str, Any]:
    # Default provider = the original local-Ollama config, so the gated live
    # test (which calls _run_scenario(**sc) with no provider) is unchanged.
    if provider_config is None:
        provider_config = _default_ollama_provider()
    active_model = provider_config.model or MODEL

    agent, fixture_path = _fresh_agent(fixture, model=active_model)
    system_prompt = build_system_prompt(agent.chat_session_id)
    grc_before = fixture_path.read_text(encoding="utf-8")

    runner = ToolAgentsRunner(provider_config=provider_config)

    events: list[dict[str, Any]] = []
    pending_tool: dict[str, Any] = {}

    def _on_tool_start(tool_name: str, args: dict[str, Any]) -> None:
        pending_tool["name"] = tool_name
        pending_tool["args"] = dict(args)

    def _on_tool_end(tool_name: str, result: Any) -> None:
        pass

    for event in runner.stream_turn(
        agent,
        prompt,
        max_tool_rounds=None,
        on_tool_start=_on_tool_start,
        on_tool_end=_on_tool_end,
    ):
        ev_copy = dict(event)
        if event.get("event") == "model_message" and event.get("role") == "tool_model" and pending_tool:
            ev_copy["tool_called"] = dict(pending_tool)
            pending_tool.clear()
        events.append(ev_copy)

    grc_after = fixture_path.read_text(encoding="utf-8")
    graph_state = _graph_state(fixture_path)

    return {
        "name": name,
        "title": title,
        "prompt": prompt,
        "system_prompt": system_prompt,
        "model": active_model,
        "fixture_name": fixture_path.name,
        "expect": expect or {},
        "grc_before": grc_before,
        "grc_after": grc_after,
        "graph_state": graph_state,
        "events": events,
    }


def _render_md(rec: dict[str, Any]) -> str:
    """Minimal tool-call trace: the exact raw inputs/outputs the model saw.

    Layout per scenario:
      1. one-line header (title, fixture, model)
      2. system prompt (raw)
      3. user prompt (raw)
      4. grc_before (raw YAML)
      5. a flat numbered list of every tool call the model made, each paired
         with the exact ``tool_call_result`` string the model received — the
         raw bytes are preserved verbatim (no json roundtrip), so the file
         shows what the model actually saw.
      6. the raw final result dict.

    grc_after is reconstructible from the tool results (the last successful
    change_graph / render leaves the fixture mutated on disk) and is omitted
    to avoid duplication. Turn headings, chunk text, and assistant reasoning
    are dropped — the focus is the model's tool I/O.
    """
    parts: list[str] = [
        f"# {rec['title']}",
        "",
        f"**Scenario:** `{rec['name']}` | **Fixture:** `{rec.get('fixture_name', '?')}` | **Model:** `{rec.get('model', MODEL)}`",
        "",
        "## System Prompt",
        "",
        "```text",
        rec.get("system_prompt", "(not captured)"),
        "```",
        "",
        "## User Prompt",
        "",
        "```text",
        rec["prompt"],
        "```",
        "",
        "## Flowgraph: BEFORE",
        "",
        "```yaml",
        rec.get("grc_before", "(not captured)"),
        "```",
        "",
        "## Tool calls (raw inputs + outputs the model saw)",
        "",
    ]

    call_idx = 0
    final_result: dict[str, Any] | None = None
    for ev in rec["events"]:
        if ev.get("event") == "model_message" and ev.get("role") == "tool_model":
            tc = ev.get("tool_called") or {}
            tool_name = tc.get("name")
            if not tool_name:
                continue  # no tool called in this event (e.g. pure assistant chunk)
            call_idx += 1
            parts.append(f"### call {call_idx} — `{tool_name}`")
            parts.append("")
            parts.append("**args (model sent):**")
            parts.append("")
            parts.append("```json")
            parts.append(json.dumps(tc.get("args", {}), indent=2, default=str))
            parts.append("```")
            parts.append("")
            # The raw tool_call_result string is the exact bytes the model saw.
            payload = ev.get("payload", {}) or {}
            content = payload.get("content") or []
            entry = content[0] if content else {}
            result_text = entry.get("tool_call_result", "")
            parts.append("**result (model saw this exact string):**")
            parts.append("")
            if isinstance(result_text, str) and result_text:
                # Verbatim — no json roundtrip — so what the model saw is
                # what the reviewer sees.
                parts.append("```")
                parts.append(result_text)
                parts.append("```")
            else:
                # Empty / missing result — record it explicitly.
                parts.append("```")
                parts.append("")
                parts.append("```")
            parts.append("")
        elif ev.get("event") == "final":
            final_result = ev.get("result", {}) or {}

    if final_result is not None:
        parts.append("## Final result (raw)")
        parts.append("")
        parts.append("```json")
        parts.append(json.dumps(final_result, indent=2, default=str))
        parts.append("```")
        parts.append("")

    return "\n".join(parts)


def _extract_metrics(rec: dict[str, Any]) -> dict[str, Any]:
    """Extract uniform semantic metrics from a scenario record.

    All metrics are scenario-agnostic — they measure agent-loop behavior,
    not scenario-specific success criteria.
    """
    events = rec["events"]
    final = next(
        (e.get("result", {}) for e in events if e.get("event") == "final"), {}
    )
    final_text = (final.get("assistant_text") or "").strip()
    hit_ceiling = final.get("error_type") == "safety_ceiling_reached"

    n_assistant = sum(1 for e in events if e.get("role") == "assistant_model")
    n_tool_model = sum(1 for e in events if e.get("role") == "tool_model")

    tool_calls: list[dict[str, Any]] = []
    for e in events:
        tc = e.get("tool_called")
        if tc and tc.get("name"):
            tool_calls.append(tc)

    tool_counts: dict[str, int] = {}
    n_change_graph_ok = 0
    n_change_graph_failed = 0
    n_change_graph_force = 0
    n_force_used = 0
    n_inline_insert_batches = 0
    n_inline_insert_committed = 0
    n_query_knowledge_calls = 0

    for e in events:
        if e.get("role") != "tool_model":
            continue

        tc = e.get("tool_called") or {}
        tool_name = tc.get("name")
        args = tc.get("args") or {}
        if tool_name:
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
        if tool_name == "query_knowledge":
            n_query_knowledge_calls += 1

        payload = e.get("payload", {})
        content = payload.get("content", [])
        result_str = content[0].get("tool_call_result") if content else None
        if not result_str or not isinstance(result_str, str):
            continue
        try:
            result_obj = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(result_obj, dict):
            continue

        is_inline = (
            bool(args.get("remove_connections"))
            and bool(args.get("add_connections"))
        )
        if is_inline:
            n_inline_insert_batches += 1

        if "ok" in result_obj and tool_name == "change_graph":
            if result_obj.get("ok"):
                n_change_graph_ok += 1
                if is_inline:
                    n_inline_insert_committed += 1
                has_gnu_val = any(
                    e.get("code") == "gnu_validation"
                    for e in (result_obj.get("errors") or [])
                )
                if has_gnu_val:
                    n_change_graph_force += 1
            else:
                n_change_graph_failed += 1
            if args.get("force") is True:
                n_force_used += 1

    # Expect-based success: assert against the resulting graph topology via
    # GRC's native is_valid() — NEVER against a tool's ``ok`` flag. ``ok`` means
    # "edits applied", which for a force-commit is true even on an invalid graph.
    # Per AGENTS.md "evidence before assertions" + "prefer native methods".
    expect = rec.get("expect") or {}
    mode = expect.get("mode", "edit")
    graph_state = rec.get("graph_state") or {}
    graph_valid = bool(graph_state.get("valid", False))
    instance_names = set(graph_state.get("instance_names", []))
    params_actual = graph_state.get("params") or {}

    fail_reasons: list[str] = []
    if hit_ceiling:
        fail_reasons.append("safety_ceiling")
    if mode == "read":
        # Read-only task: success = a read/answer tool was used + a non-empty answer.
        if not (n_query_knowledge_calls > 0 or tool_counts.get("inspect_graph", 0) > 0):
            fail_reasons.append("no read tool used")
        if not final_text:
            fail_reasons.append("empty answer")
    else:
        if not final_text:
            fail_reasons.append("empty final text")
        for blk in (expect.get("blocks_present") or []):
            if blk not in instance_names:
                fail_reasons.append(f"missing block {blk}")
        for blk in (expect.get("blocks_absent") or []):
            if blk in instance_names:
                fail_reasons.append(f"block {blk} still present")
        if "valid" in expect and graph_valid != bool(expect["valid"]):
            fail_reasons.append(f"graph valid={graph_valid} expected {expect['valid']}")
        states_actual = graph_state.get("states") or {}
        for inst, st in (expect.get("states") or {}).items():
            if str(states_actual.get(inst, "")) != str(st):
                fail_reasons.append(f"state {inst}={states_actual.get(inst)!r} expected {st!r}")
        for inst, pv in (expect.get("params") or {}).items():
            actual = params_actual.get(inst, {})
            for k, v in pv.items():
                if str(actual.get(k, "")) != str(v):
                    fail_reasons.append(f"param {inst}.{k}={actual.get(k)!r} expected {v!r}")

    semantic_success = not fail_reasons
    expect_reason = "; ".join(fail_reasons) if fail_reasons else "ok"

    return {
        "scenario": rec["name"],
        "title": rec.get("title", ""),
        "model": rec.get("model", MODEL),
        "mode": mode,
        "n_assistant_turns": n_assistant,
        "n_tool_calls": n_tool_model,
        "tool_counts": tool_counts,
        "n_change_graph_calls": tool_counts.get("change_graph", 0),
        "n_change_graph_ok": n_change_graph_ok,
        "n_change_graph_failed": n_change_graph_failed,
        "n_change_graph_force": n_change_graph_force,
        "n_force_used": n_force_used,
        "n_query_knowledge_calls": n_query_knowledge_calls,
        "n_inline_insert_batches": n_inline_insert_batches,
        "n_inline_insert_committed": n_inline_insert_committed,
        "hit_safety_ceiling": hit_ceiling,
        "empty_final_text": not bool(final_text),
        "graph_valid": graph_valid,
        "expect_reason": expect_reason,
        "semantic_success": semantic_success,
    }


def _render_summary(all_metrics: list[dict[str, Any]]) -> str:
    """Render a compact summary table across all scenarios.

    Success is the expect-based verdict (graph-state match via native
    is_valid), and the Reason column states WHY a scenario passed/failed —
    not just a bare ✓/✗.
    """
    lines = [
        "## Expect-Based Metrics Summary",
        "",
        "| Scenario | mode | Turns | CG ok | QK | force | graphValid | Pass | Reason |",
        "|----------|:----:|------:|------:|---:|------:|:----------:|:----:|--------|",
    ]
    for m in all_metrics:
        reason = m.get("expect_reason", "")
        if len(reason) > 60:
            reason = reason[:57] + "..."
        lines.append(
            f"| {m['scenario']} "
            f"| {m.get('mode', '?')} "
            f"| {m['n_assistant_turns']} "
            f"| {m['n_change_graph_ok']} "
            f"| {m['n_query_knowledge_calls']} "
            f"| {m['n_force_used']} "
            f"| {'V' if m.get('graph_valid') else 'INV'} "
            f"| {'✓' if m['semantic_success'] else '✗'} "
            f"| {reason} |"
        )

    n = len(all_metrics)
    n_success = sum(1 for m in all_metrics if m["semantic_success"])
    n_ceiling = sum(1 for m in all_metrics if m["hit_safety_ceiling"])
    n_empty = sum(1 for m in all_metrics if m["empty_final_text"])
    n_qk = sum(1 for m in all_metrics if m["n_query_knowledge_calls"] > 0)
    n_force = sum(1 for m in all_metrics if m["n_force_used"] > 0)
    n_inline = sum(m["n_inline_insert_batches"] for m in all_metrics)
    n_inline_ok = sum(m["n_inline_insert_committed"] for m in all_metrics)
    total_ok = sum(m["n_change_graph_ok"] for m in all_metrics)

    lines.extend(
        [
            "",
            f"**Expect-based success:** {n_success}/{n}",
            f"**Safety ceiling hits:** {n_ceiling}/{n}",
            f"**Empty final text:** {n_empty}/{n}",
            f"**Used query_knowledge:** {n_qk}/{n}",
            f"**Used force flag:** {n_force}/{n}",
            f"**Inline-insert batches:** {n_inline} total, {n_inline_ok} ok",
            f"**Total ok change_graph calls:** {total_ok}",
            "",
        ]
    )
    return "\n".join(lines)


def main(runs: int = 1, provider: str = "ollama") -> None:
    provider_config = _make_provider(provider)
    active_model = provider_config.model or MODEL
    print(f"Provider: {provider} | Model: {active_model}")
    print(f"Fixture: {FIXTURE.name}")
    print(f"Scenarios: {len(SCENARIOS)} | runs per scenario: {runs}")
    print("Max tool rounds: system default (8)")
    print()

    all_metrics: list[dict[str, Any]] = []
    pass_rates: dict[str, str] = {}
    for sc in SCENARIOS:
        fixture_name = Path(sc.get("fixture", FIXTURE)).name
        passed = 0
        last_metrics: dict[str, Any] | None = None
        for attempt in range(runs):
            tag = f" r{attempt + 1}/{runs}" if runs > 1 else ""
            print(
                f"  [{sc['name']}]{tag} {sc['title']} ({fixture_name}) ...",
                end=" ",
                flush=True,
            )
            try:
                rec = _run_scenario(**sc, provider_config=provider_config)
                if attempt == 0:  # keep the first run's transcript on disk
                    md = _render_md(rec)
                    (RESULTS / f"{sc['name']}.md").write_text(md, encoding="utf-8")
                metrics = _extract_metrics(rec)
                last_metrics = metrics
                if metrics["semantic_success"]:
                    passed += 1
                status = "✓" if metrics["semantic_success"] else "✗"
                print(
                    f"[{status}] {metrics['n_assistant_turns']} turns,"
                    f" {metrics['n_change_graph_ok']} ok"
                    f" -> {metrics.get('expect_reason', '')}"
                )
            except Exception as exc:
                print(f"[FAIL] {type(exc).__name__}: {exc}")
                md = _render_md(
                    {
                        "name": sc["name"],
                        "title": sc["title"],
                        "prompt": sc["prompt"],
                        "fixture_name": fixture_name,
                        "system_prompt": "(error before capture)",
                        "grc_before": "(error before capture)",
                        "grc_after": "(error before capture)",
                        "events": [
                            {"event": "final", "result": {"ok": False, "assistant_text": str(exc)}}
                        ],
                    }
                )
                (RESULTS / f"{sc['name']}.md").write_text(md, encoding="utf-8")

        if last_metrics is not None:
            all_metrics.append(last_metrics)
        if runs > 1:
            pass_rates[sc["name"]] = f"{passed}/{runs}"

    summary = _render_summary(all_metrics)
    if pass_rates:
        summary += "**Pass-rate (k/N):**\n" + "\n".join(
            f"- {name}: {rate}" for name, rate in pass_rates.items()
        ) + "\n\n"
    (RESULTS / "METRICS.md").write_text(summary, encoding="utf-8")
    (RESULTS / "metrics.json").write_text(
        json.dumps(all_metrics, indent=2, default=str), encoding="utf-8"
    )
    print(f"\n{summary}")
    print(f"Results: {RESULTS.relative_to(WORKSPACE)}")
    print(f"Metrics: {RESULTS.relative_to(WORKSPACE) / 'metrics.json'}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1, help="runs per scenario (pass-rate mode)")
    ap.add_argument(
        "--provider",
        choices=("ollama", "openrouter"),
        default="ollama",
        help="model provider (default ollama; openrouter loads .env)",
    )
    args = ap.parse_args()
    main(runs=args.runs, provider=args.provider)
