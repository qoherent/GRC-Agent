# GNU Radio Tutorial Research & Agent Adaptation

**Context**: This document was generated during an autonomous "Fresh-Eyes" audit of the official GNU Radio Wiki tutorials (April 2026). 
**Purpose**: To extract "Expert Recipes," structural rules, and diagnostic heuristics from the official documentation and transform them into actionable enhancements for the `grc-agent` toolset and the Phase 7 evaluation suite.
**Methodology**: Tutorials were reviewed sequentially according to the official curriculum. Each section disassembles a tutorial to identify:
1. **Critical Findings**: Non-obvious GNU Radio behaviors or "best practices."
2. **Adaptations**: Specific ways the agent's code, tools, or prompts should change.
3. **Testable Scenarios**: High-value workflows for the `llama_eval` suite.

## Harness Boundary After The Audit

### Implemented In Python Preflight
1.  **Affected-edit structural revalidation**: after a staged transaction succeeds, the validator now re-checks only the blocks touched by the edit and their incident connections.
2.  **Vector-length enforcement**: stream/vector compatibility now includes `vlen`, not just domain and dtype.
3.  **Metadata-backed block asserts**: catalog `asserts` now participate in structural preflight when a touched block's parameters violate its own documented constraints.
4.  **Duplicate enabled identifiers**: staged edits now reject duplicate enabled parsed block names, which covers the common variable/widget shadowing failure mode when both definitions remain enabled.

### Kept Out Of Python Preflight On Purpose
1.  **DSP lockstep rules** such as constellation modulus vs `Unpack K Bits`, packet formatter/parser compatibility, OFDM carrier-map semantics, or FM/audio gain math remain prompt/eval guidance, not hardcoded Python rules.
2.  **Import alias assumptions** such as `np.arange(...)` requiring an `Import` block remain prompt guidance. Valid graphs may source symbols from other GNU Radio mechanisms.
3.  **Expression-proof removals** remain conservative. The harness does not try to prove that an identifier embedded in a string can never matter at runtime.
4.  **Virtual source/sink topology restrictions** were not added. GNU Radio permits broadcast-style virtual routing, so the harness should not overconstrain it.

### Tool Contract Outcome
1.  No new public tools were added from this audit.
2.  The improvement path was: leaner tool outputs, stronger structural preflight, richer expert recipes, and broader eval coverage.

---

## Section 1: Introducing GNU Radio & Flowgraph Fundamentals

### Critical Findings
1.  **Tutorial 1 is conceptual, not procedural**: `What Is GNU Radio?` establishes the mental model: GNU Radio is a modular DSP toolkit built from blocks and flowgraphs, usable with RF hardware or in pure simulation, but not a hardware-specific or standards-specific application by itself.
    *   *Harness Impact*: Section 1 should not attribute port-color troubleshooting, block disabling, SI-unit entry, or type-converter workflows to Tutorial 1. Those belong to later fundamentals pages and need separate source-backed audit before they stay in this section.
2.  **Block-family vocabulary is part of the user contract**: The tutorial teaches users to think in categories such as waveform generators, modulators, GUI sinks, filters, resamplers, and synchronizers.
    *   *Adaptation*: Tighten the `GrcAgent` system prompt examples in `src/grc_agent/agent.py` so requests for a block family or processing role keep routing through `search_grc(scope="catalog")` with category-shaped queries instead of speculative direct `describe_block` calls.
3.  **Simulation and hardware must stay distinct**: The page explicitly says GNU Radio can operate without hardware and does not, by itself, target any one SDR device or wireless standard.
    *   *Adaptation*: Add a concise prompt rule in `src/grc_agent/agent.py` that defaults conceptual or prototype requests to software blocks unless the user explicitly names hardware. This carries more weight than inventing a new tool.
4.  **Section 1 must separate real gaps from unsupported or already-covered ideas**: The current draft proposes `toggle_block_state`, but `GrcAgent.execute_tool()` only dispatches the fixed registry declared in `agent.py`, and runtime schema validation rejects undeclared tool names before execution. By contrast, one supposed Section 1 enhancement is already present: the system prompt already expands common rate abbreviations such as `32k` to `32000`.
    *   *Adaptation*: Remove Section 1 claims that treat unsupported tools or already-implemented prompt behavior as fresh discoveries. Keep only changes that clearly require either a prompt edit or a new Python implementation.
5.  **The validator is narrower than the draft implies, and Tutorial 1 does not justify widening it yet**: `preflight_transaction()` plus `validation/checks.py` validate transaction shape, block existence, parameter IDs, port occupancy, and stream/message compatibility. They do not semantically validate DSP math, Python expressions, or GUI affordances, and Tutorial 1 provides no concrete recipe that would justify a new preflight rule.
    *   *Adaptation*: Keep this section focused on prompt rules and eval scenarios. Heavier executable validation should be derived from later fundamentals tutorials that specify concrete block settings or graph mutations.

### Testable Scenarios for Phase 7
*   `Scenario_Conceptual_Boundary`: User asks whether GNU Radio is a ready-made LTE or FM application. Agent must answer that GNU Radio is a toolkit/framework, avoid hallucinating out-of-the-box support, and avoid unnecessary tool calls.
*   `Scenario_Block_Family_Search`: User asks to find a resampler, GUI sink, or modulator for a new graph. Agent should begin with `search_grc(scope="catalog")` using the block-family vocabulary surfaced in Tutorial 1.
*   `Scenario_Simulation_First`: User asks to prototype a receiver "without hardware yet." Agent should keep the plan in simulation/software blocks and only introduce SDR hardware blocks when the user asks for them explicitly.

