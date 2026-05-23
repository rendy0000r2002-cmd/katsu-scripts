"""
偵測 Drive 案件 輸出/ 資料夾的新上傳並推 LINE 通知。

機制：用 Drive Changes API 抓 SA 可見的 Drive 全域變更，篩出 輸出/ 內的影片新檔，
按「完成 / 修改」推 LINE。狀態存 state file（pageToken）供下次接續。

由我（rendy0000r2002@gmail.com）親自上傳/搬檔的不通知。

NAS cron 每 1 分鐘執行一次。
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------- config ----------
_IS_WINDOWS = os.name == "nt"
SA_PATH = os.environ.get("SA_PATH") or (
    r"C:\Users\rendy\service_account.json" if _IS_WINDOWS
    else "/volume2/docker-prod/scripts/原初映像片庫/service_account.json"
)
STATE_PATH = Path(os.environ.get(
    "UPLOAD_NOTIFIER_STATE",
    str(Path(__file__).parent / "upload_notifier_state.json"),
))
def _load_line_creds():
    tok = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    uid = os.environ.get("LINE_USER_ID", "")
    if tok and uid:
        return tok, uid
    # 開發機 fallback：讀 line_token.json
    for p in [
        Path(r"C:\Users\rendy\line_token.json"),
        Path("/volume2/docker-prod/scripts/原初映像片庫/line_token.json"),
    ]:
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return d.get("LINE_CHANNEL_ACCESS_TOKEN", ""), d.get("LINE_USER_ID", "")
            except Exception:
                pass
    return "", ""

LINE_TOKEN, LINE_USER_ID = _load_line_creds()
SELF_EMAIL = os.environ.get("SELF_EMAIL", "rendy0000r2002@gmail.com")

VIDEO_EXT = {
    "mp4", "mov", "mkv", "avi", "m4v",
    "mts", "m2ts", "mxf", "wmv", "flv", "webm",
}
SCOPES = ["https://www.googleapis.com/auth/drive"]

# ---------- helpers ----------
def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}

def save_state(s):
    STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

def get_service():
    creds = service_account.Credentials.from_service_account_file(SA_PATH, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def is_video(name: str) -> bool:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in VIDEO_EXT

def parents_ancestor_chain(svc, file_id, max_depth=5):
    """走 parents 一路往上抓到最多 max_depth 層，回傳 [{id, name}, ...] 從近到遠。"""
    chain = []
    cur = file_id
    for _ in range(max_depth):
        meta = svc.files().get(fileId=cur, fields="id,name,parents").execute()
        chain.append({"id": meta["id"], "name": meta["name"]})
        parents = meta.get("parents", [])
        if not parents:
            break
        cur = parents[0]
    return chain

def find_output_case(svc, file_meta):
    """
    從檔案 file_meta（含 parents）判斷是否落在 案件/輸出/ 結構。
    回傳 {output_id, case_id, case_name} 或 None（不在 輸出/ 直屬底下）。
    """
    parents = file_meta.get("parents") or []
    if not parents:
        return None
    output_id = parents[0]
    try:
        output_meta = svc.files().get(fileId=output_id, fields="id,name,parents").execute()
    except HttpError:
        return None
    if output_meta.get("name") != "輸出":
        return None
    case_parents = output_meta.get("parents") or []
    if not case_parents:
        return None
    case_id = case_parents[0]
    try:
        case_meta = svc.files().get(fileId=case_id, fields="id,name").execute()
    except HttpError:
        return None
    return {"output_id": output_id, "case_id": case_id, "case_name": case_meta["name"]}

def detect_kind(svc, output_id) -> str:
    """完成 = 輸出/ 只有 1 個檔且 舊/ 為空；其餘為 修改。"""
    # 看 輸出/ 內有沒有 「舊」 子資料夾
    res = svc.files().list(
        q=f"'{output_id}' in parents and name='舊' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)",
        pageSize=1,
    ).execute()
    old_folders = res.get("files", [])
    if old_folders:
        old_id = old_folders[0]["id"]
        old_files = svc.files().list(
            q=f"'{old_id}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'",
            fields="files(id)",
            pageSize=1,
        ).execute()
        if old_files.get("files"):
            return "修改"
    # 數 輸出/ 直屬檔案
    out_files = svc.files().list(
        q=f"'{output_id}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'",
        fields="files(id)",
        pageSize=10,
    ).execute()
    return "修改" if len(out_files.get("files", [])) > 1 else "完成"

def push_line(text: str) -> bool:
    if not LINE_TOKEN or not LINE_USER_ID:
        print(f"[skip] LINE 未設定，假裝送：\n{text}\n")
        return False
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )
    if r.status_code != 200:
        print(f"[line err] {r.status_code} {r.text}")
        return False
    return True

# ---------- main ----------
def main():
    state = load_state()
    svc = get_service()

    if "pageToken" not in state:
        # 第一次跑：拿目前的 pageToken，這次不通知任何東西（不知道過去有什麼）
        token = svc.changes().getStartPageToken().execute().get("startPageToken")
        state["pageToken"] = token
        state["notifiedFileIds"] = []
        save_state(state)
        print(f"[init] startPageToken={token}, 跳過此次通知")
        return

    page_token = state["pageToken"]
    notified = set(state.get("notifiedFileIds", []))
    new_token = page_token
    notified_count = 0

    while page_token:
        try:
            res = svc.changes().list(
                pageToken=page_token,
                fields="nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,parents,trashed,modifiedTime,lastModifyingUser(emailAddress,displayName),webViewLink))",
                pageSize=100,
                includeRemoved=False,
                spaces="drive",
            ).execute()
        except HttpError as e:
            print(f"[changes.list err] {e}")
            return

        for ch in res.get("changes", []):
            f = ch.get("file")
            if not f:
                if os.environ.get("DEBUG"): print(f"[skip:nofile] {ch}")
                continue
            if f.get("trashed"):
                if os.environ.get("DEBUG"): print(f"[skip:trashed] {f.get('name')}")
                continue
            if f.get("mimeType") == "application/vnd.google-apps.folder":
                if os.environ.get("DEBUG"): print(f"[skip:folder] {f.get('name')}")
                continue
            if not is_video(f.get("name", "")):
                if os.environ.get("DEBUG"): print(f"[skip:nonvideo] {f.get('name')}")
                continue
            if f["id"] in notified:
                if os.environ.get("DEBUG"): print(f"[skip:dup] {f.get('name')}")
                continue
            uploader = (f.get("lastModifyingUser") or {}).get("emailAddress", "")
            if uploader.lower() == SELF_EMAIL.lower():
                if os.environ.get("DEBUG"): print(f"[skip:self] {f.get('name')} by {uploader}")
                continue
            if os.environ.get("DEBUG"): print(f"[match] {f.get('name')} by {uploader} parents={f.get('parents')}")
            # 確認落在 案件/輸出/
            ctx = find_output_case(svc, f)
            if not ctx:
                continue
            kind = detect_kind(svc, ctx["output_id"])
            output_url = f"https://drive.google.com/drive/folders/{ctx['output_id']}"
            text = f"{ctx['case_name']} {kind}囉\n雲端連結：{output_url}"
            if push_line(text):
                notified.add(f["id"])
                notified_count += 1
                print(f"[notify] {kind} {ctx['case_name']} ← {f['name']} by {uploader}")

        if "nextPageToken" in res:
            page_token = res["nextPageToken"]
        else:
            new_token = res.get("newStartPageToken", page_token)
            break

    state["pageToken"] = new_token
    # 只保留最近 1000 個 file id，避免無限長
    state["notifiedFileIds"] = list(notified)[-1000:]
    save_state(state)
    print(f"[done] notified={notified_count} newToken={new_token}")

if __name__ == "__main__":
    main()
