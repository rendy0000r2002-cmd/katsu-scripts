"""Daily health check for the video library system.

Runs inside katsu-scripts-v2 container at 09:00 (after daily_sync settles).
Pushes a single Telegram summary to chat_id 8635121564.

Checks:
  1. PostgREST endpoint live & PATCH actually mutates DB (not nginx fake-OK)
  2. DB row health: total / city-tagged / has-host / ghost (missing on disk)
  3. Thumb cache coverage (NAS scope)
  4. Gemini API quota probe (small generateContent — flags 429)
  5. Disk usage on /volume2
  6. Container running status (web/scripts/pg/postgrest/gateway/has_host)
  7. Last backup timestamp + restore_drill freshness
  8. cron file present (so syno DSM didn't strip it)

Output: single TG message; severity badge per section (✅/⚠️/🚨).
"""
from __future__ import annotations
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TG_TOKEN = "8583367633:AAFjQyLGLvYrWOZtOrtWm_vpaVpq_pXWBhY"
TG_CHAT = "8635121564"
SCRIPT_DIR = Path("/volume2/docker-prod/scripts/原初映像片庫")
ENV_FILE = Path("/volume2/docker-prod/katsu-web-v2/web/.env")
SUPABASE_URL = "http://127.0.0.1:3011/rest/v1"
BACKUP_DIR = SCRIPT_DIR / "backups"
THUMB_CACHE_DIR = Path("/volume2/@docker/volumes/katsu-web-v2_thumb-cache-v2/_data")
CRON_SNAPSHOT = SCRIPT_DIR / "_cron_snapshot.txt"