---

## Section 2: Installation and First Flowgraph

### Sources Audited
*   `InstallingGR.md`
*   `Your_First_Flowgraph.md`

### Critical Findings
1.  `InstallingGR.md` is OS/package setup only. It is a prerequisite for the harness, not a flowgraph-editing contract.
2.  `Your_First_Flowgraph.md` teaches GUI search, `Id` vs `Title`, unconnected-block red text, and color-matched ports as visual diagnostics.

### Adaptations
*   Keep installation guidance doc-only.
*   Do not add tools or validators for GUI shortcuts. Existing connection and dtype checks already cover the real graph constraints behind the visual cues.

### Testable Scenarios for Phase 7
*   `Scenario_First_Flowgraph_Overview`: user asks for a quick overview of a loaded starter graph and the agent routes to `summarize_graph`.
*   `Scenario_GUI_Diagnostic_Explain`: user asks what a red/unconnected block state means and the agent answers without inventing a tool.

## Section 3: Variables and Expressions

### Sources Audited
*   `Python_Variables_in_GRC.md`
*   `Variables_in_Flowgraphs.md`

### Critical Findings
1.  Variables are plain Python expressions. Block parameters commonly reference other variables instead of literals, for example `frequency = samp_rate/3`.
2.  GRC may display SI abbreviations like `32k`, but the graph still stores ordinary numeric values and symbolic expressions.

### Adaptations
*   Implemented: the system prompt now explicitly tells the model to preserve GNU/Python expression chains like `samp_rate/4` unless the user requests a literal or a repair transaction needs one.
*   Keep SI-display behavior doc-only. The prompt already expands common rate abbreviations for user-facing edits.

### Testable Scenarios for Phase 7
*   `Scenario_Preserve_Symbolic_Expression`: update `qtgui_time_sink_x_0.srate` to `samp_rate/2` without collapsing it to a literal.
*   `Scenario_SI_Abbreviation`: user asks for `32k` and the agent writes `32000`.

## Section 4: Runtime Widgets and Duplicate-ID Shadowing

### Sources Audited
*   `Runtime_Updating_Variables.md`

### Critical Findings
1.  `QT GUI Range` and `QT GUI Chooser` replace a static variable by taking the same `Id` and then disabling the legacy variable block.
2.  Slider start/stop/step fields can themselves be symbolic expressions such as `-samp_rate/2` and `samp_rate/2`.

### Adaptations
*   Implemented: a new `update_states` transaction op can toggle one unique loaded block between `enabled` and `disabled` by editing the real `.grc` `states.state` field.
*   Verified on a real `.grc` case: disabling one of two same-name variables lets `grcc` accept the remaining enabled definition.
*   Implemented: affected-edit integrity checks now reject final staged states that leave duplicate enabled parsed identifiers in place.
*   Deferred: full GUI-widget insertion and duplicate-name disambiguation remain out of scope until structural add support widens beyond detached variables.

### Testable Scenarios for Phase 7
*   `Scenario_Disable_Detached_Block`: add a detached variable, then disable it without removing it.
*   `Scenario_Legacy_Variable_Shadowing`: future fixture-based case for disabling an older variable after a widget takes over its `Id`.

## Section 5: Types and Converters

### Sources Audited
*   `Signal_Data_Types.md`
*   `Converting_Data_Types.md`

### Critical Findings
1.  Stream dtypes are part of the graph contract. Mismatches surface as red arrows in GRC because downstream ports will not accept the upstream type.
2.  The fix is either retuning compatible block parameters or inserting an explicit converter block such as `Char to Float` from the `Type Converters` family.

### Adaptations
*   Existing behavior: preflight already rejects incompatible stream dtypes.
*   Implemented: `incompatible_dtype` errors now include a hint that points the user/model at `Type Converters` instead of failing silently.
*   Deferred: do not auto-insert converter blocks yet. Current structural add support is intentionally narrower than that.

### Testable Scenarios for Phase 7
*   `Scenario_Byte_To_Float_Hint`: propose or apply an invalid byte-to-float connection and verify the error suggests a Type Converter.
*   `Scenario_Converter_Block_Search`: user asks how to bridge a dtype mismatch and the agent starts with catalog search.

## Section 6: Bits, Streams, and Vectors

### Sources Audited
*   `Packing_Bits.md`
*   `Streams_and_Vectors.md`

### Critical Findings
1.  `Pack K Bits` / `Unpack K Bits` are explicit bit-width transforms, not interchangeable with generic dtype conversion.
2.  Streams carry one sample per time step, while vectors carry multiple samples per time step. `Streams to Vector`, `Vector to Stream`, and `Vector to Streams` are the canonical boundary blocks.

### Adaptations
*   Keep pack/unpack semantics in docs and evals; no new runtime tool is justified.
*   Implemented: structural preflight now checks `vlen` on touched stream connections, so a parameter edit that changes a vector width can invalidate the transaction before `grcc`.
*   Keep pack/unpack DSP semantics in prompt/eval guidance rather than hardcoding modem math into Python preflight.

