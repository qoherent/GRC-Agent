"""Live-integration tests for the RAG stack (vec1 + ollama embeddinggemma).

These tests verify real behavior end-to-end: real embeddings, real vec1
search, real LLM synthesis. They are SKIPPED by default because they
require a running ``ollama serve`` with ``embeddinggemma:latest`` and
the configured chat model pulled.

Run them with::

    GRC_AGENT_LIVE_RAG=1 pytest tests/retrieval_eval/test_rag_integration.py -v

These tests are the regression net for the audit findings S1 (DB
pollution), S2 (empty model), and S10 (chunking). The mock-based tests
in ``tests/test_mvp_tool_profile.py`` cannot catch any of these — they
mock every external boundary, which is exactly how S1 and S2 shipped.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import struct
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("GRC_AGENT_LIVE_RAG") != "1",
    reason="Set GRC_AGENT_LIVE_RAG=1 to run live RAG integration tests (requires ollama).",
)

_LIVE_DB_DIR = Path(os.environ.get("GRC_AGENT_VECTORS_DIR", ".grc_agent/vectors"))
_LIVE_DB_PATH = _LIVE_DB_DIR / "docs_v1.db"


def _ingest_into(db_path: Path, server_url: str, corpus_dir: Path) -> None:
    """Trigger a real ingestion into ``db_path`` from ``corpus_dir``.

    Uses the production code path: VectorDocsStore.ingest_if_needed with
    the live embeddinggemma endpoint. Each call is ~1s per chunk, so test
    corpora are kept tiny.
    """
    from grc_agent.runtime.doc_answer import VectorDocsStore, _wiki_corpus_root

    # _wiki_corpus_root is what ingest_if_needed uses to find the source
    # markdown; we point it at our temp corpus via monkeypatching the
    # module global.
    import grc_agent.runtime.doc_answer as mod

    original_root = mod._wiki_corpus_root
    mod._wiki_corpus_root = lambda: corpus_dir
    try:
        store = VectorDocsStore(db_path, server_url)
        store.ingest_if_needed()
    finally:
        mod._wiki_corpus_root = original_root


def _make_corpus(tmp: Path, pages: dict[str, str]) -> Path:
    corpus = tmp / "corpus"
    corpus.mkdir()
    for name, body in pages.items():
        (corpus / f"{name}.md").write_text(body, encoding="utf-8")
    return corpus


# ---------------------------------------------------------------------------
# S1 — DB pollution rejection
# ---------------------------------------------------------------------------


def test_is_db_usable_rejects_all_constant_vectors(tmp_path: Path) -> None:
    """is_db_usable must reject a DB whose stored embeddings have zero
    variance. This is the exact signature of the test-mock pollution
    that shipped with the original RAG (S1)."""
    from grc_agent.runtime.doc_answer import VectorDocsStore, is_db_usable

    db_path = tmp_path / "polluted.db"
    store = VectorDocsStore(db_path, "http://localhost:11434")
    conn = store._get_connection()
    try:
        store.init_db(conn)
        const_vec = struct.pack("768f", *([1.0] * 768))
        for rid in range(1, 6):
            conn.execute(
                "INSERT INTO document_chunks(rowid, title, source, heading, excerpt) VALUES (?, ?, ?, ?, ?)",
                (rid, "T", "t.md", "h", "content"),
            )
            conn.execute(
                "INSERT INTO document_idx(rowid, embedding) VALUES (?, ?)",
                (rid, const_vec),
            )
        conn.execute(
            "INSERT INTO document_idx(cmd, arg) VALUES('rebuild', '{\"index\": \"flat\", \"distance\": \"cos\"}')"
        )
        conn.commit()
    finally:
        conn.close()

    assert not is_db_usable(db_path), "DB of identical vectors must be rejected"


def test_is_db_usable_accepts_real_embeddings(tmp_path: Path) -> None:
    """After live ingestion of a tiny corpus, is_db_usable must return True."""
    corpus = _make_corpus(
        tmp_path,
        {
            "Alpha": "# Alpha\nThe quick brown fox jumps over the lazy dog.",
            "Beta": "# Beta\nPack my box with five dozen liquor jugs.",
        },
    )
    db_path = tmp_path / "live.db"
    _ingest_into(db_path, "http://localhost:11434", corpus)

    from grc_agent.runtime.doc_answer import is_db_usable

    assert is_db_usable(db_path)


def test_live_ingestion_produces_non_constant_vectors(tmp_path: Path) -> None:
    """Stored embeddings from real embeddinggemma must have non-zero
    variance and be pairwise distinct (cos_sim < 0.95). Catches any
    future regression where the ingestion writes placeholder vectors."""
    corpus = _make_corpus(
        tmp_path,
        {
            "Alpha": "# Alpha\nThe quick brown fox jumps over the lazy dog.",
            "Beta": "# Beta\nPack my box with five dozen liquor jugs.",
            "Gamma": "# Gamma\nSphinx of black quartz, judge my vow.",
        },
    )
    db_path = tmp_path / "live.db"
    _ingest_into(db_path, "http://localhost:11434", corpus)

    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    conn.load_extension(str(Path("vec1.so").resolve()))
    rows = conn.execute("SELECT rowid, embedding FROM document_idx").fetchall()
    conn.close()

    assert len(rows) >= 3
    vectors = []
    for rid, raw in rows:
        floats = struct.unpack(f"{len(raw) // 4}f", raw)
        assert max(floats) != min(floats), f"rowid={rid} is a constant vector"
        vectors.append(floats)
    # every pair must be distinct
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            dot = sum(a * b for a, b in zip(vectors[i], vectors[j], strict=False))
            na = sum(a * a for a in vectors[i]) ** 0.5
            nb = sum(b * b for b in vectors[j]) ** 0.5
            cos_sim = dot / (na * nb) if na and nb else 0.0
            assert cos_sim < 0.99, f"rowid {i+1} and {j+1} are identical (cos_sim={cos_sim:.4f})"


# ---------------------------------------------------------------------------
# Retrieval correctness (S10 chunking + S8 prefix)
# ---------------------------------------------------------------------------


def test_live_retrieval_finds_canonical_pmt_chunk(tmp_path: Path) -> None:
    """Query 'What is a PMT?' must surface a Polymorphic-Types chunk
    as the top-1 result, not AcademicPapers / ALSAPulseAudio (the
    exact wrong-answer pattern that shipped with the polluted DB)."""
    wiki = Path(__file__).resolve().parents[2] / "docs" / "wiki_gnuradio_org"
    if not wiki.is_dir():
        pytest.skip("wiki_gnuradio_org corpus not available")
    db_path = tmp_path / "wiki.db"
    _ingest_into(db_path, "http://localhost:11434", wiki)

    import httpx
    from grc_agent.runtime.doc_answer import VectorDocsStore

    q_text = "task: search result | query: What is a PMT?"
    r = httpx.post(
        "http://localhost:11434/api/embed",
        json={"model": "embeddinggemma:latest", "input": q_text},
        timeout=30.0,
    )
    r.raise_for_status()
    qv = r.json()["embeddings"][0]

    store = VectorDocsStore(db_path, "http://localhost:11434")
    matched = store.search(qv, limit=3)
    assert matched, "no results returned"
    titles = [m["title"] for m in matched]

    # Positive: the canonical PMT page must appear in the top-3.
    assert "Polymorphic Types (PMTs)" in titles, (
        f"Polymorphic Types (PMTs) must be in top-3 for 'What is a PMT?'; "
        f"got top-3: {titles}"
    )
    # Negative: the original polluted-DB false positives (audit finding S1)
    # must NOT appear in top-3.
    forbidden = {"AcademicPapers", "ALSAPulseAudio", "AGC"}
    violated = forbidden & set(titles)
    assert not violated, (
        f"top-3 must not contain polluted-DB false positives {violated}; "
        f"got top-3: {titles}"
    )
    best = matched[0]["distance"]
    assert best < 0.7, (
        f"top-1 distance {best:.4f} too high; chunking or embedding is weak"
    )


# ---------------------------------------------------------------------------
# S2 — LLM synthesis actually works end-to-end
# ---------------------------------------------------------------------------


def test_live_ask_grc_docs_returns_grounded_answer(tmp_path: Path) -> None:
    """Full ask_grc_docs against the live stack. Must return a grounded
    answer with confidence medium/high and degraded_retrieval=False.
    Catches S2 (empty model) and any future regression of the synthesis
    path."""
    wiki = Path(__file__).resolve().parents[2] / "docs" / "wiki_gnuradio_org"
    if not wiki.is_dir():
        pytest.skip("wiki_gnuradio_org corpus not available")

    # Use a per-test DB so the run is reproducible
    tmp_db_dir = tmp_path / "vecs"
    tmp_db_dir.mkdir()
    tmp_db = tmp_db_dir / "docs_v1.db"
    _ingest_into(tmp_db, "http://localhost:11434", wiki)

    from grc_agent.agent import GrcAgent
    from grc_agent.flowgraph_session import FlowgraphSession

    fixture = Path(__file__).resolve().parents[1] / "data" / "random_bit_generator.grc"
    session = FlowgraphSession()
    session.load(fixture)

    import grc_agent.runtime.doc_answer as mod
    original_path = mod.DB_PATH
    mod.DB_PATH = tmp_db
    try:
        agent = GrcAgent(session)
        result = agent.execute_tool("ask_grc_docs", {"question": "What is a PMT?"})
    finally:
        mod.DB_PATH = original_path

    assert result["ok"], result
    assert not result.get("degraded_retrieval"), result
    assert not result.get("fallback_used"), result
    assert result.get("confidence") in {"medium", "high"}, result
    answer = (result.get("answer") or "").lower()
    assert "pmt" in answer or "polymorphic" in answer, (
        f"answer must mention PMT/Polymorphic Types; got: {result.get('answer')!r}"
    )
    assert len(result.get("answer") or "") > 40, "answer too short — synthesis fell back"
