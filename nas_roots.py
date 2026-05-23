"""
NAS 多 volume 路徑集中管理（v1 原 /volume1，2026-05-19 搬到 /volume2/homes；v2=/volume2/homes2）。

每個 root 同時記錄 3 種路徑：
  - linux: NAS host 上的路徑（/volume2/homes/ETtomorrow）
  - win:   PC 上的 SMB mount（Y:/home 或 Y:/homes2/ETtomorrow）
  - docker: 容器內 bind mount（/data/homes/ETtomorrow）

新增 volume 在這裡加一筆，所有腳本自動跟著生效。

注意 synthetic_id：v1 保留原算法（hash(rel_path)）；v2+ 加 volume 前綴
避免相同 rel_path 撞 ID。
"""
from __future__ import annotations
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NasRoot:
    volume: str         # "v1", "v2", ...
    linux: str          # "/volume2/homes/ETtomorrow"
    win: str            # "Y:/home" or "Y:/homes2/ETtomorrow"
    docker: str         # "/data/homes/ETtomorrow"
    label: str = ""     # 顯示用名稱


# 主清單：新增 volume 改這裡
ALL_ROOTS: list[NasRoot] = [
    NasRoot(
        volume="v1",
        linux="/volume2/homes/ETtomorrow",
        win="Y:/home",
        docker="/data/homes/ETtomorrow",
        label="原 v1（2026-05-19 搬 V2）",
    ),
    NasRoot(
        volume="v2",
        linux="/volume2/homes2/ETtomorrow",
        win="Y:/homes2/ETtomorrow",
        docker="/data/homes2/ETtomorrow",
        label="volume2（DX517 新集區）",
    ),
]


def _norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/")


def detect_platform() -> str:
    """回傳 'linux' / 'win' / 'docker'，給 get_prefix() 用。
    docker 偵測：跑在 Linux 且 /data 存在但 /volume2 不存在（容器內）。
    """
    if sys.platform.startswith("linux"):
        if Path("/data/homes/ETtomorrow").exists() and not Path("/volume2/homes/ETtomorrow").exists():
            return "docker"
        return "linux"
    return "win"


def get_prefix(root: NasRoot, platform: str | None = None) -> str:
    """根據 platform 回傳對應的路徑前綴（無尾斜線）"""
    platform = platform or detect_platform()
    return {"linux": root.linux, "win": root.win, "docker": root.docker}[platform]


def existing_roots(platform: str | None = None) -> list[NasRoot]:
    """回傳「在當前 host 實際存在」的 NasRoot 列表。
    用於 scan 類腳本：只掃實際 mount 的 volume。
    """
    platform = platform or detect_platform()
    return [r for r in ALL_ROOTS if Path(get_prefix(r, platform)).exists()]


def find_root_for(abs_path: str, platform: str | None = None) -> NasRoot | None:
    """給定一個絕對路徑，判斷它屬於哪個 root。"""
    platform = platform or detect_platform()
    s = _norm(abs_path)
    # 同時比對所有 platform 的 prefix（容器內可能拿到 host 路徑）
    for r in ALL_ROOTS:
        for p in (r.linux, r.win, r.docker):
            if s.lower().startswith(_norm(p).lower() + "/") or s.lower() == _norm(p).lower():
                return r
    return None


def to_rel_path(abs_path: str) -> tuple[NasRoot, str] | None:
    """把絕對路徑切成 (root, rel_path)。找不到 → None。"""
    root = find_root_for(abs_path)
    if not root:
        return None
    s = _norm(abs_path)
    for p in (root.linux, root.win, root.docker):
        pn = _norm(p)
        if s.lower().startswith(pn.lower() + "/"):
            return root, s[len(pn) + 1:]
        if s.lower() == pn.lower():
            return root, ""
    return None


def from_rel_path(rel_path: str, root: NasRoot, platform: str | None = None) -> str:
    """rel_path + root → 絕對路徑（依當前 platform）"""
    prefix = get_prefix(root, platform)
    return _norm(prefix) + "/" + rel_path.lstrip("/")


def convert_path(abs_path: str, target_platform: str) -> str | None:
    """跨 platform 路徑轉換：例如 Y:/home/.../foo.mp4 → /volume2/homes/ETtomorrow/.../foo.mp4"""
    parsed = to_rel_path(abs_path)
    if not parsed:
        return None
    root, rel = parsed
    return from_rel_path(rel, root, target_platform)


def synthetic_id(rel_path: str, volume: str = "v1") -> str:
    """產 nas:<sha1>[:16] primary key。
    v1 維持原算法（hash(rel_path)）以保留既有 DB 紀錄；
    v2+ 加 volume 前綴避免相同 rel_path 撞 ID。
    """
    if volume == "v1":
        key = rel_path
    else:
        key = f"{volume}/{rel_path}"
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"nas:{h}"


# 方便腳本一行 import
ROOTS = ALL_ROOTS  # alias
DEFAULT_ROOT = ALL_ROOTS[0]  # v1，向後相容


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"platform: {detect_platform()}")
    print(f"all roots: {len(ALL_ROOTS)}")
    for r in ALL_ROOTS:
        prefix = get_prefix(r)
        exists = Path(prefix).exists()
        mark = "OK" if exists else "MISSING"
        print(f"  {r.volume} ({r.label}): {prefix}  [{mark}]")
    print(f"existing roots on this host: {[r.volume for r in existing_roots()]}")
