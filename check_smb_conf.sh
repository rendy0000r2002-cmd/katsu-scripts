#!/bin/bash
# 檢查 Samba 實際設定
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/check_smb_conf.log
{
  echo "=== check @ $(date) ==="
  echo ""
  echo "-- /etc/samba/smb.conf wide-link 相關行 --"
  grep -niE "follow.symlinks|wide.links|unix.extensions|allow.insecure" /etc/samba/smb.conf 2>/dev/null || echo "  (沒匹配)"
  echo ""
  echo "-- 完整 smb.conf 前 80 行 --"
  head -80 /etc/samba/smb.conf
  echo ""
  echo "-- Samba 服務狀態 --"
  ps aux | grep -iE "smbd|nmbd" | grep -v grep
  echo ""
  echo "-- testparm 看實際生效設定 --"
  /usr/syno/bin/testparm -s 2>&1 | grep -iE "wide|follow|unix.ext|symlink" || echo "  testparm 沒找到，或沒匹配"
} > "$LOG" 2>&1
cat "$LOG"
