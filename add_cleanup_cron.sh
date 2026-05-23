#!/bin/bash
# 加 cleanup_missing_files detect 排程到 /etc/cron.d/原初映像片庫
# detect 每日 04:00 跑，發 Telegram 含 web 確認連結，使用者點連結到片庫網站確認
set -e

CRON_FILE="/etc/cron.d/原初映像片庫"

DETECT_LINE="0	4	*	*	*	root	/usr/local/bin/docker exec katsu-scripts-v2 python /volume2/docker-prod/scripts/原初映像片庫/cleanup_missing_files.py >> /volume2/docker-prod/scripts/原初映像片庫/logs/cleanup_missing_cron.log 2>&1"

echo "=== 1/2 備份 cron file ==="
TS=$(date +%Y%m%d_%H%M%S)
cp "$CRON_FILE" "/volume2/docker-prod/scripts/原初映像片庫/cron.bak.${TS}_pre_cleanup"
echo "Backup: cron.bak.${TS}_pre_cleanup"

echo ""
echo "=== 2/2 加 cleanup detect 行（每日 04:00）==="
# 順便清掉之前可能加進去的 telegram_callback_listener（改方案後不用）
if grep -q "telegram_callback_listener" "$CRON_FILE"; then
    sed -i.tmp '/telegram_callback_listener/d' "$CRON_FILE"
    rm -f "${CRON_FILE}.tmp"
    echo "✓ 移除舊的 telegram_callback_listener 行"
fi

if grep -q "cleanup_missing_files.py" "$CRON_FILE"; then
    echo "已存在 cleanup_missing_files 行，略過"
else
    TMP=$(mktemp)
    awk -v new_line="$DETECT_LINE" '
    /_ops_trigger_watcher.sh/ && !inserted { print new_line; inserted=1 }
    { print }
    ' "$CRON_FILE" > "$TMP"
    cat "$TMP" > "$CRON_FILE"
    rm "$TMP"
    echo "✓ 加入 cleanup_missing_files (每日 04:00)"
fi

chmod 644 "$CRON_FILE"

echo ""
echo "=== 驗證 ==="
grep "cleanup_missing_files" "$CRON_FILE" | head -2

echo ""
echo "=== DONE ==="
echo "下次 04:00 自動偵測，有失蹤檔會發 Telegram + 確認連結給你"
