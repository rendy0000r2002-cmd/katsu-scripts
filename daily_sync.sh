#!/bin/bash
# 防重疊：同時間只跑一個 instance
exec 200>/tmp/daily_sync.lock
flock -n 200 || { echo "another daily_sync is running, skip"; exit 0; }
# 每 2 小時跑的同步流程（NAS 版，取代 daily_sync.ps1）
cd /volume2/docker-prod/scripts/原初映像片庫 || exit 1

LOG_DIR=logs
mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d)
LOG="$LOG_DIR/sync_$TS.log"

run_step() {
  local name="$1"
  shift
  local script="$1"
  shift
  echo "=== [$name] $(date) ===" >> "$LOG"
  /usr/local/bin/docker exec -w /volume2/docker-prod/scripts/原初映像片庫 katsu-scripts-v2 python -u "$script" "$@" >> "$LOG" 2>&1
  local rc=$?
  if [ $rc -ne 0 ]; then
    echo "=== [FAILED at $name] $(date) ===" >> "$LOG"
    echo "FAILED at $name (exit $rc), see $LOG"
    exit 1
  fi
}

run_step 'scan drive'    scan.py
run_step 'scan nas'      scan_nas.py
run_step 'refine drive'  refine.py
run_step 'upload drive'  upload.py
run_step 'refine nas'    refine.py index_nas.json index_nas_v2.json
run_step 'upload nas'    upload.py index_nas_v2.json
run_step 'scan scripts'  scan_scripts.py
run_step 'locations p1'  extract_locations.py phase1
run_step 'enrich script' enrich_from_scripts.py
run_step 'locations app' extract_locations.py apply
run_step 'sync schedule' sync_schedule.py
run_step 'backfill dims' backfill_dimensions.py
run_step 'sync summary'  sync_summary.py

echo "=== [done] $(date) ===" >> "$LOG"
echo "done"
