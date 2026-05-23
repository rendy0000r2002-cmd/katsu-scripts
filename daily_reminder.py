"""
daily_reminder.py
每日中午12點自動推送明日拍攝行程 + 天氣到 LINE

設定排程：
  schtasks /create /tn "每日拍攝提醒" /tr "\"C:\\Users\\rendy\\AppData\\Local\\Programs\\Python\\Python313\\python.exe\" \"C:\\Users\\rendy\\daily_reminder.py\"" /sc daily /st 12:00 /f

手動測試：
  python C:/Users/rendy/daily_reminder.py
  python C:/Users/rendy/daily_reminder.py --tomorrow 4/8   （指定日期測試）
"""

import sys, csv, io, urllib.request, requests
from datetime import datetime, timedelta
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

LINE_TOKEN = "jf38wFOuMtELVgyk2EXWwLiSZsylvgDn0y0R1cuu5Ulh7sUrtj0edmZ6qtzqgmSYfbIiQafq77wWuw15/Pl/9PZ/64lgQvbfrcAv5Kzlh5Z78yfmlGKebmNPgzI1QixawpZveP1+SIk4FIGG9qGvwQdB04t89/1O/w1cDnyilFU="
LINE_USER_ID = "Ua9afcd9fed393bdc828612030672e430"
SHEETS_CSV = "https://docs.google.com/spreadsheets/d/1_naCZzjQ3G7W28RyaRe-ZuTEsr-sjeTBBf912pAJB-M/export?format=csv&gid=602539448"

WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

# 地名 → wttr.in 查詢字串
LOCATION_MAP = {
    "林口": "Linkou,TW", "淡水": "Tamsui,TW", "板橋": "Banqiao,TW",
    "新店": "Xindian,TW", "中和": "Zhonghe,TW", "永和": "Yonghe,TW",
    "土城": "Tucheng,TW", "三重": "Sanchong,TW", "蘆洲": "Luzhou,TW",
    "五股": "Wugu,TW", "八里": "Bali,TW", "三峽": "Sanxia,TW",
    "鶯歌": "Yingge,TW", "新莊": "Xinzhuang,TW", "泰山": "Taishan,TW",
    "汐止": "Xizhi,TW", "瑞芳": "Ruifang,TW", "基隆": "Keelung,TW",
    "桃園": "Taoyuan,TW", "中壢": "Zhongli,TW", "新竹": "Hsinchu,TW",
    "台中": "Taichung,TW", "台南": "Tainan,TW", "高雄": "Kaohsiung,TW",
    "宜蘭": "Yilan,TW", "花蓮": "Hualien,TW",
    "內湖": "Neihu,TW", "信義": "Xinyi+Taipei,TW", "松山": "Songshan+Taipei,TW",
    "士林": "Shilin,TW", "北投": "Beitou,TW", "南港": "Nangang,TW",
    "文山": "Wenshan,TW", "大同": "Datong+Taipei,TW",
    "台北": "Taipei,TW",
}

SKIP_KEYWORDS = ["出國", "自己案子", "家裡有事", "下雨延", "喜華不行", "休假", "補休"]


def get_schedule(target_date):
    """從 Google Sheets 讀取指定日期的行程，回傳 (家瑞, 威成, 其他, 備註) list"""
    m, d = target_date.month, target_date.day
    search_strs = [f"{m}/{d}", f"{m}月{d}日"]

    with urllib.request.urlopen(SHEETS_CSV, timeout=15) as resp:
        content = resp.read().decode("utf-8")

    rows = list(csv.reader(io.StringIO(content)))

    # 找最後一個「純日期欄」（格子內容短且吻合日期，排除案名格）
    target_col = None
    date_row_idx = None
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            cell_s = str(cell).strip()
            # 日期欄通常很短（≤10字），排除含案名的長格子
            if len(cell_s) <= 10 and any(s in cell_s for s in search_strs):
                target_col = j
                date_row_idx = i

    if target_col is None:
        return None

    # 日期行下方固定結構：其他、家瑞、威成、備註
    labels = ["其他", "家瑞", "威成", "備註"]
    result = {}
    for offset, label in enumerate(labels, start=1):
        if date_row_idx + offset < len(rows):
            row = rows[date_row_idx + offset]
            val = row[target_col].strip() if target_col < len(row) else ""
            if val and val.lower() not in ("nan", "none", ""):
                result[label] = val

    return result if result else None


