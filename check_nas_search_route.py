from __future__ import annotations
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='***REDACTED-NAS-PASS***', timeout=15)

# 看 container 內 search route 是哪一版
cmds = [
    # 找 priorityCity / priorityDistrict (新加的字串)
    "echo ***REDACTED-NAS-PASS*** | sudo -S /usr/local/bin/docker exec katsu-web sh -c 'grep -rlF priorityCity /app/.next/ 2>/dev/null | head -5'",
    # 找新 SELECT 中的 width,height (是否新版)
    "echo ***REDACTED-NAS-PASS*** | sudo -S /usr/local/bin/docker exec katsu-web sh -c 'grep -rlF \"width,height\" /app/.next/server/chunks/ 2>/dev/null | head -5'",
    # findPrimaryLocation 不在源碼是 string，看相關字串
    "echo ***REDACTED-NAS-PASS*** | sudo -S /usr/local/bin/docker exec katsu-web sh -c 'grep -rlF \"excludeCaseName\" /app/.next/server/chunks/ 2>/dev/null | head -5'",
    # 找 search_text (預設 ilike) 在哪些 chunk
    "echo ***REDACTED-NAS-PASS*** | sudo -S /usr/local/bin/docker exec katsu-web sh -c 'grep -lF search_text /app/.next/server/chunks/*.js 2>/dev/null'",
    # 看 119mu0o chunk 是否有 search_text
    "echo ***REDACTED-NAS-PASS*** | sudo -S /usr/local/bin/docker exec katsu-web sh -c 'grep -oF search_text /app/.next/server/chunks/\\[root-of-the-server\\]__119mu0o._.js | wc -l'",
]
for c in cmds:
    print(f'\n>>> {c[80:140]}')
    _, so, _ = client.exec_command(c, get_pty=True, timeout=30)
    print(so.read().decode(errors='ignore'))
client.close()
