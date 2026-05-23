"""平行版本：用 thread pool 並發 update search_text。"""
from __future__ import annotations
import os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client
from postgrest.exceptions import APIError

URL = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_SERVICE_ROLE_KEY']
WORKERS = 8

# 每個 thread 自己 client，避免共用 session 衝突
def make_client():
    return create_client(URL, KEY)


def with_retry(fn, *, name=''):
    for attempt in range(8):
        try:
            return fn()
        except Exception:
            wait = min(2 ** attempt, 30)
            time.sleep(wait)
    return None


def fetch_all():
    client = make_client()
    PAGE = 1000
    out = []
    offs = 0
    while True:
        r = with_retry(lambda: client.table('videos').select(
            'drive_file_id,search_text,city,district'
        ).not_.is_('city', 'null').range(offs, offs+PAGE-1).execute())
        rows = r.data if r else []
        if not rows: break
        for row in rows:
            st = row.get('search_text') or ''
            city = row.get('city') or ''
            dist = row.get('district') or ''
            need = []
            if city and city not in st: need.append(city)
            if dist and dist not in st: need.append(dist)
            if not need: continue
            new_st = (st + ' ' + ' '.join(need)).strip()
            out.append((row['drive_file_id'], new_st))
        if len(rows) < PAGE: break
        offs += PAGE
        if offs % 5000 == 0:
            print(f'  fetched {offs}, queued {len(out)}', flush=True)
    return out


# Per-thread client
import threading
_local = threading.local()

def get_client():
    if not hasattr(_local, 'client'):
        _local.client = make_client()
    return _local.client


def do_update(item):
    fid, new_st = item
    client = get_client()
    return with_retry(lambda: client.table('videos').update(
        {'search_text': new_st}
    ).eq('drive_file_id', fid).execute())


def main():
    print('fetching all rows with city...', flush=True)
    todo = fetch_all()
    print(f'total to update: {len(todo)}\n', flush=True)

    done = 0
    failed = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for r in ex.map(do_update, todo):
            done += 1
            if r is None:
                failed += 1
            if done % 200 == 0:
                rate = done / max(time.time() - t0, 1)
                eta = (len(todo) - done) / max(rate, 0.1) / 60
                print(f'  {done}/{len(todo)}  rate={rate:.1f}/s  failed={failed}  eta={eta:.1f}min', flush=True)
    print(f'\ndone: {done}, failed: {failed}, took {(time.time()-t0)/60:.1f}min')


if __name__ == '__main__':
    main()
