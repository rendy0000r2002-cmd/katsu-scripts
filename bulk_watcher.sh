#!/bin/sh
PID=$1
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/watcher.log
echo "[$(date)] watching PID $PID" >> $LOG

if [ -z "$PID" ]; then
    echo "[$(date)] empty PID, abort" >> $LOG
    exit 1
fi

while kill -0 $PID 2>/dev/null; do
    sleep 60
done

echo "[$(date)] PID $PID gone, restarting paused containers" >> $LOG
/usr/local/bin/docker start has_host_short >> $LOG 2>&1
/usr/local/bin/docker start has_host_detector >> $LOG 2>&1

python3 << "PYEND" >> $LOG 2>&1
import json, urllib.request
s = json.load(open("/volume2/docker-prod/scripts/原初映像片庫/tag_state.json"))
done = s.get("done", {})
tagged = sum(1 for v in done.values() if str(v).startswith("tagged:"))
indoor = sum(1 for v in done.values() if v == "indoor-skip")
nolm = sum(1 for v in done.values() if v == "no-landmark")
fail_list = len(s.get("failed", []))

msg = f"""🎉 片庫地點標註 bulk run 完成！

總處理: {len(done)}
標到地點: {tagged}
室內跳過: {indoor}
無地標: {nolm}
失敗: {fail_list}

has_host_short + has_host_detector 已自動 restart 接續跑。"""

body = json.dumps({"chat_id":"8635121564", "text": msg}).encode()
req = urllib.request.Request(
    "https://api.telegram.org/bot***REDACTED-TG-TOKEN***/sendMessage",
    data=body, headers={"Content-Type":"application/json"})
print(urllib.request.urlopen(req, timeout=15).read().decode()[:200])
PYEND

echo "[$(date)] watcher done" >> $LOG