### Testable Scenarios for Phase 7
*   `Scenario_Pack_Unpack_Explain`: explain when `Pack K Bits` is required instead of a dtype converter.
*   `Scenario_Stream_Vector_Boundary`: identify the correct conversion block for stream-to-vector or vector-to-stream changes.
*   `Scenario_Touched_Vlen_Mismatch`: staged edit changes a touched block's vector width and preflight rejects the now-invalid incident connection.

## Section 7: Hier Blocks and Scope Filter

### Sources Audited
*   `Hier_Blocks_and_Parameters.md`

### Critical Findings
1.  Parameters define a hier block's external interface; variables are internal implementation details.
2.  Hier-block authoring depends on `Pad Source` / `Pad Sink` blocks and multi-file generation.

### Adaptations
*   Keep the parameter-vs-variable distinction in the research log.
*   Skip hier-block authoring itself. It falls outside the single-flowgraph boundary this harness is meant to optimize.

### Testable Scenarios for Phase 7
*   None. This section is scope filtering, not a runtime contract expansion.

## Section 8: Embedded Python Block Structure and Callbacks

### Sources Audited
*   `Creating_Your_First_Block.md`

### Critical Findings
1.  Embedded Python Blocks are structurally defined by imports, the `__init__` signature, and `work()`.
2.  Matching parameter names and class attributes trigger GRC-generated callbacks automatically.

### Adaptations
*   Keep EPB structure in docs only for now.
*   Implemented prompt guidance: when explaining generated GNU Radio Python, do not invent manual setter logic for variables or callbacks that GRC already auto-generates.
*   Deferred: no raw EPB editing tool or preflight Python parser has been added in this pass.

### Testable Scenarios for Phase 7
*   `Scenario_EPB_Structure_Explain`: explain the EPB structure and callback behavior without hallucinating new code paths.

## Section 9: Embedded Python Vectors

### Sources Audited
*   `Python_Block_with_Vectors.md`

### Critical Findings
1.  Vector ports use `(dtype, vlen)` tuples and typically require 3D indexing inside `work()`.
2.  GRC validates against the default vector-length values in `__init__`, not just the values shown in the property dialog.

### Adaptations
*   Keep this as deferred EPB-validation work. It is source-backed, but the current harness still does not safely edit EPB code.
*   Remove speculative claims that a vector validator already exists. It does not.

### Testable Scenarios for Phase 7
*   Future-only: `Scenario_EPB_Default_Vlen_Mismatch` once EPB editing exists.

## Section 10: Embedded Python Messages and Tags

### Sources Audited
*   `Python_Block_Message_Passing.md`
*   `Python_Block_Tags.md`

### Critical Findings
1.  Message ports are asynchronous and built around PMTs plus registered handlers.
2.  Tag work is synchronous and depends on absolute/relative sample-offset math: write with `nitems_written`, read with `tag.offset - nitems_read`.

### Adaptations
*   Keep the PMT boilerplate and tag formulas in docs for now.
*   Deferred: no EPB code-generation helper or validator has been added in this pass.
*   Implemented prompt guidance: when explaining EPBs, preserve the distinction between async message-state updates and synchronous tag/sample-offset math instead of inventing custom callback plumbing.

### Testable Scenarios for Phase 7
*   Future-only: message-handler and tag-offset scenarios once EPB editing is supported.

## Section 11: Filters and Taps

### Sources Audited
*   `Low_Pass_Filter_Example.md`
*   `Designing_Filter_Taps.md`

### Critical Findings
1.  Filter tutorials overwhelmingly use symbolic sample-rate-relative expressions like `samp_rate/4` rather than hard-coded literals.
2.  The recommended visual verification trick is an impulse-style `Vector Source` probe, not manual inspection of taps alone.

### Adaptations
*   Implemented via prompt: preserve symbolic expressions during parameter edits.
*   Keep the impulse trick as a documentation/eval pattern. Current graph-edit support is too narrow to add whole verification branches safely.

### Testable Scenarios for Phase 7
*   `Scenario_Filter_Expression_Preservation`: update filter parameters while keeping `samp_rate`-relative expressions intact.
*   `Scenario_Impulse_Trick_Explain`: explain the impulse-response check when asked how to inspect a filter.

## Section 12: Rate Change and Frequency Shift

### Sources Audited
*   `Sample_Rate_Change.md`
*   `Frequency_Shifting.md`

### Critical Findings
1.  Rate-change tutorials rely on explicit interpolation/decimation variables and downstream rate propagation, not one isolated parameter change.
2.  Frequency-shift tutorials use a symbolic variable plus `Signal Source` and `Multiply` blocks, often upgraded later to a runtime widget.

### Adaptations
*   Keep rate-chain reasoning in docs and evals.
*   Keep full sample-rate-consistency validation deferred until the harness can evaluate expression chains across wider block families with low false-positive risk.
*   Keep full widget-replacement workflows deferred; this pass only added block enable/disable support.

### Testable Scenarios for Phase 7
*   `Scenario_Rate_Chain_Explain`: explain which parameters must change when interpolation or decimation changes a branch rate.
*   `Scenario_Math_Shift_Explain`: identify the multiply-plus-oscillator pattern for software frequency shifting.

