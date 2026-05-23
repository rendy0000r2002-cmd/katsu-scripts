"""直接從 NAS 內部繞過 auth 打 API（用一個 dev cookie 或直接打 supabase 重現邏輯）"""
from __future__ import annotations
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='***REDACTED-NAS-PASS***', timeout=15)

# 直接從 container 內 dump search route 的實際 source（看 turbopack module 51604 內容）
# 試法：node -e 動態 require route.js & inspect
script = r'''
const path = require('path');
process.chdir('/app');
try {
  // 看現在的 route handler 是不是有 relaxQuery
  const fs = require('fs');
  const chunks = [
    'server/chunks/[root-of-the-server]__119mu0o._.js',
  ];
  for (const c of chunks) {
    const t = fs.readFileSync(path.join('/app/.next', c), 'utf8');
    console.log('===', c, t.length, 'bytes ===');
    // 印出 search/route 相關片段
    console.log(t.length, 'bytes');
  }
} catch (e) { console.error(e.stack); }
'''
import base64
b64 = base64.b64encode(script.encode()).decode()
cmd = (
    f"echo ***REDACTED-NAS-PASS*** | sudo -S /usr/local/bin/docker exec katsu-web sh -c "
    f"\"echo {b64} | base64 -d > /tmp/probe.js && node /tmp/probe.js\""
)
_, so, _ = client.exec_command(cmd, get_pty=True, timeout=60)
print(so.read().decode(errors='ignore'))
client.close()
