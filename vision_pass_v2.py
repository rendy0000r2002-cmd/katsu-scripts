"""Vision pass v2 — folder-context-aware variant.

Changes vs v1:
  - Resolves city/district from the case folder name using a built-in keyword table.
  - Feeds that as a "known location range" hint into Gemini's prompt so the model
    is constrained to identifying landmarks WITHIN that range.
  - After Gemini returns a place, sanity-checks: does the place's known city match
    the folder's city? If not, status=out-of-area (rejected, not written to DB).
  - Otherwise behaves identically to v1 (same DB schema, same journal format,
    same threading, same pidfile, same retry/backoff).

Launch:
  sudo docker exec -d katsu-scripts-v2 sh -c \
    'cd /volume2/docker-prod/scripts/原初映像片庫 && \
     nohup python3 vision_pass_v2.py --all --apply --yes > logs/vision_pass_v2.log 2>&1 &'
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue

from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
# Default to flash-lite (5-10x cheaper than flash). Can override via --model.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
assert GEMINI_KEY, "GEMINI_API_KEY missing"

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
CONFIDENCE_MIN = 0.7
SCRIPT_DIR = Path("/volume2/docker-prod/scripts/原初映像片庫")
JOURNAL_DIR = SCRIPT_DIR / "logs" / "vision_journals"
PIDFILE = SCRIPT_DIR / "logs" / "vision_pass_v2.pid"
CASE_CACHE_FILE = SCRIPT_DIR / "logs" / "case_locations.json"
CASE_CACHE: dict = {}  # populated by load_case_cache() in main()
NAS_ROOTS = [
    Path("/volume2/homes/ETtomorrow"),
    Path("/volume2/homes2/ETtomorrow"),
]

# Region prefix (used in 0_空拍素材 / 0_空景)
REGION_PREFIX = {
    "0_北部": "北部",
    "1_中部": "中部",
    "2_南部": "南部",
    "3_東部": "東部",
}

# Top-level folders that hold non-location content — skip entirely
EXCLUDED_TOPLEVEL = {
    "剪輯效果、素材",
    "剪輯效果",
    "@eaDir",
    "#recycle",
    "_ops_triggers",
    "_proxies",
}

# keyword → (city, district hint).  Order matters: longer / more specific first.
# `district` may be "A/B" if ambiguous; resolve() will split.
KEYWORD_MAP: list[tuple[str, str, str]] = [
    # 北士科 must precede 北 → checked by substring containment in folder name
    ("北士科", "台北", "北投/士林"),
    ("士科", "台北", "北投/士林"),
    ("北投", "台北", "北投"),
    ("士林", "台北", "士林"),
    ("松山", "台北", "松山"),
    ("信義區", "台北", "信義"),
    ("信義富境", "台北", "信義"),  # 建案名
    ("大巨蛋", "台北", "信義"),
    ("信義", "台北", "信義"),  # 一般情境，大部分指台北信義（信義鄉誤判機率很低）
    ("大安", "台北", "大安"),
    ("大同區", "台北", "大同"),
    ("中山區", "台北", "中山"),
    ("文山", "台北", "文山"),
    ("萬華", "台北", "萬華"),
    ("中正區", "台北", "中正"),
    ("南港", "台北", "南港"),
    ("內湖", "台北", "內湖"),
    ("大直", "台北", "中山"),
    ("關渡", "台北", "北投"),
    ("民生社區", "台北", "松山"),
    ("民生", "台北", "松山"),
    # 新北
    ("三重", "新北", "三重"),
    ("中和", "新北", "中和"),
    ("永和", "新北", "永和"),
    ("板橋", "新北", "板橋"),
    ("新莊", "新北", "新莊"),
    ("新店", "新北", "新店"),
    ("土城", "新北", "土城"),
    ("汐止", "新北", "汐止"),
    ("蘆洲", "新北", "蘆洲"),
    ("淡水", "新北", "淡水"),
    ("樹林", "新北", "樹林"),
    ("鶯歌", "新北", "鶯歌"),
    ("三峽", "新北", "三峽"),
    ("五股", "新北", "五股"),
    ("泰山", "新北", "泰山"),
    ("八里", "新北", "八里"),
    ("林口", "新北", "林口"),
    ("新潤世界都心", "新北", "林口"),
    ("新潤", "新北", "林口"),
    ("景平", "新北", "中和"),
    ("江翠", "新北", "板橋"),
    ("紅樹林", "新北", "淡水"),
    # 桃園
    ("青埔", "桃園", "中壢/大園"),
    ("中壢", "桃園", "中壢"),
    ("南崁", "桃園", "蘆竹"),
    ("蘆竹", "桃園", "蘆竹"),
    ("楊梅", "桃園", "楊梅"),
    ("平鎮", "桃園", "平鎮"),
    ("大園", "桃園", "大園"),
    ("八德", "桃園", "八德"),
    ("小檜溪", "桃園", "桃園"),
    ("鳳鳴", "桃園", "八德"),
    ("草漯", "桃園", "觀音"),
    ("Tpark", "桃園", "楊梅"),
    ("桃園", "桃園", "桃園"),
    # 新竹
    ("竹北", "新竹", "竹北"),
    ("新豐", "新竹", "新豐"),
    ("新竹", "新竹", "新竹"),
    # 苗栗
    ("頭份", "苗栗", "頭份"),
    ("苗栗", "苗栗", "苗栗"),
    # 台中
    ("北屯", "台中", "北屯"),
    ("西屯", "台中", "西屯"),
    ("南屯", "台中", "南屯"),
    ("七期", "台中", "西屯"),
    ("八期", "台中", "南屯"),
    ("十一期", "台中", "北屯"),
    ("十二期", "台中", "北屯"),
    ("14期", "台中", "北屯"),
    ("水湳", "台中", "北屯/西屯"),
    # NOTE: 「麗寶」keyword 拿掉，因為 user 的 麗寶 案件都是 麗寶建設(桃園)，
    # 不是 麗寶樂園(台中后里)。讓 Gemini text lookup 處理。
    ("后里", "台中", "后里"),
    ("逢甲", "台中", "西屯"),
    ("中科", "台中", "西屯"),
    ("歌劇院", "台中", "西屯"),
    ("草悟道", "台中", "西區"),
    ("勤美", "台中", "西區"),
    ("台中港", "台中", "梧棲"),
    ("沙鹿", "台中", "沙鹿"),
    ("梧棲", "台中", "梧棲"),
    ("大里", "台中", "大里"),
    ("太平", "台中", "太平"),
    ("霧峰", "台中", "霧峰"),
    ("烏日", "台中", "烏日"),
    ("神岡", "台中", "神岡"),
    ("潭子", "台中", "潭子"),
    ("豐原", "台中", "豐原"),
    ("臺中", "台中", "台中"),
    ("台中", "台中", "台中"),
    # 彰投雲嘉
    ("員林", "彰化", "員林"),
    ("彰化", "彰化", "彰化"),
    ("埔里", "南投", "埔里"),
    ("草屯", "南投", "草屯"),
    ("南投", "南投", "南投"),
    ("斗六", "雲林", "斗六"),
    ("虎尾", "雲林", "虎尾"),
    ("雲林", "雲林", "雲林"),
    ("民雄", "嘉義", "民雄"),
    ("嘉義", "嘉義", "嘉義"),
    # 台南
    ("永康", "台南", "永康"),
    ("安南", "台南", "安南"),
    ("安平", "台南", "安平"),
    ("新化", "台南", "新化"),
    ("善化", "台南", "善化"),
    ("歸仁", "台南", "歸仁"),
    ("仁德", "台南", "仁德"),
    ("南科", "台南", "善化/新市"),
    ("臺南", "台南", "台南"),
    ("台南", "台南", "台南"),
    # 高雄
    ("左營", "高雄", "左營"),
    ("前鎮", "高雄", "前鎮"),
    ("苓雅", "高雄", "苓雅"),
    ("鳳山", "高雄", "鳳山"),
    ("岡山", "高雄", "岡山"),
    ("美濃", "高雄", "美濃"),
    ("旗山", "高雄", "旗山"),
    ("林園", "高雄", "林園"),
    ("橋頭", "高雄", "橋頭"),
    ("澄清湖", "高雄", "鳥松"),
    ("愛河", "高雄", "前金/鹽埕"),
    ("內惟", "高雄", "鼓山"),
    ("凹子底", "高雄", "左營"),
    ("高雄", "高雄", "高雄"),
    # 屏東
    ("潮州", "屏東", "潮州"),
    ("東港", "屏東", "東港"),
    ("屏東", "屏東", "屏東"),
    # 宜花東
    ("羅東", "宜蘭", "羅東"),
    ("蘇澳", "宜蘭", "蘇澳"),
    ("頭城", "宜蘭", "頭城"),
    ("礁溪", "宜蘭", "礁溪"),
    ("宜蘭", "宜蘭", "宜蘭"),
    ("花蓮", "花蓮", "花蓮"),
    ("臺東", "台東", "台東"),
    ("台東", "台東", "台東"),
]


def load_case_cache() -> dict:
    if CASE_CACHE_FILE.exists():
        try:
            return json.loads(CASE_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_case_cache(cache: dict) -> None:
    CASE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CASE_CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


CASE_LOOKUP_PROMPT = """「{name}」是台灣的一個建案、不動產案件、影片拍攝主題或地名。
你知道它在台灣的哪個城市或區域嗎？

