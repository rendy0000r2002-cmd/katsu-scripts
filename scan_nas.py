"""
NAS 掃描器 - 走 U:/home/ 下的頻道資料夾，規則與 scan.py 一致。
輸出 index_nas.json，結構相容 refine.py（同樣有 rel_path / channel_folder / case_folder / subpath）。

drive_file_id 用 `nas:<sha1(rel_path)[:16]>` 當 synthetic primary key，
跟 Drive 共用 videos 表；source 標 'nas'。
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 多 volume 支援：從 nas_roots 模組讀路徑清單
from nas_roots import existing_roots, get_prefix, synthetic_id as ns_synthetic_id

NAS_ROOTS = existing_roots()  # 當前 host 上實際 mount 的 root（v1 必有，v2 有就掃）
OUT_JSON = Path(__file__).parent / "index_nas.json"

VIDEO_EXT = {
    "mp4", "mov", "mkv", "avi", "m4v",
    "mts", "m2ts", "mxf", "wmv", "flv", "webm",
}

SKIP_FOLDERS = {"暫存", "雜物", "PING", "LUTS 調色檔", "AI字幕", "ET即賞屋模板", "tiles", "#recycle", "@eaDir", "_proxies"}
SKIP_PREFIXES = ("l_", "c_", "cf_", ".cache", "#", "@")
MAX_DEPTH = 8

YEAR_DEFAULT = datetime.now().year
CASE_DATE_RE = re.compile(r"^(\d{4})(.+)$")


def parse_channel(folder: str) -> dict:
    m = re.match(r"^(\d+)_?\s*(.+)$", folder)
    if m:
        return {"channel_order": int(m.group(1)), "channel_name": m.group(2).strip()}
    return {"channel_order": None, "channel_name": folder}


def parse_case(folder: str) -> dict:
    m = CASE_DATE_RE.match(folder)
    if m:
        mmdd = m.group(1)
        try:
            month = int(mmdd[:2]); day = int(mmdd[2:])
            if 1 <= month <= 12 and 1 <= day <= 31:
                return {
                    "case_date": f"{YEAR_DEFAULT}-{month:02d}-{day:02d}",
                    "case_name": m.group(2).strip(),
                }
        except ValueError:
            pass
    return {"case_date": None, "case_name": folder}


def classify_subpath(parts: list) -> str:
    joined = "/".join(parts).lower() if parts else ""
    for kw, label in [("輸出", "輸出"), ("修改", "修改"), ("cam", "拍帶"),
                      ("raw", "拍帶"), ("素材", "素材")]:
        if kw in joined:
            return label
    return "其他"


def synthetic_id(rel_path: str, volume: str = "v1") -> str:
    """delegate to nas_roots.synthetic_id (v1 保持原算法以維持向後相容)"""
    return ns_synthetic_id(rel_path, volume)


def walk(root: Path, path_parts: list, rows: list, volume: str, depth: int = 0):
    indent = "  " * depth
    folder_label = "/".join(path_parts) if path_parts else "(root)"
    if depth > MAX_DEPTH:
        print(f"{indent}[max-depth] {folder_label}", flush=True)
        return
    if depth <= 3:
        print(f"{indent}> {folder_label}", flush=True)

    try:
        entries = list(os.scandir(root))
    except (PermissionError, OSError) as e:
        print(f"{indent}[skip] {folder_label}: {e}", flush=True)
        return

    for entry in entries:
        name = entry.name
        # 雙 volume 期間 v1 案件資料夾可能是 symlink 或 bind mount 指向 v2（透明 union）。
        # 跳過避免 v2 內容被 v1 walk 又掃一次造成 duplicate（v2 root 自己會掃）。
        try:
            if entry.is_symlink():
                continue
            is_dir = entry.is_dir()
            # 偵測 bind mount：v1 walk 不進去 mount point，由 v2 walk 直接掃
            if is_dir and os.path.ismount(entry.path):
                continue
        except OSError:
            continue
        if is_dir:
            if name in SKIP_FOLDERS or any(name.startswith(p) for p in SKIP_PREFIXES):
                continue
            walk(Path(entry.path), path_parts + [name], rows, volume, depth + 1)
        else:
            # 濾掉 macOS metadata 小檔 ._foo.mp4
            if name.startswith("._"):
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in VIDEO_EXT:
                continue
            # 剪輯效果、素材/音效/ 內的 .mp4 是音效檔（無視訊流），不歸入片庫
            if (len(path_parts) >= 2
                and "剪輯效果" in path_parts[0]
                and path_parts[1] == "音效"):
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            rel_path = "/".join(path_parts + [name])
            channel_folder = path_parts[0] if len(path_parts) >= 1 else ""
            case_folder = path_parts[1] if len(path_parts) >= 2 else ""
            sub_parts = path_parts[2:]
            row = {
                "drive_file_id": synthetic_id(rel_path, volume),
                "rel_path": rel_path,
                "volume": volume,
                "filename": name,
                "ext": ext,
                "size_bytes": int(st.st_size),
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                "drive_web_link": None,
                "channel_folder": channel_folder,
                "case_folder": case_folder,
                "subpath": "/".join(sub_parts),
                "category": classify_subpath(sub_parts),
                "source": "nas",
                "nas_path": str(Path(entry.path)).replace("\\", "/"),
                **parse_channel(channel_folder),
                **parse_case(case_folder),
            }
            rows.append(row)
            if len(rows) % 200 == 0:
                print(f"  ...{len(rows)} videos so far", flush=True)


def main():
    if not NAS_ROOTS:
        print("No NAS root mounted on this host", file=sys.stderr)
        sys.exit(1)
    rows = []
    scanned_roots = []
    for root in NAS_ROOTS:
        prefix = get_prefix(root)
        print(f"掃描 [{root.volume}] {prefix} ...", flush=True)
        walk(Path(prefix), [], rows, root.volume)
        scanned_roots.append({"volume": root.volume, "path": prefix})

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_JSON.with_suffix(OUT_JSON.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "roots": scanned_roots,
            "count": len(rows),
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_JSON)

    by_ch = {}
    by_vol = {}
    for r in rows:
        by_ch[r["channel_name"]] = by_ch.get(r["channel_name"], 0) + 1
        by_vol[r["volume"]] = by_vol.get(r["volume"], 0) + 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"\ntotal: {len(rows)} NAS video files -> {OUT_JSON}")
    print("\n== by volume ==")
    for k, v in sorted(by_vol.items()):
        print(f"  {k}: {v}")
    print("\n== by channel ==")
    for k, v in sorted(by_ch.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
