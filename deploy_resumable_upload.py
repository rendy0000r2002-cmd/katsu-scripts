"""Deploy 3 changed files for resumable upload feature, then rebuild Docker."""
import base64
import paramiko
import sys
from pathlib import Path

HOST = "192.168.18.6"
USER = "ETtomorrow"
PASSWORD = "Et666666"

LOCAL_BASE = Path(r"C:\Users\rendy\原初映像片庫\web")
REMOTE_BASE = "/volume2/docker-prod/katsu-web-v2/web"

FILES = [
    "lib/drive.ts",
    "app/api/upload/route.ts",
    "components/UploadApp.tsx",
]


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {USER}@{HOST}...", flush=True)
    client.connect(HOST, username=USER, password=PASSWORD, timeout=10)

    try:
        for rel in FILES:
            local = LOCAL_BASE / rel.replace("/", "\\")
            remote = f"{REMOTE_BASE}/{rel}"
            data = local.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            print(f"  uploading {rel} ({len(data)} bytes)...", flush=True)
            cmd = f"base64 -d > {remote}"
            stdin, stdout, stderr = client.exec_command(cmd)
            stdin.write(b64)
            stdin.channel.shutdown_write()
            rc = stdout.channel.recv_exit_status()
            err = stderr.read().decode("utf-8", "replace")
            if rc != 0:
                print(f"    FAILED rc={rc}: {err}", flush=True)
                return 1
            check_cmd = f"wc -c < {remote}"
            _, out, _ = client.exec_command(check_cmd)
            size_remote = int(out.read().decode().strip())
            if size_remote != len(data):
                print(f"    SIZE MISMATCH local={len(data)} remote={size_remote}", flush=True)
                return 1
            print(f"    OK ({size_remote} bytes)", flush=True)

        print("\nRebuilding docker container...", flush=True)
        rebuild = (
            "cd /volume2/docker-prod/katsu-web-v2/web && "
            "echo Et666666 | sudo -S bash -lc "
            "'docker compose build app && docker compose up -d app'"
        )
        chan = client.get_transport().open_session()
        chan.get_pty()
        chan.exec_command(rebuild)
        while True:
            if chan.recv_ready():
                sys.stdout.write(chan.recv(4096).decode("utf-8", "replace"))
                sys.stdout.flush()
            if chan.recv_stderr_ready():
                sys.stderr.write(chan.recv_stderr(4096).decode("utf-8", "replace"))
                sys.stderr.flush()
            if chan.exit_status_ready():
                while chan.recv_ready():
                    sys.stdout.write(chan.recv(4096).decode("utf-8", "replace"))
                while chan.recv_stderr_ready():
                    sys.stderr.write(chan.recv_stderr(4096).decode("utf-8", "replace"))
                break
        rc = chan.recv_exit_status()
        print(f"\nrebuild exit code: {rc}", flush=True)
        return rc
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
