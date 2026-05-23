"""Vision pass — NAS container resident version.

Runs inside katsu-scripts-v2 (network_mode: host, /volume2 mounted).
- Reads videos from /volume2/homes/ETtomorrow + /volume2/homes2/ETtomorrow
- ffmpeg extracts frame to memory
- POSTs to Gemini Vision (gemini-flash-latest) with retry/backoff
- Updates videos table via PostgREST at http://127.0.0.1:3011
- Pidfile prevents duplicate runs

Launch:
  sudo docker exec -d katsu-scripts-v2 sh -c \
    'cd /volume2/docker-prod/scripts/原初映像片庫 && \
     nohup python3 vision_pass_nas.py --all --apply --yes > logs/vision_pass.log 2>&1 &'
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
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
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
assert GEMINI_KEY, "GEMINI_API_KEY missing from .env"

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
CONFIDENCE_MIN = 0.7
SCRIPT_DIR = Path("/volume2/docker-prod/scripts/原初映像片庫")
JOURNAL_DIR = SCRIPT_DIR / "logs" / "vision_journals"
PIDFILE = SCRIPT_DIR / "logs" / "vision_pass.pid"
NAS_ROOTS = [
    Path("/volume2/homes/ETtomorrow"),
    Path("/volume2/homes2/ETtomorrow"),
]

# Landmark names that GPS pass already covered — pre-tagged files skip Vision
LANDMARK_NAMES = {
    "中科台積電","中科行政大樓","中科二期","中科后里園區","國家歌劇院","新光三越中港",
    "台中市政府","逢甲商圈","七期市政","台中車站","勤美草悟道","科博館","台中高鐵站",
    "一中商圈","水湳經貿園區","中央公園","文心森林公園","大坑風景區","麗寶樂園","三井OUTLET",
    "台北101信義","西門町","永康街","中山商圈","台北車站","松山機場","圓山","天母",
    "公館商圈","信義安和","大安森林公園","北投溫泉","淡水老街","九份老街","板橋車站商圈",
    "林口三井OUTLET","桃園機場","中壢SOGO","竹科","新竹巨城","新竹車站","南科","安平古堡",
    "林百貨","台南車站","駁二藝術特區","西子灣","蓮池潭","高鐵左營站","漢神巨蛋","夢時代",
    "日月潭","阿里山","墾丁大街","太魯閣","清境農場",
}

PROMPT = """請看這張影片畫面，判斷拍攝地點。

回 JSON：
{
  "place": "中科台積電" | "逢甲商圈" | "板橋車站商圈" | "信義商圈" | "西門町" | "其他具體地名" | null,
  "confidence": 0.0~1.0,
  "scene_type": "建築外觀" | "馬路車流" | "室內樣品屋" | "空拍" | "夜景" | "招牌特寫" | "人物" | "其他",
  "reason": "看到什麼線索（一句話）"
}

判斷原則：
- 看到明顯招牌/Logo/建築特徵且能對應台灣特定地名 → place 填地點 + confidence 0.7~1.0
- 只看得出大致氛圍但無具體線索（例：一般馬路、樣品屋內景、純空拍農田）→ place=null
- 偏向保守：寧可 null 也不要瞎猜
- place 用簡稱，不加「市/區」字
- 只回 JSON，不要其他文字"""

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
    """Refuse to start if another instance running."""
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    if PIDFILE.exists():
        try:
            old_pid = int(PIDFILE.read_text().strip())
            # Check if process still alive
            if (Path("/proc") / str(old_pid)).exists():
                sys.exit(f"vision_pass already running (pid={old_pid})")
        except Exception:
            pass
        PIDFILE.unlink()  # stale
    PIDFILE.write_text(str(os.getpid()))


def release_pidfile():
    try:
        PIDFILE.unlink()
    except Exception:
        pass


def disk_path(rel_path: str) -> Path | None:
    """Resolve rel_path to absolute under one of NAS_ROOTS."""
    for root in NAS_ROOTS:
        p = root / rel_path
        if p.exists():
            return p
    return None


def ffmpeg_extract(path: Path) -> bytes | None:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-ss", "00:00:02", "-i", str(path),
             "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=20,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return proc.stdout
    except Exception:
        return None


def call_gemini(image_bytes: bytes) -> dict | None:
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0,
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


def list_untagged(client, scope_prefix: str | None, limit: int | None):
    landmarks = sorted(LANDMARK_NAMES)
    rows = []
    # PostgREST: page through, filter NOT (tags && ARRAY[...])
    # supabase-py doesn't easily support array-not-overlap, fall back to client-side filter
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
            tags = r.get("tags") or []
            if any(t in LANDMARK_NAMES for t in tags):
                continue
            rows.append((
                r["drive_file_id"], r["rel_path"], r["filename"],
                r.get("search_text") or "", tags,
            ))
            if limit and len(rows) >= limit:
                return rows
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def process_one(item):
    fid, rel_path, fn, search_text, tags = item
    disk = disk_path(rel_path)
    if not disk:
        return {"fid": fid, "rel_path": rel_path, "status": "file-missing"}
    img = ffmpeg_extract(disk)
    if not img:
        return {"fid": fid, "rel_path": rel_path, "status": "ffmpeg-fail"}
    result = call_gemini(img)
    if not result or "_err" in result:
        return {"fid": fid, "rel_path": rel_path, "status": "gemini-fail",
                "err": result.get("_err") if result else "no-response"}
    place = result.get("place")
    conf = float(result.get("confidence") or 0)
    scene = result.get("scene_type", "")
    reason = result.get("reason", "")
    if not place or conf < CONFIDENCE_MIN:
        return {"fid": fid, "rel_path": rel_path, "filename": fn,
                "status": "low-conf", "conf": conf, "scene": scene,
                "reason": reason, "place": place}
    return {"fid": fid, "rel_path": rel_path, "filename": fn,
            "status": "tag", "place": place, "conf": conf,
            "scene": scene, "reason": reason,
            "search_text": search_text, "tags": list(tags)}


def db_writer(q: Queue, jf, apply: bool, client, stats: dict, stats_lock):
    while True:
        item = q.get()
        if item is None:
            break
        with stats_lock:
            stats[item["status"]] = stats.get(item["status"], 0) + 1
            stats["_n"] = stats.get("_n", 0) + 1
        if item["status"] == "tag":
            print(f"[tag {item['conf']:.2f}] {item['filename']:<40s}  →  "
                  f"{item['place']}  ({item['scene']}: {item['reason'][:30]})",
                  flush=True)
            if apply:
                place = item["place"]
                new_tags = list(item["tags"])
                if place not in new_tags:
                    new_tags.append(place)
                new_search = item["search_text"]
                if place not in new_search:
                    new_search = (new_search + " " + place).strip()
                try:
                    client.table("videos").update(
                        {"tags": new_tags, "search_text": new_search}
                    ).eq("drive_file_id", item["fid"]).execute()
                except Exception as e:
                    item["status"] = f"db-fail:{str(e)[:60]}"
        jf.write(json.dumps(item, ensure_ascii=False) + "\n")
        jf.flush()
        if stats["_n"] % 100 == 0:
            print(f"[progress] {stats['_n']}  "
                  + " ".join(f"{k}={v}" for k, v in sorted(
                      stats.items(), key=lambda x: -x[1])[:6] if not k.startswith('_')),
                  flush=True)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--root", help="rel path under NAS_HOME (e.g. '1_即賞屋')")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
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
        print(f"[query] {len(files)} untagged videos\n", flush=True)

        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        jpath = JOURNAL_DIR / f"vision_pass_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        print(f"[journal] {jpath}\n", flush=True)

        stats = {}
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
