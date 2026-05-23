"""
每天背景跑 Gemini Vision 標拍帶地點。
室外能認出地標 → 改檔名加 _<地標>；室內/特寫/不確定 → 跳過、標記已分析。
跑完每天發 Telegram 摘要。

排程：Windows Task Scheduler 每日 18:00（避開其他排程）
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "tag_state.json"
LOG_DIR = ROOT / "logs"
STATE_SCHEMA_VERSION = "v2-keyed"  # state["done"] key = "{volume}:{rel_path}"，避免 path 變動失效

# v1 集區 2026-05-19 從 /volume1/homes/ETtomorrow 搬到 /volume2/homes/ETtomorrow
# state migration 用這個對應表把舊 abs path key 還原成 (volume, rel_path)
_LEGACY_KEY_PREFIXES = (
    ("/volume2/homes2/ETtomorrow/", "v2"),
    ("/volume2/homes/ETtomorrow/",  "v1"),
    ("/volume1/homes/ETtomorrow/",  "v1"),  # 搬遷前的舊路徑
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
)

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = "8635121564"

from nas_roots import existing_roots, get_prefix, synthetic_id as ns_synthetic_id

CHANNEL_FOLDERS = [
    ("2_琦郁房事悄悄話", "琦郁房事悄悄話"),
    ("12_女子開箱", "女子開箱"),
    ("7_怡怡", "怡怡"),
    ("11_Ivy", "Ivy"),
    ("14_Amber", "Amber"),
]

_ACTIVE_ROOTS = existing_roots()
if not _ACTIVE_ROOTS:
    raise RuntimeError("No NAS root mounted on this host")

# 跨 volume：每個 channel 跟每個存在的 volume 配對
# NAS_ROOTS = [(abs_path, channel_label, NasRoot), ...]
NAS_ROOTS = [
    (f"{get_prefix(r).rstrip('/')}/{folder}", label, r)
    for r in _ACTIVE_ROOTS
    for folder, label in CHANNEL_FOLDERS
]
# 兼容舊變數（向後相容）
WIN_NAS_PREFIX = get_prefix(_ACTIVE_ROOTS[0]).rstrip("/") + "/"
NAS_LINUX_ROOT = "/volume2/homes"  # 用來反推 supabase rel_path（v1 已搬 V2，2026-05-19）

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".mkv", ".m4v"}
# 檔名含 _<中文> 或 _<英文 ≥3 字 PascalCase/UPPER> → 視為已標
# 中文：_泰山國中、_台中洲際棒球場
# 英文：_Costco、_MITSUI、_IKEA
# 不會誤判 IMG_2131 / DJI_0220（純數字）或 cam1（無底線）
TAGGED_RE = re.compile(r"_(?:[一-鿿]|[A-Z][A-Za-z]{2,})")
DAILY_LIMIT = 25000    # paid tier 啟用後可一次清空 backlog（Flash-Lite Tier 1: 4000 RPM, no RPD）
RPM_DELAY = 0          # paid tier 並行下不需要 sleep
NUM_WORKERS = 4        # 並行 worker 數量。實測 8 反而比 4 慢（NAS 4 core + IO 飽和），4 最佳
FRAME_TIME_PCT = 50    # 抽幀位置（影片中段 50%）

# 命名規則 → 直接判定為 indoor，不用送 Gemini 浪費 quota
# 取保守策略：只匹配「幾乎一定是室內 / 主持人 talking head」的命名
INDOOR_NAME_PATTERNS = re.compile(
    r"(客廳|樣品屋|接待中心|公設|電梯|門廳|大廳|衛浴|廚房|臥室|健身房|"
    r"主持|訪談|口白|VO|cam[123])",
    re.IGNORECASE,
)
# 這些字眼是高價值外景，排序時優先送
PRIORITY_NAME_PATTERNS = re.compile(
    r"(外景|外觀|街景|街訪|空拍|空景|周邊|附近|地標|aerial|drone)",
    re.IGNORECASE,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / f"tag_locations_{date.today():%Y%m%d}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def send_tg(text):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        body = json.dumps({"chat_id": TG_CHAT, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log(f"TG 失敗: {e}")


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _state_key(volume: str, rel_path: str) -> str:
    """state["done"] 的 key 格式：避免用 abs path（path 變動會失效）"""
    return f"{volume}:{rel_path}"


def migrate_state(state) -> int:
    """把舊版 abs-path key 轉成 (volume:rel_path) key。idempotent。回傳遷移筆數。"""
    if state.get("schema_version") == STATE_SCHEMA_VERSION:
        return 0
    done = state.get("done", {})
    migrated = {}
    converted = 0
    for k, v in done.items():
        # 已是新格式（volume:rel）直接保留
        if not k.startswith("/") and ":" in k and k.split(":", 1)[0] in ("v1", "v2", "v3", "v4"):
            migrated[k] = v
            continue
        s = k.replace("\\", "/")
        matched = False
        for prefix, vol in _LEGACY_KEY_PREFIXES:
            if s.startswith(prefix):
                rel = s[len(prefix):]
                new_key = _state_key(vol, rel)
                # 若兩個舊 path 對應到同一 (vol, rel)，後寫的覆蓋（值通常等價）
                migrated[new_key] = v
                matched = True
                converted += 1
                break
        if not matched:
            # 無法解析的 key 原樣保留（安全），下次再看
            migrated[k] = v
    state["done"] = migrated
    state["schema_version"] = STATE_SCHEMA_VERSION
    return converted


def load_state():
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        state = {"done": {}, "tagged_count": 0, "no_tag_count": 0, "failed": []}
    converted = migrate_state(state)
    if converted:
        save_state(state)
        log(f"state schema 已升級 → {STATE_SCHEMA_VERSION}（轉換 {converted} 筆 done key）")
    return state


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def synthetic_id(rel_path: str, volume: str = "v1") -> str:
    """跟 scan_nas.py 同算法（v1 不加前綴，v2+ 加 volume 前綴）"""
    return ns_synthetic_id(rel_path, volume)


# 同案件資料夾下的所有影片共用 city/district，cache by case_dir 避免重複 supabase 查詢
# 一個 case 平均 25 支影片，可省 24 次 HTTP call
_CASE_INFO_CACHE = {}
_CASE_CACHE_LOCK = threading.Lock()


def supabase_get_case_location(env, rel_path: str, volume: str = "v1"):
    """從 Supabase 撈該影片所在案件的 city/district 當 prompt 提示。"""
    case_key = f"{volume}:{rel_path.rsplit('/', 1)[0]}"  # cache key 帶 volume
    with _CASE_CACHE_LOCK:
        if case_key in _CASE_INFO_CACHE:
            return _CASE_INFO_CACHE[case_key]
    base = env["SUPABASE_URL"].rstrip("/")
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    sid = synthetic_id(rel_path, volume)
    url = (
        f"{base}/rest/v1/videos"
        f"?drive_file_id=eq.{urllib.parse.quote(sid)}"
        "&select=case_name,city,district&limit=1"
    )
    req = urllib.request.Request(url, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
    })
    result = None
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        if data:
            result = data[0]
    except Exception as e:
        log(f"  supabase get 失敗: {e}")
    with _CASE_CACHE_LOCK:
        _CASE_INFO_CACHE[case_key] = result
    return result


def supabase_delete_row(env, rel_path: str, volume: str = "v1"):
    """刪除舊 rel_path 對應的 row（檔名變了之後 next sync 會建新 row）"""
    base = env["SUPABASE_URL"].rstrip("/")
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    sid = synthetic_id(rel_path, volume)
    url = f"{base}/rest/v1/videos?drive_file_id=eq.{urllib.parse.quote(sid)}"
    req = urllib.request.Request(url, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        log(f"  supabase delete 失敗: {e}")


# NAS host 的 ffmpeg 4.1.9 缺 JPEG muxer 且沒裝 ffprobe，必須借 katsu-web 容器內的完整 ffmpeg/ffprobe。
# 容器 LANG 為空，無法寫入含中文的輸出路徑，所以走 web_media-tmp volume：host 端 _data/ 對應容器內 /data/media-tmp/。
_USE_DOCKER_FFMPEG = sys.platform.startswith("linux") and Path("/usr/local/bin/docker").exists()
# 用容器內 `timeout` 包住 ffmpeg：subprocess.run 超時時 docker exec 客戶端被殺但 ffmpeg 不會跟著死，
# 會累積成 zombie 把 NAS CPU 卡爆。container 內 timeout 25 才能真正 SIGKILL ffmpeg。
_FFMPEG_CMD = (
    ["/usr/local/bin/docker", "exec", "katsu-web", "timeout", "25", "ffmpeg"]
    if _USE_DOCKER_FFMPEG else ["ffmpeg"]
)
_FFPROBE_CMD = ["/usr/local/bin/docker", "exec", "katsu-web", "ffprobe"] if _USE_DOCKER_FFMPEG else ["ffprobe"]
if _USE_DOCKER_FFMPEG:
    TMP_DIR_HOST = Path("/volume2/@docker/volumes/web_media-tmp/_data")
    TMP_DIR_DOCKER = "/data/media-tmp"
else:
    TMP_DIR_HOST = Path(__file__).parent
    TMP_DIR_DOCKER = str(TMP_DIR_HOST)


def tmp_jpg_paths(worker_id: int):
    """回傳 (host_path, docker_path) — 每個 worker 用獨立檔避免並行衝突"""
    name = f"tag_frame_w{worker_id}.jpg"
    return TMP_DIR_HOST / name, f"{TMP_DIR_DOCKER}/{name}"


def extract_frame(video_path: Path, jpg_host: Path, jpg_docker: str) -> bool:
    """ffmpeg 抽 frame，scale 到 720p，寫到 jpg_host。回傳成功與否。
    跳過 ffprobe（每次省 1 秒 docker exec 開銷）— 直接 -ss 2 抓 2 秒處的 frame，
    對 4 秒以上影片夠用；更短的 ffmpeg 會 fallback 到最後一個 keyframe，仍能輸出。
    -hwaccel auto：Sony FX cinema 機 4K 140Mbps all-intra 軟解 90s+ 會 timeout，加 hwaccel 降到 8s（ffmpeg 8.0 內建 vulkan/qsv 軟解最佳化）。
    """
    try:
        jpg_host.unlink()
    except FileNotFoundError:
        pass
    try:
        subprocess.run(
            _FFMPEG_CMD + ["-hwaccel", "auto", "-ss", "2", "-i", str(video_path),
             "-update", "1", "-vframes", "1", "-vf", "scale=720:-1",
             "-q:v", "5", "-y", jpg_docker],
            capture_output=True, timeout=35,  # container timeout 25 + 10s buffer
        )
        return jpg_host.exists() and jpg_host.stat().st_size > 1000
    except Exception:
        return False


def call_gemini_vision(jpg_path: Path, case_name: str, city: str, district: str) -> dict:
    """送 Vision，要求回 JSON {landmark: str|null, kind: 'outdoor'|'indoor'|'closeup'}"""
    img_b64 = base64.b64encode(jpg_path.read_bytes()).decode()

    prompt = f"""這張畫面是台灣不動產業 podcaster 的拍帶 frame。
