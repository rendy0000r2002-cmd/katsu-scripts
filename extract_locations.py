"""
為 videos 表抽取 city + district。
Phase 1: regex 從 case_folder / case_name 尾綴 `_城市行政區` 抽
Phase 2: 剩下的寫到 unknowns.json，交給外部（WebSearch）查完再批次回填

用法：
  python extract_locations.py phase1      # 跑 regex，產 known.json + unknowns.json
  python extract_locations.py apply       # 讀 known.json（含後續補的），UPDATE DB
"""
from __future__ import annotations
import json
import os
import re
import sys


def _write_atomic(path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
from collections import defaultdict
from pathlib import Path

from supabase import create_client

ROOT = Path(__file__).parent
ENV = ROOT / ".env"
KNOWN = ROOT / "locations_known.json"
UNKNOWN = ROOT / "locations_unknown.json"
MANUAL = ROOT / "manual_locations.json"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 台灣 22 直轄市/縣市
CITIES = [
    "台北", "臺北", "新北", "桃園", "新竹市", "新竹縣", "新竹",
    "苗栗", "台中", "臺中", "彰化", "南投", "雲林", "嘉義市", "嘉義縣", "嘉義",
    "台南", "臺南", "高雄", "屏東", "宜蘭",
    "花蓮", "台東", "臺東", "基隆", "澎湖", "金門", "馬祖", "連江",
]
NORMALIZE_CITY = {
    "臺北": "台北", "臺中": "台中", "臺南": "台南", "臺東": "台東",
    "新竹市": "新竹", "新竹縣": "新竹",
    "嘉義市": "嘉義", "嘉義縣": "嘉義",
}

# 台北區：中正/大同/中山/松山/大安/萬華/信義/士林/北投/內湖/南港/文山
# 新北區：板橋/三重/中和/永和/新莊/新店/土城/蘆洲/汐止/樹林/鶯歌/三峽/淡水/汐止/瑞芳/林口/八里/五股/泰山/深坑/石碇/坪林/烏來/平溪/雙溪/貢寮/金山/萬里/三芝/石門
# 桃園區：桃園/中壢/平鎮/八德/楊梅/蘆竹/大溪/龍潭/龜山/大園/觀音/新屋/復興
# 台中區：中區/東區/西區/南區/北區/北屯/西屯/南屯/太平/大里/霧峰/烏日/豐原/后里/石岡/東勢/和平/新社/潭子/大雅/神岡/大肚/沙鹿/龍井/梧棲/清水/大甲/外埔/大安
DISTRICTS_BY_CITY = {
    "台北": ["中正", "大同", "中山", "松山", "大安", "萬華", "信義", "士林", "北投", "內湖", "南港", "文山"],
    "新北": ["板橋", "三重", "中和", "永和", "新莊", "新店", "土城", "蘆洲", "汐止", "樹林", "鶯歌", "三峽", "淡水",
             "瑞芳", "林口", "八里", "五股", "泰山", "深坑", "石碇", "坪林", "烏來", "平溪", "雙溪", "貢寮",
             "金山", "萬里", "三芝", "石門"],
    "桃園": ["桃園區", "桃園", "中壢", "平鎮", "八德", "楊梅", "蘆竹", "大溪", "龍潭", "龜山", "大園", "觀音", "新屋", "復興"],
    "新竹": ["東區", "北區", "香山", "竹北", "湖口", "新豐", "竹東", "寶山", "芎林", "橫山", "尖石", "北埔", "峨眉", "關西", "新埔", "五峰"],
    "苗栗": ["苗栗", "頭份", "竹南", "後龍", "通霄", "苑裡", "三灣", "南庄", "大湖", "獅潭", "卓蘭", "公館", "銅鑼", "三義", "西湖", "造橋", "頭屋", "泰安"],
    "台中": ["中區", "東區", "西區", "南區", "北區", "北屯", "西屯", "南屯", "太平", "大里", "霧峰", "烏日",
             "豐原", "后里", "石岡", "東勢", "和平", "新社", "潭子", "大雅", "神岡", "大肚", "沙鹿", "龍井",
             "梧棲", "清水", "大甲", "外埔", "大安"],
    "彰化": ["彰化", "和美", "鹿港", "溪湖", "員林", "田中", "北斗", "二林"],
    "南投": ["南投", "埔里", "草屯", "竹山", "集集", "名間", "鹿谷", "中寮", "魚池", "國姓", "水里", "信義", "仁愛"],
    "雲林": ["斗六", "斗南", "虎尾", "西螺", "土庫", "北港", "古坑", "大埤", "莿桐", "林內", "二崙", "崙背", "麥寮"],
    "嘉義": ["東區", "西區", "太保", "朴子", "布袋", "大林", "民雄", "溪口", "新港", "六腳", "東石", "義竹", "鹿草"],
    "台南": ["中西區", "東區", "南區", "北區", "安平", "安南", "永康", "歸仁", "新化", "左鎮", "玉井", "楠西",
             "南化", "仁德", "關廟", "龍崎", "官田", "麻豆", "佳里", "西港", "七股", "將軍", "學甲", "北門",
             "新營", "後壁", "白河", "東山", "六甲", "下營", "柳營", "鹽水", "善化", "大內", "山上", "新市", "安定"],
    "高雄": ["新興", "前金", "苓雅", "鹽埕", "鼓山", "旗津", "前鎮", "三民", "楠梓", "小港", "左營", "仁武",
             "大社", "岡山", "路竹", "阿蓮", "田寮", "燕巢", "橋頭", "梓官", "彌陀", "永安", "湖內", "鳳山",
             "大寮", "林園", "鳥松", "大樹", "旗山", "美濃", "六龜", "內門", "杉林", "甲仙", "桃源", "那瑪夏", "茂林", "茄萣"],
    "屏東": ["屏東", "潮州", "東港", "恆春", "萬丹", "長治", "麟洛", "九如", "里港", "鹽埔", "高樹", "萬巒",
             "內埔", "竹田", "新埤", "枋寮", "新園", "崁頂", "林邊", "南州", "佳冬", "琉球", "車城", "滿州",
             "枋山", "三地門", "霧台", "瑪家", "泰武", "來義", "春日", "獅子", "牡丹"],
    "宜蘭": ["宜蘭", "羅東", "蘇澳", "頭城", "礁溪", "壯圍", "員山", "冬山", "五結", "三星", "大同", "南澳"],
    "花蓮": ["花蓮", "鳳林", "玉里", "新城", "吉安", "壽豐", "光復", "豐濱", "瑞穗", "富里", "秀林", "萬榮", "卓溪"],
    "台東": ["台東", "成功", "關山", "卑南", "鹿野", "池上", "東河", "長濱", "太麻里", "大武", "綠島", "海端", "延平", "金峰", "達仁", "蘭嶼"],
    "基隆": ["中正", "七堵", "暖暖", "仁愛", "中山", "安樂", "信義"],
    "澎湖": ["馬公", "湖西", "白沙", "西嶼", "望安", "七美"],
    "金門": ["金城", "金湖", "金沙", "金寧", "烈嶼", "烏坵"],
    "連江": ["南竿", "北竿", "莒光", "東引"],
}

# 區域粗分（供未來用）
REGION_BY_CITY = {
    "台北": "北部", "新北": "北部", "基隆": "北部", "桃園": "北部", "新竹": "北部", "宜蘭": "北部",
    "苗栗": "中部", "台中": "中部", "彰化": "中部", "南投": "中部", "雲林": "中部",
    "嘉義": "南部", "台南": "南部", "高雄": "南部", "屏東": "南部",
    "花蓮": "東部", "台東": "東部",
    "澎湖": "離島", "金門": "離島", "連江": "離島",
}


def load_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def parse_tail_location(name: str) -> dict | None:
    """嘗試從字串尾綴抽 `_城市行政區`。"""
    if not name:
        return None
    # 找最後一個 _ 或空白或）隔開的片段
    tail = re.split(r"[_\s\)）]+", name.strip())[-1]
    return match_location(tail)


# 中文期數 → 標準化標籤（搜尋以中文字為主）
_PERIOD_NORMALIZE = {
    "1": "一期", "2": "二期", "3": "三期", "4": "四期", "5": "五期",
    "6": "六期", "7": "七期", "8": "八期", "9": "九期", "10": "十期",
    "11": "十一期", "12": "十二期", "13": "十三期", "14": "十四期",
}
_PERIOD_RE = re.compile(
    r"(?:第)?(十四|十三|十二|十一|十|一|二|三|四|五|六|七|八|九|14|13|12|11|10|9|8|7|6|5|4|3|2|1)期"
)
# 直接命名的重劃區關鍵字（無歧義）
_AREA_KEYWORDS = ["水湳", "北士科", "單元二", "單元三"]


def detect_area(candidates: list[str]) -> str | None:
    """從 case_name / case_folder / rel_path segments 中自動偵測重劃區標籤。
    只回傳明確命中的，避免誤判（地標如「歌劇院」「美術館」交給人工標）。"""
    seen: list[str] = []
    for c in candidates:
        if not c:
            continue
        # 期數
        for m in _PERIOD_RE.finditer(c):
            key = m.group(1)
            label = _PERIOD_NORMALIZE.get(key) or (
                key + "期" if key.endswith("期") is False else key
            )
            if label not in seen:
                seen.append(label)
        # 重劃區名
        for kw in _AREA_KEYWORDS:
            if kw in c and kw not in seen:
                seen.append(kw)
    if not seen:
        return None
    return " ".join(seen)


def match_location(text: str) -> dict | None:
    """在一段文字裡找台灣城市+行政區。"""
    if not text:
        return None
    # 先找城市（最長匹配優先）
    for raw_city in sorted(CITIES, key=len, reverse=True):
        if raw_city in text:
            city = NORMALIZE_CITY.get(raw_city, raw_city)
            districts = DISTRICTS_BY_CITY.get(city, [])
            rest = text.split(raw_city, 1)[-1]
            for d in sorted(districts, key=len, reverse=True):
                if d in rest or d in text:
                    return {"city": city, "district": d}
            return {"city": city, "district": ""}
    # 找不到城市時，試單獨的行政區名（如「林口們」）
    # 但只匹配不會歧義的區名（很多「中山」「信義」多個城市都有）
    UNIQUE_DISTRICTS = {
        "林口": "新北", "新莊": "新北", "板橋": "新北", "三重": "新北",
        "土城": "新北", "蘆洲": "新北", "永和": "新北", "中和": "新北", "新店": "新北",
        "樹林": "新北", "鶯歌": "新北", "三峽": "新北", "淡水": "新北",
        "瑞芳": "新北", "八里": "新北", "五股": "新北", "泰山": "新北",
        "汐止": "新北",
        "萬華": "台北", "大安": "台北", "士林": "台北", "北投": "台北",
        "內湖": "台北", "南港": "台北", "文山": "台北", "松山": "台北",
        "中壢": "桃園", "平鎮": "桃園", "八德": "桃園", "楊梅": "桃園",
        "蘆竹": "桃園", "大溪": "桃園", "龍潭": "桃園", "龜山": "桃園",
        "北屯": "台中", "西屯": "台中", "南屯": "台中", "大里": "台中",
        "霧峰": "台中", "烏日": "台中", "豐原": "台中", "沙鹿": "台中",
        "梧棲": "台中", "清水": "台中", "大甲": "台中",
        "安平": "台南", "安南": "台南", "永康": "台南", "歸仁": "台南",
        "麻豆": "台南", "善化": "台南", "新營": "台南",
        "鳳山": "高雄", "楠梓": "高雄", "左營": "高雄", "鼓山": "高雄",
        "前鎮": "高雄", "三民": "高雄", "小港": "高雄",
        "頭份": "苗栗", "竹南": "苗栗",
        "竹北": "新竹", "湖口": "新竹",
        "羅東": "宜蘭", "頭城": "宜蘭", "礁溪": "宜蘭", "蘇澳": "宜蘭",
    }
    for d, city in UNIQUE_DISTRICTS.items():
        if d in text:
            return {"city": city, "district": d}
    # 商圈/重劃區別名 → 實際 (city, district)
    AREA_ALIASES = {
        "水湳": ("台中", "西屯"),  # 水湳經貿園區
        "11期": ("台中", "西屯"),
        "12期": ("台中", "西屯"),
        "14期": ("台中", "南屯"),
        "單元二": ("台中", "西屯"),
        "單元三": ("台中", "南屯"),
        "美術館": ("台中", "西區"),
        "七期": ("台中", "西屯"),
        "北士科": ("台北", "北投"),  # 北投士林科技園區
    }
    for alias, (city, dist) in AREA_ALIASES.items():
        if alias in text:
            return {"city": city, "district": dist}
    return None


def search_location_in_text(s: str) -> dict | None:
    """整段搜尋 city+district，找最前面的命中。"""
    return match_location(s)


def fetch_all(client, table="videos"):
    out = []
    PAGE = 1000
    offs = 0
    while True:
        r = client.table(table).select("drive_file_id,rel_path,channel_name,case_folder,case_name").range(offs, offs + PAGE - 1).execute()
        rows = r.data or []
        out.extend(rows)
        if len(rows) < PAGE:
            break
        offs += PAGE
    return out


def phase1():
    env = load_env()
    client = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    print("fetching all videos...")
    rows = fetch_all(client)
    print(f"total: {len(rows)}")

    # 以 case_name 為去重 key（多支影片共享同案）
    bucket = defaultdict(list)
    for r in rows:
        cn = (r.get("case_name") or "").strip()
        if not cn:
            continue
        bucket[cn].append(r)

    print(f"distinct case_names: {len(bucket)}")

    known = {}
    unknown = []
    for cn, items in bucket.items():
        # 依序嘗試：case_name 尾、case_folder 尾、整段 case_name、整段 case_folder、rel_path
        candidates = [
            cn,
            items[0].get("case_folder") or "",
        ]
        # 從 rel_path 取所有片段
        for rp in [items[0].get("rel_path") or ""]:
            for seg in rp.split("/"):
                candidates.append(seg)
        hit = None
        for c in candidates:
            hit = parse_tail_location(c) or search_location_in_text(c)
            if hit:
                break
        # area 自動偵測（檔名/資料夾含明確重劃區關鍵字才標）
        area = detect_area(candidates)
        if hit:
            if area:
                hit = {**hit, "area": area, "auto_area": True}
            known[cn] = hit
        else:
            if area:
                # 沒抽到 city/district 但有 area → 還是記下（city/district 留 None）
                known[cn] = {"city": None, "district": None, "area": area, "auto_area": True}
            else:
                unknown.append({
                    "case_name": cn,
                    "case_folder": items[0].get("case_folder") or "",
                    "channel": items[0].get("channel_name") or "",
                    "sample_path": items[0].get("rel_path") or "",
                    "count": len(items),
                })

    # 先讀既存 known (如果手動補過的不要覆蓋)
    if KNOWN.exists():
        existing = json.loads(KNOWN.read_text(encoding="utf-8"))
        for k, v in existing.items():
            # 手動補的優先保留
            if v.get("manual"):
                known[k] = v
            elif k not in known:
                known[k] = v

    # 同步合併 manual_locations.json，讓 unknown 能正確排除已手動補的
    # 重要：手動 entry 沒寫 area 時，不要蓋掉本次 phase1 自動偵測的 area
    if MANUAL.exists():
        manual = json.loads(MANUAL.read_text(encoding="utf-8"))
        for k, v in manual.items():
            auto_area = (known.get(k) or {}).get("area") if (known.get(k) or {}).get("auto_area") else None
            if auto_area and not v.get("area"):
                known[k] = {**v, "area": auto_area, "auto_area": True}
            else:
                known[k] = v

    # 把已在 known 裡的從 unknown 剔除（修正先算 unknown 才合 manual 的舊 bug）
    unknown = [u for u in unknown if u["case_name"] not in known]

    _write_atomic(KNOWN, json.dumps(known, ensure_ascii=False, indent=2))
    unknown.sort(key=lambda x: -x["count"])
    _write_atomic(UNKNOWN, json.dumps(unknown, ensure_ascii=False, indent=2))

    print(f"\n== phase1 結果 ==")
    print(f"已抽出城市的案件數: {len(known)}")
    print(f"未知的案件數:       {len(unknown)}")
    from collections import Counter
    city_ct = Counter(v["city"] for v in known.values())
    print(f"\n== city 分佈 ==")
    for k, v in city_ct.most_common():
        print(f"  {k}: {v}")


def merge_manual():
    """把 manual_locations.json 合入 locations_known.json。manual 值優先覆蓋。
    例外：當 known 端的 area 是 phase1 自動偵測來的（auto_area=True），且 manual 沒指定 area，
    則保留自動 area，不被手動 entry 蓋掉。"""
    if not MANUAL.exists():
        print(f"沒有 {MANUAL.name}，略過 merge")
        return
    known = json.loads(KNOWN.read_text(encoding="utf-8")) if KNOWN.exists() else {}
    manual = json.loads(MANUAL.read_text(encoding="utf-8"))
    added = updated = 0
    for k, v in manual.items():
        existing = known.get(k) or {}
        if k in known:
            updated += 1
        else:
            added += 1
        if existing.get("auto_area") and existing.get("area") and not v.get("area"):
            known[k] = {**v, "area": existing["area"], "auto_area": True}
        else:
            known[k] = v
    _write_atomic(KNOWN, json.dumps(known, ensure_ascii=False, indent=2))
    print(f"merge 完成：新增 {added}，覆蓋 {updated}，known 總數 {len(known)}")


def _retry(call, label, retries=5):
    """退避重試：用於 Supabase 偶爾 502/網路抖。call 是無參 lambda。"""
    import time
    for attempt in range(retries):
        try:
            return call()
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt+1}/{retries} after {wait}s: {label} ({type(e).__name__})", flush=True)
            time.sleep(wait)