from pathlib import Path as _PPath
_HERE = _PPath(__file__).resolve().parent
# 優先用同目錄的 cache（NAS 用）；找不到再用舊 Windows 路徑（PC 兼容）
_pc_cache = _PPath(r"C:\Users\rendy\property_locations.json")
_local_cache = _HERE / "property_locations.json"
PROPERTY_CACHE_FILE = str(_local_cache if _local_cache.exists() else
                          (_pc_cache if _pc_cache.exists() else _local_cache))


def load_property_cache():
    try:
        import json
        with open(PROPERTY_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if k != "說明" and v}
    except Exception:
        return {}


def save_property_cache(name, loc):
    import json
    try:
        with open(PROPERTY_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"說明": "建案名稱 → 地區對照，找不到的案名手動補在這裡"}
    data[name] = loc
    with open(PROPERTY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def geocode_property(name):
    """查詢台灣建案地點，回傳匹配的 LOCATION_MAP 地名（或 None）"""
    # 1. 本地快取
    cache = load_property_cache()
    if name in cache:
        return cache[name]

    def find_loc_near_name(text, search_name, window=300):
        """只在案名出現位置前後 window 字元內找地名（大小寫不敏感）"""
        text_lower = text.lower()
        name_lower = search_name.lower()
        idx = text_lower.find(name_lower)
        if idx == -1:
            return None
        vicinity = text[max(0, idx - window): idx + len(search_name) + window]
        for loc in LOCATION_MAP:
            if loc in vicinity:
                return loc
        return None

    # 2. DuckDuckGo 搜尋（可繞過 JS 限制）
    try:
        try:
            from ddgs import DDGS  # 新版（Python 3.10+）
        except ImportError:
            from duckduckgo_search import DDGS  # 舊版（NAS Python 3.8 用）
        with DDGS() as ddgs:
            results = list(ddgs.text(f"{name} 建案 地址", region="tw-tzh", max_results=5))
        combined = " ".join(r.get("title", "") + " " + r.get("body", "") for r in results)
        loc = find_loc_near_name(combined, name)
        if loc:
            save_property_cache(name, loc)
            return loc
    except Exception:
        pass

    return None


def parse_schedule_entry(text):
    """解析行程條目，回傳 (顯示文字, 剪輯師 or None)"""
    import re
    editor = None
    m = re.search(r'\(([^)]+)\)$', text.strip())
    if m:
        editor = m.group(1)
        text = text[:m.start()].strip()
    # 移除案名開頭的日期前綴（如「4/8安曼...」→「安曼...」）
    text = re.sub(r'^\d+/\d+\s*', '', text).strip()
    return text, editor


def extract_locations(texts):
    """從行程文字中抽取地名；找不到則嘗試搜尋案名"""
    combined = " ".join(texts)

    # 1. 直接關鍵詞比對
    found = [loc for loc in LOCATION_MAP if loc in combined]
    if found:
        return found[:3]

    # 2. 取出案名搜尋
    import re
    for text in texts:
        # 移除括號內容和日期前綴
        clean = re.sub(r'\([^)]*\)', '', text)
        clean = re.sub(r'^\d+/\d+\s*', '', clean).strip()
        # 取破折號前的部分（格式：案名-機型-主持人）
        core = clean.split('-')[0].split('*')[0].strip()
        # 從長到短依序嘗試（逐步去掉尾字），找到地點就停
        for length in range(len(core), 1, -1):
            candidate = core[:length]
            loc = geocode_property(candidate)
            if loc:
                return [loc]

    return ["台北"]


WMO_CODE = {
    0: "晴", 1: "大致晴", 2: "部分多雲", 3: "多雲",
    45: "霧", 48: "霧淞",
    51: "輕毛毛雨", 53: "毛毛雨", 55: "濃毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "陣雨", 81: "中陣雨", 82: "強陣雨",
    95: "雷陣雨", 96: "雷雨夾雹", 99: "強雷雨夾雹",
}

# 地名 → 經緯度（Open-Meteo 不需 API key）
LOCATION_COORDS = {
    "林口": (25.08, 121.38), "淡水": (25.17, 121.45), "板橋": (25.01, 121.46),
    "新店": (24.97, 121.54), "中和": (24.99, 121.49), "永和": (25.01, 121.51),
    "土城": (24.97, 121.44), "三重": (25.06, 121.49), "蘆洲": (25.08, 121.47),
    "五股": (25.08, 121.43), "八里": (25.15, 121.41), "三峽": (24.93, 121.37),
    "鶯歌": (24.95, 121.35), "新莊": (25.04, 121.44), "泰山": (25.06, 121.42),
    "汐止": (25.07, 121.66), "基隆": (25.13, 121.74), "桃園": (24.99, 121.31),
    "中壢": (24.96, 121.22), "新竹": (24.80, 120.97), "台中": (24.15, 120.67),
    "台南": (22.99, 120.20), "高雄": (22.63, 120.27), "宜蘭": (24.76, 121.75),
    "花蓮": (23.99, 121.60), "內湖": (25.08, 121.59), "士林": (25.09, 121.52),
    "北投": (25.13, 121.50), "南港": (25.05, 121.61), "台北": (25.05, 121.53),
    "善化": (23.15, 120.31), "新化": (23.04, 120.32), "仁德": (22.95, 120.23),
    "龜山": (25.04, 121.35), "蘆竹": (25.05, 121.26), "大園": (25.07, 121.22),
    "平鎮": (24.95, 121.22), "楊梅": (24.91, 121.14), "八德": (24.94, 121.29),
}

# ===== 中央氣象局 (CWA) 天氣 API =====
CWA_KEY = "CWA-4FD82174-9082-4691-AAB9-4DFBFFA5C8DE"

# 縣市 → CWA 鄉鎮預報資料集 ID（用 12-hour 7-day 預報，odd+2 系列）
CWA_CITY_DATASET = {
    "宜蘭": "003", "桃園": "007", "新竹縣": "011", "苗栗": "015",
    "彰化": "019", "南投": "023", "雲林": "027", "嘉義縣": "031",
    "屏東": "035", "臺東": "039", "台東": "039",
    "花蓮": "043", "澎湖": "047", "基隆": "051",
    "新竹": "055", "新竹市": "055",
    "嘉義": "059", "嘉義市": "059",
    "臺北": "063", "台北": "063",
    "高雄": "067", "新北": "071",
    "臺中": "075", "台中": "075",
    "臺南": "079", "台南": "079",
    "連江": "083", "金門": "087",
}

# 鄉鎮/區 → 縣市
TOWNSHIP_TO_CITY = {
    # 台北
    "中正": "台北", "大同": "台北", "中山": "台北", "松山": "台北",
    "大安": "台北", "萬華": "台北", "信義": "台北", "士林": "台北",
    "北投": "台北", "內湖": "台北", "南港": "台北", "文山": "台北",
    # 新北
    "板橋": "新北", "三重": "新北", "中和": "新北", "永和": "新北",
    "新莊": "新北", "新店": "新北", "土城": "新北", "蘆洲": "新北",
    "汐止": "新北", "樹林": "新北", "鶯歌": "新北", "三峽": "新北",
    "淡水": "新北", "瑞芳": "新北", "五股": "新北", "泰山": "新北",
    "林口": "新北", "深坑": "新北", "石碇": "新北", "坪林": "新北",
    "三芝": "新北", "石門": "新北", "八里": "新北", "平溪": "新北",
    "雙溪": "新北", "貢寮": "新北", "金山": "新北", "萬里": "新北", "烏來": "新北",
    # 桃園
    "桃園區": "桃園", "中壢": "桃園", "平鎮": "桃園", "八德": "桃園",
    "楊梅": "桃園", "蘆竹": "桃園", "大溪": "桃園", "龜山": "桃園",
    "大園": "桃園", "觀音": "桃園", "新屋": "桃園", "龍潭": "桃園", "復興": "桃園",
    # 臺中
    "中區": "台中", "東區": "台中", "西區": "台中", "南區": "台中", "北區": "台中",
    "西屯": "台中", "南屯": "台中", "北屯": "台中", "豐原": "台中", "東勢": "台中",
    "大甲": "台中", "清水": "台中", "沙鹿": "台中", "梧棲": "台中", "后里": "台中",
    "神岡": "台中", "潭子": "台中", "大雅": "台中", "新社": "台中", "石岡": "台中",
    "外埔": "台中", "大安": "台中", "烏日": "台中", "大肚": "台中", "龍井": "台中",
    "霧峰": "台中", "太平": "台中", "大里": "台中", "和平": "台中",
    # 臺南
    "新營": "台南", "鹽水": "台南", "白河": "台南", "柳營": "台南",
    "後壁": "台南", "東山": "台南", "麻豆": "台南", "下營": "台南",
    "六甲": "台南", "官田": "台南", "大內": "台南", "佳里": "台南",
    "學甲": "台南", "西港": "台南", "七股": "台南", "將軍": "台南",
    "北門": "台南", "新化": "台南", "善化": "台南", "新市": "台南",
    "安定": "台南", "山上": "台南", "玉井": "台南", "楠西": "台南",
    "南化": "台南", "左鎮": "台南", "仁德": "台南", "歸仁": "台南",
    "關廟": "台南", "龍崎": "台南", "永康": "台南",
    # 高雄
    "鹽埕": "高雄", "鼓山": "高雄", "左營": "高雄", "楠梓": "高雄",
    "三民": "高雄", "前金": "高雄", "苓雅": "高雄", "前鎮": "高雄",
    "旗津": "高雄", "小港": "高雄", "鳳山": "高雄", "林園": "高雄",
    "大寮": "高雄", "大樹": "高雄", "大社": "高雄", "仁武": "高雄",
    "鳥松": "高雄", "岡山": "高雄", "橋頭": "高雄", "燕巢": "高雄",
    "田寮": "高雄", "阿蓮": "高雄", "路竹": "高雄", "湖內": "高雄",
    "茄萣": "高雄", "永安": "高雄", "彌陀": "高雄", "梓官": "高雄",
    "旗山": "高雄", "美濃": "高雄", "六龜": "高雄", "甲仙": "高雄",
    "杉林": "高雄", "內門": "高雄", "茂林": "高雄", "桃源": "高雄", "那瑪夏": "高雄",
    # 宜蘭
    "宜蘭市": "宜蘭", "羅東": "宜蘭", "蘇澳": "宜蘭", "頭城": "宜蘭",
    "礁溪": "宜蘭", "壯圍": "宜蘭", "員山": "宜蘭", "冬山": "宜蘭",
    "五結": "宜蘭", "三星": "宜蘭", "大同": "宜蘭", "南澳": "宜蘭",
    # 基隆
    "中正區": "基隆", "七堵": "基隆", "暖暖": "基隆", "仁愛區": "基隆",
    "信義區": "基隆", "中山區": "基隆", "安樂": "基隆",
    # 花蓮
    "花蓮市": "花蓮", "光復": "花蓮", "玉里": "花蓮", "鳳林": "花蓮",
}

# 直接城市名（無需 township）
CWA_DIRECT_CITY = {
    "台北", "臺北", "新北", "桃園", "新竹", "新竹市", "新竹縣",
    "苗栗", "台中", "臺中", "彰化", "南投", "雲林",
    "嘉義", "嘉義市", "嘉義縣", "台南", "臺南", "高雄",
    "屏東", "宜蘭", "花蓮", "台東", "臺東", "基隆",
    "澎湖", "金門", "連江",
}


def _resolve_cwa(loc_name):
    """回傳 (city, township_or_first)。township_or_first 為 None 表示用該縣市第一個鄉鎮。"""
    # 1. 直接城市名 → 用該城市第一個鄉鎮的天氣（代表全市）
    if loc_name in CWA_DIRECT_CITY:
        return loc_name, None
    # 2. 鄉鎮名 → 找縣市
    if loc_name in TOWNSHIP_TO_CITY:
        city = TOWNSHIP_TO_CITY[loc_name]
        # 補上「區」/「鎮」/「市」字尾以對齊 CWA LocationName
        # 簡單規則：如果原名沒尾字，加「區」
        township = loc_name if loc_name[-1] in "區市鎮鄉" else loc_name + "區"
        return city, township
    # 3. 找不到，回 None
    return None, None


def _parse_cwa_time_for_date(times, target_date):
    """從 Time array 找出 target_date 當天的最大值（或第一個區間）。"""
    target_str = target_date.strftime("%Y-%m-%d")
    matches = []
    for t in times:
        start = t.get("StartTime") or t.get("DataTime") or ""
        if start.startswith(target_str):
            matches.append(t)
    return matches


def _value(time_entry, key_options):
    """從 ElementValue 抓第一個非 None 的指定 key。"""
    ev = time_entry.get("ElementValue")
    if isinstance(ev, list) and ev:
        ev = ev[0]
    if not isinstance(ev, dict):
        return None
    for k in key_options:
        if k in ev and ev[k] not in (None, "", "-"):
            return ev[k]
    return None


def get_weather_cwa(loc_name, target_date):
    """從中央氣象局抓指定日期天氣。失敗回 None。
    使用 12-hour 7-day 預報資料集（編號 003/007/011/.../075/...）。"""
    city, township = _resolve_cwa(loc_name)
    if not city or city not in CWA_CITY_DATASET:
        return None
    ds_id = CWA_CITY_DATASET[city]
    elements = "天氣現象,最高溫度,最低溫度,12小時降雨機率,紫外線指數,最高體感溫度"
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-{ds_id}"
    params = {"Authorization": CWA_KEY, "ElementName": elements}
    if township:
        params["LocationName"] = township
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        locs = data["records"]["Locations"][0]["Location"]
        if not locs:
            return None
        loc = locs[0]
        township_actual = loc["LocationName"]

        # 抓 target_date 當天白天時段（StartTime ~ 06:00 的那筆）
        target_str = target_date.strftime("%Y-%m-%d")

        wx, max_t, min_t, pop, uvi, app_t = None, None, None, None, None, None
        for el in loc.get("WeatherElement", []):
            name = el["ElementName"]
            day_times = [t for t in el.get("Time", [])
                         if (t.get("StartTime") or "").startswith(target_str)]
            if not day_times:
                continue
            # 偏好「白天」時段（StartTime 含 06:00），否則第一個
            day_t = next(
                (t for t in day_times if "06:00" in (t.get("StartTime") or "")),
                day_times[0],
            )
            if name == "天氣現象":
                wx = _value(day_t, ["Weather"])
            elif name == "最高溫度":
                max_t = _value(day_t, ["MaxTemperature"])
            elif name == "最低溫度":
                # 最低溫常在夜間時段，掃整天找最小
                vals = [int(_value(t, ["MinTemperature"]) or 99)
                        for t in day_times if _value(t, ["MinTemperature"])]
                min_t = str(min(vals)) if vals else None
            elif name == "12小時降雨機率":
                pop = _value(day_t, ["ProbabilityOfPrecipitation"])
            elif name == "紫外線指數":
                uvi = _value(day_t, ["UVIndex"])
            elif name == "最高體感溫度":
                app_t = _value(day_t, ["MaxApparentTemperature"])

        if not wx:
            return None
        # 組裝顯示文字
        head = f"{loc_name}（{township_actual}）"
        bits = [wx]
        if min_t and max_t:
            bits.append(f"{min_t}~{max_t}°C")
        if app_t:
            bits.append(f"體感{app_t}°C")
        if pop is not None:
            bits.append(f"降雨{pop}%")
        if uvi:
            bits.append(f"UV{uvi}")
        return f"{head}：{' '.join(bits)}"
    except Exception as e:
        print(f"CWA error for {loc_name}: {e}")
        return None


def _get_weather_open_meteo(loc_name, target_date):
    """fallback: open-meteo"""
    coords = LOCATION_COORDS.get(loc_name, LOCATION_COORDS["台北"])
    lat, lon = coords
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&timezone=Asia%2FTaipei&forecast_days=7"
        )
        r = requests.get(url, timeout=10)
        data = r.json()["daily"]
        # find target date in data["time"]
        target_str = target_date.strftime("%Y-%m-%d")
        idx = data["time"].index(target_str) if target_str in data["time"] else 1
        code = data["weathercode"][idx]
        tmax = data["temperature_2m_max"][idx]
        tmin = data["temperature_2m_min"][idx]
        rain_pct = data["precipitation_probability_max"][idx]
        desc = WMO_CODE.get(code, f"代碼{code}")
        return f"{loc_name}：{desc} {tmin:.0f}~{tmax:.0f}°C 降雨{rain_pct}%"
    except Exception:
        return f"{loc_name}：天氣資料無法取得"


