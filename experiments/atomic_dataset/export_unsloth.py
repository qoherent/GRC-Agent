"""Convert the Tier-1 SFT trajectories to Unsloth-ready chat JSONL.

Audits every row (structural + wire-format checks), drops defective
trajectories, and emits one JSON object per line in OpenAI chat format:

    {"scenario_id": ..., "category": ..., "messages": [...]}

The ``messages`` list is byte-identical to the captured wire format
(system / user / assistant(+tool_calls) / tool(tool_call_id) turns).
The captured ``reasoning`` field of each assistant message is merged into
its ``content`` wrapped in think open/close tags (Qwen3 chat template).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent / "output"
SRC = OUT / "tier1_sft_trajectories.jsonl"
DST = OUT / "tier1_unsloth.jsonl"
REPORT = OUT / "tier1_unsloth_report.json"

# Built at runtime so the tag literals never appear in this source file.
_THINK_OPEN = chr(60) + "think" + chr(62)
_THINK_CLOSE = chr(60) + "/" + "think" + chr(62)


def _tool_names(row: dict) -> set[str]:
    return {t["function"]["name"] for t in row["tools"]}


def audit_row(row: dict) -> list[str]:
    """Return a list of defects (empty == clean)."""
    errs: list[str] = []
    if not row.get("flowgraph_valid"):
        errs.append("flowgraph_invalid")
    names = _tool_names(row)
    call_ids: set[str] = set()
    for j, m in enumerate(row["messages"]):
        if m["role"] == "assistant":
            for tc in m.get("tool_calls", []):
                nm = tc["function"]["name"]
                if nm not in names:
                    errs.append(f"invalid_tool_name@{j}: {nm[:60]!r}")
                call_ids.add(tc["id"])
                try:
                    json.loads(tc["function"]["arguments"])
                except Exception:  # noqa: BLE001
                    errs.append(f"invalid_arguments_json@{j}")
        elif m["role"] == "tool" and m.get("tool_call_id") not in call_ids:
            errs.append(f"orphan_tool_result@{j}")
    last = row["messages"][-1]
    if last["role"] != "assistant":
        errs.append(f"ends_on_{last['role']}")
    elif not (last.get("content") or "").strip():
        tcs = last.get("tool_calls") or []
        if not any(t["function"]["name"] == "final_result" for t in tcs):
            errs.append("ends_on_non_final_tool_call")
    return errs


def convert_messages(row: dict) -> list[dict]:
    """Wire messages -> chat-template messages (reasoning merged for Qwen3)."""
    out = []
    for m in row["messages"]:
        if m["role"] == "assistant":
            content = m.get("content") or ""
            reasoning = (m.get("reasoning") or "").strip()
            if reasoning:
                body = content if content.strip() else ""
                content = f"{_THINK_OPEN}\n{reasoning}\n{_THINK_CLOSE}" + (f"\n{body}" if body else "")
            mm = {"role": "assistant", "content": content}
            if m.get("tool_calls"):
                mm["tool_calls"] = m["tool_calls"]
            out.append(mm)
        else:
            mm = {"role": m["role"], "content": m.get("content") or ""}
            if m["role"] == "tool":
                mm["tool_call_id"] = m.get("tool_call_id")
            out.append(mm)
    return out


def main() -> None:
    dropped: list[dict] = []
    kept = 0
    with SRC.open() as src, DST.open("w") as dst:
        for i, line in enumerate(src, 1):
            row = json.loads(line)
            errs = audit_row(row)
            if errs:
                dropped.append({"row": i, "scenario_id": row["scenario_id"], "reasons": errs})
                continue
            dst.write(json.dumps(
                {
                    "scenario_id": row["scenario_id"],
                    "category": row["category"],
                    "messages": convert_messages(row),
                },
                ensure_ascii=False,
            ) + "\n")
            kept += 1

    report = {
        "source": str(SRC.name),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_rows": kept + len(dropped),
        "converted": kept,
        "dropped": dropped,
        "reasoning_merge": "assistant reasoning field merged into content wrapped in "
        "think open/close tags (Qwen3 chat template); tool_calls/tool_call_id kept verbatim",
        "format": "OpenAI chat JSONL ({'scenario_id','category','messages'}) for Unsloth SFTDataset",
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"converted: {kept} | dropped: {len(dropped)} -> {DST.name}, {REPORT.name}")
    for d in dropped:
        print(f"  dropped row {d['row']} {d['scenario_id']}: {d['reasons']}")


if __name__ == "__main__":
    main()
