"""
檢查縮圖預熱進度，發 Telegram 通知。給 Windows Task Scheduler 排程用（一次性）。
"""
from __future__ import annotations
import json
import sys
import urllib.request
from pathlib import Path

import paramiko

ROOT = Path(r"/volume2/docker-prod/scripts/原初映像片庫")
TG_TOKEN = "8583367633:AAFjQyLGLvYrWOZtOrtWm_vpaVpq_pXWBhY"
TG_CHAT = "8635121564"

NAS_HOST = "192.168.18.6"
NAS_USER = "ETtomorrow"
NAS_PASS = "Et666666"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def ssh_run(c, cmd, timeout=20):
    full = f"echo {NAS_PASS} | sudo -S -p '' bash -lc {cmd!r}"
    _, stdout, _ = c.exec_command(full, timeout=timeout)
    return stdout.read().decode("utf-8", errors="replace")


def send_tg(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TG_CHAT, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def main():
    drv = json.loads((ROOT / "index_v2.json").read_text(encoding="utf-8"))
    nas = json.loads((ROOT / "index_nas_v2.json").read_text(encoding="utf-8"))
    total = drv["count"] + nas["count"]

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(NAS_HOST, username=NAS_USER, password=NAS_PASS, timeout=15)

    cached_str = ssh_run(c, "docker exec katsu-web sh -c 'ls /data/thumb-cache | wc -l'").strip().splitlines()[-1].strip()
    cached = int(cached_str)

    ps = ssh_run(c, "docker exec katsu-web ps aux")
    healthy = "prewarm-thumbs.mjs" in ps and "prewarm-loop.sh" in ps

    log_tail = ssh_run(c, "docker exec katsu-web tail -1 /data/thumb-cache/prewarm.log").strip()
    last_progress = log_tail.split()[-5:] if log_tail else []

    c.close()

    pct = cached / total * 100
    status_emoji = "✅" if healthy else "⚠️"
    msg = (
        f"📊 縮圖預熱進度回報\n"
        f"\n"
        f"覆蓋率：{pct:.1f}% ({cached:,}/{total:,})\n"
        f"prewarm 狀態：{status_emoji} {'健康運行中' if healthy else '已停止 (需重啟)'}\n"
        f"log 最新：{' '.join(last_progress) if last_progress else '(無)'}\n"
    )
    send_tg(msg)
    print(msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_tg(f"⚠️ 縮圖預熱進度檢查失敗：{e!r}")
        raise
