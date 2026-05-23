"""
片庫還原工具。互動式：列備份 → 選一份 → 還原。

還原內容：
1. db/videos.jsonl, db/login_logs.jsonl → upsert 回 Supabase
2. files/ 下的設定 / 累積 JSON / 腳本 → 覆蓋本機（會先備份原檔到 .pre_restore_*）

使用：
    python restore_video_library.py             # 互動選備份
    python restore_video_library.py vlbackup_*.zip   # 直接指定
    python restore_video_library.py --list      # 只列出可用備份

⚠️ 還原會覆蓋現有 manual_locations.json 等檔案，會把原檔改名成 .pre_restore_時間 保留。
⚠️ DB 還原採 upsert（用主鍵覆蓋），不會刪除備份之後新增的列。若要完全還原，請另外手動 truncate。
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\rendy\原初映像片庫")
LOCAL_BACKUP_DIR = ROOT / "backups"
DRIVE_BACKUP_DIR = Path(r"Z:\片庫備份")

PRIMARY_KEYS = {
    "videos": "drive_file_id",
    "login_logs": "id",
    "case_locations": "case_name",
    "weekly_schedule": "date,case_name",
    "shoot_schedules": "id",
}
BATCH = 500

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def list_backups():
    candidates = []
    for d in (LOCAL_BACKUP_DIR, DRIVE_BACKUP_DIR):
        if not d.exists():
            continue
        for p in sorted(d.glob("vlbackup_*.zip")):
            candidates.append((p, p.stat().st_size, datetime.fromtimestamp(p.stat().st_mtime)))
    return candidates


def pick_interactive(candidates):
    print("可用備份：\n")
    for i, (p, sz, mt) in enumerate(candidates):
        loc = "本機" if str(p).startswith(str(LOCAL_BACKUP_DIR)) else "Drive"
        print(f"  [{i:2d}] {mt:%Y-%m-%d %H:%M}  {loc:<5}  {sz/1024/1024:6.1f} MB  {p.name}")
    print()
    raw = input("選編號（Enter 取消）: ").strip()
    if not raw:
        return None
    return candidates[int(raw)][0]


def upsert_table(env, table, rows, on_conflict):
    base = env["SUPABASE_URL"].rstrip("/")
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    url = f"{base}/rest/v1/{table}?on_conflict={on_conflict}"
    sent = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        body = json.dumps(batch, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            method="POST",
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    r.read()
                break
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                wait = 2 ** attempt + 1
                print(f"  {table} batch {i//BATCH+1} 失敗 ({e})，{wait}s 後重試 ({attempt+1}/5)")
                time.sleep(wait)
        else:
            raise RuntimeError(f"{table} batch {i//BATCH+1} 連續 5 次失敗")
        sent += len(batch)
        print(f"  {table}: {sent}/{len(rows)}")
    return sent


def restore(zip_path: Path, dry_run=False):
    print(f"還原來源：{zip_path}")
    env = load_env()

    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        print(f"備份時間：{manifest['created_at']}")
        print(f"DB 表：{list(manifest['tables'].keys())}")
        print(f"本機檔案：{len(manifest['files'])} 份\n")

        if dry_run:
            print("(dry-run，不執行還原)")
            return

        # 1) 還原本機檔案
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for fname in manifest["files"]:
            tgt = ROOT / fname
            arc = f"files/{fname}"
            try:
                data = zf.read(arc)
            except KeyError:
                print(f"  ⚠️ 跳過（zip 內找不到）{fname}")
                continue
            if tgt.exists():
                tgt.rename(tgt.with_name(f"{tgt.name}.pre_restore_{ts}"))
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.write_bytes(data)
            print(f"  ✓ {fname}")

        # 2) 還原 DB 表
        for table, info in manifest["tables"].items():
            if "error" in info:
                print(f"\n⚠️ {table} 在備份時失敗，跳過")
                continue
            print(f"\n還原 {table}...")
            arc = info["file"]
            rows = []
            for line in zf.read(arc).decode("utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
            print(f"  共 {len(rows)} 列")
            pk = PRIMARY_KEYS.get(table)
            if not pk:
                print(f"  ⚠️ 不知道 {table} 主鍵，跳過")
                continue
            upsert_table(env, table, rows, pk)

    print("\n✅ 還原完成")
    print(f"原本檔案已改名保留為 *.pre_restore_{ts}，確認新檔正常後可手動刪除。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", nargs="?", help="指定備份 zip 檔，省略則互動選")
    ap.add_argument("--list", action="store_true", help="只列出可用備份")
    ap.add_argument("--dry-run", action="store_true", help="不實際還原，只看會做什麼")
    args = ap.parse_args()

    candidates = list_backups()
    if not candidates:
        print("沒有任何備份檔（找過本機與 Drive）")
        sys.exit(1)

    if args.list:
        for p, sz, mt in candidates:
            loc = "本機" if str(p).startswith(str(LOCAL_BACKUP_DIR)) else "Drive"
            print(f"{mt:%Y-%m-%d %H:%M}  {loc}  {sz/1024/1024:.1f} MB  {p}")
        return

    if args.zip:
        zp = Path(args.zip)
        if not zp.exists():
            for p, _, _ in candidates:
                if p.name == args.zip:
                    zp = p
                    break
        if not zp.exists():
            print(f"找不到：{args.zip}")
            sys.exit(1)
    else:
        zp = pick_interactive(candidates)
        if zp is None:
            print("取消")
            return

    confirm = "y" if args.dry_run else input(
        f"\n⚠️ 確定要從 {zp.name} 還原？這會覆蓋現有檔案 + upsert 進 Supabase。輸入 yes 繼續：").strip()
    if confirm != "yes" and not args.dry_run:
        print("取消")
        return

    restore(zp, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
