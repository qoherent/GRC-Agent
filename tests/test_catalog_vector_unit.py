"""Deterministic unit tests for the catalog embed-text helpers.

No Ollama / no live embedding model required. These guard the noise-param
filter and the 256-word cap that fix vector-search pollution for GUI blocks
like ``qtgui_time_sink_x`` (84 params, dominated by ``alpha1..10``,
``color1..10``, ``label1..10``).
"""
from __future__ import annotations

from grc_agent.runtime.catalog_vector import (
    _filter_noise_params,
    compose_block_embed_text,
)


def test_filter_drops_3plus_numeric_suffix_groups():
    assert _filter_noise_params(["alpha1", "alpha2", "alpha3", "type", "name"]) == [
        "type",
        "name",
    ]


def test_filter_keeps_pairs_of_2():
    assert _filter_noise_params(["alpha1", "alpha2", "type"]) == [
        "alpha1",
        "alpha2",
        "type",
    ]


def test_filter_keeps_unique_numbered():
    assert sorted(_filter_noise_params(["gain1", "freq1", "samp_rate1"])) == [
        "freq1",
        "gain1",
        "samp_rate1",
    ]


def test_filter_handles_underscore_prefix():
    assert _filter_noise_params(["_alpha1", "_alpha2", "_alpha3", "type"]) == [
        "type",
    ]


def test_compose_drops_noise_params():
    text = compose_block_embed_text(
        block_id="qtgui_time_sink_x",
        label="QT GUI Time Sink",
        categories=("Core", "Instrumentation", "QT"),
        parameters=tuple(f"alpha{i}" for i in range(1, 11)) + ("type", "name"),
        ports=(),
        documentation="Time-domain signal visualization.",
    )
    assert "alpha" not in text
    assert "type" in text
    assert "name" in text


def test_compose_caps_at_256_words():
    long_doc = " ".join(f"word{i}" for i in range(500))
    text = compose_block_embed_text(
        block_id="x",
        label="X",
        categories=("C",),
        parameters=(),
        ports=(),
        documentation=long_doc,
    )
    # The cap is 256 words; allow a small slack for the prefix parts
    # (label, block_id, category) that share the budget. The key
    # invariant: body is well under the untruncated 506 words.
    body_only = text.split("[TRUNCATED")[0]
    word_count = len(body_only.split())
    assert word_count <= 270, f"body has {word_count} words, expected <= 270"
    assert word_count < 506, "truncation did not happen"
    assert "TRUNCATED" in text
    assert "was 506 words, kept 256" in text
