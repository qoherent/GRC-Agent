# GNU Radio Tutorial Research & Agent Adaptation

**Context**: This document was generated during an autonomous "Fresh-Eyes" audit of the official GNU Radio Wiki tutorials (April 2026). 
**Purpose**: To extract "Expert Recipes," structural rules, and diagnostic heuristics from the official documentation and transform them into actionable enhancements for the `grc-agent` toolset and the Phase 7 evaluation suite.
**Methodology**: Tutorials were reviewed sequentially according to the official curriculum. Each section disassembles a tutorial to identify:
1. **Critical Findings**: Non-obvious GNU Radio behaviors or "best practices."
2. **Adaptations**: Specific ways the agent's code, tools, or prompts should change.
3. **Testable Scenarios**: High-value workflows for the `llama_eval` suite.

---

## Section 1: Introducing GNU Radio & Flowgraph Fundamentals

### Critical Findings
1.  **Instructional Language (Color Coding)**: Tutorials consistently use port colors (Blue=Complex, Orange=Float, Purple=Byte) to explain mismatches.
    *   *Adaptation*: Enhance `describe_block` to include color hints for data types. This allows the model to interpret user requests like "connect the orange output."
2.  **Conflict Resolution (Disabling Blocks)**: Tutorial "Runtime Updating Variables" solves an "ID not unique" error by *disabling* the conflicting block (shortcut 'D').
    *   *Adaptation*: Add a `toggle_block_state` tool to allow the agent to `disable` or `enable` blocks without removing them. This is essential for non-destructive troubleshooting.
3.  **Parameter Expressions**: GNU Radio allows (and tutorials encourage) using Python expressions in parameter fields (e.g., `frequency = samp_rate / 3`).
    *   *Adaptation*: Ensure `apply_edit` validation treats strings as potential expressions rather than just literal names.
4.  **SI Unit Display vs. Python Value**: GRC displays `32000` as `32k`.
    *   *Adaptation*: The agent should be aware that a user asking to "change rate to 32k" means `32000` in the YAML/JSON payload.
5.  **Type Conversion Retrieval**: Users are taught to search for "Type Converters" when direct connections fail.
    *   *Adaptation*: Improve `search_grc` to prioritize blocks in the `Type Converters` category when the query implies a mismatch fix.

### Testable Scenarios for Phase 7
*   `Scenario_SI_Units`: User asks to set frequency to "100M". Agent must set value to `100000000`.
*   `Scenario_Conflict_Resolve`: Add a `QT GUI Range` with an existing ID and resolve the error by disabling the original variable.
*   `Scenario_Type_Cascade`: Switch a Signal Source from Complex to Float and fix all downstream blocks to match.

---

## Section 2: Creating and Modifying Python Blocks

### Critical Findings
1.  **Embedded Python Block (EPB) Logic**: Custom logic is stored in the `code` parameter of a block with the ID `epy_block`. GRC dynamically generates the block's interface (ports/parameters) by parsing the `__init__` method.
    *   *Adaptation*: Add an `edit_python_block` tool. Generic `apply_edit` is too risky for raw Python code. A specialized tool can provide a "skeleton" and ensure the `blk` class structure remains intact.
2.  **Signature Persistence**: Changing `in_sig` or `out_sig` in the Python code immediately changes the block's ports in GRC.
    *   *Adaptation*: The agent must be taught that editing EPB code is a "Structural Edit." It should call `validate_graph` immediately after an EPB edit to ensure the Python code is syntactically valid and the ports haven't caused new connection errors.
3.  **NumPy Slicing**: Tutorials emphasize using `output_items[0][:] = ...` for efficiency.
    *   *Adaptation*: Include "Numpy Idioms" in the agent's system prompt or a specialized EPB sub-agent to ensure it writes performant GNU Radio code.
4.  **Callback Mechanism**: If a class attribute name matches a parameter name (e.g., `self.additionFlag = additionFlag`), GRC automatically creates a setter callback.
    *   *Adaptation*: The agent can use this to create "Runtime Adjustable" custom blocks without manually writing setter methods.

### Testable Scenarios for Phase 7
*   `Scenario_EPB_Param_Add`: Add a new parameter to an existing Embedded Python Block and use it in the `work` function.
*   `Scenario_EPB_Port_Expand`: Modify an EPB to have two inputs instead of one and update the `work` function to sum them.

## Section 3: Advanced Python Block Features

### Critical Findings
1.  **3D Indexing for Vectors**: In the `work` method, switching from streams to vectors adds a dimension. Indexing becomes `input_items[portIndex][vectorIndex][sampleIndex]`.
    *   *Adaptation*: The agent's system prompt must include this indexing rule. If the agent detects a vector signature, it must use 3-level nested loops or 3D NumPy slicing.
2.  **The "Default Parameter Trap"**: GRC validates flowgraph connectivity using the *default* values defined in the Python `__init__` method, not the values set in the block's property dialog.
    *   *Adaptation*: When the agent modifies an EPB's port signature (e.g., adding a vector size parameter), it MUST update the default value in the `__init__` signature to match the current flowgraph's `vector_length`. Failure to do this causes a "Hidden Mismatch" that crashes at runtime but looks valid in GRC.
3.  **Tuple-based Signatures**: Vector ports are defined as `(type, size)` tuples in the signature lists.
    *   *Adaptation*: Preflight validation should check that `in_sig` matches the `vlen` (vector length) of the source block.

### Testable Scenarios for Phase 7
*   `Scenario_Vector_MaxHold`: Create a Max Hold block that operates on vectors of size 16.
*   `Scenario_MultiPort_Loop`: Modify an EPB to process all input ports dynamically using `range(len(input_items))`.

---

## Section 4: Message Passing & Tags

### Critical Findings
1.  **PMT Dependency**: All message-passing and tag-aware EPBs require `import pmt`.
2.  **Tag Synchronicity**: Unlike messages, tags are tied to specific sample indices.
    *   *Adaptation*: The agent must use `self.add_item_tag()` for time-critical events (like burst detection) and messages for async events (like manual frequency tuning).
3.  **The "Absolute Index" Formula**: Adding tags requires an absolute index. The standard formula is `self.nitems_written(port) + buffer_index`.
    *   *Adaptation*: The agent's EPB "Code Snippet Library" must hardcode this formula to prevent sample-offset hallucinations.
4.  **Tag Retrieval & Offsets**: Tags returned by `get_tags_in_window` have an absolute `.offset`. To use them within the current `input_items` buffer, the agent MUST calculate the relative offset: `rel_index = tag.offset - self.nitems_read(port)`.
    *   *Adaptation*: This is a high-risk logic point. The agent should always double-check its math when subtracting `nitems_read`.
5.  **Implicit Propagation**: GNU Radio propagates tags automatically.
    *   *Adaptation*: The agent should suggest a `Tag Gate` if the user wants to "clean" a stream of metadata.

### Testable Scenarios for Phase 7
*   `Scenario_Tag_BurstDetector`: Create an EPB that tags every sample exceeding a threshold.
*   `Scenario_Tag_Counter`: Create an EPB that reads tags and outputs the number of samples since the last tag.

---

## Section 5: DSP Logic & Advanced GRC Usage

