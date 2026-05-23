"""
為 videos 表補 city / region 資訊，讓搜尋「台中」能跳出勤美之真等案件。

兩階段：
1) 先用 regex 從 case_folder 尾綴抽 `_台中西區` 之類的地點（多數琦郁的案子有帶）
2) 剩下無法抽出的案名，批次送 Gemini 回答「台灣哪個縣市 / 區域」

結果寫入：videos.city, videos.region, 並把 city/region 併入 search_text。
同時產 case_locations.json 作為快取，下次跑會跳過已有的案名。
"""
from __future__ import annotations
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from supabase import create_client

ROOT = Path(r"/volume2/docker-prod/scripts/原初映像片庫")
ENV = ROOT / ".env"
CACHE = ROOT / "case_locations.json"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TW_CITIES = [
    "台北", "臺北", "新北", "桃園", "新竹", "苗栗", "台中", "臺中", "彰化",
    "南投", "雲林", "嘉義", "台南", "臺南", "高雄", "屏東", "宜蘭",
    "花蓮", "台東", "臺東", "基隆", "澎湖", "金門", "馬祖", "連江",
]
NORMALIZE = {"臺北": "台北", "臺中": "台中", "臺南": "台南", "臺東": "台東"}

# 例："0705勤美之真_台中西區" → region="台中西區" city="台中"
#     "0128_朱琦郁 x 新潤世界都心 直式_新北林口" → region="新北林口" city="新北"
TAIL_RE = re.compile(r"_([\u4e00-\u9fff]{2,10})$")


def load_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def parse_city(tail: str) -> str | None:
    for c in TW_CITIES:
        if tail.startswith(c):
            return NORMALIZE.get(c, c)
    return None


def regex_extract(name: str) -> dict | None:
    m = TAIL_RE.search(name or "")
    if not m:
        return None
    tail = m.group(1)
    city = parse_city(tail)
    if not city:
        return None
    return {"city": city, "region": tail}


def fetch_all_case_folders(client):
    """分頁拉全部 videos 的 case_folder + case_name"""
    all_rows = []
    page = 0
    page_size = 1000
    while True:
        res = client.table("videos").select("case_folder,case_name,channel_name") \
            .range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = res.data or []
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        page += 1
    return all_rows


def build_case_map(rows):
    """以 case_name 為 key，記下代表性的 case_folder / channel 方便 Gemini 判斷"""
    m = {}
    for r in rows:
        cn = (r.get("case_name") or "").strip()
        if not cn:
            continue
        if cn not in m:
            m[cn] = {
                "case_folder": (r.get("case_folder") or "").strip(),
                "channel": (r.get("channel_name") or "").strip(),
            }
    return m


def ask_gemini(unknowns: list) -> dict:
    """
    送給 Gemini 批次問地點。unknowns: list[ {case_folder, case_name, channel} ]
    回傳 { case_folder: {"city": "...", "region": "..."} }
    """
    # 這個函式由 claude 上層呼叫 mcp__gemini__ask_gemini 來實作 — 直接回傳未 tag
    return {}


def save_cache(cache):
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cache():
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def main():
    env = load_env()
    client = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

    print("pulling case names...")
    rows = fetch_all_case_folders(client)
    print(f"total rows: {len(rows)}")

    case_map = build_case_map(rows)
    print(f"distinct case_names: {len(case_map)}")

    cache = load_cache()
    tagged = {}
    need_gemini = []

    for cn, info in case_map.items():
        if cn in cache:
            tagged[cn] = cache[cn]
            continue
        # 依序試 case_name、case_folder 尾
        hit = regex_extract(cn) or regex_extract(info["case_folder"])
        if hit:
            tagged[cn] = hit
            cache[cn] = hit
            continue
        need_gemini.append({"case_name": cn, **info})

    print(f"regex tagged: {len(tagged)}")
    print(f"need gemini: {len(need_gemini)}")

    save_cache(cache)

    pending = ROOT / "pending_regions.json"
    pending.write_text(json.dumps(need_gemini, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"pending list saved -> {pending}")
    print("\n== regex 抽到城市分佈 ==")
    from collections import Counter
    ct = Counter(v["city"] for v in tagged.values())
    for k, v in ct.most_common():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
