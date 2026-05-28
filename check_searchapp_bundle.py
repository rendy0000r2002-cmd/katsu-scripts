"""檢查 client side bundle 是否含有最新的 onChange 邏輯"""
from __future__ import annotations
import paramiko, sys, base64
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='Et666666', timeout=15)

script = r'''
const fs = require('fs');
const path = require('path');
const dirs = ['/app/.next/static/chunks', '/app/.next/server/chunks/ssr', '/app/.next/server/chunks'];
for (const dir of dirs) {
  let files;
  try { files = fs.readdirSync(dir); } catch { continue; }
  for (const f of files) {
    const p = path.join(dir, f);
    let stat;
    try { stat = fs.statSync(p); } catch { continue; }
    if (!stat.isFile()) continue;
    let t;
    try { t = fs.readFileSync(p, 'utf8'); } catch { continue; }
    if (t.includes('activeSchedule')) {
      console.log('FOUND:', p, t.length, 'bytes');
      // 檢查是否有 setActiveSchedule(null) 在 onChange 旁
      const idx = t.indexOf('setActiveSchedule(null)');
      console.log('  setActiveSchedule(null) count:', (t.match(/setActiveSchedule\(null\)/g)||[]).length);
      console.log('  case_name count:', (t.match(/case_name/g)||[]).length);
      console.log('  setCity("") count:', (t.match(/setCity\(""\)/g)||[]).length);
    }
  }
}
'''
b64 = base64.b64encode(script.encode()).decode()
cmd = (
    f"echo Et666666 | sudo -S /usr/local/bin/docker exec katsu-web sh -c "
    f"\"echo {b64} | base64 -d > /tmp/probe.js && node /tmp/probe.js\""
)
_, so, _ = client.exec_command(cmd, get_pty=True, timeout=60)
print(so.read().decode(errors='ignore'))
client.close()
