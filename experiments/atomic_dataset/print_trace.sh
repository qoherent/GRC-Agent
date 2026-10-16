#!/usr/bin/env bash
# print_trace.sh — print one full tier-1 trace, untruncated.
#
#   ./print_trace.sh 42                 # 42nd row of tier1_sft_trajectories.jsonl (1-based)
#   ./print_trace.sh t1_retune_xxx      # by scenario id
#   ./print_trace.sh 42 -a              # same trace, raw full checkpoint (incl. audit fields, via jq)
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

JSONL=output/tier1_sft_trajectories.jsonl
[[ -f $JSONL ]] || { echo "missing $JSONL — run the collector first" >&2; exit 1; }
[[ $# -ge 1 ]] || { echo "usage: print_trace.sh <row | scenario-id> [-a]" >&2; exit 1; }

key=$1; audit=${2:-}

id=$(K=$key P=$JSONL python3 - <<'PY'
import json, os
k, p = os.environ['K'], os.environ['P']
rows = open(p).readlines()
row = json.loads(rows[int(k) - 1] if k.isdigit()
                else next(l for l in rows if f'"scenario_id": "{k}"' in l))
print(row['scenario_id'])
PY
)

if [[ $audit == -a ]]; then
  jq . "output/$id.json"
else
  python3 - "$id" <<'PY'
import json, sys
sid = sys.argv[1]
r = json.load(open(f'output/{sid}.json'))
print(f"=== {sid}  ({r.get('category')})  msgs={len(r['messages'])}  "
      f"valid={r.get('flowgraph_valid')}  guardrails={len(r.get('guardrail_events') or [])}  "
      f"usage={r.get('usage')}\n")
for m in r['messages']:
    if m['role'] == 'system':
        print(">>> SYSTEM\n" + m['content'] + "\n")
    elif m['role'] == 'user':
        print(">>> USER\n" + m['content'] + "\n")
    elif m['role'] == 'assistant':
        if m.get('reasoning'):
            print(">>> REASONING\n" + m['reasoning'] + "\n")
        for tc in m.get('tool_calls', []):
            fn = tc['function']
            print(f">>> TOOL CALL: {fn['name']}\n" + fn['arguments'] + "\n")
        if (m.get('content') or '').strip():
            print(">>> ASSISTANT\n" + m['content'] + "\n")
    elif m['role'] == 'tool':
        print(f">>> TOOL RESULT ({m.get('tool_call_id', '')})\n" + m['content'] + "\n")
PY
fi
