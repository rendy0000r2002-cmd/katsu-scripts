"""檢查 deployed bundle (簡化版)"""
from __future__ import annotations
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='***REDACTED-NAS-PASS***', timeout=15)

# 先看 chunks 目錄列表 + mtime
cmd = "echo ***REDACTED-NAS-PASS*** | sudo -S -p '' /usr/local/bin/docker exec katsu-web ls -la /app/.next/static/chunks/ 2>&1 | head -30"
_, so, _ = client.exec_command(cmd, timeout=30)
print('=== static/chunks ===')
print(so.read().decode(errors='ignore'))

cmd = "echo ***REDACTED-NAS-PASS*** | sudo -S -p '' /usr/local/bin/docker exec katsu-web ls -la /app/.next/server/chunks/ 2>&1 | head -30"
_, so, _ = client.exec_command(cmd, timeout=30)
print('=== server/chunks ===')
print(so.read().decode(errors='ignore'))

client.close()