def get_weather(loc_name, target_date=None):
    """主入口：先試 CWA，失敗 fallback 到 open-meteo。"""
    if target_date is None:
        target_date = datetime.now() + timedelta(days=1)
    cwa = get_weather_cwa(loc_name, target_date)
    if cwa:
        return cwa
    return _get_weather_open_meteo(loc_name, target_date)


def send_line(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]}
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    if not r.ok:
        print(f"LINE 發送失敗：{r.status_code} {r.text}")
    else:
        print("已發送到 LINE")


def main():
    # 支援 --tomorrow M/D 測試指定日期
    if "--tomorrow" in sys.argv:
        idx = sys.argv.index("--tomorrow")
        parts = sys.argv[idx + 1].split("/")
        now = datetime.now()
        target = now.replace(month=int(parts[0]), day=int(parts[1]))
    else:
        target = datetime.now() + timedelta(days=1)

    date_str = f"{target.month}/{target.day}（{WEEKDAYS[target.weekday()]}）"
    print(f"查詢日期：{date_str}")

    schedule = get_schedule(target)

    if not schedule:
        msg = f"📅 明日 {date_str} 無拍攝行程"
        print(msg)
        send_line(msg)
        return

    # 過濾掉休假/取消等無效條目
    valid = {k: v for k, v in schedule.items()
             if not any(kw in v for kw in SKIP_KEYWORDS)}

    if not valid:
        msg = f"📅 明日 {date_str} 無拍攝行程"
        print(msg)
        send_line(msg)
        return

    lines = [f"📅 明日拍攝提醒 {date_str}", ""]
    for label in ["家瑞", "威成", "其他", "備註"]:
        if label in valid:
            entry, editor = parse_schedule_entry(valid[label])
            line = f"{label}：{entry}"
            if editor:
                line += f"  ｜剪輯：{editor}"
            lines.append(line)

    # 天氣：每個有行程的人各自查地點
    lines.append("")
    lines.append("🌤 天氣預報")
    seen_locs = []
    for label in ["家瑞", "威成", "其他"]:
        if label not in valid:
            continue
        locs = extract_locations([valid[label]])
        for loc in locs:
            if loc not in seen_locs:
                seen_locs.append(loc)
                lines.append(get_weather(loc, target))
    if not seen_locs:
        lines.append(get_weather("台北", target))

    msg = "\n".join(lines)
    print(msg)
    send_line(msg)


if __name__ == "__main__":
    main()
