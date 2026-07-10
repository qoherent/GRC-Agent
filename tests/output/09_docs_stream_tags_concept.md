# 09 Docs Stream Tags Concept

**Scenario:** `09_docs_stream_tags_concept` | **Fixture:** `dial_tone.grc` | **Model:** `qwen3.6:35b-a3b-q4_K_M`

## System Prompt

```text
Session ID: pai-experiment
Role: GNU Radio graph editing assistant.
inspect_graph: read topology, blocks, connections, field values, and validation status. Pass a targets list of block instance names to scope it to those blocks instead of the whole graph.
query_knowledge: search catalog blocks or GNU Radio documentation.
change_graph: add/remove blocks, edit field values, add/remove connections.
Parameter values are string expressions; a variable reference is simply the variable's name (e.g. use 'base_freq * 1.5', NOT 'vars.base_freq * 1.5').
Set a type-controlling parameter (e.g. 'type', 'itype', 'otype') to the literal value 'auto' to resolve it from a connected neighbor's dtype instead of guessing a value.
Stream-port connections use numeric port keys (e.g. '0', '1', '2'), not names like 'out', 'in(0)', or 'in0'. GRC error messages like 'in(0)' refer to port index '0'. Message ports are the exception: they use their exact declared string identifier (e.g. 'pdus', 'msg') instead of a numeric index.
Connection strings must use the exact format 'src_block:src_port->dst_block:dst_port' (e.g. 'source_0:0->sink_0:0').
Do not attempt to rename blocks by changing the 'id' parameter in update_params; changing a block's ID is not supported and will be ignored. To rename a block, you must remove it and add a new one.
Variables are blocks; use block_id "variable" (not "parameter") to add one.
Every GNU Radio fact must be grounded in query_knowledge, not memory.
Ensure the final state of the flowgraph is valid. The environment automatically validates the graph state when you complete a turn and will raise a validation error if invalid.
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
"{\"query\":\"stream tag definition and how they move through a flowgraph\",\"domain\":\"docs\"}"
```

**result (model saw this exact string):**