## Section 13: Binary Files and Scaling

### Sources Audited
*   `Reading_and_Writing_Binary_Files.md`

### Critical Findings
1.  Binary file handling depends on exact agreement about real vs complex layout, bit width, and endianness.
2.  The tutorial's canonical scaling rule is `32768` for short/float conversion, and extremely large displayed magnitudes often indicate a format mismatch.

### Adaptations
*   Keep file-format heuristics as prompt/doc-only for now.
*   Do not add a file-format validator until the harness can model file-source/file-sink semantics more precisely than simple port dtype checks.

### Testable Scenarios for Phase 7
*   `Scenario_File_Format_Diagnosis`: explain why `10^38`-scale values usually mean a wrong file format or endian setting.
*   `Scenario_32768_Scaling`: explain the short/float scale rule when asked about file I/O normalization.

## Section 14: Hardware FM Receiver Variants

### Sources Audited
*   `RTL-SDR_FM_Receiver.md`
*   `B200-B205mini_FM_Receiver.md`
*   `E310_FM_Receiver.md`

### Critical Findings
1.  The common FM receiver chain is hardware source -> filtering/resampling -> FM demod -> audio sink.
2.  Most of the tutorial surface is hardware setup, gains, networking, or provisioning rather than `.grc` authoring.

### Adaptations
*   Keep these tutorials doc-only for the harness.
*   Preserve only the high-level FM chain as a search/describe recipe when the user explicitly names hardware.

### Testable Scenarios for Phase 7
*   `Scenario_FM_Chain_Explain`: identify the standard FM receive stages without trying to provision hardware automatically.

## Section 15: Stream Tags and PMTs

### Sources Audited
*   `Stream_Tags.md`
*   `Polymorphic_Types_(PMTs).md`

### Critical Findings
1.  Tag arithmetic requires both absolute and relative offset formulas.
2.  PMT dictionaries are immutable and helper converters like `to_pmt()` / `to_python()` are the safest bridge between Python objects and PMTs.

### Adaptations
*   Keep these formulas and PMT rules in docs for future EPB tooling.
*   Remove claims that the current harness already validates EPB PMT or tag logic. It does not.

### Testable Scenarios for Phase 7
*   Future-only: PMT-dict immutability and tag-offset scenarios once EPB mutation is supported.

## Section 16: Message Passing and Virtual Blocks

### Sources Audited
*   `Message_Passing.md`
*   `Virtual_Sinks_and_Sources.md`

### Critical Findings
1.  Message handlers should update internal state instead of doing blocking work inside `work()`.
2.  Virtual blocks connect by shared `Stream ID`, infer types from real connections, and need explicit renaming when `Stream ID` collisions occur.

### Adaptations
*   Existing behavior: preflight already allows message-port fan-in by exempting message domains from the occupied-input check.
*   Deferred: duplicate `Stream ID` validation belongs with broader virtual-block structural editing, not this pass.
*   Explicit non-change: the harness does not restrict Virtual Source/Sink topologies beyond GRC's own structural metadata because 1-to-N virtual broadcast is legitimate.

### Testable Scenarios for Phase 7
*   `Scenario_Message_FanIn`: verify that multiple message sources can target one input without tripping stream-port occupancy rules.
*   `Scenario_Virtual_Stream_ID_Explain`: explain how Virtual Sinks/Sources bind together and why duplicate IDs are ambiguous.

## Section 17: Import Blocks and Library Expressions

### Sources Audited
*   `Importing_Libraries.md`

### Critical Findings
1.  Library-backed expressions in variables or parameters only work if a corresponding `Import` block exists in the flowgraph.
2.  Those expressions should stay symbolic so their runtime behavior is preserved.

### Adaptations
*   Implemented prompt guidance: preserve symbolic expressions.
*   Explicit non-change: missing import aliases are diagnosed by prompt/evals and the final `grcc` validation pass, not by Python preflight.
*   Deferred: do not auto-add `Import` blocks in this pass. Current `add_block` only supports detached variables, so a half-implemented import workflow would be misleading.

### Testable Scenarios for Phase 7
*   `Scenario_Import_Missing_Diagnosis`: explain that a `np.*` expression needs an `Import` block.
*   `Scenario_Expression_Preservation`: keep a library expression as a string instead of collapsing it to a literal.

## Section 18: Narrowband FM and SSB Recipes

### Sources Audited
*   `Simulation_example__Narrowband_FM_transceiver.md`
*   `Simulation_example__Single_Sideband_transceiver.md`

### Critical Findings
1.  Narrowband FM receiver chains depend on explicit filtering, squelch, and sample-rate reduction stages.
2.  SSB examples hinge on Hilbert transforms, positive-frequency selection, and IQ-sideband manipulation.

### Adaptations
*   Keep these as prompt/doc search recipes rather than transaction-level edits.
*   Do not invent a broad radio-construction tool from these tutorials.

### Testable Scenarios for Phase 7
*   `Scenario_Search_Squelch_And_Hilbert`: user asks what blocks are needed for NBFM or SSB and the agent starts with catalog search.
*   `Scenario_SSB_Recipe_Explain`: explain why Hilbert and complex filtering appear in SSB chains.

---

## Section 19: Linear Modulation Gaps and BPSK

