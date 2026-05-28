"""
偵測 + 清理 NAS DB 中實體檔不存在的紀錄（兩階段 + Telegram 確認）。

執行模式：
  python cleanup_missing_files.py                    # detect 模式（預設）
  python cleanup_missing_files.py --execute --token X # 真的刪除（要 token 對得上 pending）
  python cleanup_missing_files.py --cancel  --token X # 取消這次

互動流程：
  1. cron 每日 04:00 跑 detect 模式
  2. 偵測到失蹤檔 → 寫 cleanup_pending.json (含 token) → 發 Telegram 含 inline 按鈕
  3. 你在 Telegram 點「✅ 確認刪除」→ telegram_callback_listener.py 收到 callback
     → 自動跑 --execute --token X
  4. 點「❌ 取消」→ 跑 --cancel --token X，清掉 pending

安全機制：
  - Volume mount health check（整個 volume 掛掉就 abort，不誤刪）
  - Pending 超過 7 天自動失效（防止舊 pending 被誤觸發）
  - 單次最多刪 max-delete 筆（預設 500）
"""
from __future__ import annotations
import argparse
import json
import os
import secrets
import sys
import urllib.request
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from supabase import create_client

from nas_roots import ALL_ROOTS, convert_path, detect_platform, find_root_for

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
ENV_PATH = ROOT / ".env"
PENDING_TTL_DAYS = 7

# Telegram（雙向對話用，跟通知類 LINE 區分）
TG_TOKEN = "8583367633:AAFjQyLGLvYrWOZtOrtWm_vpaVpq_pXWBhY"
TG_CHAT = "8635121564"

# 片庫網站 URL（用於 confirm link）
WEB_BASE_URL = os.environ.get("WEB_BASE_URL") or "https://randynas.tailb1ff82.ts.net"


def _pending_file() -> Path:
    """pending 檔位置：
    - NAS / container：/volume2/homes/ETtomorrow/.cleanup_pending.json（katsu-web 可讀寫）
    - PC（測試用）：腳本同目錄
    """
    shared = Path("/volume2/homes/ETtomorrow/.cleanup_pending.json")
    if shared.parent.exists():
        return shared
    return ROOT / "cleanup_pending.json"


PENDING_FILE = _pending_file()


def load_env() -> dict:
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / f"cleanup_missing_{date.today():%Y%m%d}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def send_tg(text: str, reply_markup: dict | None = None) -> int | None:
    """送 Telegram 訊息，可選擇帶 inline keyboard。回傳 message_id（供後續編輯用）"""
    body: dict = {
        "chat_id": TG_CHAT,
        "text": text[:4000],
        "parse_mode": "HTML",
    }
    if reply_markup:
        body["reply_markup"] = reply_markup
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
        return resp.get("result", {}).get("message_id")
    except Exception as e:
        log(f"TG send 失敗: {e}")
        return None


def edit_tg_message(message_id: int, text: str, remove_keyboard: bool = True) -> None:
    """編輯既有 Telegram 訊息（按鈕點完後更新狀態用）"""
    body = {
        "chat_id": TG_CHAT,
        "message_id": message_id,
        "text": text[:4000],
        "parse_mode": "HTML",
    }
    if not remove_keyboard:
        pass  # 想保留按鈕的話另設
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/editMessageText",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        log(f"TG edit 失敗: {e}")


def to_local_path(nas_share_url: str) -> str | None:
    if not nas_share_url:
        return None
    target = detect_platform()
    return convert_path(nas_share_url, target_platform=target)


