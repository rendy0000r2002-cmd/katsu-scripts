"""
.env 一致性檢查 — 偵測三個 .env 是否還指向自架 Postgres，沒誤被舊雲端 URL 蓋回去。

每天 cron 跑，異常就推 LINE。

預期：
  /volume2/docker-prod/scripts/原初映像片庫/.env         SUPABASE_URL=http://192.168.18.6:3011
  /volume2/docker-prod/scripts/原初映像片庫/has_host/.env SUPABASE_URL=http://192.168.18.6:3011
  + 所有檔的 SUPABASE_SERVICE_ROLE_KEY / SUPABASE_ANON_KEY 都是本機簽的 JWT（不是 sb_secret_ / sb_publishable_）
"""
from __future__ import annotations
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

JWT_WARN_DAYS = 30

# 預期值定義
EXPECT = {
    "/volume2/docker-prod/scripts/原初映像片庫/.env": "http://192.168.18.6:3011",
    "/volume2/docker-prod/scripts/原初映像片庫/has_host/.env": "http://192.168.18.6:3011",
}
CLOUD_MARKERS = ("supabase.co", "sb_secret_", "sb_publishable_")

SCRIPTS_ENV = Path("/volume2/docker-prod/scripts/原初映像片庫/.env")


def load_env(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# LINE creds from scripts .env (katsu-scripts container has no env_file，自己讀)
_E = load_env(SCRIPTS_ENV)
LINE_TOKEN = _E.get("LINE_CHANNEL_ACCESS_TOKEN") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER = _E.get("LINE_USER_ID") or os.environ.get("LINE_USER_ID")


def push_line(msg: str) -> None:
    if not (LINE_TOKEN and LINE_USER):
        print("(no LINE creds, skip push)", flush=True)
        return
    body = json.dumps({
        "to": LINE_USER,
        "messages": [{"type": "text", "text": msg[:4900]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_TOKEN}",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"LINE push 失敗: {e}", file=sys.stderr)


def jwt_exp(token: str) -> int | None:
    """解 JWT payload 拿 exp（沒有 / 解不出來 → None）"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))
        exp = payload.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def main() -> int:
    problems: list[str] = []
    now = int(time.time())

    for path, expected_url in EXPECT.items():
        env = load_env(Path(path))
        if not env:
            problems.append(f"❌ {path} 不存在或讀不到")
            continue
        url = env.get("SUPABASE_URL", "")
        if url != expected_url:
            problems.append(f"❌ {path}\n  期望 SUPABASE_URL={expected_url}\n  實際 SUPABASE_URL={url}")
        for k in ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY"):
            v = env.get(k, "")
            if not v:
                continue
            if any(marker in v for marker in CLOUD_MARKERS):
                problems.append(f"❌ {path} 的 {k} 還是雲端值 ({v[:30]}...)")
                continue
            exp = jwt_exp(v)
            if exp is not None:
                days_left = (exp - now) // 86400
                if days_left < 0:
                    problems.append(f"❌ {path} 的 {k} JWT 已過期 {-days_left} 天")
                elif days_left < JWT_WARN_DAYS:
                    problems.append(f"⚠️ {path} 的 {k} JWT 剩 {days_left} 天到期（要重簽）")
        nxt = env.get("NEXT_PUBLIC_SUPABASE_URL")
        if nxt and nxt != expected_url:
            problems.append(f"❌ {path}\n  期望 NEXT_PUBLIC_SUPABASE_URL={expected_url}\n  實際 NEXT_PUBLIC_SUPABASE_URL={nxt}")

    if not problems:
        print("✓ 所有 .env 一致")
        return 0

    msg = "⚠️ 片庫 .env 一致性異常：\n\n" + "\n".join(problems)
    print(msg, file=sys.stderr)
    push_line(msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
