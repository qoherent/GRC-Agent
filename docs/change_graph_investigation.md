# Investigation Report: `change_graph` Layout Failure & Agent Degradation

## 1. Executive Summary

During complex flowgraph modifications, the agent frequently encountered internal tool failures returning:
```text
Graph modification failed. Errors: [{'code': 'mutation_failed', 'message': "'DummyVertex' object has no attribute 'data'"}]. Adjust your parameters/connections based on the errors above and retry — force=True will not help here.
```

This error occurred whenever an edit created a **skip-layer connection** (an edge where $\text{rank}(dst) - \text{rank}(src) > 1$). The agent had no insight into the tool-level layout crash, mistook it for an invalid GNU Radio block combination (e.g. connecting multiplier to adder blocks), and entered an extensive retry loop (100+ turns in Session 95) before abandoning its intended design.

---

## 2. How to Re-inspect the Issue from the Chat Database

The local chat history is stored in `.grc_agent/chat_sessions.db` in SQLite format.

### A. List Sessions with the Error
To locate sessions affected by the `DummyVertex` error:

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('.grc_agent/chat_sessions.db')
cursor = conn.cursor()
cursor.execute('SELECT id, grc_file_path, updated_at FROM sessions WHERE messages LIKE \"%DummyVertex%\"')
for row in cursor.fetchall():
    print(f'Session ID: {row[0]} | Path: {row[1]} | Updated: {row[2]}')
"
```

### B. Extract Error Events & Tool Calls from Session 95
To inspect the tool calls and retry prompts where the agent encountered the crash:

```bash
python3 -c "
import sqlite3, json

conn = sqlite3.connect('.grc_agent/chat_sessions.db')
cursor = conn.cursor()
cursor.execute('SELECT messages FROM sessions WHERE id = 95')
row = cursor.fetchone()
if row and row[0]:
    msgs = json.loads(row[0])
    for i, m in enumerate(msgs):
        for p in m.get('parts', []):
            pk = p.get('part_kind')
            if pk == 'retry-prompt' and 'DummyVertex' in str(p.get('content')):
                print(f'=== Msg {i} RETRY ===\n{p.get(\"content\")}\n')
            elif pk == 'tool-call' and p.get('tool_name') == 'change_graph':
                args = p.get('args', {})
                if 'add_connections' in args:
                    print(f'Msg {i} change_graph connections: {args.get(\"add_connections\")}')
"
```

### C. Minimal Standalone Reproduction
To reproduce the crash directly without the agent harness:

```bash
uv run python -c "
from grc_agent.adapter.layout import _compute_layout_model
from grc_agent.adapter.graph import get_platform

p = get_platform()
fg = p.make_flow_graph()

# Create 3 blocks
b_src = fg.new_block('analog_sig_source_x')
b_src.params['id'].set_value('src')
b_mix = fg.new_block('blocks_multiply_xx')
b_mix.params['id'].set_value('mix')
b_add = fg.new_block('blocks_add_xx')
b_add.params['id'].set_value('add')
b_add.params['num_inputs'].set_value('2')
fg.rewrite()

# Connect src -> mix (rank 0 -> 1), mix -> add (rank 1 -> 2), and src -> add (rank 0 -> 2, skip-layer!)
fg.connect(b_src.sources[0], b_mix.sinks[0])
fg.connect(b_mix.sources[0], b_add.sinks[0])
fg.connect(b_src.sources[0], b_add.sinks[1])

# Fails with AttributeError: 'DummyVertex' object has no attribute 'data'
model = _compute_layout_model(fg, set(), [])
"
```

---

## 3. Root Cause Findings

### 1. Unhandled `DummyVertex` in Grandalf Layout Ordering
- **Location**: [`src/grc_agent/adapter/layout.py`](../src/grc_agent/adapter/layout.py) lines 119–121.
- **Mechanism**: In Grandalf's Sugiyama layout implementation, `sug.init_all()` creates intermediate `grandalf.layouts.DummyVertex` objects to route multi-rank edges through intervening layers.
- **The Bug**: Line 120 executes:
  ```python
  for layer in sug.layers:
      layer.sort(key=lambda v: v.data)
      layer.setup(sug)
  ```
  While regular `Vertex` instances store their name in `.data`, `DummyVertex` instances have no `.data` attribute. Accessing `v.data` raises an unhandled `AttributeError`.
- While line 136 guarded against dummy vertices (`if not getattr(sug.grx[v], "dummy", 0)`), line 120 omitted this guard. In addition, the `try...except` in `_rank_and_order_component` wrapped `sug.init_all()` but not the layer sort loop.

### 2. Fatal Coupling of Cosmetic Layout to Semantic Mutations
- **Location**: [`src/grc_agent/adapter/graph.py`](../src/grc_agent/adapter/graph.py) lines 1088–1098.
- **Mechanism**: Auto-layout is invoked inside `change_graph` whenever blocks or connections are modified.
- **The Bug**: Any unhandled exception during layout calculation causes `change_graph` to abort in its top-level exception handler, roll back all flowgraph modifications, and report `mutation_failed`. Cosmetic coordinate failures should not roll back valid semantic graph changes.

### 3. Misleading Error Reporting & Retry Loop Trap
- **Location**: [`src/grc_agent/agent.py`](../src/grc_agent/agent.py) lines 646–653.
- **Mechanism**: Tool-level crashes (`mutation_failed`) are passed to the model as retry prompts with the hint:
  *"Adjust your parameters/connections based on the errors above and retry — force=True will not help here."*
- **The Trap**: The agent is told that its inputs are wrong rather than that the tool crashed. In Session 95, this led the agent to spend 100+ turns testing dummy connections, creating isolation blocks, and abandoning its desired receiver architecture.

---

## 4. Recommendations

### Immediate Fixes
1. **Defensive Vertex Sorting in `layout.py`**:
   - Filter or safely access `.data` during layer sorting:
     ```python
     for layer in sug.layers:
         layer.sort(key=lambda v: getattr(v, "data", ""))
         layer.setup(sug)
     ```
   - Extend the `try...except` in `_rank_and_order_component` to cover the entire ordering pass so any layout algorithm failure falls back cleanly to default positioning.

2. **Fault-Tolerant Auto-Layout in `graph.py`**:
   - Wrap `compute_full_layout` in a non-fatal `try...except`. If layout calculation fails, log a warning and retain fallback positions rather than rolling back the flowgraph mutation.

### Architectural Improvements
1. **Error Classification**:
   - Clearly separate **Internal Tool Failures** (Python exceptions, bugs in adapter code) from **Domain Validation Errors** (invalid GNU Radio parameters, port mismatches).
   - Internal tool errors should not instruct the LLM to "adjust parameters".
2. **Granular Connection Diagnostics**:
   - Provide explicit failure context (e.g. source/sink block names, port indices, and datatypes) when a connection fails, eliminating the need for the agent to bisect batches manually.
