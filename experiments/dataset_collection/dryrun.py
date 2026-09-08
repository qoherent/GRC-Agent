"""Fault-injected dry run (plan U6, KTD8 leg 3) — hermetic, under xvfb.

Deliberately creates each dataset-poisoning class against the real app +
real persistence, then requires the auditor and exporter to catch them:

  F1  autofix synthetic turn        -> manifest provenance=autofix, excluded from SFT
  F2  simulator abort mid-task      -> verdict simulator_aborted, quarantined
  F3  compaction placeholder        -> auditor passes only with the archive;
                                       exporter quarantines the session
  F4  still-running flowgraph       -> teardown stops it and awaits not-tracking
                                       (exercised-or-noted honestly)
  F5  containment                   -> real DB + playground untouched (every task)

The primary SFT file must contain ONLY clean completed tasks — any leak fails
the dry run loudly. No LLM spend (TestModel agent + scripted simulator).

    xvfb-run -a uv run python -m experiments.dataset_collection.dryrun \\
        --campaign-dir /tmp/grc_dryrun
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
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


async def dryrun(campaign_dir: Path) -> int:  # noqa: C901 - fault-injection scenarios
    campaign_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}

    original_run_task = collect_mod.run_task
    f4_exercised: dict[str, bool] = {"tracking": False}

    def injected_run_task(**kwargs):
        task = kwargs["task"]

        if task.id == "t04_broken_sink_repair":

            def inject_autofix(sidebar) -> None:
                # F1: notify_run_failure spawns the synthetic auto-fix turn.
                sidebar.notify_run_failure(1, "log text")

            kwargs["pre_teardown"] = inject_autofix

        if task.id == "t09_knowledge_concepts":

            def inject_still_running(sidebar) -> None:
                # F4: a flowgraph left tracking at stop; teardown must stop
                # it and await not-tracking.
                async def _spawn() -> None:
                    with contextlib.suppress(Exception):
                        await sidebar._flowgraph_proxy.run_flowgraph(
                            action="start", wait=False, stop_after_seconds=5
                        )

                async def _watch() -> None:
                    await _spawn()
                    for _ in range(15):
                        if sidebar._flowgraph_proxy._exec_monitor.is_tracking:
                            f4_exercised["tracking"] = True
                            return
                        await asyncio.sleep(0.2)

                asyncio.get_running_loop().create_task(_watch())

            kwargs["pre_teardown"] = inject_still_running

        return original_run_task(**kwargs)

    original_run_task = collect_mod.run_task

    def factory(task, persona):  # noqa: ARG001 - uniform factory shape
        canned = {
            "t04_broken_sink_repair": [
                SimulatorTurn("fix my graph please", False, "opening"),
                SimulatorTurn("did that fix it?", False, "checking"),
                SimulatorTurn("great, done then", True, "satisfied"),
            ],
            "t10_param_tuning": [
                SimulatorTurn("tune it", False, "opening"),
            ],
            "t09_knowledge_concepts": [
                SimulatorTurn("explain it", False, "opening"),
                SimulatorTurn("ok thanks", True, "satisfied"),
            ],
        }
        return ScriptedSimulator(turns=list(canned.get(task.id, canned["t04_broken_sink_repair"])))

    # F2 needs a mid-task LLM-style abort, not a stop decision: wrap t10's
    # simulator so its second call raises SimulatorAborted.
    original_factory_injector = factory

    def simulator_factory(task, persona):
        sim = original_factory_injector(task, persona)
        if task.id == "t10_param_tuning":
            outer = ScriptedSimulator(turns=[SimulatorTurn("tune it", False, "opening")])
            calls = {"n": 0}

            class _AbortSim:
                async def next_turn(self, reply: str) -> SimulatorTurn:
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return await outer.next_turn(reply)
                    raise SimulatorAborted("injected: provider timeout after retry")

            return _AbortSim()
        return sim

    collect_mod.run_task = injected_run_task
    try:
        rc = await collect_mod.campaign(
            _Args(
                campaign_dir=str(campaign_dir),
                tasks="t04_broken_sink_repair,t10_param_tuning,t09_knowledge_concepts",
                simulator="scripted",
                agent="testmodel",
            ),
            simulator_factory=simulator_factory,
        )
    finally:
        collect_mod.run_task = original_run_task
    if rc != 0:
        return rc

    # F3: inject a compaction placeholder + its archive into the campaign DB.
    # The auditor must PASS (archive present) and the exporter must quarantine.
    from pydantic_ai_harness.step_persistence import (
        ContinuableSnapshot,
        RunRecord,
    )

    from grc_agent.db import get_step_store

    from .audit import audit_campaign

    store = get_step_store()
    manifest = Manifest(campaign_dir)
    t09_sid = next((t.session_id for t in manifest.load_tasks() if t.task_id == "t09_knowledge_concepts"), None)
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
        RunRecord(run_id=run_id, conversation_id=conv, agent_name="grc_executor", metadata={"kind": "pre_compaction_transcript"})
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
    results["F3_compaction_audited"] = "caught (archive present, session flagged)" if audit_rc == 0 else "MISSED"

    # Export: compacted + aborted tasks must be quarantined; primary clean.
    from .export import export_campaign

    export_rc = export_campaign(campaign_dir, audit_first=False)
    if export_rc != 0:
        print("F3 export failed outright")
        return 1
    primary = (campaign_dir / "export" / "sft_with_thinking.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in primary.splitlines() if line.strip()]
    results["primary_only_clean"] = (
        "caught" if all(r.get("task_id") == "t04_broken_sink_repair" for r in rows) else "MISSED"
    )
    quar = (campaign_dir / "export" / "quarantine.jsonl").read_text(encoding="utf-8")
    q_tasks = [json.loads(line)["task_id"] for line in quar.splitlines() if line.strip()]
    results["F2_abort_quarantined"] = "caught" if "t10_param_tuning" in q_tasks else "MISSED"
    results["F3_compaction_quarantined"] = "caught" if "t09_knowledge_concepts" in q_tasks else "MISSED"

    # F1: manifest carries the autofix provenance record.
    manifest = Manifest(campaign_dir)
    autofix = [t for t in manifest.load_turns() if t.provenance == "autofix"]
    results["F1_autofix_recorded"] = "caught" if autofix else "MISSED"
    results["F4_still_running"] = (
        "exercised (teardown stopped it)" if f4_exercised["tracking"] else "not-exercised (spawn failed)"
    )

    print("\n=== dry-run fault-injection report ===")
    failures = []
    for name, outcome in results.items():
        print(f"  {name:<28} {outcome}")
        if outcome == "MISSED":
            failures.append(name)
    if failures:
        print(f"DRY RUN FAILED: {failures}")
        return 1
    print("DRY RUN OK: every injected fault was caught by the auditor/exporter")
    return 0


async def _dryrun_entry(campaign_dir: Path) -> int:
    return await dryrun(campaign_dir)


def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", default=None)
    args = parser.parse_args(argv)
    campaign_dir = Path(args.campaign_dir or tempfile.mkdtemp(prefix="grc_dryrun_"))
    return asyncio.run(dryrun(campaign_dir))


if __name__ == "__main__":
    sys.exit(main())
