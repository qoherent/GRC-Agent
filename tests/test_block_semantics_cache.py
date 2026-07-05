"""Direct unit tests for ``_EVALUATED_HIDE_CACHE`` in ``block_semantics``.

Caches at module level — cleared on test teardown.
"""

from __future__ import annotations

from unittest import mock

import pytest
from grc_agent.runtime.block_semantics import (
    _EVALUATED_HIDE_CACHE,
    evaluated_param_hides,
)


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    _EVALUATED_HIDE_CACHE.clear()
    yield


@mock.patch("grc_agent.runtime.block_semantics._compute_evaluated_param_hides")
def test_populated_on_first_call(mock_compute) -> None:
    mock_compute.return_value = {"gain": "none"}
    result = evaluated_param_hides("blocks_amplifier", {"gain": "12"})
    assert result == {"gain": "none"}
    mock_compute.assert_called_once_with("blocks_amplifier", {"gain": "12"})


@mock.patch("grc_agent.runtime.block_semantics._compute_evaluated_param_hides")
def test_cache_hit_returns_same_dict_object(mock_compute) -> None:
    mock_compute.return_value = {"gain": "none"}
    first = evaluated_param_hides("blocks_amplifier", {"gain": "12"})
    second = evaluated_param_hides("blocks_amplifier", {"gain": "12"})
    assert first is second
    mock_compute.assert_called_once()


@mock.patch("grc_agent.runtime.block_semantics._compute_evaluated_param_hides")
def test_cache_miss_different_param_values(mock_compute) -> None:
    mock_compute.side_effect = [{"gain": "none"}, {"gain": "all"}]
    r1 = evaluated_param_hides("blocks_amplifier", {"gain": "12"})
    r2 = evaluated_param_hides("blocks_amplifier", {"gain": "0"})
    assert mock_compute.call_count == 2
    assert r1 == {"gain": "none"}
    assert r2 == {"gain": "all"}


@mock.patch("grc_agent.runtime.block_semantics._compute_evaluated_param_hides")
def test_cache_miss_different_block_type(mock_compute) -> None:
    mock_compute.side_effect = [{"gain": "none"}, {"freq": "none"}]
    r1 = evaluated_param_hides("blocks_amplifier", {"gain": "12"})
    r2 = evaluated_param_hides("blocks_signal_source", {"freq": "1k"})
    assert mock_compute.call_count == 2
    assert r1 == {"gain": "none"}
    assert r2 == {"freq": "none"}


@mock.patch("grc_agent.runtime.block_semantics._compute_evaluated_param_hides")
def test_cache_key_order_independent(mock_compute) -> None:
    mock_compute.return_value = {"gain": "none", "freq": "part"}
    r1 = evaluated_param_hides("blocks_amplifier", {"gain": "12", "freq": "1k"})
    r2 = evaluated_param_hides("blocks_amplifier", {"freq": "1k", "gain": "12"})
    assert r1 is r2
    mock_compute.assert_called_once()


@mock.patch("grc_agent.runtime.block_semantics._compute_evaluated_param_hides")
def test_cache_key_none_vs_empty_string(mock_compute) -> None:
    mock_compute.return_value = {"mode": "none"}
    r1 = evaluated_param_hides("blocks_amplifier", {"mode": None})
    r2 = evaluated_param_hides("blocks_amplifier", {"mode": ""})
    assert r1 is r2
    mock_compute.assert_called_once()


@mock.patch("grc_agent.runtime.block_semantics._compute_evaluated_param_hides")
def test_cache_populated_for_unknown_block_type(mock_compute) -> None:
    mock_compute.return_value = {}
    r1 = evaluated_param_hides("nonexistent_block_xyz", {})
    r2 = evaluated_param_hides("nonexistent_block_xyz", {})
    assert r1 is r2
    mock_compute.assert_called_once()
