from __future__ import annotations
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

CASE = '鳴森大苑(松山、信義、民生社區)'
CITY = '台北'
NEW_DISTRICT = '松山,信義'  # 民生社區屬松山，已含

# 1. 更新 city/district
r = sb.table('videos').update({'city': CITY, 'district': NEW_DISTRICT}) \
    .eq('case_name', CASE).execute()
print(f'updated city/district rows: {len(r.data or [])}')

# 2. 補進 search_text，讓「信義」「台北信義」可命中
rows = sb.table('videos').select('drive_file_id,search_text') \
    .eq('case_name', CASE).execute().data or []
print(f'patching search_text for {len(rows)} rows...')
patched = 0
for row in rows:
    st = row.get('search_text') or ''
    need = []
    for token in ['信義', '台北 信義', '台北信義']:
        if token not in st:
            need.append(token)
    if not need:
        continue
    new_st = (st + ' ' + ' '.join(need)).strip()
    sb.table('videos').update({'search_text': new_st}).eq('drive_file_id', row['drive_file_id']).execute()
    patched += 1
print(f'  patched {patched}')

# 驗證
r2 = sb.table('videos').select('drive_file_id', count='exact', head=True) \
    .eq('city', '台北').like('district', '%信義%').eq('category', '空拍').execute()
print(f'\n台北信義空拍 (district like %信義%): {r2.count} 筆')