建案：{case_name}（{city or '未知城市'} · {district or '未知區'}）

判斷這張 frame：
1. 室內（樣品屋/接待中心/客廳/室內裝潢/特寫鏡頭）→ kind: "indoor"，landmark: null
2. 戶外能認出**台灣具體地標**（例：元生公園、機捷A9林口站、三井 OUTLET 外觀、文心森林公園、新莊運動公園、特定建案外觀）→ kind: "outdoor"，landmark: 該地標名（簡潔，不超過 8 字）
3. 戶外但只是**不知名建築/街景/天空**，無法確定具體地標 → kind: "outdoor"，landmark: null

**嚴格只回傳 JSON**（不要 markdown code fence、不要其他文字）：
{{"kind": "indoor|outdoor|closeup", "landmark": "地標名" 或 null}}

寧願 null 不要瞎猜。
"""

    body = json.dumps({
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
        ]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 100},
    }).encode("utf-8")

    req = urllib.request.Request(
        GEMINI_URL, data=body,
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            # 抽 JSON
            m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            return {"kind": "unknown", "landmark": None, "raw": text[:100]}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                # 判斷是 daily quota 還是 RPM quota：daily 的 quotaId 帶 PerDay
                if "PerDayPerProject" in err or "free_tier_requests" in err and "limit: 0" in err:
                    raise RuntimeError(f"Daily quota 已用完: {err[:200]}")
                m = re.search(r'"retryDelay":\s*"(\d+)s"', err)
                wait = int(m.group(1)) + 5 if m else 60
                if wait > 120:
                    raise RuntimeError(f"Daily quota 已用完: {err[:200]}")
                log(f"  429 等 {wait}s ({attempt+1}/3)")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini {e.code}: {err[:200]}")
        except Exception as e:
            log(f"  call 例外: {e}")
            time.sleep(5)
    raise RuntimeError("Gemini 重試上限")


def collect_pending(state):
    """掃所有 (root, channel) 組合，列出未標 + 未處理過的影片清單。
    回傳 [(Path, volume), ...]，volume 標記讓後續能正確算 synthetic_id 與 rel_path。"""
    done_set = set(state.get("done", {}).keys())
    pending = []
    filename_skipped = 0
    for root_path, ch_label, nas_root in NAS_ROOTS:
        p = Path(root_path)
        if not p.exists():
            log(f"❌ {root_path} 不存在")
            continue
        for case in p.iterdir():
            # 跳過 symlink / bind mount 的 case，避免 v1 walk 透過 union 掃到 v2 內容造成 duplicate
            if case.is_symlink():
                continue
            if case.is_dir() and os.path.ismount(case):
                continue
            if not case.is_dir() or case.name.startswith(("#", "_")):
                continue
            try:
                for f in case.rglob("*"):
                    # rglob 內也防 symlink（保險）
                    if f.is_symlink():
                        continue
                    if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
                        continue
                    # 跳過 macOS AppleDouble metadata (._foo.mov) 與 Synology 縮圖快取
                    if f.name.startswith("._") or "@eaDir" in f.parts:
                        continue
                    rel = to_rel_path(f)
                    skey = _state_key(nas_root.volume, rel)
                    if skey in done_set:
                        continue
                    if TAGGED_RE.search(f.stem):
                        # 已標過，記入 done 不再處理
                        state.setdefault("done", {})[skey] = "pre-tagged"
                        continue
                    # 檔名/路徑命中 indoor 規則 → 直接 skip 不浪費 Gemini quota
                    full = f"{f.parent.name}/{f.name}"
                    if INDOOR_NAME_PATTERNS.search(full):
                        state.setdefault("done", {})[skey] = "filename-indoor-skip"
                        filename_skipped += 1
                        continue
                    pending.append((f, nas_root.volume))
            except Exception:
                pass
    if filename_skipped:
        log(f"命名規則跳過：{filename_skipped} 支（室內/talking-head）")
    # 高優先序（外景/空拍）排前面，先標到高價值地點
    pending.sort(key=lambda t: 0 if PRIORITY_NAME_PATTERNS.search(f"{t[0].parent.name}/{t[0].name}") else 1)
    return pending


def to_rel_path(abs_path: Path) -> str:
    """X:/<prefix>/2_琦郁/.../foo.mp4 → 2_琦郁/.../foo.mp4，自動嘗試所有 root 前綴"""
    s = str(abs_path).replace("\\", "/")
    for r in _ACTIVE_ROOTS:
        prefix = get_prefix(r).rstrip("/") + "/"
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


SAFE_CHARS = re.compile(r"[\\/:*?\"<>|]")


def sanitize_tag(tag: str) -> str:
    """檔名安全化"""
    tag = tag.strip()
    tag = SAFE_CHARS.sub("", tag)
    tag = tag[:30]  # 不要過長
    return tag


def main():
    # CLI: 可選 --limit N 覆蓋每日上限（測試用）
    daily_limit = DAILY_LIMIT
    if len(sys.argv) > 2 and sys.argv[1] == "--limit":
        daily_limit = int(sys.argv[2])
        log(f"測試模式：本次上限 {daily_limit}")

    log(f"=== tag_locations_daily 開始 {date.today()} ===")
    env = load_env()
    state = load_state()

    pending = collect_pending(state)
    log(f"待處理：{len(pending)} 檔")

    if not pending:
        save_state(state)
        send_tg(f"📝 拍帶地點標註：今天沒有待處理檔案 🎉")
        return

    counters = {"done": 0, "tagged": 0, "indoor": 0, "no_landmark": 0, "failed": 0}
    state_lock = threading.Lock()
    stop_flag = threading.Event()
    pending_to_process = pending[:daily_limit]

    def worker(idx, f, volume):
        if stop_flag.is_set():
            return
        with state_lock:
            if counters["done"] >= daily_limit:
                return
        abs_str = str(f).replace("\\", "/")
        rel_str = to_rel_path(f)
        skey = _state_key(volume, rel_str)
        jpg_host, jpg_docker = tmp_jpg_paths(idx)
        try:
            info = supabase_get_case_location(env, rel_str, volume) or {}
            case_name = info.get("case_name") or f.parent.name
            city = info.get("city") or ""
            district = info.get("district") or ""

            if not extract_frame(f, jpg_host, jpg_docker):
                with state_lock:
                    state.setdefault("failed", []).append({"path": abs_str, "error": "ffmpeg"})
                    state.setdefault("done", {})[skey] = "ffmpeg-fail"
                    counters["failed"] += 1
                    counters["done"] += 1
                return

            result = call_gemini_vision(jpg_host, case_name, city, district)
            kind = result.get("kind", "unknown")
            landmark = result.get("landmark")

            if landmark and kind == "outdoor":
                tag = sanitize_tag(landmark)
                if tag:
                    new_name = f"{f.stem}_{tag}{f.suffix}"
                    new_path = f.with_name(new_name)
                    new_rel = to_rel_path(new_path)
                    new_skey = _state_key(volume, new_rel)
                    supabase_delete_row(env, rel_str, volume)
                    f.rename(new_path)
                    with state_lock:
                        # 同時記 old + new key，避免重啟後 scan 找到 new path 又重跑一次
                        state.setdefault("done", {})[skey] = f"tagged:{tag}"
                        state["done"][new_skey] = f"tagged:{tag}"
                        counters["tagged"] += 1
                        counters["done"] += 1
                    log(f"  ✓ {f.name} → _{tag}")
                else:
                    with state_lock:
                        state.setdefault("done", {})[skey] = "no-tag-empty"
                        counters["no_landmark"] += 1
                        counters["done"] += 1
            elif kind == "indoor":
                with state_lock:
                    state.setdefault("done", {})[skey] = "indoor-skip"
                    counters["indoor"] += 1
                    counters["done"] += 1
            else:
                with state_lock:
                    state.setdefault("done", {})[skey] = "no-landmark"
                    counters["no_landmark"] += 1
                    counters["done"] += 1

            with state_lock:
                done_n = counters["done"]
                tagged_n = counters["tagged"]
            if done_n % 100 == 0:
                with state_lock:
                    save_state(state)
                log(f"  進度 {done_n}/{len(pending_to_process)}（已標 {tagged_n}）")
            if done_n > 0 and done_n % 2000 == 0:
                send_tg(f"⏳ 進度 {done_n}/{len(pending_to_process)} ({100*done_n/len(pending_to_process):.0f}%) — 已標 {tagged_n} 個地點")

            if RPM_DELAY:
                time.sleep(RPM_DELAY)

        except RuntimeError as e:
            if "Daily quota" in str(e):
                log(f"💀 daily quota 用完")
                stop_flag.set()
                return
            with state_lock:
                counters["failed"] += 1
                counters["done"] += 1
                state.setdefault("failed", []).append({"path": abs_str, "error": str(e)[:200]})
                state.setdefault("done", {})[skey] = "error"
        except Exception as e:
            with state_lock:
                counters["failed"] += 1
                counters["done"] += 1
                state.setdefault("failed", []).append({"path": abs_str, "error": str(e)[:200]})
                state.setdefault("done", {})[skey] = "error"
            log(f"  ✗ {f.name}: {e}")

    log(f"啟動 {NUM_WORKERS} 個並行 worker，目標 {len(pending_to_process)} 支")
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as ex:
        # 分配 worker_id 用循環，確保 tmp_jpg 互不衝突
        futures = [ex.submit(worker, i % NUM_WORKERS, f, vol) for i, (f, vol) in enumerate(pending_to_process)]
        for fut in as_completed(futures):
            if stop_flag.is_set():
                break

    save_state(state)

    today_done = counters["done"]
    today_tagged = counters["tagged"]
    today_indoor = counters["indoor"]
    today_no_landmark = counters["no_landmark"]
    today_failed = counters["failed"]

    # 清理所有 worker 的 tmp jpg
    for i in range(NUM_WORKERS):
        jh, _ = tmp_jpg_paths(i)
        try:
            jh.unlink()
        except Exception:
            pass

    state["tagged_count"] = state.get("tagged_count", 0) + today_tagged
    state["no_tag_count"] = state.get("no_tag_count", 0) + today_indoor + today_no_landmark
    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    remaining = len(pending) - today_done
    days_left = (remaining + daily_limit - 1) // max(daily_limit, 1) if remaining else 0
    cum_processed = state["tagged_count"] + state["no_tag_count"]
    tag_rate = state["tagged_count"] / cum_processed if cum_processed else 0
    expected_more_tags = round(remaining * tag_rate) if cum_processed >= 50 else None
    rate_line = f"歷史標到率：{tag_rate:.0%}（{state['tagged_count']}/{cum_processed}）"
    if expected_more_tags is not None:
        rate_line += f"\n預估還能標到：~{expected_more_tags} 筆"
    msg = (
        f"📝 拍帶地點標註 — 今日進度\n"
        f"\n"
        f"今天處理：{today_done}\n"
        f"  ✓ 標到地點：{today_tagged}\n"
        f"  🏠 室內跳過：{today_indoor}\n"
        f"  ⚪ 無具體地標：{today_no_landmark}\n"
        f"  ✗ 失敗：{today_failed}\n"
        f"\n"
        f"剩餘待處理：{remaining}\n"
        f"預估還要：{days_left} 天（以每日上限 {daily_limit} 計）\n"
        f"{rate_line}"
    )
    send_tg(msg)
    log("=== 結束 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log(f"FATAL: {e}\n{tb}")
        send_tg(f"⚠️ tag_locations_daily 失敗：{e!r}\n{tb[-300:]}")
        raise