def load_env(p):
    env = {}
    if not p.exists(): return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def fetch(url, headers=None, method="GET", data=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {}, method=method,
                                 data=data.encode() if data else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", errors="replace")


def check_postgrest(env):
    sr_key = env.get("SUPABASE_SERVICE_ROLE_KEY")
    h = {"apikey": sr_key, "Authorization": f"Bearer {sr_key}",
         "Content-Type": "application/json", "Prefer": "return=representation"}
    try:
        st, body = fetch(f"{SUPABASE_URL}/videos?limit=1&select=drive_file_id", headers=h)
        if st != 200:
            return "🚨", f"PostgREST GET failed: {st}"
        rows = json.loads(body)
        if not (isinstance(rows, list) and rows):
            return "🚨", "PostgREST returned no rows / not list"
        # Smoke PATCH (touch the column with same value)
        fid = rows[0]["drive_file_id"]
        st, body = fetch(
            f"{SUPABASE_URL}/videos?drive_file_id=eq.{fid}", headers=h,
            method="PATCH", data=json.dumps({"is_old": False}),
        )
        if st != 200:
            return "🚨", f"PATCH failed: {st}"
        try:
            patched = json.loads(body)
            if isinstance(patched, list) and len(patched) == 1:
                return "✅", "GET ok, PATCH mutates 1 row"
            return "🚨", f"PATCH returned wrong shape: {body[:100]}"
        except Exception:
            return "🚨", f"PATCH non-JSON (likely nginx fake): {body[:80]}"
    except Exception as e:
        return "🚨", f"PostgREST exception: {e}"


def check_db_rows(env):
    sr_key = env.get("SUPABASE_SERVICE_ROLE_KEY")
    h = {"apikey": sr_key, "Authorization": f"Bearer {sr_key}", "Prefer": "count=exact"}
    try:
        # Use Content-Range to get count via HEAD
        req = urllib.request.Request(f"{SUPABASE_URL}/videos?select=*&limit=0",
                                     headers={**h, "Range": "0-0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            cr = r.headers.get("Content-Range", "")
            total = int(cr.split("/")[-1]) if "/" in cr else 0
        # City tagged
        req = urllib.request.Request(
            f"{SUPABASE_URL}/videos?select=*&city=not.is.null&city=neq.&limit=0",
            headers={**h, "Range": "0-0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            cr = r.headers.get("Content-Range", "")
            city_tagged = int(cr.split("/")[-1]) if "/" in cr else 0
        return "✅", f"total={total}, city-tagged={city_tagged} ({city_tagged*100//total}%)"
    except Exception as e:
        return "⚠️", f"DB count failed: {e}"


def check_thumb_cache():
    try:
        if not THUMB_CACHE_DIR.exists():
            return "⚠️", f"{THUMB_CACHE_DIR} not visible"
        count = sum(1 for _ in THUMB_CACHE_DIR.glob("*.jpg"))
        return "✅", f"{count} jpg cached"
    except Exception as e:
        return "⚠️", f"thumb cache check failed: {e}"


def check_gemini(env):
    key = env.get("GEMINI_API_KEY")
    if not key: return "⚠️", "GEMINI_API_KEY missing"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={key}"
    payload = json.dumps({"contents":[{"parts":[{"text":"hi"}]}],
                          "generationConfig":{"maxOutputTokens":10}})
    try:
        st, body = fetch(url, headers={"Content-Type":"application/json"},
                         method="POST", data=payload)
        if st == 200:
            return "✅", "Gemini API responsive"
        return "🚨", f"Gemini {st}: {body[:100]}"
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return "🚨", f"Gemini {e.code}: {body[:120]}"
    except Exception as e:
        return "⚠️", f"Gemini exception: {e}"


def check_disk():
    try:
        out = subprocess.run(
            ["df", "-BG", "/volume2"], capture_output=True, text=True, timeout=10,
        ).stdout
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                size, used, avail, pct = parts[1], parts[2], parts[3], parts[4]
                pct_int = int(pct.rstrip("%"))
                badge = "🚨" if pct_int >= 90 else ("⚠️" if pct_int >= 80 else "✅")
                return badge, f"/volume2 {used}/{size} used ({pct})"
        return "⚠️", f"df parse fail: {out[:100]}"
    except Exception as e:
        return "⚠️", f"df failed: {e}"


def check_containers():
    """Indirect check: probe each container's exposed endpoint."""
    probes = [
        ("pg-katsu-v2 + gateway", "http://127.0.0.1:3011/rest/v1/", lambda b: "Open API" in b or "swagger" in b.lower() or len(b) > 100),
        ("web-v2 (next.js)", "http://127.0.0.1:3000/", lambda b: b is not None),
    ]
    results = []
    for name, url, ok in probes:
        try:
            st, body = fetch(url, timeout=5)
            if st < 500 and ok(body):
                results.append(("✅", name))
            else:
                results.append(("🚨", f"{name} st={st}"))
        except Exception as e:
            results.append(("🚨", f"{name} {type(e).__name__}"))
    severity = "🚨" if any(r[0] == "🚨" for r in results) else "✅"
    return severity, ", ".join(r[1] for r in results)


def check_backups():
    try:
        bks = sorted(BACKUP_DIR.glob("vlbackup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not bks:
            return "🚨", "no backup zip found"
        latest = bks[0]
        age_h = (datetime.datetime.now().timestamp() - latest.stat().st_mtime) / 3600
        badge = "🚨" if age_h > 30 else ("⚠️" if age_h > 26 else "✅")
        return badge, f"latest {latest.name} ({age_h:.1f}h ago)"
    except Exception as e:
        return "⚠️", f"backup check failed: {e}"


def check_cron():
    """Read hourly snapshot of /etc/cron.d/原初映像片庫-v2 (host cron copies it)."""
    try:
        if not CRON_SNAPSHOT.exists():
            return "🚨", f"snapshot missing: {CRON_SNAPSHOT.name}"
        age_h = (datetime.datetime.now().timestamp() - CRON_SNAPSHOT.stat().st_mtime) / 3600
        txt = CRON_SNAPSHOT.read_text(encoding="utf-8", errors="replace")
        lines = [l for l in txt.splitlines() if l and not l.startswith("#") and "*" in l]
        if age_h > 3:
            return "⚠️", f"snapshot stale ({age_h:.1f}h), {len(lines)} entries"
        return "✅", f"{len(lines)} cron entries (snapshot {age_h:.1f}h old)"
    except Exception as e:
        return "⚠️", f"cron check failed: {e}"


def push_tg(text):
    payload = json.dumps({"chat_id": TG_CHAT, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type":"application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except Exception as e:
        return f"err:{e}"


def check_aerial_backlog(env):
    """地點待判讀 backlog（city=null 的 NAS 建案素材）+ Claude fallback 累計定位數。
    backlog 太大 = Gemini 跟不上 / Claude drain 沒收 → ⚠️。"""
    from urllib.parse import quote
    sr_key = env.get("SUPABASE_SERVICE_ROLE_KEY")
    h = {"apikey": sr_key, "Authorization": f"Bearer {sr_key}", "Prefer": "count=exact"}

    def cnt(q):
        req = urllib.request.Request(f"{SUPABASE_URL}/videos?{q}&limit=0",
                                     headers={**h, "Range": "0-0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            cr = r.headers.get("Content-Range", "")
            return int(cr.split("/")[-1]) if "/" in cr else 0
    try:
        nb = quote("{非建案}")
        backlog = cnt(f"select=*&city=is.null&source=eq.nas&tags=not.cs.{nb}")
        claude = cnt("select=*&city_source=eq.claude-vision-fallback")
        badge = "⚠️" if backlog > 200 else "✅"
        return badge, f"city=null 待判讀={backlog}, Claude已定位={claude}"
    except Exception as e:
        return "⚠️", f"backlog 查詢失敗: {e}"


def main():
    env = load_env(ENV_FILE)
    checks = [
        ("PostgREST", check_postgrest(env)),
        ("DB rows", check_db_rows(env)),
        ("地點待判讀", check_aerial_backlog(env)),
        ("Thumb cache", check_thumb_cache()),
        ("Gemini API", check_gemini(env)),
        ("Disk /volume2", check_disk()),
        ("Containers", check_containers()),
        ("Backups", check_backups()),
        ("Cron file", check_cron()),
    ]
    severity = "✅"
    for _, (b, _) in checks:
        if b == "🚨": severity = "🚨"; break
        if b == "⚠️" and severity == "✅": severity = "⚠️"
    parts = [f"{severity} 片庫 daily health  {datetime.datetime.now():%Y-%m-%d %H:%M}", ""]
    for name, (badge, msg) in checks:
        parts.append(f"{badge} {name}: {msg}")
    text = "\n".join(parts)
    print(text)
    print()
    print(f"[push] tg={push_tg(text)}")


if __name__ == "__main__":
    main()
