#!/bin/bash
# Ops-status manual trigger watcher.
# 由 katsu-web /api/ops-status/trigger 寫 flag 檔到 /volume2/homes/ETtomorrow/_ops_triggers/
# 此 script 由 cron 每分鐘檢查、執行對應任務、刪 flag。
set -e

FLAG_DIR=/volume2/homes/ETtomorrow/_ops_triggers
LOG_DIR=/volume2/docker-prod/scripts/原初映像片庫/logs
SCRIPTS=/volume2/docker-prod/scripts/原初映像片庫
mkdir -p "$FLAG_DIR" "$LOG_DIR"

run_job() {
  local name="$1"
  local cmd="$2"
  local log="$LOG_DIR/${name}_manual_$(date +%Y%m%d_%H%M%S).log"
  echo "=== ops-status triggered: $(date) ===" > "$log"
  echo "command: $cmd" >> "$log"
  bash -c "$cmd" >> "$log" 2>&1 &
  echo "started PID $! → $log"
}

shopt -s nullglob
for flag in "$FLAG_DIR"/*.flag; do
  name=$(basename "$flag" .flag)
  case "$name" in
    enrich_locations)
      run_job "$name" "/usr/local/bin/docker exec katsu-scripts-v2 python $SCRIPTS/enrich_locations.py"
      ;;
    tag_locations_daily)
      run_job "$name" "/usr/local/bin/docker exec katsu-scripts-v2 python $SCRIPTS/tag_locations_daily.py"
      ;;
    backfill_dimensions)
      run_job "$name" "/usr/local/bin/docker exec katsu-scripts-v2 python $SCRIPTS/backfill_dimensions.py"
      ;;
    daily_sync)
      run_job "$name" "$SCRIPTS/daily_sync.sh"
      ;;
    rebuild_web_full)
      run_job "$name" "bash $SCRIPTS/rebuild_web_full.sh"
      ;;
    add_cleanup_cron)
      run_job "$name" "bash $SCRIPTS/add_cleanup_cron.sh"
      ;;
    cleanup_missing_detect)
      run_job "$name" "/usr/local/bin/docker exec katsu-scripts-v2 python $SCRIPTS/cleanup_missing_files.py"
      ;;
    setup_symlink_union)
      run_job "$name" "bash $SCRIPTS/setup_symlink_union.sh"
      ;;
    restart_samba)
      run_job "$name" "bash $SCRIPTS/restart_samba.sh"
      ;;
    symlink_watcher_now)
      run_job "$name" "bash $SCRIPTS/symlink_v2_watcher.sh"
      ;;
    verify_symlink_test)
      run_job "$name" "bash $SCRIPTS/verify_symlink_test.sh"
      ;;
    check_smb_conf)
      run_job "$name" "bash $SCRIPTS/check_smb_conf.sh"
      ;;
    force_restart_smb)
      run_job "$name" "bash $SCRIPTS/force_restart_smb.sh"
      ;;
    add_insecure_wide_links)
      run_job "$name" "bash $SCRIPTS/add_insecure_wide_links.sh"
      ;;
    diag_smb_homes)
      run_job "$name" "bash $SCRIPTS/diag_smb_homes.sh"
      ;;
    fix_symlinks_owner)
      run_job "$name" "bash $SCRIPTS/fix_existing_symlinks_owner.sh"
      ;;
    diag_v2_perms)
      run_job "$name" "bash $SCRIPTS/diag_v2_perms.sh"
      ;;
    diag_smbclient)
      run_job "$name" "bash $SCRIPTS/diag_smbclient.sh"
      ;;
    switch_to_bind_mount)
      run_job "$name" "bash $SCRIPTS/switch_to_bind_mount.sh"
      ;;
    union_watcher_now)
      run_job "$name" "bash $SCRIPTS/union_v2_watcher.sh"
      ;;
    cleanup_test_symlink)
      run_job "$name" "bash $SCRIPTS/cleanup_test_symlink.sh"
      ;;
    deploy_editor_schedule)
      run_job "$name" "bash /volume2/homes/ETtomorrow/_staging/deploy_editor_schedule.sh"
      ;;
    *)
      echo "[ops-trigger] unknown job: $name (flag ignored)" >&2
      ;;
  esac
  rm -f "$flag"
done
