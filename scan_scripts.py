"""
掃 Drive 找檔名含「腳本」的檔案（.docx 為主），存到 scripts_index.json。
給 enrich_from_scripts.py 用來補建案地點。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
SA_PATH = "/volume2/docker-prod/scripts/原初映像片庫/service_account.json"
from googleapiclient.discovery import build

TOKEN = str(Path(__file__).parent / "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive"]
OUT_JSON = Path(__file__).parent / "scripts_index.json"
ROOT_FOLDER_NAME = "房產"
# 只掃指定頻道（user 限定範圍，加速）
INCLUDE_CHANNELS = {
    "12_日常女子開箱",
    "5_琦郁房事悄悄話",
    "9_琦郁",
    "5_Ivy",
    "7_Amber",
}
SKIP_FOLDERS = {"暫存", "雜物", "PING", "LUTS 調色檔", "AI字幕", "ET即賞屋模板", "tiles"}
SKIP_PREFIXES = ("l_", "c_", "cf_", ".cache")
MAX_DEPTH = 8

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def get_service():
    creds = service_account.Credentials.from_service_account_file(SA_PATH, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def find_root(svc):
    res = svc.files().list(
        q=f"name='{ROOT_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id,name)",
    ).execute()
    files = res.get("files", [])
    if not files:
        print(f"找不到根 {ROOT_FOLDER_NAME}", file=sys.stderr)
        sys.exit(1)
    return files[0]["id"]


def list_children(svc, parent_id):
    items = []
    page = None
    while True:
        res = svc.files().list(
            q=f"'{parent_id}' in parents and trashed=false",
            fields="nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink)",
            pageSize=1000,
            pageToken=page,
        ).execute()
        items.extend(res.get("files", []))
        page = res.get("nextPageToken")
        if not page:
            break
    return items


def walk(svc, folder_id, path_parts, rows, depth=0):
    if depth > MAX_DEPTH:
        return
    if depth <= 2:
        print(f"{'  ' * depth}> {'/'.join(path_parts) or '(root)'}", flush=True)
    children = list_children(svc, folder_id)
    for child in children:
        name = child["name"]
        if child["mimeType"] == "application/vnd.google-apps.folder":
            if name in SKIP_FOLDERS or any(name.startswith(p) for p in SKIP_PREFIXES):
                continue
            walk(svc, child["id"], path_parts + [name], rows, depth + 1)
        else:
            if "腳本" not in name:
                continue
            channel_folder = path_parts[0] if len(path_parts) >= 1 else ""
            case_folder = path_parts[1] if len(path_parts) >= 2 else ""
            # 找最內層、非分類層的資料夾名 = 真正案名
            CATEGORY_LAYERS = {"0_業配", "1_業配", "2_業配", "3_業配",
                               "01_正片資料夾", "02_業配資料夾", "03_業配資料夾",
                               "00_短影正片", "2_短影or精華", "ET合作",
                               "結案", "業配", "舊"}
            case_subfolder = ""
            for p in reversed(path_parts):
                if p and p not in CATEGORY_LAYERS and not p.startswith("0_") and p != case_folder:
                    case_subfolder = p
                    break
            if not case_subfolder and len(path_parts) >= 3:
                case_subfolder = path_parts[-1]
            rows.append({
                "drive_file_id": child["id"],
                "name": name,
                "mimeType": child.get("mimeType"),
                "size_bytes": int(child.get("size", 0)) if child.get("size") else 0,
                "mtime": child.get("modifiedTime"),
                "drive_web_link": child.get("webViewLink"),
                "channel_folder": channel_folder,
                "case_folder": case_folder,
                "case_subfolder": case_subfolder,
                "rel_path": "/".join(path_parts + [name]),
            })


def main():
    print("連 Drive...", flush=True)
    svc = get_service()
    root = find_root(svc)
    print(f"root id={root}", flush=True)
    rows = []
    # 只走指定頻道
    children = list_children(svc, root)
    for child in children:
        if child["mimeType"] != "application/vnd.google-apps.folder":
            continue
        if child["name"] not in INCLUDE_CHANNELS:
            continue
        print(f"-> {child['name']}", flush=True)
        walk(svc, child["id"], [child["name"]], rows, depth=1)
    OUT_JSON.write_text(
        json.dumps({
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(rows),
            "rows": rows,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n找到 {len(rows)} 個腳本檔 -> {OUT_JSON}")


if __name__ == "__main__":
    main()
