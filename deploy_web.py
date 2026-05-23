"""
用 paramiko 部署 web 檔案到 NAS，重 build container
"""
import paramiko
import os, base64, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

NAS_HOST = '192.168.18.6'
NAS_USER = 'ETtomorrow'
NAS_PASS = '***REDACTED-NAS-PASS***'
NAS_WEB = '/volume2/docker-prod/katsu-web-v2/web'
LOCAL_WEB = os.path.join(os.path.dirname(__file__), 'web')

FILES = [
    'components/SearchApp.tsx',
    'app/api/search/route.ts',
    'app/api/case-names/route.ts',
    'app/api/download-zip/route.ts',
    'app/api/stream/[id]/route.ts',
    'auth.ts',
]

def run(client, cmd, label, stream=True):
    print(f'[*] {label}', flush=True)
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=None)
    if stream:
        for line in iter(stdout.readline, ''):
            if not line: break
            print(line.rstrip(), flush=True)
    else:
        out = stdout.read().decode(errors='ignore')
        if out.strip(): print(out.rstrip(), flush=True)
    err = stderr.read().decode(errors='ignore')
    if err.strip(): print('[stderr]', err.rstrip(), flush=True)

def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f'[*] Connecting to {NAS_USER}@{NAS_HOST}...', flush=True)
    client.connect(NAS_HOST, username=NAS_USER, password=NAS_PASS, timeout=15)

    for rel in FILES:
        local = os.path.join(LOCAL_WEB, rel.replace('/', os.sep))
        remote = f'{NAS_WEB}/{rel}'
        remote_dir = os.path.dirname(remote)
        if not os.path.exists(local):
            print(f'[!] missing local file: {local}', flush=True)
            continue
        with open(local, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        cmd = f"mkdir -p '{remote_dir}' && echo {b64} | base64 -d > '{remote}' && ls -la '{remote}'"
        run(client, cmd, f'upload {rel}', stream=False)

    rebuild = (
        f"echo {NAS_PASS} | sudo -S bash -lc "
        f"'cd {NAS_WEB} && /usr/local/bin/docker compose build app && /usr/local/bin/docker compose up -d app'"
    )
    run(client, rebuild, 'Rebuilding container...', stream=True)
    client.close()
    print('[+] DONE — hard refresh browser (Ctrl+Shift+R)', flush=True)

if __name__ == '__main__':
    main()
