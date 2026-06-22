# GNU Radio Native Methods — Consolidated Reference

> **Single source of truth** for the `gnuradio.grc.core` Python API surface and the
> native filtering/visibility rules the GRC Agent uses to classify blocks and
> parameters. This document supersedes the previous split between
> `GRC_Core_API_Surface3.md` and `GNU_NATIVE_METHODS.md`.
>
> **Scope:** GRC 3.10.9.2 (`/usr/lib/python3/dist-packages/gnuradio/grc/core/`).
> **Audience:** agents implementing the native adapter, the model-visible tool
> handlers, and the GUI inspector. **Not a GRC user manual** — it documents the
> Python bindings the agent consumes, not the GRC UI.
>
> **Status:** Current for post-Phase-7 architecture. The native adapter at
> `grc_native_adapter.py` implements the load/inspect/mutate/validate pipeline
> described here. `param_filter.py` (§3.5) is the single filtering authority.
> `domain_models.py` exposes the typed LLM-facing schemas.

---

## When to use this

Any code path that decides which blocks, parameters, or connections to expose to
the model (catalog search, graph inspection, embed text composition, mutation
candidates) should consult this document and use the same native signals. The
principle: **GRC already knows which params matter; read its evaluation, don't
reimplement it.**

---

# 1. Class Dictionary & Method References

All classes below inherit from GRC's base `Element` class and inherit its core
parent-child navigation and validation interfaces.

## 1.1 Element (`gnuradio.grc.core.base.Element`)

Base class for all canvas elements (`Platform`, `FlowGraph`, `Block`, `Param`,
`Port`, `Connection`).

- **`__init__(self, parent=None)`** — Initializes the element and establishes
  parent-child tracking using weak references to prevent GC leaks.
- **`validate(self)`** — Traverses the tree hierarchy and recursively invokes
  `validate()` on all children returned by `self.children()`.
- **`is_valid(self)`** — Returns `True` if the element has no active validation
  errors or is bypassed/disabled. Replaces manual validation tracking.
- **`add_error_message(self, msg)`** — Appends a validation error string to the
  element's internal `_error_messages` list.
- **`get_error_messages(self)`** — Recursively collects and indents error
  strings from this element and all active children.
- **`iter_error_messages(self)`** — Yields `(element, message)` tuples for this
  node and enabled children.
- **`rewrite(self)`** — Resets local error lists and propagates `rewrite()`
  calls down to children to clear states.
- **`enabled` (Property)** — Returns `True` if the element is active. Defaults
  to `True` (overridden in `Block` and `Connection`).
- **`get_bypassed(self)`** — Checks if the block/element is bypassed. Defaults
  to `False` (overridden in `Block`).
- **`parent` (Property)** — Safely dereferences weak parent pointers.
- **`get_parent_by_type(self, cls)`** — Recursively walks parent chain to resolve
  first parent matching `cls`.
- **`parent_platform` / `parent_flowgraph` / `parent_block`** — Lazy properties
  resolving key GRC ancestor references.
- **`children(self)`** — Defines children collections (overridden in
  sub-classes).
- **Type testing booleans:** `is_flow_graph`, `is_block`, `is_connection`,
  `is_port`, `is_param`, `is_variable`, `is_import`, `is_snippet`.

## 1.2 Platform (`gnuradio.grc.core.platform.Platform`)

The root container; loads libraries, domains, schemas, and handles flowgraph
builds.

- **`build_library(self, path=None)`** — Scans block paths and loads block
  descriptions (`.block.yml`), domain rules (`.domain.yml`), and category trees
  (`.tree.yml`) into the platform catalog.
- **`make_flow_graph(self, from_filename=None)`** — Factory method. Creates a
  new `FlowGraph` and imports raw dictionary mappings if `from_filename` is
  provided.
- **`parse_flow_graph(self, filename)`** — Parses `.grc` files (translating
  legacy XML to YAML) and runs schema checks. Returns a nested `dict`.
- **`save_flow_graph(self, filename, flow_graph)`** — Serializes a `FlowGraph`
  instance back to clean GRC YAML.
