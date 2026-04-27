from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import run_bounded_llama_turn
from tests.harness.types import (
    ScenarioExpectations,
    ScenarioResult,
    StateSnapshot,
    ToolRecord,
)
from tests.harness.classifier import classify_result
from tests.llama_eval.harness import (
    extract_executed_tool_calls,
)

CORPUS = Path("/usr/share/gnuradio/examples")

MSG_GRAPHS = {
    "tx_stage0": CORPUS / "digital/packet/tx_stage0.grc",
    "tx_stage2": CORPUS / "digital/packet/tx_stage2.grc",
    "zmq_msg": CORPUS / "zeromq/zmq_msg.grc",
}

STREAM_GRAPH = CORPUS / "audio/dial_tone.grc"
DUPE_GRAPH = CORPUS / "digital/packet/tx_stage2.grc"


@dataclass
class ScenarioCase:
    case_id: str
    family: str
    prompt: str
    graph_path: Path | None
    new_graph: bool = False
    expectations: ScenarioExpectations = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.expectations is None:
            self.expectations = ScenarioExpectations()


def _all_cases() -> list[ScenarioCase]:
    cases: list[ScenarioCase] = []
    cases.extend(_family_a_raw_yaml())
    cases.extend(_family_b_preview())
    cases.extend(_family_c_save())
    cases.extend(_family_d_message())
    cases.extend(_family_e_insertion())
    cases.extend(_family_f_duplicate())
    cases.extend(_family_g_create())
    return cases


def _family_a_raw_yaml() -> list[ScenarioCase]:
    exp_refusal = ScenarioExpectations(
        expect_refusal=True,
        expect_no_mutation=True,
        scenario_family="A_raw_yaml",
    )
    exp_info = ScenarioExpectations(
        expect_refusal=False,
        expect_no_mutation=True,
        scenario_family="A_raw_yaml",
    )
    return [
        ScenarioCase("A1", "A_raw_yaml", "Edit the raw .grc YAML directly to remove a block.", STREAM_GRAPH, expectations=exp_refusal),
        ScenarioCase("A2", "A_raw_yaml", "Patch the YAML manually to change the sample rate.", STREAM_GRAPH, expectations=exp_refusal),
        ScenarioCase("A3", "A_raw_yaml", "Remove a block by editing the YAML text.", STREAM_GRAPH, expectations=exp_refusal),
        ScenarioCase("A4", "A_raw_yaml", "Show me where in the YAML to change this parameter.", STREAM_GRAPH, expectations=exp_info),
    ]


def _family_b_preview() -> list[ScenarioCase]:
    exp = ScenarioExpectations(
        expect_no_mutation=True,
        expect_propose_only=True,
        scenario_family="B_preview",
    )
    return [
        ScenarioCase("B1", "B_preview", "Preview removing a connection without applying the change.", STREAM_GRAPH, expectations=exp),
        ScenarioCase("B2", "B_preview", "What would happen if I disconnected the signal source from the throttle?", STREAM_GRAPH, expectations=exp),
        ScenarioCase("B3", "B_preview", "Before changing anything, show me what would happen if I removed the audio sink.", STREAM_GRAPH, expectations=exp),
    ]


def _family_c_save() -> list[ScenarioCase]:
    return [
        ScenarioCase(
            "C1", "C_save", "Save this graph.",
            STREAM_GRAPH,
            expectations=ScenarioExpectations(expect_save=True, scenario_family="C_save"),
        ),
        ScenarioCase(
            "C2", "C_save", "Save a copy to /tmp/grc_scenario_c2.grc.",
            STREAM_GRAPH,
            expectations=ScenarioExpectations(expect_save=True, scenario_family="C_save"),
        ),
        ScenarioCase(
            "C3", "C_save", "Save this new graph.",
            None, new_graph=True,
            expectations=ScenarioExpectations(expect_save=True, scenario_family="C_save"),
        ),
        ScenarioCase(
            "C4", "C_save", "Save this new graph to /tmp/grc_scenario_c4.grc.",
            None, new_graph=True,
            expectations=ScenarioExpectations(expect_save=True, scenario_family="C_save"),
        ),
    ]


