#!/usr/bin/env bash
# ==============================================================================
# Tier 1 campaign watchdog
#
# Runs the checkpointed collector to completion. If the runner dies (kill,
# crash, transient API outage), it restarts after a pause and resumes from
# checkpoints. It stops when:
#   - the runner exits 0 (all scenarios pass), or
#   - all checkpoints exist (pass or fail — quality reviewed afterwards), or
#   - two consecutive attempts make zero checkpoint progress (stalled).
# Crash checkpoints (native C++ kills in the GRC layer) are RETRYABLE: the next
# attempt re-runs them, since the crash depends on the model's chosen actions.
#
# Launch (detached, survives session teardown):
#   setsid nohup bash experiments/atomic_dataset/watchdog_tier1.sh &
# ==============================================================================
set -u
cd /mnt/hdd/storage/GRC-Agent || exit 1
WLOG=/tmp/t1_watchdog.log
RLOG=/tmp/t1_campaign.log
MANIFEST=experiments/atomic_dataset/output/tier1_manifest.json

prev=-1
stall=0
while true; do
    .venv/bin/python -m experiments.atomic_dataset.run_scenarios \
        --suite tier1 --resume >>"$RLOG" 2>&1
    rc=$?
    # Suite size is data-driven (ineligible blocks are excluded); read the fresh
    # manifest the runner just rewrote at the start of that attempt.
    TOTAL=$(python3 -c "import json; print(json.load(open('$MANIFEST'))['total'])" 2>/dev/null || echo 1253)
    cur=$(ls experiments/atomic_dataset/output/t1_*.json 2>/dev/null | wc -l)
    echo "[$(date '+%m-%d %H:%M:%S')] attempt ended rc=$rc done=$cur/$TOTAL" >>"$WLOG"
    if [ "$rc" -eq 0 ] || [ "$cur" -ge "$TOTAL" ]; then
        echo "[$(date '+%m-%d %H:%M:%S')] CAMPAIGN COMPLETE rc=$rc done=$cur/$TOTAL" >>"$WLOG"
        break
    fi
    if [ "$cur" -eq "$prev" ]; then
        stall=$((stall + 1))
        if [ "$stall" -ge 2 ]; then
            echo "[$(date '+%m-%d %H:%M:%S')] STALLED: no checkpoint progress twice — stopping watchdog" >>"$WLOG"
            break
        fi
    else
        stall=0
    fi
    prev=$cur
    echo "[$(date '+%m-%d %H:%M:%S')] restarting in 30s (rc=$rc, done=$cur/$TOTAL)" >>"$WLOG"
    sleep 30
done
