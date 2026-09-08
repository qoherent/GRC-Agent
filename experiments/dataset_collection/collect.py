"""Campaign collection driver (plan U3) — boots the real app once under xvfb
and runs every task through the runner contract.

Usage (under xvfb-run -a, after the Ollama Cloud key is exported):

    OLLAMA_API_KEY=... uv run python -m experiments.dataset_collection.collect \
        --campaign-dir /tmp/campaign1

Hermetic mode (no LLM spend) uses the scripted stub simulator:

    uv run python -m experiments.dataset_collection.collect --simulator scripted \\
        --campaign-dir /tmp/campaign_dry [--tasks t01_dial_tone]

Campaign isolation (plan KTD3): the campaign env file is empty and dedicated;
API keys must arrive via the process environment (never a copy of the real
`.env`); the campaign DB starts empty; the denylist is pinned (refuse empty).
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .manifest import CampaignMeta, Manifest, TaskRecord, TurnRecord
from .runner_contract import (
    RunnerContractError,
    await_turn,
    drain,
    last_assistant_text,
    send_or_fail,
    teardown_task,
    tree_hash,
)
from .task_spec import TaskSpec, load_personas, load_tasks

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REAL_DB = REPO_ROOT / ".grc_agent" / "chat_sessions.db"
PLAYGROUND = REPO_ROOT / "playground"

_AUTOFIX_PREFIXES = ("Flowgraph run failed",)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _session_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()


async def freeze_tools(agent) -> tuple[list[dict], bool]:
    """Snapshot the executor's model-visible tool definitions at boot (KTD4/I6).

    Returns (tools, ok). On any failure the manifest records the fallback
    honestly (ok=False) and the auditor flags it — never a silent stub.
    """
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    try:
        ctx = RunContext(deps=None, model=agent.model, usage=RunUsage())
        tools = await agent._get_toolset().get_tools(ctx)
        frozen = []
        for name, tool in sorted(tools.items()):
            tool_def = getattr(tool, "tool_def", None)
            frozen.append(
                {
                    "name": str(getattr(tool_def, "name", name) or name),
                    "description": str(getattr(tool_def, "description", "") or ""),
                    "parameters": getattr(tool_def, "parameters_json_schema", None),
                }
            )
        return frozen, True
    except Exception:  # noqa: BLE001 - recorded, not swallowed
        return [], False


def _evaluate_acceptance(task: TaskSpec, canvas, proxy, start_hash: str) -> tuple[bool | None, str]:
    """Machine-checkable completion predicate (KTD7). None = not evaluable."""
    acc = task.acceptance
    try:
        if acc.kind in ("graph_valid", "graph_unchanged_valid"):
            fg = canvas.current_flow_graph
            if fg is None:
                return False, "no flowgraph open"
            fg.validate()
            if not fg.is_valid():
                errors = "; ".join(str(e) for e in list(fg.iter_error_messages())[:3])
                return False, f"invalid graph: {errors}"
            if acc.kind == "graph_unchanged_valid":
                from grc_agent.adapter.graph import flow_graph_content_hash

                if flow_graph_content_hash(fg) != start_hash:
                    return False, "knowledge task graph was modified"
            return True, "graph valid"
        if acc.kind in ("run_succeeded", "log_contains"):
            log = proxy.get_run_log() or {}
            if acc.kind == "run_succeeded":
                return bool(log.get("ran_successfully")), f"rc={log.get('return_code')}"
            pattern = acc.pattern or ""
            text = str(log.get("log_text") or "")
            return bool(re.search(pattern, text)), f"pattern={pattern!r} matched={bool(re.search(pattern, text))}"
        if acc.kind == "file_exists":
            target = FIXTURES_DIR / task.id / (acc.path or "")
            return target.is_file(), f"{target}"
        if acc.kind == "hier_block_saved":
            from grc_agent.adapter.block_library import hier_block_lib_dir

            hits = [
                str(p)
                for p in hier_block_lib_dir().glob("*")
                if fnmatch.fnmatch(p.name.lower(), f"*{acc.pattern.lower()}*")
            ]
            return bool(hits), f"hier hits: {hits[:3]}"
    except Exception as e:  # noqa: BLE001 - evaluation failure is a result
        return False, f"acceptance evaluation error: {e}"
    return None, f"unknown kind {acc.kind}"


def _count_autofix_prompts(history) -> int:
    """History user prompts injected by notify_run_failure (known prefixes)."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    count = 0
    for msg in history:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if (
                    isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                    and part.content.startswith(_AUTOFIX_PREFIXES)
                ):
                    count += 1
    return count


