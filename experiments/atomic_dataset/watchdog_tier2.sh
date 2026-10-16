#!/usr/bin/env bash
# ==============================================================================
# Tier 2 campaign watchdog (multi-turn suite)
#
# Same contract as watchdog_tier1.sh: restarts the checkpointed collector on
# death, resumes from checkpoints, and stops on completion, all-checkpoints,
# or two consecutive stalled attempts.
#
# Launch (detached, survives session teardown):
#   setsid nohup bash experiments/atomic_dataset/watchdog_tier2.sh &
# ==============================================================================
set -u
cd /mnt/hdd/storage/GRC-Agent || exit 1
WLOG=/tmp/t2_watchdog.log
RLOG=/tmp/t2_campaign.log
MANIFEST=experiments/atomic_dataset/output/tier2_manifest.json

prev=-1
stall=0
while true; do
    .venv/bin/python -m experiments.atomic_dataset.run_scenarios \
        --suite tier2 --resume >>"$RLOG" 2>&1
    rc=$?
    TOTAL=$(python3 -c "import json; print(json.load(open('$MANIFEST'))['total'])" 2>/dev/null || echo 600)
    cur=$(ls experiments/atomic_dataset/output/t2_*.json 2>/dev/null | wc -l)
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
