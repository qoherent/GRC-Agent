import os
import sys
import shutil
import tempfile
import json
import dataclasses
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext, Tool, ModelSettings, ModelMessage, ModelRequest
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.capabilities import ProcessHistory

# Local imports
from grc_adapter import (
    load_flow_graph,
    inspect_graph,
    change_graph,
    query_catalog,
    query_docs,
    web_search,
    web_fetch,
)

MODEL = "qwen3.6:35b-a3b-q4_K_M"
OLLAMA_V1 = "http://localhost:11434/v1"

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
]

INSPECT_GRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "view": {
            "type": "string",
            "enum": ["overview"],
            "description": "The view mode. Defaults to 'overview'."
        },
        "targets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional block instance_names to inspect. Empty/omitted (or ['all']) returns the whole-graph overview; a non-empty list scopes the result to those blocks plus connections touching them."
        }
    },
    "additionalProperties": False
}

QUERY_KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Block capability, block-id, or concept question."
        },
        "domain": {
            "type": "string",
            "enum": ["catalog", "docs"],
            "description": "'catalog' for block types/params; 'docs' for concepts."
        }
    },
    "required": ["query", "domain"],
    "additionalProperties": False
}

WEB_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The web search query."
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of results to return (1-10, default 5).",
            "minimum": 1,
            "maximum": 10
        }
    },
    "required": ["query"],
    "additionalProperties": False
}

WEB_FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "The URL of the web page to fetch."
        }
    },
    "required": ["url"],
    "additionalProperties": False
}

CHANGE_GRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "add_blocks": {
            "type": "array",
            "description": "Add blocks with optional initial params/states using installed catalog block_ids.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": "Installed GNU Radio catalog block ID (e.g. 'analog_sig_source_x')."
                    },
                    "instance_name": {
                        "type": "string",
                        "description": "New unique graph instance name (e.g. 'my_source')."
                    },
                    "params": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Initial parameter values keyed by parameter ID."
                    },
                    "state": {
                        "type": "string",
                        "enum": ["enabled", "disabled", "bypass"],
                        "description": "Initial block state; defaults to 'enabled'."
                    }
                },
                "required": ["block_id", "instance_name"]
            }
        },
        "remove_blocks": {
            "type": "array",
            "description": "Remove existing blocks from the graph by instance name.",
            "items": {"type": "string"}
        },
        "update_params": {
            "type": "array",
            "description": "Update parameters on existing blocks keyed by parameter ID.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "description": "Target block instance name (e.g. 'my_source')."
                    },
                    "params": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Param updates keyed by parameter ID."
                    }
                },
                "required": ["instance_name", "params"]
            }
        },
        "update_states": {
            "type": "array",
            "description": "Modify target block enablement state.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "description": "Target block instance name (e.g. 'my_source')."
                    },
                    "state": {
                        "type": "string",
                        "enum": ["enabled", "disabled", "bypass"],
                        "description": "New block state."
                    }
                },
                "required": ["instance_name", "state"]
            }
        },
        "add_connections": {
            "type": "array",
            "description": "Connection strings to add.",
            "items": {"type": "string"}
        },
        "remove_connections": {
            "type": "array",
            "description": "Connection strings to remove.",
            "items": {"type": "string"}
        },
        "force": {
            "type": "boolean",
            "description": "When true, edits are committed even if validation fails. Default false."
        }
    },
    "additionalProperties": False
}

def fresh_agent(fixture):
    tmp_dir = tempfile.mkdtemp()
    tmp = Path(tmp_dir) / Path(fixture).name
    shutil.copy2(fixture, tmp)
    fg = load_flow_graph(str(tmp))
    return fg, tmp, tmp_dir