### Critical Findings
1.  **Expression-Heavy Filters**: Tutorials almost never use literal values for filters; they use `samp_rate` fractions (e.g., `samp_rate/8`).
    *   *Adaptation*: The agent's `apply_edit` must be proficient at symbolic math in parameters to avoid breaking the "expression chain" that allows a flowgraph to scale with sample rate.
2.  **The "Impulse Trick" for Testing**: To visualize a filter's response, the tutorial uses a `Vector Source` with the value `(N,)+(0,)*int(N-1)`.
    *   *Adaptation*: This is a powerful "Autonomous Verification" pattern. If the user asks the agent to "check my filter," the agent can temporarily insert this pattern into the graph to confirm the spectral response matches the goal.
3.  **Block Swapping Pattern**: The tutorial recommends replacing a `Signal Source` with a `Noise Source` or `Vector Source` to see different characteristics.
    *   *Adaptation*: Add a `swap_block_type` tool. This would allow the agent to change a block's class (e.g., `analog_sig_source_x` to `analog_noise_source_x`) while preserving as many parameter IDs and connections as possible. This is a common tutorial move that is currently tedious for an agent (remove + add + reconnect).

### Testable Scenarios for Phase 7
*   `Scenario_Filter_Design`: Build a Low Pass Filter flowgraph using `samp_rate`-relative cutoff frequencies.
*   `Scenario_Filter_Verification`: Use the "Impulse Trick" to verify that a filter's transition width is correct.

---

## Section 6: Advanced DSP & SDR Hardware

### Critical Findings
1.  **The "Taps Block" Pattern**: GNU Radio separates filter design (Taps blocks) from filter execution (FIR blocks).
    *   *Adaptation*: The agent must understand this decoupling. If asked to "sharpen the filter," it should look for a `Low-Pass Filter Taps` block and edit its `Transition Width`, rather than looking for parameters on the `FIR Filter` itself.
2.  **Complex NumPy Expressions**: Variable values can be full NumPy one-liners (e.g., `np.ones(8)/8` or `np.exp(2j*np.pi*0.25*n)`).
    *   *Adaptation*: The agent requires a "Symbolic Execution" understanding of Python strings. It must know that `import numpy as np` is a prerequisite for these expressions to work in GRC.
3.  **Variable Dependency Tracing**: Tutorials build chains like `bandPassTaps = lowPassTaps * frequencyShift`.
    *   *Adaptation*: When the user asks for a change, the agent should perform a "Variable Backtrace." If the target parameter is a variable name, the agent should check *that* variable's value and see if it's a leaf node or another expression. This prevents the agent from overwriting a calculated variable with a literal value and breaking the flowgraph logic.
4.  **Domain-Shifting Parameters**: Parameters like `Type` in `Frequency Xlating FIR Filter` are "Meta-Parameters"—changing them changes the port colors and logic of the entire block.
    *   *Adaptation*: The agent should be cautious when editing "Type" parameters, as they almost always trigger a cascade of connection errors.

### Testable Scenarios for Phase 7
*   `Scenario_Variable_Chain`: Create a dependency chain of 3 variables and update the root variable.
*   `Scenario_Complex_Taps`: Use a NumPy expression to design a custom bandpass filter and apply it to a real-to-complex FIR block.

---

## Section 7: System Integration & SDR

### Critical Findings
1.  **The "Rate Chain" Pattern**: Tutorials establish explicit variables for rate changes (e.g., `samp_rate_interpolated = samp_rate * interpolation_rate`).
    *   *Adaptation*: The agent should proactively manage these chains. If it adds an `Interpolating FIR Filter`, it should check if a `samp_rate_new` variable exists or needs to be created to keep downstream Sinks accurate.
2.  **Rational Resampling Glue**: Non-integer rate matching (e.g., 960Hz to 500Hz) is the standard "Glue" between hardware (fixed 48k) and symbol rates.
    *   *Adaptation*: If the agent detects a hardware block (Audio Sink, USRP), it should verify that the input sample rate matches the hardware's supported rates, and suggest a `Rational Resampler` if there is a mismatch.
3.  **Self-Documenting via Comments**: GRC blocks have a `comment` field that appears in the GUI.
    *   *Adaptation*: When the agent performs complex multi-block edits (like adding a resampling chain), it should use the `comment` field to explain *why* those blocks were added. This makes the agent's work transparent to a human opening the GRC file later.
4.  **Throttle Positioning**: Tutorials emphasize that `Throttle` should match the *current* sample rate of the stream it is on.
    *   *Adaptation*: The agent should always verify that `Throttle` blocks are updated if the sample rate of their branch changes due to decimation or interpolation.

### Testable Scenarios for Phase 7
*   `Scenario_Rate_Conversion`: Build a flowgraph that converts a 48kHz audio source to a 500Hz symbol stream using multiple decimation steps.
*   `Scenario_Rate_Validation`: Identify a mismatch between a `Signal Source` rate and an `Audio Sink` rate and fix it with a `Rational Resampler`.

---

## Section 8: Modulation & Demodulation

### Critical Findings
1.  **Mathematical Tuning Pattern**: To shift a signal's frequency without hardware tuning, tutorials use the `Signal Source` + `Multiply` pattern.
    *   *Adaptation*: The agent should have this pattern in its "Recipe Library." If a user says "shift the signal up by 10kHz," and there is no hardware block to tune, the agent should automatically insert a complex `Signal Source` and a `Multiply` block.
2.  **The "Variable Shadowing" Pattern**: Tutorials often replace a static `Variable` with a `QT GUI Range` by giving them the **same ID** and disabling the original.
    *   *Adaptation*: The agent must be comfortable with "ID Shadowing." It should know that having two blocks with ID `freq` is okay *if* one is disabled. This is a primary method for adding interactivity to a static graph.
3.  **Data Type Promotion**: Multiplying a real signal by a complex sinusoid produces a complex signal.
    *   *Adaptation*: When the agent performs mathematical frequency shifting, it must update all downstream blocks to `Complex` (blue ports), or it will create a "red arrow" mismatch error.

### Testable Scenarios for Phase 7
*   `Scenario_Math_Tuning`: Mathematically shift a centered signal to 5kHz using a complex mixer.
*   `Scenario_GUI_Interactivity`: Replace a static `samp_rate` variable with a `QT GUI Range` while keeping the ID identical (using the `disable` strategy).

---

## Section 9: Advanced SDR & Hardware

### Critical Findings
1.  **Metadata-Rich Filenames**: Tutorials encode sample rate and data type directly into filenames (e.g., `capture_100k.complex_float`).
    *   *Adaptation*: The agent should adopt this "Self-Documenting Filename" convention. If asked to record a signal, it should suggest a filename like `{name}_{rate}Hz.{type}`.
2.  **The "32768 Scaling" Rule**: Converting between `Short` (16-bit) and `Float` requires a scale factor of `2^15` (32768).
    *   *Adaptation*: This is a mandatory validation check. If the agent adds a `Complex to IShort` or `Short to Float` block, it MUST set the scale factor to `32768` (or its inverse) to ensure the signal doesn't clip or disappear.
