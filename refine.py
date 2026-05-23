"""
讀 index.json，重新解析 case_name / category / tags，寫 index_v2.json。
不重新掃 Drive，只做 metadata 精煉。
"""
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

# NAS 完檔/輸出檔名帶 V2/V3/V4... 表版本；同 base 只保留最大 V 號
NAS_VERSION_RE = re.compile(r'\s+V(\d+)\b', re.IGNORECASE)

import sys as _sys
_HERE = Path(__file__).parent
_IN_ARG = _sys.argv[1] if len(_sys.argv) > 1 else str(_HERE / "index.json")
_OUT_ARG = _sys.argv[2] if len(_sys.argv) > 2 else str(_HERE / "index_v2.json")
IN = Path(_IN_ARG)
OUT = Path(_OUT_ARG)

# 第 2 層「分類資料夾」白名單 — 這些不是真正的案名，要往下一層找
CATEGORY_LAYER_NAMES = {
    "0_業配", "1_業配", "2_業配", "3_業配",
    "01_正片資料夾", "02_業配資料夾", "03_業配資料夾",
    "00_短影正片", "2_短影or精華",
    "04_安娜的個人素材",
    "結案", "業配",
    "0_結案", "01_結案", "02_結案",
    # 素材庫（空拍/空景）的 北/中/南 分區
    "0_北部", "1_中部", "2_南部",
    "北部", "中部", "南部",
}

# 兩大素材庫頻道 — category 硬綁、不跑自動判斷
STOCK_CHANNELS = {
    "空拍素材 (重要)": "空拍",
    "空景": "空景",
}

# 子路徑關鍵字 → 歸檔分類
CAT_MAP = [
    ("輸出", "輸出"),
    ("完檔", "輸出"),  # NAS 上「完檔」= 輸出成品
    ("修改", "拍帶"),  # 「修改」資料夾下的素材也視為拍帶
    ("拍帶", "拍帶"),
    ("raw", "拍帶"),
    ("cam", "拍帶"),
    ("素材", "拍帶"),
    ("空拍", "空拍"),
    ("劇照", "劇照"),
]

# 用來判斷檔名是不是相機原檔：沒有中文字 = 相機輸出 = 拍帶
HAS_CHINESE_RE = re.compile(r"[一-鿿]")

# 案名層常見要跳過（即使不符合 category layer regex 也不是案名）
NON_CASE_NAMES = {
    "輸出", "修改", "拍帶", "素材", "cam", "raw", "舊",
    "A", "B", "C", "D", "E", "產品照",
}


def is_category_layer(name: str) -> bool:
    return name in CATEGORY_LAYER_NAMES


def derive_case_name(channel: str, case_folder: str, subpath: str) -> str:
    """
    舊結構：scanner 把 case_folder=2nd layer（例 1_業配），subpath=3rd+。
    新解析：若 case_folder 是 category layer，把 subpath 第一段當 case_name。
    """
    if not is_category_layer(case_folder):
        return case_folder  # e.g. Stan/雲宇宙_直式短影 本身就是案名
    # 取 subpath 第一段
    parts = [p for p in subpath.split("/") if p]
    for p in parts:
        if p in NON_CASE_NAMES:
            continue
        if is_category_layer(p):
            continue
        return p
    return case_folder  # fallback


def derive_category(channel: str, full_path_lower: str, filename: str = "") -> str:
    # 素材庫頻道一律硬綁類別
    if channel in STOCK_CHANNELS:
        return STOCK_CHANNELS[channel]
    for kw, label in CAT_MAP:
        if kw.lower() in full_path_lower:
            return label
    # Fallback：路徑沒給線索時，看檔名
    # 沒中文字的檔名 = 相機原檔（如 20251107_S3_0081.MP4）→ 拍帶
    if filename:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        if not HAS_CHINESE_RE.search(stem):
            return "拍帶"
    # 沒落入任何規則的，視同拍帶（之前叫「成品」其實大多就是拍帶）
    return "拍帶"


