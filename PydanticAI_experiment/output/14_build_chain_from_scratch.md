# 14 Build Chain From Scratch

**Scenario:** `14_build_chain_from_scratch` | **Fixture:** `empty.grc` | **Model:** `qwen3.6:35b-a3b-q4_K_M`

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
Inspect the flowgraph — right now it's empty except for the samp_rate variable. Build a minimal signal chain: a signal source called `sig` (type float, freq 1000, amp 0.5, using samp_rate), a throttle called `throttle` (type float, samples_per_second using samp_rate), and a null sink called `sink` (type float). Wire the source into the throttle, and the throttle into the sink. Inspect to confirm the chain is valid.
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
    description: empty flow graph
    gen_cmake: 'On'
    gen_linking: dynamic
    generate_options: qt_gui
    hier_block_src_path: '.:'
    id: empty
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
    title: Empty Graph
    window_size: 1280, 1024
  states:
    coordinate: [16, 12.0]
    rotation: 0
    state: enabled

blocks:
- name: samp_rate
  id: variable
  parameters:
    comment: ''
    value: '32000'
  states:
    coordinate: [208, 12.0]
    rotation: 0
    state: enabled

connections: []

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
{"ok": true, "graph": {"graph_name": "empty", "blocks": [{"instance_name": "empty", "block_id": "options", "role": "options", "state": "enabled", "params": {"title": "Empty Graph", "author": "Example", "description": "empty flow graph", "output_language": "python", "generate_options": "qt_gui"}, "inputs": [], "outputs": []}, {"instance_name": "samp_rate", "block_id": "variable", "role": "variable", "state": "enabled", "params": {"value": "32000"}, "inputs": [], "outputs": []}], "connections": [], "validation": {"status": "valid", "errors": []}}}
```

### call 2 — `query_knowledge`

**args (model sent):**

```json
"{\"domain\":\"catalog\",\"query\":\"signal source float analog sig source\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "query": "signal source float analog sig source", "results": [{"block_id": "analog_sig_source_x", "label": "Signal Source", "category": "Core > Waveform Generators", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "samp_rate": "[real]=samp_rate", "waveform": "[raw]=analog.GR_COS_WAVE", "freq": "[real]=1000", "amp": "[real]=1", "offset": "[complex]=0", "phase": "[real]=0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 0.917}, {"block_id": "analog_const_source_x", "label": "Constant Source", "category": "Core > Waveform Generators", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "const": "[complex]=0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 0.973}, {"block_id": "analog_noise_source_x", "label": "Noise Source", "category": "Core > Waveform Generators", "params": {"type": "enum=[complex,float,int,short]=complex", "noise_type": "[raw]=analog.GR_GAUSSIAN", "amp": "[real]=1", "seed": "[int]=0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 1.027}, {"block_id": "blocks_sigmf_source_minimal", "label": "SigMF Source (Minimal)", "category": "Core > File Operators", "params": {"file": "[file_open]=", "type": "enum=[complex float (fc32_le),real float (rc32_le),complex short (sc16_le),real short (rc16_le)]=complex float (fc32_le)", "repeat": "[bool]=True", "begin_tag": "[raw]=pmt.PMT_NIL", "length": "[int]=0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 1.056}, {"block_id": "analog_phase_modulator_fc", "label": "Phase Mod", "category": "Core > Modulators", "params": {"sensitivity": "[real]="}, "inputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 1.06}], "output_truncated": true}
```

### call 3 — `query_knowledge`

**args (model sent):**

```json
"{\"domain\":\"catalog\",\"query\":\"throttle block samples per second\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "query": "throttle block samples per second", "results": [{"block_id": "blocks_throttle2", "label": "Throttle", "category": "Core > Misc", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "samples_per_second": "[real]=samp_rate", "vlen": "[int]=1", "ignoretag": "[bool]=True", "limit": "enum=[auto,time,items]=auto"}, "inputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 0.752}, {"block_id": "blocks_throttle", "label": "Throttle (old)", "category": "Core > Misc", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "samples_per_second": "[real]=samp_rate", "vlen": "[int]=1", "ignoretag": "[bool]=True"}, "inputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 0.785}, {"block_id": "analog_standard_squelch", "label": "Standard Squelch", "category": "Core > Level Controllers", "params": {"audio_rate": "[real]=", "threshold": "[real]="}, "inputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "distance": 0.968}, {"block_id": "blocks_sample_and_hold_xx", "label": "Sample and Hold", "category": "Core > Level Controllers", "params": {"type": "enum=[float,int,short,byte]=float"}, "inputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}, {"port_id": "1", "dtype": "byte", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "distance": 0.986}, {"block_id": "blocks_vco_f", "label": "VCO", "category": "Core > Waveform Generators", "params": {"samp_rate": "[real]=", "sensitivity": "[real]=", "amplitude": "[real]="}, "inputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "outputs": [{"port_id": "0", "dtype": "float", "domain": "stream"}], "distance": 1.037}], "output_truncated": true}
```

### call 4 — `query_knowledge`

**args (model sent):**

```json
"{\"domain\":\"catalog\",\"query\":\"null sink float\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "query": "null sink float", "results": [{"block_id": "blocks_null_sink", "label": "Null Sink", "category": "Core > Misc", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "vlen": "[int]=1", "num_inputs": "[int]=1"}, "inputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "outputs": [], "distance": 0.852}, {"block_id": "uhd_rfnoc_null_src_sink", "label": "RFNoC Null Source Sink Block", "category": "Core > UHD > RFNoC > Blocks", "params": {"btype": "enum=[sink,source,both]=sink", "block_args": "[string]=", "device_select": "[int]=-1", "instance_index": "[int]=-1"}, "inputs": [{"port_id": "0", "dtype": "sc16", "domain": "rfnoc"}], "outputs": [], "distance": 0.989}, {"block_id": "blocks_null_source", "label": "Null Source", "category": "Core > Misc", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "vlen": "[int]=1", "num_outputs": "[int]=1"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "distance": 0.993}, {"block_id": "pad_sink", "label": "Pad Sink", "category": "Core > Misc", "params": {"label": "[string]=out", "type": "enum=[complex,float,int,short,byte,bit,message,]=complex", "vlen": "[int]=1", "num_streams": "[int]=1", "optional": "[bool]=False"}, "inputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "outputs": [], "distance": 0.996}, {"block_id": "blocks_vector_sink_x", "label": "Vector Sink", "category": "Core > Debug Tools", "params": {"type": "enum=[complex,float,int,short,byte]=complex", "vlen": "[int]=1", "reserve_items": "[int]=1024"}, "inputs": [{"port_id": "0", "dtype": "complex", "domain": "stream"}], "outputs": [], "distance": 1.033}], "output_truncated": true}
```

### call 5 — `change_graph`

**args (model sent):**

```json
"{\"add_blocks\":[{\"block_id\":\"analog_sig_source_x\",\"instance_name\":\"sig\",\"params\":{\"amp\":\"0.5\",\"freq\":\"1000\",\"offset\":\"0\",\"phase\":\"0\",\"samp_rate\":\"samp_rate\",\"type\":\"float\",\"waveform\":\"analog.GR_SIN_WAVE\"},\"state\":\"enabled\"},{\"block_id\":\"blocks_throttle2\",\"instance_name\":\"throttle\",\"params\":{\"samples_per_second\":\"samp_rate\",\"type\":\"float\",\"vlen\":\"1\"},\"state\":\"enabled\"},{\"block_id\":\"blocks_null_sink\",\"instance_name\":\"sink\",\"params\":{\"num_inputs\":\"1\",\"type\":\"float\",\"vlen\":\"1\"},\"state\":\"enabled\"}],\"add_connections\":[\"sig 0 -\\u003e throttle 0\",\"throttle 0 -\\u003e sink 0\"]}"
```

**result (model saw this exact string):**

```json
{"ok": false, "errors": [{"code": "invalid_connection_format", "message": "Unparseable connection format: sig 0 -> throttle 0"}, {"code": "invalid_connection_format", "message": "Unparseable connection format: throttle 0 -> sink 0"}]}
```

### call 6 — `change_graph`

**args (model sent):**

```json
"{\"add_blocks\":[{\"block_id\":\"analog_sig_source_x\",\"instance_name\":\"sig\",\"params\":{\"amp\":\"0.5\",\"freq\":\"1000\",\"offset\":\"0\",\"phase\":\"0\",\"samp_rate\":\"samp_rate\",\"type\":\"float\",\"waveform\":\"analog.GR_SIN_WAVE\"},\"state\":\"enabled\"},{\"block_id\":\"blocks_throttle2\",\"instance_name\":\"throttle\",\"params\":{\"samples_per_second\":\"samp_rate\",\"type\":\"float\",\"vlen\":\"1\"},\"state\":\"enabled\"},{\"block_id\":\"blocks_null_sink\",\"instance_name\":\"sink\",\"params\":{\"num_inputs\":\"1\",\"type\":\"float\",\"vlen\":\"1\"},\"state\":\"enabled\"}],\"add_connections\":[\"sig:0-\\u003ethrottle:0\",\"throttle:0-\\u003esink:0\"]}"
```

**result (model saw this exact string):**

```json
{"ok": true}
```

### call 7 — `inspect_graph`

**args (model sent):**

```json
"{\"view\":\"overview\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "graph": {"graph_name": "empty", "blocks": [{"instance_name": "empty", "block_id": "options", "role": "options", "state": "enabled", "params": {"title": "Empty Graph", "author": "Example", "description": "empty flow graph", "output_language": "python", "generate_options": "qt_gui"}, "inputs": [], "outputs": []}, {"instance_name": "samp_rate", "block_id": "variable", "role": "variable", "state": "enabled", "params": {"value": "32000"}, "inputs": [], "outputs": []}, {"instance_name": "sig", "block_id": "analog_sig_source_x", "role": "source", "state": "enabled", "params": {"type": "float", "samp_rate": "samp_rate", "waveform": "analog.GR_SIN_WAVE", "freq": "1000", "amp": "0.5", "offset": "0", "phase": "0"}, "inputs": [], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "throttle", "block_id": "blocks_throttle2", "role": "transform", "state": "enabled", "params": {"type": "float", "samples_per_second": "samp_rate", "limit": "auto"}, "inputs": [{"port_id": "0", "dtype": "float"}], "outputs": [{"port_id": "0", "dtype": "float"}]}, {"instance_name": "sink", "block_id": "blocks_null_sink", "role": "sink", "state": "enabled", "params": {"type": "float"}, "inputs": [{"port_id": "0", "dtype": "float"}], "outputs": []}], "connections": ["throttle:0->sink:0", "sig:0->throttle:0"], "validation": {"status": "valid", "errors": []}}}
```

## Final result (raw)

```json
{
  "ok": true,
  "assistant_text": "The flowgraph has been built and validated successfully. Here's a summary:\n\n**Blocks added:**\n\n1. **`sig`** (`analog_sig_source_x`) \u2014 Signal Source\n   - `type`: float\n   - `freq`: 1000\n   - `amp`: 0.5\n   - `samp_rate`: samp_rate (references the variable)\n   - `waveform`: analog.GR_SIN_WAVE\n\n2. **`throttle`** (`blocks_throttle2`) \u2014 Throttle\n   - `type`: float\n   - `samples_per_second`: samp_rate (uses the variable, originally 32000)\n\n3. **`sink`** (`blocks_null_sink`) \u2014 Null Sink\n   - `type`: float\n\n**Connections:**\n- `sig:0 \u2192 throttle:0`\n- `throttle:0 \u2192 sink:0`\n\n**Validation status:** \u2705 valid (no errors)\n\nThe minimal signal chain is complete and ready to run.",
  "expect_reason": "ok"
}
```
