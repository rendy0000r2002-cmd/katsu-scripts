"""SAFE v3: De-duplicate NAS videos older than 3 months — using QUARANTINE.

Per codex review v2:
  - require width/height/codec all present (skip groups with null metadata)
  - require content match via 3-point partial SHA256 (head + middle + tail, 1MB each)
  - canonicalize path on NAS (readlink -f) and verify still under NAS_PREFIX
  - re-verify keeper before each delete: exists, size matches DB, mtime > 90d, probe-able
  - re-stat each target before delete: exists, size matches DB, mtime > 90d, hash matches keeper
  - DB delete restricted to (drive_file_id AND source='nas' AND nas_share_url=path)
  - DON'T rm; mv to /volume2/homes/ETtomorrow/.dedupe_quarantine/<timestamp>/<orig_path>
  - manifest.json saved alongside quarantine, can rollback later

Usage:
  python dedupe_old_videos.py            # dry-run (default)
  python dedupe_old_videos.py --execute  # quarantine + DB delete
"""
import argparse
import json
import os
import shlex
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import cv2
import paramiko
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
from nas_roots import ALL_ROOTS, find_root_for, convert_path

# 接受的 NAS prefix 清單（用於驗證路徑是否合法）
NAS_PREFIXES = tuple(r.linux.rstrip("/") + "/" for r in ALL_ROOTS)
# 各 volume 的 quarantine 區位於該 volume 自己的 root 下，避免跨 volume 搬檔
QUARANTINE_PER_VOLUME = {
    r.volume: r.linux.rstrip("/") + "/.dedupe_quarantine" for r in ALL_ROOTS
}
NOW = datetime.now(timezone.utc)
THREE_MONTHS_AGO = NOW - timedelta(days=90)
HASH_SAMPLE_BYTES = 1024 * 1024  # 1MB at each of head/mid/tail


def nas_to_local(nas_path):
    if not nas_path:
        return None
    local = convert_path(nas_path, target_platform="win")
    if not local:
        return None
    return local.replace("/", os.sep)


def quarantine_base_for(nas_path: str) -> str | None:
    """根據 nas_path 落在哪個 volume，回該 volume 的 quarantine root"""
    r = find_root_for(nas_path)
    if not r:
        return None
    return QUARANTINE_PER_VOLUME[r.volume]


def probe_playable(local_path):
    """Open via cv2, sample 3 frames at 10/50/90%. True iff all 3 read OK."""
    if not local_path or not os.path.exists(local_path):
        return False
    cap = cv2.VideoCapture(local_path)
    try:
        if not cap.isOpened():
            return False
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        if total <= 0 or fps <= 0:
            return False
        for frac in (0.10, 0.50, 0.90):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * frac))
            ret, frame = cap.read()
            if not ret or frame is None:
                return False
        return True
    finally:
        cap.release()


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fetch_all_nas():
    out = []
    last_id = ""
    while True:
        q = (sb.table("videos")
             .select("drive_file_id, filename, rel_path, source, size_bytes, "
                     "mtime, nas_share_url, width, height, codec")
             .eq("source", "nas")
             .gt("size_bytes", 0)
             .order("drive_file_id")
             .limit(1000))
        if last_id:
            q = q.gt("drive_file_id", last_id)
        r = q.execute()
        if not r.data:
            break
        out.extend(r.data)
        last_id = r.data[-1]["drive_file_id"]
        if len(r.data) < 1000:
            break
    return out


def nas_canon(ssh, path):
    """Resolve path via readlink -f. Returns canonical path or None."""
    cmd = f"readlink -f {shlex.quote(path)}"
    _, stdout, _ = ssh.exec_command(cmd, timeout=15)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    return out or None


