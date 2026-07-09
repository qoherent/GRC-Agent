import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
import sqlite_vec
from openai import OpenAI

_PLATFORM: Any = None

def get_platform() -> Any:
    global _PLATFORM
    if _PLATFORM is not None:
        return _PLATFORM
    from gnuradio import gr
    from gnuradio.grc.core.platform import Platform
    _PLATFORM = Platform(
        name="grc_agent",
        prefs=gr.prefs(),
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
    )
    _PLATFORM.build_library()
    return _PLATFORM

def load_flow_graph(file_path: str) -> Any:
    platform = get_platform()
    flow_graph = platform.make_flow_graph()
    flow_graph.grc_file_path = str(Path(file_path).resolve())
    parsed = platform.parse_flow_graph(str(file_path))
    flow_graph.import_data(parsed)
    flow_graph.rewrite()
    return flow_graph

def parse_conn(conn_str: str):
    if "->" not in conn_str:
        return None
    src, dst = conn_str.split("->")
    if ":" not in src or ":" not in dst:
        return None
    src_block, src_port = src.split(":")
    dst_block, dst_port = dst.split(":")
    return {
        "src_block": src_block.strip(),
        "src_port": src_port.strip(),
        "dst_block": dst_block.strip(),
        "dst_port": dst_port.strip(),
    }

def resolve_auto(flow_graph: Any, block_name: str, param_key: str, add_connections: list[str]) -> str | None:
    for conn_str in add_connections:
        p = parse_conn(conn_str)
        if not p:
            continue
        if p["src_block"] == block_name:
            neighbor_name = p["dst_block"]
            neighbor_port = p["dst_port"]
            kind = "sink"
        elif p["dst_block"] == block_name:
            neighbor_name = p["src_block"]
            neighbor_port = p["src_port"]
            kind = "source"
        else:
            continue
            
        try:
            neigh_block = flow_graph.get_block(neighbor_name)
        except KeyError:
            continue
            
        ports = neigh_block.active_sinks if kind == "sink" else neigh_block.active_sources
        for prt in ports:
            if str(prt.key) == str(neighbor_port):
                dtype = getattr(prt, "dtype", None)
                if dtype:
                    return str(dtype)
    return "float"

def set_block_state(block: Any, state: str) -> None:
    aliases = {"bypass": "bypassed"}
    canonical = aliases.get(state, state)
    if canonical not in block.STATE_LABELS:
        raise ValueError(f"Invalid state {state!r}; must be one of {block.STATE_LABELS}")
    block.state = canonical

def keep_param(param_key: str, param: Any, block: Any) -> bool:
    hide = getattr(param, "hide", "none") or "none"
    category = getattr(param, "category", "") or ""
    dtype = getattr(param, "dtype", "") or ""
    
    if dtype == "id" or param_key == "showports" or param_key.startswith("bus_structure_"):
        return False
    if hide == "all":
        return False
    if category in ("Advanced", "Config"):
        return False
    if dtype == "gui_hint":
        return False
    return True

def classify_role(b: Any) -> str:
    is_variable = bool(getattr(b, "is_variable", False))
    is_import = bool(getattr(b, "is_import", False))
    is_snippet = bool(getattr(b, "is_snippet", False))
    is_virtual_or_pad = bool(getattr(b, "is_virtual_or_pad", False))
    has_sources = len(getattr(b, "active_sources", ()) or ()) > 0
    has_sinks = len(getattr(b, "active_sinks", ()) or ()) > 0
    
    if is_variable:
        return "variable"
    if is_import:
        return "import"
    if is_snippet:
        return "snippet"
    if is_virtual_or_pad:
        return "virtual_or_pad"
    if getattr(b, "key", "") == "options":
        return "options"
    if has_sources and not has_sinks:
        return "source"
    if has_sinks and not has_sources:
        return "sink"
    if has_sources and has_sinks:
        return "transform"
    return "other"

