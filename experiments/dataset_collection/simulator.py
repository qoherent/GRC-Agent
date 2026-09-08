"""User-simulator clients for the dataset-collection harness (plan U2, KTD6).

Two interchangeable implementations of one protocol:

- ``ScriptedSimulator`` — deterministic canned ``{message, stop}`` sequences.
  The dry run and every hermetic check run on this; no LLM spend.
- ``OllamaUserSimulator`` — the live persona-conditioned user on Ollama Cloud
  (``glm-5.3-flash``), structured JSON output, bounded retries, and
  ``SimulatorAborted`` as the terminal failure (plan I1: a mid-task simulator
  failure leaves a quarantine-verdicted task, never a hang).

Key resolution reuses the app's own ``resolve_key`` rule so the simulator
reads the campaign-isolated env (plan KTD3) exactly like the app does.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from .simulator_prompt import build_system_prompt, build_user_message
from .task_spec import Persona, TaskSpec

ENDPOINT = "https://ollama.com/v1/chat/completions"
DEFAULT_MODEL = "glm-5.3-flash:cloud"
_REQUEST_TIMEOUT = 120.0
_MAX_RETRIES = 2  # bounded retries, then simulator_aborted — never a hang
_REPAIR_RETRIES = 1  # one JSON repair attempt, then abort


class SimulatorAborted(Exception):
    """Terminal simulator failure — the task verdict becomes simulator_aborted."""


class ScriptExhausted(Exception):
    """Scripted simulator ran out of turns — the runner maps this to budget_exhausted."""


@dataclass
class SimulatorTurn:
    message: str
    stop: bool
    reason: str = ""
    raw: dict | None = None  # raw provider response for the manifest (R6)


class UserSimulator(Protocol):
    async def next_turn(self, agent_reply: str) -> SimulatorTurn: ...


# --- secret filtering (review S3: reply text egresses to Ollama Cloud) -------

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"(?i)(api_?key|token|secret|password)\s*[=:]\s*\S{8,}"),
    re.compile(r"[A-Fa-f0-9]{40,}"),
    re.compile(r"-----BEGIN [A-Z ]*KEY-----"),
)
_REDACTED = "[redacted]"


def filter_secrets(text: str) -> tuple[str, int]:
    """Redact secret-shaped strings from text before it egresses.

    Returns (filtered_text, redaction_count). One uniform rule applied to the
    whole text; counts are surfaced (never silently dropped) in the manifest.
    """
    count = 0

    def _sub(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return _REDACTED

    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_sub, text)
    return text, count


# --- repetition detection (anti-collapse, harness-side) -----------------------

_NORMALIZE = re.compile(r"[^a-z0-9 ]+")


def _normalized(text: str) -> str:
    return " ".join(_NORMALIZE.sub(" ", text.lower()).split())


@dataclass
class RepetitionDetector:
    threshold: int = 3
    _history: list[str] = field(default_factory=list, repr=False)

    def observe(self, message: str) -> bool:
        """Record a simulator message; True when near-identical messages have
        repeated `threshold` times consecutively (collapse signal for the
        manifest)."""
        self._history.append(_normalized(message))
        if len(self._history) < self.threshold:
            return False
        tail = self._history[-self.threshold :]
        return len(set(tail)) == 1


# --- scripted stub (hermetic dry runs) ---------------------------------------

@dataclass
class ScriptedSimulator:
    """Deterministic canned turns; exhausts into ScriptExhausted (budget path)."""

    turns: list[SimulatorTurn]

    def __post_init__(self) -> None:
        self._iter = iter(self.turns)

    async def next_turn(self, agent_reply: str) -> SimulatorTurn:  # noqa: ARG002 - protocol shape
        try:
            return next(self._iter)
        except StopIteration as e:
            raise ScriptExhausted from e


# --- live Ollama Cloud client --------------------------------------------------

def _resolve_model() -> str:
    import os

    from grc_agent.settings import get_env_value

    return get_env_value("OLLAMA_CLOUD_MODEL") or os.environ.get("OLLAMA_CLOUD_MODEL", DEFAULT_MODEL)


def _resolve_key() -> str:
    from grc_agent.settings import resolve_key

    key = resolve_key("OLLAMA_API_KEY") or resolve_key("OLLAMA_CLOUD_API_KEY")
    if not key:
        raise SimulatorAborted("Ollama Cloud API key not set (OLLAMA_API_KEY)")
    return key


def _parse_turn(payload: str) -> SimulatorTurn:
    """Parse the provider's JSON object; tolerate surrounding prose."""
    start = payload.find("{")
    end = payload.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    obj = json.loads(payload[start : end + 1])
    return SimulatorTurn(
        message=str(obj["message"]),
        stop=bool(obj["stop"]),
        reason=str(obj.get("reason", "")),
    )


