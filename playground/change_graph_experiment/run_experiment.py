"""Phase 2 — change_graph LEGACY experiment (for the native-vs-legacy diff).

Runs the same logical mutations through the production (legacy) change_graph
flat-batch surface to capture: (a) per-mutation latency, (b) accept/reject.
Compared against playground/change_graph_experiment/results_native/ in
analysis.md.

No src changes — this only *calls* the existing tool.
"""
import json
import time
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

WORKSPACE = Path(__file__).resolve().parents[2]
DATA_DIR = WORKSPACE / "tests" / "data"
RESULTS = Path(__file__).resolve().parent / "results_legacy"
RESULTS.mkdir(parents=True, exist_ok=True)


def _agent_for(fixture_path: Path) -> GrcAgent:
    session = FlowgraphSession()
    session.load(str(fixture_path))
    return GrcAgent(session)


def _targets(agent: GrcAgent) -> dict:
    blocks = agent.session.flowgraph.blocks
    names = [b.instance_name for b in blocks]
    first_var = next((b.instance_name for b in blocks
                      if b.block_type.startswith("variable")), None)
    first_removable = next((b.instance_name for b in blocks
                            if b.block_type != "options"
                            and not b.block_type.startswith("variable")), None)
    return {"names": names, "first_var": first_var, "first_removable": first_removable}


def _run(agent: GrcAgent, payload: dict) -> dict:
    t0 = time.perf_counter()
    try:
        result = agent.execute_tool("change_graph", payload)
        latency_ms = (time.perf_counter() - t0) * 1000
        errors = result.get("errors", []) if isinstance(result, dict) else []
        ok = bool(result.get("ok", False)) if isinstance(result, dict) else False
        committed = bool(result.get("committed", False)) if isinstance(result, dict) else False
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        ok, committed, errors = False, False, [{"exception": repr(exc)}]
    codes = [e.get("code", str(e)) if isinstance(e, dict) else str(e) for e in errors]
    return {"ok": ok, "committed": committed, "error_codes": codes,
            "latency_ms": round(latency_ms, 2)}


def _mutations(agent: GrcAgent) -> dict:
    t = _targets(agent)
    out = {}
    out["add_block"] = {"add_blocks": [{
        "block_id": "analog_sig_source_x",
        "instance_name": "experiment_sig_source",
        "params": {"freq": "12345"},
    }]}
    if t["first_removable"]:
        out["remove_block"] = {"remove_blocks": [t["first_removable"]]}
    if t["first_var"]:
        out["update_param"] = {"update_params": [{
            "instance_name": t["first_var"], "params": {"value": "48000"},
        }]}
    if t["first_removable"]:
        out["update_state"] = {"update_states": [{
            "instance_name": t["first_removable"], "state": "disabled",
        }]}
    return out


def run():
    fixtures = sorted(DATA_DIR.glob("*.grc"))
    print(f"Found {len(fixtures)} fixtures.")
    consolidated = {}
    for fx in fixtures:
        consolidated[fx.name] = {}
        for name, payload in _mutations(_agent_for(fx)).items():
            agent = _agent_for(fx)  # fresh agent per mutation
            rec = _run(agent, payload)
            rec["payload"] = payload
            consolidated[fx.name][name] = rec
            print(f"  [{'OK' if rec['ok'] else 'REJECT':6s}] {fx.name:48s} {name:14s} "
                  f"{rec['latency_ms']:8.1f} ms  {rec['error_codes']}")
    out = RESULTS / "consolidated_legacy.json"
    out.write_text(json.dumps(consolidated, indent=2), encoding="utf-8")
    print(f"\nWrote {out.relative_to(WORKSPACE)}")


if __name__ == "__main__":
    run()