- **`load_and_generate_flow_graph(self, file_path, out_dir=None, hier_only=False)`**
  — Programmatically loads, rewrites, validates, and generates Python code for
  a GRC flowgraph. Returns `(FlowGraph, str)`.
- **Factory helpers:** `make_block`, `make_param`, `make_port`.
- **`blocks`** — `ChainMap[str, Block]` of loaded and built-in block classes.

> **Refactor note (the agent's adapter):** the platform is a heavy object.
> Hold one in a lazy singleton. The constructor needs `name`, `prefs=gr.prefs()`,
> `version=gr.version()`, and a 3-tuple `version_parts=(gr.major_version(),
> gr.api_version(), gr.minor_version())`. `build_library()` is the call that
> populates the catalog and is **headless-safe** (no DISPLAY, gtk, qt, or
> gobject references in `platform.py`).

## 1.3 FlowGraph (`gnuradio.grc.core.FlowGraph.FlowGraph`)

Holds flowgraph layouts, connections, variable scopes, and the global
evaluation namespace.

- **`options_block`** — Direct reference to the unique `options` block mapping
  global metadata.
- **`blocks` / `connections`** — `List[Block]` / `Set[Connection]`. Collections
  of active graph components.
- **`namespace`** — `dict`. The evaluated Python context mapping variable and
  param names to live values.
- **`evaluate(self, expr, namespace=None, local_namespace=None)`** — Dynamically
  resolves expressions inside the GRC namespace using Python `eval` with
  evaluation caches.
- **`rewrite(self)`** — Rebuilds namespace scopes topologically and triggers
  rewrites across blocks and ports.
- **Block traversal:** `iter_enabled_blocks`, `get_enabled_blocks`,
  `get_bypassed_blocks`.
- **`connect(self, porta, portb, params=None)`** — Links two port elements in
  the graph structure.
- **`disconnect(self, *ports)`** — Removes connections referencing specified
  ports.
- **`import_data(self, data)`** — Handles graph import mappings. Returns an
  errors flag (`bool`).
- **`export_data(self)`** — Returns an `OrderedDict` suitable for
  serialization.

## 1.4 Block (`gnuradio.grc.core.blocks.block.Block`)

Represents an instance of a block class in a flowgraph.

- **`state` (Property/Setter)** — `'enabled' | 'disabled' | 'bypassed'`.
  Direct control over block operational states.
- **`enabled` (Property)** — `True` if `state != 'disabled'`.
- **`get_bypassed(self)` / `set_bypassed(self)`** — Checks/assigns bypass state.
- **`can_bypass(self)`** — Checks configuration (1 source, 1 sink, matching
  data type) to determine if block is bypass-eligible.
- **`params`** — `OrderedDict[str, Param]`. Direct map of parameters.
- **`sources` / `sinks`** — `List[Port]`. Direct lists of output/input ports.
- **`active_sources` / `active_sinks`** — Active ports (hidden ports filtered
  out during rewrites).
- **`name`** — Unique block ID (e.g., `'samp_rate_0'`).
- **`flags`** — `Flags` instance; direct check for `not_dsp`, `need_qt_gui`,
  `deprecated`, `throttle`.

## 1.5 Param (`gnuradio.grc.core.params.param.Param`)

Defines a block parameter, value expressions, types, and UI visibility
categories.

- **`dtype`** — The parameter data type as a string (`'int'`, `'float'`,
  `'enum'`, `'gui_hint'`, etc.).
- **`hide`** — Visibility state. One of `'none' | 'part' | 'all'`. See §3.1
  for the dynamic evaluation rules.
- **`category`** — Tab grouping string (`'General'`, `'Advanced'`, `'Config'`,
  custom tabs). See §3.2.
- **`value` / `default`** — Raw string value expression and GRC default values.
- **`get_evaluated(self)`** — Returns cached evaluated parameter value.
- **`options` / `get_opt(self, item)`** — Access enum configuration values and
  option attributes.
- **`evaluate(self)`** — Triggers metric calculations, AST parses, and variable
  resolution.

## 1.6 Port (`gnuradio.grc.core.ports.port.Port`)

Represents output (source) and input (sink) nodes.

- **`dtype` / `vlen`** — Native type keys (`'fc32'`, `'f32'`) and vector
  dimensions.
- **`domain`** — `'stream' | 'message'`.
- **`is_sink` / `is_source`** — Node direction.
- **`item_size`** — Combined sizing in bytes
  (`Constants.TYPE_TO_SIZEOF[dtype] * vlen`).
- **`inherit_type`** — `True` if port is a wildcard port.
- **`resolve_empty_type(self)`** — Crawls connection graph paths recursively to
  propagate types to wildcard ports.

## 1.7 Connection (`gnuradio.grc.core.Connection.Connection`)

Represents connection links between ports.

- **`source_port` / `sink_port`** — `Port` instances.
- **`source_block` / `sink_block`** — Parent block instances.
- **`enabled`** — `self.source_block.enabled and self.sink_block.enabled`.
- **`type`** — `Tuple[str, str]` of source and sink domains (e.g.,
  `('stream', 'stream')`).

## 1.8 Supporting Core Classes & Constants

### Core utility classes

- **`MakoTemplates`** (`gnuradio.grc.core.blocks._templates.MakoTemplates`)
  - `render(self, item)` — Renders block template attributes (`'make'`,
    `'imports'`, etc.) using local block namespaces.
  - `compile(cls, text)` — Compiles raw template strings.
- **`Flags`** (`gnuradio.grc.core.blocks._flags.Flags`) — Direct check for block
  properties like `not_dsp`, `need_qt_gui`, `deprecated`, `throttle`.
- **`TemplateArg`** (`gnuradio.grc.core.params.template_arg.TemplateArg`) —
  Wraps block parameters during Mako evaluations. Invoking it returns the
  evaluated value code string.
- **`EvaluatedDescriptors`** (`gnuradio.grc.core.utils.descriptors.evaluated`) —
  Classes: `Evaluated`, `EvaluatedEnum`, `EvaluatedPInt`, `EvaluatedFlag`. Manage
  caching and automatic lazy re-evaluation of elements' properties.

### Key constants (`gnuradio.grc.core.Constants`)

- `DEFAULT_PARAM_TAB = "General"`
- `ADVANCED_PARAM_TAB = "Advanced"`
- `GR_STREAM_DOMAIN = "stream"`
- `GR_MESSAGE_DOMAIN = "message"`
- `PARAM_TYPE_NAMES` — 24 parameter type identifiers (e.g., `'int'`,
  `'float'`, `'gui_hint'`).
- `TYPE_TO_SIZEOF` — maps data type shortcuts (`'fc32'`, `'f32'`) to byte
  lengths.
- `ALIASES_OF` — maps data types to compatible aliases (e.g.,
  `'complex'` → `{'fc32'}`).

---

# 2. The Evaluation & Namespace Pipeline

GRC manages evaluation namespaces to ensure block parameters and variable values
resolve correctly according to Python execution semantics.

## 2.1 Namespace Renewal — `FlowGraph._renew_namespace()`

The method `_renew_namespace()` coordinates the complete rebuilding of the
evaluation context. It clears the existing context to purge deleted or disabled
blocks and reloads elements in a strict order:

```python
def _renew_namespace(self) -> None:
    self.namespace.clear()

    namespace = self._reload_imports({})
    self.imported_names = set(namespace.keys())
    namespace = self._reload_modules(namespace)
    namespace = self._reload_parameters(namespace)

    self.namespace.update(namespace)
    namespace = self._reload_variables(namespace)
    self._eval_cache.clear()
```

The reload pipeline proceeds as follows:

1. **Imports** — `_reload_imports()` runs `exec(expr, namespace)` for all enabled
   import blocks in the canvas.
2. **Modules (EPy)** — `_reload_modules()` inspects enabled Embedded Python
   (`epy_module`) blocks, instantiates a Python `types.ModuleType` object, runs
   their source code inside it via `exec()`, and maps the module ID to the
   namespace.
3. **Parameters** — `_reload_parameters()` evaluates block parameters (which are
   independent of each other) using the parsed import and module namespace.
4. **Variables (Topological Sorting)** — `_reload_variables()` parses and
   updates variable blocks. Since variables can depend on other variables, they
   are sorted topologically via Kahn's algorithm in `expr_utils.sort_objects`.

### Kahn's Topological Sorting (`expr_utils.py`)

- A dependency graph `_graph` is built. For each variable expression,
  `get_variable_dependencies()` extracts references using Python's `ast`
  parsing.
- A directed edge from `dep` to `var` is added when `var` depends on `dep`.
- In `_sort_variables(exprs)`, the graph is traversed to identify leaf nodes
  (`indep_vars` with no outgoing edges, meaning nothing else depends on them).
- Leaf nodes are added to a list and removed from the graph. The cycle repeats
  until the graph is empty.
- Reversing the list puts root variables (which depend on nothing) first
  (`indep → dep`), which are then safely evaluated sequentially. If no leaf
  node is found during an iteration, a circular dependency exception is raised.

> **Refactor note:** the agent's adapter must call `flow_graph.rewrite()` once
> after `import_data()`. If the graph contains a circular variable dependency,
> `rewrite()` raises — catch it and return `ok=False` with a structured error
> code (e.g., `REWRITE_FAILED`).

## 2.2 Local Dictionary Build — `Block.namespace`

Every block compiles its own local execution context under the `namespace`
property:

```python
@property
def namespace(self):
    # update block namespace
    self.block_namespace.update(
        {key: param.get_evaluated() for key, param in self.params.items()}
    )
    return self.block_namespace
```

During `Block.rewrite()`, the block clears its local namespace and evaluates its
local imports:

```python
self.block_namespace.clear()
imports = ""
try:
    imports = self.templates.render('imports')
    exec(imports, self.block_namespace)
except ImportError:
    pass
```

During variable block evaluation in `_reload_variables()`, Python's `eval()` is
executed with `globals` = global flowgraph `namespace` (imports, modules,
parameters, evaluated variables) and `locals` = block's local `namespace` (its
evaluated parameters and block-specific imports). This allows variables to
resolve names across both scopes.

