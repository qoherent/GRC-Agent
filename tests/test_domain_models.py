"""Phase 4 — Pydantic V2 domain model tests.

Validates the outbound (extra="forbid") / inbound (extra="ignore") contract,
the stable JSON schema, and the no-in-band-directive rule.
"""
import re

import pytest
from pydantic import ValidationError

from grc_agent.domain_models import (
    BlockRole,
    ChangeGraphArgs,
    ChangeGraphUpdateParams,
    GrcBlock,
    GrcConnection,
    GrcFlowgraph,
    GrcParameter,
    GrcValidation,
    InspectGraphArgs,
)


def test_grc_flowgraph_round_trip():
    fg = GrcFlowgraph(
        ok=True,
        graph_name="demo",
        blocks=[
            GrcBlock(instance_name="src", block_type="analog_sig_source_x",
                     block_uid="u1", role=BlockRole.SOURCE, state="enabled",
                     parameters=[GrcParameter(name="freq", dtype="real", value="350")]),
        ],
        connections=[
            GrcConnection(connection_id="src:0->dst:0", src_block="src", src_port="0",
                          dst_block="dst", dst_port="0"),
        ],
    )
    dumped = fg.model_dump(exclude_none=True)
    assert set(dumped.keys()) == {"ok", "graph_name", "blocks", "connections",
                                  "validation", "errors", "state_revision"}
    assert dumped["blocks"][0]["role"] == "source"
    assert dumped["validation"]["status"] == "unknown"


def test_grc_block_extra_forbid():
    with pytest.raises(ValidationError):
        GrcBlock(instance_name="x", block_type="t", block_uid="u", role=BlockRole.OTHER,
                 state="enabled", foobar=42)


def test_grc_parameter_evaluated_value_omitted_when_none():
    p = GrcParameter(name="freq", dtype="real", value="350")
    assert "evaluated_value" not in p.model_dump(exclude_none=True)


def test_block_role_enum_values():
    assert BlockRole.SOURCE.value == "source"
    assert len(list(BlockRole)) == 9
    assert {m.value for m in BlockRole} == {
        "variable", "source", "sink", "transform", "virtual_or_pad",
        "import", "snippet", "options", "other",
    }


def test_grc_validation_default():
    v = GrcValidation()
    assert v.status == "unknown"
    assert v.errors == []
    assert v.native_ok is None


def test_model_json_schema_is_stable():
    schema = GrcFlowgraph.model_json_schema()
    assert set(schema["properties"].keys()) == {
        "ok", "graph_name", "file_format", "grc_version", "blocks",
        "connections", "validation", "errors", "state_revision",
    }


def test_no_in_band_directives():
    forbidden = re.compile(r"^[A-Z_]{2,}$")
    forbidden_phrases = ("use this when", "call ", "retry")
    models = [GrcFlowgraph, GrcBlock, GrcParameter, GrcConnection, GrcValidation,
              InspectGraphArgs, ChangeGraphArgs]
    for model in models:
        for field in model.model_fields.values():
            # enum/string values are lowercase by construction; check any default str
            for phrase in forbidden_phrases:
                assert phrase not in str(field.description or "").lower()
            if isinstance(field.default, str):
                assert not forbidden.match(field.default), field.default


def test_inspect_graph_args_extra_ignored():
    args = InspectGraphArgs(view="overview", targets=["all"], verbose=True)  # type: ignore[call-arg]
    assert args.view == "overview"
    assert not hasattr(args, "verbose")


def test_change_graph_args_extra_ignored():
    args = ChangeGraphArgs(  # type: ignore[call-arg]
        update_params=[ChangeGraphUpdateParams(instance_name="samp_rate", params={"value": "48000"})],
        mystery_field="hello",
    )
    assert not hasattr(args, "mystery_field")
    assert args.update_params[0].instance_name == "samp_rate"


def test_change_graph_args_missing_required():
    args = ChangeGraphArgs()
    assert args.add_blocks == []
    assert args.force is False


def test_inspect_graph_args_default_view():
    args = InspectGraphArgs()
    assert args.view == "overview"
    assert args.targets == []
    assert args.params == []
    assert args.debug is False


def test_inbound_type_error_still_raised():
    # extra="ignore" drops unknown fields; type errors still validate.
    with pytest.raises(ValidationError):
        InspectGraphArgs(view=123)  # type: ignore[arg-type]


def test_query_knowledge_domain_default():
    from grc_agent.domain_models import QueryKnowledgeArgs
    args = QueryKnowledgeArgs(query="throttle")
    assert args.domain == "catalog"
    assert args.query == "throttle"
