"""
每日同步彙整通知。daily_sync.ps1 結尾呼叫，比對前一天狀態，發 Telegram 摘要。

只在「跨日後第一次跑」時發訊息，避免每 2 小時轟炸。
"""
import json
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "sync_state.json"

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = "8635121564"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def supabase_count(env, table, extra_qs=""):
    """用 Range header + Prefer count=exact 取整表 row 數。"""
    base = env["SUPABASE_URL"].rstrip("/")
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    url = f"{base}/rest/v1/{table}?select=drive_file_id"
    if extra_qs:
        url += "&" + extra_qs
    req = urllib.request.Request(url, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Prefer": "count=exact",
        "Range": "0-0",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        cr = r.headers.get("Content-Range", "")
        if "/" in cr:
            return int(cr.split("/")[-1])
    return 0


def count_distinct_case_names(env):
    """數 case_name 不同值有多少。Supabase 沒有 distinct，用 RPC 或自己拉。"""
    # 簡化：拉所有 case_name（最多 50k 列），用 set 計算
    base = env["SUPABASE_URL"].rstrip("/")
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    cases = set()
    offset = 0
    PAGE = 1000
    while True:
        url = f"{base}/rest/v1/videos?select=case_name"
        req = urllib.request.Request(url, headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Range": f"{offset}-{offset + PAGE - 1}",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        if not data:
            break
        for row in data:
            cn = row.get("case_name")
            if cn:
                cases.add(cn)
        if len(data) < PAGE:
            break
        offset += PAGE
    return len(cases)


def file_count(path: Path):
    if not path.exists():
        return 0
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return len(d) if isinstance(d, list) else 0
    except Exception:
        return 0


def send_tg(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TG_CHAT, "text": text}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"TG 失敗: {e}", flush=True)


def main():
    today = date.today().isoformat()
    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    # 跨日才發訊息（同一天多次跑只更新狀態，不重複通知）
    last_summary = prev.get("last_summary_date", "")
    is_first_today = last_summary != today

    env = load_env()
    now = {
        "ts": datetime.now().isoformat(),
        "videos": supabase_count(env, "videos"),
        "videos_with_city": supabase_count(env, "videos", "city=not.is.null"),
        "case_names": count_distinct_case_names(env),
        "unknown": file_count(ROOT / "locations_unknown.json"),
    }

    if not is_first_today:
        # 同一天，只更新狀態（不發訊息）
        prev.update({k: v for k, v in now.items() if k != "ts"})
        prev["ts"] = now["ts"]
        STATE.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{datetime.now():%H:%M}] 同一天，靜默更新 state", flush=True)
        return

    # 跨日：算 delta + 發訊息
    def delta(key):
        return now[key] - prev.get(key, now[key])

    dv = delta("videos")
    dc = delta("case_names")
    dl = delta("videos_with_city")
    du = delta("unknown")

    last_str = prev.get("last_summary_date", "首次紀錄")
    msg = (
        f"🔄 片庫每日同步彙整\n"
        f"（自 {last_str} 到 {today}）\n"
        f"\n"
        f"📹 影片：{now['videos']:,} ({dv:+,})\n"
        f"📂 案件：{now['case_names']:,} ({dc:+,})\n"
        f"📍 已標地點影片：{now['videos_with_city']:,} ({dl:+,})\n"
        f"❓ 待補建案：{now['unknown']:,} ({du:+,})"
    )

    if dv == 0 and dc == 0 and dl == 0 and du == 0:
        # 真的沒變，跳過通知
        print(f"[{datetime.now():%H:%M}] 跨日但無變動，跳過 TG", flush=True)
    else:
        send_tg(msg)
        print(msg, flush=True)

    now["last_summary_date"] = today
    STATE.write_text(json.dumps(now, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        send_tg(f"⚠️ sync_summary 失敗：{e!r}\n{traceback.format_exc()[-400:]}")
        raise
