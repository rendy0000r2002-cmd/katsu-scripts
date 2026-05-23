#!/bin/bash
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/diag_v2_perms.log
{
  echo "=== @ $(date) ==="
  echo ""
  echo "--- v1 symlink stat ---"
  ls -la /volume2/homes/ETtomorrow/12_女子開箱/99999_test_symlink
  echo ""
  echo "--- v2 target dir stat ---"
  ls -la /volume2/homes2/ETtomorrow/12_女子開箱/99999_test_symlink/
  echo ""
  echo "--- v2 target dir perms (ls -la 父層) ---"
  ls -la /volume2/homes2/ETtomorrow/12_女子開箱/ | head -5
  echo ""
  echo "--- v2 homes2 root perms ---"
  ls -la /volume2/homes2/
  echo ""
  echo "--- synoacl 看 v2 homes2/ETtomorrow ---"
  /usr/syno/bin/synoacltool -get /volume2/homes2/ETtomorrow 2>&1 | head -15
  echo ""
  echo "--- 模擬 ETtomorrow 讀 v2 case ---"
  sudo -u ETtomorrow ls -la /volume2/homes2/ETtomorrow/12_女子開箱/99999_test_symlink/ 2>&1
  echo ""
  echo "--- 模擬 ETtomorrow 透過 symlink 讀 ---"
  sudo -u ETtomorrow cat /volume2/homes/ETtomorrow/12_女子開箱/99999_test_symlink/test_readme.txt 2>&1
  echo ""
  echo "--- smbclient 從 localhost 用 guest 看 home/12_女子開箱 ---"
  /var/packages/SMBService/target/usr/bin/smbclient //localhost/home -U guest%' ' -c "cd 12_女子開箱; ls" 2>&1 | head -25
  echo ""
  echo "--- smbd log 最後 20 行 (找 wide / symlink 相關) ---"
  ls /var/log/samba/ 2>&1
  tail -30 /var/log/samba/log.smbd 2>/dev/null
} > "$LOG" 2>&1
cat "$LOG"
