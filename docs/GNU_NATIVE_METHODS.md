# GNU Radio Native Methods for Parameter Filtering

This document catalogs every native GRC (GNU Radio Companion) method
available for filtering, ordering, and classifying block parameters.
These are the platform's own APIs — not heuristics, not regexes, not
hand-picked allowlists. They are the same signals GRC's own GUI uses
to decide what to show, where to show it, and in what order.

## When to use this

Any code path that selects which parameters to expose to the model
(catalog search, graph inspection, embed text composition, mutation
candidates) should consult this document and use the same native
signals. The principle is: **GRC already knows which params matter;
read its evaluation, don't reimplement it.**

---

## Method 1: `hide` attribute (runtime visibility)

**Source**: `gnuradio.grc.core.blocks._build` + block YAML `<hide>` field

**API**: `grc_agent.runtime.block_semantics.evaluated_param_hides(block_type, param_values) -> dict[str, str]`

**Values**: `'none'` | `'part'` | `'all'`

| Value | Meaning | GRC GUI behavior |
|-------|---------|------------------|
| `'none'` | Always visible | Full form in properties dialog |
| `'part'` | Conditionally visible | Reduced form (e.g., collapsed row) |
| `'all'` | Hidden | Not shown |

The `hide` field in block YAML can be:
- A literal string (`hide: 'all'`)
- A Mako expression evaluated at runtime (`hide: "${ ('none' if nconnections > 1 else 'all') }"`)

`evaluated_param_hides` instantiates a throwaway flow graph, sets param
values, calls `flow_graph.rewrite()`, and reads the evaluated `hide`
attribute. This correctly resolves conditional expressions.

**Filtering rule**: `hide != 'all'` to keep visible params.

**Latency**: ~20ms per block (flow graph instantiation). Cached in
`_EVALUATED_HIDE_CACHE` keyed on `(block_type, param_values)`.

**Used by**:
- `inspect_graph._param_keys_by_block` (line 750)
- `inspect_graph._param_detail_payload` (prominence sort, line 578)
- `catalog_vector._visible_param_keys` (embed text filter)
- `search_blocks._compact_catalog_details` (catalog output filter)

---

## Method 2: `category` attribute (GUI tab grouping)

**Source**: `gnuradio.grc.core.Constants` + block YAML `<tab>` field

**API**: `param.category` (attribute on the live GRC `Param` object)

**GRC constants**:
```python
DEFAULT_PARAM_TAB = "General"       # functional params (block-defined)
ADVANCED_PARAM_TAB = "Advanced"     # GRC auto-added metadata
```

When a block YAML does not specify `<tab>`, the param defaults to
`DEFAULT_PARAM_TAB` ("General"). Block authors can create custom
categories (e.g., "Config", "RF Options", "Trigger").

### Category census (measured across 564 catalog blocks)

| Category | Unique params | Blocks | Content type |
|----------|--------------|--------|-------------|
| **General** | 3482 | 563 | Functional (type, samp_rate, freq, gain, etc.) |
| **Advanced** | 2485 | 563 | GRC auto-added (alias, affinity, comment, minoutbuf, maxoutbuf) |
| **RF Options** | 646 | 17 | SDR RF tuning (center_freq, gain, antenna, bandwidth) |
| **Config** | 498 | 10 | **100% styling** (color, alpha, marker, style, width, label) |
| **FE Corrections** | 128 | 2 | Frontend IQ/DC corrections |
| **Trigger** | 21 | 4 | Scope trigger configuration |
| Filter / Fields / TX1 / TX2 / Optional | ~73 | various | Block-specific functional |

### Key findings

1. **`ADVANCED_PARAM_TAB` ("Advanced")** is always GRC auto-added metadata.
   Defined in `gnuradio.grc.core.blocks._build` lines 116-146: alias,
   affinity, minoutbuf, maxoutbuf, comment. Same for every block. Never
   block-specific.

2. **`"Config"`** is 100% styling. Verified: searched all 84 unique param
   names in Config for functional keywords (freq, gain, rate, type, samp,
   center, antenna, bw, bandwidth). **Zero matches.** Every Config param
   is a color, alpha, marker, style, width, label, or display toggle.
   Exists only in 10 qtgui GUI sink/source blocks.

3. **`"RF Options"`** is functional. Contains `center_freq0`, `gain0`,
   `ant0`, `bw0`, `dc_offs_enb0` — real SDR tuning params. Dropping this
   category would hide the most important USRP/pluto params.