```json
{"ok": true, "query": "stream tag definition and how they move through a flowgraph", "answer": "path: index.php_Guided_Tutorial_Programming_Topics\nheading: Stream Tags\n<merged with Stream Tags usage manual page>\n\n---\n\npath: Stream_Tags\nheading: Introduction\nStream tags are an isosynchronous data stream that runs parallel to the main data stream. A stream _tag_ is generated by a block's work function and from there on flows downstream alongside a particular sample, until it reaches a sink or is forced to stop propagating by another block. \nStream tags are defined for a specific item in the data stream and are formed as a key:value pair. The _key_ identifies what the _value_ represents while the value holds the data that the tag contains. Both _key_ and _value_ are [Polymorphic Types (PMTs)](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\\(PMTs\\) \"Polymorphic Types \\(PMTs\\)\") where the _key_ is a PMT symbol while the _value_ is any type of PMT and can therefore handle any data we wish to pass. An additional part of the tag is the _srcid_ , which is a PMT symbol and is used to identify the block that created the tag (which is usually the block's alias).\n\n---\n\npath: Stream_Tags\nheading: API Extensions to the gr::block\nTo enable the stream tags, we have extended the API of gr::block to understand _absolute_ item numbers. In the data stream model, each block's work function is given a buffer in the data stream that is referenced from 0 to N-1. This is a _relative_ offset into the data stream. The absolute reference starts from the beginning of the flowgraph and continues to count up with every item. Each input stream is associated with a concept of the 'number of items read' and each output stream has a 'number of items written'. These are retrieved during runtime using the two API calls: \n\n```\nunsigned long int nitems_read(unsigned int which_input);\nunsigned long int nitems_written(unsigned int which_output);\n\n```\n\nEach tag is associated with some item in this absolute time scale that is calculated using these functions. \nLike the rest of the data stream, the number of items read/written are only updated once during the call to work. So in a work function, nitems_read/written will refer to the state of the data stream at the start of the work function. We must therefore add to this value the current relative offset in the data stream. So if we are iterating _i_ over all output items, we would write the stream tag to output ports at nitems_written(0)+i for the 0th output port.\n\n---\n\npath: Stream_Tags\nheading: Example Flowgraph\nLet's have a look at a simple example: \n[![tut5_tagstest_fg.png](https://wiki.gnuradio.org/images/thumb/7/76/Tut5_tagstest_fg.png/600px-Tut5_tagstest_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Tut5_tagstest_fg.png \"tut5_tagstest_fg.png\")\nIn this flow graph, we have two sources: A sinusoid and a tag strobe. A tag strobe is a block that will output a constant tag, in this case, on every 1000th item (the actual value of the items is always zero). Those sources get added up. The signal after the adder is identical to the sine wave we produced, because we are always adding zeros. However, the tags stay attached to the same position as they were coming from the tag strobe! This means every 1000th sample of the sinusoid now has a tag. The QT scope can display tags, and even trigger on them. \n[![tut5_tagstest_scope.png](https://wiki.gnuradio.org/images/thumb/d/da/Tut5_tagstest_scope.png/500px-Tut5_tagstest_scope.png)](https://wiki.gnuradio.org/index.php?title=File:Tut5_tagstest_scope.png \"tut5_tagstest_scope.png\")  \n\nWe now have a mechanism to randomly attach any metadata to specific items. There are several blocks that use tags. One of them is the UHD Sink block, the driver used for transmitting with USRP devices. It will react to tags with certain keys, one of them being `tx_freq`, which can be used to set the transmit frequency of a USRP while streaming. \n### Adding tags to the QPSK demodulator\nGoing back to our [QPSK demodulation example](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_PSK_Demodulation \"Guided Tutorial PSK Demodulation\"), we might want to add a feature to tell downstream blocks that the demodulation is not going well. Remember the output of our block is always hard-decision, and we have to output something. So we could use tags to notify that the input is not well formed, and that the output is not reliable. \nAs a failure criterion, we discuss the case where the input amplitude is too small, say smaller than 0.01. When the amplitude drops below this value, we output one tag. Another tag is only sent when the amplitude has recovered, and rises back above the threshold. We extend our work function like this: \n\n```\nif (std::abs(in[i]) < 0.01 and not d_low_ampl_state) {\n    add_item_tag(0, // Port number\n                 nitems_written(0) + i, // Offset\n                 pmt::mp(\"amplitude_warning\"), // Key\n                 pmt::from_double(std::abs(in[i])) // Value\n    );\n    d_low_ampl_state = true;\n}\nelse if (std::abs(in[i]) >= 0.01 and d_low_ampl_state) {\n    add_item_tag(0, // Port number\n        nitems_written(0) + i, // Offset\n        pmt::mp(\"amplitude_recovered\"), // Key\n        pmt::PMT_T // Value\n    );\n    d_low_ampl_state = false; // Reset state\n}\n```\n\nIn Python, the code would look like this (assuming we have a member of our block class called `d_low_ampl_state`): \n\n```\n\n---\n\npath: Stream_Tags\nheading: Contents\n* [1 Introduction](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Introduction)\n  * [2 Background](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Background)\n  * [3 API Extensions to the gr::block](https://wiki.gnuradio.org/index.php?title=Stream_Tags#API_Extensions_to_the_gr::block)\n  * [4 Stream Tags API](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Stream_Tags_API)\n    * [4.1 Adding a Tag to a Stream](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Adding_a_Tag_to_a_Stream)\n    * [4.2 Getting tags from a Stream](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Getting_tags_from_a_Stream)\n  * [5 Tag Propagation](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Tag_Propagation)\n  * [6 Notes on How to Use Tags](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Notes_on_How_to_Use_Tags)\n  * [7 Example Flowgraph](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Example_Flowgraph)\n    * [7.1 Adding tags to the QPSK demodulator](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Adding_tags_to_the_QPSK_demodulator)\n  * [8 Use case: FIR filters](https://wiki.gnuradio.org/index.php?title=Stream_Tags#Use_case:_FIR_filters)"}
```

## Final result (raw)

```json
{
  "ok": true,
  "assistant_text": "Based on the GNU Radio documentation, here is a summary of stream tags and how they move through a flowgraph:\n\n**What are Stream Tags?**\nStream tags act as an invisible, \"isosynchronous\" companion stream that runs parallel to your actual data (samples). They allow you to attach metadata directly to specific items within a sample stream rather than changing the samples themselves. Structurally, each tag consists of:\n*   A **Key**: A PMT symbol identifying what the data is (e.g., `tx_freq`, `amplitude_warning`).\n*   A **Value**: Any PMT type holding the actual metadata.\n*   A **Source ID (`srcid`)**: Identifies which block created the tag.\n\n**How Tags Move Through a Flowgraph:**\n1.  **Creation:** A block generates a tag in its `work()` function by associating the key:value pair with an *absolute item index* (calculated using `nitems_written()` plus the current relative offset).\n2.  **Propagation:** Once created, the tag flows downstream alongside the specific sample it is attached to. It passes through intermediate blocks exactly as if it were metadata attached to that exact timing position in the data stream.\n3.  **Termination:** Tags naturally reach a **Sink** block (where they can be read/acted upon) or are explicitly caught and consumed by an intermediate block using tag management APIs (`add_item_tag`, `get_tags_in_range`, etc.). A block can also actively choose to stop a tag from propagating further.\n4.  **Use Cases:** They are primarily used for sideband control or telemetry\u2014such as telling a USRP transmit block to change its frequency mid-stream, triggering an oscilloscope visualization at specific events, or passing warning/error states (like loss of signal) alongside the data.\n\nThe diagram currently in your workspace has not been changed by this query.",
  "expect_reason": "ok"
}
```
