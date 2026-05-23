"""
從 Google Sheet 抓本週拍攝行程，配對 city/district，upsert 到 Supabase weekly_schedule。
給 daily_sync.ps1 排程用。
"""
import re
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

from google.oauth2 import service_account
import os
SA_PATH = (
    r"C:\Users\rendy\service_account.json" if os.name == "nt"
    else "/volume2/docker-prod/scripts/原初映像片庫/service_account.json"
)
from googleapiclient.discovery import build
from supabase import create_client

ROOT = Path(__file__).parent
ENV = ROOT / ".env"
TOKEN_PATH = str(Path(__file__).parent / "token.json")
SPREADSHEET_ID = "1_naCZzjQ3G7W28RyaRe-ZuTEsr-sjeTBBf912pAJB-M"
SCOPES = ["https://www.googleapis.com/auth/drive"]

# 行程顯示範圍：今天起算 7 天（含今天）
WINDOW_DAYS = 7

EDITOR_TAGS = ["Jia", "jia", "JIA", "承佳", "雅", "雅憶", "康", "P", "p", "賢",
               "夏", "夏子", "耀陽", "葉", "葉子", "瑞", "安", "阿國", "喜華"]
EXCLUDE_KEYWORDS = ["出國", "自己案子", "家裡有事", "下雨延", "喜華不行",
                    "休假", "請假", "不在"]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def get_creds():
    creds = service_account.Credentials.from_service_account_file(SA_PATH, scopes=SCOPES)
    return creds


def pick_recent_sheet(sheets_service):
    """挑出最新的「YYYY年M月」或「YYYY年」分頁（年份大、月份大優先）。"""
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    cands = []
    for s in meta["sheets"]:
        title = s["properties"]["title"]
        m1 = re.match(r"^(\d{4})年(\d{1,2})月$", title)
        m2 = re.match(r"^(\d{4})年$", title)
        if m1:
            cands.append((int(m1.group(1)), int(m1.group(2)), title))
        elif m2:
            cands.append((int(m2.group(1)), 13, title))  # 純年份視為「最末月」
    cands.sort(reverse=True)
    if not cands:
        raise RuntimeError("找不到符合命名的分頁")
    return cands[0][2], (cands[0][0], cands[0][1])


def is_date_cell(t):
    if not isinstance(t, str):
        return False
    return bool(re.match(r"^\d{1,2}/\d{1,2}", t.strip())) and len(t.strip()) <= 10


def extract_editor(text):
    if not isinstance(text, str) or not text.strip():
        return None
    m = re.search(r"[（(]([^）)\n]+)[）)]", text)
    if not m:
        return None
    inside = m.group(1).strip()
    for tag in EDITOR_TAGS:
        if tag in inside:
            return tag
    return None


def remove_editor_tag(text):
    return re.sub(r"\s*[（(][^）)\n]+[）)]\s*$", "", text).strip()


# 排在案名末尾要清掉的雜訊詞：拍攝類型、機數、後綴
TRAILING_NOISE = [
    "雲端連結", "拍一天剪2短", "拍一天", "剪2短",
    "即賞屋雙機", "即賞屋單機", "即賞屋",
    "短影音", "短影",
    "街訪", "即新聞",
    "雙機", "單機",
    "女子",
    "琦郁",
]

# 開頭常見的主持人/品牌前綴（與案名無關）
LEADING_NOISE = ["琦郁", "Amber", "海蒂", "Lavina外", "Lavina", "Ivy外", "Ivy"]


# 出現在拍攝描述中的「邊界詞」：找到第一個就只保留前面
BOUNDARY_WORDS = [
    "短影音", "短影",
    "即賞屋", "街訪", "即新聞",
    "拍一天",
    "雙機", "單機",
    "女子",
]


def clean_case_name(text):
    """從排程原始字串萃取乾淨可搜尋的案名。"""
    if not text:
        return text
    s = text
    # 去除「\n雲端連結」之類整段尾巴
    s = re.split(r"[\n\r]", s, maxsplit=1)[0].strip()
    # 去除剪輯人 paren
    s = re.sub(r"\s*[（(][^）)]+[）)]\s*", " ", s).strip()
    # 剝除開頭主持人前綴（先剝，才能露出日期前綴）
    for w in LEADING_NOISE:
        if s.startswith(w):
            s = s[len(w):].lstrip(" 、_-")
    # 去除開頭日期前綴：4/21 / 04/21 / 0422 / 0307 等
    s = re.sub(r"^\s*\d{1,2}\s*[/／]\s*\d{1,2}\s*", "", s)
    s = re.sub(r"^\s*\d{3,4}\s*", "", s)
    # 找到第一個邊界詞，只保留前面部分
    cut = len(s)
    for w in BOUNDARY_WORDS:
        i = s.find(w)
        if i >= 0 and i < cut:
            cut = i
    s = s[:cut].strip()
    # 去除尾端 *3、*數字
    s = re.sub(r"\s*\*\s*\d+\s*$", "", s)
    # 去除尾端雜訊詞（再保險一次）
    changed = True
    while changed:
        changed = False
        for w in TRAILING_NOISE:
            if s.endswith(w):
                s = s[: -len(w)].rstrip(" 、_-")
                changed = True
    # 多餘空白歸一
    s = re.sub(r"\s+", " ", s).strip()
    return s


