"""
Backfill width/height for videos table.
- NAS: ffprobe locally via Y:/U: mount (parallel workers); on NAS host 用 docker exec katsu-web ffprobe
- Drive: Google Drive API videoMediaMetadata (batch)

Resume-safe: only processes rows where width is NULL.
"""
from __future__ import annotations
import os
import sys
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

SB_URL = os.environ["SUPABASE_URL"]
SB_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
TOKEN = r"C:\Users\rendy\token.json"

# 跨平台：NAS 上沒有 ffprobe，用 docker exec katsu-web ffprobe；NAS 路徑改 /volume2/...
IS_NAS = os.name == "posix" and os.path.exists("/volume2")
DOCKER_EXEC = ["sudo", "/usr/local/bin/docker", "exec", "katsu-web"] if IS_NAS else []

NAS_WORKERS = 24
DRIVE_BATCH = 100
UPDATE_BATCH = 200


def sb():
    return create_client(SB_URL, SB_KEY)


# ---------- NAS ----------
# Windows: prefer Y: (fast local SMB on home LAN) over U: (slow QuickConnect).
# NAS：直接用 /volume2/... 路徑（容器 bind mount 一致；v1 2026-05-19 已搬 V2）。
from nas_roots import convert_path, ALL_ROOTS  # noqa: E402

