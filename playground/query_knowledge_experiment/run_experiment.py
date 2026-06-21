"""Phase 3 — exercise query_knowledge via the agent (smoke test).

Confirms the tool routes to its two backends (catalog blocks, docs RAG) and
degrades gracefully when an index/embedding backend is unavailable (per
AGENTS.md: launch into degraded mode, never sys.exit on network failure).
"""
import json
import time
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

QUERIES = [
    ("throttle", "catalog"),
    ("FM demodulator", "catalog"),
    ("center frequency", "catalog"),
    ("How do I save a flowgraph in headless mode?", "docs"),
    ("What is a hier block?", "docs"),
]


def main():
    agent = GrcAgent(FlowgraphSession())
    out = {}
    for query, domain in QUERIES:
        t0 = time.perf_counter()
        try:
            result = agent.execute_tool("query_knowledge", {"query": query, "domain": domain})
            latency_ms = (time.perf_counter() - t0) * 1000
            if isinstance(result, dict):
                ok = bool(result.get("ok", False))
                degraded = bool(result.get("degraded_retrieval", False))
                msg = result.get("message", "")
            else:
                ok, degraded, msg = False, False, repr(result)
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            ok, degraded, msg = False, True, f"EXCEPTION: {exc!r}"
        slug = query.lower().replace(" ", "_")[:24]
        key = f"{domain}:{slug}"
        out[key] = {"ok": ok, "degraded": degraded, "message": msg,
                    "latency_ms": round(latency_ms, 2)}
        flag = "OK" if ok else ("DEGRADED" if degraded else "FAIL")
        print(f"  [{flag:8s}] {domain:7s} {query!r:42s} {latency_ms:7.1f} ms  {msg[:60]}")
    (RESULTS / "query_smoke.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {RESULTS / 'query_smoke.json'}")


if __name__ == "__main__":
    main()
