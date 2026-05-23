"""Rollback DB updates from vision_pass.py:
  - From each row (fid in journal status=tag), remove the Gemini-added `place`
    from `tags` array, and remove " {place}" / "{place} " from `search_text`.
  - Dry-run by default; pass --apply to actually mutate.

  Caveat: if `place` was legitimately in tags BEFORE Gemini ran, we still remove it.
  In practice Gemini's identifications here are mostly wrong on aerial DJI footage,
  so this is acceptable. Journal everything so manual recovery is possible.
"""
import argparse
import datetime
import json
import os
import sys
from collections import Counter
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POSTGREST_URL = "http://127.0.0.1:3011/rest/v1"
SCRIPT_DIR = Path("/volume2/docker-prod/scripts/原初映像片庫")
ROLLBACK_JOURNAL_DIR = SCRIPT_DIR / "logs" / "rollback_journals"
ENV_FILE = Path("/volume2/docker-prod/katsu-web-v2/web/.env")


def load_env(p: Path) -> None:
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def now_ts() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def remove_place_from_search(text: str, place: str) -> str:
    """Remove ' {place}' (the way vision_pass appended it) or standalone {place}."""
    if not text or not place:
        return text or ""
    # vision_pass code: new_search = (new_search + " " + place).strip()
    # so ' {place}' is the most likely suffix. Try both ' place' and 'place '.
    out = text
    for variant in [f" {place}", f"{place} ", place]:
        if variant in out:
            out = out.replace(variant, "")
    return out.strip()


def collect_journal_entries(journal_path: Path) -> list[dict]:
    """Read vision_pass journal, return only status=tag entries with fid+place."""
    entries = []
    with journal_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("status") != "tag":
                continue
            if not (d.get("fid") and d.get("place")):
                continue
            entries.append(d)
    return entries


def process(entry: dict, session: requests.Session, headers: dict, apply: bool, jf) -> str:
    fid = entry["fid"]
    place = entry["place"]
    rec = {
        "ts": now_iso(), "fid": fid, "place": place,
        "rel_path": entry.get("rel_path"),
    }

    # GET current row
    try:
        r = session.get(
            f"{POSTGREST_URL}/videos",
            params={"drive_file_id": f"eq.{fid}", "select": "drive_file_id,tags,search_text"},
            headers=headers, timeout=30,
        )
    except requests.RequestException as e:
        rec["status"] = "get-fail"
        rec["err"] = str(e)
        jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
        return "get-fail"

    if r.status_code != 200:
        rec["status"] = "get-fail"
        rec["err"] = f"HTTP {r.status_code}: {r.text[:200]}"
        jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
        return "get-fail"

    rows = r.json()
    if not rows:
        rec["status"] = "row-missing"
        jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
        return "row-missing"

    row = rows[0]
    old_tags = row.get("tags") or []
    old_search = row.get("search_text") or ""

    new_tags = [t for t in old_tags if t != place]
    new_search = remove_place_from_search(old_search, place)

    rec["old_tags"] = old_tags
    rec["new_tags"] = new_tags
    rec["old_search"] = old_search[:200]
    rec["new_search"] = new_search[:200]

    if new_tags == old_tags and new_search == old_search:
        rec["status"] = "no-change"
        jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
        return "no-change"

    if not apply:
        rec["status"] = "would-revert"
        jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
        return "would-revert"

    # Apply PATCH
    try:
        r2 = session.patch(
            f"{POSTGREST_URL}/videos",
            params={"drive_file_id": f"eq.{fid}", "select": "drive_file_id"},
            headers={**headers, "Prefer": "return=representation"},
            json={"tags": new_tags, "search_text": new_search},
            timeout=30,
        )
    except requests.RequestException as e:
        rec["status"] = "patch-fail"
        rec["err"] = str(e)
        jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
        return "patch-fail"

    if not (200 <= r2.status_code < 300):
        rec["status"] = "patch-fail"
        rec["err"] = f"HTTP {r2.status_code}: {r2.text[:200]}"
        jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
        return "patch-fail"

    data = []
    try:
        data = r2.json() if r2.text else []
    except Exception:
        pass
    if isinstance(data, list) and len(data) == 0:
        rec["status"] = "patch-fail"
        rec["err"] = "no rows updated"
        jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
        return "patch-fail"

    rec["status"] = "reverted"
    jf.write(json.dumps(rec, ensure_ascii=False) + "\n"); jf.flush()
    return "reverted"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", required=True, help="vision_pass_<TS>.jsonl path")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="apply", action="store_false")
    g.add_argument("--apply", dest="apply", action="store_true")
    ap.set_defaults(apply=False)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv or sys.argv[1:])

    load_env(ENV_FILE)
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        sys.exit("SUPABASE_SERVICE_ROLE_KEY missing")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    journal = Path(args.journal)
    if not journal.exists():
        sys.exit(f"journal not found: {journal}")

    entries = collect_journal_entries(journal)
    if args.limit > 0:
        entries = entries[: args.limit]
    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}  "
          f"tag entries to revert: {len(entries)}")

    ROLLBACK_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    mode = "apply" if args.apply else "dry-run"
    out_path = ROLLBACK_JOURNAL_DIR / f"rollback_{now_ts()}_{mode}.jsonl"
    print(f"output journal: {out_path}")

    stats: Counter[str] = Counter()
    session = requests.Session()
    with out_path.open("w", encoding="utf-8") as jf:
        for i, e in enumerate(entries, 1):
            st = process(e, session, headers, args.apply, jf)
            stats[st] += 1
            if i % 50 == 0:
                summary = "  ".join(f"{k}={v}" for k, v in sorted(stats.items()))
                print(f"[{i}/{len(entries)}]  {summary}", flush=True)

    print("\n=== rollback summary ===")
    for k, v in sorted(stats.items()):
        print(f"  {k:>16s}  {v}")

    fail_keys = {"get-fail", "patch-fail", "row-missing"}
    has_fail = any(stats[k] > 0 for k in fail_keys)
    return 1 if has_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
