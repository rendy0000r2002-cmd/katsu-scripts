"""Phase 2: Detect has_host on Google Drive source videos.

Streams partial bytes via ffmpeg with Authorization header — only the bytes
needed for the 8 sample frames get downloaded (HTTP Range), saving time vs
full download.

Auth: Service Account (sacred-union-277206 / katsu-drive-bot).
The 房產 folder must be shared with the SA email.

Run on PC (CUDA YOLO).

Usage:
  python detect_has_host_drive.py [--limit N] [--workers 4]
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import cv2
import jwt as pyjwt
import numpy as np
import requests
from supabase import create_client

ENV_PATH = Path(__file__).parent / ".env"
env = {}
for line in ENV_PATH.read_text().splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

sb = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

SA_PATH = Path(__file__).parent / "service_account.json"
with SA_PATH.open() as f:
    _SA = json.load(f)

PERSON_SCORE = 0.4
PERSON_AREA_MIN = 0.005
FRAME_FRACTIONS = (0.05, 0.18, 0.30, 0.42, 0.55, 0.68, 0.80, 0.92)
DONE_TAGS = {"有主持人", "無主持人", "非建案"}

_token_lock = threading.Lock()
_token_state = {"access_token": None, "expires_at": 0.0}


def get_access_token():
    with _token_lock:
        if _token_state["access_token"] and time.time() < _token_state["expires_at"] - 60:
            return _token_state["access_token"]
        now = int(time.time())
        claim = {
            "iss": _SA["client_email"],
            "scope": "https://www.googleapis.com/auth/drive",
            "aud": _SA["token_uri"],
            "iat": now,
            "exp": now + 3600,
        }
        assertion = pyjwt.encode(
            claim,
            _SA["private_key"],
            algorithm="RS256",
            headers={"kid": _SA["private_key_id"]},
        )
        r = requests.post(
            _SA["token_uri"],
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=20,
        )
        r.raise_for_status()
        j = r.json()
        _token_state["access_token"] = j["access_token"]
        _token_state["expires_at"] = time.time() + j.get("expires_in", 3600)
        return _token_state["access_token"]


def drive_url(file_id):
    return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"


def ffprobe_duration_url(url, token):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-headers", f"Authorization: Bearer {token}\r\n",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", url],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def extract_frame_url(url, token, ts):
    """Returns numpy BGR or None."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error",
             "-headers", f"Authorization: Bearer {token}\r\n",
             "-ss", f"{ts:.2f}", "-i", url,
             "-frames:v", "1", "-vf", "scale=416:-2",
             "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0 or not r.stdout:
            return None
        arr = np.frombuffer(r.stdout, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def has_person_yolo(model, img):
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


def detect_drive_video(model, yolo_lock, file_id):
    token = get_access_token()
    url = drive_url(file_id)
    dur = ffprobe_duration_url(url, token)
    if not dur or dur < 0.5:
        return None, "no_duration"
    for frac in FRAME_FRACTIONS:
        ts = max(0.1, dur * frac)
        frame = extract_frame_url(url, token, ts)
        if frame is None:
            continue
        with yolo_lock:
            hit = has_person_yolo(model, frame)
        if hit:
            return True, "ok"
    return False, "ok"


def fetch_pending(limit):
    pending = []
    page = 1000
    offset = 0
    while True:
        r = (sb.table("videos")
             .select("drive_file_id, tags, filename")
             .eq("source", "drive")
             .range(offset, offset + page - 1)
             .execute())
        if not r.data:
            break
        for v in r.data:
            tags = v.get("tags") or []
            if set(tags) & DONE_TAGS:
                continue
            pending.append(v)
            if len(pending) >= limit:
                return pending
        if len(r.data) < page:
            break
        offset += page
    return pending


def write_tag(v, tag):
    if tag is None:
        return
    new_tags = list(set((v.get("tags") or []) + [tag]))
    try:
        row = sb.table("videos").select(
            "channel_name, case_name, subpath, filename, city, district"
        ).eq("drive_file_id", v["drive_file_id"]).single().execute()
        d = row.data or {}
        pair = (f"{d.get('city','')} {d.get('district','')}".strip()
                if d.get("city") and d.get("district") else "")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10**9)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--watch", action="store_true",
                    help="loop forever; sleep 600s when pending is empty")
    ap.add_argument("--watch-sleep", type=int, default=600)
    args = ap.parse_args()

    print("Loading YOLOv8n model...")
    from ultralytics import YOLO
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu"
    model_path = str(Path(__file__).parent / "yolov8n.pt")
    if not os.path.exists(model_path):
        model_path = "yolov8n.pt"
    model = YOLO(model_path)
    model.to(device)
    print(f"  device: {device}")

    print("Authenticating Drive (service account)...")
    get_access_token()  # warm token
    print(f"  ok, SA={_SA['client_email']}")

    yolo_lock = threading.Lock()

    def run_one_pass():
        videos = fetch_pending(args.limit)
        print(f"To process: {len(videos)}", flush=True)
        if not videos:
            return 0
        counts = {"yes": 0, "no": 0, "err": 0}
        counts_lock = threading.Lock()
        t0 = time.time()
        completed = 0

        def process_one(v):
            d, reason = detect_drive_video(model, yolo_lock, v["drive_file_id"])
            return v, d, reason

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(process_one, v) for v in videos]
            for fut in as_completed(futs):
                v, d, reason = fut.result()
                completed += 1
                if d is None:
                    tag = None; mark = "??"
                    with counts_lock: counts["err"] += 1
                elif d:
                    tag = "有主持人"; mark = "Y "
                    with counts_lock: counts["yes"] += 1
                else:
                    tag = "無主持人"; mark = "  "
                    with counts_lock: counts["no"] += 1
                write_tag(v, tag)
                elapsed = time.time() - t0
                rate = completed / max(elapsed, 1e-6)
                eta = (len(videos) - completed) / max(rate, 1e-6)
                print(f"[{completed:>5}/{len(videos)}] {mark} {reason:<12} {v['filename'][:60]}  | "
                      f"yes={counts['yes']} no={counts['no']} err={counts['err']}  "
                      f"rate={rate:.2f}/s eta={eta/60:.0f}m", flush=True)

        print(f"\nPASS DONE  yes={counts['yes']} no={counts['no']} err={counts['err']} "
              f"total={len(videos)} elapsed={(time.time()-t0)/60:.1f}m", flush=True)
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