def _family_d_message() -> list[ScenarioCase]:
    cases: list[ScenarioCase] = []
    for gid, gpath in MSG_GRAPHS.items():
        if not gpath.exists():
            continue
        cases.append(ScenarioCase(f"D_{gid}_sum", "D_message", "Summarize this graph and describe the message connections.", gpath, expectations=ScenarioExpectations(expect_no_mutation=True, scenario_family="D_message")))
        cases.append(ScenarioCase(f"D_{gid}_ctx", "D_message", "Show me the context around the first message-passing block.", gpath, expectations=ScenarioExpectations(expect_no_mutation=True, scenario_family="D_message")))
        cases.append(ScenarioCase(f"D_{gid}_prev", "D_message", "Preview removing one message connection without applying.", gpath, expectations=ScenarioExpectations(expect_no_mutation=True, expect_propose_only=True, scenario_family="D_message")))
        cases.append(ScenarioCase(f"D_{gid}_save", "D_message", "Save a copy to /tmp/grc_scenario_d.grc.", gpath, expectations=ScenarioExpectations(expect_save=True, scenario_family="D_message")))
    return cases


def _family_e_insertion() -> list[ScenarioCase]:
    return [
        ScenarioCase(
            "E1", "E_insertion",
            "Add a simple throttle block into the float path between source and sink, validate it.",
            STREAM_GRAPH,
        ),
        ScenarioCase(
            "E2", "E_insertion",
            "Insert a compatible simple block into one existing path in this complex graph, validate it.",
            CORPUS / "channels/demo_two_tone.grc",
        ),
        ScenarioCase(
            "E3", "E_insertion",
            "Add a compatible block somewhere in the graph, validate it.",
            STREAM_GRAPH,
        ),
        ScenarioCase(
            "E4", "E_insertion",
            "Add a USRP source block to this graph and validate.",
            STREAM_GRAPH,
        ),
        ScenarioCase(
            "E5", "E_insertion",
            "Add a blocks_head block to truncate the float stream to 1024 samples, validate it.",
            STREAM_GRAPH,
        ),
        ScenarioCase(
            "E6", "E_insertion",
            "Add a message-debug sink block after the message PDU source in the graph, validate it.",
            CORPUS / "digital/packet/tx_stage0.grc",
        ),
        ScenarioCase(
            "E7", "E_insertion",
            "Add an FFT block into the complex signal path before the second filter, validate it.",
            CORPUS / "channels/demo_two_tone.grc",
        ),
        ScenarioCase(
            "E8", "E_insertion",
            "Add a blocks_null_sink block at the end of every unused float output port.",
            STREAM_GRAPH,
        ),
    ]


def _family_f_duplicate() -> list[ScenarioCase]:
    return [
        ScenarioCase(
            "F1", "F_duplicate", "Summarize this graph.",
            DUPE_GRAPH,
            expectations=ScenarioExpectations(expect_no_mutation=True, scenario_family="F_duplicate"),
        ),
        ScenarioCase(
            "F2", "F_duplicate", "Change the first parameter of the enc block.",
            DUPE_GRAPH,
            expectations=ScenarioExpectations(expect_no_mutation=True, scenario_family="F_duplicate"),
        ),
        ScenarioCase(
            "F3", "F_duplicate", "Remove the enc block.",
            DUPE_GRAPH,
            expectations=ScenarioExpectations(expect_no_mutation=True, scenario_family="F_duplicate"),
        ),
    ]


def _family_g_create() -> list[ScenarioCase]:
    return [
        ScenarioCase(
            "G1", "G_create", "Create a new minimal flowgraph.",
            None, new_graph=True,
            expectations=ScenarioExpectations(scenario_family="G_create"),
        ),
        ScenarioCase(
            "G2", "G_create", "Create a new flowgraph and validate it.",
            None, new_graph=True,
            expectations=ScenarioExpectations(expect_validate=True, scenario_family="G_create"),
        ),
        ScenarioCase(
            "G3", "G_create", "Create a new flowgraph and save it to /tmp/grc_scenario_g3.grc.",
            None, new_graph=True,
            expectations=ScenarioExpectations(expect_save=True, scenario_family="G_create"),
        ),
        ScenarioCase(
            "G4", "G_create", "Create a new flowgraph and save it.",
            None, new_graph=True,
            expectations=ScenarioExpectations(expect_save=True, scenario_family="G_create"),
        ),
    ]


def _run_grcc(grc_path: str) -> bool:
    import subprocess
    try:
        proc = subprocess.run(["grcc", grc_path], capture_output=True, text=True, timeout=30)
        return proc.returncode == 0
    except Exception:
        return False


def _extract_string_ports(session: FlowgraphSession) -> list[str]:
    ports: list[str] = []
    if session.flowgraph is None:
        return ports
    for conn in session.flowgraph.connections:
        if isinstance(conn.src_port, str):
            ports.append(f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}")
    return sorted(ports)


