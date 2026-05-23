#!/bin/bash
# 強制重啟 Synology SMB Service（不靠 synoservice 那些找不到的指令）
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/force_restart_smb.log
{
  echo "=== force restart @ $(date) ==="
  echo ""
  echo "Try 1: synopkg restart SMBService"
  synopkg restart SMBService 2>&1
  echo "exit: $?"
  echo ""
  echo "等 3 秒..."
  sleep 3
  echo ""
  echo "Try 2: /var/packages/SMBService/target/var/scripts/script.sh restart"
  /var/packages/SMBService/target/var/scripts/script.sh restart 2>&1
  echo "exit: $?"
  echo ""
  sleep 3
  echo ""
  echo "Try 3: killall -HUP smbd（reload config）"
  killall -HUP smbd 2>&1
  echo "exit: $?"
  echo ""
  echo "--- Samba 程序狀態 ---"
  ps -eo pid,lstart,cmd | grep smbd | grep -v grep | head -5
  echo ""
  echo "--- testparm 看 wide links ---"
  /var/packages/SMBService/target/usr/bin/testparm -s 2>/dev/null | grep -iE "wide|follow|unix.ext" || echo "  沒匹配"
} > "$LOG" 2>&1
cat "$LOG"
