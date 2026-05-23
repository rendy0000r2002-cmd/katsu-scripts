from __future__ import annotations
import sys, os, urllib.parse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

# 模擬 search route：probe 不帶任何 filter
q = '吉祥如藝第二篇'
r1 = sb.table('videos').select('drive_file_id', count='exact', head=True) \
    .ilike('search_text', f'%{q}%').execute()
print(f'probe ilike search_text 含「{q}」: count={r1.count}')

# 模擬 relax 後
q2 = '吉祥如藝'
r2 = sb.table('videos').select('drive_file_id', count='exact', head=True) \
    .ilike('search_text', f'%{q2}%').execute()
print(f'relaxed q={q2}: count={r2.count}')

# 假設 user 勾「輸出」=不再排除「輸出」=不發 excludeCategory，那預設搜尋仍會排除「拍帶」嗎？
# 看 SearchApp 預設 excluded = {"輸出"}，user 勾掉 = excluded = {}（沒排除任何分類）
# 帶 category 過濾呢？如果 category 是空，excludeCategory 也是空，就純 search_text
print('\n-- 帶 q 與 不排除任何分類 --')
r3 = sb.table('videos').select('drive_file_id,filename,case_name,category', count='exact') \
    .ilike('search_text', f'%{q2}%').limit(5).execute()
print(f'count={r3.count}')
for row in r3.data: print(f"  {row['filename'][:50]} cat={row['category']}")

# 再加 is_old=False (預設不含舊)
print('\n-- 加 is_old=false --')
r4 = sb.table('videos').select('drive_file_id', count='exact', head=True) \
    .ilike('search_text', f'%{q2}%').eq('is_old', False).execute()
print(f'count={r4.count}')

# is_old=False AND category=輸出
print('\n-- is_old=false AND category=輸出 --')
r5 = sb.table('videos').select('drive_file_id,filename,case_name', count='exact') \
    .ilike('search_text', f'%{q2}%').eq('is_old', False).eq('category', '輸出').execute()
print(f'count={r5.count}')
for row in r5.data: print(f"  {row['filename'][:50]} case={row['case_name']}")

# 直接打網站 API 看
import urllib.request, json
url = 'https://randynas.tailb1ff82.ts.net/api/search?q=' + urllib.parse.quote(q) + '&limit=5'
print(f'\n-- API call (no auth, expect 401) --\n{url}')
try:
    with urllib.request.urlopen(url, timeout=10) as resp:
        print(resp.status, resp.read()[:200])
except Exception as e:
    print(f'  {e}')