def check_mounts(sb) -> dict[str, bool]:
    """每個 volume 跑健康檢查"""
    results = {}
    for r in ALL_ROOTS:
        target = detect_platform()
        root_local = convert_path(r.linux, target_platform=target) or r.linux
        if not Path(root_local).exists():
            log(f"  [{r.volume}] root NOT mounted: {root_local}")
            results[r.volume] = False
            continue
        prefix = r.linux.rstrip("/") + "/"
        q = sb.table("videos").select("drive_file_id,nas_share_url").eq("source", "nas").like("nas_share_url", f"{prefix}%").limit(1).execute()
        if not q.data:
            log(f"  [{r.volume}] no records to validate（假設 OK）")
            results[r.volume] = True
            continue
        local = to_local_path(q.data[0]["nas_share_url"])
        if local and Path(local).exists():
            results[r.volume] = True
            log(f"  [{r.volume}] mount OK")
        else:
            log(f"  [{r.volume}] sample missing；整個 volume 可能掛掉 → 跳過")
            results[r.volume] = False
    return results


def detect(sb, max_preview: int = 20) -> dict | None:
    """detect 模式：找出所有實體不存在的紀錄，回傳 pending dict（或 None 表示無）"""
    log("檢查各 volume mount 狀態...")
    mounts = check_mounts(sb)
    healthy = {v for v, ok in mounts.items() if ok}
    if not healthy:
        msg = "⚠️ <b>cleanup_missing_files</b>\n所有 volume 都不健康，abort 不刪任何東西"
        log(msg)
        send_tg(msg)
        return None

    log("撈 DB 所有 NAS 影片紀錄...")
    all_records = []
    last_id = ""
    page = 1000
    while True:
        q = sb.table("videos").select("drive_file_id,nas_share_url,filename").eq("source", "nas").order("drive_file_id").limit(page)
        if last_id:
            q = q.gt("drive_file_id", last_id)
        r = q.execute()
        if not r.data:
            break
        all_records.extend(r.data)
        last_id = r.data[-1]["drive_file_id"]
        if len(r.data) < page:
            break
    log(f"DB 總共 {len(all_records)} 筆 NAS 紀錄")

    missing = []
    skipped_unhealthy = 0
    for v in all_records:
        url = v.get("nas_share_url") or ""
        if not url:
            continue
        root = find_root_for(url)
        if not root or root.volume not in healthy:
            skipped_unhealthy += 1
            continue
        local = to_local_path(url)
        if not local:
            continue
        try:
            if not Path(local).exists():
                missing.append(v)
        except Exception:
            continue

    log(f"檢查完成：實體檔不存在 {len(missing)} 筆（跳過不健康 volume 的紀錄 {skipped_unhealthy} 筆）")

    if not missing:
        send_tg(f"📁 <b>cleanup_missing_files</b>\n\n今日無異常，{len(all_records)} 筆 NAS 紀錄全對得上 ✓")
        return None

    # 寫 pending file
    token = secrets.token_urlsafe(12)
    pending = {
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_records": len(all_records),
        "missing_count": len(missing),
        "healthy_volumes": sorted(healthy),
        "ids": [v["drive_file_id"] for v in missing],
        "preview": [
            {"id": v["drive_file_id"][:25], "url": (v.get("nas_share_url") or "")[:120]}
            for v in missing[:max_preview]
        ],
    }
    PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"寫入 pending: token={token}, {len(missing)} 筆待確認")

    # 發 Telegram 含 web 連結
    preview_lines = [
        f"  • {p['url']}" for p in pending["preview"]
    ]
    more = ""
    if len(missing) > max_preview:
        more = f"\n  ... 還有 {len(missing) - max_preview} 筆"
    confirm_url = f"{WEB_BASE_URL}/admin/cleanup-confirm?token={token}"
    text = (
        f"📁 <b>cleanup_missing_files</b>\n"
        f"\n"
        f"檢查 {len(all_records)} 筆 NAS 紀錄\n"
        f"<b>實體檔不存在：{len(missing)} 筆</b>\n"
        f"\n"
        f"前 {min(max_preview, len(missing))} 筆預覽：\n"
        + "\n".join(preview_lines)
        + more
        + f'\n\n<b>👉 <a href="{confirm_url}">點此確認 / 取消</a></b>\n'
        + f"\nToken: <code>{token}</code>\n"
        + f"（{PENDING_TTL_DAYS} 天後過期）"
    )
    msg_id = send_tg(text)
    if msg_id:
        pending["message_id"] = msg_id
        PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return pending


