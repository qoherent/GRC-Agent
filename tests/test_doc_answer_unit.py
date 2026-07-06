"""Direct unit tests for ``ask_grc_docs`` — the model-facing docs-RAG tool.

No Ollama, no live embeddings. The httpx post (embedding) and the
``call_agent_llm`` (answer generation) are mocked so we can pin:
  * the prompt structure (path + heading + body, word-capped)
  * the citation shape (path + distance per source)
  * every error path (empty question, retrieval failure, no hits, LLM error)
"""

from __future__ import annotations

from unittest import mock

from grc_agent.config import RetrievalConfig
from grc_agent.domain_models import ErrorCode


class FakeAgent:
    """Bare-minimum GrcAgent stub for ``ask_grc_docs`` tests."""

    def __init__(self, *, retrieval_cfg=None, llama_url="http://llama"):
        self._retrieval_cfg = retrieval_cfg or RetrievalConfig(
            ask_grc_docs_default_k=3,
            search_blocks_default_k=5,
        )
        self._llama_server_url = llama_url
        self._llama_backend = "ollama"
        self._embedding_model = "embeddinggemma:latest"
        self._embedding_api_key = "not-needed"

    def _tool_result(self, tool_name, *, ok, message=None, error_type=None):
        return {
            "ok": ok,
            "tool": tool_name,
            "message": message,
            "error_type": error_type,
        }

    def _payload_result(self, tool_name, payload):
        return {"ok": payload.get("ok", True), "tool": tool_name, **payload}


def _hit(path="wiki/widget.md", heading="Widget", text="body text", distance=0.1):
    return {
        "path": path,
        "heading": heading,
        "text": text,
        "distance": distance,
    }


# --- Cross-import regression (WS2 Task 1) -----------------------------------


def test_embedding_constants_live_in_dedicated_module():
    """Constants are the single source of truth across both stores."""
    import grc_agent.runtime._embedding_config as cfg
    from grc_agent.runtime.catalog_vector import (
        _DOCUMENT_PREFIX as _CDP,
    )
    from grc_agent.runtime.catalog_vector import (
        _QUERY_PREFIX as _CQP,
    )
    from grc_agent.runtime.doc_answer import _DOCUMENT_PREFIX, _QUERY_PREFIX

    assert _DOCUMENT_PREFIX == cfg._DOCUMENT_PREFIX == _CDP
    assert _QUERY_PREFIX == cfg._QUERY_PREFIX == _CQP


# --- Empty question path ----------------------------------------------------


def test_empty_question_returns_invalid_request():
    from grc_agent.runtime.doc_answer import ask_grc_docs

    payload = ask_grc_docs(FakeAgent(), question="   ")
    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.INVALID_REQUEST
    assert "non-empty" in payload["message"]


def test_non_string_question_returns_invalid_request():
    from grc_agent.runtime.doc_answer import ask_grc_docs

    payload = ask_grc_docs(FakeAgent(), question=42)
    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.INVALID_REQUEST


# --- Successful path: prompt + citations -----------------------------------


def test_successful_call_includes_sources_and_answer():
    from grc_agent.runtime.doc_answer import ask_grc_docs

    fake_agent = FakeAgent()
    hits = [
        _hit("wiki/widget.md", "Widget", "Widget text 1", 0.05),
        _hit("wiki/gizmo.md", "Gizmo", "Gizmo text 2", 0.10),
    ]
    with (
        mock.patch("grc_agent.runtime.doc_answer.VectorDocsStore") as FakeStore,
        mock.patch(
            "grc_agent.runtime.doc_answer._generate_grounded_answer",
            return_value="Two relevant blocks were found.",
        ),
    ):
        FakeStore.return_value.search.return_value = hits
        with mock.patch(
            "grc_agent.runtime.doc_answer.embed_query",
            return_value=[0.0] * 768,
        ) as eq:
            payload = ask_grc_docs(fake_agent, question="What is a widget?")

    assert payload["ok"] is True
    assert payload["question"] == "What is a widget?"
    assert payload["answer"] == "Two relevant blocks were found."
    assert payload["sources"] == [
        {"path": "wiki/widget.md", "distance": 0.05},
        {"path": "wiki/gizmo.md", "distance": 0.10},
    ]
    eq.assert_called_once_with(
        fake_agent._llama_server_url,
        "What is a widget?",
        model=fake_agent._embedding_model,
        api_key=fake_agent._embedding_api_key,
    )


def test_prompt_includes_each_source_path_heading_and_body():
    """The LLM prompt must carry every source (path + heading + content)."""
    from grc_agent.runtime.doc_answer import _generate_grounded_answer

    captured: list = []

    def fake_llm(agent, prompt):
        captured.append(prompt)
        return "answer"

    sources = [
        {
            "path": "wiki/widget.md",
            "heading": "Widget",
            "distance": 0.1,
            "content": "Widget reference body.",
        },
        {
            "path": "wiki/gizmo.md",
            "heading": "Gizmo",
            "distance": 0.2,
            "content": "Gizmo reference body.",
        },
    ]
    with mock.patch("grc_agent.runtime.doc_answer.call_agent_llm", side_effect=fake_llm):
        _generate_grounded_answer(FakeAgent(), "What?", sources)
    assert len(captured) == 1
    prompt = captured[0]
    assert "wiki/widget.md" in prompt and "Widget" in prompt
    assert "wiki/gizmo.md" in prompt and "Gizmo" in prompt
    assert "Widget reference body." in prompt
    assert "Gizmo reference body." in prompt
    assert "What?" in prompt


# --- Error paths ----------------------------------------------------------


def test_retrieval_backend_failure_returns_retrieval_not_ready():
    from grc_agent.runtime.doc_answer import ask_grc_docs

    fake_agent = FakeAgent()
    with mock.patch(
        "grc_agent.runtime.doc_answer.embed_query",
        side_effect=ConnectionError("embedding server down"),
    ):
        payload = ask_grc_docs(fake_agent, question="anything")

    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.RETRIEVAL_NOT_READY
    assert "embedding server down" in payload["message"]


def test_no_chunk_hits_returns_retrieval_not_ready():
    from grc_agent.runtime.doc_answer import ask_grc_docs

    fake_agent = FakeAgent()
    with (
        mock.patch("grc_agent.runtime.doc_answer.VectorDocsStore") as FakeStore,
        mock.patch(
            "grc_agent.runtime.doc_answer.embed_query",
            return_value=[0.0] * 768,
        ),
    ):
        FakeStore.return_value.search.return_value = []
        payload = ask_grc_docs(fake_agent, question="alien topic")

    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.RETRIEVAL_NOT_READY
    assert "No matching documentation" in payload["message"]


def test_answer_generation_failure_returns_internal_error():
    from grc_agent.runtime.doc_answer import ask_grc_docs

    fake_agent = FakeAgent()
    with (
        mock.patch("grc_agent.runtime.doc_answer.VectorDocsStore") as FakeStore,
        mock.patch(
            "grc_agent.runtime.doc_answer.embed_query",
            return_value=[0.0] * 768,
        ),
        mock.patch(
            "grc_agent.runtime.doc_answer._generate_grounded_answer",
            side_effect=RuntimeError("LLM boom"),
        ),
    ):
        FakeStore.return_value.search.return_value = [
            _hit("wiki/x.md", "X", "body", 0.1),
        ]
        payload = ask_grc_docs(fake_agent, question="what?")

    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.INTERNAL_ERROR
    assert "LLM boom" in payload["message"]