3.  **Diagnosis by Magnitude**: The tutorial identifies `10^38` magnitude as a symptom of reading integers as floats (or endianness swap).
    *   *Adaptation*: If a user reports "extremely high values" in a time sink, the agent's diagnostic logic should prioritize checking the `File Source` data type and `Endian Swap` state.
4.  **Hardware Glue (Rational Resampler)**: This tutorial reinforces the need for `Rational Resampler` when moving from a stored file (at one rate) to hardware (at another rate).

### Testable Scenarios for Phase 7
*   `Scenario_File_Scaling`: Build a flowgraph that records a complex sinusoid to a file as 16-bit integers, ensuring correct scaling.
*   `Scenario_File_Replay`: Read a `.complex_float` file recorded at 1MHz and display it on a frequency sink with the correct axes.

---

## Section 10: Hardware & Real-World SDR

### Critical Findings
1.  **SDR-to-Audio Pipeline**: This is the "Benchmark Workflow" for SDR agents.
    *   *Path*: `SDR Source` (Complex) -> `Rational Resampler` -> `WBFM Receive` (Demod + Decimate) -> `Audio Sink` (Float).
    *   *Adaptation*: The agent should recognize "build an FM radio" as a request for this specific 4-block chain. It must calculate the resampler factor (`target / source`) and ensure the `WBFM Receive` decimation matches the audio card's rate (usually 48k).
2.  **Fractional Math for Resampling**: The tutorial simplifies `192000 / 2048000` to `3 / 32`.
    *   *Adaptation*: The agent's `apply_edit` tool could benefit from a "Simplified Fraction" helper when setting interpolation/decimation parameters to avoid using massive raw integers that tax the GNU Radio scheduler.
3.  **Layout Management (GUI Hints)**: Tutorials use the `GUI Hint` parameter (e.g., `0,0,1,10`) to stack sliders at the top of the window.
    *   *Adaptation*: When adding multiple `QT GUI Range` blocks, the agent should automatically increment the `GUI Hint` row index to keep the user's interface organized.
4.  **Audio "Silent Failure"**: The tutorial warns that `Quadrature Rate / Audio Decimation` must exactly equal the `Audio Sink` sample rate.
    *   *Adaptation*: This should be a hard validation rule in our preflight checks. If the math doesn't align, the agent should preemptively fix the decimation factor before saving.

### Testable Scenarios for Phase 7
*   `Scenario_FM_Radio`: Build a complete FM radio flowgraph from scratch for an RTL-SDR source.
*   `Scenario_Audio_Resample`: Connect a 2Msps source to a 48ksps audio sink using the correct mathematical resampler chain.

---

## Section 11: Intermediate/Advanced Core Mechanics

### Critical Findings
1.  **Tag Propagation Policies**: Blocks govern how metadata flows via three policies: `TPP_ALL_TO_ALL`, `TPP_ONE_TO_ONE`, and `TPP_DONT`.
    *   *Adaptation*: The agent must check the propagation policy of custom EPBs. If a block is "Eating Tags" (dropping metadata), the agent should check if `TPP_DONT` was set by mistake or if a `Tag Gate` is in the way.
2.  **Scheduler-Managed Offsets**: The GNU Radio scheduler automatically adjusts tag offsets during interpolation/decimation using the `relative_rate`.
    *   *Adaptation*: The agent should trust the scheduler for standard rate changes but should warn the user if using a custom `gr::block` (where `relative_rate` is 1.0 by default) that actually changes sample counts.
3.  **The "Group Delay" Nuance**: FIR filters introduce a sample delay (`(N-1)/2`).
    *   *Adaptation*: When the agent designs a high-precision synchronous system, it must account for this delay when interpreting tag positions downstream from a filter.
4.  **Tag Source Tracing**: Tags carry a `srcid` (source ID).
    *   *Adaptation*: The agent can use the `srcid` property in `get_tags_in_window` to filter for metadata originating from a specific block (e.g., only reacting to tags from the `UHD Source`).

### Testable Scenarios for Phase 7
*   `Scenario_Tag_Propagation`: Build a chain with an interpolator and verify that tags remain attached to the correct (first) item of the repeated sequence.
*   `Scenario_Tag_Strobe`: Use a `Tag Strobe` to trigger a downstream EPB state change every 1000 samples.

---

## Section 12: Core Mechanics & Polymorphism

### Critical Findings
1.  **Helper-First Coding**: GNU Radio provides `pmt.to_pmt()` and `pmt.to_python()` to bridge Python types and PMTs.
    *   *Adaptation*: The agent should prioritize these "Universal Converters" in its EPB code. They are more resilient to minor type errors (e.g., passing a float to a long converter) than the specific `from_long`/`to_double` family.
2.  **Dictionary Immutability**: PMT dictionaries are immutable; `dict_add` returns a new object.
    *   *Adaptation*: The agent's code generation logic must follow the pattern `metadata = pmt.dict_add(metadata, key, val)` rather than assuming in-place mutation.
3.  **PDU Standard Structure**: A PDU (Protocol Data Unit) is a PMT Pair of `(Dictionary . UniformVector)`.
    *   *Adaptation*: If the agent is tasked with "Packet-based processing," it should automatically look for this pair structure. It must know that `pmt.car(msg)` is the metadata and `pmt.cdr(msg)` is the actual data.
4.  **Special Constants**: `pmt.PMT_NIL` is the equivalent of `None`.
    *   *Adaptation*: The agent should use `pmt.eq(val, pmt.PMT_NIL)` for error checking in messages.

### Testable Scenarios for Phase 7
*   `Scenario_PMT_Conversion`: Create an EPB that converts a Python dictionary of parameters into a PMT dictionary and sends it as a message.
*   `Scenario_PDU_Parsing`: Create an EPB that receives a PDU, extracts a 'gain' key from the metadata, and scales the uniform vector by that amount.

---

## Section 13: Asynchronous Communication

### Critical Findings
1.  **Command-Style PMT Formats**: Tutorials standardize on two formats for block control: `pmt.cons(KEY, VALUE)` for single values and `pmt.dict` for multiple parameters.
    *   *Adaptation*: When the agent generates "Control Logic" (e.g., an EPB that tunes a USRP), it should always use these standardized PMT envelopes to ensure interoperability with built-in GNU Radio blocks.
2.  **Message Port Fan-in**: Unlike streaming ports (solid lines), message ports (dotted lines) allow multiple sources to connect to a single input.
    *   *Adaptation*: The agent's preflight validator should relax the "Single Source" rule for ports of domain `message`. It should also suggest merging multiple control signals into a single input port to simplify the graph.
3.  **The "Work vs Handler" Rule**: Tutorials strongly discourage processing messages inside the `work()` function (as it encourages blocking).
    *   *Adaptation*: The agent's code generator should always register a message handler in `__init__` and use it to update internal class state variables, which are then read by `work()`.
4.  **Flowgraph Poking**: Blocks can receive messages from external Python code via the `_post()` method.
    *   *Adaptation*: The agent can use this for "Remote Debugging." If a flowgraph is running but behaving poorly, the agent can write a temporary Python snippet to `_post` a reset or diagnostic message to a block's input port.