## 2.3 Under the Hood of `Param.evaluate()`

The `evaluate()` method in `param.py` converts raw parameter input strings into
Python type instances:

- **Un-evaluated Types** (`id`, `stream_id`, `name`, `enum`) — Returned as raw
  strings. If the parameter has options with custom attributes, it wraps the
  string inside `attributed_str(expr)` and sets those attributes as fields on
  the string object via `setattr()`.
- **Numeric Scaling Suffixes** — For types `complex`, `real`, `float`, `int`,
  `hex`, and `bool`, if the expression ends with an SI scaling suffix (such as
  `k`, `M`, `u`), it parses the suffix and converts it to a standard scientific
  representation:
  ```python
  self.scale = {'E': 1e18, 'P': 1e15, 'T': 1e12, 'G': 1e9, 'M': 1e6, 'k': 1e3,
                'm': 1e-3, 'u': 1e-6, 'n': 1e-9, 'p': 1e-12, 'f': 1e-15, 'a': 1e-18}
  ```
  It checks if `isinstance(expr, str) and self._is_float(expr[:-1])` is True,
  retrieves the scale factor, multiplies the prefix float by the scale
  multiplier, and passes the computed float string to `FlowGraph.evaluate()`.
- **Numeric Vector Types** — Vector types evaluate their expressions. If the
  returned value is not an instance of `Constants.VECTOR_TYPES` (list, tuple,
  numpy array, set), it sets `_lisitify_flag = True` and wraps it in a list. In
  `to_code()`, if `_lisitify_flag` is True, it serializes the value enclosed in
  square brackets `[...]`.
