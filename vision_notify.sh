#!/bin/sh
# 等 vision_pass.py 真的結束（pid 不見就 done），然後撈統計推 Telegram
set -e
LOGS=/volume2/docker-prod/scripts/原初映像片庫/logs
PID_FILE=$LOGS/vision_pass.pid
LOG=$LOGS/vision_pass.log

while [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; do
  sleep 60
done

# 撈最終 summary（最後幾行）
SUMMARY=$(tail -25 "$LOG")
# 撈 tag count
TAGGED=$(grep -c "tag 0\." "$LOG" 2>/dev/null || echo 0)

curl -s -X POST "https://api.telegram.org/bot***REDACTED-TG-TOKEN***/sendMessage"   -H "Content-Type: application/json"   -d "{\"chat_id\":\"8635121564\",\"text\":\"✅ Vision pass 跑完了

已 tag: ${TAGGED} 個檔

統計：
\`\`\`
${SUMMARY}
\`\`\`\",\"parse_mode\":\"Markdown\"}" >> $LOGS/vision_notify.log 2>&1
