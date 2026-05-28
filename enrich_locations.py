"""
批次用 Gemini API 查 to_enrich.json 裡的建案地點，寫入 manual_locations.json，
然後跑 extract_locations.py phase1 + apply 寫回 Supabase，最後發 Telegram 通知。

獨立腳本（不靠 MCP），給 Windows Task Scheduler 排程用。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent

# Supabase（讀寫 case_locations table）
try:
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv(ROOT / ".env")
    _SB = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
except Exception as _e:
    _SB = None
    print(f"[warn] supabase 不可用: {_e}", flush=True)
# 改為讀 locations_unknown.json：這份由 daily_sync 內的 extract_locations.py phase1
# 每 2 小時重新計算，永遠是當下最新的「regex 找不到地點」清單。
TO_ENRICH = ROOT / "locations_unknown.json"
MANUAL = ROOT / "manual_locations.json"
# 連續被 Gemini 判 null 的紀錄，達 NULL_SKIP_THRESHOLD 次後不再呼叫 Gemini
NULL_HISTORY = ROOT / "null_history.json"
NULL_SKIP_THRESHOLD = 3
LOG = ROOT / "logs" / f"enrich_{date.today():%Y%m%d}.log"

GEMINI_API_KEY = "AIzaSyB2iMKzNJKyQL1kPzSQidONswGWYx9lRaE"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"

TG_TOKEN = "8583367633:AAFjQyLGLvYrWOZtOrtWm_vpaVpq_pXWBhY"
TG_CHAT_ID = "8635121564"

CITIES = {
    "台北", "新北", "桃園", "新竹", "苗栗", "台中", "彰化", "南投", "雲林",
    "嘉義", "台南", "高雄", "屏東", "宜蘭", "花蓮", "台東", "基隆",
    "澎湖", "金門", "連江",
}

BATCH_SIZE = 15
TAG = f"gemini-batch-{date.today():%Y-%m-%d}"

# 影片可能存在 Drive (Z:/房產) 或 NAS（多 volume）。
# 同一個 channel 名稱在多處可能都有 → 不能靠 prefix 判斷，逐一試。
from nas_roots import ALL_ROOTS as _NAS_ROOTS  # noqa: E402

CANDIDATE_ROOTS = [
    Path("Z:/房產"),                    # PC: Drive Desktop mount
] + [
    Path(p) for r in _NAS_ROOTS for p in (r.win, r.linux, r.docker)
]


def find_finals(sample_path: str, max_results: int = 3):
    """從案件資料夾找出含 Final 的腳本檔名（最多 max_results 個）。
    案件資料夾 = sample_path 倒數第二段以上、首個確實存在的目錄。
    一旦找到該層 dir 就 commit，不再往上爬，避免抓到兄弟案件的 Final。
    """
    if not sample_path:
        return []
    parts = sample_path.split("/")
    for root in CANDIDATE_ROOTS:
        if not root.exists():
            continue
        for depth in range(len(parts) - 2, 0, -1):
            case_dir = root.joinpath(*parts[:depth + 1])
            if case_dir.is_dir():
                finals = []
                try:
                    for p in case_dir.rglob("*"):
                        # 跳過 symlink / bind mount，避免 v1 case 透過 union 走進 v2 重複處理
                        if p.is_symlink():
                            continue
                        if "final" in p.name.lower() and p.is_file():
                            finals.append(p.name)
                            if len(finals) >= max_results:
                                break
                except Exception:
                    pass
                return finals
    return []

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def call_gemini(prompt: str, retries=5) -> str:
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 32768},
    }).encode("utf-8")
    req = urllib.request.Request(
        GEMINI_URL, data=body,
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                # 解析 retryDelay
                m = re.search(r'"retryDelay":\s*"(\d+)s"', err)
                wait = int(m.group(1)) + 5 if m else 60
                log(f"  429, retry in {wait}s ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini {e.code}: {err[:300]}")
    raise RuntimeError("Gemini 重試上限")


def build_prompt(batch):
    lines = []
    for i, d in enumerate(batch):
        line = f"{i+1}. {d['case_name']} | 上層={d['case_folder']} | 頻道={d['channel']}"
        finals = find_finals(d.get("sample_path", ""))
        if finals:
            line += "\n   Final 腳本: " + " / ".join(finals)
        lines.append(line)
    return f"""這些是台灣不動產影音公司的影片案名，請查每個建案位於台灣哪個縣市哪個行政區。