- **String Types** — String parameters are evaluated using
  `parent_flowgraph.evaluate()`. If evaluation fails or does not yield a string,
  it falls back to a raw string representation and sets `_stringify_flag = True`,
  causing `to_code()` to serialize it using `repr(value)`.
- **Import Types** — Runs `exec(expr, n)` inside a fresh dictionary `n` and
  returns the keys of all imported modules and symbols, omitting `__builtins__`.

## 2.4 Cache Invalidation and Lazy Re-evaluation

GRC uses descriptors in `evaluated.py` to implement cached property evaluations:

- **`Evaluated`** — Base descriptor class. It checks if the evaluated value is
  cached in `instance.__dict__[self.name]`. If not (cache miss), it triggers
  `self.eval_function(instance)` and stores the result.
- **`__set__`** — If a value is set and starts with `"${"` and ends with `"}"`,
  it extracts the expression, sets it in `instance.__dict__[self.name_raw]`, and
  pops `self.name` to invalidate the cache. If it is a literal, it converts it
  to the default type and caches it directly.
- **`__delete__`** — Removes `self.name` from `instance.__dict__` to invalidate
  the cache.

During parameter rewrite inside `Param.rewrite()`, calling `del self.name`,
`del self.dtype`, and `del self.hide` triggers `__delete__` on their respective
descriptors. This clears the cache and forces lazy re-evaluation when the
attributes are accessed next.

