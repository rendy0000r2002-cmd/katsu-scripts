#!/bin/bash
# 用 ETtomorrow 從 NAS 內部用 smbclient 看 Samba 實際 serve 的內容
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/diag_smbclient.log

# 從 .env 撈 ETtomorrow 密碼（或讓 user 提供）
# Synology user 密碼通常不在 .env，但我們有 NAS_SSH_PASSWORD reference
PASS="${1:-***REDACTED-NAS-PASS***}"

{
  echo "=== @ $(date) ==="
  echo ""
  echo "--- smbclient 用 ETtomorrow 列 home/12_女子開箱 ---"
  /var/packages/SMBService/target/usr/bin/smbclient //localhost/home -U "ETtomorrow%$PASS" -c "cd 12_女子開箱; ls" 2>&1 | head -30
  echo ""
  echo "--- 試直接 cd 進 symlink 並 ls ---"
  /var/packages/SMBService/target/usr/bin/smbclient //localhost/home -U "ETtomorrow%$PASS" -c "cd \"12_女子開箱/99999_test_symlink\"; ls" 2>&1 | head -10
  echo ""
  echo "--- 看 smbd log 是否有 wide-link / symlink 拒絕訊息 ---"
  grep -iE "symlink|wide|widelink|denied" /var/log/samba/log.smbd 2>/dev/null | tail -15
} > "$LOG" 2>&1
cat "$LOG"
