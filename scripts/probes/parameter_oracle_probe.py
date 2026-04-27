"""Safe scalar edit oracle — test-only.

Suggests nearby safe parameter values using catalog metadata,
then applies them to a graph copy and validates with grcc.
"""

from __future__ import annotations

import copy
import random
import subprocess
import sys
import tempfile
from typing import Any
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import yaml

from grc_agent.catalog.describe import describe_block
from grc_agent.flowgraph_session import FlowgraphSession


def _select_candidate_params(session: FlowgraphSession, max_per_graph: int = 5) -> list[dict]:
    """Pick candidate scalar params for probing."""
    candidates: list[dict] = []
    if not session.flowgraph:
        return candidates
    for block in session.flowgraph.blocks:
        params = block.params.get("parameters")
        if not isinstance(params, dict):
            continue
        desc = describe_block(block.block_type)
        if not desc.get("ok"):
            continue
        rules = {p["id"]: p for p in desc.get("parameters", [])}
        for param_id, current_value in list(params.items())[:3]:
            rule = rules.get(param_id)
            if not rule:
                continue
            dtype = str(rule.get("dtype", "")).lower()
            if dtype == "raw":
                continue
            options = rule.get("options")
            default = rule.get("default")
            candidates.append({
                "block": block.instance_name,
                "param_id": param_id,
                "dtype": dtype,
                "current": current_value,
                "options": options,
                "default": default,
                "block_type": block.block_type,
            })
            if len(candidates) >= max_per_graph:
                return candidates
    return candidates


def _suggest_value(candidate: dict) -> str | None:
    """Derive a nearby safe value or None."""
    dtype = candidate.get("dtype", "")
    options = candidate.get("options")
    default = candidate.get("default")

    if options:
        # pick a different option than current
        current = str(candidate["current"])
        others = [o for o in options if str(o) != current]
        return str(random.choice(others)) if others else None

    if dtype in ("int", "int_vector"):
        current = _try_int(candidate["current"])
        if current is not None:
            return str(current + 1)
        if default is not None:
            return str(default)

    if dtype == "float":
        current = _try_float(candidate["current"])
        if current is not None:
            return str(current * 2.0)
        if default is not None:
            return str(default)

    if dtype == "bool":
        current = str(candidate["current"]).lower()
        return "True" if current == "false" else "False"

    if dtype == "enum":
        # handled by options above
        return None

    return None


def _try_int(value: Any) -> int | None:
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _try_float(value: Any) -> float | None:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _apply_and_validate(
    session: FlowgraphSession,
    *,
    block_name: str,
    param_id: str,
    new_value: str,
) -> tuple[bool, str]:
    """Apply one param update to a graph copy and run grcc.

    Returns (grcc_ok, notes).
    """
    raw = copy.deepcopy(session.flowgraph.raw_data) if session.flowgraph else {}
    for b in raw.get("blocks", []):
        if b.get("name") == block_name:
            p = b.get("parameters")
            if isinstance(p, dict):
                p[param_id] = new_value
            break

    with tempfile.NamedTemporaryFile("w", suffix=".grc", delete=False) as fp:
        yaml.dump(raw, fp, default_flow_style=False)
        tmp_path = fp.name

    try:
        result = subprocess.run(
            ["grcc", "-o", "/tmp", tmp_path],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0, (
            "stderr truncated" if len(result.stderr) > 200 else result.stderr.decode("utf-8", errors="replace").strip()
        )
    except Exception as exc:
        return False, str(exc)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


GRAPHS = [
    "/usr/share/gnuradio/examples/audio/dial_tone.grc",
    "/usr/share/gnuradio/examples/blocks/selector.grc",
    "/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc",
    "/usr/share/gnuradio/examples/filter/resampler_demo.grc",
    "/usr/share/gnuradio/examples/fec/polar_code_example.grc",
]


def main() -> None:
    print("| Graph | Block | Param | DType | Current | Suggested | Basis | grcc OK | Notes |")
    print("|---|---|---|---|---|---|---|---|---|")

    for path in GRAPHS:
        session = FlowgraphSession()
        try:
            session.load(path)
        except Exception:
            continue

        candidates = _select_candidate_params(session, max_per_graph=4)
        for c in candidates:
            sugg = _suggest_value(c)
            if sugg is None:
                continue
            ok, notes = _apply_and_validate(
                session,
                block_name=c["block"],
                param_id=c["param_id"],
                new_value=sugg,
            )
            basis = "options" if c["options"] else "default" if c["default"] is not None else "heuristic"
            print(
                f"| {Path(path).name} | {c['block']} | {c['param_id']} | "
                f"{c['dtype']} | {c['current']} | {sugg} | {basis} | "
                f"{'yes' if ok else 'no'} | {notes[:60].replace('|','')} |"
            )


if __name__ == "__main__":
    main()
