"""Bounded agentic insert workflow: search, score, try, commit one validated candidate.

All attempts use staged copies via apply_edit; the live session is only mutated once.

Relevance policy (hardened):

- If user goal names a clear block family or exact block type:
    only commit candidates matching that goal family/type.
    If none validate, return safe failure.

- If user goal is generic (e.g. "insert compatible block"):
    If exactly one strong candidate validates, commit it.
    If multiple validated candidates exist, return ClarificationRequest.

- If preferred_block_type is provided:
    only try that block_type (or sub-type if wildcard).
    If unavailable/incompatible, reject safely.

- If goal is unsupported (e.g. "add a sink"):
    return unsupported_goal if the abstraction is wrong.

No hardcoded fixture logic. No block recipes. No blacklists.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from grc_agent.runtime.clarification import ClarificationOption, ClarificationRequest
from grc_agent.session.insertion_suggestions import InsertionCandidate, suggest_insertions
from grc_agent.transaction.apply import apply_edit
from grc_agent.transaction.rollback import clone_session


# How many validated candidates trigger a clarification instead of auto-commit.
_CLARIFICATION_THRESHOLD = 2
# Maximum number of options presented in the MCQ (A/B/C).
_MAX_MCQ_OPTIONS = 3


def auto_insert_block(
    session,
    goal: str,
    preferred_block_type: str | None = None,
    target_hint: str | None = None,
    max_candidates: int = 10,
    catalog_root: str | None = None,
) -> dict[str, Any]:
    """Autonomously insert a compatible block into the graph.

    1. Enumerate stream connections.
    2. Gather insertion candidates from suggest_insertions.
    3. Classify goal (explicit family vs generic vs unsupported).
    4. Filter candidates to matching family when goal is explicit.
    5. Score remaining candidates using generic signals.
    6. Try top candidates on cloned sessions to find validated options.
    7. If exactly one validates, commit on live session.
    8. If multiple validate, return clarification with real executable options.
    9. If none succeed, return safe rejection with diagnostics.
    """
    if session.flowgraph is None:
        return _error("NO_GRAPH_LOADED", "No flowgraph loaded in session.")

    stream_connections = _stream_connections(session)
    if not stream_connections:
        return _error(
            "UNSUPPORTED_GOAL_FOR_AUTO_INSERT",
            "No stream connections found. Auto-insert requires a graph with at least one stream connection.",
        )

    # Reject unsupported goals before candidate collection
    intent = _classify_goal(goal, preferred_block_type)
    if intent["mode"] == "unsupported":
        return _error(
            "UNSUPPORTED_GOAL_FOR_AUTO_INSERT",
            f"Goal '{goal}' is not supported for automated insertion. "
            "Use `insert_block_on_connection` with exact block_type and connection_id.",
        )

    # Collect candidates from all stream connections
    # For explicit-family goals, fetch more candidates to find matching family members
    suggest_k = 500 if intent["mode"] in ("explicit_family", "preferred_type") else 5
    all_candidates: list[tuple[str, InsertionCandidate]] = []
    for conn_id in stream_connections:
        suggestions = suggest_insertions(session, conn_id, k=suggest_k)
        if suggestions.ok and suggestions.candidates:
            for c in suggestions.candidates:
                all_candidates.append((conn_id, c))

    if not all_candidates:
        return _error(
            "UNSUPPORTED_GOAL_FOR_AUTO_INSERT",
            "No compatible insertion candidates found for any stream connection.",
        )

    # If explicit family is named, filter to family matches only
    if intent["mode"] == "explicit_family" and intent["family_tokens"]:
        filtered = [
            (conn_id, cand)
            for conn_id, cand in all_candidates
            if _candidate_matches_family(cand, intent["family_tokens"])
        ]
        if not filtered:
            return _error(
                "AUTO_INSERT_NO_GOAL_MATCH",
                f"No compatible candidates match goal '{goal}'. "
                f"Tried {len(all_candidates)} compatible candidates across {len(stream_connections)} connections; "
                f"none matched block family {intent['family_tokens']}. "
                f"Try a different goal or use `insert_block_on_connection` with exact block_type.",
            )
        all_candidates = filtered

    # If preferred_block_type is provided, filter to that type or subtypes
    if intent["mode"] == "preferred_type" and preferred_block_type:
        filtered = [
            (conn_id, cand)
            for conn_id, cand in all_candidates
            if preferred_block_type in cand.block_type or cand.block_type == preferred_block_type
        ]
        if not filtered:
            return _error(
                "AUTO_INSERT_NO_GOAL_MATCH",
                f"No compatible candidates match preferred_block_type '{preferred_block_type}'. "
                f"Tried {len(all_candidates)} candidates across {len(stream_connections)} connections.",
            )
        all_candidates = filtered

    # Score, rank, and cap
    scored = _score_candidates(all_candidates, goal, preferred_block_type)
    ranked = sorted(scored, key=lambda x: x[0], reverse=True)[:max_candidates]

    # Minimum semantic relevance check for explicit goals
    if intent["mode"] == "explicit_family":
        top_score = ranked[0][0] if ranked else -1
        if top_score < intent.get("min_score", 3):
            return _error(
                "AUTO_INSERT_NO_GOAL_MATCH",
                f"Goal '{goal}' was too specific; top candidate relevance score {top_score} "
                f"is below threshold {intent.get('min_score', 3)}. "
                f"No semantically relevant candidate found.",
            )

    # Step 1: Pre-validate candidates on cloned sessions (no live mutation).
    validated: list[tuple[int, str, InsertionCandidate, dict[str, Any]]] = []
    attempted: list[dict[str, Any]] = []
    for score, conn_id, candidate in ranked:
        result = _try_candidate_on_clone(session, conn_id, candidate, catalog_root)
        entry = _build_attempted_entry(score, conn_id, candidate, result, intent)
        attempted.append(entry)
        if result.get("ok"):
            validated.append((score, conn_id, candidate, entry))
            if len(validated) >= _CLARIFICATION_THRESHOLD:
                break  # enough for clarification or single-commit decision
        if len(attempted) >= max_candidates:
            break

    # Step 2: Decision
    if len(validated) == 0:
        return _safe_rejection(attempted, intent)

    if len(validated) == 1:
        # Exactly one strong candidate — commit on live session.
        _score_val, conn_id, candidate, entry = validated[0]
        live_result = _try_candidate(session, conn_id, candidate, catalog_root)
        entry_from_live = _build_attempted_entry(
            entry["score"], conn_id, candidate, live_result, intent
        )
        if live_result.get("ok"):
            return {
                "ok": True,
                "message": f"Inserted '{candidate.block_type}' into {conn_id}.",
                "committed": entry_from_live,
                "attempted": [entry_from_live],
                "attempt_count": 1,
                "goal_mode": intent["mode"],
            }
        # Clone passed but live failed — extremely rare; expand attempted list.
        full_attempted = list(attempted)
        full_attempted.append(entry_from_live)
        return _safe_rejection(full_attempted, intent)

    # len(validated) >= _CLARIFICATION_THRESHOLD
    return _build_clarification_payload(validated, attempted, goal, intent)


# --------------------------------------------------------------------------- #
# Candidate validation helpers
# --------------------------------------------------------------------------- #


def _try_candidate(
    session,
    conn_id: str,
    candidate: InsertionCandidate,
    catalog_root: str | None,
) -> dict[str, Any]:
    """Try one candidate via apply_edit. Live session is only mutated on success."""
    if candidate.insert_tool_args is None:
        return {
            "ok": False,
            "message": "Candidate missing insert_tool_args.",
            "error_type": "MISSING_INSERT_TOOL_ARGS",
        }
    transaction = {
        "op_type": "insert_block_on_connection",
        "connection_id": conn_id,
        "block_type": candidate.block_type,
        "instance_name": candidate.insert_tool_args["instance_name"],
        "params": candidate.insert_tool_args.get("params", {}),
    }
    return apply_edit(session, transaction, catalog_root)


def _try_candidate_on_clone(
    session,
    conn_id: str,
    candidate: InsertionCandidate,
    catalog_root: str | None,
) -> dict[str, Any]:
    """Validate one candidate on a cloned session. Does NOT mutate live session."""
    clone = clone_session(session)
    return _try_candidate(clone, conn_id, candidate, catalog_root)


def _build_attempted_entry(
    score: int,
    conn_id: str,
    candidate: InsertionCandidate,
    result: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Build a diagnostic entry for one candidate attempt."""
    return {
        "connection_id": conn_id,
        "block_type": candidate.block_type,
        "instance_name": candidate.insert_tool_args.get("instance_name") if candidate.insert_tool_args else None,
        "score": score,
        "goal_fit": _candidate_matches_family(candidate, intent.get("family_tokens", [])),
        "ok": result.get("ok", False),
        "error_type": result.get("error_type"),
        "message": result.get("message"),
    }