def search_keys(name):
    """產生多個由強到弱的搜尋字串，逐一嘗試。"""
    if not name:
        return []
    keys = []
    # 0: 原本清理後的全名
    keys.append(name)
    # 1: 切掉 _xxx 副標
    head = re.split(r"[_\-－]", name, maxsplit=1)[0].strip()
    if head and head != name:
        keys.append(head)
    # 2: 剝除尾端「第N篇/段/集」等
    stripped = re.sub(r"\s*第[一二三四五六七八九十百\d]+(篇|段|集)\s*$", "", head).strip()
    if stripped and stripped != head:
        keys.append(stripped)
    # 3: 取連續中文（含英數）block
    m = re.match(r"^([一-鿿]+)", stripped or head)
    if m:
        zh = m.group(1)
        if len(zh) >= 2 and zh not in keys:
            keys.append(zh)
    return keys


def normalize_for_match(s):
    """比對時正規化：去掉所有空白並轉小寫。"""
    if not s:
        return ""
    return re.sub(r"\s+", "", s).lower()


def parse_grid(grid, base_ym):
    """從 sheet grid 抽出所有 (datetime, case_name, editor) 記錄。"""
    if not grid:
        return []

    max_cols = max((len(r) for r in grid), default=0)
    for row in grid:
        while len(row) < max_cols:
            row.append("")

    date_row_indices = [i for i, row in enumerate(grid)
                        if sum(1 for t in row if is_date_cell(t)) >= 3]
    if not date_row_indices:
        return []

    def week_date_cols(row):
        cols = [(ci, t.strip()) for ci, t in enumerate(row) if is_date_cell(t)]
        return cols[:7]

    # 純年份分頁：往回推首段年份
    total_rollovers = 0
    prev_mo = None
    for dr in date_row_indices:
        for _, ds in week_date_cols(grid[dr]):
            try:
                mo = int(ds.split("/")[0])
            except Exception:
                continue
            if prev_mo is not None and mo < prev_mo:
                total_rollovers += 1
            prev_mo = mo
    running_year = base_ym[0] - total_rollovers if base_ym[1] == 13 else base_ym[0]

    cell_dates = {}
    prev_mo = None
    for dr in date_row_indices:
        for col_i, ds in week_date_cols(grid[dr]):
            try:
                mo, da = map(int, ds.split("/"))
            except Exception:
                continue
            if prev_mo is not None and mo < prev_mo:
                running_year += 1
            try:
                cell_dates[(dr, col_i)] = datetime(running_year, mo, da)
            except ValueError:
                pass
            prev_mo = mo

    records = []
    seen = set()
    for dr in date_row_indices:
        date_cols = [ci for ci, _ in week_date_cols(grid[dr])]
        for offset in range(1, 5):
            nx = dr + offset
            if nx >= len(grid):
                break
            row = grid[nx]
            if sum(1 for t in row if is_date_cell(t)) >= 3:
                break
            for col_i in date_cols:
                dt = cell_dates.get((dr, col_i))
                if dt is None or col_i >= len(row):
                    continue
                cell_text = row[col_i]
                if not cell_text or cell_text == "nan":
                    continue
                if any(kw in cell_text for kw in EXCLUDE_KEYWORDS):
                    continue
                editor = extract_editor(cell_text)
                if not editor:
                    continue
                case_name = clean_case_name(remove_editor_tag(cell_text))
                if not case_name:
                    continue
                key = (dt.date(), case_name[:30], editor)
                if key in seen:
                    continue
                seen.add(key)
                records.append({
                    "date": dt.date(),
                    "case_name": case_name,
                    "editor": editor,
                })
    return records


def fetch_grid(creds, sheet_name):
    svc = build("sheets", "v4", credentials=creds)
    res = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=sheet_name,
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return res.get("values", [])