### Testable Scenarios for Phase 7
*   `Scenario_Message_FanIn`: Connect three different sources to a single `Message Debug` block and verify the connection logic.
*   `Scenario_Command_Control`: Create a flowgraph where a `Message Strobe` sends a `freq` command to a `Signal Source` using the `pmt.cons` format.

---

## Section 14: Flowgraph Organization & Libraries

### Critical Findings
1.  **Implicit Connectivity via Stream IDs**: Virtual Sinks and Sources connect through a shared string parameter called `Stream ID`.
    *   *Adaptation*: The agent's "Connectivity Graph" logic must be updated to treat matching `Stream ID` pairs as an active data path. When searching for the source of a signal, the agent should know to look across the "Virtual Boundary."
2.  **Functional Grouping**: Tutorials use Virtual Sinks to segment flowgraphs into "Signal Generation," "Simulated Effects," and "Plotting."
    *   *Adaptation*: The agent should adopt this "Modular Design" pattern for complex requests. Instead of a messy single-line graph, it can build discrete modules connected by Virtual Sinks, making the resulting GRC file much easier for the human user to navigate.
3.  **Type-Agnostic "Glue"**: Virtual blocks remain typeless (white ports) until they are connected to a typed block.
    *   *Adaptation*: The agent can use this to its advantage when "Pre-Plumbing" a complex graph. It can place all the Virtual Sinks/Sources first and let GRC resolve the data types once the final hardware or sink block is attached.

### Testable Scenarios for Phase 7
*   `Scenario_Virtual_Modular`: Build a flowgraph using three functional sections (Source, DSP, Sink) connected exclusively via Virtual Sinks.
*   `Scenario_Virtual_Ambiguity`: Add two Virtual Sinks with the same ID and verify that the agent can resolve the "Ambiguous Connection" error by renaming one.

---

## Section 15: External Dependencies & Environments

### Critical Findings
1.  **The "Import Block" Prerequisite**: Advanced math in parameters (like `np.arange` or `np.random.uniform`) only works if an `Import` block (e.g., `import numpy as np`) is present in the flowgraph.
    *   *Adaptation*: This is a high-priority "Agent Intelligence" buff. If the agent generates a parameter using a library function, it MUST verify the presence of the corresponding `Import` block. If the block is missing, the agent should proactively add it to the graph.
2.  **Unevaluated Variable Strings**: GRC treats variable values as raw Python strings until runtime.
    *   *Adaptation*: The agent should not attempt to "pre-calculate" complex strings into literals unless specifically asked. Keeping the symbolic expression (e.g., `np.random.rand()`) preserves the intended dynamic behavior of the flowgraph.
3.  **Library-Based Variable Chains**: Tutorials show variables derived from other variables using library methods (e.g., `reversed_vec = original_vec[::-1]`).
    *   *Adaptation*: The agent must be capable of tracing these "Logic Chains." It should understand that changing a root variable might affect the shape or type of a downstream variable in a way that breaks a block (e.g., a vector length mismatch).

### Testable Scenarios for Phase 7
*   `Scenario_Import_AutoAdd`: User asks to generate a randomized gain using NumPy. Agent must add both the `Variable` and the `Import` block.
*   `Scenario_Slicing_Logic`: Create a flowgraph where a `Vector Source` uses a sliced version of a NumPy array defined in a separate `Variable` block.

---

## Section 16: Communication Systems & Modulation

### Critical Findings
1.  **ZMQ-Based Loopback Simulation**: To simulate transceivers without SDR hardware, tutorials use `ZMQ PUB Sink` and `ZMQ SUB Source`.
    *   *Adaptation*: This is a foundational "Agent Verification" pattern. The agent can build and test complex transceiver logic (like NBFM or SSB) by creating a "Simulation Loopback" using ZMQ blocks. This allows the agent to prove the logic works even in a hardware-less CI environment.
2.  **Asymmetric Transceiver Chains**: Narrowband FM requires specific filtering and resampling steps:
    *   *TX Pipeline*: Band-pass filter (voice range) -> NBFM Transmit -> Low-pass filter (aliasing) -> Interpolation (for transmission).
    *   *RX Pipeline*: Decimation (from channel) -> FFT Filter -> Squelch -> NBFM Receive.
    *   *Adaptation*: The agent should have these "Standard Chain Templates" in its memory. Building a radio is not just connecting a modulator; it is the entire filtering/resampling ecosystem.
3.  **Squelch Logic**: Tutorials introduce `Simple Squelch` to mute noise when no carrier is present.
    *   *Adaptation*: The agent should proactively add a Squelch block to any receiver flowgraph to prevent "Static Noise" in the user's audio output.
4.  **PL Tones (Sub-audible Control)**: High-quality radio sims add a low-frequency tone (PL Tone) to the audio.
    *   *Adaptation*: The agent should understand that "Tones" are not just for testing; they are functional components of many real-world radio protocols.

### Testable Scenarios for Phase 7
*   `Scenario_NBFM_Loopback`: Build an NBFM transmitter and receiver that talk to each other over a local ZMQ socket.
*   `Scenario_Squelch_Config`: Add a squelch block to an FM receiver and set the threshold based on the observed noise floor.

---

## Section 17: Linear Modulation & Complex DSP

### Critical Findings
1.  **The "Analytical Signal" Prerequisite**: Single Sideband (SSB) modulation requires converting real audio to a complex analytical signal.
    *   *Adaptation*: The agent must know that a `Hilbert` filter is the mandatory "Front-End" for any linear modulation chain starting from a real source (like a microphone).
2.  **The "Complex Taps" Sideband Trick**: Tutorials use a `Band Pass Filter` with `Float -> Complex` type and complex taps to select the Upper Sideband (USB) by passing only positive frequencies.
    *   *Adaptation*: This is a sophisticated "DSP Recipe." The agent should prioritize this method for SSB requests as it is more computationally efficient in GNU Radio than traditional phasing methods.
3.  **Sideband Swapping via IQ**: The `Swap IQ` block is used to toggle between USB and LSB without changing filter parameters.
    *   *Adaptation*: This is a high-value "Tip" for the agent's troubleshooting and feature-addition logic. If a user asks to "invert the sideband," the agent should suggest inserting a `Swap IQ` block rather than recalculating filter taps.
4.  **Interactive Mode Selection**: The `Selector` block, paired with a `QT GUI Chooser`, allows users to switch between different DSP algorithms (e.g., Filter Method vs. Weaver Method) at runtime.
    *   *Adaptation*: The agent should adopt this "Multi-Algorithm" pattern for research-oriented flowgraphs, allowing the user to compare different demodulation strategies side-by-side.

### Testable Scenarios for Phase 7
*   `Scenario_SSB_Generation`: Build a USB transmitter from a real audio source using the Hilbert + Complex BPF method.
*   `Scenario_Sideband_Inversion`: Add a `Swap IQ` block and a `QT GUI Chooser` to an existing SSB flowgraph to allow runtime sideband selection.

---

## Section 18: Digital Communication

### Critical Findings
1.  **The "Carrier as DC" Paradigm**: In baseband equivalent simulations, the carrier itself is represented by a DC signal (value `1.0`).
    *   *Adaptation*: The agent should understand that for DSP-only logic, "Frequency" is relative to the center. It shouldn't be surprised to see a "Signal Source" at 0 Hz acting as the local oscillator for a baseband mixer.
