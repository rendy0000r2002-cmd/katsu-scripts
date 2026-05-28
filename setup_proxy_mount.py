"""
1. host 上建 _proxies 目錄
2. 改 docker-compose.yml: 加 writable proxy mount + PROXY_DIR env
3. 重啟 container
"""
import paramiko, sys, base64
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

NAS_HOST = '192.168.18.6'
NAS_USER = 'ETtomorrow'
NAS_PASS = 'Et666666'

NEW_COMPOSE = """services:
  app:
    build: .
    image: katsu-web:latest
    container_name: katsu-web
    restart: unless-stopped
    environment:
      AUTH_SECRET: ${AUTH_SECRET}
      AUTH_URL: ${AUTH_URL}
      AUTH_TRUST_HOST: "true"
      AUTH_GOOGLE_ID: ${AUTH_GOOGLE_ID}
      AUTH_GOOGLE_SECRET: ${AUTH_GOOGLE_SECRET}
      SUPABASE_URL: ${SUPABASE_URL}
      SUPABASE_SERVICE_ROLE_KEY: ${SUPABASE_SERVICE_ROLE_KEY}
      NEXT_PUBLIC_SUPABASE_URL: ${NEXT_PUBLIC_SUPABASE_URL}
      NEXT_PUBLIC_SUPABASE_ANON_KEY: ${NEXT_PUBLIC_SUPABASE_ANON_KEY}
      NAS_LINUX_ROOT: /volume2
      NAS_HOME_LINUX: /volume2/homes/ETtomorrow
      THUMB_CACHE_DIR: /data/thumb-cache
      PROXY_DIR: /proxies
    volumes:
      - /volume2:/volume2:ro
      - /volume2/homes/ETtomorrow/_proxies:/proxies:rw
      - thumb-cache:/data/thumb-cache
    ports:
      - "3000:3000"

volumes:
  thumb-cache:
"""

def run(client, cmd, label, stream=True):
    print(f'[*] {label}', flush=True)
    _, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=None)
    if stream:
        for line in iter(stdout.readline, ''):
            if not line: break
            print(line.rstrip(), flush=True)
    else:
        out = stdout.read().decode(errors='ignore').rstrip()
        if out: print(out, flush=True)
    err = stderr.read().decode(errors='ignore')
    if err.strip(): print('[stderr]', err.rstrip(), flush=True)

def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print('[*] Connecting...', flush=True)
    client.connect(NAS_HOST, username=NAS_USER, password=NAS_PASS, timeout=15)

    run(client, "mkdir -p /volume2/homes/ETtomorrow/_proxies && ls -la /volume2/homes/ETtomorrow/_proxies",
        'create _proxies dir on host', stream=False)

    b64 = base64.b64encode(NEW_COMPOSE.encode()).decode()
    run(client, f"echo {b64} | base64 -d > /volume2/docker-prod/katsu-web-v2/web/docker-compose.yml && cat /volume2/docker-prod/katsu-web-v2/web/docker-compose.yml",
        'write new docker-compose.yml', stream=False)

    rebuild = (
        f"echo {NAS_PASS} | sudo -S bash -lc "
        f"'cd /volume2/docker-prod/katsu-web-v2/web && /usr/local/bin/docker compose up -d app'"
    )
    run(client, rebuild, 'recreate container with new mount', stream=True)
    client.close()
    print('[+] DONE', flush=True)

if __name__ == '__main__':
    main()