def lookup_city_district(client, case_name, all_video_cases=None, manual_map=None):
    """從 case_locations 優先 / manual_locations / videos 找該案件 city/district。
    all_video_cases: list of (case_name, city, district)，用來做正規化比對 fallback。
    manual_map: dict[case_name -> {city, district}]，沒影片的新案件用（來自 manual_locations.json）。
    """
    if not case_name:
        return None, None

    from collections import Counter

    # 最高優先：case_locations table（admin/locations UI 維護，比 manual_locations.json 新）
    try:
        cl = client.table("case_locations").select("city,district,is_non_building") \
            .eq("case_name", case_name).maybeSingle().execute()
        d = cl.data
        if d:
            if d.get("is_non_building"):
                return None, None
            if d.get("city"):
                return d.get("city"), d.get("district") or ""
    except Exception:
        pass

    # 次優先：manual_locations.json 精確 match（避免 ilike 模糊比對撈到同前綴的別案）
    if manual_map:
        if case_name in manual_map:
            v = manual_map[case_name]
            if v.get("city"):
                return v.get("city"), v.get("district") or ""
        norm = normalize_for_match(case_name)
        if len(norm) >= 3:
            for k, v in manual_map.items():
                if normalize_for_match(k) == norm and v.get("city"):
                    return v.get("city"), v.get("district") or ""

    # 多級 fallback：用 search_keys 產生候選 key，逐一試 ilike
    for key in search_keys(case_name):
        if not key or len(key) < 2:
            continue
        # ilike 模糊比對
        res = client.table("videos").select("city,district") \
            .ilike("case_name", f"%{key}%").not_.is_("city", "null").limit(20).execute()
        rows = res.data or []
        if rows:
            pairs = Counter((r.get("city"), r.get("district") or "") for r in rows if r.get("city"))
            if pairs:
                (city, district), _ = pairs.most_common(1)[0]
                return city, district

    # Fallback: 用正規化（去空白、轉小寫）在記憶體裡比對
    if all_video_cases:
        norm = normalize_for_match(case_name)
        if len(norm) >= 3:
            matches = []
            for vc, city, district in all_video_cases:
                vn = normalize_for_match(vc)
                if not vn or len(vn) < 3:
                    continue
                if norm in vn or vn in norm:
                    matches.append((city, district or ""))
            if matches:
                pairs = Counter(matches)
                (city, district), _ = pairs.most_common(1)[0]
                return city, district

    # 最後 fallback: manual_locations.json（新案件還沒有影片，用腳本內文抽出來的）
    if manual_map:
        # 精確
        if case_name in manual_map:
            v = manual_map[case_name]
            return v.get("city"), v.get("district") or ""
        # 正規化
        norm = normalize_for_match(case_name)
        if len(norm) >= 3:
            for k, v in manual_map.items():
                if normalize_for_match(k) == norm:
                    return v.get("city"), v.get("district") or ""
            for k, v in manual_map.items():
                kn = normalize_for_match(k)
                if kn and len(kn) >= 3 and (norm in kn or kn in norm):
                    return v.get("city"), v.get("district") or ""

    return None, None


def fetch_all_video_cases(client):
    """一次拉所有有 city 的案名，給正規化 fallback 用。"""
    seen = {}
    page_size = 1000
    offset = 0
    while True:
        res = client.table("videos").select("case_name,city,district") \
            .not_.is_("city", "null").not_.is_("case_name", "null") \
            .range(offset, offset + page_size - 1).execute()
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            cn = r.get("case_name")
            if cn and cn not in seen:
                seen[cn] = (r.get("city"), r.get("district"))
        if len(rows) < page_size:
            break
        offset += page_size
    return [(cn, c, d) for cn, (c, d) in seen.items()]


def main():
    env = load_env()
    client = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

    creds = get_creds()
    svc = build("sheets", "v4", credentials=creds)
    sheet_name, base_ym = pick_recent_sheet(svc)
    print(f"分頁：{sheet_name}（base_ym={base_ym}）")

    grid = fetch_grid(creds, sheet_name)
    records = parse_grid(grid, base_ym)
    print(f"全分頁解析出 {len(records)} 筆")

    today = datetime.now(timezone(timedelta(hours=8))).date()
    end = today + timedelta(days=WINDOW_DAYS - 1)
    week = [r for r in records if today <= r["date"] <= end]
    print(f"本週（{today} ~ {end}）{len(week)} 筆")

    # 預載所有有 city 的案名，給 fallback 比對
    all_cases = fetch_all_video_cases(client)
    print(f"預載 {len(all_cases)} 個有 city 的案名")

    # 載 manual_locations.json（沒影片的新案件 fallback）
    import json as _json
    manual_path = ROOT / "manual_locations.json"
    manual_map = {}
    if manual_path.exists():
        manual_map = _json.loads(manual_path.read_text(encoding="utf-8"))
    print(f"manual_locations 共 {len(manual_map)} 筆")

    # 配 city/district
    rows = []
    for r in week:
        city, district = lookup_city_district(client, r["case_name"], all_cases, manual_map)
        rows.append({
            "date": r["date"].isoformat(),
            "case_name": r["case_name"],
            "editor": r["editor"],
            "city": city,
            "district": district,
        })

    # 清掉所有舊資料（含本週前一次跑的髒資料），然後 insert
    client.table("weekly_schedule").delete().gte("date", "1900-01-01").execute()
    if rows:
        client.table("weekly_schedule").insert(rows).execute()
    print(f"upsert {len(rows)} 筆，配到 city: {sum(1 for r in rows if r['city'])}")


if __name__ == "__main__":
    main()