def grc_tools() -> list[Tool[Any]]:
    tools = []
    
    async def inspect_graph_func(ctx: RunContext[Any], view: str = "overview", targets: list[str] = None) -> str:
        return json.dumps(inspect_graph(ctx.deps, targets=targets))
    tools.append(Tool.from_schema(
        function=inspect_graph_func,
        name="inspect_graph",
        description="Read-only inspection of the active graph. Returns topology, block instances, connections, parameter values, and validation status.",
        json_schema=INSPECT_GRAPH_SCHEMA,
        takes_ctx=True
    ))
    
    async def query_knowledge_func(ctx: RunContext[Any], query: str, domain: str) -> str:
        if domain == "catalog":
            return json.dumps(query_catalog(query))
        else:
            return json.dumps(query_docs(query))
    tools.append(Tool.from_schema(
        function=query_knowledge_func,
        name="query_knowledge",
        description="Answer GNU Radio knowledge questions from two domains: catalog (block IDs, port names, parameter keys) or docs (concepts).",
        json_schema=QUERY_KNOWLEDGE_SCHEMA,
        takes_ctx=True
    ))
    
    async def web_search_func(ctx: RunContext[Any], query: str, max_results: int = 5) -> str:
        return json.dumps(web_search(query, max_results=max_results))
    tools.append(Tool.from_schema(
        function=web_search_func,
        name="web_search",
        description="Search the live web. Returns up to 10 result snippets.",
        json_schema=WEB_SEARCH_SCHEMA,
        takes_ctx=True
    ))
    
    async def web_fetch_func(ctx: RunContext[Any], url: str) -> str:
        return json.dumps(web_fetch(url))
    tools.append(Tool.from_schema(
        function=web_fetch_func,
        name="web_fetch",
        description="Fetch a single web page by URL.",
        json_schema=WEB_FETCH_SCHEMA,
        takes_ctx=True
    ))
    
    async def change_graph_func(ctx: RunContext[Any], 
                                add_blocks: list[dict] = None, 
                                remove_blocks: list[str] = None, 
                                update_params: list[dict] = None, 
                                update_states: list[dict] = None, 
                                add_connections: list[str] = None, 
                                remove_connections: list[str] = None,
                                force: bool = False) -> str:
        return json.dumps(change_graph(
            ctx.deps,
            add_blocks=add_blocks,
            remove_blocks=remove_blocks,
            update_params=update_params,
            update_states=update_states,
            add_connections=add_connections,
            remove_connections=remove_connections,
            force=force
        ))
    tools.append(Tool.from_schema(
        function=change_graph_func,
        name="change_graph",
        description="Apply a batch of structural graph edits. Can add/remove blocks, update parameters/states, and add/remove connections in a single transaction.",
        json_schema=CHANGE_GRAPH_SCHEMA,
        takes_ctx=True
    ))
    
    return tools

def build_system_prompt(session_id: str | None = None) -> str:
    prefix = f"Session ID: {session_id}\n" if session_id else ""
    return prefix + (
        "Role: GNU Radio graph editing assistant.\n"
        "inspect_graph: read topology, blocks, connections, field values, and validation status. "
        "Pass a targets list of block instance names to scope it to those blocks instead of the whole graph.\n"
        "query_knowledge: search catalog blocks or GNU Radio documentation.\n"
        "web_search: search the live web. web_fetch: fetch a specific page by URL.\n"
        "change_graph: add/remove blocks, edit field values, add/remove connections.\n"
        "Parameter values are string expressions; a variable reference is simply the variable's name (e.g. use 'base_freq * 1.5', NOT 'vars.base_freq * 1.5').\n"
        "Set a type-controlling parameter (e.g. 'type', 'itype', 'otype') to the literal value 'auto' "
        "to resolve it from a connected neighbor's dtype instead of guessing a value.\n"
        "Stream-port connections use numeric port keys (e.g. '0', '1', '2'), not names like 'out', 'in(0)', or 'in0'. "
        "GRC error messages like 'in(0)' refer to port index '0'. Message ports are the exception: "
        "they use their exact declared string identifier (e.g. 'pdus', 'msg') instead of a numeric index.\n"
        "Do not attempt to rename blocks by changing the 'id' parameter in update_params; "
        "changing a block's ID is not supported and will be ignored. To rename a block, you must remove it and add a new one.\n"
        'Variables are blocks; use block_id "variable" (not "parameter") to add one.\n'
        "Every GNU Radio fact must be grounded in query_knowledge, not memory.\n"
        "Ensure the final state of the flowgraph is valid: run inspect_graph before finishing "
        "and verify that validation.status is 'valid'.\n"
        "A change_graph call that returns ok=false applied nothing — the batch was rolled back. "
        "Read the errors, adjust the call, and retry; do not resubmit identical arguments.\n"
        "Describing a change_graph call in your reply text does not execute it; only an actual tool call applies changes to the graph.\n"
        "The force=True flag in change_graph commits edits but does not resolve errors; "
        "you must still fix any unconnected ports or blocks to make the graph valid.\n"
        "To change a block's enablement, use the update_states batch field: "
        "{instance_name, state}, where state is enabled, disabled, or bypass.\n"
        "'Port is not connected' means a required port has zero active connections — this includes a "
        "newly added block that was never wired up, not only a block being disabled. "
        "Disabling a block that is part of a connection also fails this same validation; "
        "use state=bypass to take a connected block out of service without breaking the graph, "
        "or force=true to commit the disabled state anyway.\n"
        "When removing blocks, also update_states (disabled/bypass) or remove any source blocks that become unconnected.\n"
        "Never use hallucinated block IDs; if query_knowledge does not return a block ID, it does not exist.\n"
        "When the user asks a question, answer concisely: lead with the direct answer, then add only the context needed to act on it.\n"
        "Do not use LaTeX or TeX math notation in chat replies; write math inline in plain text (e.g. `350 microHz`, `f^2`, `x_i`).\n"
    )

