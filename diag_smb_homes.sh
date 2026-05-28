#!/bin/bash
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/diag_smb_homes.log
{
  echo "=== @ $(date) ==="

  echo "--- testparm 完整輸出（看 [homes] / [home] share）---"
  /var/packages/SMBService/target/usr/bin/testparm -s 2>/dev/null

  echo ""
  echo "--- /etc/samba/ 目錄結構 ---"
  ls -la /etc/samba/

  echo ""
  echo "--- 從 NAS 端用 smbclient 直接列 home share ---"
  /var/packages/SMBService/target/usr/bin/smbclient -L localhost -U guest -N 2>&1 | head -20

  echo ""
  echo "--- 列 home share 內容（用 smbclient） ---"
  # 用 guest 不行就試 ETtomorrow 帳號（用 -N 不要密碼，會失敗但會看到 share）
  /var/packages/SMBService/target/usr/bin/smbclient //localhost/home -U ETtomorrow%xxxxx -c "cd 12_女子開箱; ls" 2>&1 | head -30

  echo ""
  echo "--- smbd log 最後幾行 ---"
  tail -20 /var/log/samba/log.smbd 2>/dev/null || ls /var/log/samba/ 2>/dev/null
} > "$LOG" 2>&1
cat "$LOG"