2.  **The "XLating Filter" Power-User Block**: The `Frequency Xlating FIR Filter` is the preferred tool for combined frequency shifting, filtering, and decimation.
    *   *Adaptation*: This should be the agent's "Primary Recommendation" for channelized receivers. It is more robust and easier to manage than separate Multiply + Filter + Decimate blocks.
3.  **Spectral Symmetry as Diagnostic**: Real signals have symmetric spectra; complex signals do not.
    *   *Adaptation*: If the agent is asked to "identify if a signal is real or complex" from a text description of a waterfall, it should look for spectral symmetry around the center frequency.
4.  **IQ Modulator Standard Architecture**: Digital modulation (PSK, QAM) is built by combining two independent NRZ bitstreams into I and Q components.
    *   *Adaptation*: When asked to "build a custom constellation," the agent should use the pattern: `Bitstream -> Chunk to Symbols -> I/Q Mapping -> Complex Signal`.

### Testable Scenarios for Phase 7
*   `Scenario_Channel_Selection`: Build a flowgraph that uses a `Frequency Xlating FIR Filter` to pick out a 10kHz wide channel that is offset from the center by 25kHz.
*   `Scenario_RealToComplex_Waterfall`: Create a flowgraph that compares a real sinusoid and a complex sinusoid on a waterfall sink to demonstrate spectral (non)symmetry.

---

## Section 19: Advanced Modulation & Packet Systems

### Critical Findings
1.  **The "Constellation Object" Dependency**: The `Constellation Modulator` block does not define its own mapping; it requires a separate `Constellation Object` variable block.
    *   *Adaptation*: This is a mandatory "Structure Rule." If the agent adds a `Constellation Modulator`, it MUST also add a `Constellation Object` and link them via the block ID.
2.  **Differential Coding as Ambiguity Fix**: Digital links often suffer from "Phase Ambiguity" (e.g., bits being inverted).
    *   *Adaptation*: The agent should know that enabling "Differential Encoding" in the modulator and adding a "Differential Decoder" in the receiver is the standard fix for inverted bitstreams in PSK systems.
3.  **The "Subtraction Verification" Trick**: To prove a digital link works in simulation, tutorials subtract the (delayed) source bits from the received bits. A result of zero indicates a perfect link.
    *   *Adaptation*: This is an elite "Autonomous Testing" pattern. The agent can verify its own receiver designs by temporarily building this "Verification Branch" and checking the output statistics.
4.  **Matched Filtering (RRC)**: Root Raised Cosine (RRC) filters are required at both ends of a digital link to minimize Inter-Symbol Interference (ISI).
    *   *Adaptation*: The agent must ensure that if an RRC is used in the modulator, a matching RRC (or a block with an integrated RRC like `Symbol Sync`) is present in the receiver.

### Testable Scenarios for Phase 7
*   `Scenario_BPSK_Link`: Build a complete BPSK link from random bits to a constellation decoder, including differential coding to handle phase flips.
*   `Scenario_Link_Verification`: Use the "Subtraction Trick" to find the correct `Delay` value that synchronizes a transmitter and receiver bitstream.

---

## Section 20: Multi-Level Modulation & Spectral Efficiency

### Critical Findings
1.  **The "Delay Scaling" Formula**: In synchronized digital simulation links, the required alignment `Delay` is a function of Samples Per Symbol (`sps`) and bits per symbol (`k`).
    *   *Formula*: `Delay = int(5.5 * sps + 7) * k`.
    *   *Adaptation*: This is a precision "DSP Recipe" for the agent. When upgrading a flowgraph's modulation order (e.g., BPSK to 16-QAM), the agent must recalculate and update the `Delay` variable to maintain bit-perfect verification.
2.  **Unpacking Requirement**: M-ary demodulators output symbols containing `k` bits. To verify BER against a bit-source, a `K-bit Unpack` block is mandatory.
    *   *Adaptation*: The agent should automatically insert or reconfigure the `Unpack` block when changing the modulation order `M`. It must know that `k = log2(M)`.
3.  **One-Dimensional Constellations (ASK)**: For Amplitude Shift Keying, GNU Radio uses the `Constellation Rect Object`.
    *   *Adaptation*: The agent should prefer "Rect" constellation objects for 1D schemes (ASK) and standard objects for 2D schemes (PSK, QAM) to ensure the GRC UI and constellation sinks display the data correctly.
4.  **Modulus Parameter Sync**: The `Modulus` parameter in the `Constellation Modulator` must match the size of the `Constellation Object` symbol map.
    *   *Adaptation*: This is a high-priority "Consistency Check" for the agent's preflight validator. A mismatch here causes silent data corruption or crashes at runtime.

### Testable Scenarios for Phase 7
*   `Scenario_Modulation_Upgrade`: Upgrade a BPSK flowgraph to 16-QAM, including the recalculation of the verification `Delay` and updating the `K-bit Unpack` block.
*   `Scenario_ASK_Build`: Build a 4-ASK transmitter and receiver using the `Constellation Rect Object` pattern.

---

## Section 21: Frequency Modulation & Orthogonality

### Critical Findings
1.  **The "VCO-Based FSK" Recipe**: Frequency Shift Keying (FSK) is classically simulated using a `VCO` (Voltage Controlled Oscillator) block driven by a real bitstream.
    *   *Adaptation*: The agent should recognize "build an FSK transmitter" as a request for the `Bitstream -> VCO` pattern. It must calculate the `VCO Sensitivity` based on the desired Mark/Space frequency deviation.
2.  **Frequency-to-Voltage Demodulation**: FSK reception uses a `Quadrature Demod` block to translate frequency shifts into positive/negative voltage swings, which are then bit-sliced.
    *   *Adaptation*: The agent should know the standard FSK RX pipeline: `XLating Filter -> Quadrature Demod -> Binary Slicer`.
3.  **AGC as Symbol Sync Prerequisite**: The `Symbol Sync` block (used for timing recovery) expects an input signal with a normalized magnitude of 1.0.
    *   *Adaptation*: This is a high-priority "Structural Best Practice." The agent should always insert an `AGC` (Automatic Gain Control) block immediately before any `Symbol Sync` block to prevent timing drift in low-SNR or variable-power channels.
4.  **Byte Alignment via Access Codes**: Raw bitstreams from a slicer are "Un-aligned." Real-world packet reception requires a `Correlate Access Code` block.
    *   *Adaptation*: When the user's goal is "reliable data transfer," the agent must move beyond raw demodulation and add the "Packet Framing" layer (Preamble + Access Code + CRC).

### Testable Scenarios for Phase 7
*   `Scenario_FSK_VCO`: Build an FSK transmitter where bits `0` and `1` result in frequencies of `2125Hz` and `2295Hz` respectively.
*   `Scenario_Sync_Normalization`: Build an FSK receiver and verify that adding an `AGC` block before the `Symbol Sync` results in a stable eye diagram.

---

## Section 22: Multicarrier Systems & OFDM

