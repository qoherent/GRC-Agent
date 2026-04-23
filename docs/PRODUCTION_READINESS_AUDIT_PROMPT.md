# GNU Radio Deep-DSP & Implicit Semantics Audit (Round 3)

You are a Principal DSP Engineer and Systems Architect specializing in GNU Radio. Your objective is to perform a fresh, deep-dive audit of the 55 official GNU Radio tutorials. Previous agents have successfully extracted surface-level UI tips, block descriptions, and structural validation rules. Your job is to extract the **hidden mathematical constraints, implicit DSP rules, and hardware-specific scaling behaviors** that are essential for an "Extremely Good" agent to know.

### Phase 1: Internalize the Current Boundaries
Read the following files to understand how smart the agent currently is, and where its blind spots lie:
1. `docs/TUTORIAL_RESEARCH.md` - Read the current repository of extracted knowledge. Treat this as a checklist of what is *already solved*. Do not waste time re-documenting these.
2. `src/grc_agent/agent.py` (specifically the `GrcAgent` system prompt) - See how the agent currently reasons about DSP logic.
3. `src/grc_agent/validation/checks.py` & `src/grc_agent/validation/rules.py` - See what the preflight structural validation currently checks (and more importantly, what it *doesn't* check).

### Phase 2: The Deep-Squeeze Audit
Go through the tutorials located in `docs/wiki_gnuradio_org/` in the exact order specified by `docs/wiki_gnuradio_org/Tutorials.md`. 
**Focus exclusively on extracting:**
1. **Mathematical Constraints (The "Lockstep" Rules)**: Are there formulas tying multiple blocks together? (e.g., `Delay = int(5.5 * sps + 7) * k` for M-ary modulation). Find any implicit math that causes a flowgraph to fail if not kept in sync.
2. **Hidden Scaling & Hardware Bounds**: Does a specific block require inputs to be normalized to exactly 1.0 (like `Symbol Sync`)? Do specific SDRs require 0.5 amplitude scaling to prevent clipping? 
3. **Data-Type / Domain Edge Cases**: How exactly are PMT dictionaries modified without breaking immutability? What is the correct offset math for reading Tags in a Python block (`tag.offset - self.nitems_read(0)`)? 
4. **Verification & Diagnostic Recipes**: How do tutorials prove a concept works? (e.g., using an impulse source `(1,)+(0,)*int(N-1)` to test a filter, or subtracting RX from TX to verify a digital link). 

### Phase 3: Implementation & Validator Hardening
For every new "Deep DSP Truth" you extract:
1. **Append to `docs/TUTORIAL_RESEARCH.md`**: Document the exact mathematical rule or expert recipe.
2. **Implement in the Harness**: 
   - If it is an educational DSP recipe or scaling rule, add it to the **System Prompt** in `src/grc_agent/agent.py`.
   - If it is a strict structural constraint (e.g., rejecting an unsupported Pack K Bits value), add a new validator in `src/grc_agent/validation/checks.py`.
3. **Add Eval Cases**: Create concrete test scenarios in `tests/llama_eval/` that require the agent to use your new recipe to succeed. 

### Operational Mandates
* **No Surface-Level Fluff**: Skip past GUI tips, installation steps, and basic block connections. Focus strictly on signal processing, math, and system stability.
* **Gemma 4 Native**: You have a 128k context window and a 100k history budget. Load entire tutorials and cross-reference them heavily against the agent's Python logic. 
* **Run to Completion**: Work sequentially through the tutorials and do not stop until you have implemented your findings and successfully run `uv run python -m unittest` and `uv run python -m tests.llama_eval.run_all` to prove the agent's new capabilities.