回 JSON：
{{
  "city": "台北" | "新北" | "桃園" | "新竹" | "苗栗" | "台中" | "彰化" | "南投" | "雲林" | "嘉義" | "台南" | "高雄" | "屏東" | "宜蘭" | "花蓮" | "台東" | null,
  "district": "信義/三重/北屯/...等具體行政區或商圈名" | null,
  "confidence": 0.0~1.0,
  "reason": "判斷依據（一句話）"
}}

判斷原則：
- 你確定知道這個名稱的位置 → 填城市+地區 + confidence 0.7~1.0
- 模糊或不確定 → confidence < 0.7 但仍可填猜測
- 完全不熟（普通詞彙、無法判斷）→ 全 null
- city 用簡稱（台北/新北/桃園/...），不加「市」字"""


def gemini_text_lookup(case_name: str) -> dict | None:
    """Ask Gemini (text-only) what city a given case folder name belongs to."""
    payload = {
        "contents": [{"parts": [{"text": CASE_LOOKUP_PROMPT.format(name=case_name)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "nullable": True},
                    "district": {"type": "string", "nullable": True},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["city", "district", "confidence", "reason"],
            },
            "temperature": 0,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    for attempt, delay in enumerate([2, 5, 12, 30]):
        req = urllib.request.Request(
            GEMINI_URL, data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
            if not text:
                return None
            return json.loads(text)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(delay)
                continue
            return None
        except Exception:
            time.sleep(delay)
    return None


def resolve_folder_context(rel_path: str) -> dict:
    """Parse rel_path → extract region + city/district from any path part.

    Scans ALL intermediate path parts (excluding filename) for known location keywords.
    Top-level matches REGION_PREFIX, deeper parts match KEYWORD_MAP.
    """
    parts = rel_path.split("/")
    if not parts:
        return {"region": None, "case_name": "", "sub_name": "",
                "cities": [], "districts": [], "matched_keywords": [],
                "excluded": False}

    # Check top-level for excluded set
    top = parts[0] if parts else ""
    if top in EXCLUDED_TOPLEVEL:
        return {"region": None, "case_name": top, "sub_name": "",
                "cities": [], "districts": [], "matched_keywords": [],
                "excluded": True}

    # Region: look for 0_北部 / 1_中部 / 2_南部 anywhere in path
    region = None
    for p in parts:
        if p in REGION_PREFIX:
            region = REGION_PREFIX[p]
            break

    # case_name: first non-region, non-toplevel-channel part
    # Walk path parts (excluding the filename, which is parts[-1])
    interior = parts[:-1]  # all dirs above the file
    # Build a context string from interior parts (skip region/toplevel-channel labels)
    case_candidates = [p for p in interior if p not in REGION_PREFIX and p != top]
    case_name = case_candidates[0] if case_candidates else (interior[-1] if interior else "")
    sub_name = case_candidates[1] if len(case_candidates) > 1 else ""

    # Match keywords against the joined interior text
    full_context = " ".join(interior)

    cities: list[str] = []
    districts: list[str] = []
    matched_kws: list[str] = []
    source = ""

    # PRIORITY 1: case-name cache (exact match, authoritative if confidence >= 0.7)
    for cand in (case_name, sub_name):
        cand = (cand or "").strip()
        if not cand:
            continue
        entry = CASE_CACHE.get(cand)
        if not entry or entry.get("confidence", 0) < 0.7:
            continue
        ci = entry.get("city")
        if ci:
            cities = [ci]
            d = entry.get("district")
            if d:
                districts = [p.strip() for p in d.split("/") if p.strip()]
            matched_kws.append(f"cache:{cand}")
            source = "gemini-lookup"
            break

    # PRIORITY 2: keyword table (substring match, fallback)
    if not cities:
        source = "keyword"
        for kw, city, district in KEYWORD_MAP:
            if kw in full_context:
                matched_kws.append(kw)
                if city not in cities:
                    cities.append(city)
                for d in district.split("/"):
                    d = d.strip()
                    if d and d not in districts:
                        districts.append(d)
                break

    return {
        "region": region,
        "case_name": case_name,
        "sub_name": sub_name,
        "cities": cities,
        "districts": districts,
        "matched_keywords": matched_kws,
        "context_source": source,
        "excluded": False,
    }


def resolve_place_city(place: str) -> str | None:
    """Reverse-lookup: what city does Gemini's place name belong to?"""
    for kw, city, _ in KEYWORD_MAP:
        if kw in place:
            return city
    return None


