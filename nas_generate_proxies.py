"""
Phase 2 — Generate H.264 mp4 proxies for codecs that browsers can't play.
Runs on NAS host (/volume2/ direct paths; v1 已搬 V2). Skips files with existing proxy.
"""
import os, sys, json, hashlib, subprocess, time, urllib.request, urllib.parse

SUPABASE_URL = os.environ["SUPABASE_URL"]
SERVICE_KEY  = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

NEEDS_PROXY = ["prores", "prores_ks", "dnxhd", "dnxhr", "cineform",
               "mjpeg", "rawvideo", "v210", "v410", "qtrle", "png"]

PROXY_ROOT = "/volume2/homes/ETtomorrow/_proxies"
os.makedirs(PROXY_ROOT, exist_ok=True)

HEADERS_BASE = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
}

def http(method, url, body=None, extra=None):
    headers = dict(HEADERS_BASE)
    if extra:
        headers.update(extra)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None

def fetch_candidates():
    rows = []
    offset = 0
    page = 1000
    codec_filter = ",".join(f'"{c}"' for c in NEEDS_PROXY)
    while True:
        url = (
            f"{SUPABASE_URL}/rest/v1/videos"
            "?select=drive_file_id,nas_share_url,filename,codec"
            "&source=eq.nas"
            f"&codec=in.({codec_filter})"
            "&nas_share_url=not.is.null"
            f"&offset={offset}&limit={page}"
        )
        chunk = http("GET", url)
        rows.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
    return rows

def to_linux(s: str):
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

def proxy_path(drive_id):
    h = hashlib.sha1(drive_id.encode()).hexdigest()
    return os.path.join(PROXY_ROOT, f"{h}.mp4")

SUDO_PASS = os.environ.get("SUDO_PASS", "")

def transcode(src, dst, log_prefix=""):
    """Run ffmpeg INSIDE katsu-web container (host ffmpeg 4.1.9 has h264/hevc disabled)."""
    tmp = dst + ".tmp.mp4"
    # Inside container: /volume2 mounted ro, /proxies mounted rw -> /volume2/homes/ETtomorrow/_proxies
    container_dst = tmp.replace(PROXY_ROOT, "/proxies")
    container_src = src  # /volume2/... is the same inside container
    inner = (
        "ffmpeg -y -i {sq_src} "
        "-c:v libx264 -preset fast -crf 23 "
        "-vf 'scale=if(gt(iw\\,ih)\\,-2\\,720):if(gt(iw\\,ih)\\,720\\,-2)' "
        "-c:a aac -b:a 128k "
        "-movflags +faststart "
        "-loglevel error "
        "{sq_dst}"
    ).format(sq_src=shquote(container_src), sq_dst=shquote(container_dst))
    cmd = (
        f"echo {SUDO_PASS} | sudo -S -p '' bash -lc "
        f"\"docker exec katsu-web sh -c {shquote(inner)}\""
    )
    try:
        r = subprocess.run(["bash", "-lc", cmd], capture_output=True, timeout=1800)
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, dst)
            return True, None
        if os.path.exists(tmp):
            os.remove(tmp)
        err = (r.stderr.decode("utf-8", errors="replace")
               + r.stdout.decode("utf-8", errors="replace"))[-300:]
        return False, err
    except subprocess.TimeoutExpired:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, "timeout 30min"
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, str(e)

def shquote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"

def main():
    print(f"[query] fetching candidates (codec in {NEEDS_PROXY})...", flush=True)
    rows = fetch_candidates()
    print(f"[query] candidates: {len(rows)}", flush=True)

    done = fail = skip = miss = 0
    start = time.time()
    for i, row in enumerate(rows, 1):
        dst = proxy_path(row["drive_file_id"])
        if os.path.exists(dst):
            skip += 1
            continue
        src = to_linux(row.get("nas_share_url") or "")
        if not src:
            miss += 1
            continue
        elapsed = time.time() - start
        rate = (done + 1) / max(elapsed, 1)
        remaining = len(rows) - i
        eta_min = int(remaining / max(rate, 1e-6) / 60)
        print(f"[{i}/{len(rows)}] {row['filename']} ({row['codec']})  "
              f"done={done} fail={fail} skip={skip}  ETA {eta_min}m", flush=True)
        ok, err = transcode(src, dst)
        if ok:
            sz_mb = os.path.getsize(dst) // 1024 // 1024
            done += 1
            print(f"  -> proxy {sz_mb}MB", flush=True)
        else:
            fail += 1
            print(f"  FAIL: {err}", flush=True)

    print(f"\n=== DONE ===", flush=True)
    print(f"total candidates: {len(rows)}", flush=True)
    print(f"transcoded:       {done}", flush=True)
    print(f"already had proxy: {skip}", flush=True)
    print(f"path not found:    {miss}", flush=True)
    print(f"failed:           {fail}", flush=True)
    print(f"elapsed:          {(time.time()-start)/60:.1f} min", flush=True)

if __name__ == "__main__":
    main()