def resolve_nas_path(stored: str) -> str | None:
    if not stored:
        return None
    if IS_NAS:
        # 先試 nas_roots.convert_path 把 Y:/U:/ 開頭轉成 /volume*/
        for src_platform in ("linux", "win", "docker"):
            translated = convert_path(stored, target_platform="linux")
            if translated and os.path.exists(translated):
                return translated
        # 退路：原 stored 已經是 /volume*/... 形式
        if stored.startswith("/volume2/") and os.path.exists(stored):
            return stored
        return None
    # Windows: 嘗試所有 root 在 win 平台對應的 path
    win_candidate = convert_path(stored, target_platform="win")
    candidates = [win_candidate] if win_candidate else []
    # U:/ vs Y:/ swap 補強（同一個 root 不同網路狀態下的對應）
    candidates += [
        stored,
        stored.replace("U:/", "Y:/"),
        stored.replace("Y:/", "U:/"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


import shutil as _shutil
_HAS_LOCAL_FFPROBE = _shutil.which("ffprobe") is not None


def ffprobe_dims(path: str) -> tuple[int, int] | None:
    """Return (width, height) with rotation applied.
    - None: ffprobe error / timeout（暫時失敗，下次再試）
    - (0, 0): ffprobe 成功但無 video stream（audio-only / 純音檔 .mp4）→ 永久 sentinel
    """
    base_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:stream_side_data=rotation:stream_tags=rotate",
        "-of", "json",
        path,
    ]
    if IS_NAS and not _HAS_LOCAL_FFPROBE:
        # 舊路徑：NAS host 沒 ffprobe → docker exec katsu-web-v2 ffprobe
        is_root = os.geteuid() == 0
        if is_root:
            cmd = ["/usr/local/bin/docker", "exec", "katsu-web-v2"] + base_cmd
        else:
            cmd = ["sudo", "/usr/local/bin/docker", "exec", "katsu-web-v2"] + base_cmd
    else:
        # PC 或 katsu-scripts container 內（已裝 ffmpeg/ffprobe）
        cmd = base_cmd
    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            out, _ = p.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
            return None
        if p.returncode != 0:
            # ffprobe error（檔案損毀、權限、I/O error）→ retry
            return None
        try:
            info = json.loads(out.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            return None
        streams = info.get("streams", [])
        if not streams:
            # ffprobe 成功但無 video stream → audio-only，永遠不會有 dims
            return (0, 0)
        s = streams[0]
        if "width" not in s or "height" not in s:
            return (0, 0)
        w = int(s["width"])
        h = int(s["height"])
        # Rotation can live in tags.rotate or side_data.rotation
        rot = 0
        tags = s.get("tags") or {}
        if "rotate" in tags:
            try: rot = int(tags["rotate"])
            except: pass
        for sd in s.get("side_data_list", []) or []:
            if "rotation" in sd:
                try: rot = int(sd["rotation"])
                except: pass
        if rot in (90, -90, 270, -270):
            w, h = h, w
        return w, h
    except Exception:
        return None


def process_nas():
    client = sb()
    print("Fetching NAS rows without dimensions...", flush=True)
    all_rows = []
    PAGE = 1000
    offset = 0
    while True:
        r = client.table("videos") \
            .select("drive_file_id,nas_share_url") \
            .eq("source", "nas") \
            .is_("width", "null") \
            .range(offset, offset + PAGE - 1).execute()
        rows = r.data or []
        all_rows.extend(rows)
        if len(rows) < PAGE:
            break
        offset += PAGE
    print(f"  {len(all_rows)} NAS rows need backfill", flush=True)

    if not all_rows:
        return

    pending: list[tuple[str, int, int]] = []
    stats = {"ok": 0, "nopath": 0, "fail": 0}

    def work(row):
        stored = (row.get("nas_share_url") or "").strip()
        if not stored:
            return row["drive_file_id"], None, "nopath"
        p = resolve_nas_path(stored)
        if not p:
            return row["drive_file_id"], None, "nopath"
        dims = ffprobe_dims(p)
        if dims is None:
            return row["drive_file_id"], None, "probefail"
        return row["drive_file_id"], dims, None

    FLUSH_EVERY = 500
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=NAS_WORKERS) as ex:
        futures = [ex.submit(work, row) for row in all_rows]
        for fut in as_completed(futures):
            fid, dims, err = fut.result()
            done += 1
            if dims:
                pending.append((fid, dims[0], dims[1]))
                stats["ok"] += 1
            elif err == "nopath":
                stats["nopath"] += 1
            else:
                stats["fail"] += 1
            if done % 50 == 0:
                rate = done / max(time.time() - t0, 0.01)
                eta = (len(all_rows) - done) / max(rate, 0.01)
                print(f"  probed {done}/{len(all_rows)}  ok={stats['ok']} nopath={stats['nopath']} fail={stats['fail']}  {rate:.1f}/s  eta={eta/60:.1f}min", flush=True)
            if len(pending) >= FLUSH_EVERY:
                print(f"  → flushing {len(pending)} rows to DB...", flush=True)
                retry = write_updates(client, pending)
                pending[:] = retry

    if pending:
        print(f"  → final flush {len(pending)} rows...", flush=True)
        retry = write_updates(client, pending)
        # last-ditch: try once more serially
        for fid, w, h in retry:
            for attempt in range(5):
                try:
                    client.table("videos").update({"width": w, "height": h}).eq("drive_file_id", fid).execute()
                    break
                except Exception:
                    time.sleep(1 * (attempt + 1))
    print(f"  final: ok={stats['ok']} nopath={stats['nopath']} fail={stats['fail']}", flush=True)


SA_PATH = (
    "/volume2/docker-prod/scripts/原初映像片庫/service_account.json"
    if IS_NAS else
    r"C:\Users\rendy\service_account.json"
)


# ---------- Drive ----------
def process_drive():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print("Drive libs not installed; skipping drive backfill")
        return

    client = sb()
    print("Fetching Drive rows without dimensions...")
    all_rows = []
    PAGE = 1000
    offset = 0
    while True:
        r = client.table("videos") \
            .select("drive_file_id") \
            .eq("source", "drive") \
            .is_("width", "null") \
            .range(offset, offset + PAGE - 1).execute()
        rows = r.data or []
        all_rows.extend(rows)
        if len(rows) < PAGE:
            break
        offset += PAGE
    print(f"  {len(all_rows)} Drive rows need backfill")
    if not all_rows:
        return

    creds = service_account.Credentials.from_service_account_file(SA_PATH, scopes=["https://www.googleapis.com/auth/drive"])
    svc = build("drive", "v3", credentials=creds)

    results: list[tuple[str, int, int]] = []
    failed: list[str] = []
    no_meta = 0  # Drive 自己沒生 videoMediaMetadata，標 sentinel
    done = 0
    for row in all_rows:
        fid = row["drive_file_id"]
        try:
            r = svc.files().get(fileId=fid, fields="id,videoMediaMetadata", supportsAllDrives=True).execute()
            md = r.get("videoMediaMetadata") or {}
            w = md.get("width")
            h = md.get("height")
            if w and h:
                results.append((fid, int(w), int(h)))
            else:
                # Drive 自己沒生 videoMediaMetadata（多半是業配 raw material / Drive 沒分析過）
                # → 標 sentinel (0,0)，下次 backfill 不再 retry
                results.append((fid, 0, 0))
                no_meta += 1
        except Exception as e:
            # SA 沒權限 / API error / 檔案被刪 → 保留 NULL 下次再試
            failed.append(fid)
        done += 1
        if done % 100 == 0:
            print(f"  fetched {done}/{len(all_rows)} ok={len(results)-no_meta} no_meta={no_meta} fail={len(failed)}")

    print(f"  final: ok={len(results)-no_meta} no_meta={no_meta} fail={len(failed)}")
    print("Writing to Supabase...")
    write_updates(client, results)


# ---------- Update ----------
def write_updates(client, items: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Update rows with retry on transient socket errors.
    Returns list of rows that permanently failed (caller should re-queue)."""
    if not items:
        return []
    failed: list[tuple[str, int, int]] = []
    lock = __import__("threading").Lock()

    def worker(item):
        fid, w, h = item
        for attempt in range(5):
            try:
                client.table("videos").update({"width": w, "height": h}).eq("drive_file_id", fid).execute()
                return
            except Exception as e:
                msg = str(e)
                if "10035" in msg or "10060" in msg or "ConnectionError" in msg or "RemoteDisconnected" in msg:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                print(f"  update fail {fid}: {e}", flush=True)
                break
        with lock:
            failed.append(item)

    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(worker, items))
    if failed:
        print(f"  {len(failed)} rows will be retried next flush", flush=True)
    return failed


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    t0 = time.time()
    if mode in ("nas", "both"):
        process_nas()
    if mode in ("drive", "both"):
        process_drive()
    print(f"Done in {time.time()-t0:.1f}s")
