#!/bin/bash
# 從 NAS 端驗證 v1/99999_test_symlink 的狀態
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/verify_symlink_test.log
{
  echo "=== verify @ $(date) ==="
  echo "-- ls /volume2/homes/ETtomorrow/12_女子開箱/ | grep 99999 --"
  ls -la /volume2/homes/ETtomorrow/12_女子開箱/ | grep 99999
  echo "-- stat --"
  stat /volume2/homes/ETtomorrow/12_女子開箱/99999_test_symlink
  echo "-- readlink --"
  readlink /volume2/homes/ETtomorrow/12_女子開箱/99999_test_symlink
  echo "-- cat 透過 symlink 讀內容 --"
  cat /volume2/homes/ETtomorrow/12_女子開箱/99999_test_symlink/test_readme.txt
  echo ""
} > "$LOG" 2>&1
cat "$LOG"
