"""
掃描所有 .mov 檔，ffprobe 取得 video codec 寫回 DB
用法: python probe_codecs.py [--all]
  預設只跑 codec is null 的檔案
  --all 強制重新掃所有 .mov
"""
from __future__ import annotations
import os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client

sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

def resolve_path(raw: str) -> str | None:
    if not raw:
        return None
    cands = [
        raw,
        raw.replace('U:/', 'Y:/'),
        raw.replace('Y:/', 'U:/'),
        raw.replace('U:/home/', 'Y:/homes/ETtomorrow/'),
        raw.replace('/volume2/homes/ETtomorrow/', 'Y:/homes/ETtomorrow/'),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None

def probe(path: str) -> str | None:
    # 限制 probesize/analyzeduration 避免讀整個檔（ProRes moov 常在檔尾）
    # 失敗就標 UNKNOWN，後續用更貴的方式重掃
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error',
             '-probesize', '8M', '-analyzeduration', '8M',
             '-select_streams', 'v:0',
             '-show_entries', 'stream=codec_name',
             '-of', 'default=nw=1:nk=1', path],
            capture_output=True, text=True, timeout=15
        )
        out = r.stdout.strip()
        return out if out else None
    except subprocess.TimeoutExpired:
        return 'TIMEOUT'
    except Exception:
        return None

def fetch_batch(rescan: bool, limit: int = 1000):
    q = sb.table('videos').select('drive_file_id,nas_share_url') \
          .ilike('filename', '%.mov').not_.is_('nas_share_url', 'null').limit(limit)
    if not rescan:
        q = q.is_('codec', 'null')
    return q.execute().data

def process_one(row):
    raw = row.get('nas_share_url')
    p = resolve_path(raw)
    if not p:
        return (row['drive_file_id'], 'NOTFOUND')
    codec = probe(p)
    return (row['drive_file_id'], codec or 'UNKNOWN')

def main():
    rescan = '--all' in sys.argv
    total_processed = 0
    while True:
        rows = fetch_batch(rescan, 1000)
        if not rows:
            break
        print(f'batch: {len(rows)} files', flush=True)
        results = []
        with ThreadPoolExecutor(max_workers=24) as pool:
            futs = {pool.submit(process_one, r): r for r in rows}
            for i, fut in enumerate(as_completed(futs), 1):
                drive_id, codec = fut.result()
                results.append((drive_id, codec))
                if i % 50 == 0:
                    print(f'  progress: {i}/{len(rows)}', flush=True)
        # 統計
        stats = {}
        for _, c in results:
            stats[c] = stats.get(c, 0) + 1
        print('  codec stats:', stats, flush=True)
        # 寫回 DB
        for drive_id, codec in results:
            sb.table('videos').update({'codec': codec}).eq('drive_file_id', drive_id).execute()
        total_processed += len(rows)
        print(f'  total processed: {total_processed}', flush=True)
        if rescan:
            # rescan 模式只跑一輪不會結束，加保險
            break
    print(f'DONE. total: {total_processed}')

if __name__ == '__main__':
    main()
