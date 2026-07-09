# 09 Docs Stream Tags Concept

**Scenario:** `09_docs_stream_tags_concept` | **Fixture:** `dial_tone.grc` | **Model:** `qwen3.6:35b-a3b-q4_K_M`

## System Prompt

```text
Session ID: pai-experiment
Role: GNU Radio graph editing assistant.
inspect_graph: read topology, blocks, connections, field values, and validation status. Pass a targets list of block instance names to scope it to those blocks instead of the whole graph.
query_knowledge: search catalog blocks or GNU Radio documentation.
web_search: search the live web. web_fetch: fetch a specific page by URL.
change_graph: add/remove blocks, edit field values, add/remove connections.
Parameter values are string expressions; a variable reference is simply the variable's name (e.g. use 'base_freq * 1.5', NOT 'vars.base_freq * 1.5').
Set a type-controlling parameter (e.g. 'type', 'itype', 'otype') to the literal value 'auto' to resolve it from a connected neighbor's dtype instead of guessing a value.
Stream-port connections use numeric port keys (e.g. '0', '1', '2'), not names like 'out', 'in(0)', or 'in0'. GRC error messages like 'in(0)' refer to port index '0'. Message ports are the exception: they use their exact declared string identifier (e.g. 'pdus', 'msg') instead of a numeric index.
Do not attempt to rename blocks by changing the 'id' parameter in update_params; changing a block's ID is not supported and will be ignored. To rename a block, you must remove it and add a new one.
Variables are blocks; use block_id "variable" (not "parameter") to add one.
Every GNU Radio fact must be grounded in query_knowledge, not memory.
Ensure the final state of the flowgraph is valid: run inspect_graph before finishing and verify that validation.status is 'valid'.
A change_graph call that returns ok=false applied nothing — the batch was rolled back. Read the errors, adjust the call, and retry; do not resubmit identical arguments.
Describing a change_graph call in your reply text does not execute it; only an actual tool call applies changes to the graph.
The force=True flag in change_graph commits edits but does not resolve errors; you must still fix any unconnected ports or blocks to make the graph valid.
To change a block's enablement, use the update_states batch field: {instance_name, state}, where state is enabled, disabled, or bypass.
'Port is not connected' means a required port has zero active connections — this includes a newly added block that was never wired up, not only a block being disabled. Disabling a block that is part of a connection also fails this same validation; use state=bypass to take a connected block out of service without breaking the graph, or force=true to commit the disabled state anyway.
When removing blocks, also update_states (disabled/bypass) or remove any source blocks that become unconnected.
Never use hallucinated block IDs; if query_knowledge does not return a block ID, it does not exist.
When the user asks a question, answer concisely: lead with the direct answer, then add only the context needed to act on it.
Do not use LaTeX or TeX math notation in chat replies; write math inline in plain text (e.g. `350 microHz`, `f^2`, `x_i`).

```

## User Prompt

```text
I'm learning GNU Radio. Use `query_knowledge` with the **docs** domain to explain what a 'stream tag' is and how tags move through a flowgraph. Summarize what the documentation says. Don't change the graph.
```

## Flowgraph: BEFORE

```yaml
options:
  parameters:
    author: Example
    category: Custom
    cmake_opt: ''
    comment: ''
    copyright: ''
    description: example flow graph
    gen_cmake: 'On'
    gen_linking: dynamic
    generate_options: qt_gui
    hier_block_src_path: '.:'
    id: dial_tone
    max_nouts: '0'
    output_language: python
    placement: (0,0)
    qt_qss_theme: ''
    realtime_scheduling: ''
    run: 'True'
    run_command: '{python} -u {filename}'
    run_options: prompt
    sizing_mode: fixed
    thread_safe_setters: ''
    title: Dial Tone
    window_size: 1280, 1024
  states:
    coordinate: [16, 12.0]
    rotation: 0
    state: enabled

blocks:
- name: ampl
  id: variable_qtgui_range
  parameters:
    comment: ''
    gui_hint: 0, 0, 1, 2
    label: Volume
    min_len: '200'
    orient: Qt.Horizontal
    rangeType: float
    start: '0'
    step: '.001'
    stop: '.5'
    value: '.4'
    widget: counter_slider
  states:
    coordinate: [816, 12.0]
    rotation: 0
    state: enabled
- name: noise
  id: variable_qtgui_range
  parameters:
    comment: ''
    gui_hint: 1, 0, 1, 2
    label: Noise Amplitude
    min_len: '200'
    orient: Qt.Horizontal
    rangeType: float
    start: '0'
    step: '.001'
    stop: '.2'
    value: '0.005'
    widget: counter_slider
  states:
    coordinate: [680, 12.0]
    rotation: 0
    state: enabled
- name: samp_rate
  id: variable
  parameters:
    comment: ''
    value: '32000'
  states:
    coordinate: [208, 12.0]
    rotation: 0
    state: enabled
- name: analog_noise_source_x_0
  id: analog_noise_source_x
  parameters:
    affinity: ''
    alias: ''
    amp: noise
    comment: ''
    maxoutbuf: '0'
    minoutbuf: '0'
    noise_type: analog.GR_GAUSSIAN
    seed: '-42'
    type: float
  states:
    coordinate: [312, 252.0]
    rotation: 0
    state: enabled
- name: analog_sig_source_x_0
  id: analog_sig_source_x
  parameters:
    affinity: ''
    alias: ''
    amp: ampl
    comment: ''
    freq: '350'
    maxoutbuf: '0'
    minoutbuf: '0'
    offset: '0'
    samp_rate: samp_rate
    type: float
    waveform: analog.GR_COS_WAVE
  states:
    coordinate: [320, 12.0]
    rotation: 0
    state: enabled
- name: analog_sig_source_x_1
  id: analog_sig_source_x
  parameters:
    affinity: ''
    alias: ''
    amp: ampl
    comment: ''
    freq: '440'
    maxoutbuf: '0'
    minoutbuf: '0'
    offset: '0'
    samp_rate: samp_rate
    type: float
    waveform: analog.GR_COS_WAVE
  states:
    coordinate: [320, 124.0]
    rotation: 0
    state: enabled
- name: audio_sink
  id: audio_sink
  parameters:
    affinity: ''
    alias: ''
    comment: ''
    device_name: ''
    num_inputs: '1'
    ok_to_block: 'True'
    samp_rate: '32000'
  states:
    coordinate: [800, 156.0]
    rotation: 0
    state: enabled
- name: blocks_add_xx
  id: blocks_add_xx
  parameters:
    affinity: ''
    alias: ''
    comment: ''
    maxoutbuf: '0'
    minoutbuf: '0'
    num_inputs: '3'
    type: float
    vlen: '1'
  states:
    coordinate: [600, 128.0]
    rotation: 0
    state: enabled

connections:
- [analog_noise_source_x_0, '0', blocks_add_xx, '2']
- [analog_sig_source_x_0, '0', blocks_add_xx, '0']
- [analog_sig_source_x_1, '0', blocks_add_xx, '1']
- [blocks_add_xx, '0', audio_sink, '0']

metadata:
  file_format: 1

```

