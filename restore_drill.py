"""Weekly backup restore drill.

跑在 katsu-scripts container（host network、可裝 psycopg2）。
連到 pg-katsu 127.0.0.1:5443 建臨時 DB、apply schemas、bulk insert from zip JSONL、verify、drop。

呼叫方式：
  docker exec katsu-scripts python /volume2/docker-prod/scripts/原初映像片庫/restore_drill.py

排程：
  30 4 * * 0 weekly Sunday 04:30
"""
from __future__ import annotations
import json
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

# psycopg2-binary 在 katsu-scripts 容器內可能沒有 → 先試 import，缺就 pip install
try:
    import psycopg2
    from psycopg2.extras import execute_values, Json
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "psycopg2-binary"])
    import psycopg2
    from psycopg2.extras import execute_values, Json


def adapt_value(v):
    """Wrap dict/list-of-dict as psycopg2 Json so jsonb columns accept it."""
    if isinstance(v, (dict,)):
        return Json(v)
    return v

INIT_DIR = Path("/volume2/docker-prod/pg-katsu-v2/init")
BACKUPS_DIR = Path("/volume2/docker-prod/scripts/原初映像片庫/backups")
SCRIPTS_ENV = Path("/volume2/docker-prod/scripts/原初映像片庫/.env")
PG_ENV = Path("/volume2/docker-prod/pg-katsu-v2/.env")

THRESHOLDS = {
    "videos": 30000,
    "login_logs": 1000,
    "case_locations": 500,
}


def load_env(p: Path) -> dict:
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def push_line(msg: str) -> None:
    env = load_env(SCRIPTS_ENV)
    tok = env.get("LINE_CHANNEL_ACCESS_TOKEN")
    uid = env.get("LINE_USER_ID")
    if not (tok and uid):
        print("(no LINE creds)")
        return
    body = json.dumps({"to": uid, "messages": [{"type": "text", "text": msg[:4900]}]}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"line push fail: {e}")


def conn(database: str):
    pg_env = load_env(PG_ENV)
    return psycopg2.connect(
        host="127.0.0.1",
        port=5443,
        dbname=database,
        user="postgres",
        password=pg_env["POSTGRES_PASSWORD"],
    )


def latest_backup() -> Path:
    zips = sorted(BACKUPS_DIR.glob("vlbackup_*.zip"))
    if not zips:
        raise RuntimeError("no backup zip")
    return zips[-1]


def main() -> int:
    started = datetime.now()
    zp = latest_backup()
    drill_db = f"drill_{started.strftime('%Y%m%d_%H%M%S')}"
    print(f"[drill] zip={zp.name} db={drill_db}")

    # 1. Create disposable DB on default postgres DB
    c0 = conn("postgres")
    c0.autocommit = True
    cur = c0.cursor()
    cur.execute(f'CREATE DATABASE "{drill_db}"')
    cur.close()
    c0.close()
    print(f"[drill] created")

    try:
        # 2. Apply schemas
        cdb = conn(drill_db)
        cdb.autocommit = True
        cur = cdb.cursor()
        for sql in sorted(INIT_DIR.glob("*.sql")):
            try:
                cur.execute(sql.read_text(encoding="utf-8"))
                print(f"[drill] applied {sql.name}")
            except psycopg2.Error as e:
                # Roles already exist OK to skip on duplicate
                print(f"[drill] {sql.name} skip: {str(e)[:120]}")
                cdb.rollback()
        cur.close()

        # 3. Restore tables
        counts: dict[str, int] = {}
        with zipfile.ZipFile(zp) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            for table, info in manifest.get("tables", {}).items():
                if "error" in info:
                    continue
                rows = []
                for line in zf.read(info["file"]).decode("utf-8").splitlines():
                    if line.strip():
                        r = json.loads(line)
                        if table == "videos":
                            r.pop("is_vertical", None)  # generated col
                        rows.append(r)
                if not rows:
                    counts[table] = 0
                    continue
                cols = sorted({k for r in rows for k in r.keys()})
                vals = [tuple(adapt_value(r.get(c)) for c in cols) for r in rows]
                col_sql = ", ".join(f'"{c}"' for c in cols)
                cur = cdb.cursor()
                try:
                    execute_values(
                        cur,
                        f"INSERT INTO {table} ({col_sql}) VALUES %s ON CONFLICT DO NOTHING",
                        vals,
                        page_size=500,
                    )
                    cdb.commit()
                except psycopg2.Error as e:
                    cdb.rollback()
                    print(f"[drill] insert {table} FAIL: {str(e)[:200]}")
                cur = cdb.cursor()
                cur.execute(f"SELECT count(*) FROM {table}")
                counts[table] = cur.fetchone()[0]
                cur.close()
                print(f"[drill] {table}: zip={len(rows)} restored={counts[table]}")

        cdb.close()

        # 4. Verify
        failures = []
        for table, threshold in THRESHOLDS.items():
            got = counts.get(table, 0)
            if got < threshold:
                failures.append(f"{table}: {got} < {threshold}")
        elapsed = (datetime.now() - started).total_seconds()

        if failures:
            msg = f"⚠️ 片庫 restore drill 失敗 ({zp.name}, {elapsed:.0f}s):\n" + "\n".join(failures)
            print(msg)
            push_line(msg)
            return 1
        else:
            print(f"✅ drill OK ({elapsed:.0f}s) counts={counts}")
            return 0
    finally:
        # 5. DROP DATABASE
        c0 = conn("postgres")
        c0.autocommit = True
        cur = c0.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{drill_db}"')
        cur.close()
        c0.close()
        print(f"[drill] dropped {drill_db}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        push_line(f"⚠️ 片庫 restore drill 例外: {e!r}")
        raise