def inspect_graph(flow_graph: Any, targets: list[str] = None) -> dict[str, Any]:
    blocks_all = []
    connections_all = []
    
    for c in flow_graph.connections:
        conn_str = f"{c.source_block.name}:{c.source_port.key}->{c.sink_block.name}:{c.sink_port.key}"
        connections_all.append(conn_str)
        
    for b in flow_graph.blocks:
        params = {}
        for k, p in b.params.items():
            if keep_param(k, p, b):
                params[k] = str(p.value)
        
        inputs = []
        for p in b.active_sinks:
            inputs.append({
                "port_id": str(p.key),
                "dtype": str(getattr(p, "dtype", "")),
                "domain": str(getattr(p, "domain", "") or "stream")
            })
        outputs = []
        for p in b.active_sources:
            outputs.append({
                "port_id": str(p.key),
                "dtype": str(getattr(p, "dtype", "")),
                "domain": str(getattr(p, "domain", "") or "stream")
            })
            
        role = classify_role(b)
        state = str(getattr(b, "state", "enabled"))
        if state == "bypassed":
            state = "bypass"
            
        blocks_all.append({
            "instance_name": b.name,
            "block_id": b.key,
            "role": role,
            "state": state,
            "params": params,
            "inputs": inputs,
            "outputs": outputs
        })
        
    valid = bool(flow_graph.is_valid())
    errors = []
    if not valid:
        for elem, msg in flow_graph.iter_error_messages():
            errors.append(f"{elem}: {msg}")
            
    whole_graph = not targets or any(t in ("all", "*") for t in targets)
    if not whole_graph:
        requested = set(targets)
        existing_names = {b["instance_name"] for b in blocks_all}
        missing = [t for t in targets if t not in existing_names]
        if missing:
            return {
                "ok": False,
                "errors": [
                    {
                        "code": "block_not_found",
                        "message": f"Unknown block name(s): {', '.join(missing)}",
                        "valid_blocks": [{"instance_name": b["instance_name"], "block_id": b["block_id"]} for b in blocks_all]
                    }
                ]
            }
        blocks = [b for b in blocks_all if b["instance_name"] in requested]
        connections = []
        for c in connections_all:
            p = parse_conn(c)
            if p and (p["src_block"] in requested or p["dst_block"] in requested):
                connections.append(c)
    else:
        blocks = blocks_all
        connections = connections_all
        
    opt_block = getattr(flow_graph, "options_block", None)
    graph_name = opt_block.name if opt_block is not None else ""
    
    return {
        "ok": True,
        "graph": {
            "graph_name": graph_name,
            "blocks": blocks,
            "connections": connections,
            "validation": {
                "status": "valid" if valid else "invalid",
                "errors": errors
            }
        }
    }

def change_graph(flow_graph: Any, 
                 add_blocks: list[dict] = None, 
                 remove_blocks: list[str] = None, 
                 update_params: list[dict] = None, 
                 update_states: list[dict] = None, 
                 add_connections: list[str] = None, 
                 remove_connections: list[str] = None,
                 force: bool = False) -> dict[str, Any]:
    
    initial_data = flow_graph.export_data()
    
    try:
        # Phase 1: add_blocks
        if add_blocks:
            for item in add_blocks:
                block_id = item["block_id"]
                instance_name = item["instance_name"]
                block = flow_graph.new_block(block_id)
                if block is None:
                    raise KeyError(f"Block type {block_id!r} not found in catalog")
                block.params["id"].set_value(str(instance_name))
                flow_graph.rewrite()
                
                for k, v in (item.get("params") or {}).items():
                    if v == "auto":
                        block.params[k].set_value("auto") # Resolved in Phase 4
                        continue
                    if k not in block.params:
                        raise KeyError(f"Param {k!r} not in block {block.name!r}")
                    block.params[k].set_value(str(v))
                if "state" in item:
                    set_block_state(block, item["state"])
                    
        # Phase 2: remove_blocks
        if remove_blocks:
            for name in remove_blocks:
                block = flow_graph.get_block(name)
                flow_graph.remove_element(block)
                
        # Phase 3: update_params
        if update_params:
            for item in update_params:
                block = flow_graph.get_block(item["instance_name"])
                for k, v in (item.get("params") or {}).items():
                    if v == "auto":
                        block.params[k].set_value("auto")
                        continue
                    if k == "id":
                        continue
                    if k not in block.params:
                        raise KeyError(f"Param {k!r} not in block {block.name!r}")
                    block.params[k].set_value(str(v))
                    
        # Phase 4: auto_resolve_types
        for b in flow_graph.blocks:
            for k, p in b.params.items():
                if str(p.value) == "auto":
                    resolved = resolve_auto(flow_graph, b.name, k, add_connections or [])
                    if resolved:
                        p.set_value(resolved)
                        
        # Phase 5: update_states
        if update_states:
            for item in update_states:
                block = flow_graph.get_block(item["instance_name"])
                set_block_state(block, item["state"])
                
        # Phase 6: remove_connections
        if remove_connections:
            for conn_str in remove_connections:
                p = parse_conn(conn_str)
                if p:
                    for connection in list(flow_graph.connections):
                        if (connection.source_block.name == p["src_block"] and
                            str(connection.source_port.key) == str(p["src_port"]) and
                            connection.sink_block.name == p["dst_block"] and
                            str(connection.sink_port.key) == str(p["dst_port"])):
                            flow_graph.remove_element(connection)
                            
        # Phase 7: add_connections
        if add_connections:
            for conn_str in add_connections:
                p = parse_conn(conn_str)
                if p:
                    src_block = flow_graph.get_block(p["src_block"])
                    dst_block = flow_graph.get_block(p["dst_block"])
                    
                    src_port = None
                    for prt in src_block.active_sources:
                        if str(prt.key) == str(p["src_port"]):
                            src_port = prt
                            break
                    if not src_port:
                        raise KeyError(f"source port {p['src_port']!r} not on block {p['src_block']!r}")
                        
                    dst_port = None
                    for prt in dst_block.active_sinks:
                        if str(prt.key) == str(p["dst_port"]):
                            dst_port = prt
                            break
                    if not dst_port:
                        raise KeyError(f"sink port {p['dst_port']!r} not on block {p['dst_block']!r}")
                        
                    flow_graph.connect(src_port, dst_port)
                    
        flow_graph.rewrite()
        
    except Exception as exc:
        flow_graph.import_data(initial_data)
        flow_graph.rewrite()
        return {"ok": False, "errors": [{"code": "mutation_failed", "message": str(exc)}]}
        
    # Validate
    valid = bool(flow_graph.is_valid())
    if not valid and not force:
        flow_graph.import_data(initial_data)
        flow_graph.rewrite()
        return {"ok": False, "errors": [{"code": "gnu_validation", "message": "GRC validation failed"}]}
        
    # Commit changes by serializing
    from gnuradio.grc.core.io import yaml as _grc_yaml
    content = _grc_yaml.dump(flow_graph.export_data())
    with open(flow_graph.grc_file_path, "w", encoding="utf-8") as f:
        f.write(content)
        
    return {"ok": True}

