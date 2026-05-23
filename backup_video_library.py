"""
片庫系統備份。每日由 Windows Task Scheduler 觸發。

備份範圍：
1. Supabase 表（videos / login_logs）→ JSONL（分頁拉取）
2. 累積人工資料（manual_locations.json、locations_unknown.json、index_*.json）
3. schema_v*.sql、設定檔（.env、docker-compose.yml）
4. 本批 enrich/sort 等核心 Python 腳本（讓還原時有完整工具鏈）

輸出：C:\\Users\\rendy\\原初映像片庫\\backups\\YYYYMMDD_HHMM\\vlbackup_*.zip
本機保留最近 14 份；複製一份到 Z:\\片庫備份\\ 保留最近 30 份。
"""
import json
import os
import shutil
import sys
import time
import urllib.request
import urllib.error
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_BACKUP_DIR = ROOT / "backups"
# Drive 備份只在 Windows 端做（Z: drive 必須真的存在）；
# Linux/容器內 Path.parent 對 'Z:\\xxx' 會誤判 → 不能用 parent.exists() 判斷
_IS_WINDOWS = os.name == "nt"
_DRIVE_DIR = Path(r"Z:\片庫備份")
DRIVE_BACKUP_DIR = _DRIVE_DIR if (_IS_WINDOWS and Path("Z:\\").exists()) else None
LOG_DIR = ROOT / "logs"

LOCAL_RETAIN_DAYS = 14
DRIVE_RETAIN_DAYS = 30

TG_TOKEN = "***REDACTED-TG-TOKEN***"
TG_CHAT = "8635121564"

PAGE_SIZE = 1000
TABLES = ["videos", "login_logs", "case_locations", "weekly_schedule", "shoot_schedules"]
# 每張表的 PK，用來 stable pagination（沒指定 order 時 PostgREST 分頁會 race，
# DB 邊跑邊改會回傳重複 row + 漏 row。2026-05-19 backup 撞 daily_sync 暴露此 bug）
SORT_BY = {
    "videos": "drive_file_id",
    "login_logs": "id",
    "case_locations": "case_name",
    "weekly_schedule": "date,case_name",
    "shoot_schedules": "id",
}
LOCAL_FILES = [
    "manual_locations.json",
    "locations_unknown.json",
    "index_v2.json",
    "index_nas_v2.json",
    "to_enrich.json",
    # .env intentionally excluded — 2026-05-15 自架 Postgres 後若還原會把 SUPABASE_URL
    # 蓋回雲端值，連線立刻爆。.env 是部署設定，不是資料，獨立用密碼管理者保管。
    "docker-compose.yml",
    "scan.py", "scan_nas.py", "refine.py", "upload.py",
    "extract_locations.py", "enrich_locations.py",
    "daily_sync.ps1",
    "backup_video_library.py", "restore_video_library.py",
    "check_prewarm_progress.py",
]
LOCAL_GLOBS = ["schema*.sql"]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def log(stamp, msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / f"backup_{stamp}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def send_tg(text):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        body = json.dumps({"chat_id": TG_CHAT, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"TG 發送失敗: {e}", flush=True)


def fetch_all(env, table, jsonl_path, log_fn):
    """分頁拉取整張表 → 寫成 JSONL（按 PK 排序避免 race condition 重複/漏 row）"""
    base = env["SUPABASE_URL"].rstrip("/")
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    sort_cols = SORT_BY.get(table, "")
    order_param = "&order=" + ",".join(f"{c}.asc" for c in sort_cols.split(",")) if sort_cols else ""
    url_base = f"{base}/rest/v1/{table}?select=*{order_param}"
    total = 0
    with open(jsonl_path, "w", encoding="utf-8") as f:
        offset = 0
        while True:
            req = urllib.request.Request(
                url_base,
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Range-Unit": "items",
                    "Range": f"{offset}-{offset + PAGE_SIZE - 1}",
                },
            )
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = json.loads(r.read().decode("utf-8"))
                    break
                except (urllib.error.HTTPError, urllib.error.URLError) as e:
                    wait = 2 ** attempt + 1
                    log_fn(f"  {table} offset={offset} 失敗 ({e})，{wait}s 後重試 ({attempt+1}/5)")
                    time.sleep(wait)
            else:
                raise RuntimeError(f"{table} offset={offset} 連續 5 次失敗，放棄")

            if not data:
                break
            for row in data:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            total += len(data)
            log_fn(f"  {table}: {total} rows")
            if len(data) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    return total


