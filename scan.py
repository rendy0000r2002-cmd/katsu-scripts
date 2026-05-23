"""
原初映像片庫 掃描器 - Google Drive API 版
直接查 Drive API 取得 Z:\房產\ 的影片清單，比 os.walk Drive File Stream 快 100x。
後續階段 2 改寫為 upsert 到 Supabase。
"""
import os
import re
import json
import sys
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
SA_PATH = "/volume2/docker-prod/scripts/原初映像片庫/service_account.json"
from googleapiclient.discovery import build

_HERE = Path(__file__).parent
TOKEN = str(_HERE / "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive"]
OUT_JSON = _HERE / "index.json"

ROOT_FOLDER_NAME = "房產"
VIDEO_EXT = {
    "mp4", "mov", "mkv", "avi", "m4v",
    "mts", "m2ts", "mxf", "wmv", "flv", "webm",
}

YEAR_DEFAULT = datetime.now().year
CASE_DATE_RE = re.compile(r"^(\d{4})(.+)$")


def get_service():
    creds = service_account.Credentials.from_service_account_file(SA_PATH, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def find_root(svc):
    res = svc.files().list(
        q=f"name='{ROOT_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id,name,parents)",
    ).execute()
    files = res.get("files", [])
    if not files:
        print(f"找不到根資料夾 {ROOT_FOLDER_NAME}", file=sys.stderr)
        sys.exit(1)
    # 若有多個，取第一個
    return files[0]["id"]


def list_children(svc, parent_id):
    """分頁列出一個資料夾底下的子項目"""
    items = []
    page = None
    while True:
        res = svc.files().list(
            q=f"'{parent_id}' in parents and trashed=false",
            fields="nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,parents)",
            pageSize=1000,
            pageToken=page,
        ).execute()
        items.extend(res.get("files", []))
        page = res.get("nextPageToken")
        if not page:
            break
    return items


def parse_channel(folder: str) -> dict:
    m = re.match(r"^(\d+)_?\s*(.+)$", folder)
    if m:
        return {"channel_order": int(m.group(1)), "channel_name": m.group(2).strip()}
    return {"channel_order": None, "channel_name": folder}


def parse_case(folder: str) -> dict:
    m = CASE_DATE_RE.match(folder)
    if m:
        mmdd = m.group(1)
        try:
            month = int(mmdd[:2]); day = int(mmdd[2:])
            if 1 <= month <= 12 and 1 <= day <= 31:
                return {
                    "case_date": f"{YEAR_DEFAULT}-{month:02d}-{day:02d}",
                    "case_name": m.group(2).strip(),
                }
        except ValueError:
            pass
    return {"case_date": None, "case_name": folder}


def classify_subpath(parts: list) -> str:
    joined = "/".join(parts).lower() if parts else ""
    for kw, label in [
        ("輸出", "輸出"),
        ("修改", "修改"),
        ("cam", "拍帶"),
        ("raw", "拍帶"),
        ("素材", "拍帶"),
    ]:
        if kw in joined:
            return label
    return "其他"


SKIP_FOLDERS = {"暫存", "雜物", "PING", "LUTS 調色檔", "AI字幕", "ET即賞屋模板", "tiles"}
# 路徑片段命中即跳過（Matterport tileset：l_X / c_X / cf_X；還有字幕預覽類）
SKIP_PREFIXES = ("l_", "c_", "cf_", ".cache")
MAX_DEPTH = 8


def walk(svc, folder_id, path_parts, rows, depth=0):
    """遞迴走資料夾。path_parts = [channel, case, sub...]"""
    indent = "  " * depth
    folder_label = "/".join(path_parts) if path_parts else "(root)"
    if depth > MAX_DEPTH:
        print(f"{indent}[max-depth] {folder_label}", flush=True)
        return
    # 只在前 3 層印，避免 log 爆炸
    if depth <= 3:
        print(f"{indent}> {folder_label}", flush=True)
    children = list_children(svc, folder_id)
    for child in children:
        name = child["name"]
        if child["mimeType"] == "application/vnd.google-apps.folder":
            if name in SKIP_FOLDERS or any(name.startswith(p) for p in SKIP_PREFIXES):
                continue
            walk(svc, child["id"], path_parts + [name], rows, depth + 1)
        else:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in VIDEO_EXT:
                continue
            channel_folder = path_parts[0] if len(path_parts) >= 1 else ""
            case_folder = path_parts[1] if len(path_parts) >= 2 else ""
            sub_parts = path_parts[2:]
            row = {
                "drive_file_id": child["id"],
                "rel_path": "/".join(path_parts + [name]),
                "filename": name,
                "ext": ext,
                "size_bytes": int(child.get("size", 0)) if child.get("size") else 0,
                "mtime": child.get("modifiedTime"),
                "drive_web_link": child.get("webViewLink"),
                "channel_folder": channel_folder,
                "case_folder": case_folder,
                "subpath": "/".join(sub_parts),
                "category": classify_subpath(sub_parts),
                "source": "drive",
                **parse_channel(channel_folder),
                **parse_case(case_folder),
            }
            rows.append(row)
            if len(rows) % 100 == 0:
                print(f"  ...{len(rows)} videos so far", flush=True)


def main():
    print("連 Drive API...", flush=True)
    svc = get_service()
    root = find_root(svc)
    print(f"房產根目錄 id={root}", flush=True)

    rows = []
    walk(svc, root, [], rows)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_JSON.with_suffix(OUT_JSON.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "root": ROOT_FOLDER_NAME,
            "count": len(rows),
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_JSON)

    by_ch = {}; by_cat = {}
    for r in rows:
        by_ch[r["channel_name"]] = by_ch.get(r["channel_name"], 0) + 1
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1

    print(f"\ntotal: {len(rows)} video files -> {OUT_JSON}")
    print("\n== by channel ==")
    for k, v in sorted(by_ch.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print("\n== by category ==")
    for k, v in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
