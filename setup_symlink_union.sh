#!/bin/bash
# 一鍵設定 v1↔v2 symlink union：
# 1. 加 cron 每分鐘跑 symlink_v2_watcher.sh
# 2. 開 Samba wide links + unix extensions（讓 SMB 客戶端能跨 share 跟隨 symlink）
# 3. reload Samba 套用
# 4. 立刻跑一輪 watcher
set -e

CRON_FILE=/etc/cron.d/原初映像片庫
SMB_CONF=/etc/samba/smb.conf
SCRIPTS=/volume2/docker-prod/scripts/原初映像片庫

echo "=== 1/4 加 cron 行（每分鐘跑 watcher）==="
WATCHER_LINE="*	*	*	*	*	root	$SCRIPTS/symlink_v2_watcher.sh"
TS=$(date +%Y%m%d_%H%M%S)
cp "$CRON_FILE" "$SCRIPTS/cron.bak.${TS}_pre_symlink"
if grep -q "symlink_v2_watcher" "$CRON_FILE"; then
    echo "  cron 已含 watcher 行，略過"
else
    TMP=$(mktemp)
    awk -v new_line="$WATCHER_LINE" '
    /_ops_trigger_watcher.sh/ && !inserted { print new_line; inserted=1 }
    { print }
    ' "$CRON_FILE" > "$TMP"
    cat "$TMP" > "$CRON_FILE"
    rm "$TMP"
    chmod 644 "$CRON_FILE"
    echo "  ✓ 加入"
fi

echo ""
echo "=== 2/4 設定 Samba wide links ==="
cp "$SMB_CONF" "$SCRIPTS/smb.conf.bak.${TS}_pre_widelinks"

# 在 [global] 區段加 wide links / unix extensions
# Synology 預設可能沒有這幾行，加進去
NEED_RESTART=0
for setting in "follow symlinks = yes" "wide links = yes" "unix extensions = no"; do
  key=$(echo "$setting" | sed 's/ *= *.*//' | xargs)
  if grep -qE "^\s*${key}\s*=" "$SMB_CONF"; then
    # 已存在，改成正確值
    current=$(grep -E "^\s*${key}\s*=" "$SMB_CONF" | head -1 | sed 's/.*= *//' | xargs)
    expected=$(echo "$setting" | sed 's/.*= *//' | xargs)
    if [ "$current" != "$expected" ]; then
      sed -i "s|^\s*${key}\s*=.*|${setting}|" "$SMB_CONF"
      echo "  ✓ 改 $setting"
      NEED_RESTART=1
    else
      echo "  - 已設好: $setting"
    fi
  else
    # 不存在，在 [global] 區段尾加
    awk -v line="	${setting}" '
    /^\[global\]/ { in_global=1; print; next }
    /^\[/ && in_global { print line; in_global=0 }
    { print }
    END { if (in_global) print line }
    ' "$SMB_CONF" > "$SMB_CONF.tmp"
    cat "$SMB_CONF.tmp" > "$SMB_CONF"
    rm "$SMB_CONF.tmp"
    echo "  ✓ 加入 $setting"
    NEED_RESTART=1
  fi
done

echo ""
echo "=== 3/4 reload Samba ==="
if [ $NEED_RESTART -eq 1 ]; then
    synoservice --restart smbd 2>/dev/null || systemctl restart smbd 2>/dev/null || \
      /usr/syno/etc.defaults/rc.subr/smbd.subr restart 2>/dev/null || \
      echo "  ⚠️ 無法自動重啟 Samba，請手動：DSM 控制台 → 檔案服務 → SMB → 套用"
    echo "  ✓ Samba 重啟"
else
    echo "  - 設定沒變，不用重啟"
fi

echo ""
echo "=== 4/4 立刻跑一輪 watcher ==="
bash "$SCRIPTS/symlink_v2_watcher.sh"
echo "  watcher first run done"

echo ""
echo "=== DONE ==="
echo "現在 v2 case 資料夾會在 v1 出現對應 symlink（v1 沒同名才會）"
echo "log: /volume2/docker-prod/scripts/原初映像片庫/logs/symlink_watcher_*.log"
