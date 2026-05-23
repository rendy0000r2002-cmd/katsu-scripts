#!/bin/sh
# 每日 02:00 跑 vision_pass + 自動 rename，只處理尚未標籤的檔案。
# 區域校驗已在 vision_pass_v2.py 內建 (out-of-area 不寫入)，可放心 --apply --yes
cd /volume2/docker-prod/scripts/原初映像片庫 || exit 1
LOG=logs/vision_pass_v2.log
TS=$(date '+%F %T')
echo "[daily] start $TS" >> logs/wrap.log

# 兩個 scope：空拍素材 + 空景
for SCOPE in "0_空拍素材 (重要)" "0_空景"; do
  /usr/local/bin/docker exec -e GEMINI_MODEL=gemini-flash-latest katsu-scripts-v2 python3 vision_pass_v2.py \
    --root "$SCOPE" --apply --yes --workers 4 \
    >> "$LOG" 2>&1
  RC=$?
  echo "[daily] $SCOPE rc=$RC" >> logs/wrap.log
done

# 收尾：rename + LINE 推播
/usr/local/bin/docker exec katsu-scripts-v2 python3 logs/finisher_script.py >> logs/wrap.log 2>&1
TS2=$(date '+%F %T')
echo "[daily] done $TS2" >> logs/wrap.log
