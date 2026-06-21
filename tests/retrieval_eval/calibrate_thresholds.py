"""Empirical calibration of vec1 cosine-distance thresholds.

This is NOT a pytest test — it is a manual benchmark whose output is the
source of truth for ``DISTANCE_THRESHOLD_HIGH``, ``DISTANCE_THRESHOLD_MEDIUM``,
and ``INSUFFICIENT_EVIDENCE_DISTANCE`` in ``src/grc_agent/runtime/doc_answer.py``.

Usage::

    # 1. Make sure the live DB is freshly ingested with the current
    #    embedding model + chunking strategy (delete .grc_agent/vectors/
    #    docs_v1.db and trigger one warmup).
    # 2. Run this script.
    # 3. Read the histogram and update the constants in doc_answer.py
    #    plus the table in docs/MODEL_CONTEXT_BIBLE.md.

The calibration set is a hand-curated list of (query, expected_title)
pairs spanning the wiki's common question types. Run it whenever the
embedding model, the chunking strategy, or the task-prefix format changes.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

import httpx

from grc_agent.runtime.doc_answer import (
    DB_PATH,
    _embed_query_text,
)

CALIBRATION_CASES: list[tuple[str, tuple[str, ...]]] = [
    ("What is a PMT?", ("Polymorphic Types", "pmt architecture")),
    ("What are stream tags?", ("Stream Tags", "Programming Topics")),
    ("How do message ports differ from stream ports?", ("Message Passing",)),
    ("What is the sample rate?", ("Audio Sink", "Audio Source", "Sample")),
    ("How does the throttle block work?", ("Throttle", "YAML GRC")),
    ("What is a polymorphic type?", ("Polymorphic Types", "pmt architecture")),
    ("Explain PDUs", ("Polymorphic Types", "pmt architecture", "Message Passing")),
    ("What does the FFT block do?", ("FFT", "Spectrum", "Window")),
    ("How do I use the QT GUI sink?", ("QT GUI", "QTGUI", "GUI")),
    ("What is the difference between float and complex?", ("Type", "data types")),
    ("Tell me about ZMQ blocks", ("ZMQ", "ZeroMQ")),
    ("How do I write an out-of-tree module?", ("OutOfTreeModules", "Out of Tree", "Module")),
]


def _embed(text: str, model: str = "embeddinggemma:latest") -> list[float]:
    r = httpx.post(
        "http://localhost:11434/api/embed",
        json={"model": model, "input": text},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()["embeddings"][0]


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(
            f"DB not found at {DB_PATH}. Ingest first via GrcAgent.warmup_vector_index()."
        )
    # Resolve vec1.so the same way VectorDocsStore does (no CWD fallback).
    vec1_so: Path | None = None
    for parent in Path(__file__).resolve().parents:
        cand = parent / "vec1.so"
        if cand.exists():
            vec1_so = cand
            break
    if vec1_so is None:
        raise SystemExit("vec1.so not found alongside the grc_agent package.")
    conn = sqlite3.connect(str(DB_PATH))
    conn.enable_load_extension(True)
    conn.load_extension(str(vec1_so))
    conn.row_factory = sqlite3.Row

    print(f"{'query':50s} {'top-1 dist':>10s} {'top-3 dist':>10s} {'top-1 title':40s} relevant?")
    relevant_dists: list[float] = []
    irrelevant_dists: list[float] = []
    for query, good in CALIBRATION_CASES:
        qv = _embed(_embed_query_text(query))
        packed = struct.pack(f"{len(qv)}f", *qv)
        rows = conn.execute(
            "SELECT rowid, distance FROM document_idx(?, ?)",
            (packed, json.dumps({"K": 3})),
        ).fetchall()
        titles: list[str] = []
        for r in rows:
            ch = conn.execute(
                "SELECT title FROM document_chunks WHERE rowid = ?", (r["rowid"],)
            ).fetchone()
            titles.append(ch["title"] if ch else "?")
        d1 = rows[0]["distance"]
        d3 = rows[-1]["distance"]
        relevant = any(
            any(g.lower() in t.lower() for g in good) for t in titles[:2]
        )
        print(
            f"{query[:50]:50s} {d1:10.4f} {d3:10.4f} "
            f"{titles[0][:40]:40s} {'Y' if relevant else 'n'}"
        )
        (relevant_dists if relevant else irrelevant_dists).append(d1)

    print()
    if relevant_dists:
        print(
            f"RELEVANT top-1 distances:   n={len(relevant_dists)} "
            f"min={min(relevant_dists):.4f} max={max(relevant_dists):.4f} "
            f"mean={sum(relevant_dists) / len(relevant_dists):.4f}"
        )
    if irrelevant_dists:
        print(
            f"IRRELEVANT top-1 distances: n={len(irrelevant_dists)} "
            f"min={min(irrelevant_dists):.4f} max={max(irrelevant_dists):.4f} "
            f"mean={sum(irrelevant_dists) / len(irrelevant_dists):.4f}"
        )
    print()
    print("Suggested constants for src/grc_agent/runtime/doc_answer.py:")
    if relevant_dists:
        print(f"  DISTANCE_THRESHOLD_HIGH     = {sorted(relevant_dists)[len(relevant_dists) // 4]:.2f}")
        print(f"  DISTANCE_THRESHOLD_MEDIUM   = {max(relevant_dists):.2f}")
    if irrelevant_dists:
        print(f"  INSUFFICIENT_EVIDENCE_DISTANCE = {max(irrelevant_dists + relevant_dists):.2f}")
    conn.close()


if __name__ == "__main__":
    main()