---

# 3. Parameter Filtering, Tabs & Visibilities

Headless agents must efficiently filter out cosmetic or GUI-specific parameters
to reduce context size. The native signals are `param.hide` and
`param.category` — no per-block allowlists, no per-scenario branches.

## 3.1 Dynamic `hide` Attribute Evaluation

`hide` is defined as a descriptor:

```python
hide = EvaluatedEnum('none all part')
```

If a parameter's hide condition is a dynamic Mako template (e.g.,
`"${ 'none' if type == 'float' else 'all' }"`), accessing `param.hide`
evaluates this expression in the parent block's namespace. In `Param.rewrite()`,
`del self.hide` is called to clear the cache and force re-evaluation. If
`hide == 'all'`, the parameter is hidden.

| Value | Meaning | GRC GUI behavior |
|-------|---------|------------------|
| `'none'` | Always visible | Full form in properties dialog |
| `'part'` | Conditionally visible | Reduced form (e.g., collapsed row) |
| `'all'` | Hidden | Not shown |

The `hide` field in block YAML can be:
- A literal string (`hide: 'all'`)
- A Mako expression evaluated at runtime (`hide: "${ ('none' if nconnections > 1 else 'all') }"`)

**Filtering rule:** `hide != 'all'` to keep visible params.

**Used by:** the unified filter `grc_agent.runtime.param_filter` (which
consumes evaluated `hide`), the catalog embed text builder, and `inspect_graph`.

## 3.2 Tabs and Category Boundaries

Block parameters are assigned tab groupings (categories).

- **`General`** — Core functional variables (e.g., sample rates, gains,
  frequencies). Default tab when no `<tab>` is set.
- **`Advanced`** (`Constants.ADVANCED_PARAM_TAB`) — Automatically appended by
  `build_params()` in `_build.py` to all blocks. Holds generic metadata
  parameters like `alias`, `affinity`, `minoutbuf`, `maxoutbuf`, and `comment`.
- **`Config`** — Styling parameters (colors, alphas, grid styles, line widths)
  used only by QT GUI blocks.

### Tab Census (measured across 564 catalog blocks)

| Category | Unique params | Blocks | Content type |
|----------|--------------|--------|--------------|
| **General** | 3482 | 563 | Functional (type, samp_rate, freq, gain, etc.) |
| **Advanced** | 2485 | 563 | GRC auto-added (alias, affinity, comment, minoutbuf, maxoutbuf) |
| **RF Options** | 646 | 17 | SDR RF tuning (center_freq, gain, antenna, bandwidth) |
| **Config** | 498 | 10 | **100% styling** (color, alpha, marker, style, width, label) |
| **FE Corrections** | 128 | 2 | Frontend IQ/DC corrections |
| **Trigger** | 21 | 4 | Scope trigger configuration |
| Filter / Fields / TX1 / TX2 / Optional | ~73 | various | Block-specific functional |

### Key findings

1. **`ADVANCED_PARAM_TAB` ("Advanced")** is always GRC auto-added metadata.
   Defined in `gnuradio.grc.core.blocks._build` lines 116-146: alias, affinity,
   minoutbuf, maxoutbuf, comment. Same for every block. Never block-specific.
2. **`"Config"`** is 100% styling. Verified: searched all 84 unique param names
   in Config for functional keywords (freq, gain, rate, type, samp, center,
   antenna, bw, bandwidth). **Zero matches.** Every Config param is a color,
   alpha, marker, style, width, label, or display toggle. Exists only in 10
   qtgui GUI sink/source blocks.