def prune_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    if len(messages) <= 12:
        return messages
    target = len(messages) - 10
    for i in range(target, 0, -1):
        msg = messages[i]
        if isinstance(msg, ModelRequest):
            if any(isinstance(p, UserPromptPart) for p in msg.parts):
                return [messages[0]] + messages[i:]
    return messages

def check_expect(fixture_path, expect, run_result=None):
    fg = load_flow_graph(str(fixture_path))
    snap = inspect_graph(fg)["graph"]
    valid = snap["validation"]["status"] == "valid"
    names = {b["instance_name"] for b in snap["blocks"]}
    params = {b["instance_name"]: b["params"] for b in snap["blocks"]}
    states = {b["instance_name"]: b["state"] for b in snap["blocks"]}
    
    fail_reasons = []
    mode = expect.get("mode", "edit")
    
    if mode == "read":
        has_read_tool = False
        if run_result:
            from pydantic_ai.messages import ToolCallPart
            for msg in run_result.all_messages():
                if hasattr(msg, 'parts'):
                    if any(isinstance(p, ToolCallPart) and p.tool_name in ("query_knowledge", "inspect_graph") for p in msg.parts):
                        has_read_tool = True
        if not has_read_tool:
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
                    
    return {"pass": not fail_reasons, "reasons": fail_reasons, "valid": valid}