### Sources Audited
*   `QPSK_Mod_and_Demod` is linked from `Tutorials.md` but is not mirrored locally in this workspace.
*   `Simulation_example__BPSK_Demodulation.md`

### Critical Findings
1.  The BPSK tutorial depends on matched RRC shaping, consistent samples-per-symbol, Costas-loop recovery, and differential coding/decoding to resolve phase ambiguity.
2.  The official QPSK page is not mirrored locally, so no fresh QPSK-specific claims should remain in this research log unless they are sourced somewhere else in the repository.

### Adaptations
*   Keep BPSK guidance as doc/eval-only.
*   Remove unsourced QPSK-specific claims from any future harness work until the actual local source exists.

### Testable Scenarios for Phase 7
*   `Scenario_BPSK_Recipe_Explain`: explain matched filtering, Costas recovery, and differential decoding in a BPSK chain.

## Section 20: M-ary and FSK Recipes

### Sources Audited
*   `M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod.md`
*   `Simulation_example__FSK.md`

### Critical Findings
1.  Delay, unpacking, and verification patterns scale with `log2(M)` when moving from binary to M-ary schemes.
2.  The FSK tutorial uses a VCO-based transmit path plus xlating-filter and quadrature-demod receive stages.

### Adaptations
*   Keep these as doc/prompt patterns.
*   Explicit non-change: modulus, delay, and `K=log2(M)` lockstep rules stay in expert recipes and evals, not in Python preflight.
*   Do not add an automatic delay calculator, modulation upgrader, or FSK synthesizer tool in this pass.

### Testable Scenarios for Phase 7
*   `Scenario_Mary_Unpack_Explain`: explain why a `K-bit Unpack` stage appears in M-ary receivers.
*   `Scenario_FSK_Block_Search`: search for VCO or quadrature-demod blocks when the user asks about FSK.

## Section 21: OFDM

### Sources Audited
*   `Basic_OFDM_Tutorial.md`

### Critical Findings
1.  OFDM carrier maps use centered FFT/DC conventions and vector-of-vectors carrier/pilot definitions.
2.  Cyclic prefix, pilots, and Schmidl-Cox synchronization are structural prerequisites, not optional extras.

### Adaptations
*   Keep OFDM in docs and search-oriented evals only.
*   Do not invent carrier-map or OFDM-builder tools from this tutorial.

### Testable Scenarios for Phase 7
*   `Scenario_OFDM_Block_Search`: search for OFDM sync, carrier-allocation, or cyclic-prefix blocks by family.
*   `Scenario_OFDM_Explain`: explain why OFDM uses centered FFT indexing and preambles.

## Section 22: Packet Communications

### Sources Audited
*   `Packet_Communications.md`

### Critical Findings
1.  Access codes, header/protocol format objects, and CRC blocks are the backbone of GNU Radio's packet framing examples.
2.  Packet systems bridge async message PDUs and sync tag-delimited streams; both domains appear in the same workflow.

### Adaptations
*   Keep packet guidance in docs and evals for now.
*   Implemented prompt guidance: packetized modem chains should keep `Repack Bits`, `Unpack K Bits`, and access-code/header settings aligned across the chain.
*   Do not add packet-topology validators that assume one packet architecture when the harness still lacks broad packet-block editing support.

### Testable Scenarios for Phase 7
*   `Scenario_Access_Code_Explain`: explain the role of access codes and protocol formatter/parser objects.
*   `Scenario_Packet_Domain_Bridge`: explain how PDUs bridge into tagged streams.

## Section 23: File Transfer over Packet/BPSK

### Sources Audited
*   `File_transfer_using_Packet_and_BPSK.md`

### Critical Findings
1.  Long preambles/postambles and conservative amplitude scaling are treated as practical necessities, not optional cleanup.
2.  The tutorial advocates staged loopback/channel-model testing rather than one-shot over-the-air integration.

### Adaptations
*   Keep these heuristics in docs for future prompt work.
*   Do not auto-synthesize preambles, postambles, or delay heuristics in this pass.

### Testable Scenarios for Phase 7
*   `Scenario_Preamble_Heuristic_Explain`: explain why packet links often need long preambles and flushing tails.
*   `Scenario_Amplitude_0p5_Explain`: explain why hardware-targeted packet links often scale constellations down.

## Section 24: OOT and YAML Scope Filter

### Sources Audited
*   `Creating_Python_OOT_with_gr-modtool.md`
*   `Creating_C++_OOT_with_gr-modtool.md`
*   `YAML_GRC.md`

### Critical Findings
1.  OOT authoring is YAML- and build-system-driven rather than single-file `.grc` editing.
2.  YAML remains the ground truth for block parameters and ports, but the workflow is multi-file and compilation heavy.

### Adaptations
*   Keep only the high-level note that YAML defines the true block interface.
*   Remove any suggestion that the harness should author OOT modules or raw YAML in this pass.

### Testable Scenarios for Phase 7
*   None. This section is explicit scope control.

## Section 25: Generated Python and Callbacks

### Sources Audited
*   `Flowgraph_Python_Code.md`

### Critical Findings
1.  Generated Python mirrors GRC block names and parameter IDs exactly.
2.  Variable callbacks and setters are generated automatically by GRC from those names.