def extract_version(filename: str):
    """從 NAS 檔名抽 V 版本。回傳 (去版本 base_stem, version, ext)。
    無 V 視為 V1。變體如 `_送稿頁` 會保留在 base，避免與主版本合併。"""
    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
    else:
        stem, ext = filename, ""
    matches = list(NAS_VERSION_RE.finditer(stem))
    if matches:
        m = matches[-1]
        version = int(m.group(1))
        base = (stem[:m.start()] + stem[m.end():]).strip()
        return base, version, ext.lower()
    return stem.strip(), 1, ext.lower()


def mark_nas_old_versions(rows):
    """對 source=nas 且 category='輸出' 的檔案，同 base 保留最大 V，其他 is_old=True。"""
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        if r.get("source") != "nas":
            continue
        if r.get("category") != "輸出":
            continue
        base, version, ext = extract_version(r["filename"])
        key = (
            r.get("channel_folder", ""),
            r.get("case_folder", ""),
            r.get("subpath", ""),
            base,
            ext,
        )
        groups[key].append((version, i))
    marked = 0
    for items in groups.values():
        if len(items) <= 1:
            continue
        items.sort(key=lambda x: x[0], reverse=True)
        for _, idx in items[1:]:
            if not rows[idx].get("is_old"):
                rows[idx]["is_old"] = True
                marked += 1
    return marked


def derive_tags(channel: str, case_name: str, subpath: str, filename: str) -> list:
    """產生可搜尋的 token 清單：案名、子資料夾名、頻道。"""
    tokens = set()
    for part in (subpath or "").split("/"):
        p = part.strip()
        if p and p not in NON_CASE_NAMES and not is_category_layer(p):
            tokens.add(p)
    if case_name:
        tokens.add(case_name)
    if channel:
        tokens.add(channel)
    # filename 去副檔名
    stem = filename.rsplit(".", 1)[0]
    if stem:
        tokens.add(stem)
    return sorted(tokens)


def main():
    with IN.open(encoding="utf-8") as f:
        data = json.load(f)

    new_rows = []
    skipped_dotunderscore = 0
    for r in data["rows"]:
        # 過濾 macOS metadata 小檔（._foo.mp4），這些不是真的影片
        if r["filename"].startswith("._"):
            skipped_dotunderscore += 1
            continue
        channel = r["channel_name"]
        old_case = r["case_folder"]
        sub = r.get("subpath", "")
        full = r["rel_path"].lower()

        case_name = derive_case_name(channel, old_case, sub)
        category = derive_category(channel, full, r["filename"])
        tags = derive_tags(channel, case_name, sub, r["filename"])
        is_old = any(p.strip() == "舊" for p in (sub or "").split("/"))

        new_rows.append({
            **r,
            "case_name": case_name,
            "category": category,
            "tags": tags,
            "category_folder": old_case if is_category_layer(old_case) else "",
            "is_old": is_old,
        })

    # NAS 完檔/輸出：依檔名 V 版本號留最新
    nas_marked = mark_nas_old_versions(new_rows)

    data["rows"] = new_rows
    data["refined_at"] = data["scanned_at"]
    tmp = OUT.with_suffix(OUT.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)

    # stats
    import io, sys
    sys.stdout.reconfigure(encoding="utf-8")
    by_ch = Counter(r["channel_name"] for r in new_rows)
    by_cat = Counter(r["category"] for r in new_rows)
    case_ct = Counter(r["case_name"] for r in new_rows if r["case_name"])
    unknown = sum(1 for r in new_rows if not r["case_name"] or r["case_name"] == r.get("category_folder"))

    print(f"refined: {len(new_rows)} rows -> {OUT} (skipped ._ metadata: {skipped_dotunderscore})")
    print(f"NAS 舊版本（非最新 V）: {nas_marked} 筆 → is_old=True")
    print("\n== by category (new) ==")
    for k, v in by_cat.most_common():
        print(f"  {v:>5} {k}")
    print(f"\n== 可識別案名數: {len(case_ct)} 個")
    print(f"== 無法解出案名或 fallback: {unknown}")
    print("\n== Top 30 案名 ==")
    for k, v in case_ct.most_common(30):
        print(f"  {v:>4} {k}")


if __name__ == "__main__":
    main()