def render_scenario_markdown(sc, grc_before, run_result, verdict) -> str:
    events = []
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from pydantic_ai import ModelRequest, ModelResponse
    
    messages = run_result.all_messages()
    tool_calls = {}
    
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tool_calls[part.tool_call_id] = {
                        "name": part.tool_name,
                        "args": part.args,
                        "result": None
                    }
                    
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    if part.tool_call_id in tool_calls:
                        tool_calls[part.tool_call_id]["result"] = part.content
                        
    call_idx = 0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    t_info = tool_calls.get(part.tool_call_id)
                    if t_info:
                        call_idx += 1
                        events.append({
                            "event": "model_message",
                            "role": "tool_model",
                            "tool_called": {
                                "name": t_info["name"],
                                "args": t_info["args"]
                            },
                            "payload": {
                                "content": [
                                    {
                                        "tool_call_result": str(t_info["result"]) if t_info["result"] is not None else ""
                                    }
                                ]
                            }
                        })
                        
    final_res = {
        "ok": verdict["pass"],
        "assistant_text": run_result.output,
        "expect_reason": "; ".join(verdict["reasons"]) if verdict["reasons"] else "ok"
    }
    events.append({
        "event": "final",
        "result": final_res
    })
    
    title = sc["name"].replace("_", " ").title()
    rec = {
        "title": title,
        "name": sc["name"],
        "fixture_name": Path(sc["fixture"]).name,
        "system_prompt": build_system_prompt("pai-experiment"),
        "prompt": sc["prompt"],
        "grc_before": grc_before,
        "events": events
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
            parts.append(f"### call {idx+1} — `{tool_name}`")
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

def main():
    sc_filter = os.environ.get("GRC_AGENT_PAI_SCENARIOS", "01,11")
    indices = [s.strip() for s in sc_filter.split(",")]
    run_scenarios = [s for s in SCENARIOS if any(ind in s["name"] for ind in indices)]
        
    print(f"Starting experiment sweep on {len(run_scenarios)} scenarios using {MODEL} via {OLLAMA_V1}...")
    metrics = []
    
    output_dir = Path("PydanticAI_experiment/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for sc in run_scenarios:
        print(f"\n==================================================")
        print(f"Running scenario: {sc['name']}")
        print(f"==================================================")
        
        grc_before = Path(sc["fixture"]).read_text(encoding="utf-8")
        fg, fixture_path, tmp_dir = fresh_agent(sc["fixture"])
        
        try:
            agent = Agent(
                OllamaModel(MODEL, provider=OllamaProvider(base_url=OLLAMA_V1)),
                deps_type=Any,
                instructions=build_system_prompt("pai-experiment"),
                tools=grc_tools(),
                capabilities=[ProcessHistory(prune_history)],
                model_settings=ModelSettings(extra_body={"think": True})
            )
            
            print("Invoking agent run...")
            res = agent.run_sync(sc["prompt"], deps=fg)
            print("Agent run completed.")
            print(f"Final output:\n{res.output}\n")
            
            verdict = check_expect(fixture_path, sc["expect"], run_result=res)
            print(f"Verdict for {sc['name']}: {verdict}")
            
            md_log = render_scenario_markdown(sc, grc_before, res, verdict)
            log_path = output_dir / f"{sc['name']}.md"
            log_path.write_text(md_log, encoding="utf-8")
            print(f"Saved scenario log to {log_path}")
            
            # Count turns and tool calls
            tool_calls_count = 0
            tool_counts = {}
            from pydantic_ai.messages import ToolCallPart
            for msg in res.all_messages():
                if hasattr(msg, 'parts'):
                    for p in msg.parts:
                        if isinstance(p, ToolCallPart):
                            tool_calls_count += 1
                            tool_counts[p.tool_name] = tool_counts.get(p.tool_name, 0) + 1
                            
            metrics.append({
                "name": sc["name"],
                "pass": verdict["pass"],
                "reasons": verdict["reasons"],
                "turns": len(res.all_messages()),
                "tool_calls": tool_calls_count,
                "tool_counts": tool_counts
            })
            
        except Exception as e:
            print(f"Scenario {sc['name']} failed with error: {e}")
            metrics.append({
                "name": sc["name"],
                "pass": False,
                "reasons": [str(e)],
                "turns": 0,
                "tool_calls": 0,
                "tool_counts": {}
            })
        finally:
            shutil.rmtree(tmp_dir)
            
    # Write METRICS.md
    metrics_path = Path("PydanticAI_experiment/METRICS.md")
    print(f"\nWriting execution metrics to {metrics_path}...")
    
    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write("# PydanticAI Experiment Metrics\n\n")
        f.write("| Scenario | Verdict | Turns | Tool Calls | Breakdowns | Reasons |\n")
        f.write("|----------|---------|-------|------------|------------|---------|\n")
        for m in metrics:
            status_emoji = "✅ PASS" if m["pass"] else "❌ FAIL"
            breakdown_str = ", ".join(f"{k}:{v}" for k, v in m["tool_counts"].items())
            reasons_str = "; ".join(m["reasons"]) if m["reasons"] else "None"
            f.write(f"| {m['name']} | {status_emoji} | {m['turns']} | {m['tool_calls']} | `{breakdown_str}` | {reasons_str} |\n")
            
    print("Done.")

if __name__ == "__main__":
    main()