def execute(sb, token: str, max_delete: int = 500) -> None:
    if not PENDING_FILE.exists():
        log("沒有 pending file，nothing to execute")
        send_tg("⚠️ <b>cleanup_missing_files</b> execute 失敗：沒有 pending file")
        return
    pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    if pending.get("token") != token:
        log(f"token 不對：expected={pending.get('token')[:10]}... got={token[:10]}...")
        send_tg("⚠️ <b>cleanup_missing_files</b> execute 失敗：token 不對（可能舊的 pending 過期，已重新偵測）")
        return
    # TTL 檢查
    created = datetime.fromisoformat(pending["created_at"])
    if datetime.now(timezone.utc) - created > timedelta(days=PENDING_TTL_DAYS):
        log("pending 過期 (> 7 天)")
        send_tg(f"⚠️ <b>cleanup_missing_files</b> execute 失敗：pending 超過 {PENDING_TTL_DAYS} 天已過期，請等下次 detect")
        PENDING_FILE.unlink()
        return

    ids = pending["ids"][:max_delete]
    capped = len(pending["ids"]) - len(ids)
    log(f"執行刪除 {len(ids)} 筆" + (f"（capped, 剩 {capped} 筆下次再跑）" if capped else ""))

    batch = 50
    deleted = 0
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        try:
            sb.table("videos").delete().in_("drive_file_id", chunk).execute()
            deleted += len(chunk)
        except Exception as e:
            log(f"刪 batch [{i}:{i+batch}] 失敗: {e}")

    # 結算
    text = (
        f"✅ <b>cleanup_missing_files</b> 已執行\n"
        f"\n"
        f"刪除 <b>{deleted}</b> 筆失蹤檔紀錄\n"
    )
    if capped:
        text += f"\n剩 {capped} 筆下次 detect 會再列\n"
    text += f"\nToken: <code>{token}</code>"

    msg_id = pending.get("message_id")
    if msg_id:
        edit_tg_message(msg_id, text)
    else:
        send_tg(text)

    PENDING_FILE.unlink()
    log("execute done, pending file removed")


def cancel(token: str) -> None:
    if not PENDING_FILE.exists():
        log("沒有 pending file，nothing to cancel")
        return
    pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    if pending.get("token") != token:
        log("token 不對，不取消")
        return
    msg_id = pending.get("message_id")
    cnt = pending.get("missing_count", 0)
    PENDING_FILE.unlink()
    text = (
        f"❌ <b>cleanup_missing_files</b> 已取消\n"
        f"\n"
        f"不刪除 {cnt} 筆失蹤檔紀錄。下次 04:00 偵測會重新列。"
    )
    if msg_id:
        edit_tg_message(msg_id, text)
    else:
        send_tg(text)
    log(f"cancel done, pending removed, token={token[:10]}...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="執行刪除（需 --token）")
    ap.add_argument("--cancel", action="store_true", help="取消這次 pending（需 --token）")
    ap.add_argument("--token", type=str, default=None, help="pending token")
    ap.add_argument("--max-delete", type=int, default=500)
    args = ap.parse_args()

    env = load_env()
    sb = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])

    if args.execute:
        if not args.token:
            print("--execute 需要 --token", file=sys.stderr)
            sys.exit(2)
        log(f"=== execute mode (token={args.token[:10]}...) ===")
        execute(sb, args.token, args.max_delete)
    elif args.cancel:
        if not args.token:
            print("--cancel 需要 --token", file=sys.stderr)
            sys.exit(2)
        log(f"=== cancel mode (token={args.token[:10]}...) ===")
        cancel(args.token)
    else:
        log("=== detect mode ===")
        detect(sb)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log(f"FATAL: {e}\n{tb}")
        send_tg(f"⚠️ <b>cleanup_missing_files</b> 失敗\n\n<pre>{tb[-500:]}</pre>")
        raise
