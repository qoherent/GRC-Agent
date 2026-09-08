"""Recording-completeness auditor (plan U4, KTD8) — the bulletproof verifier.

Three modes, one rule each:

- ``--validate-real-db`` — read-only validation of the auditor itself against
  the known real session DB (archive runs without run_started, interrupted
  snapshots, run_failed events). Opens the DB through a SQLite ``mode=ro``
  immutable URI; NEVER through app helpers that trigger schema-init writes
  (review fix F: the byte-identity gate must survive its own check).
- ``--selftest`` — synthetic sessions, each violating exactly one invariant;
  every one must be caught.
- ``--campaign <dir>`` — audit a collected campaign: structural invariants per
  conversation plus manifest cross-checks (plan U4 step 2). A session that
  fails cannot be exported (U5 refuses unaudited or failed sessions).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from .invariants import (
    check_instructions_recorded,
    check_media_markers,
    check_placeholders,
    check_request_response_shape,
    check_tool_call_matching,
    deserialize,
    runs_shape,
    tool_effects_cover,
)
from .manifest import Manifest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REAL_DB = REPO_ROOT / ".grc_agent" / "chat_sessions.db"


def read_only_connection(db_path: Path) -> sqlite3.Connection:
    """Immutable read-only connection — no schema writes, ever."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_conversations(db_path: Path) -> dict[str, dict]:
    """Everything the auditor needs from one DB, read-only, keyed by
    conversation id."""
    conn = read_only_connection(db_path)
    try:
        sessions = {
            f"session-{r['id']}": r["messages"]
            for r in conn.execute("SELECT id, messages FROM sessions")
        }
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conversations: dict[str, dict] = {}

        def _ensure(conv_id: str) -> dict:
            return conversations.setdefault(
                conv_id, {"runs": [], "events": [], "tool_effects": [], "snapshots": [], "session": sessions.get(conv_id)}
            )

        run_to_conv: dict[str, str] = {}
        if "runs" in tables:
            for r in conn.execute("SELECT * FROM runs"):
                run = dict(r)
                run_to_conv[run["run_id"]] = run["conversation_id"]
                _ensure(run["conversation_id"])["runs"].append(run)
        orphans = {"snapshots": [], "events": [], "tool_effects": []}
        if "snapshots" in tables:
            for r in conn.execute("SELECT run_id, step_index, state, messages FROM snapshots"):
                snap = dict(r)
                conv = run_to_conv.get(snap["run_id"])
                if conv is None:
                    orphans["snapshots"].append(snap["run_id"])
                    continue
                _ensure(conv)["snapshots"].append(snap)
        if "events" in tables:
            for r in conn.execute("SELECT * FROM events"):
                e = dict(r)
                _ensure(e.get("conversation_id") or run_to_conv.get(e["run_id"], "orphaned"))["events"].append(e)
        if "tool_effects" in tables:
            for r in conn.execute("SELECT * FROM tool_effects"):
                te = dict(r)
                _ensure(run_to_conv.get(te["run_id"], "orphaned"))["tool_effects"].append(te)
        # Sessions with no step-store rows are still audited.
        for conv_id in sessions:
            _ensure(conv_id)
        # Orphaned step-store rows are evidence of a tear: fail closed.
        for kind, ids in orphans.items():
            if ids:
                _ensure(f"orphaned-{kind}")["orphans"] = sorted(set(ids))
        return conversations
    finally:
        conn.close()


def audit_conversation(
    conv_id: str, data: dict, *, require_session: bool = False, loss_as_warning: bool = False
) -> list[str]:
    """Structural invariants for one conversation (plan U4 step 1).

    Call-answer matching joins the archive snapshots (KTD5 union): compaction
    removes returns from the canonical blob and their answers live there.
    """
    violations: list[str] = []
    warnings: list[str] = []
    session_blob = data.get("session")
    if session_blob is None:
        return [f"{conv_id}: manifest-referenced session row missing"] if require_session else []
    try:
        messages = deserialize(session_blob)
    except Exception as e:  # noqa: BLE001
        return [f"{conv_id}: session blob does not deserialize: {e}"]
    archive_messages: list[list] = []
    for snap in data.get("snapshots", []):
        try:
            archive_messages.append(deserialize(snap["messages"]))
        except Exception:  # noqa: BLE001 - a corrupt snapshot is itself a finding
            violations.append(f"{conv_id}: snapshot at step {snap.get('step_index')} does not deserialize")
    for v in check_tool_call_matching(messages, archive_messages):
        call_id = v.split("tool call ", 1)[-1].split(" ", 1)[0]
        if loss_as_warning:
            warnings.append(f"{conv_id}: {v} (pre-fix session (R9 known shape))")
        elif _call_in_turn_failure(data, call_id):
            warnings.append(f"{conv_id}: {v} (crash-attributed: call present in the turn_failure archive)")
        else:
            violations.append(f"{conv_id}: {v}")
    for check in (
        check_instructions_recorded,
        check_request_response_shape,
        check_media_markers,
    ):
        violations += [f"{conv_id}: {v}" for v in check(messages)]
    placeholders = check_placeholders(messages)
    if placeholders:
        archives = [r for r in data["runs"] if "pre_compaction_transcript" in r["run_id"]]
        if not archives:
            violations.append(f"{conv_id}: {placeholders[0]} with no compaction archive")
    orphaned = data.get("orphans")
    if orphaned:
        violations.append(f"{conv_id}: {len(orphaned)} orphaned step-store rows reference no run (possible torn write)")
    for v in runs_shape(data["runs"], data["events"]):
        # A crashed run (no terminal event / unfinished request) is a real
        # shape the verdicts already gate (agent_error -> quarantine, plan I2);
        # it is reported as a warning, not a gating violation.
        if "no terminal event" in v or "started=" in v:
            warnings.append(f"{conv_id}: {v}")
        else:
            violations.append(f"{conv_id}: {v}")
    violations += [f"{conv_id}: {v}" for v in tool_effects_cover(data["tool_effects"], data["events"])]
    return violations + [f"WARNING {w}" for w in warnings]


