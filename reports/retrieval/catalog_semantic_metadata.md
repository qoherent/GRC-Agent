# Catalog Semantic Metadata Governance

Date: 2026-04-28

Policy:

- Metadata must describe stable block capability, not patch one eval query.
- Each entry must identify the block, field, reason, helped queries, and
  false-positive checks.
- Metadata may improve retrieval candidate discovery only; it must never
  authorize mutation.

Current governed entries are defined in
`src/grc_agent/retrieval/vector.py::CATALOG_SEMANTIC_METADATA`.

| Block | Field | Reason | Helped queries | False-positive checks |
|---|---|---|---|---|
| `low_pass_filter` | aliases | Low-pass filters attenuate rapid/high-frequency changes and are commonly used as smoothing filters. | audio smoother; smooth audio; smoothing filter | low_pass_filter; disable low pass filter |
| `high_pass_filter` | aliases | High-pass filters attenuate low-frequency content while passing higher-frequency signal components. | reject low frequencies; remove bass rumble | high_pass_filter; delete high_pass_filter |
| `analog_agc_xx` | aliases | AGC blocks automatically adjust gain to stabilize signal amplitude around a reference level. | automatic gain control; stabilize volume; amplitude regulator | analog_agc_xx; transaction analog_agc_xx |
| `qtgui_freq_sink_x` | aliases | QT GUI frequency sinks visualize frequency-domain content and spectrum occupancy. | spectrum display; frequency display; fft view | qtgui_freq_sink_x; block recipe qtgui_freq_sink_x |
| `qtgui_waterfall_sink_x` | aliases | QT GUI waterfall sinks show spectrum content over time as a waterfall display. | waterfall display; spectral plot; see occupied bandwidth | qtgui_waterfall_sink_x; insert qtgui_waterfall_sink_x |
| `blocks_throttle2` | aliases | Throttle blocks pace sample flow and limit processing rate in non-hardware flowgraphs. | rate limiter; sample rate limiter; throttle stream | blocks_throttle2; save blocks_throttle2 |
| `blocks_throttle` | aliases | Throttle blocks pace sample flow and limit processing rate in non-hardware flowgraphs. | rate limiter; sample rate limiter; throttle stream | blocks_throttle; save blocks_throttle |
| `qtgui_time_sink_x` | aliases | QT GUI time sinks display sample amplitude over time like an oscilloscope trace. | scope trace; oscilloscope; waveform display | qtgui_time_sink_x; apply qtgui_time_sink_x |
| `blocks_file_source` | aliases | File Source reads sample streams from a configured file and provides them to the flowgraph. | read samples from a file; file input source | blocks_file_source; insert blocks_file_source |
| `blocks_head` | aliases | Head passes only the first configured number of items and then stops forwarding stream data. | stop after a fixed number of samples; limit stream length | blocks_head; delete blocks_head |
| `blocks_null_sink` | aliases | Null Sink consumes stream items and intentionally discards them without producing output. | drop output samples; discard stream data | blocks_null_sink; insert_tool_args blocks_null_sink |
| `blocks_vector_source_x` | aliases | Vector Source emits configured vector data, optionally repeating it as a deterministic sample sequence. | repeat a known sample sequence; constant vector source | blocks_vector_source_x; delete block blocks_vector_source_x |
| `blocks_add_xx` | aliases | Add blocks sum corresponding stream items from multiple inputs. | sum signals together; add two streams | blocks_add_xx; remove_connection blocks_add_xx |
| `blocks_message_strobe` | aliases | Message Strobe periodically emits a configured PMT message. | send a PMT message repeatedly; periodic message generator | blocks_message_strobe; repair plan blocks_message_strobe |
| `digital_constellation_decoder_cb` | aliases | Constellation Decoder maps received constellation points to decoded symbol or bit decisions. | map constellation points to bits; decode constellation symbols | digital_constellation_decoder_cb; delete digital_constellation_decoder_cb |

Latest governed-metadata eval:

- Command: `uv run python -m tests.retrieval_eval.vector_retrieval`
- Persisted result: `reports/retrieval/vector_eval_governed_metadata.json`
- Result: 276/290 vector top-k hits, 168/290 lexical top-k hits, 0 exact-ID misses, 0 false-positive failures, 0 source-type misses.
- Decision: keep `BAAI/bge-small-en-v1.5`, vector-only retrieval, no hybrid/reranker/runtime multi-model path.

Next additions require:

1. At least 3 clustered misses or repeated misses across 2 distinct sources.
2. A capability reason that remains true outside the failing query.
3. At least one mutation-shaped negative trap added or confirmed.
4. A retrieval eval rerun with miss-analysis recorded.
