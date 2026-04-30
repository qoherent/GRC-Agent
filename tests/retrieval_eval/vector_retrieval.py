"""Deterministic vector retrieval eval harness.

This module does not call an LLM. It compares lexical and vector retrieval and
checks that vector results remain read-only candidate evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sys
import time
from typing import Any

from grc_agent.retrieval.search import search_grc
from grc_agent.retrieval.vector import (
    FORBIDDEN_RESULT_KEYS,
    build_vector_records,
    semantic_search_grc,
)

EVAL_DIMENSIONS: tuple[str, ...] = (
    "lexical_top_k_hit",
    "vector_top_k_hit",
    "catalog_metadata_hit",
    "manual_hit",
    "tutorial_hit",
    "semantic_paraphrase_hit",
    "exact_id_hit",
    "false_positive_pass",
    "provenance_pass",
    "safety_pass",
    "latency_ms",
    "deterministic_rebuild_pass",
)


@dataclass(frozen=True)
class RetrievalEvalCase:
    name: str
    query: str
    scope: str
    expected_block_ids: tuple[str, ...] = ()
    expected_source_types: tuple[str, ...] = ()
    case_type: str = "semantic_paraphrase"


def _catalog_cases(
    prefix: str,
    rows: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    case_type: str = "semantic_paraphrase",
) -> tuple[RetrievalEvalCase, ...]:
    return tuple(
        RetrievalEvalCase(f"{prefix}_{index}", query, "catalog", expected, case_type=case_type)
        for index, (query, expected) in enumerate(rows)
    )


def _manual_cases(prefix: str, queries: tuple[str, ...]) -> tuple[RetrievalEvalCase, ...]:
    return tuple(
        RetrievalEvalCase(
            f"{prefix}_{index}",
            query,
            "manual",
            expected_source_types=("manual_chunk",),
            case_type="manual",
        )
        for index, query in enumerate(queries)
    )


PARAPHRASE_CASES: tuple[RetrievalEvalCase, ...] = tuple(
    RetrievalEvalCase(f"low_pass_{index}", query, "catalog", ("low_pass_filter",))
    for index, query in enumerate(
        (
            "audio smoother",
            "smooth audio",
            "smoothing filter",
            "remove high frequency noise",
            "soften harsh audio",
            "filter high frequencies",
            "low frequency pass filter",
            "cut treble noise",
            "clean noisy audio",
            "suppress rapid changes",
        )
    )
) + tuple(
    RetrievalEvalCase(f"agc_{index}", query, "catalog", ("analog_agc_xx",))
    for index, query in enumerate(
        (
            "automatic gain control",
            "auto gain",
            "gain stabilizer",
            "stabilize volume",
            "normalize signal level",
            "keep amplitude steady",
            "control signal gain automatically",
            "adaptive gain",
            "leveler block",
            "amplitude regulator",
        )
    )
) + tuple(
    RetrievalEvalCase(
        f"spectrum_{index}",
        query,
        "catalog",
        ("qtgui_freq_sink_x", "qtgui_waterfall_sink_x"),
    )
    for index, query in enumerate(
        (
            "spectrum display",
            "show spectrum",
            "frequency display",
            "inspect frequency content",
            "fft view",
            "spectral plot",
            "frequency sink",
            "waterfall display",
            "visualize channels",
            "see occupied bandwidth",
        )
    )
) + tuple(
    RetrievalEvalCase(
        f"throttle_{index}",
        query,
        "catalog",
        ("blocks_throttle2", "blocks_throttle"),
    )
    for index, query in enumerate(
        (
            "rate limiter",
            "sample rate limiter",
            "throttle stream",
            "slow stream down",
            "limit sample flow",
            "cap processing rate",
            "control stream speed",
            "pace samples",
            "avoid CPU overload",
            "software rate control",
        )
    )
) + tuple(
    RetrievalEvalCase(f"time_sink_{index}", query, "catalog", ("qtgui_time_sink_x",))
    for index, query in enumerate(
        (
            "scope trace",
            "oscilloscope",
            "time trace",
            "waveform display",
            "view samples over time",
            "plot signal amplitude",
            "time domain graph",
            "signal scope",
            "trace viewer",
            "sample waveform viewer",
        )
    )
)

EXPANDED_PARAPHRASE_CASES: tuple[RetrievalEvalCase, ...] = _catalog_cases(
    "extra_para",
    (
        ("reject low frequencies", ("high_pass_filter",)),
        ("remove bass rumble", ("high_pass_filter",)),
        ("only keep high frequency content", ("high_pass_filter",)),
        ("band limiter for a narrow channel", ("band_pass_filter",)),
        ("keep only a frequency band", ("band_pass_filter",)),
        ("reject a narrow interference band", ("band_reject_filter",)),
        ("notch out one band", ("band_reject_filter",)),
        ("change sample rate by rational factor", ("rational_resampler_xxx",)),
        ("resample stream by interpolation and decimation", ("rational_resampler_xxx",)),
        ("polyphase arbitrary resampler", ("pfb_arb_resampler_xxx",)),
        ("audio output device", ("audio_sink",)),
        ("play samples through speakers", ("audio_sink",)),
        ("microphone input source", ("audio_source",)),
        ("capture audio from sound card", ("audio_source",)),
        ("read samples from a file", ("blocks_file_source",)),
        ("write stream to a file", ("blocks_file_sink",)),
        ("generate a sine wave", ("analog_sig_source_x",)),
        ("signal generator source", ("analog_sig_source_x",)),
        ("white noise generator", ("analog_noise_source_x",)),
        ("random noise source", ("analog_noise_source_x",)),
        ("stop after a fixed number of samples", ("blocks_head",)),
        ("limit stream length", ("blocks_head",)),
        ("drop output samples", ("blocks_null_sink",)),
        ("discard stream data", ("blocks_null_sink",)),
        ("constant vector source", ("blocks_vector_source_x",)),
        ("repeat a known sample sequence", ("blocks_vector_source_x",)),
        ("add two streams", ("blocks_add_xx",)),
        ("sum signals together", ("blocks_add_xx",)),
        ("multiply two streams", ("blocks_multiply_xx",)),
        ("mix signals by multiplication", ("blocks_multiply_xx",)),
        ("periodic message generator", ("blocks_message_strobe",)),
        ("send a PMT message repeatedly", ("blocks_message_strobe",)),
        ("convert pdu to tagged stream", ("pdu_pdu_to_tagged_stream",)),
        ("packet message to stream", ("pdu_pdu_to_tagged_stream",)),
        ("decode constellation symbols", ("digital_constellation_decoder_cb",)),
        ("map constellation points to bits", ("digital_constellation_decoder_cb",)),
        ("FFT transform block", ("fft_vxx",)),
        ("frequency transform vector", ("fft_vxx",)),
        ("finite impulse response filter", ("fir_filter_xxx",)),
        ("FIR decimating filter", ("fir_filter_xxx",)),
        ("publish stream over ZMQ", ("zeromq_pub_sink",)),
        ("receive stream over ZMQ", ("zeromq_sub_source",)),
        ("USRP hardware source", ("uhd_usrp_source",)),
        ("USRP hardware sink", ("uhd_usrp_sink",)),
        ("PlutoSDR source block", ("iio_pluto_source",)),
        ("PlutoSDR sink block", ("iio_pluto_sink",)),
        ("HackRF source block", ("soapy_hackrf_source",)),
        ("HackRF sink block", ("soapy_hackrf_sink",)),
        ("quadrature demodulator", ("analog_quadrature_demod_cf",)),
        ("FM demodulator block", ("analog_fm_demod_cf",)),
    ),
)

EXACT_ID_CASES: tuple[RetrievalEvalCase, ...] = tuple(
    RetrievalEvalCase(f"exact_{block_id}", block_id, "catalog", (block_id,), case_type="exact_id")
    for block_id in (
        "blocks_throttle2",
        "analog_agc_xx",
        "low_pass_filter",
        "qtgui_freq_sink_x",
        "qtgui_time_sink_x",
        "audio_sink",
        "audio_source",
        "analog_sig_source_x",
        "blocks_head",
        "blocks_file_sink",
        "blocks_file_source",
        "blocks_vector_source_x",
        "blocks_null_sink",
        "digital_constellation_decoder_cb",
        "fir_filter_xxx",
        "fft_vxx",
        "blocks_add_xx",
        "blocks_multiply_xx",
        "blocks_message_strobe",
        "pdu_pdu_to_tagged_stream",
    )
)

EXPANDED_EXACT_ID_CASES: tuple[RetrievalEvalCase, ...] = _catalog_cases(
    "extra_exact",
    (
        ("high_pass_filter", ("high_pass_filter",)),
        ("band_pass_filter", ("band_pass_filter",)),
        ("band_reject_filter", ("band_reject_filter",)),
        ("rational_resampler_xxx", ("rational_resampler_xxx",)),
        ("pfb_arb_resampler_xxx", ("pfb_arb_resampler_xxx",)),
        ("analog_noise_source_x", ("analog_noise_source_x",)),
        ("analog_quadrature_demod_cf", ("analog_quadrature_demod_cf",)),
        ("analog_fm_demod_cf", ("analog_fm_demod_cf",)),
        ("zeromq_pub_sink", ("zeromq_pub_sink",)),
        ("zeromq_sub_source", ("zeromq_sub_source",)),
        ("uhd_usrp_source", ("uhd_usrp_source",)),
        ("uhd_usrp_sink", ("uhd_usrp_sink",)),
        ("iio_pluto_source", ("iio_pluto_source",)),
        ("iio_pluto_sink", ("iio_pluto_sink",)),
        ("soapy_hackrf_source", ("soapy_hackrf_source",)),
        ("soapy_hackrf_sink", ("soapy_hackrf_sink",)),
        ("blocks_copy", ("blocks_copy",)),
        ("blocks_delay", ("blocks_delay",)),
        ("blocks_selector", ("blocks_selector",)),
        ("blocks_mute_xx", ("blocks_mute_xx",)),
        ("blocks_stream_mux", ("blocks_stream_mux",)),
        ("blocks_stream_to_vector", ("blocks_stream_to_vector",)),
        ("blocks_vector_to_stream", ("blocks_vector_to_stream",)),
        ("blocks_keep_one_in_n", ("blocks_keep_one_in_n",)),
        ("blocks_skiphead", ("blocks_skiphead",)),
        ("blocks_tag_debug", ("blocks_tag_debug",)),
        ("pdu_tagged_stream_to_pdu", ("pdu_tagged_stream_to_pdu",)),
        ("pdu_pdu_to_tagged_stream", ("pdu_pdu_to_tagged_stream",)),
        ("digital_costas_loop_cc", ("digital_costas_loop_cc",)),
        ("digital_clock_recovery_mm_xx", ("digital_clock_recovery_mm_xx",)),
        ("digital_corr_est_cc", ("digital_corr_est_cc",)),
        ("digital_crc32_bb", ("digital_crc32_bb",)),
        ("digital_map_bb", ("digital_map_bb",)),
        ("logpwrfft_x", ("logpwrfft_x",)),
        ("fft_filter_xxx", ("fft_filter_xxx",)),
        ("filter_freq_xlating_fir_filter_xxx", ("freq_xlating_fir_filter_xxx",)),
        ("qtgui_waterfall_sink_x", ("qtgui_waterfall_sink_x",)),
        ("qtgui_const_sink_x", ("qtgui_const_sink_x",)),
        ("qtgui_sink_x", ("qtgui_sink_x",)),
        ("qtgui_time_raster_sink_x", ("qtgui_time_raster_sink_x",)),
        ("variable", ("variable",)),
        ("variable_qtgui_range", ("variable_qtgui_range",)),
        ("xmlrpc_client", ("xmlrpc_client",)),
        ("xmlrpc_server", ("xmlrpc_server",)),
        ("channels_channel_model", ("channels_channel_model",)),
        ("channels_fading_model", ("channels_fading_model",)),
        ("channels_selective_fading_model", ("channels_selective_fading_model",)),
        ("dtv_dvbt2_framemapper_cc", ("dtv_dvbt2_framemapper_cc",)),
        ("vocoder_codec2_encode_sp", ("vocoder_codec2_encode_sp",)),
        ("vocoder_codec2_decode_ps", ("vocoder_codec2_decode_ps",)),
    ),
    case_type="exact_id",
)

MANUAL_CASES: tuple[RetrievalEvalCase, ...] = tuple(
    RetrievalEvalCase(f"manual_{index}", query, "manual", expected_source_types=("manual_chunk",), case_type="manual")
    for index, query in enumerate(
        (
            "stream tags",
            "message passing",
            "PMT dictionary",
            "sample rate tutorial",
            "binary files DSP",
            "VOLK kernels",
            "runtime updating variables",
            "ZMQ blocks",
            "hier block parameters",
            "packet communications",
            "OFDM carriers",
            "flowgraph python code",
            "GRC YAML",
            "out of tree module",
            "hardware considerations",
            "polyphase resampler",
            "frequency shifting",
            "signal data types",
            "streams and vectors",
            "filter taps",
        )
    )
)

EXPANDED_MANUAL_CASES: tuple[RetrievalEvalCase, ...] = _manual_cases(
    "extra_manual",
    (
        "GNU Radio scheduler",
        "stream to vector conversion",
        "tagged stream blocks",
        "packet header format",
        "creating custom Python blocks",
        "writing out of tree modules",
        "C++ block coding guide",
        "message strobe examples",
        "polymorphic type PMT pairs",
        "stream metadata tags",
        "ZMQ publish subscribe flowgraph",
        "UHD hardware setup",
        "audio sink source ALSA PulseAudio",
        "filter design taps transition width",
        "root raised cosine filter taps",
        "frequency translating FIR filter",
        "FM receiver tutorial",
        "OFDM synchronization",
        "QAM modulation demodulation",
        "BPSK packet communications",
        "VOLK profiling",
        "runtime variable update",
        "XMLRPC block control",
        "hierarchical block parameter",
        "virtual sink source",
        "YAML flowgraph format",
        "Python generated flowgraph code",
        "GNU Radio companion variables",
        "signal data type conversion",
        "streams versus vectors",
        "binary file source sink",
        "reading writing files",
        "sample rate change resampling",
        "polyphase filter bank",
        "frequency shifting tutorial",
        "IQ complex samples",
        "RTL SDR FM receiver",
        "USRP hardware considerations",
        "creating first flowgraph",
        "installing GNU Radio",
        "guided tutorial GRC",
        "guided tutorial programming",
        "custom buffers",
        "control port probe",
        "gr modtool new block",
        "OOT module porting guide",
        "stream tags Python block",
        "message passing Python block",
        "packet communications tutorial",
        "hardware source sink notes",
    ),
)

FALSE_POSITIVE_CASES: tuple[RetrievalEvalCase, ...] = tuple(
    RetrievalEvalCase(f"trap_{index}", query, "catalog", expected, case_type="false_positive")
    for index, (query, expected) in enumerate(
        (
            ("blocks_head", ("blocks_head",)),
            ("disable low pass filter", ("low_pass_filter",)),
            ("save blocks_throttle2", ("blocks_throttle2",)),
            ("remove audio sink", ("audio_sink",)),
            ("apply qtgui_time_sink_x", ("qtgui_time_sink_x",)),
            ("transaction analog_agc_xx", ("analog_agc_xx",)),
            ("insert blocks_file_sink", ("blocks_file_sink",)),
            ("repair fft_vxx", ("fft_vxx",)),
            ("remove_connection blocks_add_xx", ("blocks_add_xx",)),
            ("save_graph audio_source", ("audio_source",)),
            ("params blocks_multiply_xx", ("blocks_multiply_xx",)),
            ("insert_tool_args blocks_null_sink", ("blocks_null_sink",)),
            ("apply_edit analog_sig_source_x", ("analog_sig_source_x",)),
            ("delete block blocks_vector_source_x", ("blocks_vector_source_x",)),
            ("YAML edit pdu_pdu_to_tagged_stream", ("pdu_pdu_to_tagged_stream",)),
            ("raw GRC filter_fir_filter_xxx", ("fir_filter_xxx",)),
            ("block recipe qtgui_freq_sink_x", ("qtgui_freq_sink_x",)),
            ("default value audio_sink", ("audio_sink",)),
            ("repair plan blocks_message_strobe", ("blocks_message_strobe",)),
            ("blacklist analog_agc_xx", ("analog_agc_xx",)),
        )
    )
)

EXPANDED_FALSE_POSITIVE_CASES: tuple[RetrievalEvalCase, ...] = _catalog_cases(
    "extra_trap",
    (
        ("delete high_pass_filter", ("high_pass_filter",)),
        ("save band_pass_filter", ("band_pass_filter",)),
        ("apply_edit rational_resampler_xxx", ("rational_resampler_xxx",)),
        ("transaction pfb_arb_resampler_xxx", ("pfb_arb_resampler_xxx",)),
        ("params analog_noise_source_x", ("analog_noise_source_x",)),
        ("insert_tool_args zeromq_pub_sink", ("zeromq_pub_sink",)),
        ("remove_connection zeromq_sub_source", ("zeromq_sub_source",)),
        ("raw YAML uhd_usrp_source", ("uhd_usrp_source",)),
        ("block recipe uhd_usrp_sink", ("uhd_usrp_sink",)),
        ("default mutation iio_pluto_source", ("iio_pluto_source",)),
        ("repair plan iio_pluto_sink", ("iio_pluto_sink",)),
        ("blacklist soapy_hackrf_source", ("soapy_hackrf_source",)),
        ("allowlist soapy_hackrf_sink", ("soapy_hackrf_sink",)),
        ("save_graph blocks_copy", ("blocks_copy",)),
        ("apply blocks_delay", ("blocks_delay",)),
        ("remove blocks_selector", ("blocks_selector",)),
        ("disable blocks_mute_xx", ("blocks_mute_xx",)),
        ("insert blocks_stream_mux", ("blocks_stream_mux",)),
        ("YAML blocks_stream_to_vector", ("blocks_stream_to_vector",)),
        ("raw GRC blocks_vector_to_stream", ("blocks_vector_to_stream",)),
        ("repair blocks_keep_one_in_n", ("blocks_keep_one_in_n",)),
        ("transaction blocks_skiphead", ("blocks_skiphead",)),
        ("params blocks_tag_debug", ("blocks_tag_debug",)),
        ("default value digital_costas_loop_cc", ("digital_costas_loop_cc",)),
        ("apply_edit digital_map_bb", ("digital_map_bb",)),
        ("save logpwrfft_x", ("logpwrfft_x",)),
        ("delete qtgui_waterfall_sink_x", ("qtgui_waterfall_sink_x",)),
        ("remove qtgui_const_sink_x", ("qtgui_const_sink_x",)),
        ("insert qtgui_sink_x", ("qtgui_sink_x",)),
        ("repair channels_channel_model", ("channels_channel_model",)),
    ),
    case_type="false_positive",
)

EVAL_CASES: tuple[RetrievalEvalCase, ...] = (
    PARAPHRASE_CASES
    + EXPANDED_PARAPHRASE_CASES
    + EXACT_ID_CASES
    + EXPANDED_EXACT_ID_CASES
    + MANUAL_CASES
    + EXPANDED_MANUAL_CASES
    + FALSE_POSITIVE_CASES
    + EXPANDED_FALSE_POSITIVE_CASES
)


def empty_eval_summary() -> dict[str, Any]:
    """Return the stable top-level shape for vector retrieval eval reports."""
    return {
        "ok": True,
        "dimensions": list(EVAL_DIMENSIONS),
        "cases": [],
        "summary": {},
        "miss_analysis": {},
    }


def run_eval(
    *,
    eval_cases: tuple[RetrievalEvalCase, ...] = EVAL_CASES,
    vector_search_fn: Any = semantic_search_grc,
) -> dict[str, Any]:
    started = time.perf_counter()
    case_results: list[dict[str, Any]] = []
    vector_hit_count = 0
    lexical_hit_count = 0
    safety_count = 0
    provenance_count = 0
    false_positive_count = 0
    for case in eval_cases:
        before = time.perf_counter()
        vector_payload = vector_search_fn(case.query, scope=case.scope, k=5)
        latency_ms = round((time.perf_counter() - before) * 1000, 3)
        vector_results = vector_payload.get("results", []) if vector_payload.get("ok") else []
        lexical_payload = (
            search_grc(case.query, scope="catalog", k=5)
            if case.expected_block_ids and case.scope == "catalog"
            else {"ok": True, "results": []}
        )
        lexical_results = lexical_payload.get("results", []) if lexical_payload.get("ok") else []
        vector_block_ids = {
            result.get("canonical_block_id")
            for result in vector_results
            if isinstance(result, dict)
        }
        lexical_block_ids = {
            result.get("block_id")
            for result in lexical_results
            if isinstance(result, dict)
        }
        expected = set(case.expected_block_ids)
        vector_hit = bool(expected & vector_block_ids) if expected else bool(vector_results)
        lexical_hit = bool(expected & lexical_block_ids) if expected else bool(lexical_results)
        expected_source_types = set(case.expected_source_types)
        source_type_hit = (
            any(result.get("source_type") in expected_source_types for result in vector_results)
            if expected_source_types
            else True
        )
        safety_pass = all(_result_is_safe(result) for result in vector_results)
        provenance_pass = all(
            isinstance(result.get("provenance"), dict)
            and bool(result["provenance"].get("path"))
            for result in vector_results
            if isinstance(result, dict)
        )
        vector_hit_count += int(vector_hit and source_type_hit)
        lexical_hit_count += int(lexical_hit)
        safety_count += int(safety_pass)
        provenance_count += int(provenance_pass)
        false_positive_pass = case.case_type != "false_positive" or vector_hit
        false_positive_count += int(false_positive_pass)
        case_results.append(
            {
                "name": case.name,
                "case_type": case.case_type,
                "query": case.query,
                "scope": case.scope,
                "expected_block_ids": list(case.expected_block_ids),
                "expected_source_types": list(case.expected_source_types),
                "lexical_top_k_hit": lexical_hit,
                "vector_top_k_hit": vector_hit,
                "catalog_metadata_hit": case.case_type in {"semantic_paraphrase", "exact_id", "false_positive"} and vector_hit,
                "manual_hit": case.case_type == "manual" and source_type_hit,
                "tutorial_hit": any(result.get("source_type") == "tutorial_chunk" for result in vector_results),
                "semantic_paraphrase_hit": case.case_type == "semantic_paraphrase" and vector_hit,
                "exact_id_hit": case.case_type == "exact_id" and vector_hit,
                "false_positive_pass": false_positive_pass,
                "provenance_pass": provenance_pass,
                "safety_pass": safety_pass,
                "latency_ms": latency_ms,
                "deterministic_rebuild_pass": None,
                "top_vector_ids": [result.get("canonical_block_id") or result.get("record_id") for result in vector_results[:5]],
                "top_vector_results": [_vector_result_summary(result) for result in vector_results[:5]],
                "top_lexical_results": [_lexical_result_summary(result) for result in lexical_results[:5]],
            }
        )
    miss_analysis = _build_miss_analysis(case_results)
    deterministic_rebuild_pass = _deterministic_rebuild_pass()
    for case_result in case_results:
        case_result["deterministic_rebuild_pass"] = deterministic_rebuild_pass
    total = len(case_results)
    ok = (
        total > 0
        and vector_hit_count >= lexical_hit_count
        and safety_count == total
        and provenance_count == total
        and false_positive_count == total
        and deterministic_rebuild_pass
    )
    return {
        "ok": ok,
        "dimensions": list(EVAL_DIMENSIONS),
        "cases": case_results,
        "summary": {
            "total_cases": total,
            "lexical_top_k_hits": lexical_hit_count,
            "vector_top_k_hits": vector_hit_count,
            "safety_passes": safety_count,
            "provenance_passes": provenance_count,
            "false_positive_passes": false_positive_count,
            "deterministic_rebuild_pass": deterministic_rebuild_pass,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        },
        "miss_analysis": miss_analysis,
    }


def _result_is_safe(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if any(key in result for key in FORBIDDEN_RESULT_KEYS):
        return False
    for value in result.values():
        if isinstance(value, dict) and not _result_is_safe(value):
            return False
    return True


def _build_miss_analysis(cases: list[dict[str, Any]]) -> dict[str, Any]:
    vector_misses = [
        _case_miss_summary(case)
        for case in cases
        if not case.get("vector_top_k_hit") and case.get("case_type") != "manual"
    ]
    lexical_wins = [
        _case_miss_summary(case)
        for case in cases
        if case.get("lexical_top_k_hit") and not case.get("vector_top_k_hit")
    ]
    exact_id_misses = [
        _case_miss_summary(case)
        for case in cases
        if case.get("case_type") == "exact_id" and not case.get("exact_id_hit")
    ]
    false_positive_failures = [
        _case_miss_summary(case)
        for case in cases
        if case.get("case_type") == "false_positive" and not case.get("false_positive_pass")
    ]
    source_type_misses = [
        _case_miss_summary(case)
        for case in cases
        if case.get("case_type") == "manual" and not case.get("manual_hit")
    ]
    return {
        "vector_miss_count": len(vector_misses),
        "lexical_win_count": len(lexical_wins),
        "exact_id_miss_count": len(exact_id_misses),
        "false_positive_failure_count": len(false_positive_failures),
        "source_type_miss_count": len(source_type_misses),
        "vector_misses": vector_misses,
        "lexical_wins_over_vector": lexical_wins,
        "exact_id_misses": exact_id_misses,
        "false_positive_failures": false_positive_failures,
        "source_type_misses": source_type_misses,
    }


def _case_miss_summary(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": case.get("name"),
        "case_type": case.get("case_type"),
        "query": case.get("query"),
        "scope": case.get("scope"),
        "expected_block_ids": case.get("expected_block_ids", []),
        "expected_source_types": case.get("expected_source_types", []),
        "lexical_top_k_hit": case.get("lexical_top_k_hit"),
        "vector_top_k_hit": case.get("vector_top_k_hit"),
        "top_vector_ids": case.get("top_vector_ids", []),
        "top_vector_results": case.get("top_vector_results", []),
        "top_lexical_results": case.get("top_lexical_results", []),
    }


def _vector_result_summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        "id": result.get("canonical_block_id") or result.get("record_id"),
        "title": result.get("title"),
        "source_type": result.get("source_type"),
        "vector_score_raw": result.get("vector_score_raw"),
    }


def _lexical_result_summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        "id": result.get("block_id"),
        "label": result.get("label"),
        "score": result.get("score"),
    }


def _deterministic_rebuild_pass() -> bool:
    first_records, first_metadata = build_vector_records()
    second_records, second_metadata = build_vector_records()
    return (
        [record.record_id for record in first_records]
        == [record.record_id for record in second_records]
        and first_metadata["corpus_hash"] == second_metadata["corpus_hash"]
    )


if __name__ == "__main__":
    summary = run_eval()
    print(json.dumps(summary, indent=2, sort_keys=True))
    sys.exit(0 if summary["ok"] else 1)