async def run_task(  # noqa: C901 - one task, one linear contract
    *,
    task: TaskSpec,
    window,
    canvas,
    sidebar,
    proxy,
    manifest: Manifest,
    simulator_factory,
    db_path: Path,
    playground_baseline: str,
    real_db_baseline: str | None,
    tasks_completed: int,
    pre_teardown: Callable[[object], None] | None = None,
) -> TaskRecord:
    personas = load_personas()
    record = TaskRecord(
        task_id=task.id,
        persona_id=task.persona_id,
        category=task.category,
        goal=task.goal,
        generate_options=task.generate_options,
        seed_grc=task.seed_grc,
        acceptance_kind=task.acceptance.kind,
    )
    fixture = FIXTURES_DIR / task.id
    starter = sorted(fixture.glob("*.grc"))[0]
    expected_path = str(starter.resolve())

    # --- setup: open the task's fixture as a saved page, never untitled ---
    window.new_page(str(starter), show=True)
    await asyncio.sleep(0.5)  # let the notebook switch fire _sync_sidebar
    page = canvas.current_page
    actual = getattr(page, "file_path", None)
    if actual is None or str(Path(actual).resolve()) != expected_path:
        raise RunnerContractError(
            f"{task.id}: opened page file_path {actual!r} does not match fixture {expected_path}"
        )
    sidebar.set_project_directory(fixture)
    monitor = proxy._exec_monitor
    await drain(sidebar, monitor)

    from grc_agent.adapter.graph import flow_graph_content_hash

    start_hash = flow_graph_content_hash(canvas.current_flow_graph)

    simulator = simulator_factory(task, personas[task.persona_id])
    from .simulator import RepetitionDetector, ScriptExhausted, SimulatorAborted

    repetition = RepetitionDetector()
    reply = ""
    turn_idx = 0
    unproductive = 0
    sent_any = False
    verdict = "budget_exhausted"
    stop_reason = "turn budget exhausted"
    autofix_seen = 0

    try:
        while turn_idx < task.max_simulator_turns:
            sim_turn = await simulator.next_turn(reply)
            repetition_flagged = repetition.observe(sim_turn.message)

            await drain(sidebar, monitor)
            await send_or_fail(sidebar, sim_turn.message)
            sent_any = True
            rec = TurnRecord(
                task_id=task.id,
                turn_index=turn_idx,
                provenance="simulator",
                message=sim_turn.message,
                session_id=sidebar._active_session_id,
                simulator_raw=sim_turn.raw,
                repetition_flagged=repetition_flagged,
            )
            await await_turn(sidebar)
            await drain(sidebar, monitor)
            reply = last_assistant_text(sidebar._message_history)
            rec.reply = reply
            rec.is_tracking_at_end = monitor.is_tracking
            rec.session_id = sidebar._active_session_id
            rec.stop = sim_turn.stop
            rec.reason = sim_turn.reason
            manifest.record_turn(rec)

            autofix_now = _count_autofix_prompts(sidebar._message_history)
            for _ in range(autofix_seen, autofix_now):
                manifest.record_turn(
                    TurnRecord(
                        task_id=task.id,
                        turn_index=-1,
                        provenance="autofix",
                        message="<auto-fix synthetic turn>",
                        session_id=sidebar._active_session_id,
                    )
                )
            autofix_seen = autofix_now

            if not reply.strip():
                unproductive += 1
            else:
                unproductive = 0
            if unproductive >= task.give_up_after_unproductive:
                verdict = "agent_gave_up"
                stop_reason = f"{unproductive} unproductive turns in a row"
                break
            if sim_turn.stop:
                verdict = "stopped_by_simulator"
                stop_reason = sim_turn.reason or "simulator stop"
                break
            turn_idx += 1
    except ScriptExhausted:
        verdict = "budget_exhausted"
        stop_reason = "script exhausted (budget path)"
    except SimulatorAborted as e:
        verdict = "simulator_aborted"
        stop_reason = str(e)
    except RunnerContractError as e:
        verdict = "infrastructure_abort"
        stop_reason = str(e)
    except Exception as e:  # noqa: BLE001 - agent turn failure (plan I2)
        verdict = "agent_error"
        stop_reason = f"{type(e).__name__}: {e}"

    record.session_id = sidebar._active_session_id
    record.simulator_turns = turn_idx + 1
    record.autofix_turns = autofix_seen

    # --- acceptance predicate gates `completed` (KTD7) ---
    if verdict == "stopped_by_simulator":
        passed, detail = _evaluate_acceptance(task, canvas, proxy, start_hash)
        record.acceptance_passed = passed
        record.acceptance_detail = detail
        verdict = "completed" if passed else "failed_acceptance"
    record.verdict = verdict
    record.stop_reason = stop_reason

    # --- dry-run fault injection hook (U6) + post-hook autofix rescan ---
    if pre_teardown is not None:
        try:
            pre_teardown(sidebar)
            await drain(sidebar, monitor)
            autofix_now = _count_autofix_prompts(sidebar._message_history)
            for _ in range(autofix_seen, autofix_now):
                manifest.record_turn(
                    TurnRecord(
                        task_id=task.id,
                        turn_index=-1,
                        provenance="autofix",
                        message="<auto-fix synthetic turn>",
                        session_id=sidebar._active_session_id,
                    )
                )
            autofix_seen = autofix_now
        except Exception as e:  # noqa: BLE001 - an injected fault is a task error
            verdict = "agent_error"
            stop_reason = f"pre_teardown fault: {type(e).__name__}: {e}"
            record.verdict = verdict
            record.stop_reason = stop_reason

    # --- teardown: fixed order, containment checks after settle (U3 step 4) ---
    async def _stop_run() -> None:
        monitor = proxy._exec_monitor
        if monitor.is_tracking:
            await proxy.stop_flowgraph()
            for _ in range(50):
                if not monitor.is_tracking:
                    break
                await asyncio.sleep(0.2)

    def _checks() -> None:
        if sidebar._active_session_id is not None:
            raise RunnerContractError("session id not cleared at teardown")
        if sidebar._message_history:
            raise RunnerContractError("history not empty at teardown")
        expected_sessions = tasks_completed + (1 if sent_any else 0)
        actual_sessions = _session_count(db_path)
        if actual_sessions != expected_sessions:
            raise RunnerContractError(
                f"session count {actual_sessions} != tasks-completed {expected_sessions}"
            )

    containment_ok = True
    containment_notes: list[str] = []
    try:
        await teardown_task(sidebar, proxy, stop_run=_stop_run, checks=[_checks])
        if tree_hash(PLAYGROUND) != playground_baseline:
            containment_ok = False
            containment_notes.append("playground tree changed")
        if REAL_DB.exists():
            from .runner_contract import file_hash

            if file_hash(REAL_DB) != real_db_baseline:
                containment_ok = False
                containment_notes.append("real user DB changed")
    except RunnerContractError as e:
        containment_ok = False
        containment_notes.append(str(e))

    record.containment_ok = containment_ok
    if containment_notes:
        record.acceptance_detail += f" | containment: {'; '.join(containment_notes)}"
    record.ended_at = time.time()
    manifest.record_task(record)
    return record




