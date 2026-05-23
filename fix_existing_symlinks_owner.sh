#!/bin/bash
# 修現有已被 root 建的 v1→v2 symlink owner，改成 ETtomorrow
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/fix_existing_symlinks_owner.log
{
  echo "=== @ $(date) ==="
  count=0
  for v1_link in /volume2/homes/ETtomorrow/*/*; do
    [ -L "$v1_link" ] || continue
    target=$(readlink "$v1_link")
    case "$target" in
      /volume2/homes2/ETtomorrow/*)
        chown -h ETtomorrow:users "$v1_link"
        echo "  fixed: $v1_link"
        count=$((count+1))
        ;;
    esac
  done
  echo "Total fixed: $count"
} > "$LOG" 2>&1
cat "$LOG"