# -------- the actual vision pass: prompt + Gemini call --------

BASE_PROMPT = """請看這張影片畫面，判斷拍攝地點。

回 JSON：
{
  "place": "具體可辨識的地標 / 建案 / 捷運站 / 重劃區 / 商圈 / 公園 / 橋樑 / 著名建築" | null,
  "confidence": 0.0~1.0,
  "scene_type": "建築外觀" | "馬路車流" | "室內樣品屋" | "空拍" | "夜景" | "招牌特寫" | "人物" | "其他",
  "reason": "看到什麼線索（一句話）"
}

判斷原則：
- 看到明顯招牌/Logo/建築特徵/地標 → place 填具體地名 + confidence 0.7~1.0
- 只看得出大致氛圍但無具體線索 → place=null
- 偏向保守：寧可 null 也不要瞎猜
- place 用簡稱（不加「市/區」字）
- 只回 JSON，不要其他文字"""


def build_prompt(ctx: dict) -> str:
    if not ctx["cities"]:
        return BASE_PROMPT
    region = ctx["region"] or ""
    cities = "、".join(ctx["cities"])
    districts = "、".join(ctx["districts"][:3]) if ctx["districts"] else ""
    hint_loc = f"{region}{cities}" if region else cities
    if districts:
        hint_loc += f" {districts}"
    hint = (
        f"\n\n【已知拍攝範圍】這支影片來自「{ctx['case_name']}」案件，"
        f"拍攝範圍應在 {hint_loc} 一帶。\n"
        f"請辨識畫面中此範圍內的具體地標。"
        f"如果畫面中可見的線索明顯不在 {cities} 範圍內，或畫面太普通看不出地標，"
        f"請回 place=null，不要猜。"
    )
    return BASE_PROMPT + hint


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "place": {"type": "string", "nullable": True},
        "confidence": {"type": "number"},
        "scene_type": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["place", "confidence", "scene_type", "reason"],
}


