"""從 NAS 容器內 localhost:3000 打 API + 看 logs"""
from __future__ import annotations
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('192.168.18.6', username='ETtomorrow', password='***REDACTED-NAS-PASS***', timeout=15)

# 檢查 logEvent table 裡 user 最近的 search log
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

# event log table 名稱
r = sb.table('login_logs').select('*').order('created_at', desc=True).limit(15).execute()
print(f'== 最近 search 相關 log ==')
for row in r.data:
    if row.get('event') == 'search' or 'search' in str(row.get('event', '')):
        print(row)
print('\n-- 全部 row keys --')
print(list(r.data[0].keys()) if r.data else 'empty')
for row in r.data[:5]:
    print(row)
client.close()
