"""grep deployed client bundle for new keywords"""
from __future__ import annotations
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='Et666666', timeout=15)

# 找出所有 chunks 檔案中 setActiveSchedule(null) 出現次數
cmd = (
    "echo Et666666 | sudo -S -p '' /usr/local/bin/docker exec katsu-web sh -c "
    "'for f in /app/.next/static/chunks/*.js /app/.next/server/chunks/*.js /app/.next/server/chunks/ssr/*.js; do "
    "if [ -f \"$f\" ]; then "
    "  c1=$(grep -o \"setActiveSchedule(null)\" \"$f\" 2>/dev/null | wc -l); "
    "  c2=$(grep -o \"activeSchedule\" \"$f\" 2>/dev/null | wc -l); "
    "  if [ \"$c2\" -gt 0 ]; then echo \"$f setActiveSchedule(null)=$c1 activeSchedule=$c2\"; fi; "
    "fi; done' 2>&1"
)
_, so, _ = client.exec_command(cmd, timeout=60)
print(so.read().decode(errors='ignore'))
client.close()