3. **`"RF Options"`** is functional. Contains `center_freq0`, `gain0`, `ant0`,
   `bw0`, `dc_offs_enb0` — real SDR tuning params. Dropping this category would
   hide the most important USRP/pluto params.

**Filtering rule:** exclude `ADVANCED_PARAM_TAB` and `"Config"`. Include all
other categories (General, RF Options, FE Corrections, Trigger, etc.).

## 3.3 Prominence Ordering and Value-based Filtering

GRC determines the visual prominence of parameters using these rules:

- **Prominence ordering** — Sort by evaluated `hide` status: `'none'` (0,
  always prominent) first, then `'part'` (1, conditionally prominent), and
  finally `'all'` (2, hidden).
- **Value-based filter** — implemented in
  `grc_agent.runtime.param_filter.keep_param` (the single authority). In
  Overview/prominence mode a parameter is retained if `dtype == 'enum'`
  (a structural selector such as `type`), OR its value differs from its
  default, OR its value references a flowgraph variable. `hide == 'none'`
  alone no longer retains a default-valued param. Details mode uses
  visibility-only (dense: defaults shown).

The value-based filter is the tightest — used by `inspect_graph` Overview
when no specific params are requested. It shows structural selectors plus
params the user has actually changed. Not suitable for catalog search (no
instance values), but useful for graph inspection where the goal is "what's
configured" rather than "what exists".

## 3.4 Block Role Classification

GRC provides native boolean properties to classify blocks:

- `Block.is_variable` — `True` if the block defines a flowgraph variable.
- `Block.is_import` — `True` if `block.key == 'import'`.
- `Block.is_snippet` — `True` if `block.key == 'snippet'`.
- `Block.is_virtual_or_pad` — `True` if
  `block.key in ("virtual_source", "virtual_sink", "pad_source", "pad_sink")`.

## 3.5 Parameter Filtering (the agent's working code)

The **single authority** is `grc_agent.runtime.param_filter.keep_param`.
Every model-visible parameter payload — `inspect_graph` overview/details,
catalog `describe_block`, and the catalog embed text — delegates its
keep/drop decision to it. The pipeline: drop `hide == 'all'` → drop
`category in {Advanced, Config}` → drop `dtype == 'gui_hint'` →
(prominence only) keep `dtype == 'enum'` OR `value != default` OR
value-references-a-variable. Do **not** re-implement this recipe inline;
the duplicated inline copies were the source of the drift that forced the
`param_filter` consolidation. See `src/grc_agent/runtime/param_filter.py`.

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

# 4. Wildcard & Port Type Resolution

Wildcard ports (`inherit_type = True`) resolve their data types (`dtype`) and
vector lengths (`vlen`) dynamically during compilation.

## 4.1 Wildcard Detection

A port is a wildcard if its `dtype` is empty (`not self.dtype`).

```python
@property
def inherit_type(self):
    return not self.dtype
```

## 4.2 Recursive Type Propagation

During `Port.rewrite()`, if `self.inherit_type` is True, GRC calls
`self.resolve_empty_type()`. This method invokes the crawl functions in
`_virtual_connections.py` to trace linked paths:

```python
def resolve_empty_type(self):
    def find_port(finder):
        try:
            return next((p for p in finder(self) if not p.inherit_type), None)
        except _virtual_connections.LoopError as error:
            self.add_error_message(str(error))
        except (StopIteration, Exception):
            pass

    try:
        port = find_port(_virtual_connections.upstream_ports) or \
            find_port(_virtual_connections.downstream_ports)
        self.set_evaluated('dtype', port.dtype)
        self.set_evaluated('vlen', port.vlen)
        self.domain = port.domain
    except AttributeError:
        self.domain = Constants.DEFAULT_DOMAIN
```

- **Upstream Crawling (`upstream_ports`)**:
  - If the port is a sink, it retrieves the connected source ports using
    connection mappings.
  - If the port is a source and its block is a `VirtualSource`, it resolves the
    corresponding `VirtualSink` by matching their `stream_id` parameter, then
    crawls upstream from the sink port.
- **Loop Prevention**: Both crawls track visited ports inside a `_traversed` set.
  If an already visited port is encountered, a `LoopError` is raised, bubbling
  up a validation error.

