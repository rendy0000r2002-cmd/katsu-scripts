"""
上傳 generate_proxies.mjs 到 NAS，docker exec 在 container 裡跑（背景模式）
log 寫到 /volume2/docker-prod/katsu-web-v2/proxies.log
"""
import paramiko, os, base64, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

NAS_HOST = '192.168.18.6'
NAS_USER = 'ETtomorrow'
NAS_PASS = 'Et666666'
LOCAL = os.path.join(os.path.dirname(__file__), 'generate_proxies.mjs')
REMOTE_HOST = '/volume2/docker-prod/katsu-web-v2/generate_proxies.mjs'
LOG = '/volume2/docker-prod/katsu-web-v2/proxies.log'

def run(client, cmd, label, stream=True):
    print(f'[*] {label}', flush=True)
    _, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=None)
    if stream:
        for line in iter(stdout.readline, ''):
            if not line: break
            print(line.rstrip(), flush=True)
    else:
        print(stdout.read().decode(errors='ignore').rstrip(), flush=True)
    err = stderr.read().decode(errors='ignore')
    if err.strip(): print('[stderr]', err.rstrip(), flush=True)

def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f'[*] Connecting...', flush=True)
    client.connect(NAS_HOST, username=NAS_USER, password=NAS_PASS, timeout=15)

    with open(LOCAL, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    run(client, f"echo {b64} | base64 -d > {REMOTE_HOST} && ls -la {REMOTE_HOST}",
        'upload generate_proxies.mjs', stream=False)

    # 把腳本 cp 進 container 然後 nohup 背景跑
    cmd = (
        f"echo {NAS_PASS} | sudo -S bash -lc \""
        f"/usr/local/bin/docker cp {REMOTE_HOST} katsu-web:/tmp/generate_proxies.mjs && "
        f"/usr/local/bin/docker exec -d katsu-web sh -c "
        f"'node /tmp/generate_proxies.mjs > /tmp/proxies.log 2>&1' && "
        f"echo STARTED\""
    )
    run(client, cmd, 'Starting batch transcoder in container (detached)...', stream=True)
    client.close()
    print('[+] DONE — log: docker exec katsu-web tail -f /tmp/proxies.log', flush=True)

if __name__ == '__main__':
    main()
