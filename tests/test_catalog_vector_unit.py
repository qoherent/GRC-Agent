"""Deterministic unit tests for the catalog embed-text helpers.

No Ollama / no live embedding model required. The param filter lives in
:mod:`grc_agent.runtime.param_filter` (Stage A visibility via GRC's native
``evaluated_param_hides`` + categories); the live tests need a working GRC
platform, the mock-based tests below don't.
"""

from __future__ import annotations

from unittest import mock

from grc_agent.runtime.catalog_vector import (
    compose_block_embed_text,
)
from grc_agent.runtime.param_filter import visible_param_keys

# --- visible_param_keys ---------------------------------------------------


def test_visible_params_drops_hide_all_keys():
    """When GRC evaluates a param with hide='all', it is dropped."""
    fake_eval = {
        "type": "none",
        "name": "none",
        "alpha1": "all",
        "alpha2": "all",
        "color1": "all",
    }
    with mock.patch(
        "grc_agent.runtime.param_filter.evaluated_param_hides",
        return_value=fake_eval,
    ):
        with mock.patch(
            "grc_agent.runtime.param_filter.categories",
            return_value={},
        ):
            result = visible_param_keys(
                "fake_block",
                ["type", "name", "alpha1", "alpha2", "color1"],
            )
    assert result == ["type", "name"]


def test_visible_params_keeps_hide_part_keys():
    """hide='part' (reduced-form visible) is KEPT, not dropped."""
    fake_eval = {
        "ylabel": "part",
        "type": "none",
    }
    with mock.patch(
        "grc_agent.runtime.param_filter.evaluated_param_hides",
        return_value=fake_eval,
    ):
        with mock.patch(
            "grc_agent.runtime.param_filter.categories",
            return_value={},
        ):
            result = visible_param_keys(
                "fake_block",
                ["ylabel", "type", "not_in_eval"],
            )
    # ylabel kept (part), type kept (none), not_in_eval kept (unknown)
    assert "ylabel" in result
    assert "type" in result
    assert "not_in_eval" in result


def test_visible_params_falls_back_when_grc_unavailable():
    """If GRC is unavailable, return the full list (no silent drop)."""
    with mock.patch(
        "grc_agent.runtime.param_filter.evaluated_param_hides",
        return_value={},
    ):
        result = visible_param_keys(
            "fake_block",
            ["a", "b", "c"],
        )
    assert result == ["a", "b", "c"]


# --- compose_block_embed_text ---------------------------------------------


def test_compose_includes_passed_params_verbatim():
    """compose is a pure composer — it does NOT filter on its own.

    Filtering is the caller's job (see ``visible_param_keys``). The
    compose function trusts the ``parameters`` argument.
    """
    text = compose_block_embed_text(
        block_id="x",
        label="X",
        categories=("C",),
        parameters=("alpha1", "type"),
        ports=(),
        documentation="d",
    )
    # Both params are in the body — no silent filter at compose time.
    assert "param: alpha1" in text
    assert "param: type" in text


def test_compose_caps_at_900_words():
    long_doc = " ".join(f"word{i}" for i in range(1500))
    text = compose_block_embed_text(
        block_id="x",
        label="X",
        categories=("C",),
        parameters=(),
        ports=(),
        documentation=long_doc,
    )
    # The cap is 900 words; allow a small slack for the prefix parts
    # (label, block_id, category) that share the budget.
    body_only = text.split("[TRUNCATED")[0]
    word_count = len(body_only.split())
    assert word_count <= 910, f"body has {word_count} words, expected <= 910"
    assert word_count < 1506, "truncation did not happen"
    assert "TRUNCATED" in text
    assert "was 1506 words, kept 900" in text


# --- integration with the real GRC platform -------------------------------


