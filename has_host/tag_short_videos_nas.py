"""NAS-side: probe NAS videos and tag those < 3 seconds with 「短影3秒」.

Watch mode: loops, sleeps 600s when nothing new.
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import cv2
from supabase import create_client

ENV_PATH = Path(__file__).parent / ".env"
env = {}
for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
sb = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

# 多 volume 支援
from nas_roots import convert_path

SHORT_TAG = "短影3秒"
THRESHOLD_SEC = 3.0


def nas_to_local(p):
    if not p:
        return None
    return convert_path(p, target_platform="docker")


def probe_duration(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    try:
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        if total <= 0 or fps <= 0:
            return None
        return total / fps
    finally:
        cap.release()


def fetch_pending():
    out = []
    last = ""
    while True:
        q = (sb.table("videos")
             .select("drive_file_id, tags, nas_share_url")
             .eq("source", "nas")
             .order("drive_file_id")
             .limit(1000))
        if last:
            q = q.gt("drive_file_id", last)
        r = q.execute()
        if not r.data:
            break
        for v in r.data:
            tags = v.get("tags") or []
            if SHORT_TAG in tags:
                continue
            if not v.get("nas_share_url"):
                continue
            out.append(v)
        last = r.data[-1]["drive_file_id"]
        if len(r.data) < 1000:
            break
    return out


def process(v):
    local = nas_to_local(v["nas_share_url"])
    if not local or not os.path.exists(local):
        return v, None
    dur = probe_duration(local)
    if dur is None:
        return v, None
    return v, dur < THRESHOLD_SEC


def run_one_pass(workers):
    videos = fetch_pending()
    print(f"[pass] to probe: {len(videos)}", flush=True)
    if not videos:
        return 0
    short = long = err = 0
    completed = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(process, v) for v in videos]
        for fut in as_completed(futs):
            v, is_short = fut.result()
            completed += 1
            if is_short is None:
                err += 1
            elif is_short:
                short += 1
                tags = list(set((v.get("tags") or []) + [SHORT_TAG]))
                try:
                    sb.table("videos").update({"tags": tags}).eq(
                        "drive_file_id", v["drive_file_id"]
                    ).execute()
                except Exception as e:
                    print(f"  ! db update failed: {e}", flush=True)
            else:
                long += 1
            if completed % 200 == 0:
                rate = completed / max(time.time() - t0, 1e-6)
                eta = (len(videos) - completed) / max(rate, 1e-6)
                print(f"  {completed}/{len(videos)}  short={short} long={long} err={err}  "
                      f"rate={rate:.1f}/s eta={eta/60:.0f}m", flush=True)
    print(f"[pass done] short={short} long={long} err={err}", flush=True)
    return len(videos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--watch-sleep", type=int, default=600)
    args = ap.parse_args()

    if args.watch:
        while True:
            run_one_pass(args.workers)
            print(f"[watch] sleep {args.watch_sleep}s", flush=True)
            time.sleep(args.watch_sleep)
    else:
        run_one_pass(args.workers)


if __name__ == "__main__":
    main()