async def campaign(args: argparse.Namespace, simulator_factory=None) -> int:  # noqa: C901 - linear contract
    campaign_dir = Path(args.campaign_dir).resolve()
    campaign_dir.mkdir(parents=True, exist_ok=True)
    env_file = campaign_dir / ".env"
    env_file.touch()

    # Isolation guards BEFORE any app import reads settings (KTD3, S4, S1).
    os.environ["GRC_AGENT_ENV"] = str(env_file)
    os.environ["GRC_AGENT_APPROVE_CHANGES"] = "yolo"
    deny = os.environ.get("GRC_SHELL_DENIED_COMMANDS", "")
    if deny.strip() == "" and "GRC_SHELL_DENIED_COMMANDS" in os.environ:
        # Explicitly-empty denylist disables filtering — refuse (plan C4).
        print("REFUSING: GRC_SHELL_DENIED_COMMANDS is set but empty (filtering off).")
        return 2
    os.environ.setdefault("GRC_SHELL_TIMEOUT", "120")

    # Imports happen after env pinning so every settings read is campaign-scoped.
    from grc_agent.desktop_app import build_app
    from grc_agent.settings import load_settings

    db_path = campaign_dir / ".grc_agent" / "chat_sessions.db"
    if _session_count(db_path) != 0:
        print(f"REFUSING: campaign DB {db_path} is not empty (KTD3). Use a fresh --campaign-dir.")
        return 2

    tasks = load_tasks()
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t.id in wanted]
    personas = load_personas()
    missing = [t.id for t in tasks if t.persona_id not in personas]
    if missing:
        print(f"REFUSING: tasks with unknown personas: {missing}")
        return 2

    window, canvas, sidebar, proxy = build_app()
    monitor = proxy._exec_monitor
    await drain(sidebar, monitor)

    cfg = load_settings()
    if args.agent == "testmodel":
        # Hermetic dry-run agent (plan U2 Execution note / U6): the same
        # TestModel + production-StepPersistence pattern the sidebar tests
        # use — no LLM spend, real persistence path, real instructions.
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel
        from pydantic_ai_harness.step_persistence import StepPersistence

        from grc_agent.db import get_step_store
        from grc_agent.prompts import build_system_prompt

        sidebar._agent = Agent(
            TestModel(),
            instructions=build_system_prompt("pai-desktop-chat"),
            capabilities=[StepPersistence(store=get_step_store(), agent_name="grc_executor")],
            output_type=str,
            name="grc_desktop_executor_agent",
        )
        # _select_executor() (page sync, clear_messages) resets _agent from
        # _executor_agent — swap both so the hermetic agent survives teardown.
        sidebar._executor_agent = sidebar._agent
    agent = sidebar._agent
    if sidebar._agent_mode != "executor":
        print("REFUSING: campaign must run in executor mode")
        return 2
    frozen_tools, freeze_ok = await freeze_tools(agent)

    manifest = Manifest(campaign_dir)
    if simulator_factory is None:
        from .simulator import OllamaUserSimulator, ScriptedSimulator, SimulatorTurn

        if args.simulator == "scripted":

            def simulator_factory(task: TaskSpec, persona):  # noqa: ARG001 - uniform shape
                turns = [
                    SimulatorTurn(f"please help me with: {task.goal[:60]}", False, "opening"),
                    SimulatorTurn("nice, keep going", False, "push"),
                    SimulatorTurn("looks good, that's all I needed", True, "satisfied"),
                ]
                return ScriptedSimulator(turns=turns)

        else:

            def simulator_factory(task: TaskSpec, persona):
                return OllamaUserSimulator(persona=persona, task=task)

    real_db_baseline = None
    if REAL_DB.exists():
        from .runner_contract import file_hash

        real_db_baseline = file_hash(REAL_DB)
    playground_baseline = tree_hash(PLAYGROUND)

    meta = CampaignMeta(
        campaign_id=manifest.campaign_id,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        teacher={"provider": cfg.get("provider", ""), "model": cfg.get("model", "")},
        approval_mode=os.environ["GRC_AGENT_APPROVE_CHANGES"],
        shell_denied_commands_sha=_sha(os.environ.get("GRC_SHELL_DENIED_COMMANDS", "<harness-default>")),
        shell_timeout=float(os.environ["GRC_SHELL_TIMEOUT"]),
        tool_freeze_ok=freeze_ok,
        task_ids=[t.id for t in tasks],
    )
    manifest.write_meta(meta)
    (campaign_dir / "frozen_tools.json").write_text(json.dumps(frozen_tools, indent=1), encoding="utf-8")
    print(f"campaign {manifest.campaign_id}: {len(tasks)} tasks, teacher={meta.teacher}, agent={args.agent}")

    completed = 0
    results: list[TaskRecord] = []
    from grc_agent.db import get_step_store  # noqa: F401 - dry-run F3 hook uses it

    for task in tasks:
        print(f"[{task.id}] running ...", flush=True)
        try:
            rec = await run_task(
                task=task,
                window=window,
                canvas=canvas,
                sidebar=sidebar,
                proxy=proxy,
                manifest=manifest,
                simulator_factory=simulator_factory,
                db_path=db_path,
                playground_baseline=playground_baseline,
                real_db_baseline=real_db_baseline,
                tasks_completed=completed,
            )
        except RunnerContractError as e:
            rec = TaskRecord(
                task_id=task.id,
                persona_id=task.persona_id,
                category=task.category,
                goal=task.goal,
                generate_options=task.generate_options,
                seed_grc=task.seed_grc,
                acceptance_kind=task.acceptance.kind,
                verdict="infrastructure_abort",
                stop_reason=str(e),
            )
            manifest.record_task(rec)
        results.append(rec)
        if rec.verdict in ("completed", "failed_acceptance", "agent_gave_up", "budget_exhausted", "simulator_aborted", "agent_error"):
            completed += 1
        print(f"[{task.id}] verdict={rec.verdict} ({rec.stop_reason})", flush=True)

    print("\n=== campaign summary ===")
    for rec in results:
        print(f"  {rec.task_id:<28} {rec.verdict}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True)
    parser.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    parser.add_argument("--simulator", choices=("live", "scripted"), default="live")
    parser.add_argument(
        "--agent",
        choices=("settings", "testmodel"),
        default="settings",
        help="testmodel = hermetic TestModel agent (dry runs, no LLM spend)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(campaign(args))


if __name__ == "__main__":
    sys.exit(main())