### Critical Findings
1.  **The "Shifted FFT" Convention**: GNU Radio OFDM blocks expect vectors where the DC component is at the center (index `floor(N/2)`) rather than index 0.
    *   *Adaptation*: This is a mandatory "Validation Rule" for OFDM. If the agent adds an `FFT` or `IFFT` block into an OFDM chain, it MUST set the `Shift` parameter to `Yes`. Failure to do so will misalign all subcarriers.
2.  **Hierarchical Block Preference**: While OFDM can be built from primitives (`Carrier Allocator` -> `IFFT` -> `Cyclic Prefixer`), tutorials recommend using the high-level `OFDM Transmitter` and `OFDM Receiver` hierarchical blocks.
    *   *Adaptation*: The agent should prioritize these high-level blocks for any "standard" OFDM request. They encapsulate complex parameter synchronization (like matching cyclic prefix lengths) that are difficult to manage across multiple independent blocks.
3.  **Carrier Allocation Complexity**: Parameters like `occupied_carriers` use nested vectors (e.g., `((-2,-1,1,2),)`) to define subcarrier usage.
    *   *Adaptation*: The agent requires a "Carrier Layout Recipe." It should never attempt to calculate these subcarrier maps from first principles; instead, it should use documented "Golden Layouts" (e.g., standard 64-subcarrier WiFi-like maps) to ensure the flowgraph is valid.
4.  **Sync Schmidl & Cox**: Coarse frequency and timing synchronization in OFDM is almost always handled by the `OFDM Sync Schmidl & Cox` block.
    *   *Adaptation*: The agent should know that this specific block is the "Engine" of OFDM synchronization and should look for its presence when a user reports "Link failure" in a multicarrier system.

### Testable Scenarios for Phase 7
*   `Scenario_OFDM_Loopback`: Build a complete OFDM loopback link using the high-level hierarchical blocks and verify data flow.
*   `Scenario_OFDM_Manual_Build`: Disassemble an OFDM transmitter into its primitive components (Allocator, IFFT, CP) and verify that the `Shift` parameter is correctly applied to the IFFT.

---

## Section 23: Advanced Packet Systems & File Transfer

### Critical Findings
1.  **The "Header Format Object" Requirement**: Modern GNU Radio packet processing requires a `Header Format Object` (defined in a Variable block) to govern how `Protocol Formatter` and `Protocol Parser` blocks operate.
    *   *Adaptation*: The agent must know this "Object-Block Pairing." If it adds a `Protocol Formatter`, it MUST also add a `Variable` with a value like `digital.header_format_default(access_key, 0)`. Failure to provide this object results in a validation error.
2.  **The "Sync Anchor" (Access Code)**: The `Correlate Access Code - Tag Stream` block is the fundamental anchor for byte-alignment in a packet receiver.
    *   *Adaptation*: This is a high-value "Block Recommendation." If the user wants to "extract data from a stream," the agent should prioritize this block and ask the user for their 32-bit or 64-bit access code.
3.  **Asynchronous-to-Synchronous Bridges**: Packet systems typically use `Message Strobe` (Async/PDU) -> `PDU to Tagged Stream` (Bridge) -> `Modulator` (Sync/Stream).
    *   *Adaptation*: The agent must be comfortable bridging the "Message Domain" and the "Stream Domain." It should know that PDUs are for high-level packet logic, while Streams are for low-level sample processing.
4.  **CRC for Data Integrity**: High-quality packet tutorials always include `CRC Append` and `CRC Check` blocks.
    *   *Adaptation*: The agent should proactively add CRC blocks for any "File Transfer" or "Text Chat" request. It should explain that this allows the receiver to drop corrupted packets automatically.

### Testable Scenarios for Phase 7
*   `Scenario_Packet_Framing`: Build a flowgraph that takes a string message, appends a CRC, adds a protocol header, and outputs a tagged stream ready for modulation.
*   `Scenario_Packet_Extraction`: Build a receiver chain that takes a raw bitstream, searches for a 32-bit access code, and extracts the payload into a message debug block.

---

## Section 24: Complex System Integration

### Critical Findings
1.  **Mandatory Preamble/Postamble**: Robust packet links fail in hardware (and even high-fidelity simulation) without padding. Preambles allow sync loops (Costas, Symbol Sync) to lock, while postambles flush block-level buffers.
    *   *Adaptation*: The agent's "Packet Link Recipe" must include a 250-byte preamble and a 64-byte postamble by default. This ensures the first and last packets of a transfer are not lost.
2.  **Hardware-Safe Scaling (0.5 Rule)**: Most SDRs distort if the baseband magnitude exceeds 1.0. RRC filters often cause constellation points to "overshoot."
    *   *Adaptation*: The agent should always suggest a `Multiply Const (0.5)` block after the modulator for any flowgraph destined for hardware (USRP, Pluto, etc.) to ensure clean over-the-air transmission.
3.  **TED Gain/Amplitude Sensitivity**: Reducing transmit amplitude directly impacts the performance of the `Symbol Sync` block.
    *   *Adaptation*: The agent must know that these parameters are linked. If it reduces transmit gain, it should proactively suggest lowering the `Expected TED Gain` in the receiver's Symbol Sync block to maintain stable timing recovery.
4.  **USRP Offset Tuning Trick**: DC leakage (the "DC Spike") can ruin narrow-band signals.
    *   *Recipe*: Use `uhd.tune_request(frequency, 5e6)` for the sink and `uhd.tune_request(frequency, -5e6)` for the source.
    *   *Adaptation*: This is an "Elite Hardware Recipe." If the agent detects a USRP block, it should suggest this two-stage tuning syntax in the `Frequency` parameter field to move the leakage away from the signal of interest.

### Testable Scenarios for Phase 7
*   `Scenario_Hardware_Safe_TX`: Build a BPSK transmitter that includes a 250-byte preamble, RRC pulse shaping, and a 0.5 amplitude scaling factor.
*   `Scenario_USRP_Offset`: Configure a USRP Sink and Source pair using the `uhd.tune_request` syntax to avoid DC leakage at a center frequency of 2.4GHz.

---

## Section 25: Custom Modules & OOT Development

### Critical Findings
1.  **EPB vs. OOT Trade-offs**: Out-of-Tree (OOT) modules require a complex `cmake` -> `make` -> `sudo make install` lifecycle and environment-wide configuration (`ldconfig`, GRC block paths).
    *   *Adaptation (Strategic)*: The agent should **prioritize Embedded Python Blocks (EPB)** for custom logic. EPBs are self-contained within the `.grc` file, require no compilation, and don't pollute the system-wide block library. The agent should only suggest OOT modules for extremely complex, high-performance projects where C++ is mandatory.
2.  **YAML UI Contracts**: Every GNU Radio block (built-in or OOT) is defined by a YAML file that maps the Python/C++ code to the GRC interface.
    *   *Adaptation*: The agent can use its knowledge of YAML block definitions to "Reverse Engineer" a block's behavior. If `describe_block` isn't sufficient, the agent should know that the ground truth for a block's parameters and ports lies in its `.block.yml` file.
3.  **The "Import" Template**: YAML files use templates (e.g., `imports: from gnuradio import customModule`) to generate the final Python script.
    *   *Adaptation*: This reinforces the Section 15 finding: the agent must always manage imports as a distinct structural layer of the flowgraph.

