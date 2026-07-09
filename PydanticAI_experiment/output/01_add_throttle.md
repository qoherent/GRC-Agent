# 01 Add Throttle

**Scenario:** `01_add_throttle` | **Fixture:** `dial_tone.grc` | **Model:** `qwen3.6:35b-a3b-q4_K_M`

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
Take a look at the flowgraph, then add a throttle block in the path between the 350 Hz tone and the adder that mixes the tones together. Call it `mid_throttle`, set its type to float, and have it use the samp_rate variable for its rate. Make sure the wiring is rerouted so it actually sits inline. Then inspect the result to confirm.
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

### call 1 — `inspect_graph`

**args (model sent):**

```json
"{\"view\":\"overview\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "graph": {"graph_name": "dial_tone", "blocks": [{"instance_name": "dial_tone", "block_id": "options", "role": "options", "state": "enabled", "params": {"title": "Dial Tone", "author": "Example", "description": "example flow graph", "output_language": "python", "generate_options": "qt_gui"}, "inputs": [], "outputs": []}, {"instance_name": "ampl", "block_id": "variable_qtgui_range", "role": "variable", "state": "enabled", "params": {"label": "Volume", "value": ".4", "start": "0", "stop": ".5", "step": ".001"}, "inputs": [], "outputs": []}, {"instance_name": "noise", "block_id": "variable_qtgui_range", "role": "variable", "state": "enabled", "params": {"label": "Noise Amplitude", "value": "0.005", "start": "0", "stop": ".2", "step": ".001"}, "inputs": [], "outputs": []}, {"instance_name": "samp_rate", "block_id": "variable", "role": "variable", "state": "enabled", "params": {"value": "32000"}, "inputs": [], "outputs": []}, {"instance_name": "analog_noise_source_x_0", "block_id": "analog_noise_source_x", "role": "source", "state": "enabled", "params": {"type": "float", "noise_type": "analog.GR_GAUSSIAN", "amp": "noise", "seed": "-42"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "analog_sig_source_x_0", "block_id": "analog_sig_source_x", "role": "source", "state": "enabled", "params": {"type": "float", "samp_rate": "samp_rate", "waveform": "analog.GR_COS_WAVE", "freq": "350", "amp": "ampl", "offset": "0", "phase": "0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "analog_sig_source_x_1", "block_id": "analog_sig_source_x", "role": "source", "state": "enabled", "params": {"type": "float", "samp_rate": "samp_rate", "waveform": "analog.GR_COS_WAVE", "freq": "440", "amp": "ampl", "offset": "0", "phase": "0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "audio_sink", "block_id": "audio_sink", "role": "sink", "state": "enabled", "params": {"samp_rate": "32000"}, "inputs": [{"port_id": "0", "dtype": "float"}], "outputs": []}, {"instance_name": "blocks_add_xx", "block_id": "blocks_add_xx", "role": "transform", "state": "enabled", "params": {"type": "float", "num_inputs": "3"}, "inputs": [{"port_id": "0", "dtype": "float"}, {"port_id": "1", "dtype": "float"}, {"port_id": "2", "dtype": "float"}], "outputs": [{"port_id": "0", "dtype": "float"}]}], "connections": ["analog_sig_source_x_1:0->blocks_add_xx:1", "blocks_add_xx:0->audio_sink:0", "analog_noise_source_x_0:0->blocks_add_xx:2", "analog_sig_source_x_0:0->blocks_add_xx:0"], "validation": {"status": "valid", "errors": []}}}
```

### call 2 — `query_knowledge`

**args (model sent):**

```json
"{\"domain\":\"catalog\",\"query\":\"blocks_throttle parameters\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "query": "blocks_throttle parameters", "results": [{"block_id": "blocks_throttle2", "label": "Throttle", "category": "Core > Misc", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "samples_per_second": "[real]=samp_rate", "vlen": "[int]=1", "ignoretag": "[bool]=True", "limit": "enum=[auto,time,items]=auto"}, "inputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 0.756}, {"block_id": "blocks_throttle", "label": "Throttle (old)", "category": "Core > Misc", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "samples_per_second": "[real]=samp_rate", "vlen": "[int]=1", "ignoretag": "[bool]=True"}, "inputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 0.788}, {"block_id": "analog_simple_squelch_cc", "label": "Simple Squelch", "category": "Core > Level Controllers", "params": {"threshold": "[real]=", "alpha": "[real]="}, "inputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 0.966}, {"block_id": "analog_standard_squelch", "label": "Standard Squelch", "category": "Core > Level Controllers", "params": {"audio_rate": "[real]=", "threshold": "[real]="}, "inputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "distance": 0.97}, {"block_id": "blocks_threshold_ff", "label": "Threshold", "category": "Core > Level Controllers", "params": {"low": "[real]=-100", "high": "[real]=100", "init": "[real]=0"}, "inputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "distance": 0.984}], "output_truncated": true}
```