**只接受真實存在的台灣建案**。如果是：
- 產品名（Dyson、三星、防蚊液、寵物食品、家電、保健品）→ city: null
- Vlog / 旅遊（杜拜、東京等）→ city: null
- 編號、日期、純數字、節目排行（TOP31、20260408）→ city: null
- 安娜馭房術的「短影」「房感」「看屋筆記」系列節目 → city: null
- KOL 業配（檔名含 "x KOL名"、"街訪突即隊"、"短影音腳本"）→ city: null
- 不確定是不是真實建案 → city: null（**寧願漏，不要捏造**）

**Final 腳本提示**：每筆若帶有「Final 腳本」這行，是同層腳本資料夾內 Final 版的檔名。檔名通常透露真實主題：
- 含「X 街訪突即隊」「KOL熊熊」「短影音」「腳本大綱」等 → 業配/節目，不是建案 → null
- 含具體建案名 + 區域 → 用該資訊填 city/district
- Final 檔名與案名差距大時，以 Final 檔名為準（案名常常只是日期或代號）

確定是建案才填 city + district。

**回傳嚴格 JSON**（用案名當 key，不要其他文字、不要 markdown code fence）：
{{
  "原始案名1": {{"city": "台中", "district": "西區"}},
  "原始案名2": {{"city": null, "district": null}}
}}

city 必須是以下其中之一（或 null）：台北、新北、桃園、新竹、苗栗、台中、彰化、南投、雲林、嘉義、台南、高雄、屏東、宜蘭、花蓮、台東、基隆、澎湖、金門、連江

