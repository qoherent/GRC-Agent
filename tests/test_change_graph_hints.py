"""Tests for change_graph validation hints — the orphaned-port causal hint.

When a batch removes a block, any other block whose port was connected to it
is left dangling. The adapter traces removed-block -> orphaned-port causality
(deterministic offloading of the multi-step topology reasoning the 7.5B model
cannot do) and surfaces it as a per-error ``hint``.
"""

from __future__ import annotations

from grc_agent.runtime.change_graph import (
    _orphaned_port_hints,
    _validation_error_entries,
)


class TestOrphanedPortHints:
    def test_names_removed_block_for_a_source_whose_output_dangled(self):
        pre_edges = {
            "analog_noise_source_x_0:0->blocks_add_xx:2",
            "analog_sig_source_x_0:0->blocks_add_xx:0",
            "blocks_add_xx:0->audio_sink:0",
        }
        hints = _orphaned_port_hints(pre_edges, {"blocks_add_xx"})
        # The noise source's output was connected to the removed adder.
        assert "blocks_add_xx" in hints["analog_noise_source_x_0"]
        # The audio sink's input was fed by the removed adder.
        assert "blocks_add_xx" in hints["audio_sink"]

    def test_empty_when_no_block_removed(self):
        assert _orphaned_port_hints({"a:0->b:0"}, set()) == {}

    def test_edge_between_two_removed_blocks_is_not_an_orphan(self):
        assert _orphaned_port_hints({"a:0->b:0"}, {"a", "b"}) == {}


class TestValidationErrorEntries:
    def test_attaches_orphan_hint_to_the_matching_block_error(self):
        errs = ["analog_noise_source_x_0: Source - out(0): Port is not connected."]
        orphaned = {
            "analog_noise_source_x_0": "output was connected to removed block 'blocks_add_xx'"
        }
        entries = _validation_error_entries(errs, type_hint=None, orphaned_hints=orphaned)
        assert entries[0]["code"] == "gnu_validation"
        assert entries[0]["hint"] == orphaned["analog_noise_source_x_0"]

    def test_falls_back_to_type_hint_for_an_unrelated_error(self):
        errs = ["other_block: Sink - in(0): IO type/size mismatch"]
        orphaned = {"analog_noise_source_x_0": "output was connected to removed block 'x'"}
        entries = _validation_error_entries(
            errs, type_hint="Set type='float' on 'mid_throttle'", orphaned_hints=orphaned
        )
        assert entries[0]["hint"] == "Set type='float' on 'mid_throttle'"

    def test_no_hint_when_neither_applies(self):
        entries = _validation_error_entries(["blk: some error"], type_hint=None, orphaned_hints={})
        assert "hint" not in entries[0]
