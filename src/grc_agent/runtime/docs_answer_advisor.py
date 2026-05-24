"""Bounded docs answer synthesis over local GNU Radio retrieval snippets."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import time
from typing import Any


class DocsAnswerAdvisorError(RuntimeError):
    """Raised when docs answer synthesis fails validation or transport."""


@dataclass(frozen=True)
class DocsAnswerSnippet:
    """One bounded snippet used for docs answer synthesis."""

    title: str
    source: str
    excerpt: str


def run_docs_answer_advisor(
    *,
    client: Any,
    model: str,
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None = None,
    max_answer_chars: int = 420,
    max_excerpt_chars: int = 260,
    max_sources: int = 3,
) -> dict[str, Any]:
    """Synthesize a concise grounded answer from bounded snippets."""
    start = time.perf_counter()
    messages = _build_docs_answer_messages(
        question=question,
        answer_type=answer_type,
        snippets=snippets,
        focus=focus,
        max_answer_chars=max_answer_chars,
        max_excerpt_chars=max_excerpt_chars,
        max_sources=max_sources,
    )
    prompt_chars = sum(len(str(message.get("content", ""))) for message in messages)
    snippet_count = len(snippets)
    timeout_value = float(getattr(client, "timeout_seconds", 0.0) or 0.0)
    timeout_ms = int(max(timeout_value, 0.0) * 1000)
    last_error: Exception | None = None
    response_formats: list[dict[str, Any]] = [
        {"type": "json_object"},
        _docs_answer_json_schema_response_format(),
    ]
    for response_format in response_formats:
        try:
            response = client.create_chat_completion(
                model=model,
                messages=messages,
                response_format=response_format,
            )
            payload = _parse_payload(_extract_assistant_text(response))
            result = _validate_payload(
                payload,
                max_answer_chars=max_answer_chars,
                max_excerpt_chars=max_excerpt_chars,
                max_sources=max_sources,
            )
            result["advisor_latency_ms"] = int((time.perf_counter() - start) * 1000)
            result["prompt_chars"] = prompt_chars
            result["snippet_count"] = snippet_count
            result["schema_valid"] = True
            result["timeout_ms"] = timeout_ms
            result["helper_finish_reason"] = _extract_finish_reason(response)
            return result
        except DocsAnswerAdvisorError as exc:
            lowered = str(exc).lower()
            if (
                "timed out" in lowered
                or "transport" in lowered
                or "http " in lowered
            ):
                raise
            last_error = exc
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise DocsAnswerAdvisorError(str(last_error)) from last_error
    raise DocsAnswerAdvisorError("docs advisor did not return a valid response")


def run_docs_answer_advisor_diagnostic(
    *,
    client: Any,
    model: str,
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None = None,
    max_answer_chars: int = 420,
    max_excerpt_chars: int = 260,
    max_sources: int = 3,
    response_mode: str = "json_object",
) -> dict[str, Any]:
    """Run a single diagnostic helper attempt with phase telemetry and raw output.

    Supported response_mode values:
    - "json_object"
    - "json_schema"
    - "plain_json" (no response_format; relies on prompt-only constraints)
    """
    total_started = time.perf_counter()
    prompt_started = time.perf_counter()
    messages = _build_docs_answer_messages(
        question=question,
        answer_type=answer_type,
        snippets=snippets,
        focus=focus,
        max_answer_chars=max_answer_chars,
        max_excerpt_chars=max_excerpt_chars,
        max_sources=max_sources,
    )
    prompt_build_ms = int((time.perf_counter() - prompt_started) * 1000)
    prompt_chars = sum(len(str(message.get("content", ""))) for message in messages)
    timeout_ms = int(max(float(getattr(client, "timeout_seconds", 0.0) or 0.0), 0.0) * 1000)

    response_format: dict[str, Any] | None
    if response_mode == "json_object":
        response_format = {"type": "json_object"}
    elif response_mode == "json_schema":
        response_format = _docs_answer_json_schema_response_format()
    elif response_mode == "plain_json":
        response_format = None
    else:
        raise DocsAnswerAdvisorError(f"unsupported response mode: {response_mode}")

    raw_response_text = ""
    raw_model_output = ""
    response_parse_error = ""
    payload_parse_error = ""
    validation_error = ""
    finish_reason = None
    http_request_ms = 0
    generation_ms = 0
    parsing_started = 0.0
    parsing_ms = 0
    validation_ms = 0

    try:
        transport = client.create_chat_completion_raw(
            model=model,
            messages=messages,
            response_format=response_format,
        )
        raw_response_text = str(transport.get("raw_response_text") or "")
        http_request_ms = int(transport.get("http_request_ms") or 0)
        generation_ms = int(transport.get("generation_ms") or 0)
    except Exception as exc:
        return {
            "ok": False,
            "response_mode": response_mode,
            "question": question,
            "answer_type": answer_type,
            "prompt_chars": prompt_chars,
            "snippet_count": len(snippets),
            "timeout_ms": timeout_ms,
            "error_kind": "timeout" if "timed out" in str(exc).lower() else "transport",
            "error_message": str(exc),
            "raw_response_text": raw_response_text,
            "raw_model_output": raw_model_output,
            "finish_reason": finish_reason,
            "response_parse_error": response_parse_error,
            "payload_parse_error": payload_parse_error,
            "validation_error": validation_error,
            "phase_ms": {
                "prompt_build": prompt_build_ms,
                "http_request": http_request_ms,
                "generation": generation_ms,
                "parsing": parsing_ms,
                "validation": validation_ms,
                "total": int((time.perf_counter() - total_started) * 1000),
            },
            "messages": messages,
        }

    parsing_started = time.perf_counter()
    parsed_response: dict[str, Any] | None = None
    try:
        parsed = json.loads(raw_response_text)
        if not isinstance(parsed, dict):
            raise DocsAnswerAdvisorError("docs advisor response must be an object")
        parsed_response = parsed
    except Exception as exc:
        response_parse_error = str(exc)
    parsing_ms += int((time.perf_counter() - parsing_started) * 1000)

    if isinstance(parsed_response, dict):
        finish_reason = _extract_finish_reason(parsed_response)
        payload_parse_started = time.perf_counter()
        try:
            raw_model_output = _extract_assistant_text(parsed_response)
            payload = _parse_payload(raw_model_output)
        except Exception as exc:
            payload_parse_error = str(exc)
            payload = None
        parsing_ms += int((time.perf_counter() - payload_parse_started) * 1000)
    else:
        payload = None

    if payload is not None:
        validation_started = time.perf_counter()
        try:
            result = _validate_payload(
                payload,
                max_answer_chars=max_answer_chars,
                max_excerpt_chars=max_excerpt_chars,
                max_sources=max_sources,
            )
            validation_ms = int((time.perf_counter() - validation_started) * 1000)
            return {
                "ok": True,
                "response_mode": response_mode,
                "question": question,
                "answer_type": answer_type,
                "prompt_chars": prompt_chars,
                "snippet_count": len(snippets),
                "timeout_ms": timeout_ms,
                "result": {
                    **result,
                    "helper_finish_reason": finish_reason,
                },
                "raw_response_text": raw_response_text,
                "raw_model_output": raw_model_output,
                "finish_reason": finish_reason,
                "error_kind": "",
                "error_message": "",
                "response_parse_error": response_parse_error,
                "payload_parse_error": payload_parse_error,
                "validation_error": validation_error,
                "phase_ms": {
                    "prompt_build": prompt_build_ms,
                    "http_request": http_request_ms,
                    "generation": generation_ms,
                    "parsing": parsing_ms,
                    "validation": validation_ms,
                    "total": int((time.perf_counter() - total_started) * 1000),
                },
                "messages": messages,
            }
        except Exception as exc:
            validation_error = str(exc)
            validation_ms = int((time.perf_counter() - validation_started) * 1000)

    error_kind = "parse_response"
    error_message = response_parse_error or payload_parse_error or validation_error or "unknown"
    if payload_parse_error:
        error_kind = "parse_payload"
    if validation_error:
        error_kind = "validation"
    return {
        "ok": False,
        "response_mode": response_mode,
        "question": question,
        "answer_type": answer_type,
        "prompt_chars": prompt_chars,
        "snippet_count": len(snippets),
        "timeout_ms": timeout_ms,
        "error_kind": error_kind,
        "error_message": error_message,
        "raw_response_text": raw_response_text,
        "raw_model_output": raw_model_output,
        "finish_reason": finish_reason,
        "response_parse_error": response_parse_error,
        "payload_parse_error": payload_parse_error,
        "validation_error": validation_error,
        "phase_ms": {
            "prompt_build": prompt_build_ms,
            "http_request": http_request_ms,
            "generation": generation_ms,
            "parsing": parsing_ms,
            "validation": validation_ms,
            "total": int((time.perf_counter() - total_started) * 1000),
        },
        "messages": messages,
    }


def _build_docs_answer_messages(
    *,
    question: str,
    answer_type: str,
    snippets: list[DocsAnswerSnippet],
    focus: str | None,
    max_answer_chars: int,
    max_excerpt_chars: int,
    max_sources: int,
) -> list[dict[str, Any]]:
    system = (
        "Answer only from snippets. Return strict JSON object with keys: "
        "answer, source_indexes, insufficient_evidence. "
        f"answer <= {max_answer_chars} chars. "
        f"Use at most {max_sources} indexes. "
        "No markdown. No tool calls. No mutations. "
        "No transactions, params, YAML, save paths, or recipes. "
        "For comparison questions, compare both sides explicitly when evidence exists; "
        "otherwise set insufficient_evidence=true. "
        "For block definitions, describe purpose/role and avoid port or parameter counts."
    )
    lines = [f"Q: {question}", f"Answer type: {answer_type}"]
    if isinstance(focus, str) and focus.strip():
        lines.append(f"Focus: {focus.strip()}")
    lines.append("Snippets:")
    for index, snippet in enumerate(snippets):
        lines.append(
            f"[{index}] {snippet.title} | {snippet.source} | {snippet.excerpt}"
        )
    lines.append(
        "If evidence is weak, set insufficient_evidence=true and keep answer brief."
    )
    lines.append("Return JSON only.")
    payload_text = "\n".join(lines)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": payload_text},
    ]


def _docs_answer_json_schema_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "grc_docs_answer",
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "source_indexes": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                    },
                    "insufficient_evidence": {"type": "boolean"},
                },
                "required": ["answer", "source_indexes", "insufficient_evidence"],
                "additionalProperties": False,
            },
        },
    }


def _extract_assistant_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DocsAnswerAdvisorError("docs advisor response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise DocsAnswerAdvisorError("docs advisor choice must be object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise DocsAnswerAdvisorError("docs advisor message missing")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        combined = "".join(parts)
        if combined:
            return combined
    raise DocsAnswerAdvisorError("docs advisor response content missing")


def _extract_finish_reason(response: dict[str, Any]) -> str | None:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    finish_reason = first.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason.strip():
        return finish_reason.strip()
    return None


def _parse_payload(raw: str) -> dict[str, Any]:
    normalized = raw.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            normalized = "\n".join(lines[1:-1]).strip()
            if normalized.lower().startswith("json"):
                normalized = normalized[4:].strip()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start >= 0 and end > start:
            candidate = normalized[start : end + 1]
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError as inner_exc:
                try:
                    evaluated = ast.literal_eval(candidate)
                except Exception as literal_exc:
                    raise DocsAnswerAdvisorError("docs advisor output malformed JSON") from literal_exc
                if not isinstance(evaluated, dict):
                    raise DocsAnswerAdvisorError("docs advisor output must be object") from inner_exc
                payload = evaluated
        else:
            raise DocsAnswerAdvisorError("docs advisor output malformed JSON") from exc
    if not isinstance(payload, dict):
        raise DocsAnswerAdvisorError("docs advisor output must be object")
    return payload


def _validate_payload(
    payload: dict[str, Any],
    *,
    max_answer_chars: int,
    max_excerpt_chars: int,
    max_sources: int,
) -> dict[str, Any]:
    keys = set(payload)
    required = {"answer", "source_indexes", "insufficient_evidence"}
    unknown = keys - required
    missing = required - keys
    if unknown:
        raise DocsAnswerAdvisorError(
            f"docs advisor output contains unsupported keys: {sorted(unknown)}"
        )
    if missing:
        raise DocsAnswerAdvisorError(
            f"docs advisor output missing keys: {sorted(missing)}"
        )
    answer = payload.get("answer")
    if not isinstance(answer, str):
        raise DocsAnswerAdvisorError("docs advisor answer must be string")
    answer = " ".join(answer.split())
    if len(answer) > max_answer_chars:
        answer = answer[: max_answer_chars - 1].rstrip() + "…"
    source_indexes_raw = payload.get("source_indexes")
    if not isinstance(source_indexes_raw, list):
        raise DocsAnswerAdvisorError("docs advisor source_indexes must be array")
    source_indexes: list[int] = []
    for value in source_indexes_raw:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DocsAnswerAdvisorError(
                "docs advisor source_indexes must contain non-negative integers"
            )
        if value not in source_indexes:
            source_indexes.append(value)
        if len(source_indexes) >= max_sources:
            break
    insufficient_evidence = payload.get("insufficient_evidence")
    if not isinstance(insufficient_evidence, bool):
        raise DocsAnswerAdvisorError(
            "docs advisor insufficient_evidence must be boolean"
        )
    return {
        "answer": answer,
        "source_indexes": source_indexes,
        "insufficient_evidence": insufficient_evidence,
    }


__all__ = [
    "DocsAnswerAdvisorError",
    "DocsAnswerSnippet",
    "run_docs_answer_advisor_diagnostic",
    "run_docs_answer_advisor",
]
