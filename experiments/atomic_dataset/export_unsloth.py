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

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent / "output"

#: Suite -> (source JSONL, destination JSONL, report JSON).
SUITES: dict[str, tuple[str, str, str]] = {
    "tier1": ("tier1_sft_trajectories.jsonl", "tier1_unsloth.jsonl", "tier1_unsloth_report.json"),
    "tier2": ("tier2_sft_trajectories.jsonl", "tier2_unsloth.jsonl", "tier2_unsloth_report.json"),
}

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
    parser = argparse.ArgumentParser(description="Convert a tier SFT trajectory JSONL to Unsloth-ready chat JSONL.")
    parser.add_argument("--suite", default="tier1", choices=sorted(SUITES), help="Source suite (default: tier1)")
    args = parser.parse_args()
    src_name, dst_name, report_name = SUITES[args.suite]
    src, dst, report = OUT / src_name, OUT / dst_name, OUT / report_name
    if not src.is_file():
        raise SystemExit(f"missing {src} — run the collector first")

    dropped: list[dict] = []
    kept = 0
    with src.open() as f, dst.open("w") as dstf:
        for i, line in enumerate(f, 1):
            row = json.loads(line)
            errs = audit_row(row)
            if errs:
                dropped.append({"row": i, "scenario_id": row["scenario_id"], "reasons": errs})
                continue
            out_row = {
                "scenario_id": row["scenario_id"],
                "category": row["category"],
                "messages": convert_messages(row),
            }
            if "turns" in row:
                out_row["turns"] = row["turns"]
            dstf.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            kept += 1

    report_doc = {
        "suite": args.suite,
        "source": src.name,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_rows": kept + len(dropped),
        "converted": kept,
        "dropped": dropped,
        "reasoning_merge": "assistant reasoning field merged into content wrapped in "
        "think open/close tags (Qwen3 chat template); tool_calls/tool_call_id kept verbatim",
        "format": "OpenAI chat JSONL ({'scenario_id','category','messages'}[, 'turns']) for Unsloth SFTDataset",
    }
    report.write_text(json.dumps(report_doc, indent=2, ensure_ascii=False))
    print(f"[{args.suite}] converted: {kept} | dropped: {len(dropped)} -> {dst.name}, {report.name}")
    for d in dropped:
        print(f"  dropped row {d['row']} {d['scenario_id']}: {d['reasons']}")


if __name__ == "__main__":
    main()