def _call_in_turn_failure(data: dict, call_id: str) -> bool:
    """True when the exact lost call appears in a turn_failure archive
    snapshot — the documented approval-crash loss shape, attributed per call
    instead of per conversation."""
    for snap in data.get("snapshots", []):
        if "turn_failure" not in snap["run_id"]:
            continue
        if call_id in (snap["messages"] or ""):
            return True
    return False


def _table_counts(db_path: Path) -> dict[str, int]:
    conn = read_only_connection(db_path)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("sessions", "runs", "events", "snapshots", "tool_effects")
        }
    finally:
        conn.close()


def validate_real_db() -> int:
    """R9: prove the auditor handles reality before it gates a campaign."""
    if not REAL_DB.exists():
        print(f"real DB not found at {REAL_DB}")
        return 1
    conversations = load_conversations(REAL_DB)
    known = {
        "sessions": 5, "runs": 125, "events": 250, "snapshots": 160, "tool_effects": 43,
    }
    counts = _table_counts(REAL_DB)
    print(f"real DB counts: {counts}")
    drift = [f"{t}={counts[t]} (R9 baseline {known[t]})" for t in counts if counts[t] != known[t]]
    if drift:
        print(f"NOTE: count drift vs baseline (DB changed since grounding): {drift}")

    all_violations: list[str] = []
    archive_count = 0
    for conv_id, data in sorted(conversations.items()):
        archive_count += sum(1 for r in data["runs"] if "pre_compaction_transcript" in r["run_id"] or "turn_failure" in r["run_id"] or "handoff" in r["run_id"])
        all_violations += audit_conversation(conv_id, data, loss_as_warning=True)

    print(f"conversations: {len(conversations)}; archive/turn-failure runs exercised: {archive_count}")
    warnings = [v for v in all_violations if v.startswith('WARNING ')]
    blocking = [v for v in all_violations if not v.startswith('WARNING ')]
    if warnings:
        print(f"shape warnings (non-gating, verdicts already cover): {len(warnings)}")
    if blocking:
        print(f"REAL-DB VALIDATION FAILED ({len(blocking)} violations):")
        for v in blocking:
            print(f"  - {v}")
        return 1
    print("real-DB validation OK: auditor handles the known real shapes")
    return 0


def _expect_caught(failures: list[str], name: str, bad_messages: list, check, desc: str) -> None:
    if not check(bad_messages):
        failures.append(f"selftest {name}: {desc} was NOT caught")


