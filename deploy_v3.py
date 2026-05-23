"""Deploy filename parser update."""
import base64, paramiko, sys, io, time, re
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HOST = "192.168.18.6"
USER = "ETtomorrow"
PASSWORD = "***REDACTED-NAS-PASS***"
LOCAL_BASE = Path(r"C:\Users\rendy\原初映像片庫\web")
REMOTE_BASE = "/volume2/docker-prod/katsu-web-v2/web"
FILES = ["lib/filename.ts", "components/UploadApp.tsx"]


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=10)
    try:
        for rel in FILES:
            local = LOCAL_BASE / rel.replace("/", "\\")
            remote = f"{REMOTE_BASE}/{rel}"
            data = local.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            print(f"  uploading {rel} ({len(data)} bytes)...")
            stdin, stdout, stderr = c.exec_command(f"base64 -d > {remote}")
            stdin.write(b64); stdin.channel.shutdown_write()
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                print(f"    FAILED: {stderr.read().decode()}"); return 1
            _, out, _ = c.exec_command(f"wc -c < {remote}")
            assert int(out.read().decode().strip()) == len(data)
            print(f"    OK")

        print("\nRebuilding...")
        chan = c.get_transport().open_session()
        chan.get_pty()
        chan.exec_command(
            "cd /volume2/docker-prod/katsu-web-v2/web && echo ***REDACTED-NAS-PASS*** | sudo -S bash -lc "
            "'docker compose build app 2>&1 | tail -5 && docker compose up -d app'"
        )
        buf = b""; last = time.time()
        while True:
            if chan.recv_ready():
                buf += chan.recv(8192); last = time.time()
            elif chan.exit_status_ready():
                while chan.recv_ready(): buf += chan.recv(8192)
                break
            else:
                time.sleep(0.5)
                if time.time() - last > 30:
                    print(".", end="", flush=True); last = time.time()
        clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", buf.decode("utf-8", "replace"))
        clean = re.sub(r"⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏|⠿", "", clean)
        lines = [l for l in clean.split("\n") if l.strip() and "Container katsu-web" not in l]
        for l in lines[-15:]:
            print(l)
        return chan.recv_exit_status()
    finally:
        c.close()


sys.exit(main())
