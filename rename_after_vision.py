"""Rename pass after vision_pass.py.

For each `status=tag` entry in the vision_pass journal:
  1. Compute new filename = "<stem>_<place><suffix>"
  2. Rename file on disk (NAS roots /volume2/homes/ETtomorrow or /volume2/homes2/ETtomorrow)
  3. PATCH videos table (PostgREST) — set rel_path + filename
  4. If DB PATCH fails → rollback the rename

Defaults to --dry-run. Pass --apply to actually mutate disk + DB.

Run inside katsu-scripts-v2 container (PostgREST reachable via 127.0.0.1:3011,
.env mounted at /volume2/docker-prod/katsu-web-v2/web/.env).

Structure inspired by codex draft; merged with Claude version. Key fix:
DB filter column is `drive_file_id` (not `fid` — that's codex's bug).
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POSTGREST_URL = "http://127.0.0.1:3011/rest/v1"  # MUST include /rest/v1 (memory feedback_postgrest_url_rest_v1)
RENAME_JOURNAL_DIR = Path("/volume2/docker-prod/scripts/原初映像片庫/logs/rename_journals")
ENV_FILE = Path("/volume2/docker-prod/katsu-web-v2/web/.env")

BASE_BY_FID_PREFIX = (
    ("nas2:", Path("/volume2/homes2/ETtomorrow")),
    ("nas:", Path("/volume2/homes/ETtomorrow")),
)

STAT_KEYS = ["success", "skip-already", "file-missing", "dup", "db-fail", "mv-fail"]
FAIL_KEYS = {"db-fail", "mv-fail"}  # treat dup / file-missing as non-fatal skips


def load_env_file(p: Path) -> None:
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def truncate(text, limit=500) -> str:
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def sanitize_place(value: str) -> str:
    """Strip / \\ NUL etc — filename-safe form."""
    text = str(value).strip()
    cleaned = "".join("_" if ch in {"/", "\\", "\x00"} else ch for ch in text).strip()
    if cleaned in {"", ".", ".."}:
        return ""
    return cleaned


def safe_rel_parts(rel_path: str) -> list[str]:
    """Validate rel_path: must be relative, no NUL, no '.' or '..', no empty parts."""
    if not isinstance(rel_path, str) or rel_path == "":
        raise ValueError("missing rel_path")
    if "\x00" in rel_path:
        raise ValueError("rel_path contains NUL")
    if rel_path.startswith("/"):
        raise ValueError(f"rel_path must be relative: {rel_path}")
    parts = rel_path.split("/")
    if any(p in {"", ".", ".."} for p in parts):
        raise ValueError(f"unsafe rel_path: {rel_path}")
    return parts


def base_for_fid(fid: str) -> Path:
    if not isinstance(fid, str) or fid == "":
        raise ValueError("missing fid")
    for prefix, base in BASE_BY_FID_PREFIX:
        if fid.startswith(prefix):
            return base
    raise ValueError(f"unsupported fid prefix: {fid}")


def derive_rename(entry: dict) -> dict:
    """Validate + compute src/dst paths and new filename. Raises ValueError on bad input."""
    fid = entry.get("fid")
    rel_path = entry.get("rel_path")
    place_value = entry.get("place")
    if place_value is None:
        raise ValueError("missing place")

    base = base_for_fid(fid)
    parts = safe_rel_parts(rel_path)

    raw_place = str(place_value).strip()
    file_place = sanitize_place(raw_place)
    if not raw_place or not file_place:
        raise ValueError("empty or unsafe place")

    old_filename = parts[-1]
    already = raw_place in old_filename or file_place in old_filename
    suffix = PurePosixPath(old_filename).suffix
    stem = old_filename[:-len(suffix)] if suffix else old_filename
    new_filename = f"{stem}_{file_place}{suffix}"

    new_parts = parts[:-1] + [new_filename]
    return {
        "fid": fid,
        "place": raw_place,
        "place_for_filename": file_place,
        "old_rel_path": "/".join(parts),
        "new_rel_path": "/".join(new_parts),
        "old_filename": old_filename,
        "new_filename": new_filename,
        "src_path": base.joinpath(*parts),
        "dst_path": base.joinpath(*new_parts),
        "already": already,
    }


def patch_video(session, service_role_key, fid, new_rel_path, new_filename):
    """PATCH videos.{rel_path,filename} where drive_file_id = fid."""
    url = f"{POSTGREST_URL.rstrip('/')}/videos"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }
    params = {
        "drive_file_id": f"eq.{fid}",
        "select": "drive_file_id",
    }
    payload = {"rel_path": new_rel_path, "filename": new_filename}
    try:
        resp = session.patch(url, headers=headers, params=params, json=payload, timeout=30)
    except requests.RequestException as exc:
        return False, {"error": f"request error: {exc}"}

    info = {"http_status": resp.status_code, "body": truncate(resp.text)}
    if not (200 <= resp.status_code < 300):
        info["error"] = f"HTTP {resp.status_code}"
        return False, info
    if resp.status_code == 204:
        return True, info
    try:
        data = resp.json()
    except ValueError:
        return True, info
    if isinstance(data, list):
        info["rows"] = len(data)
        if len(data) == 0:
            info["error"] = "no rows patched"
            return False, info
    return True, info


def process_entry(entry, line_no, apply_mode, session, service_role_key):
    record = {
        "ts": now_iso(),
        "line": line_no,
        "mode": "apply" if apply_mode else "dry-run",
        "vision_status": entry.get("status"),
    }

    try:
        rename = derive_rename(entry)
    except ValueError as exc:
        record.update({
            "fid": entry.get("fid"),
            "old_rel_path": entry.get("rel_path"),
            "place": entry.get("place"),
            "status": "mv-fail",
            "reason": str(exc),
        })
        return "mv-fail", record

    src_path = rename.pop("src_path")
    dst_path = rename.pop("dst_path")
    already = rename.pop("already")
    record.update(rename)
    record["src_path"] = str(src_path)
    record["dst_path"] = str(dst_path)

    if already:
        record["status"] = "skip-already"
        record["reason"] = "filename already contains place"
        return "skip-already", record

    if not src_path.exists():
        record["status"] = "file-missing"
        record["reason"] = "source file does not exist"
        return "file-missing", record

    if src_path.is_dir():
        record["status"] = "mv-fail"
        record["reason"] = "source path is a directory"
        return "mv-fail", record

    if os.path.lexists(str(dst_path)):
        record["status"] = "dup"
        record["reason"] = "destination already exists"
        return "dup", record

    if not apply_mode:
        record["status"] = "would-rename"
        return "success", record

    # TOCTOU re-check immediately before rename
    try:
        if os.path.lexists(str(dst_path)):
            record["status"] = "dup"
            record["reason"] = "destination appeared right before rename"
            return "dup", record
        src_path.rename(dst_path)
    except OSError as exc:
        record["status"] = "mv-fail"
        record["reason"] = f"rename failed: {exc}"
        return "mv-fail", record

    ok, db_info = patch_video(
        session, service_role_key, record["fid"], record["new_rel_path"], record["new_filename"]
    )
    record["db"] = db_info
    if ok:
        record["status"] = "success"
        return "success", record

    # Rollback: undo rename
    try:
        dst_path.rename(src_path)
        record["status"] = "db-fail-rolled-back"
        record["rollback"] = "ok"
    except OSError as exc:
        record["status"] = "db-fail-rollback-fail"
        record["rollback"] = "failed"
        record["rollback_error"] = str(exc)
    return "db-fail", record


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description="Rename vision-tagged NAS videos and patch videos table."
    )
    ap.add_argument("--journal", required=True, help="vision_pass_<TS>.jsonl path")
    ap.add_argument("--limit", type=int, default=0, help="process first N tag entries only (0=all)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="apply", action="store_false", help="preview only (default)")
    mode.add_argument("--apply", dest="apply", action="store_true", help="perform mv + DB PATCH")
    ap.set_defaults(apply=False)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    apply_mode = args.apply
    mode = "apply" if apply_mode else "dry-run"

    load_env_file(ENV_FILE)
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if apply_mode and not service_role_key:
        print("ERROR: SUPABASE_SERVICE_ROLE_KEY missing (--apply needs it)", file=sys.stderr)
        return 1

    input_journal = Path(args.journal)
    if not input_journal.exists():
        print(f"ERROR: journal not found: {input_journal}", file=sys.stderr)
        return 1

    RENAME_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    output_journal = RENAME_JOURNAL_DIR / f"rename_{now_ts()}_{mode}.jsonl"

    stats = Counter({k: 0 for k in STAT_KEYS})
    processed = 0
    tag_seen = 0
    session = requests.Session() if apply_mode else None

    print(f"[start] mode={mode} journal={input_journal}", flush=True)
    print(f"[output] {output_journal}", flush=True)

    with input_journal.open("r", encoding="utf-8") as in_fp, \
         output_journal.open("x", encoding="utf-8", buffering=1) as out_fp:
        for line_no, line in enumerate(in_fp, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                stats["mv-fail"] += 1
                out_fp.write(json.dumps({
                    "ts": now_iso(), "line": line_no, "mode": mode,
                    "status": "mv-fail", "reason": f"invalid JSON: {exc}",
                    "raw": truncate(line),
                }, ensure_ascii=False) + "\n")
                continue

            if entry.get("status") != "tag":
                continue
            if args.limit and tag_seen >= args.limit:
                break
            tag_seen += 1

            stat_key, record = process_entry(entry, line_no, apply_mode, session, service_role_key)
            stats[stat_key] += 1
            processed += 1
            out_fp.write(json.dumps(record, ensure_ascii=False) + "\n")

            if processed % 100 == 0:
                summary = "  ".join(f"{k}={stats[k]}" for k in STAT_KEYS)
                print(f"[progress] {processed}  {summary}", flush=True)

    print()
    print("=== rename summary ===")
    print(f"mode:          {mode}")
    print(f"input:         {input_journal}")
    print(f"output:        {output_journal}")
    print(f"tag entries:   {processed}")
    print()
    for k in STAT_KEYS:
        print(f"  {k:<18}{stats[k]}")

    # Mark vision journal as applied for finisher idempotency
    if apply_mode:
        try:
            url = f"{POSTGREST_URL.rstrip('/')}/vision_journal_applied"
            payload = {
                "journal_basename": input_journal.name,
                "apply_journal_basename": output_journal.name,
                "tag_count": processed,
                "success_count": stats.get("success", 0),
                "source": "rename_apply",
            }
            r = requests.post(
                url, json=payload, timeout=10,
                headers={
                    "apikey": service_role_key,
                    "Authorization": f"Bearer {service_role_key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
            )
            print(f"[applied-mark] vision_journal_applied insert: {r.status_code}", flush=True)
        except Exception as exc:
            print(f"[applied-mark] failed (non-fatal): {exc}", flush=True)

    return 1 if any(stats[k] > 0 for k in FAIL_KEYS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
