"""
Run on NAS via paramiko. Reads candidates from PC-side Supabase via REST,
runs ffprobe on local /volume2/ paths (v1 已搬 V2), writes back via REST PATCH.
"""
import os, json, subprocess, urllib.request, time, sys

SUPABASE_URL = os.environ["SUPABASE_URL"]
SERVICE_KEY  = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

HEADERS_BASE = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
}

def _http(method, url, body=None, extra_headers=None):
    headers = dict(HEADERS_BASE)
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None

def fetch_candidates():
    out = []
    offset = 0
    page = 1000
    while True:
        url = (
            f"{SUPABASE_URL}/rest/v1/videos"
            "?select=drive_file_id,nas_share_url,filename,codec"
            "&source=eq.nas"
            "&ext=ilike.mov"
            "&or=(codec.is.null,codec.eq.UNKNOWN)"
            f"&offset={offset}&limit={page}"
        )
        rows = _http("GET", url, extra_headers={"Range": ""})
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out

def update_codec(drive_id, codec, w, h):
    url = f"{SUPABASE_URL}/rest/v1/videos?drive_file_id=eq.{urllib.parse.quote(drive_id, safe='')}"
    body = {"codec": codec}
    if w: body["width"] = w
    if h: body["height"] = h
    _http("PATCH", url, body=body, extra_headers={"Prefer": "return=minimal"})

def to_linux_path(s: str):
    if not s:
        return None
    s = s.replace("\\", "/")
    cands = []
    if s.lower().startswith("y:/"):
        cands.append("/volume2/homes/ETtomorrow/" + s[7:] if s.lower().startswith("y:/home/") else "/volume2/" + s[3:])
    if s.lower().startswith("u:/home/"):
        cands.append("/volume2/homes/ETtomorrow/" + s[8:])
    if s.lower().startswith("u:/"):
        cands.append("/volume2/homes/ETtomorrow/" + s[3:])
    if s.startswith("/volume1/"):
        cands.append(s)
    if s.startswith("/volume2/"):
        cands.append(s)
    for c in cands:
        if os.path.exists(c):
            return c
    return None

def normalize(c):
    c = (c or "").strip().lower()
    if c in ("h264", "avc1", "h.264"): return "h264"
    if c in ("hevc", "h265", "h.265", "hev1", "hvc1"): return "hevc"
    if c in ("mpeg4", "mp4v"): return "mpeg4"
    return c or "UNKNOWN"

import re
_CODEC_RE = re.compile(r"Stream #0:\d+(?:\([^)]*\))?: Video: (\w+)")
_DIM_RE = re.compile(r"(\d{2,5})x(\d{2,5})")

def ffprobe(path, debug=False):
    """Use ffmpeg -i (no encode) to probe; ffprobe not on Synology NAS PATH."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", path],
            capture_output=True, timeout=20,
        )
        text = r.stderr.decode("utf-8", errors="replace")
        codec = None
        w = h = None
        for line in text.splitlines():
            if "Video:" in line:
                m = _CODEC_RE.search(line)
                if m:
                    codec = m.group(1)
                d = _DIM_RE.search(line)
                if d:
                    w, h = int(d.group(1)), int(d.group(2))
                break
        if not codec and debug:
            sys.stderr.write(f"DEBUG no codec for {path}: {text[:400]}\n")
        if not codec:
            return None, None, None
        return normalize(codec), w, h
    except Exception as e:
        if debug:
            sys.stderr.write(f"DEBUG exception {path}: {e}\n")
        return None, None, None

import urllib.parse
def main():
    print("[query] fetching candidates...", flush=True)
    cands = fetch_candidates()
    print(f"[query] candidates: {len(cands)}", flush=True)
    by_codec = {}
    ok = miss = fail = 0
    start = time.time()
    for i, row in enumerate(cands, 1):
        path = to_linux_path(row.get("nas_share_url") or "")
        if not path:
            miss += 1
            continue
        codec, w, h = ffprobe(path, debug=(fail < 5))
        if not codec:
            fail += 1
            continue
        try:
            update_codec(row["drive_file_id"], codec, w, h)
            ok += 1
            by_codec[codec] = by_codec.get(codec, 0) + 1
        except Exception as e:
            print(f"  [{i}] update failed: {e}", flush=True)
            fail += 1
        if i % 25 == 0:
            eta = (time.time()-start)/i*(len(cands)-i)
            print(f"  [{i}/{len(cands)}] ok={ok} fail={fail} miss={miss}  ETA {int(eta)}s", flush=True)
    print(f"\n=== DONE ===", flush=True)
    print(f"total candidates: {len(cands)}", flush=True)
    print(f"successfully filled: {ok}", flush=True)
    print(f"path not found: {miss}", flush=True)
    print(f"ffprobe/update failed: {fail}", flush=True)
    print(f"\ncodec distribution:", flush=True)
    for k, v in sorted(by_codec.items(), key=lambda x: -x[1]):
        print(f"  {k:20s} {v}", flush=True)
    print(f"\nelapsed: {(time.time()-start):.1f}s", flush=True)

if __name__ == "__main__":
    main()