### Testable Scenarios for Phase 7
*   `Scenario_Custom_EPB_Logic`: Implement a complex "Decision Logic" block as an EPB that cannot be achieved with standard GNU Radio blocks (e.g., a block that switches modulation based on a detected SNR threshold).

---

## Section 26: System Internals & Performance

### Critical Findings
1.  **The "Setter" Pattern**: When a variable is updated in a running GNU Radio flowgraph, it triggers a call to a `set_VARIABLE_NAME()` method in the Python `top_block`.
    *   *Adaptation*: The agent should understand that variables are not just static values; they are "Active Hooks." If the agent is asked to "make a parameter adjustable at runtime," it must ensure the block being controlled has a corresponding setter method in its API (e.g., `set_sampling_freq`).
2.  **GRC-to-Python Structural Mapping**: Every `.grc` file is a blueprint for a Python class inheriting from `gr.top_block`.
    *   *Adaptation*: This is the "Ground Truth" for the agent's mental model. When the agent "explains" a flowgraph, it should think of it as a Python class. This allows the agent to reason about things like `self.connect()` calls and class-level variable scope, which is essential for writing advanced Embedded Python Blocks that interact with other parts of the graph.
3.  **The "Wipeout" Warning**: Modifying the generated `.py` file manually is dangerous because GRC will overwrite it on the next "Generate" call.
    *   *Adaptation*: The agent MUST always perform its edits in the `.grc` file (via our toolset) rather than attempting to edit the output Python script. This preserves the "Source of Truth" and ensures the user can still use the GRC GUI after the agent is done.

### Testable Scenarios for Phase 7
*   `Scenario_Active_Hooks`: Identify which blocks in a flowgraph will be updated if the `samp_rate` variable is changed, by tracing the `set_samp_rate` callback logic.

---

## Section 27: Hardware Considerations & Real-World SDR

### Critical Findings
1.  **Interactive Point-and-Click Tuning**: The `QT GUI Sink` (Frequency/Waterfall) can send frequency commands back to the source block.
    *   *Adaptation*: This is a high-value "UI Feature Recipe." When building any live receiver, the agent should proactively connect the `freq` message output of the sink to the `command` input of the SDR source (USRP, RTL-SDR, etc.). This allows the user to double-click signals in the waterfall to tune the radio.
2.  **The "AGC for Demod" Rule**: Automatic Gain Control (AGC) is helpful for stable demodulation but harmful for spectral analysis (as it hides the noise floor).
    *   *Adaptation*: The agent should apply this heuristic: "If the user's goal is Analysis/Scanning, set Gain to Manual; if the goal is Reception/Decoding, suggest adding an AGC block."
3.  **The "O" Overrun Diagnostic**: The letter 'O' appearing in the terminal is the standard GNU Radio indicator for "Overrun" (CPU cannot keep up with samples).
    *   *Adaptation*: This is a critical "SDR Diagnostic." If the user reports 'O's in the log, the agent should immediately recommend reducing the `samp_rate` or optimizing the flowgraph (e.g., using FFT Filters instead of FIR Filters).
4.  **Hardware-Visible Bandwidth**: For complex quadrature sampling, the visible bandwidth is exactly equal to the `samp_rate`.
    *   *Adaptation*: When the user says "I want to see the whole 2MHz FM band," the agent must know to set the `samp_rate` of the SDR source to at least 2Msps.

### Testable Scenarios for Phase 7
*   `Scenario_PointAndClick`: Build a spectrum analyzer with a USRP source and a QT GUI Sink, ensuring the message ports are cross-connected for interactive tuning.
*   `Scenario_Overrun_Fix`: Diagnose a flowgraph reporting overruns and implement an optimization (e.g., decimation) to reduce CPU load.

---

## Section 28: Timing & Sample Rate Fundamentals

### Critical Findings
1.  **The "Backwards Timing" Rule**: When a hardware sink (like an `Audio Sink` or `USRP Sink`) is present, it acts as the master clock for the entire flowgraph. All upstream sample rates must be derived by working backwards from the sink's fixed rate.
    *   *Adaptation*: The agent must use "Backwards Propagation" for rate validation. If the `Audio Sink` is set to 48kHz, the agent should verify that all interpolators and decimators between the source and sink result in exactly 48,000 samples per second arriving at that sink.
2.  **The "Hardware vs. Throttle" Conflict**: `Throttle` blocks are for simulation only. Using a `Throttle` in the same path as a hardware block creates a "Two-Master" conflict that causes timing jitter and eventual flowgraph hang.
    *   *Adaptation (Strict Rule)*: The agent's preflight validator MUST flag an error if a `Throttle` block exists in a flowgraph that also contains a hardware source or sink. The agent should proactively offer to remove the `Throttle` to restore hardware timing.
3.  **Aliasing as a Diagnostic**: Frequencies exceeding `samp_rate / 2` will appear at incorrect positions (Aliasing).
    *   *Adaptation*: If a user reports "The signal is at the wrong frequency in the sink," the agent should check if the `Signal Source` frequency exceeds the Nyquist limit of the current `samp_rate`.
4.  **Hardware Rate Limits**: Many SDRs and sound cards only support specific discrete sample rates (e.g., 48k, 44.1k, 1Msps, 2Msps).
    *   *Adaptation*: The agent should have a "Hardware Capability Table" (or query one) to ensure it doesn't suggest an unsupported rate like 30,000 Hz for a standard audio card.

### Testable Scenarios for Phase 7
*   `Scenario_Rate_Backwards`: Given an `Audio Sink` at 48k and a `Repeat` block (x4), correctly calculate and set the upstream `Signal Source` frequency and `samp_rate`.
*   `Scenario_Throttle_Conflict`: Identify a flowgraph containing both a USRP Sink and a Throttle block and resolve the conflict by removing the Throttle.

---

## Section 29: Network Integration & Distributed DSP

### Critical Findings
1.  **The "Single Binder" Toplology**: ZeroMQ requires exactly one `bind` per endpoint. In standard GNU Radio stream configurations, Sinks `bind` (listen) and Sources `connect`.
    *   *Adaptation*: This is a mandatory "Architecture Rule." If the agent sets up a distributed system, it must ensure the sink is the binder. For ZMQ Message blocks (which allow manual selection), the agent should default to `Bind: True` for sinks and `Bind: False` for sources to remain consistent with the streaming blocks.
2.  **Localhost Optimization**: Tutorials recommend using `127.0.0.1` instead of `localhost` or `*` for inter-process communication on the same machine to minimize lookup overhead and security risk.
    *   *Adaptation*: The agent should always suggest `tcp://127.0.0.1:PORT` for local loopback simulations (like the Section 16 transceiver sim).
3.  **ZMQ Wire Format & Tags**: Enabling "Pass Tags" in a ZMQ Sink prepends metadata to the raw sample stream.
    *   *Adaptation*: This is a high-risk "Silent Failure" point. The agent's preflight validator should verify that if "Pass Tags" is enabled on a ZMQ Sink, it is also enabled on the corresponding ZMQ Source. A mismatch here will cause the source to interpret metadata headers as raw samples, resulting in "Garbage" output.