def acquire_pidfile():
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    if PIDFILE.exists():
        try:
            old_pid = int(PIDFILE.read_text().strip())
            if (Path("/proc") / str(old_pid)).exists():
                sys.exit(f"vision_pass_v2 already running (pid={old_pid})")
        except Exception:
            pass
        PIDFILE.unlink()
    PIDFILE.write_text(str(os.getpid()))


def release_pidfile():
    try:
        PIDFILE.unlink()
    except Exception:
        pass


def disk_path(rel_path: str) -> Path | None:
    for root in NAS_ROOTS:
        p = root / rel_path
        if p.exists():
            return p
    return None


FFMPEG_TIMEOUT = 60  # 4K HEVC decoding can be slow under load


def _ffmpeg_try(path: Path, pre_args: list[str], post_args: list[str]) -> tuple[bytes | None, str]:
    """Run one ffmpeg attempt. pre_args go BEFORE -i (input options like -ss for input seek),
    post_args go AFTER -i (output options like -ss for output seek)."""
    cmd = ["ffmpeg", "-v", "error", *pre_args, "-i", str(path), *post_args,
           "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5",
           "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, f"exc:{str(e)[:60]}"
    # Accept output if we got a valid-looking JPEG (>1KB) even on non-zero exit
    # (ffmpeg sometimes emits warnings about end-of-stream but still produces a frame)
    if proc.stdout and len(proc.stdout) > 1024:
        return proc.stdout, ""
    err = proc.stderr.decode("utf-8", errors="replace")[:200] if proc.stderr else "no-output"
    return None, err


def ffmpeg_extract(path: Path) -> bytes | None:
    """Try multiple seek strategies. 4K HEVC under 6 parallel workers can be slow,
    so 60s per attempt + fallback through alternative seek modes."""
    # Strategy 1: input seek at 2s (fast, works for most well-formed files)
    img, _err = _ffmpeg_try(path, ["-ss", "00:00:02"], [])
    if img:
        return img
    # Strategy 2: output seek at 2s (more reliable for some non-standard containers)
    img, _err = _ffmpeg_try(path, [], ["-ss", "00:00:02"])
    if img:
        return img
    # Strategy 3: no seek — grab the very first decodeable frame
    img, _err = _ffmpeg_try(path, [], [])
    if img:
        return img
    return None


def call_gemini(image_bytes: bytes, prompt: str) -> dict | None:
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0,
            "thinkingConfig": {"thinkingBudget": 0},  # disable thinking to cut cost ~50-70%
        },
    }
    body = json.dumps(payload).encode("utf-8")
    delays = [2, 5, 12, 30, 60]
    last_err = None
    for i, delay in enumerate(delays):
        req = urllib.request.Request(
            GEMINI_URL, data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
            if not text:
                return None
            return json.loads(text)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                last_err = f"429 (attempt {i+1})"
                time.sleep(delay)
                continue
            return {"_err": f"HTTP {e.code}"}
        except Exception as e:
            last_err = str(e)[:60]
            time.sleep(delay)
    return {"_err": last_err or "max-retries"}


def supa():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _has_host_tag(tags) -> bool:
    """True if file is tagged as 有主持人 — these contain host face/scene,
    Gemini Vision can't reliably identify landmarks. Skip them entirely."""
    return any(t == "有主持人" for t in (tags or []))


def _has_place_suffix(filename: str) -> bool:
    """True if filename stem has at least one '_<chinese>' segment, suggesting
    a place suffix was applied by a prior rename pass (e.g. IMG_1010_中正國中.MOV).
    Used by list_untagged() to skip files already tagged via rename — avoids
    burning Gemini quota re-tagging stuff that's done."""
    import re
    stem = filename.rsplit(".", 1)[0]
    return bool(re.search(r"_[\u4e00-\u9fff]", stem))

def list_untagged(client, scope_prefix: str | None, limit: int | None):
    rows = []
    PAGE = 1000
    offset = 0
    while True:
        q = client.table("videos").select(
            "drive_file_id,rel_path,filename,search_text,tags,source"
        ).eq("source", "nas").order("rel_path").range(offset, offset + PAGE - 1)
        if scope_prefix:
            q = q.like("rel_path", scope_prefix.rstrip("/") + "/%")
        resp = q.execute()
        batch = resp.data or []
        if not batch:
            break
        for r in batch:
            # Skip files that already have a Chinese place suffix in filename
            # (rename pass on 5/21 tagged them; re-running wastes Gemini quota).
            if _has_place_suffix(r.get("filename", "")):
                continue
            # Skip files tagged 有主持人 — host-focused shots, Gemini can't
            # reliably identify location. Only 無主持人 / 空拍 worth scanning.
            if _has_host_tag(r.get("tags")):
                continue
            rows.append((
                r["drive_file_id"], r["rel_path"], r["filename"],
                r.get("search_text") or "", r.get("tags") or [],
            ))
            if limit and len(rows) >= limit:
                return rows
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def process_one(item):
    fid, rel_path, fn, search_text, tags = item
    ctx = resolve_folder_context(rel_path)

    # Skip excluded top-level folders (剪輯效果、素材 etc.)
    if ctx.get("excluded"):
        return {"fid": fid, "rel_path": rel_path, "filename": fn,
                "status": "excluded-folder", "ctx": ctx}

    # Skip files with no city/district context — folder is too generic,
    # Gemini would hallucinate without constraint. Save the API call.
    if not ctx.get("cities"):
        return {"fid": fid, "rel_path": rel_path, "filename": fn,
                "status": "no-context", "ctx": ctx}

    disk = disk_path(rel_path)
    if not disk:
        return {"fid": fid, "rel_path": rel_path, "status": "file-missing",
                "ctx": ctx}
    img = ffmpeg_extract(disk)
    if not img:
        return {"fid": fid, "rel_path": rel_path, "status": "ffmpeg-fail",
                "ctx": ctx}

    prompt = build_prompt(ctx)
    result = call_gemini(img, prompt)
    if not result or "_err" in result:
        return {"fid": fid, "rel_path": rel_path, "status": "gemini-fail",
                "err": result.get("_err") if result else "no-response",
                "ctx": ctx}

    place = result.get("place")
    conf = float(result.get("confidence") or 0)
    scene = result.get("scene_type", "")
    reason = result.get("reason", "")
    if not place or conf < CONFIDENCE_MIN:
        return {"fid": fid, "rel_path": rel_path, "filename": fn,
                "status": "low-conf", "conf": conf, "scene": scene,
                "reason": reason, "place": place, "ctx": ctx}

    # SANITY CHECK: does Gemini's place belong to a known city,
    # and does that city match folder's city?
    place_city = resolve_place_city(place)
    folder_cities = ctx.get("cities") or []
    if folder_cities and place_city and place_city not in folder_cities:
        return {"fid": fid, "rel_path": rel_path, "filename": fn,
                "status": "out-of-area",
                "place": place, "place_city": place_city,
                "folder_cities": folder_cities,
                "conf": conf, "scene": scene, "reason": reason, "ctx": ctx}

    return {"fid": fid, "rel_path": rel_path, "filename": fn,
            "status": "tag", "place": place, "conf": conf,
            "scene": scene, "reason": reason,
            "search_text": search_text, "tags": list(tags), "ctx": ctx}


def db_writer(q: Queue, jf, apply: bool, client, stats: dict, stats_lock):
    while True:
        item = q.get()
        if item is None:
            break
        with stats_lock:
            stats[item["status"]] = stats.get(item["status"], 0) + 1
            stats["_n"] = stats.get("_n", 0) + 1
        if item["status"] == "tag":
            ctx = item.get("ctx") or {}
            ctx_str = ",".join(ctx.get("cities") or [])
            print(f"[tag {item['conf']:.2f}] {item['filename']:<40s}  →  "
                  f"{item['place']}  (city:{ctx_str}  {item['scene']}: {item['reason'][:30]})",
                  flush=True)
            if apply:
                place = item["place"]
                # Also add folder cities/districts to tags
                folder_tags = []
                for c in ctx.get("cities") or []:
                    folder_tags.append(c)
                for d in ctx.get("districts") or []:
                    if d not in folder_tags:
                        folder_tags.append(d)
                new_tags = list(item["tags"])
                for t in folder_tags + [place]:
                    if t and t not in new_tags:
                        new_tags.append(t)
                new_search = item["search_text"]
                for t in folder_tags + [place]:
                    if t and t not in new_search:
                        new_search = (new_search + " " + t).strip()
                try:
                    client.table("videos").update(
                        {"tags": new_tags, "search_text": new_search}
                    ).eq("drive_file_id", item["fid"]).execute()
                except Exception as e:
                    item["status"] = f"db-fail:{str(e)[:60]}"
        elif item["status"] == "out-of-area":
            print(f"[OUT] {item['filename']:<40s}  →  {item['place']} "
                  f"(city {item['place_city']} vs folder {item['folder_cities']})", flush=True)
        jf.write(json.dumps(item, ensure_ascii=False) + "\n")
        jf.flush()
        if stats["_n"] % 100 == 0:
            top = sorted(((k, v) for k, v in stats.items() if not k.startswith('_')),
                         key=lambda x: -x[1])[:6]
            print(f"[progress] {stats['_n']}  " + " ".join(f"{k}={v}" for k, v in top),
                  flush=True)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--root", help="rel path under NAS_HOME (e.g. '0_空拍素材 (重要)')")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)  # 4 = sweet spot per memory feedback_nas_parallel_4workers
    ap.add_argument("--skip-journal", action="append", default=[],
                    help="Path(s) to vision_pass journals — skip fids with status=tag in those")
    args = ap.parse_args()
    if args.all and args.apply and not args.yes:
        sys.exit("--all --apply requires --yes")

    acquire_pidfile()
    try:
        scope = args.root.replace("\\", "/") if args.root else None
        print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}  workers={args.workers}  "
              f"model={GEMINI_MODEL}  scope={scope or 'ALL'}", flush=True)

        client = supa()
        files = list_untagged(client, scope, args.limit if args.limit > 0 else None)
        print(f"[query] {len(files)} videos\n", flush=True)

        # Optional: skip fids that were already successfully tagged in prior journals
        if args.skip_journal:
            skip_fids: set[str] = set()
            for jp in args.skip_journal:
                p = Path(jp)
                if not p.exists():
                    print(f"[skip-journal] file not found: {jp}", flush=True)
                    continue
                with p.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("status") == "tag" and d.get("fid"):
                            skip_fids.add(d["fid"])
                print(f"[skip-journal] {len(skip_fids)} fids loaded from {p.name}", flush=True)
            before = len(files)
            files = [f for f in files if f[0] not in skip_fids]
            print(f"[skip-journal] filtered out {before - len(files)} already-tagged files\n", flush=True)

        # Pre-pass: load case cache, identify ALL unique case folders, ask Gemini text
        # for any not already in cache. Cache is authoritative source of truth — keyword
        # table only used as fallback for cases not yet looked up.
        global CASE_CACHE
        CASE_CACHE = load_case_cache()
        print(f"[case-cache] {len(CASE_CACHE)} entries loaded from {CASE_CACHE_FILE.name}", flush=True)

        unknown_cases: set[str] = set()
        for fid, rel_path, fn, st, tg in files:
            parts = rel_path.split("/")
            if not parts or parts[0] in EXCLUDED_TOPLEVEL:
                continue
            # Get the case folder name (first interior part after top-level/region)
            interior = parts[:-1]
            case_candidates = [p for p in interior if p not in REGION_PREFIX and p != parts[0]]
            cn = case_candidates[0] if case_candidates else ""
            cn = cn.strip()
            if cn and cn not in CASE_CACHE:
                unknown_cases.add(cn)

        if unknown_cases:
            print(f"[case-lookup] resolving {len(unknown_cases)} unknown case folders via Gemini text", flush=True)
            for i, cn in enumerate(sorted(unknown_cases), 1):
                r = gemini_text_lookup(cn)
                CASE_CACHE[cn] = r or {"city": None, "district": None, "confidence": 0,
                                        "reason": "lookup failed"}
                if i % 5 == 0 or i == len(unknown_cases):
                    save_case_cache(CASE_CACHE)
                    print(f"  [{i}/{len(unknown_cases)}] {cn[:40]}  →  "
                          f"{r.get('city') if r else 'fail'} / {r.get('district') if r else '-'} "
                          f"(conf={r.get('confidence', 0) if r else 0:.2f})",
                          flush=True)
                else:
                    print(f"  [{i}/{len(unknown_cases)}] {cn[:40]}  →  "
                          f"{r.get('city') if r else 'fail'} / {r.get('district') if r else '-'} "
                          f"(conf={r.get('confidence', 0) if r else 0:.2f})",
                          flush=True)
                time.sleep(0.3)
            save_case_cache(CASE_CACHE)
            print(f"[case-lookup] done, cache saved to {CASE_CACHE_FILE.name}\n", flush=True)
        else:
            print("[case-lookup] no unknown cases\n", flush=True)

        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        jpath = JOURNAL_DIR / f"vision_pass_v2_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        print(f"[journal] {jpath}\n", flush=True)

        stats: dict = {}
        stats_lock = threading.Lock()
        q: Queue = Queue(maxsize=args.workers * 4)
        t0 = time.time()
        with open(jpath, "a", encoding="utf-8") as jf:
            writer = threading.Thread(target=db_writer,
                                      args=(q, jf, args.apply, client, stats, stats_lock),
                                      daemon=True)
            writer.start()
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = [ex.submit(process_one, f) for f in files]
                for fut in as_completed(futures):
                    try:
                        q.put(fut.result())
                    except Exception as e:
                        q.put({"status": f"future-err:{str(e)[:60]}"})
            q.put(None)
            writer.join()
        elapsed = time.time() - t0

        print(f"\n--- summary (elapsed {elapsed:.0f}s, "
              f"{len(files)/max(elapsed,1):.1f} files/s) ---", flush=True)
        for k, v in sorted(stats.items(), key=lambda x: -x[1]):
            if not k.startswith("_"):
                print(f"  {v:>5}  {k}", flush=True)
        print(f"\njournal: {jpath}", flush=True)
    finally:
        release_pidfile()


if __name__ == "__main__":
    main()
