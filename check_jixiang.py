from __future__ import annotations
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

# 1. rel_path 含「吉祥如藝」+「輸出」
r = sb.table('videos').select('drive_file_id,filename,case_name,category,channel_name,city,district,is_old,rel_path,search_text') \
    .ilike('rel_path', '%吉祥如藝%').ilike('rel_path', '%輸出%').limit(20).execute()
print(f'== rel_path 含「吉祥如藝」且含「輸出」: {len(r.data)} 筆 ==')
for row in r.data[:5]:
    print(f"  {row['filename'][:60]}")
    print(f"    case={row['case_name']}  cat={row['category']}  ch={row['channel_name']}")
    print(f"    city={row['city']}/{row['district']}  is_old={row['is_old']}")
    print(f"    rel={row['rel_path'][:100]}")
    print(f"    search_text={(row.get('search_text') or '')[:120]}")

# 2. 找所有 "吉祥如藝" 案件
print('\n== 案件名稱含「吉祥如藝」 ==')
r2 = sb.table('videos').select('case_name,category,is_old', count='exact', head=True) \
    .ilike('case_name', '%吉祥如藝%').execute()
print(f'總筆數: {r2.count}')
r3 = sb.table('videos').select('case_name,category,channel_name,is_old') \
    .ilike('case_name', '%吉祥如藝%').limit(30).execute()
seen = set()
for row in r3.data:
    key = (row['case_name'], row['category'], row['channel_name'], row['is_old'])
    if key in seen: continue
    seen.add(key)
    print(f"  case={row['case_name']}  cat={row['category']}  ch={row['channel_name']}  is_old={row['is_old']}")

# 3. search_text 含 "第二篇"
print('\n== search_text 含「第二篇」且 case 為吉祥如藝 ==')
r4 = sb.table('videos').select('drive_file_id,filename,case_name,search_text', count='exact') \
    .ilike('search_text', '%吉祥如藝%第二篇%').limit(5).execute()
print(f'吉祥如藝...第二篇: {r4.count} 筆')
for row in r4.data[:3]:
    print(f"  {row['filename'][:60]}  search_text={row.get('search_text','')[:80]}")

# 4. 「輸出」分類含吉祥如藝
print('\n== category=輸出 且 case 含吉祥如藝 ==')
r5 = sb.table('videos').select('drive_file_id,filename,case_name,is_old') \
    .eq('category', '輸出').ilike('case_name', '%吉祥如藝%').limit(20).execute()
print(f'  共 {len(r5.data)} 筆')
for row in r5.data[:10]:
    print(f"    {row['filename'][:60]}  case={row['case_name']}  is_old={row['is_old']}")