def prune_old(folder: Path, retain_days: int, prefix: str = "vlbackup_"):
    if not folder.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=retain_days)
    deleted = 0
    for p in folder.iterdir():
        if not p.name.startswith(prefix):
            continue
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink()
                deleted += 1
        except Exception:
            pass
    return deleted


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    LOCAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = LOCAL_BACKUP_DIR / f"vlbackup_{stamp}"
    work_dir.mkdir(exist_ok=True)

    def log_fn(msg):
        log(stamp, msg)

    log_fn(f"=== 備份開始 {stamp} ===")
    env = load_env()

    manifest = {
        "created_at": datetime.now().isoformat(),
        "tables": {},
        "files": [],
    }

    # 1) DB tables
    db_dir = work_dir / "db"
    db_dir.mkdir()
    for t in TABLES:
        log_fn(f"拉 {t} 表")
        path = db_dir / f"{t}.jsonl"
        try:
            n = fetch_all(env, t, path, log_fn)
            manifest["tables"][t] = {"rows": n, "file": f"db/{t}.jsonl"}
        except Exception as e:
            log_fn(f"  ⚠️ {t} 失敗: {e}")
            manifest["tables"][t] = {"error": str(e)}

    # 2) Local files
    files_dir = work_dir / "files"
    files_dir.mkdir()
    for name in LOCAL_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, files_dir / name)
            manifest["files"].append(name)
            log_fn(f"  copy {name}")
    for pattern in LOCAL_GLOBS:
        for src in ROOT.glob(pattern):
            shutil.copy2(src, files_dir / src.name)
            manifest["files"].append(src.name)
            log_fn(f"  copy {src.name}")

    # 3) Manifest
    (work_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4) Zip
    zip_path = LOCAL_BACKUP_DIR / f"vlbackup_{stamp}.zip"
    log_fn(f"打包 → {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in work_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(work_dir))
    shutil.rmtree(work_dir)
    zip_size_mb = zip_path.stat().st_size / 1024 / 1024
    log_fn(f"  size: {zip_size_mb:.1f} MB")

    # 5) 鏡像到 Drive (skipped on NAS where Z:\ doesn't exist)
    drive_msg = ""
    if DRIVE_BACKUP_DIR is not None:
        try:
            DRIVE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            drive_zip = DRIVE_BACKUP_DIR / zip_path.name
            shutil.copy2(zip_path, drive_zip)
            drive_msg = f"+ Drive：{drive_zip}"
            log_fn(f"  鏡像到 Drive: {drive_zip}")
        except Exception as e:
            drive_msg = f"⚠️ Drive 鏡像失敗：{e}"
            log_fn(drive_msg)
    else:
        drive_msg = "(NAS — 跳過 Drive 鏡像)"
        log_fn(drive_msg)

    # 6) 清舊
    local_pruned = prune_old(LOCAL_BACKUP_DIR, LOCAL_RETAIN_DAYS)
    drive_pruned = prune_old(DRIVE_BACKUP_DIR, DRIVE_RETAIN_DAYS) if (DRIVE_BACKUP_DIR is not None and DRIVE_BACKUP_DIR.exists()) else 0
    log_fn(f"清舊：本機 -{local_pruned}，Drive -{drive_pruned}")

    # 7) Telegram
    table_lines = []
    for t, info in manifest["tables"].items():
        if "rows" in info:
            table_lines.append(f"・{t}: {info['rows']:,} rows")
        else:
            table_lines.append(f"・{t}: ❌ {info['error'][:60]}")

    msg = (
        f"💾 片庫備份完成 {stamp}\n"
        f"\n"
        f"📊 DB:\n" + "\n".join(table_lines) + "\n"
        f"\n"
        f"📁 本機檔案：{len(manifest['files'])} 份\n"
        f"📦 ZIP：{zip_size_mb:.1f} MB\n"
        f"{drive_msg}\n"
        f"🧹 清舊：本機 -{local_pruned}，Drive -{drive_pruned}"
    )
    send_tg(msg)
    log_fn("=== 結束 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        send_tg(f"⚠️ 片庫備份失敗：{e!r}\n{tb[-500:]}")
        raise
