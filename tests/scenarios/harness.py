"""Scenario/benchmark harness for the GRC agent.

Lives with the tests that consume it, not in the installed package: nothing
in ``src/grc_agent`` imports any of this, and shipping a 15-entry benchmark
corpus inside the wheel served no runtime purpose. The consumers are
``tests/test_integration.py`` and ``tests/test_button_integration.py`` (both
``@pytest.mark.integration``) plus ``tests/test_scenarios_harness.py``.

A prior pydantic-graph-based runner also lived here and was dead: its step
return-type hints were bare forward-referenced function names, which
pydantic-graph does not resolve into edges, so building the graph raised
GraphValidationError on the only two ways to reach it. Neither was a
registered entry point.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from grc_agent.adapter import inspect_graph, load_flow_graph
from grc_agent.prompts import build_system_prompt

# Fixed model name for reproducible benchmarking — never read from env
MODEL = "qwen3.6:35b-a3b-q4_K_M"
OLLAMA_V1 = "http://localhost:11434/v1"


def build_scenario_model(provider: str, model_name: str | None = None) -> Any:
    """Construct a model for the scenario harness based on the provider string.

    Scenarios can run against different backends (Ollama, OpenRouter, etc.)
    depending on the environment.
    """
    if provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY") or "not-required"
        return OpenRouterModel(
            model_name or os.environ.get("OPENROUTER_MODEL", "z-ai/glm-5.3-flash"),
            provider=OpenRouterProvider(api_key=key),
        )
    if provider == "openai_compatible":
        key = os.environ.get("OPENAI_COMPATIBLE_API_KEY") or "not-required"
        raw_url = (
            os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or "http://localhost:8080/v1"
        ).rstrip("/")
        base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
        return OpenAIChatModel(
            model_name
            or os.environ.get("OPENAI_COMPATIBLE_MODEL", "deepseek/deepseek-v4-flash"),
            provider=OpenAIProvider(base_url=base_url, api_key=key),
        )
    if provider == "ollama_cloud":
        key = os.environ.get("OLLAMA_CLOUD_API_KEY", "")
        return OllamaModel(
            model_name or os.environ.get("OLLAMA_CLOUD_MODEL", "deepseek-v4-flash:0731"),
            provider=OllamaProvider(
                base_url="https://ollama.com/v1",
                api_key=key,
            ),
        )
    raw_url = (os.environ.get("OLLAMA_BASE_URL") or OLLAMA_V1).rstrip("/")
    base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
    key = os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_CLOUD_API_KEY")
    return OllamaModel(model_name or MODEL, provider=OllamaProvider(base_url=base_url, api_key=key))


SCENARIOS = [
    {
        "name": "01_add_throttle",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Take a look at the flowgraph, then add a throttle block in the"
            " path between the 350 Hz tone and the adder that mixes the tones"
            " together. Call it `mid_throttle`, set its type to float, and"
            " have it use the samp_rate variable for its rate. Make sure the"
            " wiring is rerouted so it actually sits inline. Then inspect the"
            " result to confirm."
        ),
        "expect": {"blocks_present": ["mid_throttle"], "valid": True},
    },
    {
        "name": "02_update_sample_rate",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the current flowgraph. Then update the `samp_rate`"
            " variable to `48000`. Confirm the change by inspecting again."
        ),
        "expect": {
            "params": {"samp_rate": {"value": "48000"}},
            "valid": True,
        },
    },
    {
        "name": "03_disable_and_enable",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph, then disable the noise source that's"
            " mixed into the audio output. Inspect again to confirm it's"
            " off. Then turn it back on and confirm."
        ),
        "expect": {"valid": True},
    },
    {
        "name": "04_add_and_remove_variable",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph. Add a new variable called `gain_value`"
            " set to 2.0, then have the 350 Hz tone's amplitude use that"
            " variable instead of its current value. Inspect to confirm"
            " both changes landed."
        ),
        "expect": {
            "blocks_present": ["gain_value"],
            "params": {"analog_sig_source_x_0": {"amp": "gain_value"}},
            "valid": True,
        },
    },
    {
        "name": "05_full_rewire",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph. I don't want the noise source anymore —"
            " remove it. In its place, add a constant source block, call it"
            " `dc_offset`, with its constant value set to 0.0, and wire its"
            " output into the same input on the adder that the noise source"
            " used to feed. Inspect the result to confirm the change."
        ),
        "expect": {
            "blocks_absent": ["analog_noise_source_x_0"],
            "blocks_present": ["dc_offset"],
            "valid": True,
        },
    },
    {
        "name": "06_query_knowledge_multiply",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph. I want to multiply the two sine wave"
            " tones together instead of adding them. Look up the right GNU"
            " Radio block for a signal multiplier using query_knowledge"
            " (catalog domain) — don't guess the block id. Add it, call it"
            " `multiplier`, set its type to float, wire both tone sources"
            " into it, and remove the adder that's currently combining"
            " them. Inspect the result to confirm."
        ),
        "expect": {
            "blocks_present": ["multiplier"],
            "blocks_absent": ["blocks_add_xx"],
            "valid": True,
        },
    },
    {
        "name": "09_docs_stream_tags_concept",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "I'm learning GNU Radio. Use `query_knowledge` with the **docs**"
            " domain to explain what a 'stream tag' is and how tags move"
            " through a flowgraph. Summarize what the documentation says."
            " Don't change the graph."
        ),
        "expect": {"mode": "read"},
    },
    {
        "name": "10_bypass_source_block",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph, then put the 350 Hz tone source into"
            " bypass mode. Inspect again to confirm it actually switched"
            " to bypass."
        ),
        "expect": {"states": {"analog_sig_source_x_0": "bypass"}},
    },
    {
        "name": "11_scoped_inspect_and_update",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "This flowgraph has several blocks in it. Using inspect_graph's"
            " targets option, look at just the sample rate variable and the"
            " 350 Hz tone source — don't pull the whole overview. Then"
            " change the sample rate to 96000. Check just those same two"
            " blocks again to confirm."
        ),
        "expect": {
            "params": {"samp_rate": {"value": "96000"}},
            "valid": True,
        },
    },
    {
        "name": "14_build_chain_from_scratch",
        "fixture": "tests/data/empty.grc",
        "prompt": (
            "Inspect the flowgraph — right now it's empty except for the"
            " samp_rate variable. Build a minimal signal chain: a signal"
            " source called `sig` (type float, freq 1000, amp 0.5, using"
            " samp_rate), a throttle called `throttle` (type float,"
            " samples_per_second using samp_rate), and a null sink called"
            " `sink` (type float). Wire the source into the throttle, and"
            " the throttle into the sink. Inspect to confirm the chain is"
            " valid."
        ),
        "expect": {
            "blocks_present": [["sig", "sig_source"], "throttle", "sink"],
            "valid": True,
        },
    },
    {
        "name": "21_type_conversion_and_conjugate",
        "fixture": "tests/data/resampler_demo.grc",
        "prompt": (
            "Inspect the flowgraph. I want to make some changes:\n"
            "1. Search the catalog for a block that converts a float stream"
            " into a complex stream, and also for a block that computes the"
            " complex conjugate of a complex signal.\n"
            "2. The FM modulator in this chain isn't needed anymore —"
            " replace it entirely with the float-to-complex converter you"
            " found. Call the converter `float_to_complex_converter`.\n"
            "3. Wire the throttle's output into the converter's real-part"
            " input.\n"
            "4. Search the catalog for a constant source block. Add one,"
            " call it `zero_imag`, type float, constant value 0.0, and wire"
            " it into the converter's imaginary-part input so the converter"
            " has a valid complex input.\n"
            "5. Connect the converter's output to both the resampler and"
            " the original spectrum display that the FM modulator used to"
            " feed.\n"
            "6. Add the complex conjugate block, call it `signal_conjugate`,"
            " and insert it right after the resampler, before the resampled"
            " spectrum display — so the resampler's output goes through the"
            " conjugate block before reaching that display.\n"
            "7. Remove the old FM modulator block entirely, make sure the"
            " flowgraph is valid, and inspect it to confirm."
        ),
        "expect": {
            "blocks_present": ["float_to_complex_converter", "zero_imag", "signal_conjugate"],
            "blocks_absent": ["analog_frequency_modulator_fc_0"],
            "valid": True,
        },
    },
    {
        "name": "22_fm_rx_filter_squelch",
        "fixture": "tests/data/fm_rx.grc",
        "prompt": (
            "Upgrade this FM Receiver flowgraph to add a band-limiting filter and a noise squelch:\n"
            "1. Search the catalog for a standard low pass filter block.\n"
            "2. Add it to the flowgraph, call it `channel_filter`. Set its type parameter to `fir_filter_ccf` "
            "(complex input/output, float taps). Set its sample rate to `in_rate`, cutoff frequency to `100e3`, "
            "and transition width to `20e3`.\n"
            "3. Search the catalog for a squelch block that operates on complex signals. Add it, call it "
            "`signal_squelch`, type `analog_simple_squelch_cc`. Set its threshold to `-50` and alpha to `0.01`.\n"
            "4. Insert `channel_filter` right after the file source (`blocks_file_source_0`). The file source's "
            "output must go into the filter's input. The time display (`qtgui_time_sink_x_0_0`) and frequency display "
            "(`qtgui_freq_sink_x_0_0`) must remain connected directly to the original file source's output.\n"
            "5. Insert `signal_squelch` right after the `channel_filter`. The channel filter's output goes into "
            "the squelch block's input. The squelch block's output then feeds the input of the quadrature demodulator "
            "(`analog_quadrature_demod_cf_0`).\n"
            "6. Make sure you remove the direct connection from the file source to the quadrature demodulator, "
            "ensuring the new filter and squelch blocks are inline. Check that the flowgraph is valid and confirm the result."
        ),
        "expect": {
            "blocks_present": ["channel_filter", "signal_squelch"],
            "params": {
                "channel_filter": {
                    "type": "fir_filter_ccf",
                    "samp_rate": "in_rate",
                    "cutoff_freq": "100e3",
                    "width": "20e3",
                },
                "signal_squelch": {
                    "threshold": "-50",
                    "alpha": "0.01",
                },
            },
            "valid": True,
        },
    },
    {
        # Not part of tests/test_integration.py's SELECTED_SCENARIOS multi-backend
        # sweep — it is driven by a single dedicated, ollama_cloud-only test
        # (test_scenario_lexical_fallback_ollama_cloud_only) that first disables
        # the local embedding backend for real (GRC_EMBED_BACKEND=llamacpp with
        # an empty runtime directory + a fresh GRC_AGENT_VECTORS_DIR — see rag.py's
        # _embed_endpoint/query_catalog), so query_knowledge's catalog lookup below is
        # forced into its real SQLite FTS5/BM25 lexical fallback (search_mode == "lexical")
        # instead of vector search. "complex conjugate" is deliberately literal/exact
        # wording — it is the block's own label text — so BM25 keyword matching
        # reliably surfaces `blocks_conjugate_cc` even with no embeddings at
        # all, proving the agent can still complete a real graph edit end to
        # end using only lexically retrieved catalog info.
        "name": "23_lexical_conjugate_insert",
        "fixture": "tests/data/resampler_demo.grc",
        "prompt": (
            "Inspect the flowgraph. Search the catalog for the block that"
            " computes the complex conjugate of a complex signal using"
            " query_knowledge (catalog domain) — don't guess the block id."
            " Add it, call it `signal_conjugate`, and insert it right after"
            " the resampler (`pfb_arb_resampler_xxx_0`) and before the"
            " resampled spectrum display (`qtgui_freq_sink_x_0_0`), so the"
            " resampler's output goes through the conjugate block before"
            " reaching that display. Remove the direct connection from the"
            " resampler to that display. Make sure the flowgraph is valid"
            " and inspect it to confirm."
        ),
        "expect": {
            "blocks_present": ["signal_conjugate"],
            "valid": True,
        },
    },
    {
        # In SELECTED_SCENARIOS (generic mode="read" check: some read tool was
        # used, answer non-empty) AND covered by a dedicated test,
        # test_scenario_generate_python_writes_nothing_to_disk, which asserts
        # generate_python specifically was called, that it returned real
        # generated source, and — the tool's actual load-bearing promise —
        # that the fixture's temp directory holds exactly the same files
        # after the live agent turn as before it.
        "name": "24_generate_python_preview",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Show me the Python code GNU Radio would actually generate for"
            " this flowgraph — use the generate_python tool for the real"
            " generated source, don't write or guess the code yourself."
            " Briefly summarize what it does. Don't change the graph."
        ),
        "expect": {"mode": "read"},
    },
    {
        "name": "25_save_epy_block_to_library",
        "fixture": "tests/data/empty.grc",
        "prompt": (
            "Inspect the flowgraph — right now it's empty except for the"
            " samp_rate variable. Write a small Embedded Python Block from"
            " scratch, call it `scale_by_three`: a gr.sync_block subclass"
            " named `blk` that takes one float input stream, multiplies"
            " every sample by a constant `scale` parameter (default 3.0),"
            " and produces one float output stream. Add a signal source"
            " called `sig` (type float, freq 1000, amp 1.0, using"
            " samp_rate) and a null sink called `null_sink` (type float)."
            " Wire the source into your new epy block, and the epy block"
            " into the sink. Inspect to confirm the chain is valid. Then"
            " use the save_block tool to export `scale_by_three` into the"
            " reusable block library under the block_id"
            " `agent_test_scale_multiplier` — if a block with that id"
            " already exists from a previous run, pass overwrite=True to"
            " replace it."
        ),
        "expect": {
            "blocks_present": ["scale_by_three", "sig", "null_sink"],
            "valid": True,
            "tools_called": ["save_block"],
        },
    },
]


def fresh_agent(fixture):
    tmp_dir = tempfile.mkdtemp()
    tmp = Path(tmp_dir) / Path(fixture).name
    shutil.copy2(fixture, tmp)
    fg = load_flow_graph(str(tmp))
    return fg, tmp, tmp_dir



# Tool names that satisfy a mode == "read" scenario's expectation. Also the
# default input to the generic tool-usage check below — one uniform helper
# backs both, rather than a hand-picked heuristic special-cased per scenario.
_READ_TOOLS = ("query_knowledge", "inspect_graph", "generate_python")


def _any_tool_called(run_result, tool_names) -> bool:
    """True if any ToolCallPart in the run's real message history used one
    of the given tool names. Backs both the mode == "read" check and any
    scenario's explicit `tools_called` expectation — a single reusable
    tool-usage check instead of a one-off hardcoded for a specific scenario."""
    if not run_result:
        return False
    from pydantic_ai.messages import ToolCallPart

    for msg in run_result.all_messages():
        if hasattr(msg, "parts") and any(
            isinstance(p, ToolCallPart) and p.tool_name in tool_names for p in msg.parts
        ):
            return True
    return False


def check_expect(fixture_path, expect, run_result=None):  # noqa: C901
    fg = load_flow_graph(str(fixture_path))
    snap = inspect_graph(fg)["graph"]
    valid = snap["validation"]["status"] == "valid"
    names = {b["instance_name"] for b in snap["blocks"]}
    params = {b["instance_name"]: b["params"] for b in snap["blocks"]}
    states = {b["instance_name"]: b["state"] for b in snap["blocks"]}

    fail_reasons = []
    mode = expect.get("mode", "edit")

    if mode == "read":
        if not _any_tool_called(run_result, _READ_TOOLS):
            fail_reasons.append("no read tool used")
        if not run_result or not run_result.output:
            fail_reasons.append("empty answer")
    else:
        for blk in expect.get("blocks_present") or []:
            if isinstance(blk, (list, tuple)):
                if not any(alt in names for alt in blk):
                    fail_reasons.append(f"missing block (one of {blk})")
            else:
                if blk not in names:
                    fail_reasons.append(f"missing block {blk}")
        for blk in expect.get("blocks_absent") or []:
            if blk in names:
                fail_reasons.append(f"block {blk} still present")
        if "valid" in expect and valid != bool(expect["valid"]):
            fail_reasons.append(f"graph valid={valid} expected {expect['valid']}")
        for inst, st in (expect.get("states") or {}).items():
            if str(states.get(inst, "")) != str(st):
                fail_reasons.append(f"state {inst}={states.get(inst)!r} expected {st!r}")
        for inst, pv in (expect.get("params") or {}).items():
            actual = params.get(inst, {})
            for k, v in pv.items():
                actual_val = str(actual.get(k, "")).replace(" ", "")
                expected_val = str(v).replace(" ", "")
                if actual_val == expected_val:
                    continue
                try:
                    numeric_match = float(actual_val) == float(expected_val)
                except ValueError:
                    numeric_match = False
                if not numeric_match:
                    fail_reasons.append(f"param {inst}.{k}={actual.get(k)!r} expected {v!r}")

    # Applies regardless of mode: any scenario can require one or more
    # specific named tools to have actually been called during the run (e.g.
    # save_block, which doesn't mutate the flowgraph itself so nothing above
    # would otherwise notice whether it ran) — one uniform check reusable by
    # any scenario, not a hand-picked one-off for a single case.
    for tool_name in expect.get("tools_called") or []:
        if not _any_tool_called(run_result, (tool_name,)):
            fail_reasons.append(f"tool {tool_name!r} was never called")

    return {"pass": not fail_reasons, "reasons": fail_reasons, "valid": valid}


def render_scenario_markdown(sc, grc_before, run_result, verdict) -> str:  # noqa: C901
    events = []
    from pydantic_ai import ModelResponse
    from pydantic_ai.messages import ToolCallPart

    messages = run_result.all_messages() if run_result else []
    tool_calls = {}

    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tool_calls[part.tool_call_id] = {
                        "name": part.tool_name,
                        "args": part.args,
                        "result": None,
                    }

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_call_id in tool_calls:
                    tool_calls[part.tool_call_id]["result"] = part.content

    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    t_info = tool_calls.get(part.tool_call_id)
                    if t_info:
                        events.append(
                            {
                                "event": "model_message",
                                "role": "tool_model",
                                "tool_called": {"name": t_info["name"], "args": t_info["args"]},
                                "payload": {
                                    "content": [
                                        {
                                            "tool_call_result": str(t_info["result"])
                                            if t_info["result"] is not None
                                            else ""
                                        }
                                    ]
                                },
                            }
                        )

    final_res = {
        "ok": verdict["pass"],
        "assistant_text": run_result.output if run_result else "",
        "expect_reason": "; ".join(verdict["reasons"]) if verdict["reasons"] else "ok",
    }
    events.append({"event": "final", "result": final_res})

    title = sc["name"].replace("_", " ").title()
    rec = {
        "title": title,
        "name": sc["name"],
        "fixture_name": Path(sc["fixture"]).name,
        "system_prompt": build_system_prompt("pai-experiment"),
        "prompt": sc["prompt"],
        "grc_before": grc_before,
        "events": events,
    }

    parts = [
        f"# {rec['title']}",
        "",
        f"**Scenario:** `{rec['name']}` | **Fixture:** `{rec['fixture_name']}` | **Model:** `{MODEL}`",
        "",
        "## System Prompt",
        "",
        "```text",
        rec["system_prompt"],
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
        rec["grc_before"],
        "```",
        "",
        "## Tool calls (raw inputs + outputs the model saw)",
        "",
    ]

    for idx, ev in enumerate(events):
        if ev.get("event") == "model_message" and ev.get("role") == "tool_model":
            tc = ev.get("tool_called") or {}
            tool_name = tc.get("name")
            parts.append(f"### call {idx + 1} — `{tool_name}`")
            parts.append("")
            parts.append("**args (model sent):**")
            parts.append("")
            parts.append("```json")
            parts.append(json.dumps(tc.get("args", {}), indent=2, default=str))
            parts.append("```")
            parts.append("")

            payload = ev.get("payload", {}) or {}
            content = payload.get("content") or []
            entry = content[0] if content else {}
            result_text = entry.get("tool_call_result", "")

            parts.append("**result (model saw this exact string):**")
            parts.append("")
            parts.append("```json")
            parts.append(result_text)
            parts.append("```")
            parts.append("")

    parts.append("## Final result (raw)")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(final_res, indent=2, default=str))
    parts.append("```")
    parts.append("")

    return "\n".join(parts)
