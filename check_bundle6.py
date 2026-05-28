"""grep deployed bundle for preserved string literals"""
from __future__ import annotations
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='Et666666', timeout=15)

# 不被 minify 的字串：URL params, JSX 文字, JSON keys
keywords = [
    "excludeCaseName",       # URL param key
    "activeSchedule",        # 變數名（會被 minify）
    "case_name",             # JSON key
    "已優先顯示",              # 中文 JSX
    "全系列其他",              # 萬一有
]
for kw in keywords:
    cmd = (
        f"echo Et666666 | sudo -S -p '' /usr/local/bin/docker exec katsu-web sh -c "
        f"\"grep -rln '{kw}' /app/.next/static/chunks/ /app/.next/server/chunks/ 2>/dev/null\""
    )
    _, so, _ = client.exec_command(cmd, timeout=30)
    out = so.read().decode(errors='ignore').strip()
    print(f'=== {kw} ===')
    print(out if out else '(none)')
client.close()
