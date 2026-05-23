from __future__ import annotations
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

# 看本周排程
r = sb.table('weekly_schedule').select('*').gte('date', '2026-04-23').order('date').execute()
print(f'本周排程 ({len(r.data)} 筆):')
for row in r.data:
    print(f"  {row['date']}  case={row['case_name']}  city={row.get('city')}  district={row.get('district')}")