### call 3 — `change_graph`

**args (model sent):**

```json
"{\"add_blocks\":[{\"block_id\":\"blocks_throttle2\",\"instance_name\":\"mid_throttle\",\"params\":{\"samples_per_second\":\"samp_rate\",\"type\":\"float\"},\"state\":\"enabled\"}],\"remove_connections\":[\"analog_sig_source_x_0:0-\\u003eblocks_add_xx:0\"],\"add_connections\":[\"analog_sig_source_x_0:0-\\u003emid_throttle:0\",\"mid_throttle:0-\\u003eblocks_add_xx:0\"]}"
```

**result (model saw this exact string):**

```json
{"ok": true}
```

### call 4 — `inspect_graph`

**args (model sent):**

```json
"{\"view\":\"overview\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "graph": {"graph_name": "dial_tone", "blocks": [{"instance_name": "dial_tone", "block_id": "options", "role": "options", "state": "enabled", "params": {"title": "Dial Tone", "author": "Example", "description": "example flow graph", "output_language": "python", "generate_options": "qt_gui"}, "inputs": [], "outputs": []}, {"instance_name": "ampl", "block_id": "variable_qtgui_range", "role": "variable", "state": "enabled", "params": {"label": "Volume", "value": ".4", "start": "0", "stop": ".5", "step": ".001"}, "inputs": [], "outputs": []}, {"instance_name": "noise", "block_id": "variable_qtgui_range", "role": "variable", "state": "enabled", "params": {"label": "Noise Amplitude", "value": "0.005", "start": "0", "stop": ".2", "step": ".001"}, "inputs": [], "outputs": []}, {"instance_name": "samp_rate", "block_id": "variable", "role": "variable", "state": "enabled", "params": {"value": "32000"}, "inputs": [], "outputs": []}, {"instance_name": "analog_noise_source_x_0", "block_id": "analog_noise_source_x", "role": "source", "state": "enabled", "params": {"type": "float", "noise_type": "analog.GR_GAUSSIAN", "amp": "noise", "seed": "-42"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "analog_sig_source_x_0", "block_id": "analog_sig_source_x", "role": "source", "state": "enabled", "params": {"type": "float", "samp_rate": "samp_rate", "waveform": "analog.GR_COS_WAVE", "freq": "350", "amp": "ampl", "offset": "0", "phase": "0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "analog_sig_source_x_1", "block_id": "analog_sig_source_x", "role": "source", "state": "enabled", "params": {"type": "float", "samp_rate": "samp_rate", "waveform": "analog.GR_COS_WAVE", "freq": "440", "amp": "ampl", "offset": "0", "phase": "0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "audio_sink", "block_id": "audio_sink", "role": "sink", "state": "enabled", "params": {"samp_rate": "32000"}, "inputs": [{"port_id": "0", "dtype": "float"}], "outputs": []}, {"instance_name": "blocks_add_xx", "block_id": "blocks_add_xx", "role": "transform", "state": "enabled", "params": {"type": "float", "num_inputs": "3"}, "inputs": [{"port_id": "0", "dtype": "float"}, {"port_id": "1", "dtype": "float"}, {"port_id": "2", "dtype": "float"}], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "mid_throttle", "block_id": "blocks_throttle2", "role": "transform", "state": "enabled", "params": {"type": "float", "samples_per_second": "samp_rate", "limit": "auto"}, "inputs": [{"port_id": "0", "dtype": "float"}], "outputs": [{"port_id": "0", "dtype": "float"}]}], "connections": ["analog_sig_source_x_0:0->mid_throttle:0", "blocks_add_xx:0->audio_sink:0", "analog_sig_source_x_1:0->blocks_add_xx:1", "mid_throttle:0->blocks_add_xx:0", "analog_noise_source_x_0:0->blocks_add_xx:2"], "validation": {"status": "valid", "errors": []}}}
```

## Final result (raw)

```json
{
  "ok": true,
  "assistant_text": "Done! The `mid_throttle` block has been inserted inline with the 350 Hz tone path. Here's what was changed:\n\n**Added block:**\n- Instance name: `mid_throttle`\n- Block ID: `blocks_throttle2` (modern Throttle)\n- Type: `float`\n- samples_per_second: `samp_rate` (resolving to 32000 from the variable)\n\n**Rerouted connections:**\n1. Removed: `analog_sig_source_x_0:0 -> blocks_add_xx:0`\n2. Added: `analog_sig_source_x_0:0 -> mid_throttle:0`\n3. Added: `mid_throttle:0 -> blocks_add_xx:0`\n\n**Current signal flow through the adder input port 0:**\n```\nanalog_sig_source_x_0 (350 Hz) \u2192 mid_throttle \u2192 blocks_add_xx (port 0)\n```\n\nThe graph validation status is **valid** with no errors.",
  "expect_reason": "ok"
}
```