案名清單：
{chr(10).join(lines)}
"""


def parse_response(text: str) -> dict:
    """從 Gemini 回應抽 JSON（可能被 markdown code fence 包起來）"""
    text = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        m = re.search(r'(\{.*\})', text, re.DOTALL)
        if m:
            text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log(f"  parse 失敗: {e}; raw[:200]={text[:200]}")
        return {}


def validate_entry(v):
    if not isinstance(v, dict):
        return None
    city = v.get("city")
    district = v.get("district") or ""
    if city is None or city == "":
        return None
    if city not in CITIES:
        return None
    return {"city": city, "district": district, "manual": True, "auto": TAG}


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log(f"Telegram 發送失敗: {e}")


def load_case_locations():
    """從 Supabase case_locations 抓所有條目，回 dict: case_name -> {city, district, is_non_building}"""
    if not _SB:
        return {}
    try:
        r = _SB.table("case_locations").select("case_name,city,district,is_non_building").execute()
        return {x["case_name"]: x for x in (r.data or [])}
    except Exception as e:
        log(f"[warn] case_locations 讀取失敗: {e}")
        return {}


def upsert_case_location(case_name, city, district, source_tag):
    """寫一筆到 case_locations，跟 manual_locations.json 平行同步。"""
    if not _SB:
        return
    try:
        _SB.table("case_locations").upsert({
            "case_name": case_name,
            "city": city,
            "district": district,
            "is_non_building": city is None,
            "reason": None,
            "source": source_tag,
            "updated_by": "enrich_locations.py",
        }).execute()
    except Exception as e:
        log(f"[warn] case_locations upsert {case_name!r} 失敗: {e}")


def main():
    log("=== enrich_locations 開始 ===")

    candidates = json.loads(TO_ENRICH.read_text(encoding="utf-8"))
    manual = json.loads(MANUAL.read_text(encoding="utf-8")) if MANUAL.exists() else {}
    null_hist = json.loads(NULL_HISTORY.read_text(encoding="utf-8")) if NULL_HISTORY.exists() else {}
    case_loc_table = load_case_locations()
    log(f"case_locations table: {len(case_loc_table)} 筆")

    # 排除規則（任一命中就跳過）：
    # 1. 已在 manual_locations.json
    # 2. 已在 case_locations table（不論 has city 或 is_non_building，都代表 admin/UI 已決定）
    # 3. 連續 NULL_SKIP_THRESHOLD 次被 Gemini 判 null
    todo = []
    perm_skipped = 0
    skipped_by_table = 0
    for c in candidates:
        cn = c["case_name"]
        if cn in manual:
            continue
        if cn in case_loc_table:
            skipped_by_table += 1
            continue
        if null_hist.get(cn, {}).get("count", 0) >= NULL_SKIP_THRESHOLD:
            perm_skipped += 1
            continue
        todo.append(c)
    log(
        f"待查 {len(todo)} 筆"
        f"（總候選 {len(candidates)}，已在 manual {sum(1 for c in candidates if c['case_name'] in manual)}，"
        f"已在 case_locations {skipped_by_table}，永久略過 {perm_skipped}）"
    )

    if not todo:
        log("沒有要處理的，結束")
        send_telegram(
            f"片庫地點補資料：沒有新案件需處理\n"
            f"（總候選 {len(candidates)}，永久略過 {perm_skipped}）"
        )
        return

    added = 0
    skipped = 0
    failed_batches = 0
    today_iso = date.today().isoformat()
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i+BATCH_SIZE]
        log(f"批次 {i//BATCH_SIZE + 1}/{(len(todo)-1)//BATCH_SIZE + 1}（{len(batch)} 筆）")
        try:
            resp = call_gemini(build_prompt(batch))
        except Exception as e:
            log(f"  失敗: {e}")
            failed_batches += 1
            continue
        parsed = parse_response(resp)
        for d in batch:
            cn = d["case_name"]
            v = parsed.get(cn)
            entry = validate_entry(v) if v else None
            if entry:
                manual[cn] = entry
                added += 1
                null_hist.pop(cn, None)
                # 同步寫入 case_locations table
                upsert_case_location(cn, entry["city"], entry["district"] or None, TAG)
            else:
                skipped += 1
                e = null_hist.get(cn, {"count": 0})
                e["count"] = e.get("count", 0) + 1
                e["last_null"] = today_iso
                null_hist[cn] = e
                # 達門檻轉永久略過時，也標進 case_locations 為 non_building
                if e["count"] >= NULL_SKIP_THRESHOLD:
                    upsert_case_location(cn, None, None, f"gemini-null-x{e['count']}")
        # 每批存檔，怕中途掛
        MANUAL.write_text(json.dumps(manual, ensure_ascii=False, indent=2), encoding="utf-8")
        NULL_HISTORY.write_text(json.dumps(null_hist, ensure_ascii=False, indent=2), encoding="utf-8")
        # 防 quota：每批間隔 5 秒
        time.sleep(5)

    newly_perm = sum(1 for cn, e in null_hist.items() if e.get("count", 0) == NULL_SKIP_THRESHOLD and e.get("last_null") == today_iso)
    log(f"完成：新增 {added}，無法判定/略過 {skipped}（其中 {newly_perm} 筆達門檻轉永久略過），失敗批次 {failed_batches}")

    # 跑 phase1 + apply
    log("跑 extract_locations.py phase1")
    p1 = subprocess.run([sys.executable, "extract_locations.py", "phase1"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    log(p1.stdout[-500:] if p1.stdout else "(無輸出)")
    if p1.returncode != 0:
        log(f"phase1 失敗: {p1.stderr[-500:]}")

    log("跑 extract_locations.py apply")
    ap = subprocess.run([sys.executable, "extract_locations.py", "apply"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    log(ap.stdout[-500:] if ap.stdout else "(無輸出)")
    if ap.returncode != 0:
        log(f"apply 失敗: {ap.stderr[-500:]}")

    # Telegram 通知
    msg = (
        f"📍 片庫地點批次補資料完成\n"
        f"新增：{added} 筆建案\n"
        f"無法判定：{skipped} 筆（其中 {newly_perm} 筆達 {NULL_SKIP_THRESHOLD} 次門檻轉永久略過）\n"
        f"永久略過池：{perm_skipped + newly_perm} 筆（已不再呼叫 Gemini）\n"
        f"admin UI 已標示略過：{skipped_by_table} 筆\n"
        f"失敗批次：{failed_batches}\n"
        f"已寫回 Supabase（apply 結果見 logs/）"
    )
    send_telegram(msg)
    log("=== 結束 ===")


if __name__ == "__main__":
    main()
