"""Fault-injected dry run (plan U6, KTD8 leg 3) — hermetic, under xvfb.

Deliberately creates each dataset-poisoning class against the real app +
real persistence, then requires the auditor and exporter to catch them:

  F1  autofix synthetic turn        -> manifest provenance=autofix, excluded
                                       from the exported transcript
  F2  simulator abort mid-task      -> verdict simulator_aborted, quarantined
  F3  compaction placeholder        -> auditor passes only with the archive;
                                       exporter quarantines the session
  F4  still-running flowgraph       -> teardown stops it and awaits
                                       not-tracking; NOT exercising the path
                                       FAILS the dry run (review: silent-pass)
  F5  containment                   -> real DB + playground untouched

The primary SFT file must contain ONLY clean completed tasks and must NOT
contain post-acceptance synthetic turns — any leak fails the dry run loudly.
No LLM spend (TestModel agent + scripted simulator).

    xvfb-run -a uv run python -m experiments.dataset_collection.dryrun \\
        --campaign-dir /tmp/grc_dryrun
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import experiments.dataset_collection.collect as collect_mod

from .manifest import Manifest
from .simulator import ScriptedSimulator, SimulatorAborted, SimulatorTurn


class _Args:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


async def dryrun(campaign_dir: Path) -> int:  # noqa: C901 - fault scenarios
    campaign_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}

    def pre_teardown_for(task):
        if task.id == "t04_broken_sink_repair":

            def inject_autofix(sidebar) -> None:
                # F1: notify_run_failure spawns the synthetic auto-fix turn
                # after the acceptance gate — the exported transcript must
                # strip it (review F7).
                sidebar.notify_run_failure(1, "log text")

            return inject_autofix

        if task.id == "t09_knowledge_concepts":

            async def inject_still_running(sidebar) -> None:
                # F4: leave a flowgraph actually tracking at stop; teardown
                # must stop it and await not-tracking. The hook awaits the
                # spawn and the tracking observation directly (no leaked
                # watcher racing the teardown).
                try:
                    await sidebar._flowgraph_proxy.run_flowgraph(action="start", wait=False)
                except Exception as e:  # noqa: BLE001 - spawn failure is surfaced by the F4 verdict
                    results["F4_spawn_error"] = f"{type(e).__name__}: {e}"
                    return
                for _ in range(20):
                    if sidebar._flowgraph_proxy._exec_monitor.is_tracking:
                        f4_exercised["task"] = task.id
                        return
                    await asyncio.sleep(0.2)

            return inject_still_running

        return None

    f4_exercised = {"task": None}

    def simulator_factory(task, persona):  # noqa: ARG001 - uniform shape
        canned = {
            "t04_broken_sink_repair": [
                SimulatorTurn("fix my graph please", False, "opening"),
                SimulatorTurn("did that fix it?", False, "checking"),
                SimulatorTurn("great, done then", True, "satisfied"),
            ],
            "t10_param_tuning": [SimulatorTurn("tune it", False, "opening")],
            "t09_knowledge_concepts": [
                SimulatorTurn("explain it", False, "opening"),
                SimulatorTurn("ok thanks", True, "satisfied"),
            ],
        }
        sim = ScriptedSimulator(turns=list(canned.get(task.id, canned["t04_broken_sink_repair"])))
        if task.id != "t10_param_tuning":
            return sim
        calls = {"n": 0}

        class _AbortSim:
            async def next_turn(self, reply: str) -> SimulatorTurn:
                calls["n"] += 1
                if calls["n"] == 1:
                    return await sim.next_turn(reply)
                raise SimulatorAborted("injected: provider timeout after retry")

        return _AbortSim()

    rc = await collect_mod.campaign(
        _Args(
            campaign_dir=str(campaign_dir),
            tasks="t04_broken_sink_repair,t10_param_tuning,t09_knowledge_concepts",
            simulator="scripted",
            agent="testmodel",
        ),
        simulator_factory=simulator_factory,
        pre_teardown_for=pre_teardown_for,
    )
    if rc != 0:
        return rc

    # F3: inject a compaction placeholder + its archive into the campaign DB.
    # The auditor must PASS (archive present) and the exporter must quarantine.
    from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord

    from grc_agent.db import get_step_store

    from .audit import audit_campaign

    store = get_step_store()
    manifest = Manifest(campaign_dir)
    t09_sid = next(
        (t.session_id for t in manifest.load_tasks() if t.task_id == "t09_knowledge_concepts"),
        None,
    )
    assert t09_sid, "t09 session id missing from manifest"
    conv = f"session-{t09_sid}"
    run_id = f"grc_executor-pre_compaction_transcript-{time.strftime('%H%M%S')}"
    blob = json.dumps(
        [
            {
                "kind": "request",
                "parts": [{"part_kind": "user-prompt", "content": "q"}],
                "instructions": "Session ID: x",
                "timestamp": None,
            },
            {
                "kind": "response",
                "parts": [
                    {
                        "part_kind": "text",
                        "content": "[Flowgraph tool output cleared to conserve context]",
                    }
                ],
            },
        ]
    )
    await store.register_run(
        RunRecord(
            run_id=run_id,
            conversation_id=conv,
            agent_name="grc_executor",
            metadata={"kind": "pre_compaction_transcript"},
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id=run_id,
            step_index=0,
            messages=json.loads(blob),
            conversation_id=conv,
            agent_name="grc_executor",
        )
    )
    import sqlite3

    conn = sqlite3.connect(str(campaign_dir / ".grc_agent" / "chat_sessions.db"))
    conn.execute(
        "UPDATE sessions SET messages = REPLACE(messages, 'success', "
        "'[Flowgraph tool output cleared to conserve context]') WHERE id = ?",
        (t09_sid,),
    )
    conn.commit()
    conn.close()

    audit_rc = audit_campaign(campaign_dir)
    results["F3_compaction_audited"] = "caught" if audit_rc == 0 else "MISSED"

    # Export: compacted + aborted tasks quarantined; primary clean + frozen.
    from .export import export_campaign

    if export_campaign(campaign_dir) != 0:
        print("F3 export failed outright")
        return 1
    primary = (campaign_dir / "export" / "sft_with_thinking.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in primary.splitlines() if line.strip()]
    results["primary_only_clean"] = (
        "caught" if all(r.get("task_id") == "t04_broken_sink_repair" for r in rows) else "MISSED"
    )
    # F1 export assertion: the synthetic post-acceptance exchange is stripped.
    t04_row = next((r for r in rows if r.get("task_id") == "t04_broken_sink_repair"), None)
    user_msgs = [m["content"] for m in (t04_row or {}).get("messages", []) if m.get("role") == "user"]
    results["F1_autofix_stripped"] = (
        "caught" if t04_row is not None and all("failed" not in c.lower() for c in user_msgs) else "MISSED"
    )
    quar = (campaign_dir / "export" / "quarantine.jsonl").read_text(encoding="utf-8")
    q_tasks = [json.loads(line)["task_id"] for line in quar.splitlines() if line.strip()]
    results["F2_abort_quarantined"] = "caught" if "t10_param_tuning" in q_tasks else "MISSED"
    results["F3_compaction_quarantined"] = "caught" if "t09_knowledge_concepts" in q_tasks else "MISSED"

    # F1: manifest carries the autofix provenance record.
    autofix = [t for t in manifest.load_turns() if t.provenance == "autofix"]
    results["F1_autofix_recorded"] = "caught" if autofix else "MISSED"

    results["F4_still_running"] = (
        "exercised (teardown stopped it)"
        if f4_exercised["task"]
        else f"not-exercised ({results.get('F4_spawn_error', 'no spawn')})"
    )

    print("\n=== dry-run fault-injection report ===")
    failures = []
    for name, outcome in results.items():
        print(f"  {name:<28} {outcome}")
        if outcome == "MISSED":
            failures.append(name)
    if results["F4_still_running"].startswith("not-exercised"):
        # Silent-pass guard: the dry run must NOT pass without exercising F4
        # (review F1/adversarial).
        failures.append("F4_not_exercised")
    if failures:
        print(f"DRY RUN FAILED: {failures}")
        return 1
    print("DRY RUN OK: every injected fault was caught by the auditor/exporter")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", default=None)
    args = parser.parse_args(argv)
    campaign_dir = Path(args.campaign_dir or tempfile.mkdtemp(prefix="grc_dryrun_"))
    return asyncio.run(dryrun(campaign_dir))


if __name__ == "__main__":
    sys.exit(main())