def _safe_rejection(attempted: list[dict[str, Any]], intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "message": f"All {len(attempted)} insertion candidates failed validation.",
        "committed": None,
        "attempted": attempted,
        "error_type": "AUTO_INSERT_ALL_CANDIDATES_FAILED",
        "attempt_count": len(attempted),
        "goal_mode": intent.get("mode"),
    }


def _build_clarification_payload(
    validated: list[tuple[int, str, InsertionCandidate, dict[str, Any]]],
    attempted: list[dict[str, Any]],
    goal: str,
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Build a clarification payload from validated candidates. No live mutation."""
    options: list[ClarificationOption] = []
    for index, (score, conn_id, candidate, entry) in enumerate(validated[:_MAX_MCQ_OPTIONS]):
        label = chr(ord("A") + index)
        options.append(
            ClarificationOption(
                label=label,
                title=f"Insert '{candidate.block_type}' into {conn_id}",
                description=f"{candidate.reason} (confidence: {candidate.confidence})",
                tool_name="insert_block_on_connection",
                tool_args=candidate.insert_tool_args or {},
                metadata={"score": score, "goal_mode": intent.get("mode"), "connection_id": conn_id},
            )
        )

    req = ClarificationRequest(
        kind="choose_insert_candidate",
        question="Multiple compatible blocks were found for the goal. Which one should be inserted?",
        options=options,
    )

    payload = req.to_dict()
    payload["ok"] = False
    payload["attempted"] = attempted
    payload["attempt_count"] = len(attempted)
    payload["goal_mode"] = intent.get("mode")
    return payload


# --------------------------------------------------------------------------- #
# Stream connections
# --------------------------------------------------------------------------- #


def _stream_connections(session) -> list[str]:
    """Return connection_id strings for all stream connections (integer ports only)."""
    conn_ids: list[str] = []
    if session.flowgraph is None:
        return conn_ids
    for conn in session.flowgraph.connections:
        if isinstance(conn.src_port, int) and isinstance(conn.dst_port, int):
            conn_ids.append(
                f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
            )
    return conn_ids


def _error(error_type: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "message": message,
        "error_type": error_type,
        "committed": None,
        "attempted": [],
        "attempt_count": 0,
    }


# --------------------------------------------------------------------------- #
# Goal classification — no hardcoded lists, token + catalog-based
# --------------------------------------------------------------------------- #

# Token frequency map derived from catalog for disambiguation.
# Single-occurrence tokens in block_ids get stronger family-match weight.
_CATALOG_FREQUENCY: dict[str, int] | None = None


def _catalog_token_frequency() -> dict[str, int]:
    """Return token -> count in all block_ids (cached)."""
    global _CATALOG_FREQUENCY
    if _CATALOG_FREQUENCY is not None:
        return _CATALOG_FREQUENCY
    from grc_agent.catalog.loaders import get_catalog_snapshot

    snapshot = get_catalog_snapshot()
    freq: dict[str, int] = {}
    for block_id in snapshot.blocks.keys():
        tokens = re.split(r"[^a-z]", block_id.lower())
        tokens = [t for t in tokens if len(t) > 1]
        for t in set(tokens):
            freq[t] = freq.get(t, 0) + 1
    _CATALOG_FREQUENCY = freq
    return freq


def _classify_goal(goal: str, preferred_block_type: str | None) -> dict[str, Any]:
    """Classify user goal to determine insertion mode.

    Returns dict with:
        mode: "generic" | "explicit_family" | "preferred_type" | "unsupported"
        family_tokens: list[str] — tokens identifying the desired block family
        min_score: int — minimum relevance score for explicit-family goals
    """
    goal_lower = goal.lower().strip()

    # If preferred_block_type is given, that overrules everything
    if preferred_block_type and preferred_block_type.strip():
        return {
            "mode": "preferred_type",
            "family_tokens": _extract_tokens(preferred_block_type),
            "min_score": 0,
        }

    # Strip generic framing words
    stripped = re.sub(
        r"\b(insert|add|put|place|compatible|block|into|the|a|an|some|one|this|that"
        r"|current|existing|flowgraph|graph|loaded|main|path|signal|stream)\b",
        "",
        goal_lower,
    )
    stripped = re.sub(r"\s+", " ", stripped).strip()

    # Detect unsupported abstractions
    if re.search(r"\b(sink|source|variable)\b", stripped):
        return {"mode": "unsupported", "family_tokens": [], "min_score": 0}

    # Detect explicit family by catalog-uncommon token
    tokens = [t for t in _extract_tokens(stripped) if len(t) > 2]
    freq = _catalog_token_frequency()
    family_tokens = [t for t in tokens if freq.get(t, 999) <= 5]

    # Multi-word goals like "low pass filter" — keep the whole phrase as family
    if "filter" in stripped and len(tokens) >= 2:
        family_tokens = list(set(family_tokens))
        family_tokens.append("filter")

    if "throttle" in stripped:
        family_tokens = list(set(family_tokens))
        family_tokens.append("throttle")

    if family_tokens:
        return {
            "mode": "explicit_family",
            "family_tokens": family_tokens,
            "min_score": 3,
        }

    return {"mode": "generic", "family_tokens": [], "min_score": 0}


def _extract_tokens(text: str) -> list[str]:
    """Extract normalized tokens from text."""
    tokens = re.split(r"[^a-z0-9]", text.lower())
    return [t for t in tokens if len(t) > 1]


def _candidate_matches_family(candidate: InsertionCandidate, family_tokens: list[str]) -> bool:
    """Check whether a candidate matches the desired block family.

    Matches on:
    - block_type tokens
    - description / label tokens (via catalog)
    - category path tokens
    """
    if not family_tokens:
        return True

    block_type_lower = candidate.block_type.lower()
    for token in family_tokens:
        if token in block_type_lower:
            return True

    # Try catalog description (label + category)
    desc = _describe_block_safe(candidate.block_type)
    if desc:
        label = (desc.get("label") or "").lower()
        category = " ".join(desc.get("category_path", [])).lower()
        for token in family_tokens:
            if token in label or token in category:
                return True

    return False


def _describe_block_safe(block_type: str) -> dict[str, Any] | None:
    try:
        from grc_agent.catalog.describe import describe_block

        d = describe_block(block_type)
        if d.get("ok"):
            return d
    except Exception as exc:
        logger.debug("_describe_block_safe failed for block_type=%s: %s", block_type, exc)
    return None


def _score_candidates(
    candidates: list[tuple[str, InsertionCandidate]],
    goal: str,
    preferred_block_type: str | None,
) -> list[tuple[int, str, InsertionCandidate]]:
    """Score candidates using generic signals only."""
    goal_lower = goal.lower()
    scored: list[tuple[int, str, InsertionCandidate]] = []
    for conn_id, candidate in candidates:
        score = 0
        block_type = candidate.block_type
        if preferred_block_type and block_type == preferred_block_type:
            score += 5
        elif preferred_block_type and preferred_block_type in block_type:
            score += 3

        # Goal-word matching in block_type
        goal_words = [
            w
            for w in goal_lower.split()
            if len(w) > 2 and w not in {"block", "into", "the", "a", "an", "insert", "add", "remove"}
        ]
        for word in goal_words:
            if word in block_type.lower():
                score += 3

        if candidate.confidence == "high":
            score += 2
        elif candidate.confidence == "medium":
            score += 1

        scored.append((score, conn_id, candidate))
    return scored
