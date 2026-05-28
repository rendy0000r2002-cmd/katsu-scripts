"""
用 paramiko 上傳 probe_codecs.mjs 到 NAS，docker cp 進 container 然後 node 跑
"""
import paramiko
import sys, os, base64

NAS_HOST = '192.168.18.6'
NAS_USER = 'ETtomorrow'
NAS_PASS = 'Et666666'
LOCAL = os.path.join(os.path.dirname(__file__), 'probe_codecs.mjs')
REMOTE = '/volume2/docker-prod/katsu-web-v2/probe_codecs.mjs'

def run(client, cmd, label, stream=True):
    print(f'[*] {label}', flush=True)
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=None)
    if stream:
        for line in iter(stdout.readline, ''):
            if not line:
                break
            print(line.rstrip(), flush=True)
    else:
        out = stdout.read().decode(errors='ignore')
        if out.strip(): print(out, flush=True)
    err = stderr.read().decode(errors='ignore')
    if err.strip():
        print('[stderr]', err.rstrip(), flush=True)

def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f'[*] Connecting to {NAS_USER}@{NAS_HOST}...', flush=True)
    client.connect(NAS_HOST, username=NAS_USER, password=NAS_PASS, timeout=15)

    with open(LOCAL, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    print(f'[*] Uploading via base64 ({len(b64)} chars) -> {REMOTE}', flush=True)
    run(client, f"echo {b64} | base64 -d > {REMOTE} && ls -la {REMOTE}", 'upload', stream=False)
    print('[+] Upload OK', flush=True)

    cmd = (
        f"echo {NAS_PASS} | sudo -S bash -lc "
        f"'docker cp {REMOTE} katsu-web:/tmp/probe_codecs.mjs && "
        f"docker exec katsu-web node /tmp/probe_codecs.mjs'"
    )
    run(client, cmd, 'Running ffprobe scan in katsu-web container...', stream=True)
    client.close()
    print('[+] DONE', flush=True)

if __name__ == '__main__':
    main()
