"""Person detection on videos to tag 有主持人 / 無主持人 (NAS-side version).

Differences vs PC version:
  - LOCAL_PREFIX = /volume2/homes/ETtomorrow/ (same as nas_share_url, no SMB)
  - Designed to run inside docker container on NAS
  - Saves checkpoint per N videos to allow resume
"""
import argparse
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8")

import cv2
import numpy as np
from supabase import create_client

ENV_PATH = Path(__file__).parent / ".env"
env = {}
for line in ENV_PATH.read_text().splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

sb = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

# 多 volume 支援：用 nas_roots.convert_path 取代固定 prefix 替換
from nas_roots import convert_path

PERSON_SCORE = 0.4
PERSON_AREA_MIN = 0.005
FRAME_FRACTIONS = (0.05, 0.18, 0.30, 0.42, 0.55, 0.68, 0.80, 0.92)
DONE_TAGS = {"有主持人", "無主持人"}
HOST_TAG_RULES = {"主持人台詞", "主持人"}
NO_HOST_TAG_RULES = {"空拍素材 (重要)", "空拍素材", "Drone", "drone"}
# 移除「空景」「空景拍帶」: 主持人有時會走進這些畫面，改用 YOLO 偵測判斷


def nas_to_local(nas_path):
    """把 DB 中的 NAS Linux 路徑轉成 container 內 bind mount 路徑"""
    if not nas_path:
        return None
    return convert_path(nas_path, target_platform="docker")


def ffprobe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20)
        return float(out.stdout.strip())
    except Exception:
        return None


def has_person_yolo(model, img):
    """img: numpy array (BGR). Returns True if YOLO finds person box meeting thresholds."""
    if img is None:
        return False
    h, w = img.shape[:2]
    frame_area = w * h
    results = model.predict(img, classes=[0], conf=PERSON_SCORE, verbose=False)
    if not results:
        return False
    for r in results:
        if r.boxes is None or len(r.boxes) == 0:
            continue
        for box in r.boxes:
            xyxy = box.xyxy[0].cpu().numpy()
            bw = xyxy[2] - xyxy[0]
            bh = xyxy[3] - xyxy[1]
            if (bw * bh) / frame_area >= PERSON_AREA_MIN:
                return True
    return False


def classify_by_tags(tags):
    if not tags:
        return None
    s = set(tags)
    if s & DONE_TAGS:
        return None
    if s & HOST_TAG_RULES:
        return "有主持人"
    if s & NO_HOST_TAG_RULES:
        return "無主持人"
    return None


def detect_video(model, local_path, _tmp_dir=None):
    """Use OpenCV VideoCapture to seek+read frames in-process (avoids 8x ffmpeg subprocess)."""
    if not os.path.exists(local_path):
        return None, "file_missing"
    cap = cv2.VideoCapture(local_path)
    if not cap.isOpened():
        return None, "open_failed"
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        if total <= 0 or fps <= 0:
            return None, "no_duration"
        found = False
        frames_ok = 0
        for frac in FRAME_FRACTIONS:
            target = int(total * frac)
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            frames_ok += 1
            # downscale long edge to 416 for faster YOLO + smaller memory
            h, w = frame.shape[:2]
            longest = max(h, w)
            if longest > 416:
                scale = 416 / longest
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            if has_person_yolo(model, frame):
                found = True
                break
        if frames_ok == 0:
            return None, "no_frames"
        return found, "ok"
    finally:
        cap.release()


def fetch_pending(limit):
    pending = []
    page = 1000
    offset = 0
    while True:
        r = (sb.table("videos").select("drive_file_id, tags, nas_share_url, source")
             .eq("source", "nas").range(offset, offset + page - 1).execute())
        if not r.data:
            break
        for v in r.data:
            tags = v.get("tags") or []
            if set(tags) & DONE_TAGS:
                continue
            if not v.get("nas_share_url"):
                continue
            pending.append(v)
            if len(pending) >= limit:
                return pending
        if len(r.data) < page:
            break
        offset += page
    return pending


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10**9)
    ap.add_argument("--shard", default="0/1", help="N/M — process only shard N out of M (hash split)")
    ap.add_argument("--watch", action="store_true",
                    help="loop forever; sleep 600s when pending is empty")
    ap.add_argument("--watch-sleep", type=int, default=600)
    args = ap.parse_args()
    shard_n, shard_m = (int(x) for x in args.shard.split("/"))

    print("Loading YOLOv8n model...", flush=True)
    from ultralytics import YOLO
    model = YOLO(str(Path(__file__).parent / "yolov8n.pt"))
    print("  model ready", flush=True)

    def run_one_pass():
        print(f"Fetching pending videos (limit={args.limit}, shard={args.shard})...", flush=True)
        videos = fetch_pending(args.limit)
        if shard_m > 1:
            import zlib
            videos = [v for v in videos
                      if zlib.crc32(v["drive_file_id"].encode("utf-8")) % shard_m == shard_n]
            print(f"  shard {shard_n}/{shard_m}: {len(videos)} videos to process (others skipped)", flush=True)
        print(f"To process: {len(videos)}", flush=True)
        if not videos:
            return 0

        yes = no = err = rule = 0
        t0 = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            for i, v in enumerate(videos, 1):
                verdict = classify_by_tags(v.get("tags"))
                if verdict is not None:
                    tag = verdict
                    mark = "R+" if tag == "有主持人" else "R-"
                    reason = "rule"
                    rule += 1
                else:
                    local = nas_to_local(v["nas_share_url"])
                    d, reason = detect_video(model, local, tmp)
                    if d is None:
                        err += 1; tag = None; mark = "??"
                    elif d:
                        yes += 1; tag = "有主持人"; mark = "Y "
                    else:
                        no += 1; tag = "無主持人"; mark = "  "

                elapsed = time.time() - t0
                rate = i / max(elapsed, 1e-6)
                eta = (len(videos) - i) / max(rate, 1e-6)
                label = (v["nas_share_url"] or "")[-60:]
                print(f"[{i:>5}/{len(videos)}] {mark} {reason:<12} {label}  | "
                      f"yes={yes} no={no} rule={rule} err={err}  rate={rate:.2f}/s eta={eta/60:.0f}m",
                      flush=True)

                if tag is None:
                    continue
                new_tags = list(set((v.get("tags") or []) + [tag]))
                try:
                    row = sb.table("videos").select(
                        "channel_name, case_name, subpath, filename, city, district"
                    ).eq("drive_file_id", v["drive_file_id"]).single().execute()
                    d = row.data or {}
                    pair = (
                        f"{d.get('city','')} {d.get('district','')}".strip()
                        if d.get("city") and d.get("district") else ""
                    )
                    search = " ".join(p for p in [
                        d.get("channel_name") or "", d.get("case_name") or "",
                        d.get("subpath") or "", d.get("filename") or "",
                        " ".join(new_tags), d.get("city") or "", d.get("district") or "",
                        pair,
                    ] if p)
                    sb.table("videos").update({
                        "tags": new_tags, "search_text": search,
                    }).eq("drive_file_id", v["drive_file_id"]).execute()
                except Exception as e:
                    print(f"  ! db update failed: {e}", flush=True)

        print(f"\nPASS DONE  yes={yes} no={no} rule={rule} err={err} total={len(videos)} "
              f"elapsed={(time.time()-t0)/60:.1f}m", flush=True)
        return len(videos)

    if args.watch:
        while True:
            run_one_pass()
            print(f"[watch] sleeping {args.watch_sleep}s before next scan...", flush=True)
            time.sleep(args.watch_sleep)
    else:
        run_one_pass()


if __name__ == "__main__":
    main()
