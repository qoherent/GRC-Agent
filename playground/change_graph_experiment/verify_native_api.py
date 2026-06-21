"""Phase 2 — change_graph native API experiment.

Proves the native ``gnuradio.grc.core`` FlowGraph API supports the 5 canonical
change_graph mutations cleanly (or documents the workaround), across every
fixture in tests/data/. No agent / no src changes — pure native platform use.

Mirrors playground/inspect_experiment/verify_native_api.py.
"""
import json
import time
from pathlib import Path

import yaml

from grc_agent.session import _ensure_platform

WORKSPACE = Path(__file__).resolve().parents[2]
DATA_DIR = WORKSPACE / "tests" / "data"
RESULTS = Path(__file__).resolve().parent / "results_native"
RESULTS.mkdir(parents=True, exist_ok=True)


def _load(path: Path):
    fg = _ensure_platform().make_flow_graph()
    fg.import_data(yaml.safe_load(path.read_text(encoding="utf-8-sig")))
    fg.rewrite()
    return fg


def _state(fg) -> dict:
    return {
        "blocks": len(fg.blocks),
        "connections": len(fg.connections),
        "valid": fg.is_valid(),
    }


def _named(fg):
    return {b.name: b for b in fg.blocks}


# --- the 5 canonical mutations (each returns (applied, note)) -----------------

def mut_add_block(fg) -> tuple[bool, str]:
    nb = fg.new_block("analog_sig_source_x")
    if nb is None:
        return False, "new_block returned None"
    nb.params["id"].set_value("experiment_sig_source")
    nb.params["freq"].set_value("12345")
    return True, f"added analog_sig_source_x as '{nb.name}' (id-driven naming)"


def mut_remove_block(fg) -> tuple[bool, str]:
    byname = _named(fg)
    victim = None
    for b in fg.blocks:
        if b.key in ("options",) or getattr(b, "is_variable", False):
            continue
        victim = b
        break
    if victim is None:
        return False, "no removable block in fixture"
    incoming = [c for c in fg.connections if c.sink_block is victim or c.source_block is victim]
    for c in list(incoming):
        fg.disconnect(c.source_port, c.sink_port)
    fg.blocks.remove(victim)
    return True, f"removed '{victim.name}' + {len(incoming)} connection(s) (list.remove; no native API)"


def mut_update_param(fg) -> tuple[bool, str]:
    byname = _named(fg)
    var = next((b for b in fg.blocks if getattr(b, "is_variable", False)), None)
    if var is None:
        return False, "no variable block to update"
    old = var.params["value"].value
    var.params["value"].set_value("48000")
    return True, f"'{var.name}' value {old!r} -> {var.params['value'].value!r}"


def mut_update_state(fg) -> tuple[bool, str]:
    target = next((b for b in fg.blocks if b.key != "options"), None)
    if target is None:
        return False, "no block to disable"
    target.states["state"] = "disabled"
    return True, f"'{target.name}' state -> disabled (enabled now {target.enabled})"


def mut_rewire(fg) -> tuple[bool, str]:
    conn = next(iter(fg.connections), None)
    if conn is None:
        return False, "no connection to rewire"
    sp, dp = conn.source_port, conn.sink_port
    fg.disconnect(sp, dp)
    fg.connect(sp, dp)
    return True, f"disconnect+reconnect {conn.source_block.name}:0 -> {conn.sink_block.name}:0"


MUTATIONS = [
    ("add_block", mut_add_block),
    ("remove_block", mut_remove_block),
    ("update_param", mut_update_param),
    ("update_state", mut_update_state),
    ("rewire", mut_rewire),
]


def run():
    fixtures = sorted(DATA_DIR.glob("*.grc"))
    print(f"Found {len(fixtures)} fixtures.")
    consolidated = {}
    for fx in fixtures:
        consolidated[fx.name] = {}
        for name, fn in MUTATIONS:
            fg = _load(fx)  # fresh load per mutation so they don't compound
            pre = _state(fg)
            t0 = time.perf_counter()
            try:
                applied, note = fn(fg)
                fg.rewrite()
                fg.validate()
                err = list(fg.get_error_messages())
                ok = applied and fg.is_valid()
            except Exception as exc:
                applied, note, ok, err = False, f"EXCEPTION: {exc!r}", False, [str(exc)]
            latency_ms = (time.perf_counter() - t0) * 1000
            post = _state(fg)
            record = {
                "applied": applied,
                "valid_after": ok,
                "pre": pre,
                "post": post,
                "errors": err,
                "latency_ms": round(latency_ms, 2),
                "note": note,
            }
            consolidated[fx.name][name] = record
            flag = "OK" if ok else ("SKIP" if not applied else "INVALID")
            print(f"  [{flag:7s}] {fx.name:48s} {name:14s} {latency_ms:6.1f} ms  {note}")
    out = RESULTS / "consolidated_native.json"
    out.write_text(json.dumps(consolidated, indent=2), encoding="utf-8")
    print(f"\nWrote {out.relative_to(WORKSPACE)}")


if __name__ == "__main__":
    run()
