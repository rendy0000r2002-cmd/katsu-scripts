from __future__ import annotations
import paramiko
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='Et666666', timeout=15)
for cmd in [
    "which docker",
    "ls /usr/local/bin/docker /usr/local/sbin/docker /usr/bin/docker 2>/dev/null",
    "echo Et666666 | sudo -S which docker",
    "echo Et666666 | sudo -S bash -lc 'which docker'",
]:
    print(f'>>> {cmd}')
    _, stdout, stderr = client.exec_command(cmd, get_pty=True)
    print(stdout.read().decode(errors='ignore'))
    err = stderr.read().decode(errors='ignore')
    if err.strip(): print('STDERR:', err)
client.close()
