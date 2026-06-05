"""Docs-answer helper-advisor orchestration."""

from __future__ import annotations

import logging
import socket
import time
from typing import Any
from urllib.parse import urlsplit

from grc_agent.runtime.docs_answer_advisor import (
    DocsAnswerSnippet,
)
from grc_agent.runtime.docs_answer_advisor import (
    run_docs_answer_advisor as run_docs_answer_advisor_core,
)
from grc_agent.toolagents_runtime import (
    ToolAgentsJsonClient,
    ToolAgentsLlamaProviderConfig,
)

logger = logging.getLogger(__name__)


def classify_docs_advisor_error(message: str) -> str:
    lower = message.lower()
    if "timed out" in lower or "timeout" in lower:
        return "timeout"
    if "unsupported keys" in lower or "missing keys" in lower or "must be" in lower:
        return "schema_parse_failure"
    if "malformed json" in lower or "must be object" in lower:
        return "malformed_helper_output"
    if "context" in lower and "length" in lower:
        return "prompt_too_large"
    if "http 400" in lower or "http 404" in lower:
        if "model" in lower:
            return "config_issue"
        return "implementation_bug"
    if "transport failure" in lower:
        return "llama_server_unavailable"
    return "implementation_bug"


def run_docs_answer_advisor(
    agent: Any,
    *,
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None,
) -> dict[str, Any] | None:
    estimated_prompt_chars = (
        len(question)
        + (len(focus) if isinstance(focus, str) else 0)
        + sum(
            len(snippet.title) + len(snippet.source) + len(snippet.excerpt)
            for snippet in snippets
        )
    )
    agent._last_docs_advisor_meta = {
        "advisor_attempted": False,
        "advisor_success": False,
        "fallback_reason": "not_attempted",
        "helper_latency_ms": None,
        "prompt_chars": 0,
        "snippet_count": len(snippets),
        "schema_valid": False,
        "timeout_ms": int(agent._docs_answer_cfg.helper_timeout_seconds * 1000),
        "cache_hit": False,
        "helper_finish_reason": None,
    }
    if not agent._llama_server_url.strip() or not agent._llama_model.strip():
        agent._last_docs_advisor_meta["fallback_reason"] = "helper_disabled"
        agent._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
        return None
    if not snippets:
        agent._last_docs_advisor_meta["fallback_reason"] = "retrieval_empty"
        agent._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
        return None
    now = time.monotonic()
    if now >= agent._docs_advisor_probe_at:
        agent._docs_advisor_reachable = probe_docs_advisor_server(agent)
        agent._docs_advisor_probe_at = now + (
            agent._docs_answer_cfg.retry_interval_on_success_seconds
            if agent._docs_advisor_reachable
            else agent._docs_answer_cfg.retry_interval_on_failure_seconds
        )
    if not agent._docs_advisor_reachable:
        agent._last_docs_advisor_meta["fallback_reason"] = "llama_server_unavailable"
        agent._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
        return None
    agent._last_docs_advisor_meta["advisor_attempted"] = True
    started = time.perf_counter()
    try:
        provider_config = ToolAgentsLlamaProviderConfig(
            base_url=agent._llama_server_url,
            model=agent._llama_model,
            api_key=None,
            timeout_seconds=min(
                agent._llama_request_timeout_seconds,
                agent._docs_answer_cfg.helper_timeout_seconds,
            ),
            max_tokens=agent._docs_answer_cfg.helper_max_output_tokens,
            temperature=0.0,
            enable_thinking=False,
        )
        client = ToolAgentsJsonClient(
            provider_config,
            timeout_seconds=provider_config.timeout_seconds,
            max_tokens=provider_config.max_tokens,
            temperature=0.0,
            enable_thinking=False,
        )
        result = run_docs_answer_advisor_core(
            client=client,
            model=agent._llama_model,
            question=question,
            answer_type=answer_type,
            snippets=snippets,
            focus=focus,
            max_answer_chars=agent._docs_answer_cfg.answer_target_chars,
            max_excerpt_chars=agent._docs_answer_cfg.excerpt_target_chars,
            max_sources=agent._docs_answer_cfg.max_sources,
        )
        agent._last_docs_advisor_meta.update(
            {
                "advisor_success": True,
                "fallback_reason": "none",
                "helper_latency_ms": int(result.get("advisor_latency_ms") or 0),
                "prompt_chars": int(result.get("prompt_chars") or 0),
                "snippet_count": int(result.get("snippet_count") or len(snippets)),
                "schema_valid": bool(result.get("schema_valid")),
                "timeout_ms": int(
                    result.get("timeout_ms")
                    or int(agent._docs_answer_cfg.helper_timeout_seconds * 1000)
                ),
                "cache_hit": False,
                "helper_finish_reason": result.get("helper_finish_reason"),
            }
        )
        return result
    except Exception as exc:
        logger.info("docs_answer_advisor_failed error=%s", exc)
        agent._last_docs_advisor_meta.update(
            {
                "advisor_success": False,
                "fallback_reason": classify_docs_advisor_error(str(exc)),
                "helper_latency_ms": int((time.perf_counter() - started) * 1000),
                "prompt_chars": estimated_prompt_chars,
                "helper_finish_reason": "error",
            }
        )
        return None


def probe_docs_advisor_server(agent: Any) -> bool:
    """Cheap connectivity probe to avoid repeated long helper timeouts."""
    try:
        parsed = urlsplit(agent._llama_server_url)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return False
        with socket.create_connection(
            (host, int(port)),
            timeout=agent._docs_answer_cfg.probe_timeout_seconds,
        ):
            return True
    except Exception:
        return False
