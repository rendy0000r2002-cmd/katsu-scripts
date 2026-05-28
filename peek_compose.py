import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='Et666666', timeout=15)
_, stdout, _ = client.exec_command("cat /volume2/docker-prod/katsu-web-v2/web/docker-compose.yml", get_pty=True)
print(stdout.read().decode(errors='ignore'))
client.close()