def nas_stat(ssh, path):
    """Returns (exists, size, mtime_dt). Uses canonical path."""
    canon = nas_canon(ssh, path)
    if not canon or not canon.startswith(NAS_PREFIXES):
        return False, None, None, canon
    cmd = f"if [ -f {shlex.quote(canon)} ]; then stat --printf='%s|%Y' {shlex.quote(canon)}; else echo MISSING; fi"
    _, stdout, _ = ssh.exec_command(cmd, timeout=15)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    if "MISSING" in out or "|" not in out:
        return False, None, None, canon
    sz_s, ep_s = out.split("|", 1)
    try:
        sz = int(sz_s)
        ep = int(ep_s)
        return True, sz, datetime.fromtimestamp(ep, tz=timezone.utc), canon
    except Exception:
        return False, None, None, canon


def nas_partial_hash(ssh, path, size):
    """SHA256 of head + middle + tail samples. Cheap content fingerprint."""
    if size < 3 * HASH_SAMPLE_BYTES:
        # File too small — hash whole file
        cmd = f"sha256sum {shlex.quote(path)} | cut -d' ' -f1"
    else:
        mid = (size // 2) - (HASH_SAMPLE_BYTES // 2)
        cmd = (
            f"(dd if={shlex.quote(path)} bs=1 count={HASH_SAMPLE_BYTES} 2>/dev/null; "
            f"dd if={shlex.quote(path)} bs=1 skip={mid} count={HASH_SAMPLE_BYTES} 2>/dev/null; "
            f"dd if={shlex.quote(path)} bs=1 skip={size - HASH_SAMPLE_BYTES} count={HASH_SAMPLE_BYTES} 2>/dev/null"
            f") | sha256sum | cut -d' ' -f1"
        )
    _, stdout, _ = ssh.exec_command(cmd, timeout=120)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    return out if len(out) == 64 else None


def nas_quarantine_move(ssh, src, qbases_by_volume, manifest):
    """Move src to <qbase-of-src-volume>/<rel_path>. Updates manifest dict."""
    r = find_root_for(src)
    if not r:
        return False, "outside prefix"
    prefix = r.linux.rstrip("/") + "/"
    if not src.startswith(prefix):
        return False, "outside prefix"
    qbase = qbases_by_volume.get(r.volume)
    if not qbase:
        return False, f"no quarantine base for {r.volume}"
    rel = src[len(prefix):]
    dst = f"{qbase}/{rel}"
    dst_dir = "/".join(dst.split("/")[:-1])
    cmd = (
        f"mkdir -p {shlex.quote(dst_dir)} && "
        f"mv -n {shlex.quote(src)} {shlex.quote(dst)} && "
        f"echo OK"
    )
    _, stdout, _ = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    if "OK" not in out:
        return False, out
    manifest.append({"src": src, "dst": dst, "ts": NOW.isoformat()})
    return True, "moved"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="actually move to quarantine (default: dry-run)")
    args = ap.parse_args()
    dry = not args.execute
    print(f"Mode: {'DRY-RUN' if dry else 'EXECUTE (moves files to quarantine)'}")

    print("Fetching NAS videos (size > 0)...")
    all_v = fetch_all_nas()
    print(f"  {len(all_v)} NAS videos")

    # Group by (filename, size, width, height, codec) — REJECT null metadata
    groups = {}
    skipped_null_meta = 0
    for v in all_v:
        if v.get("width") is None or v.get("height") is None or v.get("codec") is None:
            skipped_null_meta += 1
            continue
        key = (
            v.get("filename"),
            v.get("size_bytes"),
            v.get("width"),
            v.get("height"),
            v.get("codec"),
        )
        groups.setdefault(key, []).append(v)
    dup_groups = {k: vs for k, vs in groups.items() if len(vs) >= 2}
    print(f"  {skipped_null_meta} videos skipped (null width/height/codec)")
    print(f"  {len(dup_groups)} duplicate groups (strict: name+size+w+h+codec, all metadata present)")

    # Filter: ALL DB mtime > 90 days
    candidates = []
    for k, items in dup_groups.items():
        all_old = True
        for v in items:
            mt = parse_iso(v.get("mtime"))
            if not mt or mt > THREE_MONTHS_AGO:
                all_old = False
                break
        if all_old:
            candidates.append((k, items))
    print(f"  {len(candidates)} groups with ALL DB mtime > 90 days")

    if not candidates:
        print("Nothing to do.")
        return

    print("\nConnecting to NAS...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.18.6", username="ETtomorrow", password="***REDACTED-NAS-PASS***", timeout=20)

    # Build plan: (keeper_v, keeper_canon, keeper_hash, [delete_targets])
    plan = []
    skipped_no_keeper = 0
    skipped_recent_stat = 0
    skipped_path_unsafe = 0
    skipped_size_mismatch = 0
    skipped_hash_mismatch = 0

    for idx, (key, items) in enumerate(candidates, 1):
        if idx % 50 == 0:
            print(f"  scan {idx}/{len(candidates)}  plan={len(plan)}", flush=True)

        # Re-stat all + get canonical paths
        verified = []
        for v in items:
            path = v.get("nas_share_url")
            if not path or not path.startswith(NAS_PREFIXES):
                continue
            exists, sz, real_mt, canon = nas_stat(ssh, path)
            if not exists or not canon or not canon.startswith(NAS_PREFIXES):
                continue
            if sz != v["size_bytes"]:
                continue  # size mismatch — DB stale
            if not real_mt or real_mt > THREE_MONTHS_AGO:
                continue
            verified.append({"v": v, "canon": canon, "size": sz, "mtime": real_mt})

        if len(verified) < 2:
            continue

        # Sort newest first
        verified.sort(key=lambda x: x["mtime"], reverse=True)

        # Pick keeper: newest probe-able
        keeper = None
        for x in verified:
            local = nas_to_local(x["v"]["nas_share_url"])
            if probe_playable(local):
                keeper = x
                break
        if not keeper:
            skipped_no_keeper += 1
            continue

        # Hash keeper
        keeper_hash = nas_partial_hash(ssh, keeper["canon"], keeper["size"])
        if not keeper_hash:
            skipped_no_keeper += 1
            continue

        # For each non-keeper, hash and compare with keeper
        delete_targets = []
        for x in verified:
            if x["v"]["drive_file_id"] == keeper["v"]["drive_file_id"]:
                continue
            if x["canon"] == keeper["canon"]:
                # Same physical file — different DB rows pointing to same path
                # Can safely drop the duplicate DB row only (don't move file)
                delete_targets.append({**x, "physical": False})
                continue
            h = nas_partial_hash(ssh, x["canon"], x["size"])
            if not h or h != keeper_hash:
                skipped_hash_mismatch += 1
                continue
            delete_targets.append({**x, "physical": True})

        if not delete_targets:
            continue

        plan.append({"keeper": keeper, "keeper_hash": keeper_hash, "delete": delete_targets})

    total_freed = sum(
        sum(t["size"] for t in p["delete"] if t["physical"]) for p in plan
    )
    physical_count = sum(sum(1 for t in p["delete"] if t["physical"]) for p in plan)
    db_only_count = sum(sum(1 for t in p["delete"] if not t["physical"]) for p in plan)

    print(f"\n=== Plan ===")
    print(f"  Groups verified: {len(plan)}")
    print(f"  Files to quarantine (move): {physical_count}")
    print(f"  DB-only rows to remove (same path duplicates): {db_only_count}")
    print(f"  Estimated space freed: {total_freed / 1024 / 1024 / 1024:.2f} GB")
    print(f"  Skipped (no playable keeper): {skipped_no_keeper}")
    print(f"  Skipped (hash mismatch with keeper): {skipped_hash_mismatch}")
    if plan:
        print("\nSample of planned moves:")
        for p in plan[:3]:
            print(f"  KEEP   [{p['keeper']['mtime'].strftime('%Y-%m-%d')}] {p['keeper']['canon']}")
            for t in p["delete"][:2]:
                act = "QTN" if t["physical"] else "DB-only"
                print(f"    -> {act}  [{t['mtime'].strftime('%Y-%m-%d')}] {t['canon']}")

    if dry:
        print("\nDRY-RUN — nothing moved. Add --execute to proceed.")
        ssh.close()
        return

    # ===== EXECUTE =====
    quarantine_ts = NOW.strftime("%Y%m%d_%H%M%S")
    # 每個 volume 各自一個 quarantine 根目錄，避免跨 volume 搬檔（搬不過去）
    qbases = {
        vol: f"{base}/{quarantine_ts}"
        for vol, base in QUARANTINE_PER_VOLUME.items()
    }
    for vol, qb in qbases.items():
        print(f"\nQuarantine dir [{vol}]: {qb}")
        ssh.exec_command(f"mkdir -p {shlex.quote(qb)}")[1].read()

    manifest = []
    moved = 0
    db_dropped = 0
    failures = 0

    for p in plan:
        # RE-verify keeper before any group action
        keeper = p["keeper"]
        exists, sz, real_mt, canon = nas_stat(ssh, keeper["v"]["nas_share_url"])
        if not exists or canon != keeper["canon"] or sz != keeper["size"]:
            print(f"  ! keeper changed; skip group ({canon})", flush=True)
            failures += len(p["delete"])
            continue
        local = nas_to_local(keeper["v"]["nas_share_url"])
        if not probe_playable(local):
            print(f"  ! keeper no longer probe-able; skip group ({canon})", flush=True)
            failures += len(p["delete"])
            continue
        # re-hash keeper to catch silent corruption
        kh = nas_partial_hash(ssh, keeper["canon"], keeper["size"])
        if kh != p["keeper_hash"]:
            print(f"  ! keeper hash changed; skip group", flush=True)
            failures += len(p["delete"])
            continue

        for t in p["delete"]:
            v = t["v"]
            path = v["nas_share_url"]
            if not path or not path.startswith(NAS_PREFIXES):
                failures += 1
                continue
            # re-verify target
            exists, sz, real_mt, canon = nas_stat(ssh, path)
            if not exists or canon != t["canon"] or sz != v["size_bytes"]:
                failures += 1
                continue
            if not real_mt or real_mt > THREE_MONTHS_AGO:
                failures += 1
                continue
            if canon == keeper["canon"]:
                # Should be DB-only; just delete DB row
                pass
            else:
                # Re-hash and compare
                h = nas_partial_hash(ssh, canon, sz)
                if not h or h != p["keeper_hash"]:
                    failures += 1
                    continue
                ok, _msg = nas_quarantine_move(ssh, canon, qbases, manifest)
                if not ok:
                    failures += 1
                    continue
                moved += 1
            # DB delete (multi-key)
            try:
                sb.table("videos").delete().eq(
                    "drive_file_id", v["drive_file_id"]
                ).eq("source", "nas").eq(
                    "nas_share_url", v["nas_share_url"]
                ).execute()
                db_dropped += 1
            except Exception as e:
                print(f"  ! DB delete failed for {v['drive_file_id']}: {e}", flush=True)
                failures += 1
        if (moved + failures) % 50 == 0 and (moved + failures) > 0:
            print(f"  exec progress: moved={moved} failures={failures} db={db_dropped}", flush=True)

    # Save manifest
    manifest_path = f"{qbase}/manifest.json"
    manifest_data = {
        "timestamp": NOW.isoformat(),
        "items": manifest,
        "stats": {"moved": moved, "db_dropped": db_dropped, "failures": failures},
    }
    payload = json.dumps(manifest_data, ensure_ascii=False, indent=2)
    safe_payload = payload.replace("'", "'\\''")
    cmd = f"cat > {shlex.quote(manifest_path)} << 'JSON_EOF'\n{payload}\nJSON_EOF"
    ssh.exec_command(cmd, timeout=20)[1].read()

    ssh.close()
    print(f"\n=== Done ===")
    print(f"  Files quarantined: {moved}  → {qbase}")
    print(f"  DB rows deleted: {db_dropped}")
    print(f"  Failures: {failures}")
    print(f"  Manifest: {manifest_path}")
    print(f"\n*** 注意 ***")
    print(f"  檔案已搬到 quarantine（不是真的刪除）")
    print(f"  確認運作正常後再手動 rm -rf {qbase} 永久釋放空間")


if __name__ == "__main__":
    main()