### Adaptations
*   Implemented: the system prompt now tells the model not to invent manual setters or callback plumbing when explaining generated GNU Radio Python.
*   Keep direct `.py` editing unsupported; `.grc` remains the source of truth.

### Testable Scenarios for Phase 7
*   `Scenario_Generated_Callback_Explain`: explain generated callback behavior without claiming that the harness edits the generated Python file.

## Section 26: Hardware, IQ, and Sample Rate Fundamentals

### Sources Audited
*   `Guided_Tutorial_Hardware_Considerations.md`
*   `IQ_Complex_Tutorial.md`
*   `Sample_Rate_Tutorial.md`

### Critical Findings
1.  Hardware sources set real sample rates; `Throttle` is the simulation-side clock.
2.  IQ/baseband tutorials reinforce that passband simulation usually needs complex streams and that rate/type mismatches have predictable structural causes.

### Adaptations
*   Keep these as prompt/doc guidance only.
*   Implemented prompt guidance: software-only retuning should use oscillator-plus-multiply within `[-samp_rate/2, +samp_rate/2]`, and gain math should stay logarithmic when expressed in dB.
*   Defer a broad sample-rate-consistency validator until the harness can evaluate symbolic rate chains with low false-positive risk across more hardware-aware block families.

### Testable Scenarios for Phase 7
*   `Scenario_Throttle_Vs_Hardware_Explain`: explain why hardware-set rates and simulation throttling are different contracts.
*   `Scenario_Real_Vs_Complex_Baseband`: explain when complex streams are required for baseband or passband work.

## Section 27: ZMQ

### Sources Audited
*   `Understanding_ZMQ_Blocks.md`

### Critical Findings
1.  ZMQ sender/receiver pairs must agree on `bind` / `connect` roles.
2.  Tag-passing and wire-format settings must match across the pair.

### Adaptations
*   Keep ZMQ behavior doc-only.
*   Do not add topology-generation or endpoint-synthesis logic from this tutorial.

### Testable Scenarios for Phase 7
*   `Scenario_ZMQ_Bind_Connect_Explain`: explain why one side binds and the other connects.

## Section 28: Bandlimited Threshold Detector

### Sources Audited
*   `Bandlimited_threshold_detector.md`

### Critical Findings
1.  Advanced vector tutorials reinforce runtime tuple arithmetic and 3D vector indexing.
2.  Vector-source math must stay consistent with FFT or vector lengths.

### Adaptations
*   Keep this as future vector-validation research only.
*   Do not claim that the current harness already validates vector-size arithmetic.

### Testable Scenarios for Phase 7
*   Future-only once vector-source and EPB mutation are broader than they are today.

## Section 29: Pushbutton Recorder and Missing Embedded-App Tutorial

### Sources Audited
*   `Pushbutton_IQ_Recorder_with_descriptive_filenames.md`
*   `GNU_Radio_Flowgraph_Embedded_in_Python_Applications` is linked from `Tutorials.md` but is not mirrored locally in this workspace.

### Critical Findings
1.  File-sink filenames can use runtime conditional expressions and inline timestamp logic.
2.  The embedded-flowgraph tutorial is not available locally, so no new claims should be drawn from it here.

### Adaptations
*   Keep runtime filename expressions as doc-only guidance.
*   Remove any unsourced embedded-application claims from future harness design unless the local source is added.

### Testable Scenarios for Phase 7
*   `Scenario_Runtime_Filename_Explain`: explain why inline expressions can change while start-time variables stay frozen.

## Section 30: Porting Guides and Version Drift

### Sources Audited
*   `Porting_Existing_Flowgraphs_to_a_Newer_Version.md`
*   `GNU_Radio_3.10_OOT_Module_Porting_Guide.md`
*   `GNU_Radio_3.9_OOT_Module_Porting_Guide.md`
*   `GNU_Radio_3.8_OOT_Module_Porting_Guide.md`

### Critical Findings
1.  Version upgrades break block IDs, enum values, generated code conventions, and OOT expectations even when a file still parses.
2.  These are migration tasks, not ordinary flowgraph-authoring behaviors.

### Adaptations
*   No harness change. Keep these as doc-only context.
*   Do not turn version-porting heuristics into generic prompt rules for normal single-graph edits.

### Testable Scenarios for Phase 7
*   None.

## Section 31: Performance, Audio, IDEs, and Git

### Sources Audited
*   `VOLK_Guide.md`
*   `ALSAPulseAudio.md`
*   `UsingVSCode.md`
*   `UsingEclipse.md`
*   `UsingCB.md`
*   `DevelopingWithGit.md`

### Critical Findings
1.  Audio tutorials contribute a few real flowgraph constraints: supported sample-rate families and clipping above amplitude `1.0`.
2.  The rest of the material is development environment or optimization workflow, not `.grc` authoring.

### Adaptations
*   Keep audio amplitude/rate constraints as notes only.
*   Do not widen the harness toward IDE integration, git workflows, or VOLK tuning.

### Testable Scenarios for Phase 7
*   `Scenario_Audio_Clipping_Explain`: explain why amplitudes above `1.0` clip at audio boundaries.

## Section 32: Octave, Custom Buffers, XMLRPC, and Mirror Gaps

