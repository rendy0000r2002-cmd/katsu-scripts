"""補丁：把 city/district 寫進 search_text，讓搜尋「新北 板橋」可命中。"""
from __future__ import annotations
import os, sys, time
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client
from postgrest.exceptions import APIError

client = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

def with_retry(fn, *, name=''):
    for attempt in range(6):
        try:
            return fn()
        except APIError as e:
            wait = 2 ** attempt
            print(f'  [retry {attempt+1}/6 in {wait}s] {name} APIError code={getattr(e, "code", "?")}', flush=True)
            time.sleep(wait)
        except Exception as e:
            wait = 2 ** attempt
            print(f'  [retry {attempt+1}/6 in {wait}s] {name} {type(e).__name__}', flush=True)
            time.sleep(wait)
    raise RuntimeError(f'{name} 連續 6 次失敗')


print('fetch rows with city...')
PAGE = 1000
all_to_update = []
offs = 0
while True:
    def _fetch():
        return client.table('videos').select('drive_file_id,search_text,city,district') \
            .not_.is_('city','null').range(offs, offs+PAGE-1).execute()
    r = with_retry(_fetch, name=f'fetch offs={offs}')
    rows = r.data or []
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
        all_to_update.append((row['drive_file_id'], new_st))
    if len(rows) < PAGE: break
    offs += PAGE

print(f'\ntotal to update: {len(all_to_update)}')

for i, (fid, new_st) in enumerate(all_to_update, 1):
    def _upd():
        return client.table('videos').update({'search_text': new_st}).eq('drive_file_id', fid).execute()
    with_retry(_upd, name=f'update {fid}')
    if i % 200 == 0:
        print(f'  {i}/{len(all_to_update)}', flush=True)
        time.sleep(0.2)  # 稍微 throttle

print('done')
