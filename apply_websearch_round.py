"""WebSearch 結果寫入 manual_locations.json，最後 9 筆。"""
from __future__ import annotations
import sys, os, json, time, subprocess

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))
MANUAL = os.path.join(BASE, 'manual_locations.json')

with open(MANUAL, encoding='utf-8') as f:
    manual = json.load(f)

today = time.strftime('%Y-%m-%d')

WEBSEARCH_RESULTS = {
    '遠雄信義CENTER': ('新北', '板橋', '遠雄房地產官網確認新板特區廠辦'),
    '清景麟 研森': ('台南', '東', '南台南站重劃區崇賢一路'),
    '德浮寧聚南京': ('台北', '中山', '依案名「南京」推測南京東/西路 (德孚建設北部建商)'),
    '首岳短版': ('台北', '大同', '聖得福建設首岳，寧夏路88號'),
    '和典永峰': ('新北', '中和', '立人街14巷77號'),
    '月光之水': ('台北', '北投', '樂揚建設，明德路321號旁'),
    '頤昌松琚 公園': ('新北', '林口', '頤昌建設，中山路民生路口'),
    '微笑高鐵+微笑首馥': ('高雄', '苓雅', '松益發建設，微笑首馥(苓雅O9) + 微笑高鐵(左營) 合併'),
    '信義嘉學': ('新北', '新莊', '信義開發，副都心富貴路昌學街口'),
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

print('\n--- 跑 extract_locations.py phase1 ---')
subprocess.run([sys.executable, os.path.join(BASE, 'extract_locations.py'), 'phase1'])

print('\n--- 跑 extract_locations.py apply ---')
subprocess.run([sys.executable, os.path.join(BASE, 'extract_locations.py'), 'apply'])