### Sources Audited
*   `Octave.md`
*   `CustomBuffers.md`
*   `Understanding_XMLRPC_Blocks.md`
*   The external GNU Radio Scheduler tutorial linked from `Tutorials.md` is not mirrored locally in this workspace.

### Critical Findings
1.  Octave is post-processing, CustomBuffers is accelerator/OOT plumbing, and XMLRPC is remote-control integration.
2.  None of these widens the core single-flowgraph transaction contract.

### Adaptations
*   No harness change.
*   Keep only the note that XMLRPC exposes variables remotely and that these topics remain outside the core edit/validate loop.

### Testable Scenarios for Phase 7
*   None.

## Section 33: Round 3 Exact Additions and Ordered Gap Matrix

### Sources Audited
*   `docs/wiki_gnuradio_org/Tutorials.md`
*   Fetched official `QPSK_Mod_and_Demod`
*   Fetched official `GNU_Radio_Flowgraph_Embedded_in_Python_Applications`

### Critical Findings
1.  The missing exact stream-tag and PMT formulas are now explicit: convert an absolute tag offset into the current input buffer index with `tag.offset - self.nitems_read(0)`, emit absolute output tag positions with `self.nitems_written(0) + i`, let standard sync decimators/interpolators retime tags through `relative_rate`, and rebind immutable PMT dictionaries with `meta = pmt.dict_add(meta, key, value)`.
2.  The fetched official QPSK tutorial closes the earlier mirror gap. Its receive chain is not generic theory; it is a concrete GNU sequence: FFT Filter with the same matched RRC taps as the transmitter (`firdes.root_raised_cosine(1.0, samp_rate, samp_rate/sps, excess_bw, 11*sps)`), then Symbol Sync at the expected `sps` (the tutorial example uses `4`), then Costas Loop order `4`, then Constellation Decoder and Differential Decoder when differential encoding was used. The fetched page also makes the transmit/receive matched-filter pairing explicit and gives a stage-specific verification hint of `72` bits of delay for the tutorial's 4-sps example.
3.  The exact M-ary lockstep rule is now explicit instead of hand-wavy: `k = log2(M)`, `Unpack K Bits = k`, common timing-loop bandwidth `0.0628`, and verification delay `Delay = int(5.5 * sps + 7) * k`. For 16-QAM, that means `k = 4`, `Unpack K Bits = 4`, and `Delay = int(5.5 * sps + 7) * 4`.
4.  Packet tutorials require byte/bit seams and tag/object metadata to stay aligned all the way across the sync and async boundary. The exact lockstep set is: `Repack Bits`, `Unpack K Bits`, access code, header format object, and `packet_len` tag propagation across formatter, parser, and PDU bridge blocks.
5.  Hardware-facing tutorials add operational bounds that were missing from the earlier grouped summary: for live spectrum browsing, disable AGC so amplitudes remain interpretable; the visible complex bandwidth is exactly `samp_rate`; terminal `O` is an overrun hint that the rate or processing load is too high; and hardware packet examples keep post-modulator amplitude around `0.5` while proving the chain in loopback or a channel model before over-the-air use.
6.  The fetched embedded-flowgraph tutorial resolves the earlier `missing locally` note for Section 29. The runtime contract is: import the generated flowgraph class, instantiate the generated `gr.top_block` (often also a `Qt.QWidget`), control execution with `start()`, `stop()`, and `wait()`, use generated getters/setters for runtime variables, embed generated Qt widgets directly, and stop/wait around dynamic `connect()` / `disconnect()` changes when controlling the graph from a host Python GUI.

### Adaptations
*   Implemented prompt guidance now carries the exact tag, PMT, QPSK, M-ary, packet, and hardware-diagnostics recipes captured above.
*   Implemented eval coverage now checks that the model can answer those direct expert questions without tool use.
*   Explicit non-change: no new validator was promoted into `validation/checks.py` from this round. Every new fact above either depends on DSP semantics, runtime block behavior, PMT/tag code inside EPBs, or hardware diagnostics that are not locally provable from the staged transaction snapshot with low false-positive risk.

### Ordered Gap Matrix

