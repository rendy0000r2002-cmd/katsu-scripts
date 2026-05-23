from __future__ import annotations
import sys, os, json, time, re
import requests

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))
UNKNOWN = os.path.join(BASE, 'locations_unknown.json')
MANUAL = os.path.join(BASE, 'manual_locations.json')
LOG = os.path.join(BASE, 'logs', f'enrich_591_{time.strftime("%Y%m%d_%H%M")}.log')

os.makedirs(os.path.dirname(LOG), exist_ok=True)
log_file = open(LOG, 'w', encoding='utf-8')

def log(msg):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line)
    log_file.write(line + '\n')
    log_file.flush()

# 非建案 regex（跳過）
SKIP_PATS = [
    r'vlog', r'^99_', r'音效', r'^315', r'^0\d{3}', r'Dyson', r'防蚊',
    r'iPhone', r'MacBook', r'^三星', r'S25', r'東京', r'杜拜', r'日本$',
    r'韓國', r'短影\d+', r'^短影', r'業配', r'Sheba', r'參考模板', r'產品照',
    r'客運段', r'綠藤', r'中華電信', r'開箱我的', r'看屋筆記', r'房感',
    r'A\d+(看屋|下|上)', r'飛時代', r'豐存股', r'耳機', r'追覓', r'李亭香',
    r'^\d+_', r'Dcard', r'線上課程', r'Podcast', r'安爸', r'安娜',
    r'平安夜', r'詐騙', r'商周_', r'拍帶$', r'combo$', r'^\d+月$',
    r'車拍', r'已購客', r'^Emma$', r'^2$', r'^4月',
]
SKIP_RE = re.compile('|'.join(SKIP_PATS), re.IGNORECASE)

def is_non_case(name):
    if not name or len(name) < 2:
        return True
    if re.match(r'^\d{4,}', name) and not any(c in name for c in '路街道區縣市'):
        return True
    return bool(SKIP_RE.search(name))

# 591 搜尋
URL = 'https://newhouse.591.com.tw/home/housing/search'
HEADERS = {'User-Agent': 'Mozilla/5.0'}

def normalize(s):
    return re.sub(r'[\s\-_（）()【】［］\[\]]', '', s).upper()

def search_591(name):
    try:
        r = requests.get(URL, params={'keyword': name}, headers=HEADERS, timeout=15)
        items = r.json().get('data', {}).get('items', [])
        if not items:
            return None
        # 取首筆，但驗證名稱有交集
        target = normalize(name)
        for it in items[:3]:
            bn = normalize(it.get('build_name', ''))
            if target in bn or bn in target or len(set(target) & set(bn)) >= min(len(target), len(bn)) * 0.6:
                region = it.get('region', '').replace('臺', '台')
                section = it.get('section', '')
                return {'city': region, 'district': section, 'matched_name': it.get('build_name')}
        return None
    except Exception as e:
        return {'error': str(e)}

# 主流程
with open(UNKNOWN, encoding='utf-8') as f:
    unknown = json.load(f)
with open(MANUAL, encoding='utf-8') as f:
    manual = json.load(f)

names = list(unknown.keys()) if isinstance(unknown, dict) else [x.get('case_name') for x in unknown]
todo = [n for n in names if n and n not in manual and not is_non_case(n)]

log(f'=== enrich_591 開始 ===')
log(f'未知 {len(names)} 筆，去掉非建案後待查 {len(todo)} 筆')

hits = 0
misses = []
errors = 0

for i, name in enumerate(todo, 1):
    res = search_591(name)
    if res and 'error' not in res:
        manual[name] = {
            'city': res['city'],
            'district': res['district'],
            'manual': True,
            'auto': '591-batch-' + time.strftime('%Y-%m-%d'),
            'matched_591': res['matched_name'],
        }
        hits += 1
        log(f'[{i}/{len(todo)}] ✅ {name} → {res["city"]}/{res["district"]} ({res["matched_name"]})')
    elif res and 'error' in res:
        errors += 1
        log(f'[{i}/{len(todo)}] ⚠️ {name} ERR: {res["error"]}')
    else:
        misses.append(name)
        log(f'[{i}/{len(todo)}] ❌ {name}')
    if i % 20 == 0:
        with open(MANUAL, 'w', encoding='utf-8') as f:
            json.dump(manual, f, ensure_ascii=False, indent=2)
    time.sleep(0.3)

with open(MANUAL, 'w', encoding='utf-8') as f:
    json.dump(manual, f, ensure_ascii=False, indent=2)

# 留下未命中清單給後續 Google Maps 用
miss_path = os.path.join(BASE, '591_misses.json')
with open(miss_path, 'w', encoding='utf-8') as f:
    json.dump(misses, f, ensure_ascii=False, indent=2)

log(f'=== 結束 ===')
log(f'命中 {hits} / 未命中 {len(misses)} / 錯誤 {errors} / 跳過非建案 {len(names)-len(todo)}')
log(f'未命中清單已存: {miss_path}')

# 跑 apply 寫回 Supabase
log('跑 extract_locations.py phase1 + apply')
import subprocess
subprocess.run([sys.executable, os.path.join(BASE, 'extract_locations.py'), 'phase1'])
subprocess.run([sys.executable, os.path.join(BASE, 'extract_locations.py'), 'apply'])

log_file.close()