@dataclass
class OllamaUserSimulator:
    """Persona-conditioned live user on Ollama Cloud (plan KTD6)."""

    persona: Persona
    task: TaskSpec
    _history: list[dict] = field(default_factory=list, repr=False)

    async def next_turn(self, agent_reply: str) -> SimulatorTurn:
        filtered_reply, redactions = filter_secrets(agent_reply)
        if not self._history:
            self._history.append({"role": "system", "content": build_system_prompt(self.persona, self.task)})
        self._history.append({"role": "user", "content": build_user_message(filtered_reply)})

        body = {
            "model": _resolve_model(),
            "messages": self._history,
            "stream": False,
            # Structured output: the provider enforces the JSON shape.
            "format": {"type": "object", "properties": {"message": {"type": "string"}, "stop": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["message", "stop"]},
        }
        content = await self._post(body)
        try:
            turn = _parse_turn(content)
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            # One repair retry (plan: invalid JSON gets one repair, then abort).
            repair = (
                "Your last reply was not valid JSON matching the contract. "
                f'Error: {e}. Reply again with ONLY the JSON object {{"message", "stop", "reason"}}.'
            )
            self._history.append({"role": "assistant", "content": content})
            self._history.append({"role": "user", "content": repair})
            content2 = await self._post(body | {"messages": self._history})
            try:
                turn = _parse_turn(content2)
            except (ValueError, json.JSONDecodeError, KeyError) as e2:
                raise SimulatorAborted(f"simulator JSON invalid after repair: {e2}") from e2

        self._history.append({"role": "assistant", "content": json.dumps({"message": turn.message, "stop": turn.stop, "reason": turn.reason})})
        turn.raw = {
            "model": body["model"],
            "redactions_in_agent_reply": redactions,
            "history_len": len(self._history),
        }
        return turn

    async def _post(self, body: dict) -> str:
        key = _resolve_key()
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                    resp = await client.post(
                        ENDPOINT, headers={"Authorization": f"Bearer {key}"}, json=body
                    )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError) as e:
                last_error = e
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(2**attempt)
        raise SimulatorAborted(f"simulator request failed after {_MAX_RETRIES + 1} attempts: {last_error}")


# --- hermetic self-test --------------------------------------------------------

def _selftest() -> int:
    """Stub-driven checks: prompt assembly, asymmetry, parsing, collapse, secrets."""
    from .task_spec import load_personas, load_tasks

    personas = load_personas()
    tasks = {t.id: t for t in load_tasks()}
    failures: list[str] = []

    task = tasks["t01_dial_tone"]
    persona = personas[task.persona_id]
    prompt = build_system_prompt(persona, task)

    if task.goal not in prompt or persona.occupation not in prompt:
        failures.append("prompt assembly missing goal/persona")
    for banned in ("tool_call", "ToolReturn", "inspect_graph", "<think"):
        if banned in prompt:
            failures.append(f"prompt leaks harness concept: {banned}")

    # Information asymmetry: agent replies carrying tool internals are filtered
    # for secrets and passed through unchanged otherwise — the simulator never
    # receives anything but reply text by construction (single-input protocol).
    dirty = "key is sk-abcdef0123456789abcdef0123456789 here"
    filtered, n = filter_secrets(dirty)
    if n != 1 or "sk-abcdef" in filtered:
        failures.append(f"secret filter failed: {filtered!r} ({n})")
    clean = "the graph runs fine now"
    if filter_secrets(clean) != (clean, 0):
        failures.append("secret filter over-matches clean text")

    det = RepetitionDetector(threshold=3)
    if det.observe("ok thanks") or det.observe("ok thanks"):
        failures.append("repetition detector fired early")
    if not det.observe("OK   thanks!!"):
        failures.append("repetition detector missed normalized repeat")

    payload = 'Sure! {"message": "nice, does it work now?", "stop": false, "reason": "checking"}'
    turn = _parse_turn(payload)
    if turn.stop or "work" not in turn.message:
        failures.append(f"JSON parse failed: {turn}")

    async def _stub_run() -> None:
        import inspect

        sim = ScriptedSimulator(
            turns=[SimulatorTurn("hi, can you help", False, "opening")]
        )
        t = await sim.next_turn("hello")
        if t.message != "hi, can you help":
            failures.append("scripted simulator returned wrong turn")
        try:
            await sim.next_turn("next")
            failures.append("scripted simulator did not exhaust")
        except ScriptExhausted:
            pass
        assert inspect.iscoroutinefunction(OllamaUserSimulator.next_turn)

    asyncio.run(_stub_run())

    if failures:
        print("SELFTEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("selftest OK: prompt assembly, asymmetry, secret filter, repetition, JSON parse, stub")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