4.  **ZMQ Message Serialiation**: Messages sent via ZMQ Message blocks use PMT serialization.
    *   *Adaptation*: If the agent writes an external Python script to "poke" a flowgraph (via Section 13), it must use the `pmt.serialize_str()` and `pmt.deserialize_str()` methods to maintain compatibility with the ZMQ Message blocks.

### Testable Scenarios for Phase 7
*   `Scenario_ZMQ_Distributed`: Build a two-part system where a "Transmitter" flowgraph sends samples to a "Receiver" flowgraph over ZMQ, ensuring `bind`/`connect` and `Pass Tags` are perfectly synchronized.
*   `Scenario_ZMQ_External_Poke`: Write an Embedded Python Block that acts as a ZMQ REQ server, and provide a standalone Python snippet that sends a command to that block to update a local variable.

---

## Section 30: Real-World Applications & Edge Detection

### Critical Findings
1.  **The "Log Power FFT" Shortcut**: Tutorials use the `Log Power FFT` block as a high-level "All-in-One" tool for spectral analysis.
    *   *Adaptation*: The agent should prioritize this block over the primitive `Stream to Vector -> FFT -> Complex to Mag^2` chain. It simplifies the graph and reduces the chance of windowing or scaling errors.
2.  **Vector-Based GUI Markers**: Visual boundaries (vertical/horizontal lines) can be drawn in a `QT GUI Vector Sink` by creating specific patterns in a `Vector Source`.
    *   *Recipe (Horizontal)*: `(threshold_variable,)*fft_size`.
    *   *Recipe (Vertical)*: `(position_variable)*(low_val,) + (high_val,) + (fft_size-position_variable-1)*(low_val,)`.
    *   *Adaptation*: This is an "Elite UI Recipe." If the user wants to "see the detection threshold," the agent should use these mathematical string patterns to draw active markers in the GUI.
3.  **DSP via Vector Masking**: You can "gate" or "isolate" portions of a spectrum by adding a mask vector (zeros in the passband, extremely low numbers in the stopband).
    *   *Adaptation*: The agent can use this "Masking Logic" to implement software-defined squelch or sub-band filtering without using dedicated FIR filter blocks, making it highly adjustable at runtime.

### Testable Scenarios for Phase 7
*   `Scenario_LogPower_Monitor`: Build a spectrum monitor that uses a `Log Power FFT` block and displays the results on a vector sink.
*   `Scenario_Visual_Threshold`: Add a draggable horizontal threshold line to a spectral plot using the `(var,)*N` vector source pattern.

---

## Section 31: Advanced Automation & File Management

### Critical Findings
1.  **The "/dev/null" Recording Toggle**: Tutorials implement a "Push-to-Record" feature by using a Python ternary conditional in the `File` parameter of the `File Sink` block.
    *   *Recipe*: `filepath if record_button == 1 else "/dev/null"`.
    *   *Adaptation*: This is a high-value "Control Logic Recipe." It allows the agent to implement gated recording (by button, by squelch state, or by SNR) without needing complex state-machine blocks or custom Python sinks.
2.  **Portable Path Management**: To ensure flowgraphs work on any machine, tutorials use the `Import` block with `import os` and a variable `home_dir = os.path.expanduser('~')`.
    *   *Adaptation*: The agent MUST adopt this "Portable Path" pattern. It should never hardcode absolute paths like `/home/user/data`; instead, it should build paths relative to a `home_dir` variable to ensure the flowgraph is robust across different Linux/macOS environments.
3.  **Metadata-Encoded Filenames**: Filenames are treated as "Active Metadata Carriers," including frequency, sample rate, gain, and custom user notes.
    *   *Adaptation*: When the agent is tasked with "recording a signal," it should build a dynamic filename string like `f"{home_dir}/captures/{note}_{freq}Hz_{samp_rate}sps.cfile"`. This ensures the recording remains self-describing during post-analysis.
4.  **Real-Time Note Entry**: The `QT GUI Entry` block allows users to type descriptions that are immediately reflected in the generated filename (if using the pattern above).
    *   *Adaptation*: The agent should suggest this block for "Signal Cataloging" or "Field Research" scenarios, where labeling a capture is just as important as the samples themselves.

### Testable Scenarios for Phase 7
*   `Scenario_Gated_Recorder`: Build a flowgraph that only records to a timestamped file when a `QT GUI Check Box` is checked, otherwise streaming to `/dev/null`.
*   `Scenario_Portable_Sinks`: Configure a `File Sink` that uses an `os.path` based variable to save data into the user's Documents folder regardless of their username.

---

## Section 32: External Integration & Python Control

*Scope note*: Most of this tutorial is external PyQt application code, which sits outside `grc-agent`'s single-file `.grc` boundary. The findings below keep only the reusable `.grc`-side patterns.

### Critical Findings
1.  **Qt GUI Blocks Are the Embed-Ready Boundary**: The generated Python wraps `QT GUI` sinks as ready-made Qt widgets (`*_win`) that can be inserted directly into a host application.
    *   *Adaptation*: Add a rule to the system prompt for "embed this graph," "make it app-ready," or "prepare a control panel" requests: prefer standard `QT GUI` sinks, ranges, and entries over ad-hoc alternatives so the resulting `.grc` already exposes embeddable widgets without extra structural edits.
2.  **Setter Fanout Must Stay Coordinated**: The tutorial's generated `set_freq()` updates both the SDR source and every frequency-domain display. One control variable often fans out to multiple GUI and hardware consumers.
    *   *Adaptation*: Add a rule to inspect the shared control variable first (`freq`, `samp_rate`, gain-like variables). If a variable already drives the graph, edit that variable instead of one consumer block. If the graph duplicates literals instead of using a shared variable, update every dependent hardware/GUI field in one ordered transaction so the radio state and displays do not drift apart.
3.  **Runtime Rewiring Requires a Full Stop/Wait Cycle**: The tutorial's recording toggle uses `stop()` and `wait()` before every `connect()` or `disconnect()` call, then restarts the flowgraph.
    *   *Adaptation*: Add a rule to the system prompt that `grc-agent` should not treat live rewiring as the default `.grc` editing recipe. For requests like conditional recording or optional monitoring, prefer static single-file patterns the agent can actually express (pre-wired `QT GUI` sinks, `Selector`-style routing, or parameter/file-path gating) instead of proposing runtime `connect()` / `disconnect()` control that lives outside the tool boundary.

### Testable Scenarios for Phase 7
*   `Scenario_Embed_Ready_Spectrum`: User asks to make a receiver graph "PyQt-ready." Agent adds a `QT GUI Frequency Sink` to the existing complex stream so the generated flowgraph exposes an embeddable spectrum widget.
*   `Scenario_Coordinated_Retune`: Given a receiver graph with a shared `freq` control, user asks to retune to `102.3M`. Agent updates the shared variable—or, if no shared variable exists, updates the SDR source and frequency-domain sink parameters together in one transaction.
*   `Scenario_Record_Toggle_No_Rewire`: User asks for start/stop recording behavior in a standard `.grc` graph. Agent implements a static gated-recording pattern and does **not** solve it with `add_connection` / `remove_connection` churn that would require external runtime control.
