import asyncio
import os

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.step_persistence import StepPersistence

from grc_agent.agent_factory import _build_compaction_capability, make_summarizing_strategy
from grc_agent.db import get_step_store


def test_compaction_under_budget_preserves_exact_history():
    """When message history is well within target_tokens, TieredCompaction
    leaves all messages and parts untouched, ensuring 100% KV cache hit rate."""
    compaction = _build_compaction_capability(
        {"provider": "ollama_local", "ollama_base_url": "http://localhost:11434"}
    )

    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="Add a throttle block")]),
        ModelResponse(
            parts=[
                ThinkingPart(content="I should check the active flowgraph first."),
                ToolCallPart(
                    tool_name="inspect_graph", args={"detail": "all"}, tool_call_id="call_1"
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="inspect_graph",
                    content='{"blocks": ["samp_rate"]}',
                    tool_call_id="call_1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="I see the flowgraph.")]),
    ]

    agent = Agent(TestModel(), capabilities=[compaction])

    async def _run():
        result = await agent.run("Now wire it", message_history=history)
        all_msgs = result.all_messages()
        # Verify tool return in history was NOT cleared
        ret_part = next(
            p
            for m in all_msgs
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_call_id == "call_1"
        )
        assert ret_part.content == '{"blocks": ["samp_rate"]}'

    asyncio.run(_run())


def test_compaction_over_budget_evicts_old_tool_returns_keeps_last_n():
    """When token budget is exceeded, Tier 1 (ClearToolResults) clears older tool
    return contents to placeholder while preserving the most recent 3 pairs
    intact. Payloads must exceed min_clear_tokens (2000 tokens ~ 8000 chars) —
    small results (query_knowledge answers) are never worth reclaiming, and the
    model must not lose the answer to its own recent question mid-turn (the
    session-14 40-request loop regression)."""
    # Set a small target_tokens so compaction triggers
    cfg = {"provider": "ollama_local", "ollama_base_url": "http://localhost:11434"}
    os.environ["GRC_COMPACTION_TARGET_TOKENS"] = "1500"
    try:
        compaction = _build_compaction_capability(cfg)
    finally:
        os.environ.pop("GRC_COMPACTION_TARGET_TOKENS", None)

    # 4 turns of massive inspect_graph payloads (20,000 chars ~ 5k tokens each)
    history: list[ModelMessage] = [
        # Turn 1
        ModelRequest(parts=[UserPromptPart(content="Turn 1: inspect")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_t1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="inspect_graph", content="A" * 20000, tool_call_id="call_t1"
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Turn 1 done.")]),
        # Turn 2
        ModelRequest(parts=[UserPromptPart(content="Turn 2: inspect")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_t2")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="inspect_graph", content="B" * 20000, tool_call_id="call_t2"
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Turn 2 done.")]),
        # Turn 3
        ModelRequest(parts=[UserPromptPart(content="Turn 3: inspect")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_t3")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="inspect_graph", content="C" * 20000, tool_call_id="call_t3"
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Turn 3 done.")]),
        # Turn 4
        ModelRequest(parts=[UserPromptPart(content="Turn 4: inspect")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_t4")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="inspect_graph", content="D" * 20000, tool_call_id="call_t4"
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Turn 4 done.")]),
    ]

    agent = Agent(TestModel(), capabilities=[compaction])

    async def _run():
        result = await agent.run("Turn 4: do something", message_history=history)
        all_msgs = result.all_messages()

        returns = {
            p.tool_call_id: p.content
            for m in all_msgs
            for p in m.parts
            if isinstance(p, ToolReturnPart)
        }

        # Turn 1's tool return (oldest) should be cleared to placeholder
        assert "[Flowgraph tool output cleared to conserve context" in returns["call_t1"]

        # The last 3 tool returns (Turns 2-4) must be preserved in full
        assert returns["call_t2"] == "B" * 20000
        assert returns["call_t3"] == "C" * 20000
        assert returns["call_t4"] == "D" * 20000

    asyncio.run(_run())


def test_sliding_window_preserves_first_user_prompt():
    """When history is exceptionally long, Tier 2 (SlidingWindowCompaction)
    preserves the original user prompt (preserve_first_user_message=True)."""
    cfg = {"provider": "ollama_local", "ollama_base_url": "http://localhost:11434"}
    os.environ["GRC_COMPACTION_TARGET_TOKENS"] = "200"
    try:
        compaction = _build_compaction_capability(cfg)
    finally:
        os.environ.pop("GRC_COMPACTION_TARGET_TOKENS", None)

    # 15 conversation turns
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="INITIAL_USER_GOAL: Build an FM receiver")]),
        ModelResponse(parts=[TextPart(content="I will help you build an FM receiver.")]),
    ]
    for i in range(1, 15):
        history.append(ModelRequest(parts=[UserPromptPart(content=f"Step {i}: Add component {i}")]))
        history.append(ModelResponse(parts=[TextPart(content=f"Added component {i}.")]))

    agent = Agent(TestModel(), capabilities=[compaction])

    async def _run():
        result = await agent.run("Final step: generate python", message_history=history)
        all_msgs = result.all_messages()
        # Ensure the first user message is preserved
        first_user_part = next(
            p.content for m in all_msgs for p in m.parts if isinstance(p, UserPromptPart)
        )
        assert "INITIAL_USER_GOAL" in first_user_part

    asyncio.run(_run())


def test_compaction_target_pins_conservative_window_for_lan_openai_compatible_endpoint(monkeypatch):
    """Regression: an openai_compatible endpoint on a LAN IP (plain http://)
    must pin the conservative 32k local window (0.75 x 32k = 24k target), not
    resolve from the pricing registry — a registry entry describes the
    upstream spec, not this deployment's --ctx, so a 32k-window LAN model
    would otherwise never compact and overflow its context."""
    monkeypatch.delenv("GRC_COMPACTION_TARGET_TOKENS", raising=False)
    cap = _build_compaction_capability(
        {
            "provider": "openai_compatible",
            "openai_compatible_base_url": "http://192.168.1.5:8000/v1",
        }
    )
    assert cap.target_fraction == 0.75
    assert cap.context_window == 32_000
    assert cap.target_tokens is None


def test_compaction_target_resolves_real_window_for_https_endpoints(monkeypatch):
    """https endpoints (ollama.com, openrouter.ai, custom proxies) resolve the
    model's real window from the genai-prices registry per request; models the
    registry doesn't know fall back to 128k (0.75 x 128k = 96k — the old fixed
    cloud budget). The one uniform rule is the scheme, not the hostname."""
    monkeypatch.delenv("GRC_COMPACTION_TARGET_TOKENS", raising=False)
    for base_url in (
        "https://openrouter.ai/api/v1",
        "https://ollama.com/v1",
        "https://my-corp-proxy.example.com/v1",
    ):
        cap = _build_compaction_capability(
            {"provider": "openai_compatible", "openai_compatible_base_url": base_url}
        )
        assert cap.target_fraction == 0.75, f"{base_url} should be cloud"
        assert cap.context_window is None
        assert cap.fallback_context_window == 128_000, f"{base_url} should be cloud"


def test_compaction_target_pins_conservative_window_for_localhost_ollama(monkeypatch):
    monkeypatch.delenv("GRC_COMPACTION_TARGET_TOKENS", raising=False)
    cap = _build_compaction_capability(
        {"provider": "ollama_local", "ollama_base_url": "http://localhost:11434"}
    )
    assert cap.target_fraction == 0.75
    assert cap.context_window == 32_000
    assert cap.target_tokens is None


def test_compaction_clamp_tier_guards_the_window(monkeypatch):
    """Tier 0 must be ClampOversizedMessages, the only strategy that can
    reach a runaway NEWEST part; its threshold mirrors the window pins
    (half the assumed window: 16k local / 64k cloud), so one part can never
    alone overflow the context."""
    from pydantic_ai_harness.compaction import ClampOversizedMessages

    monkeypatch.delenv("GRC_COMPACTION_TARGET_TOKENS", raising=False)
    local = _build_compaction_capability(
        {"provider": "ollama_local", "ollama_base_url": "http://localhost:11434"}
    )
    cloud = _build_compaction_capability(
        {
            "provider": "openai_compatible",
            "openai_compatible_base_url": "https://openrouter.ai/api/v1",
        }
    )
    for cap in (local, cloud):
        assert isinstance(cap.tiers[0], ClampOversizedMessages), type(cap.tiers[0])
    assert local.tiers[0].max_part_tokens == 16_000
    assert cloud.tiers[0].max_part_tokens == 64_000


def test_compaction_window_override_for_documented_registry_errors(monkeypatch):
    """genai-prices records claude-sonnet-4-5 as 1,000,000 vs its real
    200,000 — an over-recorded window would never compact before the
    provider rejects the request. The docs prescribe an explicit
    context_window override; the map must apply it (substring match covers
    prefixed ids like OpenRouter's 'anthropic/claude-sonnet-4-5')."""
    monkeypatch.delenv("GRC_COMPACTION_TARGET_TOKENS", raising=False)
    cap = _build_compaction_capability(
        {
            "provider": "openai_compatible",
            "model": "anthropic/claude-sonnet-4-5",
            "openai_compatible_base_url": "https://openrouter.ai/api/v1",
        }
    )
    assert cap.context_window == 200_000
    assert cap.target_fraction == 0.75
    # An unknown cloud model still gets the 128k fallback.
    cap2 = _build_compaction_capability(
        {
            "provider": "openai_compatible",
            "model": "some/unknown-model",
            "openai_compatible_base_url": "https://openrouter.ai/api/v1",
        }
    )
    assert cap2.context_window is None
    assert cap2.fallback_context_window == 128_000


def test_chat_sidebar_renders_compacted_messages_cleanly():
    """ChatSidebar._render_last_message_rich must cleanly render messages
    with cleared tool return placeholders without crashing or raising GTK errors."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # Simulate message history containing a cleared tool result
    sidebar._message_history = [
        ModelRequest(parts=[UserPromptPart(content="Inspect flowgraph")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="inspect_graph", args={"detail": "all"}, tool_call_id="call_c1"
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="inspect_graph",
                    content="[Flowgraph tool output cleared to conserve context \u2014 call the tool again if you still need this data]",
                    tool_call_id="call_c1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Here is the flowgraph overview.")]),
    ]

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    # Render the ModelResponse containing the tool call
    sidebar._render_last_message_rich(box, sidebar._message_history[1])

    # Ensure the expander was created and placeholder text was populated
    children = box.get_children()
    assert len(children) >= 1
    expander = children[0]
    assert isinstance(expander, Gtk.Expander)
    assert "inspect_graph" in expander.get_label()


def test_summarizing_tier_fires_over_budget_and_replaces_old_turns(monkeypatch):
    """The summarizing tier (ResilientSummarizingCompaction, D1 model
    inheritance) replaces turns older than keep_messages with a summary
    SystemPromptPart when the cheap tiers cannot fit the history under
    target. Uses the GRC_COMPACTION_TARGET_TOKENS escape hatch so the
    TestModel's window resolution is irrelevant."""

    from pydantic_ai_harness.compaction._summarizing_compaction import (
        _KEPT_USER_MESSAGE_METADATA,
        _SUMMARY_PREFIX,
    )

    cfg = {"provider": "ollama_local", "ollama_base_url": "http://localhost:11434"}
    os.environ["GRC_COMPACTION_TARGET_TOKENS"] = "1500"
    try:
        compaction = _build_compaction_capability(cfg)
    finally:
        os.environ.pop("GRC_COMPACTION_TARGET_TOKENS", None)

    # ~300 chars per message so the 4-char/token estimate clears the target
    # (the tier's own trigger is bypassed; TieredCompaction re-measures after
    # each tier). The target must also fit the POST-summary history — the
    # keep_user_messages retention copies plus the summary — or the final
    # SlidingWindow backstop would drop the summary again.
    def _turn(i: int) -> list[ModelMessage]:
        body = f"Turn {i}: " + ("x" * 220)
        return [
            ModelRequest(parts=[UserPromptPart(content=body)]),
            ModelResponse(parts=[TextPart(content=f"Turn {i} done.")]),
        ]

    history: list[ModelMessage] = _turn(1)
    # 25 more turns — far beyond keep_messages=20, so find_safe_cutoff cuts.
    for i in range(2, 27):
        history.extend(_turn(i))

    agent = Agent(TestModel(), capabilities=[compaction])

    async def _run():
        result = await agent.run("Final turn", message_history=history)
        all_msgs = result.all_messages()

        # The summary message exists and carries the harness prefix.
        summaries = [
            p.content
            for m in all_msgs
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, SystemPromptPart) and p.content.startswith(_SUMMARY_PREFIX)
        ]
        assert summaries, "summarizing tier never fired"
        assert all(s for s in summaries), "summary must be non-empty"

        # The turn-1 MODEL RESPONSE is gone from the live context (the user
        # prompt itself survives by design as a D3 retention copy).
        live_text = " ".join(
            str(p.content)
            for m in all_msgs
            for p in m.parts
            if isinstance(p, (UserPromptPart, SystemPromptPart, TextPart))
        )
        assert "Turn 1 done." not in live_text

        # keep_user_messages (D3): retained copies carry the metadata key and
        # hold the user prompt text.
        kept = [
            m
            for m in all_msgs
            if isinstance(m, ModelRequest) and (m.metadata or {}).get(_KEPT_USER_MESSAGE_METADATA)
        ]
        assert kept, "keep_user_messages retention copies missing"
        assert all(isinstance(pp, UserPromptPart) for m in kept for pp in m.parts), (
            "retained copies must contain only user prompts"
        )

    asyncio.run(_run())


def test_summarizing_failure_degrades_keeps_history(monkeypatch):  # noqa: ARG001
    """D2: a summarization failure must never hard-fail the turn — the
    ResilientSummarizingCompaction returns the pre-compact history unchanged
    (the harness's own compact_now builds the throwaway RunContext)."""
    from pydantic_ai_harness.compaction._manual import compact_now

    from grc_agent.agent_factory import ResilientSummarizingCompaction

    strat = ResilientSummarizingCompaction(max_messages=1, keep_messages=2, keep_user_messages=True)

    async def _boom(*_a, **_k):
        raise RuntimeError("summarizer boom")

    monkeypatch.setattr(strat, "_summarize", _boom)

    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="u1")]),
        ModelResponse(parts=[TextPart(content="r1")]),
        ModelRequest(parts=[UserPromptPart(content="u2")]),
        ModelResponse(parts=[TextPart(content="r2")]),
        ModelRequest(parts=[UserPromptPart(content="u3")]),
        ModelResponse(parts=[TextPart(content="r3")]),
    ]

    out = asyncio.run(compact_now(strat, history, model=TestModel()))
    assert out is history, "failure must return the original list object"


def test_conversation_search_recalls_compacted_detail(tmp_path, monkeypatch):
    """D3 end-to-end on a real SqliteStepStore: after SummarizingCompaction
    drops an old turn, ConversationSearch (scope="conversation") recovers a
    unique phrase from the pre-compact snapshot."""

    from pydantic_ai_harness.compaction import ClearToolResults, TieredCompaction
    from pydantic_ai_harness.conversation_search import ConversationSearch, SnapshotHistorySource

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    store = get_step_store()
    compaction = TieredCompaction(
        tiers=[
            ClearToolResults(max_tokens=1, keep_pairs=3),
            make_summarizing_strategy().__class__(  # same params as production
                max_messages=1, keep_messages=2, keep_user_messages=True
            ),
        ],
        target_tokens=400,
    )
    # TestModel(call_tools=[...]) calls the search tool on every step, which
    # is exactly what we need: the turn-1 tool boundary lands the pre-compact
    # snapshot (StepPersistence saves only at settled tool boundaries), and
    # turn 3's call searches the union.
    agent = Agent(
        TestModel(call_tools=["search_conversation_history"]),
        capabilities=[
            StepPersistence(store=store, agent_name="grc_chat"),
            ConversationSearch(SnapshotHistorySource(store), scope="conversation"),
            compaction,
        ],
    )

    async def _run():
        conv = "session-1"
        r1 = await agent.run(
            "Remember the phrase MAGIC_721 and inspect the graph.",
            message_history=[],
            deps=None,
            conversation_id=conv,
        )
        h1 = r1.all_messages()
        r2 = await agent.run(
            "Continue; set the throttle to 0.005.", message_history=h1, conversation_id=conv
        )
        # Turn 3 must be a FRESH run: TestModel calls its configured tools
        # only when the message history contains no ModelResponse yet
        # (pydantic_ai/models/test.py:_request), so a history-carrying turn
        # would silently skip the search tool.
        r3 = await agent.run(
            "Call search_conversation_history for MAGIC_721 and report the phrase.",
            message_history=[],
            deps=None,
            conversation_id=conv,
        )
        returns = [
            p.content
            for m in r3.all_messages()
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_name == "search_conversation_history"
        ]
        assert returns, "search_conversation_history was never called"
        assert "MAGIC_721" in str(returns[0]), "snapshot recall missed the phrase"

        # scope="conversation" fails closed without a conversation id.
        r4 = await agent.run(
            "Call back to search_conversation_history for MAGIC_721.",
            message_history=[],
            deps=None,
        )
        h4 = r4.all_messages()
        fails = [
            p.content
            for m in h4
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_name == "search_conversation_history"
        ]
        assert fails and "conversation" in str(fails[0]).lower(), "scope must fail closed"

    asyncio.run(_run())


def test_unbounded_snapshots_keep_all_boundaries():
    """D3: max_snapshots_per_run=None must retain every settled snapshot, so
    a pre-compaction snapshot always survives for ConversationSearch."""
    from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord

    store = get_step_store()
    rid = "grc_chat-test-unbounded"
    asyncio.run(
        store.register_run(
            RunRecord(run_id=rid, conversation_id="session-u", agent_name="grc_chat")
        )
    )
    for i in range(5):
        asyncio.run(
            store.save_snapshot(
                ContinuableSnapshot(
                    run_id=rid,
                    step_index=i,
                    messages=[ModelRequest(parts=[UserPromptPart(content=f"s{i}")])],
                    conversation_id="session-u",
                    agent_name="grc_chat",
                )
            )
        )
    snaps = asyncio.run(store.list_snapshots(run_id=rid))
    assert len(snaps) >= 5, f"expected >=5 snapshots, got {len(snaps)}"
