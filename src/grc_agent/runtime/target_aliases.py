"""Graph-local target alias resolution for tightly scoped mutation calls."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_SAMPLE_RATE_ALIASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sample rate", re.compile(r"\bsample\s+rate\b", re.IGNORECASE)),
    ("sample_rate", re.compile(r"\bsample_rate\b", re.IGNORECASE)),
    ("samp rate", re.compile(r"\bsamp\s+rate\b", re.IGNORECASE)),
)

_ALIAS_PARAM_KEYS = {"samp_rate", "sample_rate", "samp rate", "sample rate", "srate"}


@dataclass(frozen=True)
class TargetAliasResolution:
    """Resolved target fields plus telemetry or a safe clarification result."""

    instance_name: str | None
    param_key: str | None
    resolved_target_alias: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None


def resolve_set_param_target_alias(
    *,
    user_text: str,
    session: Any,
    operation_kind: str | None,
    instance_name: str | None,
    param_key: str | None,
    param_value: Any,
) -> TargetAliasResolution:
    """Resolve the initial sample-rate alias only when graph-local evidence is unique.

    Phase 7 deliberately supports one alias family: sample-rate wording may target
    the existing variable named ``samp_rate``. The resolver only fills target
    fields for ``set_param`` and never infers a value.
    """

    alias_text = _find_sample_rate_alias(user_text)
    if alias_text is None or operation_kind != "set_param":
        return TargetAliasResolution(instance_name=instance_name, param_key=param_key)

    telemetry_base = {
        "alias_text": alias_text,
        "resolved_to": None,
        "source": "graph_local_alias",
    }
    if param_value is None or (isinstance(param_value, str) and not param_value.strip()):
        return TargetAliasResolution(
            instance_name=instance_name,
            param_key=param_key,
            resolved_target_alias={
                **telemetry_base,
                "reason": "missing_explicit_value",
                "ambiguity_count": 0,
            },
            clarification=_clarification(
                "Sample-rate edits require an explicit value before mutation.",
                [
                    "Provide an explicit value, for example: change the sample rate to 48000.",
                ],
            ),
        )

    candidates = _sample_rate_variable_candidates(session)
    ambiguity_count = len(candidates)
    if ambiguity_count != 1 or candidates[0] != "samp_rate":
        reason = "ambiguous_sample_rate_variable" if ambiguity_count else "missing_samp_rate_variable"
        return TargetAliasResolution(
            instance_name=instance_name,
            param_key=param_key,
            resolved_target_alias={
                **telemetry_base,
                "reason": reason,
                "ambiguity_count": ambiguity_count,
            },
            clarification=_clarification(
                "Sample-rate target is not uniquely resolvable from the active graph.",
                [
                    "Provide exact instance_name=samp_rate and param_key=value if that is the intended target.",
                    "Or inspect/list variables and choose the exact variable to edit.",
                ],
            ),
        )

    instance_text = _clean_optional_text(instance_name)
    param_text = _clean_optional_text(param_key)
    if instance_text not in (None, "samp_rate"):
        return TargetAliasResolution(
            instance_name=instance_name,
            param_key=param_key,
            resolved_target_alias={
                **telemetry_base,
                "reason": "explicit_target_conflict",
                "ambiguity_count": ambiguity_count,
            },
            clarification=_clarification(
                "Sample-rate alias conflicts with the supplied instance_name.",
                [
                    "Retry with instance_name=samp_rate and param_key=value if the sample-rate variable is intended.",
                    "Or provide a different exact target without using sample-rate alias wording.",
                ],
            ),
        )
    if param_text is not None and param_text != "value" and param_text not in _ALIAS_PARAM_KEYS:
        return TargetAliasResolution(
            instance_name=instance_name,
            param_key=param_key,
            resolved_target_alias={
                **telemetry_base,
                "reason": "explicit_param_key_conflict",
                "ambiguity_count": ambiguity_count,
            },
            clarification=_clarification(
                "Sample-rate alias conflicts with the supplied param_key.",
                [
                    "Retry with param_key=value for the samp_rate variable if that is the intended target.",
                    "Or provide a different exact parameter target without alias wording.",
                ],
            ),
        )

    return TargetAliasResolution(
        instance_name="samp_rate",
        param_key="value",
        resolved_target_alias={
            **telemetry_base,
            "resolved_to": {"instance_name": "samp_rate", "param_key": "value"},
            "reason": "unique_samp_rate_variable",
            "ambiguity_count": ambiguity_count,
        },
    )


def _find_sample_rate_alias(user_text: str) -> str | None:
    if not isinstance(user_text, str):
        return None
    for alias, pattern in _SAMPLE_RATE_ALIASES:
        if pattern.search(user_text):
            return alias
    return None


def _sample_rate_variable_candidates(session: Any) -> list[str]:
    flowgraph = getattr(session, "flowgraph", None)
    blocks = getattr(flowgraph, "blocks", None)
    if not isinstance(blocks, list):
        return []
    candidates: list[str] = []
    for block in blocks:
        if getattr(block, "block_type", None) != "variable":
            continue
        instance_name = getattr(block, "instance_name", None)
        if not isinstance(instance_name, str):
            continue
        normalized = _normalize_target_text(instance_name)
        tokens = set(normalized.split("_"))
        if normalized == "samp_rate" or (
            "rate" in tokens and ("samp" in tokens or "sample" in tokens)
        ):
            candidates.append(instance_name)
    return sorted(candidates)


def _clean_optional_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_target_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _clarification(message: str, options: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": "clarification_required",
        "message": message,
        "clarification_options": options,
    }