def test_visible_params_filters_real_qtgui_time_sink_x():
    """The native GRC filter removes the GUI-styling alpha/color/label
    grids from the time-sink block.

    This is the regression test for the "time sink" vector search miss
    (the actual block was outranked by digital_packet_sink because the
    embed text was dominated by 49 hide='all' GUI params).

    GRC evaluates ``hide`` against actual param values, so the first
    few alpha/color/label slots are ``hide='part'`` (visible in a
    reduced form) while extras beyond that are ``hide='all'``. The
    test asserts the high-N extras are filtered.
    """
    visible = visible_param_keys(
        "qtgui_time_sink_x",
        [
            "type",
            "name",
            "ylabel",
            "yunit",
            "size",
            "srate",
            "grid",
            "alpha1",
            "alpha2",
            "alpha3",
            "alpha4",
            "alpha5",
            "alpha6",
            "alpha7",
            "alpha8",
            "alpha9",
            "alpha10",
            "color1",
            "color2",
            "color3",
            "color4",
            "color5",
            "label1",
            "label2",
            "label3",
            "label4",
            "label5",
        ],
    )
    # GUI-styling params with hide='all' (the high-N extras) must be gone.
    for gui in (
        "alpha6",
        "alpha7",
        "alpha8",
        "alpha9",
        "alpha10",
        "color4",
        "color5",
        "label4",
        "label5",
    ):
        assert gui not in visible, f"{gui} should be filtered (hide=all)"
    # Semantically meaningful params must be kept.
    for kept in ("type", "name", "srate"):
        assert kept in visible, f"{kept} must be kept"


# --- Phase 2 regression guard: id drop, stream-domain drop, distance -------- #


def test_keep_param_drops_id_uniformly():
    """The 'id' param is the block's instance name — redundant with the
    instance_name field every payload already carries. keep_param must drop it
    in every mode (one uniform rule). GRC's own native dtype for this param
    is literally 'id' (see gnuradio.grc.core.params._build), which is what
    keep_param actually gates on."""
    from grc_agent.runtime.param_filter import DETAILS, OVERVIEW, keep_param

    for mode in (OVERVIEW, DETAILS):
        assert (
            keep_param(
                hide="none",
                category="General",
                dtype="id",
                value="samp_rate",
                default="samp_rate",
                mode=mode,
                param_key="id",
            )
            is False
        )
    # a non-id param with otherwise-kept attributes still survives
    assert (
        keep_param(
            hide="none",
            category="General",
            dtype="enum",
            value="float",
            default="complex",
            mode=OVERVIEW,
            param_key="type",
        )
        is True
    )


def test_block_description_payload_drops_id_keeps_domain():
    """The model-facing catalog payload drops the redundant 'id' param but
    KEEPS port domain (project policy: do not cut information from tool
    outputs). Never emits label/default_params."""
    from grc_agent.catalog.schema import (
        BlockDescription,
        NormalizedParameter,
        NormalizedPort,
    )
    from grc_agent.runtime.param_filter import OVERVIEW

    desc = BlockDescription(
        block_id="blocks_multiply_xx",
        label="Multiply",
        category_path=["Math Operators"],
        flags=[],
        parameters=[
            NormalizedParameter(id="id", dtype="id", default="blocks_multiply_xx"),
            NormalizedParameter(
                id="type",
                dtype="enum",
                default="complex",
                options=["complex", "float", "int", "short"],
            ),
            NormalizedParameter(id="vlen", dtype="int", default="1"),
        ],
        inputs=[
            NormalizedPort(port_id="0", domain="stream", dtype="complex"),
            NormalizedPort(port_id="msg", domain="message", dtype="message"),
        ],
        outputs=[NormalizedPort(port_id="0", domain="stream", dtype="complex")],
    )
    payload = desc.to_payload(mode=OVERVIEW)
    # id dropped (redundant with instance_name)
    assert "id" not in payload["params"]
    # meaningful params kept
    assert "type" in payload["params"]
    # no rich-shape fields
    assert "label" not in payload
    assert "default_params" not in payload
    # domain is preserved (stream AND message) — info is not cut
    stream_port = next(p for p in payload["inputs"] if p.get("port_id") == "0")
    assert stream_port.get("domain", "stream") == "stream"
    assert stream_port["dtype"] == "complex"
    msg_port = next(p for p in payload["inputs"] if p.get("port_id") == "msg")
    assert msg_port.get("domain") == "message"
