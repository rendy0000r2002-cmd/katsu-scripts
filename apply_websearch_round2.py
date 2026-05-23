"""第二輪 WebSearch：補抓沒被 phase1 regex 接到的建案。"""
from __future__ import annotations
import sys, os, json, time, subprocess

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))
MANUAL = os.path.join(BASE, 'manual_locations.json')

with open(MANUAL, encoding='utf-8') as f:
    manual = json.load(f)

today = time.strftime('%Y-%m-%d')

WEBSEARCH_RESULTS = {
    '20260420_日安park_Zora': ('新北', '板橋', '日安PARK 江翠重劃區藝文街'),
    '0318達麗河蘊': ('台北', '北投', '達麗河蘊 北士科承美路'),
    '0331天水一墅 3AI': ('新北', '淡水', '天水一墅 麗寶建設 濱海路一段'),
    '0328嘉潤和御第二彈': ('新北', '中和', '嘉潤和御 連城路新生街口'),
    '0307新潤世界城 黃世聰': ('新北', '板橋', '新潤世界城 南雅東路'),
    '0421北市科空拍': ('台北', '北投', '北市科=北士科'),
    '僑聯大千 影音拍攝腳本': ('台北', '中山', '民權西路61號'),
    '春城家（腳本待處理）': ('新北', '五股', '廣春成建設/麗寶機構 新城六路'),
    '1010勤樸機構': ('桃園', '中壢', '勤樸建設主案場'),
    # 沒查到、跳過：鉑岳拍帶、0112 清景麟幸福城
}

added = 0
for n, (city, dist, note) in WEBSEARCH_RESULTS.items():
    if n in manual:
        print(f'  ⏭️ 已存在: {n}')
        continue
    manual[n] = {
        'city': city,
        'district': dist,
        'manual': True,
        'auto': 'websearch-' + today,
        'note': note,
    }
    added += 1
    print(f'  ✅ {n} -> {city}/{dist}')

with open(MANUAL, 'w', encoding='utf-8') as f:
    json.dump(manual, f, ensure_ascii=False, indent=2)

print(f'\n新增 {added} 筆，manual 總數 {len(manual)}')
print('\n--- 跑 extract_locations.py phase1 + apply ---')
subprocess.run([sys.executable, os.path.join(BASE, 'extract_locations.py'), 'phase1'])
subprocess.run([sys.executable, os.path.join(BASE, 'extract_locations.py'), 'apply'])
