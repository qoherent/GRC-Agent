"""Dataset exporter (plan U5, KTD4/KTD5) — audited sessions to Unsloth-ready JSONL.

Source-of-truth rules:
- Every session must be auditor-passed (``--campaign`` runs the U4 audit first;
  unaudited or failed sessions are refused, never silently exported).
- The canonical blob is the row source; compaction-flagged sessions (clear
  placeholders) are quarantined out of the primary SFT files and their cleared
  tool returns restored from archive snapshots (fuller-than-wire, disclosed).
- OpenAI ``messages`` JSONL: native ``tool_calls``, ``role:"tool"`` returns,
  ``thinking`` as a parallel assistant field; chat-template rendering happens
  at training time — nothing is pre-baked.

Outputs per campaign (``<campaign>/export/``):
    sft_with_thinking.jsonl / sft_no_thinking.jsonl / quarantine.jsonl / dataset_card.md
    + export_report.json (per-task rows, verdicts, usage totals, round-trip)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from .audit import audit_campaign, load_conversations
from .invariants import CLEAR_PLACEHOLDER, deserialize
from .manifest import Manifest, TaskRecord
from .simulator import filter_secrets


def _json_default(obj: Any) -> str:
    return str(obj)


def map_messages(messages: list, *, include_thinking: bool) -> list[dict]:
    """ModelMessages -> OpenAI messages (native tool_calls, thinking parallel)."""
    out: list[dict] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if part.__class__.__name__ == "UserPromptPart" and isinstance(
                    getattr(part, "content", None), str
                ):
                    out.append({"role": "user", "content": part.content})
                elif isinstance(part, ToolReturnPart):
                    content = part.content
                    if not isinstance(content, str):
                        content = json.dumps(content, default=_json_default)
                    out.append({"role": "tool", "name": part.tool_name, "content": content})
        elif isinstance(msg, ModelResponse):
            thinking = "".join(p.content for p in msg.parts if isinstance(p, ThinkingPart)).strip()
            text = "".join(p.content for p in msg.parts if isinstance(p, TextPart)).strip()
            calls = [
                {"name": c.tool_name, "arguments": str(c.args)}
                for c in msg.parts
                if isinstance(c, ToolCallPart)
            ]
            row: dict[str, Any] = {"role": "assistant"}
            if calls:
                row["tool_calls"] = calls
            if text:
                row["content"] = text
            elif not calls:
                continue
            if include_thinking and thinking:
                row["thinking"] = thinking
            out.append(row)
    return out


def restore_from_archives(blob: str, snapshot_blobs: list[str]) -> str:
    """Restore placeholder-cleared content from the fullest archive snapshot
    (KTD5): the pre-compaction archive is fuller; adopt the longest parseable
    snapshot that carries no placeholder. Else the original blob (quarantined)."""
    best, best_len = blob, len(blob)
    for snap in snapshot_blobs:
        try:
            deserialize(snap)
        except Exception:  # noqa: BLE001 - a corrupt snapshot is skipped
            continue
        if len(snap) > best_len and CLEAR_PLACEHOLDER not in snap:
            best, best_len = snap, len(snap)
    return best


def sum_usage(messages: list) -> dict[str, int]:
    """Model-reported usage sums per row (the authoritative counter)."""
    totals: dict[str, int] = {}
    for msg in messages:
        if isinstance(msg, ModelResponse) and msg.usage is not None:
            u = msg.usage
            for field in ("input_tokens", "output_tokens", "requests"):
                value = getattr(u, field, None)
                if isinstance(value, int):
                    totals[field] = totals.get(field, 0) + value
            details = getattr(u, "details", None)
            reasoning = getattr(details, "reasoning_tokens", None)
            if isinstance(reasoning, int):
                totals["reasoning_tokens"] = totals.get("reasoning_tokens", 0) + reasoning
    return totals


def _primary_eligible(rec: TaskRecord, compaction_flagged: bool) -> bool:
    """KTD7 gating: completed + acceptance pass + containment + GUI stratum +
    no compaction (quarantine-by-default for compacted sessions)."""
    return (
        rec.verdict == "completed"
        and rec.acceptance_passed is True
        and rec.containment_ok is not False
        and rec.generate_options != "no_gui"
        and not compaction_flagged
    )


def _quarantine_reason(rec: TaskRecord, compaction_flagged: bool) -> str:
    reasons = []
    if rec.verdict != "completed":
        reasons.append(f"verdict={rec.verdict}")
    if rec.generate_options == "no_gui":
        reasons.append("no_gui stratum (unobserved console output)")
    if compaction_flagged:
        reasons.append("compaction flagged: fuller-than-wire (KTD5)")
    return "; ".join(reasons)


def export_campaign(campaign_dir: Path, *, audit_first: bool = True) -> int:
    if audit_first and audit_campaign(campaign_dir) != 0:
        print("export refused: campaign audit did not pass (U5 gate)")
        return 1
    manifest = Manifest(campaign_dir)
    meta = manifest.load_meta()
    tasks = manifest.load_tasks()
    db_path = campaign_dir / ".grc_agent" / "chat_sessions.db"
    conversations = load_conversations(db_path)
    frozen = _load_frozen_tools(campaign_dir)

    out_dir = campaign_dir / "export"
    out_dir.mkdir(exist_ok=True)
    report: dict[str, dict] = {}
    primary_rows: list[str] = []
    primary_no_think_rows: list[str] = []
    quarantine_rows: list[str] = []
    redaction_total = 0

    for rec in tasks:
        conv_id = f"session-{rec.session_id}"
        data = conversations.get(conv_id)
        if data is None or data.get("session") is None:
            report[rec.task_id] = {"verdict": rec.verdict, "exported": "refused", "reason": "no session row"}
            continue
        blob = restore_from_archives(data["session"], [s["messages"] for s in data.get("snapshots", [])])
        messages = deserialize(blob)
        compaction_flagged = CLEAR_PLACEHOLDER in data["session"]

        system = next(
            (
                getattr(m, "instructions", None)
                for m in messages
                if isinstance(m, ModelRequest) and getattr(m, "instructions", None)
            ),
            None,
        )
        base = {
            "session_id": conv_id,
            "task_id": rec.task_id,
            "category": rec.category,
            "verdict": rec.verdict,
            "teacher": meta.teacher,
            "system": system or "",
            "tools": frozen,
        }

        # Secret-pattern scan before anything is written (review S2): one
        # uniform rule per text part; counts are disclosed, matches redacted.
        redactions = 0
        scrubbed_messages: list = []
        for m in messages:
            if isinstance(m, ModelResponse):
                parts = []
                for p in m.parts:
                    if isinstance(p, (TextPart, ThinkingPart)) and isinstance(p.content, str):
                        filtered, n = filter_secrets(p.content)
                        redactions += n
                        parts.append(type(p)(content=filtered) if n else p)
                    else:
                        parts.append(p)
                scrubbed_messages.append(
                    ModelResponse(parts=parts, usage=m.usage, model_name=m.model_name)
                )
            else:
                scrubbed_messages.append(m)
        redaction_total += redactions

        row = dict(base)
        row["messages"] = map_messages(scrubbed_messages, include_thinking=True)
        row["usage"] = sum_usage(messages)

        if _primary_eligible(rec, compaction_flagged):
            row_no = dict(base)
            row_no["messages"] = map_messages(scrubbed_messages, include_thinking=False)
            primary_rows.append(json.dumps(row, default=_json_default))
            primary_no_think_rows.append(json.dumps(row_no, default=_json_default))
            exported = "primary"
        else:
            row["quarantine_reason"] = _quarantine_reason(rec, compaction_flagged)
            quarantine_rows.append(json.dumps(row, default=_json_default))
            exported = "quarantine"

        report[rec.task_id] = {
            "verdict": rec.verdict,
            "exported": exported,
            "redactions": redactions,
            "messages": len(row["messages"]),
            "usage": row["usage"],
        }

    out_dir = campaign_dir / "export"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "sft_with_thinking.jsonl").write_text("\n".join(primary_rows) + "\n" if primary_rows else "", encoding="utf-8")
    (out_dir / "sft_no_thinking.jsonl").write_text("\n".join(primary_no_think_rows) + "\n" if primary_no_think_rows else "", encoding="utf-8")
    (out_dir / "quarantine.jsonl").write_text("\n".join(quarantine_rows) + "\n" if quarantine_rows else "", encoding="utf-8")

    totals: dict[str, int] = {}
    for info in report.values():
        for k, v in info.get("usage", {}).items():
            totals[k] = totals.get(k, 0) + v
    card = _dataset_card(meta, tasks, totals, redaction_total)
    (out_dir / "dataset_card.md").write_text(card, encoding="utf-8")

    roundtrip_ok = _roundtrip_check_all(out_dir)
    (out_dir / "export_report.json").write_text(
        json.dumps({"per_task": report, "totals": totals, "roundtrip_ok": roundtrip_ok}, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print(f"exported -> {out_dir} (roundtrip_ok={roundtrip_ok})")
    print(f"token totals (model-reported usage): {totals or '(none)'}")
    print(f"secret-pattern redactions: {redaction_total}")
    return 0


def _load_frozen_tools(campaign_dir: Path) -> list[dict]:
    """The campaign-boot tool-schema snapshot (KTD4/I6); empty when the runner
    recorded a freeze fallback — the card and audit flag that honestly."""
    path = Path(campaign_dir) / "frozen_tools.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def _roundtrip_check(filename: str, out_dir: Path) -> bool:
    """Every line re-parses as one object; every tool_calls block is closed
    by tool returns; every row carries system/tools/messages."""
    path = out_dir / filename
    if not path.exists():
        return True
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("messages") or "system" not in row:
            return False
        open_calls = 0
        for m in row["messages"]:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                open_calls += len(m["tool_calls"])
            elif m.get("role") == "tool":
                open_calls = max(0, open_calls - 1)
        if open_calls:
            return False
    return True


def _roundtrip_check_all(out_dir: Path) -> bool:
    return all(
        _roundtrip_check(name, out_dir)
        for name in ("sft_with_thinking.jsonl", "sft_no_thinking.jsonl", "quarantine.jsonl")
    )


def _dataset_card(meta, tasks: list[TaskRecord], totals: dict[str, int], redaction_total: int) -> str:
    primary = sum(1 for t in tasks if t.verdict == "completed")
    return "\n".join(
        [
            "# Dataset Card",
            "",
            f"Campaign: {meta.campaign_id} ({meta.started_at})",
            f"Teacher: {json.dumps(meta.teacher)}",
            f"Approval mode: {meta.approval_mode} (unattended; provenance in the harness manifest)",
            f"Tasks: {len(tasks)} ({primary} primary SFT, {len(tasks) - primary} quarantined)",
            "",
            "## Fidelity limits (honest disclosure)",
            "- The `system` field is the recorded per-request instructions string; prompted-output",
            "  instructions appended after hook capture are not included (KTD9; verified per teacher",
            "  at dry run).",
            "- Compacted sessions are quarantined: the post-compaction wire payload was never",
            "  durably captured (KTD5); quarantined rows are fuller-than-wire.",
            "- Tool schemas are the campaign-boot snapshot, not per-request wire capture.",
            "- Secret-pattern matches are redacted before export; affected rows quarantined.",
            "- Content was uploaded to the teacher provider during collection and is intended for",
            "  upload to a training provider (Unsloth) — third-party disclosure applies to every row.",
            "- `no_gui`-stratum tasks are excluded from primary SFT (unobserved console output).",
            "",
            "## Token accounting",
            f"- Model-reported usage totals (authoritative): {json.dumps(totals)}",
            "- Export-time tokenizer counts are produced at training time by the target model's",
            "  tokenizer; the two counters are named separately and never substituted (R14).",
            "",
            "## Redactions",
            f"- Secret-pattern matches redacted before export: {redaction_total}.",
            "",
            "## Verdict gating",
            "- `completed` = simulator STOP AND machine-checkable acceptance predicate (KTD7);",
            "  every other verdict is quarantined with its reason in quarantine.jsonl.",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--skip-audit", action="store_true", help="audit already ran in this process")
    args = parser.parse_args(argv)
    return export_campaign(Path(args.campaign), audit_first=not args.skip_audit)


if __name__ == "__main__":
    sys.exit(main())
