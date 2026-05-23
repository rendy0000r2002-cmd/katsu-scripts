#!/bin/bash
# 清掉測試用的 99999_test_symlink
set -e
V1=/volume2/homes/ETtomorrow/12_女子開箱/99999_test_symlink
V2=/volume2/homes2/ETtomorrow/12_女子開箱/99999_test_symlink

echo "=== 清測試資料 @ $(date) ==="

if mountpoint -q "$V1" 2>/dev/null; then
  umount "$V1" && echo "umount v1 ✓"
fi
[ -d "$V1" ] && rmdir "$V1" && echo "rmdir v1 ✓"
[ -d "$V2" ] && rm -rf "$V2" && echo "rm -rf v2 ✓"

echo "=== DONE ==="
