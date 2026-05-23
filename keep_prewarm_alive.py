"""
確認 NAS katsu-web 容器內 prewarm-thumbs.mjs 還活著，沒活就重啟。
排程：Windows Task Scheduler 每週一次。

prewarm wrapper (prewarm-loop.sh) 平常會在 prewarm-thumbs.mjs 撞 502 時自爬起來，
但容器/NAS 重啟、wrapper 自己被砍時就要靠這支腳本拉回來。
"""
import base64
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import paramiko

LOG_DIR = Path(__file__).parent / "logs"

NAS_HOST = "192.168.18.6"
NAS_USER = "ETtomorrow"
NAS_PASS = os.environ.get("NAS_SSH_PASS", "")

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")

PREWARM_LOOP_SCRIPT = """#!/bin/sh
while true; do
  echo "[$(date)] start" >> /data/thumb-cache/prewarm.log
  node /app/scripts/prewarm-thumbs.mjs >> /data/thumb-cache/prewarm.log 2>&1
  echo "[$(date)] died, restart in 15s" >> /data/thumb-cache/prewarm.log
  sleep 15
done
"""

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / f"keep_prewarm_alive.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def send_tg(text):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        body = json.dumps({"chat_id": TG_CHAT, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log(f"TG 失敗: {e}")


import os, subprocess

# 在 NAS 本機跑時直接用 docker，不必 SSH 回自己
IS_NAS = os.name == "posix" and os.path.exists("/volume1")


def ssh_run(c, cmd, timeout=60):
    """SSH 版（從 PC 跑到 NAS 用）。timeout 提高到 60s 避免 ls 等慢命令卡。"""
    full = f"echo {NAS_PASS} | sudo -S -p '' bash -lc {cmd!r}"
    _, stdout, _ = c.exec_command(full, timeout=timeout)
    return stdout.read().decode("utf-8", errors="replace")


def local_run(cmd, timeout=60):
    """NAS 本機版：直接 subprocess docker，不過 SSH。"""
    is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
    # docker 用絕對路徑避免 PATH 問題
    cmd = cmd.replace("docker ", "/usr/local/bin/docker ", 1) if cmd.startswith("docker ") else cmd
    full = cmd if is_root else f"sudo -n {cmd}"
    r = subprocess.run(full, shell=True, capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def run_cmd(c, cmd, timeout=60):
    """統一介面：NAS 本機走 local，否則 SSH。"""
    if IS_NAS:
        return local_run(cmd, timeout=timeout)
    return ssh_run(c, cmd, timeout=timeout)


def main():
    log("=== keep_prewarm_alive 檢查 ===")
    c = None
    if not IS_NAS:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(NAS_HOST, username=NAS_USER, password=NAS_PASS, timeout=15)

    ps = run_cmd(c, "docker exec katsu-web-v2 ps aux")
    loop_alive = "prewarm-loop.sh" in ps
    worker_alive = "prewarm-thumbs.mjs" in ps

    if loop_alive and worker_alive:
        log("prewarm wrapper + worker 都健在")
        if c is not None:
            c.close()
        return

    if loop_alive and not worker_alive:
        log("⚠️ wrapper 在但 worker 不在，先觀察一下（wrapper sleep 15s 後會自起新 worker）")
        if c is not None:
            c.close()
        return

    log("⚠️ prewarm wrapper 不在了，重新啟動")

    # 寫 loop script + 啟動
    b64 = base64.b64encode(PREWARM_LOOP_SCRIPT.encode()).decode()
    write = f"docker exec katsu-web-v2 sh -c 'echo {b64} | base64 -d > /tmp/prewarm-loop.sh && chmod +x /tmp/prewarm-loop.sh'"
    run_cmd(c, write, timeout=30)

    launch = "docker exec -d katsu-web-v2 sh /tmp/prewarm-loop.sh"
    run_cmd(c, launch, timeout=15)

    # 確認：detached process 需要短暫等待才會出現在 ps，retry 3 次
    import time as _time
    for attempt in range(3):
        _time.sleep(3)
        ps2 = run_cmd(c, "docker exec katsu-web-v2 ps aux")
        if "prewarm-loop.sh" in ps2:
            break
    if "prewarm-loop.sh" in ps2:
        cached = run_cmd(c, "docker exec katsu-web-v2 sh -c 'ls /data/thumb-cache | wc -l'", timeout=120).strip().splitlines()[-1].strip()
        msg = (
            f"🔄 prewarm 重啟成功\n"
            f"目前快取縮圖：{cached} 張\n"
            f"loop wrapper 已起，撞 502 會自爬"
        )
        log(msg)
        send_tg(msg)
    else:
        msg = "❌ prewarm 重啟失敗，需要手動處理"
        log(msg)
        send_tg(msg)

    if c is not None:
        c.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log(f"FATAL: {e}\n{tb}")
        send_tg(f"⚠️ keep_prewarm_alive 執行失敗：{e!r}")
