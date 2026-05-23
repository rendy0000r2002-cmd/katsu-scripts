#!/bin/bash
# 從 symlink 方案切換到 bind mount 方案
# 1. 清掉所有舊的 v1→v2 symlink
# 2. cron 把 symlink_v2_watcher 改成 union_v2_watcher
# 3. 立刻跑一輪新 watcher 重建為 bind mount

LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/switch_to_bind_mount.log
SCRIPTS=/volume2/docker-prod/scripts/原初映像片庫
V1_BASE=/volume2/homes/ETtomorrow
V2_BASE=/volume2/homes2/ETtomorrow

{
  echo "=== @ $(date) ==="

  echo ""
  echo "--- Step 1: 清舊 v1→v2 symlink ---"
  removed=0
  for v1_entry in "$V1_BASE"/*/*; do
    [ -L "$v1_entry" ] || continue
    target=$(readlink "$v1_entry")
    case "$target" in
      "$V2_BASE"/*)
        rm "$v1_entry"
        echo "  removed symlink: $v1_entry -> $target"
        removed=$((removed+1))
        ;;
    esac
  done
  echo "Total removed: $removed"

  echo ""
  echo "--- Step 2: 改 cron 從 symlink_v2_watcher → union_v2_watcher ---"
  CRON_FILE=/etc/cron.d/原初映像片庫
  TS=$(date +%Y%m%d_%H%M%S)
  cp "$CRON_FILE" "$SCRIPTS/cron.bak.${TS}_pre_bind_mount"
  # 取代 symlink → union
  sed -i 's|symlink_v2_watcher.sh|union_v2_watcher.sh|g' "$CRON_FILE"
  chmod 644 "$CRON_FILE"
  grep "union_v2_watcher\|symlink_v2_watcher" "$CRON_FILE" || echo "  (cron 沒這行，下次補)"

  echo ""
  echo "--- Step 3: 立刻跑一輪新 union watcher ---"
  bash "$SCRIPTS/union_v2_watcher.sh"

  echo ""
  echo "--- 驗證 mount 狀態 ---"
  grep "$V1_BASE" /proc/mounts || echo "  (沒任何 bind mount，是預期的如果 v2 還沒新案 case)"

  echo ""
  echo "=== DONE ==="
} > "$LOG" 2>&1
cat "$LOG"
