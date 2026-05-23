"""
讀 index_v2.json，upsert 到 Supabase videos 表。
以 drive_file_id 為 primary key：重複跑不會爆，只更新有變的欄位。
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

from supabase import create_client

ENV = Path(__file__).parent / ".env"
_IDX_ARG = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "index_v2.json")
IDX = Path(_IDX_ARG)
BATCH = 500


def load_env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def to_row(r):
    ch_name = r.get("channel_name") or ""
    case_name = r.get("case_name") or ""
    subpath = r.get("subpath") or ""
    filename = r.get("filename") or ""
    tags = r.get("tags") or []
    city = r.get("city") or ""
    district = r.get("district") or ""
    pair = f"{city} {district}".strip() if city and district else ""
    search = " ".join(filter(None, [ch_name, case_name, subpath, filename, " ".join(tags), city, district, pair]))
    return {
        "drive_file_id": r["drive_file_id"],
        "rel_path": r["rel_path"],
        "filename": filename,
        "ext": r["ext"],
        "size_bytes": int(r.get("size_bytes") or 0),
        "mtime": r.get("mtime"),
        "drive_web_link": r.get("drive_web_link"),
        "source": r.get("source") or "drive",
        "nas_share_url": r.get("nas_path") if r.get("source") == "nas" else None,
        "channel_folder": r.get("channel_folder"),
        "channel_name": ch_name,
        "channel_order": r.get("channel_order"),
        "case_folder": r.get("case_folder"),
        "case_name": case_name,
        "case_date": r.get("case_date"),
        "category_folder": r.get("category_folder"),
        "category": r.get("category"),
        "subpath": subpath,
        "tags": tags,
        "search_text": search,
        "is_old": bool(r.get("is_old", False)),
    }


def main():
    env = load_env()
    url = env["SUPABASE_URL"]; key = env["SUPABASE_SERVICE_ROLE_KEY"]
    client = create_client(url, key)

    data = json.loads(IDX.read_text(encoding="utf-8"))
    rows = [to_row(r) for r in data["rows"]]
    total = len(rows)
    print(f"upserting {total} rows in batches of {BATCH}")

    # 保留特定分類 tag（由偵測腳本寫入，不可被 daily_sync 覆蓋）
    PRESERVE_TAGS = {"有主持人", "無主持人", "非建案", "短影3秒"}

    def fetch_existing_preserved(fids):
        """For each drive_file_id, fetch tags that are in PRESERVE_TAGS."""
        existing = {}
        for j in range(0, len(fids), 200):
            batch_ids = fids[j:j+200]
            resp = client.table("videos").select("drive_file_id, tags").in_(
                "drive_file_id", batch_ids
            ).execute()
            for row in resp.data or []:
                kept = [t for t in (row.get("tags") or []) if t in PRESERVE_TAGS]
                if kept:
                    existing[row["drive_file_id"]] = kept
        return existing

    done = 0
    for i in range(0, total, BATCH):
        chunk = rows[i:i+BATCH]
        # Merge preserved tags before upsert
        preserved = fetch_existing_preserved([r["drive_file_id"] for r in chunk])
        if preserved:
            for r in chunk:
                keep = preserved.get(r["drive_file_id"])
                if keep:
                    merged = list(set((r.get("tags") or []) + keep))
                    r["tags"] = merged
                    # Re-build search_text since tags changed
                    parts = [r.get("channel_name", ""), r.get("case_name", ""),
                             r.get("subpath", ""), r.get("filename", ""),
                             " ".join(merged), r.get("city", ""), r.get("district", "")]
                    pair = f"{r.get('city','')} {r.get('district','')}".strip()
                    parts.append(pair if r.get("city") and r.get("district") else "")
                    r["search_text"] = " ".join(p for p in parts if p)
        client.table("videos").upsert(chunk, on_conflict="drive_file_id").execute()
        done += len(chunk)
        print(f"  {done}/{total}", flush=True)

    # 清理殭屍條目：DB 裡有但本次掃描已不存在的 row（檔案被搬走/刪除/改名導致 fid 變）
    # 只在 full scan 安全 — daily_sync 的 scan_*.py 都是 full scan，所以這裡可以做。
    # 為避免掃描突然回傳少量資料導致誤刪，加 50% 安全閾值。
    sources = {r.get("source", "drive") for r in rows}
    print(f"\n檢查殭屍條目 (sources: {sorted(sources)})...")
    for src in sorted(sources):
        src_current = {r["drive_file_id"] for r in rows if r.get("source", "drive") == src}
        if not src_current:
            continue
        db_fids = set()
        offset = 0
        while True:
            resp = client.table("videos").select("drive_file_id").eq("source", src).range(offset, offset + 999).execute()
            chunk = resp.data or []
            db_fids.update(row["drive_file_id"] for row in chunk)
            if len(chunk) < 1000:
                break
            offset += 1000
        stale = db_fids - src_current
        if not stale:
            print(f"  source={src}: 0 筆殭屍，DB 乾淨")
            continue
        # 安全閾值：殭屍 > 一半本次掃描的數量 → 八成是 scan 有問題，跳過 prune
        if len(stale) > len(src_current) * 0.5:
            print(f"  source={src}: ⚠️ 殭屍 {len(stale)} > 50% of 本次 {len(src_current)} 筆，可疑！跳過 prune (sanity check)")
            continue
        print(f"  source={src}: 找到 {len(stale)} 筆殭屍（檔案已不存在），刪除中...")
        stale_list = list(stale)
        deleted = 0
        for i in range(0, len(stale_list), 100):
            batch = stale_list[i:i+100]
            client.table("videos").delete().in_("drive_file_id", batch).execute()
            deleted += len(batch)
        print(f"  source={src}: 刪除 {deleted} 筆殭屍條目")

    print("done")


if __name__ == "__main__":
    main()
