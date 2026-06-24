"""Phase 4 — Pydantic V2 domain model tests.

Validates the outbound (extra="forbid") contract, the stable JSON schema,
and the no-in-band-directive rule. Inbound models were deleted — the JSON
Schema (runtime/tool_schemas.py) is the real inbound contract.
"""

import re

import pytest
from grc_agent.domain_models import (
    BlockRole,
    GrcBlock,
    GrcFlowgraph,
    GrcValidation,
)
from pydantic import ValidationError


def test_grc_flowgraph_round_trip():
    fg = GrcFlowgraph(
        ok=True,
        graph_name="demo",
        blocks=[
            GrcBlock(
                instance_name="src",
                block_type="analog_sig_source_x",
                role=BlockRole.SOURCE,
                state="enabled",
                parameters={"freq": "350"},
            ),
        ],
        connections=[
            "src:0->dst:0",
        ],
    )
    dumped = fg.model_dump(exclude_none=True)
    assert set(dumped.keys()) == {
        "ok",
        "graph_name",
        "blocks",
        "connections",
        "validation",
        "errors",
    }
    assert dumped["blocks"][0]["role"] == "source"
    assert dumped["validation"]["status"] == "unknown"


def test_grc_block_extra_forbid():
    with pytest.raises(ValidationError):
        GrcBlock(
            instance_name="x",
            block_type="t",
            role=BlockRole.OTHER,
            state="enabled",
            foobar=42,
        )


def test_block_role_enum_values():
    assert BlockRole.SOURCE.value == "source"
    assert len(list(BlockRole)) == 9
    assert {m.value for m in BlockRole} == {
        "variable",
        "source",
        "sink",
        "transform",
        "virtual_or_pad",
        "import",
        "snippet",
        "options",
        "other",
    }


def test_grc_validation_default():
    v = GrcValidation()
    assert v.status == "unknown"
    assert v.errors == []
    assert v.native_ok is None


def test_model_json_schema_is_stable():
    schema = GrcFlowgraph.model_json_schema()
    assert set(schema["properties"].keys()) == {
        "ok",
        "graph_name",
        "blocks",
        "connections",
        "validation",
        "errors",
    }


def test_no_in_band_directives():
    forbidden = re.compile(r"^[A-Z_]{2,}$")
    forbidden_phrases = ("use this when", "call ", "retry")
    models = [GrcFlowgraph, GrcBlock, GrcValidation]
    for model in models:
        for field in model.model_fields.values():
            for phrase in forbidden_phrases:
                assert phrase not in str(field.description or "").lower()
            if isinstance(field.default, str):
                assert not forbidden.match(field.default), field.default
