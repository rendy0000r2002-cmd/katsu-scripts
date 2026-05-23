"""把 deployed client bundle 抓回本地 grep"""
from __future__ import annotations
import paramiko, sys, base64
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='***REDACTED-NAS-PASS***', timeout=15)

cmd = (
    "echo ***REDACTED-NAS-PASS*** | sudo -S -p '' /usr/local/bin/docker exec katsu-web "
    "cat /app/.next/static/chunks/145os_uszw7kv.js"
)
_, so, _ = client.exec_command(cmd, timeout=60)
data = so.read().decode(errors='ignore')
# 移除 sudo prompt 殘留
print('Bundle size:', len(data))

with open('client_bundle.js', 'w', encoding='utf-8') as f:
    f.write(data)

# 統計 case_name 出現次數和上下文
import re
positions = [m.start() for m in re.finditer(r'case_name', data)]
print(f'case_name 出現 {len(positions)} 次')
for i, p in enumerate(positions):
    snippet = data[max(0,p-80):p+80]
    print(f'\n--- pos {p} ---')
    print(snippet)

# 找 setCity 的呼叫
positions = [m.start() for m in re.finditer(r'setCity\b|setDistrict\b', data)]
print(f'\n\nsetCity/setDistrict 出現 {len(positions)} 次（預期至少有 6+ 個 location）')

# excludeCaseName context
positions = [m.start() for m in re.finditer(r'excludeCaseName', data)]
print(f'\nexcludeCaseName 出現 {len(positions)} 次')
for p in positions:
    print(data[max(0,p-50):p+100])
client.close()