| # | Tutorial | Maps to | Round 3 status |
|---:|---|---|---|
| 1 | What is GNU Radio? | Section 1 | Captured; no new deep DSP truth. |
| 2 | Installing GNU Radio | Section 2 | Captured; no new deep DSP truth. |
| 3 | Your First Flowgraph | Section 2 | Captured; no new deep DSP truth. |
| 4 | Python Variables in GRC | Section 3 | Captured. |
| 5 | Variables in Flowgraphs | Section 3 | Captured. |
| 6 | Runtime Updating Variables | Section 4 | Captured. |
| 7 | Signal Data Types | Section 5 | Captured. |
| 8 | Converting Data Types | Section 5 | Captured. |
| 9 | Packing Bits | Section 6 | Captured. |
| 10 | Streams and Vectors | Section 6 | Captured. |
| 11 | Hier Blocks and Parameters | Section 7 | Captured. |
| 12 | Creating Your First Block | Section 8 | Captured. |
| 13 | Python Block With Vectors | Section 9 | Captured; no new deep DSP truth beyond existing 3D indexing notes. |
| 14 | Python Block Message Passing | Sections 10 and 16 | Captured; Round 3 kept the async message-handler to synchronous `work()` boundary explicit. |
| 15 | Python Block Tags | Sections 10 and 15 | Captured; Round 3 added exact `tag.offset - self.nitems_read(0)` and `self.nitems_written(0) + i` formulas. |
| 16 | Low Pass Filter Example | Section 11 | Captured. |
| 17 | Designing Filter Taps | Section 11 | Captured. |
| 18 | Sample Rate Change | Section 12 | Captured. |
| 19 | Frequency Shifting | Section 12 | Captured. |
| 20 | Reading and Writing Binary Files | Section 13 | Captured; Round 3 kept the exact `32768` scaling rule. |
| 21 | RTL-SDR FM Receiver | Section 14 | Captured; no new harness contract. |
| 22 | B200-B205mini FM Receiver | Section 14 | Captured; no new harness contract. |
| 23 | E310 FM Receiver | Section 14 | Captured; no new harness contract. |
| 24 | Stream Tags | Section 15 | Captured; Round 3 added exact absolute/relative offset formulas and `relative_rate` note. |
| 25 | Polymorphic Types (PMTs) | Section 15 | Captured; Round 3 added explicit immutable-dict rebinding with `pmt.dict_add`. |
| 26 | Message Passing | Section 16 | Captured. |
| 27 | Virtual Sinks and Sources | Section 16 | Captured. |
| 28 | Importing Libraries | Section 17 | Captured. |
| 29 | Narrowband FM | Section 18 | Captured. |
| 30 | Single Sideband (SSB) | Section 18 | Captured. |
| 31 | QPSK Mod and Demod | Section 19 | Fetched official page; Round 3 added exact matched-filter, Symbol Sync, Costas order-4, and Differential Decoder receive recipe. |
| 32 | BPSK Demodulation | Section 19 | Captured; remains aligned with the matched-filter and differential-decoding recipe. |
| 33 | M-ASK, M-PSK and QAM-M Mod and Demod | Section 20 | Captured; Round 3 added exact `k = log2(M)`, `Unpack K Bits = k`, and `Delay = int(5.5 * sps + 7) * k` formulas. |
| 34 | Frequency Shift Keying (FSK) | Section 20 | Captured. |
| 35 | OFDM Basics | Section 21 | Captured. |
| 36 | Packet Communications | Section 22 | Captured; Round 3 added exact access-code/header/`packet_len`/bit-seam lockstep. |
| 37 | File transfer using Packet and BPSK | Section 23 | Captured; Round 3 added the loopback-first rule and post-modulator amplitude near `0.5`. |
| 38 | Creating an OOT (Python block example) | Section 24 | Captured; no new harness contract. |
| 39 | Creating an OOT (C++ block example) | Section 24 | Captured; no new harness contract. |
| 40 | Writing the YAML file for a block (GR 3.8+) | Section 24 | Captured; no new harness contract. |
| 41 | Understanding a Flowgraph's Python Code | Section 25 | Captured. |
| 42 | Using GNU Radio With SDRs | Section 26 | Captured; Round 3 added AGC-off, visible-bandwidth, and overrun notes. |
| 43 | IQ and Complex Signals | Section 26 | Captured. |
| 44 | Understanding Sample Rate | Section 26 | Captured; Round 3 reinforced that visible complex bandwidth equals `samp_rate`. |
| 45 | Understanding ZMQ Blocks | Section 27 | Captured. |
| 46 | Bandlimited Threshold and Detection Demo Application | Section 28 | Captured. |
| 47 | Pushbutton I/Q Recorder with Descriptive File Names | Section 29 | Captured. |
| 48 | GNU Radio Flowgraph Embedded in Python Applications | Section 29 | Fetched official page; Round 3 added generated-top-block embedding, `start/stop/wait`, runtime setter, Qt widget, and dynamic reconnect notes. |
| 49 | Porting Existing Flowgraphs to Newer Version | Section 30 | Captured; no new harness contract. |
| 50 | Porting Existing OOTs from 3.9 to 3.10 | Section 30 | Captured; no new harness contract. |
| 51 | Porting Existing OOTs from 3.8 to 3.9 | Section 30 | Captured; no new harness contract. |
| 52 | Porting Existing OOTs from 3.7 to 3.8 | Section 30 | Captured; no new harness contract. |
| 53 | VOLK: What it does, why it rocks, how to write new kernels | Section 31 | Captured; no new deep DSP truth for the `.grc` harness. |
| 54 | Working with ALSA and Pulse Audio | Section 31 | Captured. |
| 55 | Using Visual Studio Code for GNU Radio Development | Section 31 | Captured; no new harness contract. |
| 56 | Using Eclipse for Building and Source level debugging C++ OOTs | Section 31 | Captured; no new harness contract. |
| 57 | Using Code::Blocks IDE for GNU Radio Development | Section 31 | Captured; no new harness contract. |
| 58 | Git and GNU Radio | Section 31 | Captured; no new harness contract. |
| 59 | How to use Octave or Matlab with GNU Radio | Section 32 | Captured; no new harness contract. |
| 60 | The GNU Radio Scheduler | Section 32 | External link only; no new runtime or validator contract promoted from this round. |
| 61 | Using Custom Buffers for Hardware Accelerated Blocks | Section 32 | Captured; no new harness contract. |
| 62 | Remote Control and Automation of Flowgraphs with XMLRPC | Section 32 | Captured; no new harness contract. |
