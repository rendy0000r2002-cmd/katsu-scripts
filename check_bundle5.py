"""簡化版：用 docker cp 把檔案抓出來，本地 grep"""
from __future__ import annotations
import paramiko, sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='***REDACTED-NAS-PASS***', timeout=15)

# 在 NAS 上 grep static/chunks 找有 activeSchedule 的檔案
cmd = (
    "echo ***REDACTED-NAS-PASS*** | sudo -S -p '' /usr/local/bin/docker exec katsu-web "
    "grep -l activeSchedule /app/.next/static/chunks/0n~dq4kpx9xxx.js /app/.next/static/chunks/0p5.qr0b94vxo.js /app/.next/static/chunks/03~yq9q893hmn.js /app/.next/static/chunks/0dbhjjzl8qfwv.js /app/.next/static/chunks/0jvq16020fmqj.js /app/.next/static/chunks/145os_uszw7kv.js 2>/dev/null"
)
_, so, se = client.exec_command(cmd, timeout=30)
out = so.read().decode(errors='ignore')
err = se.read().decode(errors='ignore')
print('STDOUT:', out)
print('STDERR:', err[:500])
client.close()
