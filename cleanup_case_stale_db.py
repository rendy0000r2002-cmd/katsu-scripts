"""
sort-footage 改名後同步清掉 DB 舊紀錄。

場景：sort-footage 把 `A0446.MP4` 改成 `A0446_接待中心_建案模型特寫.MP4`。
daily_sync 跑完後 DB 會多一列新檔名，但舊檔名那列還在指向已不存在的路徑，
導致片庫網頁縮圖空白 / 下載變 .txt。

用法：
  python cleanup_case_stale_db.py <案件資料夾>
  python cleanup_case_stale_db.py <案件資料夾> --dry-run

案件資料夾接受 PC mount (Y:/, U:/) 或 NAS linux 路徑 (/volume2/)。
腳本掃這個資料夾下的 DB 紀錄，逐筆驗證實體檔是否存在，不存在就刪。

跟 cleanup_missing_files.py 的差異：
- 只處理單一案件範圍，不掃全部 NAS
- 不發 Telegram、不需 confirm（呼叫者已知 rename 剛跑完）
- 不需 mount health check（已知這個 case 的 volume 在用）
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from supabase import create_client

from nas_roots import convert_path, detect_platform, find_root_for, to_rel_path

ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
BATCH = 50


def load_env() -> dict:
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_folder", help="案件資料夾（PC 或 NAS 路徑都行）")
    ap.add_argument("--dry-run", action="store_true", help="只列出，不刪")
    args = ap.parse_args()

    case = args.case_folder.replace("\\", "/").rstrip("/")
    parsed = to_rel_path(case)
    if not parsed:
        print(f"無法判定 case_folder 屬於哪個 volume：{case}", file=sys.stderr)
        return 2
    root, rel = parsed
    linux_prefix = (root.linux.rstrip("/") + "/" + rel).rstrip("/") + "/"
    print(f"案件：{rel}（{root.label}）")
    print(f"DB like prefix：{linux_prefix}")

    env = load_env()
    url = env.get("SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL")
    key = env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("缺 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
        return 2
    sb = create_client(url, key)

    rows = (
        sb.table("videos")
        .select("drive_file_id,filename,nas_share_url")
        .eq("source", "nas")
        .like("nas_share_url", f"{linux_prefix}%")
        .execute()
        .data
    )
    print(f"DB 共 {len(rows)} 列在這個案件下")

    platform = detect_platform()
    stale_ids: list[str] = []
    for r in rows:
        local = convert_path(r["nas_share_url"], target_platform=platform)
        if not local or not Path(local).exists():
            stale_ids.append(r["drive_file_id"])

    print(f"stale 列數：{len(stale_ids)}")
    if not stale_ids:
        return 0

    if args.dry_run:
        for sid in stale_ids[:20]:
            print(f"  [dry] {sid}")
        if len(stale_ids) > 20:
            print(f"  ...（共 {len(stale_ids)} 列）")
        return 0

    deleted = 0
    for i in range(0, len(stale_ids), BATCH):
        chunk = stale_ids[i:i + BATCH]
        res = sb.table("videos").delete().in_("drive_file_id", chunk).execute()
        deleted += len(res.data or [])
        print(f"  刪除 {len(chunk)} 筆 → 實刪 {len(res.data or [])}")
    print(f"完成：共刪 {deleted} 列")
    return 0


if __name__ == "__main__":
    sys.exit(main())
