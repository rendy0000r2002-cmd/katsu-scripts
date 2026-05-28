"""完整模擬 search route 對 q='吉祥如藝第二篇'"""
from __future__ import annotations
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

q = '吉祥如藝第二篇'
print(f'== probe q={q} ==')
r = sb.table('videos').select('drive_file_id', count='exact', head=True).ilike('search_text', f'%{q}%').execute()
print(f'  count={r.count}')

print(f'== relax → 吉祥如藝 ==')
q = '吉祥如藝'
print(f'\n== findPrimaryLocation: case_name ilike 吉祥如藝 ==')
r1 = sb.table('videos').select('city,district') \
    .ilike('case_name', q).not_.is_('city', 'null').limit(100).execute()
print(f'  exact match rows: {len(r1.data)}')
if not r1.data:
    print('  fallback: case_name ilike %吉祥如藝%')
    r1 = sb.table('videos').select('city,district').ilike('case_name', f'%{q}%').not_.is_('city', 'null').limit(100).execute()
    print(f'  fuzzy rows: {len(r1.data)}')

# tally
from collections import Counter
counter = Counter()
for row in r1.data:
    key = f"{row['city']}|{row.get('district') or ''}"
    counter[key] += 1
print('  tally:', counter.most_common(5))

# best
if counter:
    best, n = counter.most_common(1)[0]
    city, district = best.split('|')
    districts = [d.strip() for d in district.split(',') if d.strip()]
    print(f'  primary: city={city}, districts={districts}')

    print(f'\n== prio query (category=輸出, q=吉祥如藝, city=台北, district like 松山) ==')
    qb = sb.table('videos').select('drive_file_id,filename,case_name,category', count='exact') \
        .order('mtime', desc=True) \
        .ilike('search_text', f'%{q}%') \
        .eq('category', '輸出') \
        .eq('is_old', False) \
        .eq('city', city)
    if districts:
        or_str = ','.join(f'district.ilike.*{d}*' for d in districts)
        qb = qb.or_(or_str)
    r2 = qb.range(0, 5).execute()
    print(f'  count={r2.count}')
    for row in r2.data:
        print(f'    {row["filename"]}  case={row["case_name"]}  cat={row["category"]}')

    print(f'\n== rest query (city != 台北 OR no district match) ==')
    qb2 = sb.table('videos').select('drive_file_id,filename,case_name,category', count='exact') \
        .order('mtime', desc=True) \
        .ilike('search_text', f'%{q}%') \
        .eq('category', '輸出') \
        .eq('is_old', False)
    if districts:
        inner_and = ','.join(f'district.not.ilike.*{d}*' for d in districts)
        qb2 = qb2.or_(f'city.neq.{city},and({inner_and}),city.is.null')
    else:
        qb2 = qb2.or_(f'city.neq.{city},city.is.null')
    r3 = qb2.range(0, 5).execute()
    print(f'  count={r3.count}')
    for row in r3.data:
        print(f'    {row["filename"]}  case={row["case_name"]}  cat={row["category"]}')

# 對照：純 q ilike + category=輸出，不含 primary 邏輯
print(f'\n== 對照：純 q + 輸出 (無 primary 切割) ==')
r4 = sb.table('videos').select('drive_file_id,filename,case_name', count='exact') \
    .ilike('search_text', f'%{q}%').eq('category', '輸出').eq('is_old', False).execute()
print(f'  count={r4.count}')
for row in r4.data: print(f'    {row["filename"]}  case={row["case_name"]}')