## Tool calls (raw inputs + outputs the model saw)

### call 1 — `query_knowledge`

**args (model sent):**

```json
"{\"query\":\"what is a stream tag and how does it move through a flowgraph\",\"domain\":\"docs\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "question": "what is a stream tag and how does it move through a flowgraph", "answer": "**What is a stream tag?**\nA stream tag is an isosynchronous data stream that runs parallel to the main data stream, used to attach metadata to specific items (`Stream_Tags.md`). Each tag is linked to a particular item via an absolute count and consists of:\n- `offset`: The unique absolute item count in the stream (`BlocksCodingGuide.md`)\n- `key`: A PMT symbol identifying the tag's type (`Stream_Tags.md`, `BlocksCodingGuide.md`)\n- `value`: A PMT holding the actual metadata data (`Stream_Tags.md`, `BlocksCodingGuide.md`)\n- `srcid` (optional): A PMT symbol identifying the block that created the tag (`Stream_Tags.md`, `BlocksCodingGuide.md`)\n\n**How does it move through a flowgraph?**\n- Tags are generated by a block's work function and flow downstream alongside a specific sample until they reach a sink or are explicitly stopped by another block (`Stream_Tags.md`).\n- They remain attached to their original absolute item position as they pass through processing blocks (e.g., tags stay at the same positions even after data is combined or processed) (`Stream_Tags.md` Example Flowgraph).\n- Downstream blocks interact with them using `get_tags_in_range()` to read and `add_item_tag()` to write within their work functions (`BlocksCodingGuide.md`).\n\n*Coverage note:* The documentation explains basic downstream propagation and absolute-position attachment but does not detail advanced routing mechanics, tag merging at multi-input blocks, or specific conditions under which tags are dropped beyond reaching a sink or being forced to stop.", "sources": [{"path": "Stream_Tags.md", "distance": 0.833}, {"path": "Stream_Tags.md", "distance": 0.897}, {"path": "Stream_Tags.md", "distance": 0.912}, {"path": "BlocksCodingGuide.md", "distance": 0.929}, {"path": "Stream_Tags.md", "distance": 0.936}]}
```

## Final result (raw)

```json
{
  "ok": true,
  "assistant_text": "Based on the documentation, here is a summary of **stream tags** and how they work:\n\n### What is a Stream Tag?\nA stream tag is essentially metadata attached to a specific item in the data stream (like a sample). It runs parallel to your actual data but carries extra information about that specific point.\n\nEvery tag consists of four components:\n*   **Offset:** The absolute count (item number) of where the tag occurs in the stream.\n*   **Key:** A unique identifier (a symbol) describing what kind of tag it is.\n*   **Value:** The actual data associated with that tag (usually stored as a PMT).\n*   **Source ID (srcid):** Optional metadata identifying which block created the tag.\n\n### How Do They Move?\n*   **Downstream Flow:** Once a block \"writes\" a tag, it flows downstream towards the sink (your output device or file).\n*   **Absolute Positioning:** Tags stay attached to their specific `offset` as they pass through other blocks. Even if data is processed or combined, the offset relative to the stream remains constant.\n*   **Lifecycle:** Tags naturally stop when they reach a sink. If you want to stop a tag earlier, another block in the middle of the flow would explicitly handle it.\n\n### Interacting with Them\nBlocks use two main methods to interact with tags within their work functions:\n1.  `get_tags_in_range()`: Used to read existing tags currently affecting the data being processed.\n2.  `add_item_tag()`: Used to write or create new tags that will flow downstream.",
  "expect_reason": "ok"
}
```
