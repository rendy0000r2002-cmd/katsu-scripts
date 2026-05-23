from __future__ import annotations
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='***REDACTED-NAS-PASS***', timeout=15)
cmd = "echo ***REDACTED-NAS-PASS*** | sudo -S bash -lc '/usr/local/bin/docker exec katsu-web tail -50 /tmp/proxies.log'"
_, stdout, stderr = client.exec_command(cmd, get_pty=True)
print(stdout.read().decode(errors='ignore'))
err = stderr.read().decode(errors='ignore')
if err.strip(): print('STDERR:', err)
client.close()
