from __future__ import annotations
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

# 找鳴森大苑相關
r = sb.table('videos').select('drive_file_id,case_name,case_folder,city,district,channel_name,category,filename,nas_share_url') \
    .ilike('rel_path', '%鳴森大苑%').limit(5).execute()
print(f'rows: {len(r.data)}')
for row in r.data:
    print(f"  case_name={row['case_name']}")
    print(f"    city={row['city']}  district={row['district']}")
    print(f"    channel={row['channel_name']}  cat={row['category']}")
    print(f"    rel={row['nas_share_url']}")

# 統計同一 case 全部
if r.data:
    cn = r.data[0]['case_name']
    cnt = sb.table('videos').select('drive_file_id', count='exact', head=True).eq('case_name', cn).execute()
    print(f'\n總筆數 case_name={cn}: {cnt.count}')

# 找信義區空拍
print('\n== 目前 city=台北 district=信義 的空拍 ==')
r2 = sb.table('videos').select('drive_file_id,case_name,filename') \
    .eq('city','台北').eq('district','信義').eq('category','空拍').limit(5).execute()
print(f'信義空拍: {len(r2.data)} 筆')
for row in r2.data[:3]:
    print(f"  {row['case_name']} :: {row['filename']}")
