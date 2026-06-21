import os
import re
import yaml
import json
from pathlib import Path
from grc_agent.session import _ensure_platform
from gnuradio.grc.core.Constants import ADVANCED_PARAM_TAB

# Native: a param value references a flowgraph variable when a whole Python
# identifier token in its expression equals a variable block's name.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")


def _references_variable(value: str, variable_names: set) -> bool:
    if not variable_names:
        return False
    return any(tok in variable_names for tok in _IDENTIFIER_RE.findall(value))

def resolve_block_role(block) -> str:
    # 1. Native booleans
    if getattr(block, "is_variable", False):
        return "variable_or_control"
    if getattr(block, "is_import", False):
        return "import"
    if getattr(block, "is_snippet", False):
        return "snippet"
    if getattr(block, "is_virtual_or_pad", False):
        return "virtual_or_pad"
        
    # Options block is metadata
    if block.key == "options":
        return "metadata"
        
    # Fallback based on ports
    has_inputs = len(block.sinks) > 0
    has_outputs = len(block.sources) > 0
    
    if has_outputs and not has_inputs:
        return "source"
    if has_inputs and not has_outputs:
        return "sink"
    if has_inputs and has_outputs:
        return "transform"
    return "metadata"

def load_and_inspect_native(grc_file_path: Path, *, targets: list[str] | None = None, params: list[str] | None = None) -> dict:
    platform = _ensure_platform()
    if platform is None:
        raise RuntimeError("GNU Radio GRC Platform is not available.")

    fg = platform.make_flow_graph()
    with open(grc_file_path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    # Import and topologically evaluate the graph
    fg.import_data(raw_data)
    fg.rewrite()

    # Run validation in memory
    fg.validate()
    is_valid = fg.is_valid()
    error_messages = list(fg.get_error_messages())

    # Native: variable/control block names form the flowgraph value namespace.
    variable_names = {b.name for b in fg.blocks if getattr(b, "is_variable", False)}

    blocks_dict = {}
    enabled_block_names = set()
    options_params = {}

    for block in fg.blocks:
        # Native: Block.enabled / Block.get_bypassed() — ghost/disabled exclusion.
        if not block.enabled or block.get_bypassed():
            continue
        enabled_block_names.add(block.name)

        role = resolve_block_role(block)
        is_targeted = targets is not None and block.name in targets

        parameters = {}
        for k, p in block.params.items():
            # --- Visibility filters (every mode) — native GRC properties ---
            # Native: Param.hide == "all" — dynamically hidden.
            if p.hide == "all":
                continue
            # Native: category == ADVANCED_PARAM_TAB — GRC-appended low-level metadata.
            if p.category == ADVANCED_PARAM_TAB:
                continue
            # Convention string: "Config" tab holds QT-GUI cosmetic styling only.
            if p.category == "Config":
                continue
            # Native: Param.dtype == "gui_hint" — pure Qt layout positioning.
            if p.dtype == "gui_hint":
                continue

            # --- Prominence filters (Overview mode: non-targeted blocks) ---
            if targets is not None and not is_targeted:
                val_str = str(p.value)
                is_prominent = (
                    p.is_enum()                                      # Native: structural selector (type, wintype, ...)
                    or val_str != str(p.default)                     # Native: user-changed value
                    or _references_variable(val_str, variable_names)  # Native namespace reference
                )
                if not is_prominent:
                    continue

            # --- Explicit param-key filter (Filtered mode) ---
            if params is not None and k not in params:
                continue

            parameters[k] = str(p.value)

        blocks_dict[block.name] = {
            "block_type": block.key,
            "role": role,
            "parameters": parameters,
        }

        # The options block doubles as the top-level flowgraph metadata summary;
        # it now flows through the same uniform rule set (no hand-picked whitelist).
        if block.key == "options":
            options_params = parameters

    # Extract enabled connections
    connections_list = []
    for conn in fg.connections:
        # Native: Connection.enabled + endpoint-enabled cross-check.
        if not conn.enabled:
            continue
        src_name = conn.source_block.name
        dst_name = conn.sink_block.name

        if src_name in enabled_block_names and dst_name in enabled_block_names:
            src_port = conn.source_port.key
            dst_port = conn.sink_port.key
            connections_list.append(f"{src_name}:{src_port}->{dst_name}:{dst_port}")

    # Sort connections for stable output
    connections_list.sort()

    return {
        "tool": "inspect_graph",
        "ok": True,
        "options": options_params,
        "blocks": blocks_dict,
        "connections": connections_list,
        "validation": {
            "status": "valid" if is_valid else "invalid",
            "errors": error_messages
        }
    }

def run_verification():
    workspace_root = Path("/home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent")
    data_dir = workspace_root / "tests" / "data"
    results_dir = workspace_root / "playground" / "inspect_experiment" / "results_native"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    grc_files = sorted(data_dir.glob("*.grc"))
    print(f"Found {len(grc_files)} GRC files.")
    
    # 1. Run standard native verification on all GRC files simulating Overview Mode (targets=[])
    for grc_file in grc_files:
        print(f"Inspecting natively: {grc_file.name}")
        inspected = load_and_inspect_native(grc_file, targets=[])
        
        with open(grc_file, "r", encoding="utf-8") as f:
            grc_content = f.read()
            
        prettified = json.dumps(inspected, indent=2)
        
        json_path = results_dir / f"{grc_file.stem}_native_inspected.json"
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(prettified)
            
        md_content = f"""# Inspection Report: {grc_file.name}

## Original GRC File Contents

```yaml
{grc_content}
```

## Inspection Output (With Validation Run)

```json
{prettified}
```
"""
        if inspected["validation"]["status"] == "invalid":
            errors_str = "\n".join(inspected["validation"]["errors"])
            md_content += f"""
## Detailed Compiler Diagnostics
**Errors:**
```text
{errors_str}
```
"""

        md_path = results_dir / f"{grc_file.stem}_native_inspected.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
            
        print(f"  Saved JSON to {json_path.relative_to(workspace_root)}")
        print(f"  Saved MD to {md_path.relative_to(workspace_root)}")

    # 2. Run the 3 specific targets/params simulations on random_bit_generator.grc
    rbg_file = data_dir / "random_bit_generator.grc"
    if rbg_file.exists():
        # a. Overview Mode (targets=[])
        overview = load_and_inspect_native(rbg_file, targets=[])
        with open(results_dir / "random_bit_generator_overview.json", "w", encoding="utf-8") as f:
            json.dump(overview, f, indent=2)
        print("Saved random_bit_generator_overview.json")

        # b. Details Mode (targets=["qtgui_time_sink_x_0"])
        details = load_and_inspect_native(rbg_file, targets=["qtgui_time_sink_x_0"])
        with open(results_dir / "random_bit_generator_details.json", "w", encoding="utf-8") as f:
            json.dump(details, f, indent=2)
        print("Saved random_bit_generator_details.json")

        # c. Filtered Mode (targets=["qtgui_time_sink_x_0"], params=["type"])
        filtered = load_and_inspect_native(rbg_file, targets=["qtgui_time_sink_x_0"], params=["type"])
        with open(results_dir / "random_bit_generator_filtered.json", "w", encoding="utf-8") as f:
            json.dump(filtered, f, indent=2)
        print("Saved random_bit_generator_filtered.json")

if __name__ == "__main__":
    run_verification()
