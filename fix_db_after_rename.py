"""Reapply DB PATCHes for the 5453 successfully-renamed-on-disk files.

The rename_after_vision.py had POSTGREST_URL missing /rest/v1, so PATCH calls
hit nginx gateway (returned 200 'pg-katsu gateway ok' plain text) and were
silently no-ops. Files moved on disk, DB not updated.

This script reads the 3 apply journals, for each `status: success` entry:
  - Verify disk file exists at new path
  - PATCH videos table (correct /rest/v1 URL) with new rel_path + filename
  - Sanity: skip if disk file missing

Idempotent. Safe to re-run.
"""
import json
import os
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POSTGREST_URL = "http://127.0.0.1:3011/rest/v1"  # WITH /rest/v1
ENV_FILE = Path("/volume2/docker-prod/katsu-web-v2/web/.env")

JOURNALS = [
    "/volume2/docker-prod/scripts/原初映像片庫/logs/rename_journals/rename_20260521_174553_apply.jsonl",
    "/volume2/docker-prod/scripts/原初映像片庫/logs/rename_journals/rename_20260521_174555_apply.jsonl",
    "/volume2/docker-prod/scripts/原初映像片庫/logs/rename_journals/rename_20260521_174606_apply.jsonl",
]


def load_env(p: Path) -> dict:
    env = {}
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


env = load_env(ENV_FILE)
KEY = env.get("SUPABASE_SERVICE_ROLE_KEY")
if not KEY:
    sys.exit("SUPABASE_SERVICE_ROLE_KEY missing")
HEADERS = {
    "apikey": KEY,
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Prefer": "return=representation",
}

sess = requests.Session()

stats = {"patched": 0, "already-new": 0, "row-missing": 0, "disk-missing": 0,
         "patch-fail": 0}

for j in JOURNALS:
    print(f"\n=== {Path(j).name} ===")
    with open(j, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    success_entries = [e for e in entries if e.get("status") == "success"]
    print(f"  success entries: {len(success_entries)}")

    for i, e in enumerate(success_entries, 1):
        fid = e["fid"]
        new_rel = e["new_rel_path"]
        new_fn = e["new_filename"]
        dst = e["dst_path"]

        if not os.path.exists(dst):
            stats["disk-missing"] += 1
            continue

        # GET current rel_path to detect already-patched
        r = sess.get(
            f"{POSTGREST_URL}/videos",
            params={"drive_file_id": f"eq.{fid}", "select": "rel_path"},
            headers=HEADERS, timeout=20,
        )
        if r.status_code != 200:
            stats["patch-fail"] += 1
            print(f"  GET fail fid={fid}: {r.status_code}")
            continue
        rows = r.json()
        if not rows:
            stats["row-missing"] += 1
            continue
        if rows[0].get("rel_path") == new_rel:
            stats["already-new"] += 1
            continue

        # PATCH
        r2 = sess.patch(
            f"{POSTGREST_URL}/videos",
            params={"drive_file_id": f"eq.{fid}"},
            headers=HEADERS,
            json={"rel_path": new_rel, "filename": new_fn},
            timeout=30,
        )
        if 200 <= r2.status_code < 300:
            try:
                data = r2.json()
                if isinstance(data, list) and not data:
                    stats["patch-fail"] += 1
                else:
                    stats["patched"] += 1
            except Exception:
                stats["patched"] += 1
        else:
            stats["patch-fail"] += 1
            if stats["patch-fail"] <= 5:
                print(f"  PATCH fail fid={fid}: {r2.status_code} {r2.text[:100]}")

        if i % 200 == 0:
            summary = "  ".join(f"{k}={v}" for k, v in stats.items())
            print(f"  [{i}/{len(success_entries)}]  {summary}")

print("\n=== FINAL ===")
for k, v in stats.items():
    print(f"  {k:<14s}  {v}")