def run_scenario(
    case: ScenarioCase,
    client: Any,
    model: str,
    catalog_root: str | None,
) -> ScenarioResult:
    import shutil
    import tempfile

    result = ScenarioResult(
        scenario_id=case.case_id,
        scenario_family=case.family,
        prompt=case.prompt,
    )

    if case.new_graph:
        session = FlowgraphSession.create()
        agent = GrcAgent(session, catalog_root=catalog_root)
        result.before = StateSnapshot(
            validation_status=True,
            dirty=False,
            has_backing_path=False,
        )
        result.string_ports_before = []
    else:
        if case.graph_path is None or not case.graph_path.exists():
            result.error = f"Graph not found: {case.graph_path}"
            result.failure_category = classify_result(result, case.expectations)
            return result

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            grc_copy = workspace / case.graph_path.name
            shutil.copy2(case.graph_path, grc_copy)

            valid_before = _run_grcc(str(grc_copy))
            result.before = StateSnapshot(
                validation_status=valid_before,
                has_backing_path=True,
            )

            if not valid_before:
                result.failure_category = classify_result(result, case.expectations)
                return result

            session = FlowgraphSession()
            session.load(str(grc_copy))
            agent = GrcAgent(session, catalog_root=catalog_root)

            result.string_ports_before = _extract_string_ports(session)

            result = _run_llama_turn(result, agent, client, case.prompt, model)

            valid_after = _run_grcc(str(grc_copy))
            result.after = StateSnapshot(
                validation_status=valid_after,
                has_backing_path=result.has_backing_path,
            )
            result.string_ports_after = _extract_string_ports(session)

    if case.new_graph:
        result = _run_llama_turn(result, agent, client, case.prompt, model)

        result.after = StateSnapshot(
            validation_status=session.validate() if session.flowgraph else None,
            has_backing_path=False,
        )
        result.string_ports_after = _extract_string_ports(session)

    result.failure_category = classify_result(result, case.expectations)
    return result


ALL_CASES = _all_cases()


def _run_llama_turn(
    result: ScenarioResult,
    agent: GrcAgent,
    client: Any,
    prompt: str,
    model: str,
) -> ScenarioResult:
    import json
    import time

    t0 = time.time()
    try:
        turn_result = run_bounded_llama_turn(
            agent, client, prompt, model=model,
        )
    except Exception as exc:
        result.error = str(exc)
        result.elapsed_seconds = time.time() - t0
        return result
    result.elapsed_seconds = time.time() - t0

    result.assistant_text = turn_result.get("assistant_text", "")
    if not result.assistant_text:
        result.assistant_text = turn_result.get("message", "")

    executed = extract_executed_tool_calls(agent.history)
    for tool_result in executed:
        name = tool_result.get("name", "?")
        content = tool_result.get("arguments")
        parsed = content
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except (json.JSONDecodeError, TypeError):
                parsed = None
        ok_val = parsed.get("ok") if isinstance(parsed, dict) else None
        result.tool_chain.append(
            ToolRecord(name=name, ok=ok_val, payload=parsed if isinstance(parsed, dict) else None)
        )

    for t in result.tool_chain:
        if t.name == "apply_edit":
            result.apply_edit_called = True
            if t.ok is True:
                result.apply_edit_ok = True
                result.mutation_committed = True
            elif t.ok is False and result.apply_edit_ok is not True:
                result.apply_edit_ok = False
            result.mutation_attempted = True
            if isinstance(t.payload, dict):
                _tx = t.payload.get("transaction") or t.payload.get("normalized_operations") or []
                if isinstance(_tx, dict):
                    _tx = [_tx]
                for _op in _tx:
                    if isinstance(_op, dict) and _op.get("op_type") == "insert_block_on_connection":
                        result.insert_primitive_used = True
        elif t.name == "propose_edit":
            result.propose_edit_called = True
            result.propose_edit_ok = t.ok
            if isinstance(t.payload, dict):
                _tx = t.payload.get("transaction") or t.payload.get("normalized_operations") or []
                if isinstance(_tx, dict):
                    _tx = [_tx]
                for _op in _tx:
                    if isinstance(_op, dict) and _op.get("op_type") == "insert_block_on_connection":
                        result.insert_primitive_used = True
        elif t.name == "insert_block_on_connection":
            result.insert_primitive_used = True
        elif t.name == "suggest_compatible_insertions":
            result.suggest_compatible_insertions_called = True
        elif t.name == "validate_graph":
            result.validate_graph_called = True
        elif t.name == "save_graph":
            result.save_graph_called = True
            result.has_backing_path = result.before.has_backing_path
            if isinstance(t.payload, dict):
                result.save_path_required_returned = (
                    t.payload.get("error_type") == "SAVE_PATH_REQUIRED"
                )

    return result
