# Vector Retrieval Miss Triage

Date: 2026-04-28

Eval command:

```bash
uv run python -m tests.retrieval_eval.vector_retrieval
```

Latest governed-metadata result:

- Total cases: 290
- Vector top-k hits: 276
- Lexical top-k hits: 168
- Safety/provenance/false-positive: 290/290 each
- Exact-ID misses: 0
- Source-type misses: 0
- Vector misses: 14
- Lexical wins over vector: 5
- Persisted report: `reports/retrieval/vector_eval_governed_metadata.json`

## Decision

Do not implement hybrid retrieval yet. The expanded suite shows vector-only
retrieval still beats lexical by a wide margin, exact-ID behavior is clean, and
false-positive traps remain clean. The 5 lexical wins are worth tracking but
are not enough to justify fusion weights or a new hybrid ranking policy.
The last metadata pass fixed stable capability gaps only; remaining misses are
not enough evidence for hybrid, reranking, or runtime multi-model retrieval.

## Miss Categories

Categories are diagnostic and not exclusive; lexical wins are tracked as a
separate signal because they would be the main evidence for future hybrid
retrieval.

| Category | Count | Examples | Recommended fix |
|---|---:|---|---|
| Embedding/ranking limitation | 7 | `normalize signal level`, `spectral plot`, `sample waveform viewer`, `reject low frequencies` | Keep current model; do not patch ranking without new evidence. |
| Ambiguous GNU wording | 3 | `visualize channels`, `leveler block`, `keep amplitude steady` | Clarify or show multiple candidates; do not force a single block. |
| Stable metadata gap | 0 | Previous file/source/head/null-sink/vector-source/add/message-strobe/decoder gaps are fixed. | Add no further metadata from this set. |
| Lexical win | 5 | `signal scope`, `band limiter for a narrow channel`, `FFT transform block` | Track; hybrid only if this grows materially. |
| Eval expectation too strict | 2 | `frequency transform vector`, `packet message to stream` | Keep as diagnostic misses unless real UX requires exact block preference. |

## Remaining Misses

| Query | Expected | Vector top results | Lexical hit | Category |
|---|---|---|---|---|
| normalize signal level | `analog_agc_xx` | `blocks_correctiq_auto`, `low_pass_filter`, `freq_xlating_fft_filter_ccc` | no | Embedding/ranking limitation |
| keep amplitude steady | `analog_agc_xx` | `blocks_correctiq_auto`, `low_pass_filter`, `blocks_keep_one_in_n` | no | Ambiguous/embedding limitation |
| leveler block | `analog_agc_xx` | `blocks_xor_xx`, `blocks_or_xx`, `blocks_selector` | no | Ambiguous wording |
| spectral plot | `qtgui_freq_sink_x` or `qtgui_waterfall_sink_x` | `channels_fading_model`, `variable_qtgui_azelplot`, `digital_diff_phasor_cc` | no | Embedding/ranking limitation |
| visualize channels | `qtgui_freq_sink_x` or `qtgui_waterfall_sink_x` | `dtv_dvbt2_cellinterleaver_cc`, `dtv_dvbt2_framemapper_cc`, `dtv_dvbt_convolutional_deinterleaver` | no | Ambiguous GNU wording |
| see occupied bandwidth | `qtgui_freq_sink_x` or `qtgui_waterfall_sink_x` | `dtv_dvbt2_cellinterleaver_cc`, `iio_fmcomms2_sink`, `blocks_throttle` | no | Embedding/ranking limitation |
| signal scope | `qtgui_time_sink_x` | `blocks_probe_signal_x`, `blocks_probe_signal_vx`, `blocks_add_xx` | yes | Lexical win |
| sample waveform viewer | `qtgui_time_sink_x` | `analog_random_source_x`, `digital_qam_demod`, `digital_psk_demod` | no | Embedding/ranking limitation |
| reject low frequencies | `high_pass_filter` | `band_reject_filter`, `variable_band_reject_filter_taps`, `blocks_correctiq` | no | Embedding/ranking limitation |
| band limiter for a narrow channel | `band_pass_filter` | `pfb_channelizer_hier_ccf`, `channels_selective_fading_model`, `channels_selective_fading_model2` | yes | Lexical win |
| keep only a frequency band | `band_pass_filter` | `variable_band_reject_filter_taps`, `band_reject_filter`, `analog_pll_freqdet_cf` | yes | Lexical win |
| packet message to stream | `pdu_pdu_to_tagged_stream` | `blocks_var_to_msg`, `digital_crc16_async_bb`, `digital_crc32_async_bb` | yes | Eval expectation / lexical win |
| FFT transform block | `fft_vxx` | `uhd_rfnoc_fft`, `uhd_fpga_fft`, `blocks_freqshift_cc` | yes | Lexical win |
| frequency transform vector | `fft_vxx` | `blocks_vector_source_x`, `analog_quadrature_demod_cf`, `analog_pll_freqdet_cf` | no | Eval expectation / embedding limitation |

## Follow-Up Policy

- If exact-ID misses appear, test catalog-scoped lexical fallback or hybrid.
- If lexical wins grow materially, test simple score fusion offline.
- If misses stay semantic with few lexical wins, compare embedding models before
  adding hybrid complexity.
- If queries are ambiguous in GNU Radio terminology, prefer clarification or
  multiple candidates over forced ranking patches.