def selftest() -> int:  # noqa: C901 - one scenario per violation, linear
    """Synthetic sessions; every synthetic invariant violation must be caught."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    def _instr() -> str:
        return "Session ID: x\nRole: test"

    def _call(id_: str) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="inspect_graph", args="{}", tool_call_id=id_)]
        )

    def _ret(id_: str) -> ModelResponse:
        return ModelResponse(
            parts=[ToolReturnPart(tool_name="inspect_graph", content="ok", tool_call_id=id_)]
        )

    clean = [
        ModelRequest(parts=[UserPromptPart(content="hi")], instructions="Session ID: x\nRole: test"),
        ModelResponse(parts=[TextPart(content="hello")]),
        ModelRequest(parts=[UserPromptPart(content="more")], instructions="Session ID: x\nRole: test"),
        _call("call-1"),
        _ret("call-1"),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    failures: list[str] = []

    # 1. Unmatched mid-history tool call caught; trailing boundary tolerated.
    _expect_caught(
        failures, "unmatched call",
        [clean[0], _call("c-a"), _call("c-b"), clean[5]],
        check_tool_call_matching, "unmatched mid-history call",
    )
    if check_tool_call_matching(clean):
        failures.append("selftest: clean session flagged for tool-call matching")
    # 2. Missing instructions caught; clean passes.
    _expect_caught(
        failures, "missing instructions",
        [ModelRequest(parts=[UserPromptPart(content="hi")]), ModelResponse(parts=[TextPart(content="x")])],
        check_instructions_recorded, "empty instructions",
    )
    if check_instructions_recorded(clean):
        failures.append("selftest: clean session flagged for instructions")
    # 3. Placeholder caught; clean passes.
    _expect_caught(
        failures, "placeholder",
        [ModelRequest(parts=[UserPromptPart(content="hi")], instructions="s"),
         ModelResponse(parts=[TextPart(content="[Flowgraph tool output cleared to conserve context — recall]")])],
        check_placeholders, "placeholder text",
    )
    if check_placeholders(clean):
        failures.append("selftest: clean session flagged for placeholder")
    # 4. Response-less session caught.
    _expect_caught(
        failures, "no response",
        [ModelRequest(parts=[UserPromptPart(content="hi")], instructions="s")],
        check_request_response_shape, "no model response",
    )
    if check_request_response_shape(clean):
        failures.append("selftest: clean session flagged for shape")
    # 5. Media marker without a reference URI caught.
    from .invariants import check_media_markers as _cmm

    _expect_caught(
        failures, "media marker",
        [ModelResponse(parts=[TextPart(content=json.dumps({"__harness_external_media__": True, "media_type": "image/png"}))])],
        _cmm, "marker without reference",
    )

    # 6. Row-level invariants against a real harness-created temp store.
    from pydantic_ai_harness.step_persistence import (
        RunRecord,
        SqliteStepStore,
    )

    async def _row_checks() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SqliteStepStore(database=str(Path(tmp) / "t.db"))
            await store.register_run(
                RunRecord(run_id="agent-nostart", conversation_id="session-9", agent_name="a")
            )
            from dataclasses import asdict

            rows = [asdict(r) for r in await store.list_runs(conversation_id="session-9")]
            violations = runs_shape(rows, [])
            if not any("no run_started" in v for v in violations):
                failures.append("selftest rows: missing run_started NOT caught")

    try:
        asyncio.run(_row_checks())
    except Exception as e:  # noqa: BLE001
        failures.append(f"selftest rows crashed: {e}")

    if failures:
        print("SELFTEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("selftest OK: every synthetic invariant violation was caught")
    return 0


def audit_campaign(campaign_dir: Path) -> int:
    """Campaign mode: structural audit + manifest cross-check (U4 step 2)."""
    manifest = Manifest(campaign_dir)
    db_path = campaign_dir / ".grc_agent" / "chat_sessions.db"
    if not db_path.exists():
        print(f"no campaign DB at {db_path}")
        return 1
    try:
        meta = manifest.load_meta()
        tasks = manifest.load_tasks()
        turns = manifest.load_turns()
    except Exception as e:  # noqa: BLE001
        print(f"manifest unreadable: {e}")
        return 1

    conversations = load_conversations(db_path)
    all_violations: list[str] = []
    for conv_id, data in sorted(conversations.items()):
        require = any(t.session_id and f"session-{t.session_id}" == conv_id for t in tasks)
        all_violations += audit_conversation(conv_id, data, require_session=require)

    # Manifest cross-checks (U4 step 2): per-task turn accounting and verdicts.
    for rec in tasks:
        conv_id = f"session-{rec.session_id}" if rec.session_id else None
        if not rec.verdict:
            all_violations.append(f"{rec.task_id}: no verdict recorded")
            continue
        if conv_id and conv_id in conversations:
            blob = conversations[conv_id]["session"]
            messages = deserialize(blob)
            from pydantic_ai.messages import ModelRequest, UserPromptPart

            user_prompts = [
                p.content
                for m in messages
                if isinstance(m, ModelRequest)
                for p in m.parts
                if isinstance(p, UserPromptPart) and isinstance(p.content, str)
                and not p.content.startswith(("Flowgraph run failed",))
            ]
            sim_turns = [t for t in turns if t.task_id == rec.task_id and t.provenance == "simulator"]
            autofix = [t for t in turns if t.task_id == rec.task_id and t.provenance == "autofix"]
            if len(user_prompts) != len(sim_turns):
                all_violations.append(
                    f"{rec.task_id}: {len(user_prompts)} non-autofix user prompts in history vs "
                    f"{len(sim_turns)} simulator turn records ({len(autofix)} autofix recorded)"
                )
        if rec.containment_ok is False:
            all_violations.append(f"{rec.task_id}: containment check failed")
        if meta.tool_freeze_ok is False:
            all_violations.append("campaign: tool schema freeze fell back to names-only")

    warnings = [v for v in all_violations if "WARNING " in v]
    blocking = [v for v in all_violations if v not in warnings]
    if warnings:
        print(f"shape warnings (non-gating; verdicts already quarantine): {len(warnings)}")
    if blocking:
        print(f"CAMPAIGN AUDIT FAILED ({len(blocking)} violations):")
        for v in blocking:
            print(f"  - {v}")
        return 1
    verdicts = {}
    for rec in tasks:
        verdicts[rec.verdict] = verdicts.get(rec.verdict, 0) + 1
    print(f"campaign audit OK: {len(tasks)} tasks, verdicts={verdicts}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-real-db", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--campaign", help="campaign directory to audit")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.validate_real_db:
        return validate_real_db()
    if args.campaign:
        return audit_campaign(Path(args.campaign))
    parser.print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