def apply_to_db():
    """只 update 真正變動的 row，並用批量寫減少 HTTP roundtrip。

    流程：
      1. merge_manual + 讀 known
      2. 一次抓全表（drive_file_id, case_name, city, district, search_text）
      3. 算出 city/district 跟期望不一致的 case_name
      4. 依 (city, district) 分群，用 in_('case_name', [...]) 批量 update（一次最多 100 筆）
      5. 算出 search_text 缺地點關鍵字的 row，用 upsert 批量寫（一次 500 筆）
    """
    env = load_env()
    client = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

    merge_manual()

    if not KNOWN.exists():
        print("先跑 phase1")
        return
    known = json.loads(KNOWN.read_text(encoding="utf-8"))
    print(f"known {len(known)} case_names")

    # 一次抓全表現況。Supabase upsert 走 INSERT...ON CONFLICT，所有 NOT NULL 欄位
    # 都得在 payload 裡，所以 select * 拉完整 row、修改後再整列塞回。
    print("fetching current videos state...")
    rows = []
    PAGE = 1000
    offs = 0
    while True:
        r = _retry(
            lambda: client.table("videos").select("*")
                .range(offs, offs + PAGE - 1).execute(),
            f"fetch offs={offs}",
        )
        chunk = r.data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        offs += PAGE
    print(f"total videos: {len(rows)}")

    # 期望值 (None 也算有效值)。area 是「重劃區」標籤（如「七期」「水湳」「11期」），
    # 補進 search_text 讓搜重劃區能精準命中（不依賴 district 推測）。
    expected = {cn: (loc.get("city") or None, loc.get("district") or None,
                     loc.get("area") or None)
                for cn, loc in known.items()}

    # 1) 找 city/district 需變更的 case_name（只要該 case 至少一筆 row 跟期望不符就要更新）
    cd_changes = defaultdict(set)  # (city, district) -> {case_name, ...}
    seen_mismatch = set()
    for r in rows:
        cn = r.get("case_name")
        if not cn or cn not in expected:
            continue
        ec, ed, _ea = expected[cn]
        cur_c = r.get("city") or None
        cur_d = (r.get("district") or "") or None  # district 空字串視同 None 比較
        ed_norm = (ed or "") or None
        if cur_c != ec or cur_d != ed_norm:
            seen_mismatch.add(cn)
            cd_changes[(ec, ed)].add(cn)

    print(f"需更新 city/district 的 case_name: {len(seen_mismatch)} / {len(expected)}")

    # 2) 批量 update：相同 (city, district) 一群、每 100 筆 case_name 一次 in_()
    cd_updated = 0
    for (city, district), cn_set in cd_changes.items():
        cn_list = list(cn_set)
        for i in range(0, len(cn_list), 100):
            chunk = cn_list[i:i+100]
            r = _retry(
                lambda: client.table("videos").update({
                    "city": city,
                    "district": district,
                }).in_("case_name", chunk).execute(),
                f"update city={city} district={district} ({len(chunk)} cases)",
            )
            cd_updated += len(r.data or [])
    print(f"city/district 更新完成：{cd_updated} rows")

    # 3-4) search_text 補綴 + 批量 upsert
    # Supabase upsert 在 batch 邊界偶有 silent skip（PostgREST 沒回 error 但也沒寫進去），
    # 一次 apply 可能漏掉部分 rows。改成 loop：每輪重新 fetch + 算 pending，
    # 直到沒有 pending 才退出（最多 5 輪）。
    def _compute_st_updates(rows_in):
        updates = []
        for r in rows_in:
            cn = r.get("case_name")
            if not cn or cn not in expected:
                continue
            ec, ed, ea = expected[cn]
            st = r.get("search_text") or ""
            need = []
            if ec and ec not in st: need.append(ec)
            if ed and ed not in st: need.append(ed)
            pair = f"{ec} {ed}".strip() if ec and ed else ""
            if pair and pair not in st: need.append(pair)
            # area 是重劃區標籤，可能是「七期」或「七期 歌劇院」這種多 token
            if ea:
                for tok in ea.split():
                    if tok and tok not in st:
                        need.append(tok)
            if not need:
                continue
            new_row = dict(r)
            new_row["search_text"] = (st + " " + " ".join(need)).strip()
            new_row["city"] = ec
            new_row["district"] = ed
            # 排除 generated columns（DB 不接受寫入）
            new_row.pop("is_vertical", None)
            updates.append(new_row)
        # Dedupe by drive_file_id（paginated SELECT 跨 page 可能回傳同一列兩次）
        dedup = {}
        for r in updates:
            fid = r.get("drive_file_id")
            if fid:
                dedup[fid] = r
        return list(dedup.values()), len(updates) - len(dedup)

    def _refetch_rows():
        out = []
        o = 0
        while True:
            rr = _retry(
                lambda: client.table("videos").select("*")
                    .range(o, o + PAGE - 1).execute(),
                f"refetch offs={o}",
            )
            ck = rr.data or []
            out.extend(ck)
            if len(ck) < PAGE:
                break
            o += PAGE
        return out

    print("計算 search_text 補綴...")
    CHUNK = 500
    MAX_ATTEMPTS = 5
    total_st_updated = 0
    working = rows
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        attempt += 1
        st_updates, dropped = _compute_st_updates(working)
        if dropped:
            print(f"  第 {attempt} 輪去重移除 {dropped} 筆")
        if not st_updates:
            print(f"  第 {attempt} 輪確認無待更新，完成")
            break
        print(f"  第 {attempt} 輪：需更新 {len(st_updates)} rows")
        for i in range(0, len(st_updates), CHUNK):
            chunk = st_updates[i:i+CHUNK]
            r = _retry(
                lambda: client.table("videos").upsert(
                    chunk, on_conflict="drive_file_id", default_to_null=False,
                ).execute(),
                f"upsert search_text ({len(chunk)} rows)",
            )
            total_st_updated += len(r.data or [])
            if (i // CHUNK + 1) % 5 == 0:
                print(f"    upsert {i + len(chunk)}/{len(st_updates)}", flush=True)
        # 下一輪前 refetch（拿到剛剛 upsert 後的最新 search_text）
        if attempt < MAX_ATTEMPTS:
            working = _refetch_rows()
    else:
        print(f"⚠️ search_text 補綴跑完 {MAX_ATTEMPTS} 輪仍有 pending，請檢查 log")

    print(f"search_text 累計更新：{total_st_updated} rows")
    print("done.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "phase1"
    if cmd == "phase1":
        phase1()
    elif cmd == "merge":
        merge_manual()
    elif cmd == "apply":
        apply_to_db()
    else:
        print("用法: phase1 | merge | apply")