> **Refactor note:** the agent's adapter must call `flow_graph.rewrite()` once
> after `import_data()`. This populates wildcard `dtype` and `vlen` on every
> port, so reading `port.dtype` post-rewrite is safe.

---

# 5. Recursive Validation & Error Bubbling

Validation runs recursively down the flowgraph tree using the APIs defined in
`base.py`.

## 5.1 Rewrite vs. Validate Phase

1. **Rewrite Phase (`rewrite()`)**:
   - Clears existing error lists: `del self._error_messages[:]`.
   - Recursively executes `child.rewrite()` on all child elements
     (`Block.children()` returns parameters and ports; `FlowGraph.children()`
     returns blocks and connections).
   - Updates namespaces, evaluates dynamic parameter values, and propagates
     wildcard types.
2. **Validate Phase (`validate()`)**:
   - Recursively executes `child.validate()` on all child elements.
   - Performs specific validations (e.g., block assertions, type validators,
     connections) and calls `add_error_message(msg)` on failure.

> **Refactor note:** the agent's adapter uses `rewrite()` + `validate()` +
> `is_valid()` + `iter_error_messages()`. There is no need to spawn the `grcc`
> subprocess — GRC's own validator is sufficient for the agent's needs. If a
> regression surfaces in eval-harness runs, expose `flow_graph.generate()` as a
> future option.

## 5.2 Built-in Type Validators (`dtypes.py`)

The validators dictionary in `dtypes.py` maps parameter types to specialized
validation routines:

- **`id`** — Ensures value is a valid Python identifier, unique across all active
  block IDs, and not in the `ID_BLACKLIST` (builtins, Python keywords, and
  `gr.top_block` attributes).
- **`stream_id`** — Assures unique stream IDs across virtual sinks, and verifies
  that virtual sources map to an existing virtual sink.
- **`complex`, `real`, `float`, `int`** — Verifies that the evaluated value
  matches the expected type in `Constants.PARAM_TYPE_MAP`.
- **Vectors** — Verifies that the evaluated value is an iterable and all
  elements are instances of the base scalar type.
- **`gui_hint`** — Validates Qt GUI layouts, grid definitions, and checks for
  cell collisions (no overlapping components in the grid).

## 5.3 Error Bubbling & Suppression

- **Error Collection**: `iter_error_messages()` recursively crawls enabled
  children, yielding tuples of `(element, message)`.
- **Child Error Suppression**: If a block or port is disabled or bypassed, GRC
  explicitly skips validation error propagation:
  ```python
  if not child.enabled or child.get_bypassed():
      continue
  ```
- **Error Formatting**: `get_error_messages()` aggregates child errors and
  indents them appropriately to maintain context:
  ```python
  "{}:\n\t{}".format(elem, msg.replace("\n", "\n\t"))
  ```

---

# 6. LLM Headless Orchestration Blueprint

This complete Python script implements programmatic loading, parameter
modification, validation, error-checking, and rendering/serialization using GRC
core APIs. **The agent's `grc_native_adapter` is a more constrained version of
this pattern** — it skips code generation, restricts mutations to a fixed
op-type set, and emits Pydantic models instead of YAML.

