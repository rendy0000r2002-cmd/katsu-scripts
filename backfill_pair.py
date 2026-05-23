"""補強：保證 search_text 一定有「{city} {district}」這個連字串。"""
from __future__ import annotations
import os, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

URL = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_SERVICE_ROLE_KEY']
WORKERS = 8

_local = threading.local()
def get_client():
    if not hasattr(_local, 'c'):
        _local.c = create_client(URL, KEY)
    return _local.c


def with_retry(fn):
    for attempt in range(8):
        try:
            return fn()
        except Exception:
            time.sleep(min(2 ** attempt, 30))
    return None


print('fetch...', flush=True)
client = create_client(URL, KEY)
PAGE = 1000
todo = []
offs = 0
while True:
    r = with_retry(lambda: client.table('videos').select(
        'drive_file_id,search_text,city,district'
    ).not_.is_('city','null').not_.is_('district','null').range(offs, offs+PAGE-1).execute())
    rows = r.data if r else []
    if not rows: break
    for row in rows:
        st = row.get('search_text') or ''
        c = row.get('city') or ''
        d = row.get('district') or ''
        if not c or not d: continue
        pair = f'{c} {d}'
        if pair in st: continue
        new_st = (st + ' ' + pair).strip()
        todo.append((row['drive_file_id'], new_st))
    if len(rows) < PAGE: break
    offs += PAGE
    if offs % 5000 == 0:
        print(f'  fetched {offs}, queued {len(todo)}', flush=True)

print(f'\ntotal pair-fix: {len(todo)}', flush=True)

def upd(item):
    fid, new_st = item
    return with_retry(lambda: get_client().table('videos').update({'search_text': new_st}).eq('drive_file_id', fid).execute())

t0 = time.time()
done = 0
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for _ in ex.map(upd, todo):
        done += 1
        if done % 500 == 0:
            rate = done / max(time.time()-t0, 1)
            print(f'  {done}/{len(todo)} rate={rate:.1f}/s', flush=True)
print(f'done in {(time.time()-t0)/60:.1f}min')