**Filtering rule**: exclude `ADVANCED_PARAM_TAB` and `"Config"`. Include
all other categories (General, RF Options, FE Corrections, Trigger, etc.).

---

## Method 3: Prominence ordering

**Source**: `inspect_graph._param_detail_payload` (line 578)

**API**: Sort by `hide` value: `'none'` first, then `'part'`, then `'all'`.

```python
key=lambda candidate: (
    0 if evaluated_hides.get(candidate.param_key) == "none"
    else 1 if evaluated_hides.get(candidate.param_key) == "part"
    else 2
)
```

This is GRC's own GUI ordering: always-visible params appear first in
the properties dialog, conditionally-visible params appear after.

**Used by**: `inspect_graph._param_detail_payload`,
`search_blocks._compact_catalog_details`

---

## Method 4: `_is_configured_or_prominent` (value-based filtering)

**Source**: `inspect_graph._is_configured_or_prominent` (line 659)

**API**: Function that returns `True` when:
1. `hide != 'all'` (not hidden), AND
2. `hide == 'none'` (always prominent), OR
3. The param's value differs from its default (user has configured it), OR
4. The param's value references a graph variable

This is the tightest filter — used by `inspect_graph` when no specific
params are requested. It shows only params the user has actually
changed, plus always-visible ones.

**Not suitable for catalog search** (no instance values), but useful for
graph inspection where we want to show "what's configured" rather than
"what exists".

---

## Method 5: Block role classification

**Source**: `grc_agent.runtime.block_semantics._semantic_role`

**API**: Reads GRC's `Block.is_variable`, `Block.is_import`,
`Block.is_snippet`, `Block.is_virtual_or_pad` native booleans.

Classifies blocks into roles: `dsp`, `variable_or_control`, `source`,
`sink`, `gui`, `import`, `snippet`, etc. Used by `inspect_graph` to
label blocks in the topology view.

**Not a param filter** — a block-level classifier. Documented here for
completeness.

---

## Combined filtering recipe (catalog search / discovery)

For `search_blocks._compact_catalog_details`:

```python
from gnuradio.grc.core.Constants import ADVANCED_PARAM_TAB

EXCLUDED_CATEGORIES = {ADVANCED_PARAM_TAB, "Config"}

# 1. Evaluate hide with actual param defaults
hides = evaluated_param_hides(block_id, param_values)

# 2. Get categories from the live GRC block
param_cats = {name: getattr(p, "category", "General")
              for name, p in block.params.items()}

# 3. Filter: visible AND not Advanced AND not Config
visible = [
    p for p in raw_params
    if hides.get(p["id"], "all") != "all"
    and param_cats.get(p["id"], "General") not in EXCLUDED_CATEGORIES
]

# 4. Sort by prominence: hide='none' first, then 'part'
visible.sort(key=lambda p: (
    0 if hides.get(p["id"]) == "none"
    else 1 if hides.get(p["id"]) == "part"
    else 2
))

# 5. Return id/label/dtype/default only — no options/option_labels
#    (discovery context; inspect_graph provides options when editing)
```

### Measured sizes (per block, no options)

| Block | Params | Tokens | Includes |
|-------|--------|--------|----------|
| qtgui_time_sink_x | 20 | 396 | type, srate, size, nconnections, trigger config |
| uhd_usrp_source | 23 | 438 | samp_rate, center_freq0, gain0, ant0, bw0 |
| blocks_throttle2 | 5 | 89 | type, samples_per_second, vlen, ignoretag, limit |
| blocks_add_xx | 3 | 51 | type, num_inputs, vlen |
| analog_sig_source_x | 8 | 158 | type, samp_rate, waveform, freq, amp, offset, phase |

### What gets dropped

| Dropped | Why | Native signal |
|---------|-----|---------------|
| color1..10, alpha1..10, marker1..10, style1..10, width1..10, label1..10 | GUI styling | `category == "Config"` (100% styling, verified) |
| alias, affinity, comment, minoutbuf, maxoutbuf | GRC auto-added metadata | `category == ADVANCED_PARAM_TAB` |
| Per-channel device params beyond active channels | Conditionally hidden | `hide == "all"` (evaluated) |
| Options/option_labels (e.g., 10 color names) | Discovery context | Not dropped by GRC — dropped by us because `inspect_graph` provides them when editing |

---

## Changelog

- 2026-06-19: Initial documentation. Methods 1-5 cataloged from GRC
  platform source code and verified against 564-block catalog.