```python
#!/usr/bin/env python3
import os
import sys
from gnuradio import gr
from gnuradio.grc.core.platform import Platform
from gnuradio.grc.core.io import yaml
from gnuradio.grc.core.Constants import ADVANCED_PARAM_TAB

def orchestrate_grc(grc_file_path, output_grc_path):
    print(f"Initializing GNU Radio Companion Platform...")

    # 1. Initialize Platform with GNU Radio runtime properties
    platform = Platform(
        name='GNU Radio Companion Core Platform',
        prefs=gr.prefs(),
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version())
    )
    platform.build_library()

    # 2. Instantiate and load a FlowGraph
    flow_graph = platform.make_flow_graph()
    flow_graph.grc_file_path = os.path.abspath(grc_file_path)

    if not os.path.exists(grc_file_path):
        raise FileNotFoundError(f"GRC file not found: {grc_file_path}")

    print(f"Loading GRC file: {grc_file_path}")
    flow_graph.import_data(platform.parse_flow_graph(grc_file_path))

    # 3. List and Inspect Functional Parameters
    print("\n--- Current Functional Variables & Blocks ---")
    for block in flow_graph.blocks:
        role = "DSP Block"
        if block.is_variable:
            role = "Variable"
        elif block.is_import:
            role = "Import"
        elif block.is_virtual_or_pad:
            role = "Virtual/Pad"

        print(f"\n[{role}] {block.name} (Key: {block.key})")

        # Filter and display parameters (Exclude Advanced and Config tabs)
        for param_id, param in block.params.items():
            if param.category in (ADVANCED_PARAM_TAB, "Config"):
                continue  # Skip styling and low-level metadata
            print(f"  - {param_id}: Raw='{param.value}' | Evaluated={param.get_evaluated()}")

    # 4. Programmatic Modifications
    print("\nApplying updates...")

    # Modify option block (ID: options)
    options_block = flow_graph.options_block
    options_block.params['title'].set_value("Headless Orchestration Flowgraph")

    # Modify variables / DSP params
    for block in flow_graph.blocks:
        if block.is_variable and block.name == 'samp_rate':
            block.params['value'].set_value("32000")
            print(f"Updated variable 'samp_rate' to '32000'")

        if block.key == 'analog_sig_source_x':
            block.params['frequency'].set_value("1000")
            print(f"Updated analog_sig_source_x frequency to '1000'")

    # 5. Connect new blocks programmatically if needed
    # Example:
    # throttle = flow_graph.new_block("blocks_throttle2")
    # flow_graph.connect(sig_source.sources[0], throttle.sinks[0])

    # 6. Execute Rewrite to update namespace and resolve wildcard types
    print("\nRewriting flowgraph (Renewing namespaces and propagating wildcard types)...")
    flow_graph.rewrite()

    # 7. Validate Flowgraph and inspect results
    print("Validating flowgraph...")
    flow_graph.validate()

    if not flow_graph.is_valid():
        print("\n[VALIDATION FAILED] Errors found:")
        for elem, msg in flow_graph.iter_error_messages():
            print(f"Error in [{elem}]: {msg}")
        return False
    else:
        print("\n[VALIDATION PASSED] Flowgraph is completely valid!")

    # 8. Export and save the modified flowgraph using GRC dumper settings
    print(f"Saving serialized flowgraph to {output_grc_path}...")
    serialized_data = flow_graph.export_data()

    with open(output_grc_path, 'w', encoding='utf-8') as f:
        yaml.dump(serialized_data, f)

    print("Success!")
    return True

if __name__ == '__main__':
    # Usage: ./orchestrate.py input.grc output.grc
    if len(sys.argv) < 3:
        print("Usage: python orchestrate.py <input.grc> <output.grc>")
    else:
        orchestrate_grc(sys.argv[1], sys.argv[2])
```

> **Critical import-order gotcha:** the import
> `from gnuradio.grc.core.io import yaml` triggers a circular import if loaded
> **before** `from gnuradio.grc.core.platform import Platform`. The platform
> import warms the `params` module, which unblocks `io/yaml.py`. Always import
> the platform first.

> **Another critical gotcha:** `gnuradio.version()` does **not exist** — only
> `gr.version()`. `from gnuradio.grc.core import Platform` and
> `from gnuradio.grc.core import FlowGraph` **also do not exist** — use
> `from gnuradio.grc.core.platform import Platform` and
> `from gnuradio.grc.core.FlowGraph import FlowGraph` (submodule paths). Only
> `from gnuradio.grc.core import Constants` works at the top level.

---

# Changelog

- 2026-06-21: Merged `GRC_Core_API_Surface3.md` into this file. Single source
  of truth. Renumbered sections (was: Methods 1-5 in `GNU_NATIVE_METHODS.md`;
  is now: §1-6 in this consolidated doc). Old section §3 (param filtering) and
  old Method 1-5 are now unified in §3. Old `GRC_Core_API_Surface3.md`
  content is preserved as §1, §2, §4, §5, §6.
- 2026-06-19: Initial documentation. Methods 1-5 cataloged from GRC platform
  source code and verified against 564-block catalog.