def embed_query(query: str) -> list[float]:
    client = OpenAI(base_url="http://localhost:11434/v1", api_key="not-needed")
    response = client.embeddings.create(
        model="embeddinggemma:latest",
        input="task: search result | query: " + query
    )
    return response.data[0].embedding

def query_catalog(query: str, limit: int = 5) -> dict[str, Any]:
    q = " ".join(str(query).split())
    if not q:
        return {"ok": False, "results": [], "message": "query must be non-empty"}
        
    try:
        query_vec = embed_query(q)
    except Exception as exc:
        return {"ok": False, "results": [], "message": f"Embedding failed: {exc}"}
        
    db_path = "src/grc_agent/vectors/catalog_ollama.db"
    if not os.path.exists(db_path):
        return {"ok": False, "results": [], "message": "Catalog DB not found"}
        
    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.row_factory = sqlite3.Row
        
        vec_rows = conn.execute(
            "SELECT rowid, distance FROM catalog_idx WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_vec), limit + 1),
        ).fetchall()
        
        results = []
        for row in vec_rows:
            rowid = row["rowid"]
            distance = row["distance"]
            chunk = conn.execute(
                "SELECT block_id FROM catalog_chunks WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if not chunk:
                continue
                
            block_id = chunk["block_id"]
            rendered = render_catalog_block(block_id, distance)
            if rendered:
                results.append(rendered)
                
            if len(results) >= limit:
                break
                
        return {
            "ok": True,
            "query": q,
            "results": results,
            "output_truncated": len(vec_rows) > limit
        }
    finally:
        conn.close()

def render_catalog_block(block_id: str, distance: float) -> dict[str, Any]:
    platform = get_platform()
    fg = platform.make_flow_graph()
    try:
        b = fg.new_block(block_id)
    except KeyError:
        return None
    fg.rewrite()
    
    params = {}
    for k, p in b.params.items():
        if keep_param(k, p, b):
            params[k] = {
                "id": k,
                "label": getattr(p, "name", "") or k,
                "dtype": getattr(p, "dtype", "") or "raw",
                "default": getattr(p, "default", "") or "",
                "options": [getattr(o, "key", str(o)) for o in getattr(p, "options", [])] if getattr(p, "options", None) else None,
                "option_labels": [getattr(o, "value", str(o)) for o in getattr(p, "options", [])] if getattr(p, "options", None) else None
            }
            
    inputs = []
    for p in b.active_sinks:
        inputs.append({
            "port_id": str(p.key),
            "dtype": str(getattr(p, "dtype", "")),
            "domain": str(getattr(p, "domain", "") or "stream"),
        })
    outputs = []
    for p in b.active_sources:
        outputs.append({
            "port_id": str(p.key),
            "dtype": str(getattr(p, "dtype", "")),
            "domain": str(getattr(p, "domain", "") or "stream"),
        })
        
    return {
        "block_id": block_id,
        "label": getattr(b, "label", block_id),
        "category": " > ".join(b.category) if isinstance(b.category, list) else str(b.category),
        "params": params,
        "inputs": inputs,
        "outputs": outputs,
        "distance": round(distance, 3)
    }

def query_docs(query: str, limit: int = 5) -> dict[str, Any]:
    q = " ".join(str(query).split())
    if not q:
        return {"ok": False, "answer": "", "message": "query must be non-empty"}
        
    try:
        query_vec = embed_query(q)
    except Exception as exc:
        return {"ok": False, "answer": "", "message": f"Embedding failed: {exc}"}
        
    db_path = "src/grc_agent/vectors/docs_ollama.db"
    if not os.path.exists(db_path):
        return {"ok": False, "answer": "", "message": "Docs DB not found"}
        
    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.row_factory = sqlite3.Row
        
        vec_rows = conn.execute(
            "SELECT rowid, distance FROM docs_idx WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_vec), limit),
        ).fetchall()
        
        sources = []
        for row in vec_rows:
            rowid = row["rowid"]
            distance = row["distance"]
            chunk = conn.execute(
                "SELECT path, heading, payload FROM docs_chunks WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if chunk:
                sources.append({
                    "path": chunk["path"],
                    "heading": chunk["heading"] if chunk["heading"] else "",
                    "distance": distance,
                    "content": chunk["payload"],
                })
                
        if not sources:
            return {"ok": False, "answer": "No matching documentation found."}
            
        context_parts = [
            f"# Source: {s['path']} — {s['heading']}\n{s['content']}" for s in sources
        ]
        context = "\n\n---\n\n".join(context_parts)
        
        prompt = (
            "You are answering a GNU Radio question. Use ONLY the documentation "
            "below. Ground every claim in the docs and cite the source file name. "
            "The sources below were retrieved as relevant to this question.\n\n"
            "Answer concisely and directly. If a specific sub-question is not "
            "addressed by the sources, say which part is not covered, but still "
            "answer what IS covered.\n\n"
            "Do not make up information. If NONE of the sources are related to "
            'the question, say exactly: "The provided documentation does not '
            'cover this."\n\n'
            f"Question: {q}\n\n"
            f"Documentation:\n{context}"
        )
        
        client = OpenAI(base_url="http://localhost:11434/v1", api_key="not-needed")
        response = client.chat.completions.create(
            model="qwen3.6:35b-a3b-q4_K_M",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        answer = response.choices[0].message.content
        
        return {
            "ok": True,
            "question": q,
            "answer": answer,
            "sources": [{"path": s["path"], "distance": round(s["distance"], 3)} for s in sources]
        }
    finally:
        conn.close()

def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    from duckduckgo_search import DDGS
    clamped = max(1, min(int(max_results), 10))
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=clamped):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "content": r.get("body", "")
                })
        return {"ok": True, "results": results, "message": ""}
    except Exception as exc:
        return {
            "ok": False,
            "message": f"web_search failed: {exc}",
            "error_type": "network_error",
        }

def web_fetch(url: str) -> dict[str, Any]:
    import httpx
    from bs4 import BeautifulSoup
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        response = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string.strip() if soup.title else ""
        
        # Decompose script and style tags
        for script in soup(["script", "style"]):
            script.decompose()
            
        content = soup.get_text(separator="\n")
        lines = (line.strip() for line in content.splitlines())
        chunks = (phrase for line in lines for phrase in line.split("  "))
        clean_text = "\n".join(chunk for chunk in chunks if chunk)
        
        # Extract links
        links = []
        for a in soup.find_all("a", href=True):
            links.append({
                "title": a.get_text().strip(),
                "url": a["href"]
            })
            if len(links) >= 50:
                break
                
        return {
            "ok": True,
            "title": title,
            "content": clean_text[:50000],
            "links": links,
            "message": ""
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"web_fetch failed: {exc}",
            "error_type": "network_error",
        }
