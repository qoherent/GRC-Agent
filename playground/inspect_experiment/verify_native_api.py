import os
import yaml
import json
from pathlib import Path
from grc_agent.session import _ensure_platform
from gnuradio.grc.core.Constants import ADVANCED_PARAM_TAB



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

def load_and_inspect_native(grc_file_path: Path) -> dict:
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
    
    # Extract options block metadata
    options_params = {}
    if fg.options_block:
        for k, p in fg.options_block.params.items():
            if k in {"generate_options", "output_language"}:
                options_params[k] = str(p.value)
                
    # Extract enabled blocks
    blocks_dict = {}
    enabled_block_names = set()
    for block in fg.blocks:
        # Ghost block check: skip if disabled or bypassed
        if not block.enabled or block.get_bypassed():
            continue
            
        enabled_block_names.add(block.name)
        
        # Parameter filtering: drop if hide='all', or Advanced/Config tab, or blacklisted GUI keys
        parameters = {}
        for k, p in block.params.items():
            if p.hide == "all":
                continue
            if p.category in {ADVANCED_PARAM_TAB, "Config"}:
                continue

                
            parameters[k] = str(p.value)
            
        role = resolve_block_role(block)
        blocks_dict[block.name] = {
            "block_type": block.key,
            "role": role,
            "parameters": parameters
        }
        
    # Extract enabled connections
    connections_list = []
    for conn in fg.connections:
        # Verify connection and its endpoints are enabled
        if not conn.enabled:
            continue
        src_name = conn.source_block.name
        dst_name = conn.sink_block.name
        
        # Only include if both endpoints are enabled blocks
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
    workspace_root = Path(__file__).resolve().parents[2]
    data_dir = workspace_root / "tests" / "data"
    results_dir = workspace_root / "playground" / "inspect_experiment" / "results_native"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    grc_files = sorted(data_dir.glob("*.grc"))
    print(f"Found {len(grc_files)} GRC files.")
    
    for grc_file in grc_files:
        print(f"Inspecting natively: {grc_file.name}")
        inspected = load_and_inspect_native(grc_file)
        
        # Read the original GRC file contents
        with open(grc_file, "r", encoding="utf-8") as f:
            grc_content = f.read()
            
        # Pretty print result
        prettified = json.dumps(inspected, indent=2)
        
        # Save to JSON file
        json_path = results_dir / f"{grc_file.stem}_native_inspected.json"
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(prettified)
            
        # Format the markdown content
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
        # If the graph was invalid, append native validation errors
        if inspected["validation"]["status"] == "invalid":
            errors_str = "\n".join(inspected["validation"]["errors"])
            md_content += f"""
## Detailed Compiler Diagnostics
**Errors:**
```text
{errors_str}
```
"""

        # Save to markdown file
        md_path = results_dir / f"{grc_file.stem}_native_inspected.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
            
        print(f"  Saved JSON to {json_path.relative_to(workspace_root)}")
        print(f"  Saved MD to {md_path.relative_to(workspace_root)}")

if __name__ == "__main__":
    run_verification()
