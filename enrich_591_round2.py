"""591 第二輪：清理名稱再搜，加上明確可判定的人工補建案。"""
from __future__ import annotations
import sys, os, json, re, time, subprocess
import requests

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))
MISSES = os.path.join(BASE, '591_misses.json')
MANUAL = os.path.join(BASE, 'manual_locations.json')

with open(MISSES, encoding='utf-8') as f:
    misses = json.load(f)
with open(MANUAL, encoding='utf-8') as f:
    manual = json.load(f)

# 強化「非建案」filter
NON_BLDG = re.compile(
    r'EP\d+|\d{1,2}/\d{1,2}|短影音?|腳本|麥當勞|Honeywell|寰宇|永豐銀行|信義房屋|'
    r'統一水果|HOROYOI|畢卡索|古文明|Ruby|張琳|心理醫生|琦郁海苔|琦郁日本|JoyLife|'
    r'侏羅紀|車位影片|全聯\+|詹惟中|公設|鋁模板|芝司樂|十得私廚|心城市-|星城Online|'
    r'颱風天|護理師|捷運環狀|港阜|萊爾富|酷塑|波碧特調|中台灣燈會|日暮拉麵|'
    r'街訪|車拍|中山商圈|忠孝復興|毛玻璃|泰國市集|Lavina|Tpark$',
    re.IGNORECASE
)


def clean(name):
    name = re.sub(r'^\d+%3A\d+\s*', '', name)
    name = re.sub(r'^\d{4}\s*', '', name)
    name = re.sub(r'[（(].*?[)）]', '', name)
    name = re.sub(r'\s*(短影音?|腳本|拍帶|直式|重拍|精華|x?\d秒|\*\d|x\d|P\d|II|III)+\s*', '', name)
    name = re.sub(r'\s*\d+$', '', name)
    return name.strip()


URL = 'https://newhouse.591.com.tw/home/housing/search'
HDR = {'User-Agent': 'Mozilla/5.0'}

def to_short(city, district):
    return city.rstrip('市縣').replace('臺', '台'), district.rstrip('區鎮市鄉')


def search_591(name):
    try:
        r = requests.get(URL, params={'keyword': name}, headers=HDR, timeout=15)
        items = r.json().get('data', {}).get('items', [])
        if not items:
            return None
        bn = re.sub(r'\s', '', items[0].get('build_name', '')).upper()
        n = re.sub(r'\s', '', name).upper()
        if n[:3] in bn or bn[:3] in n:
            city, dist = to_short(items[0]['region'], items[0]['section'])
            return city, dist, items[0]['build_name']
    except:
        pass
    return None


# 1) 591 清理重搜
hits_591 = 0
google_pending = []
for n in misses:
    if NON_BLDG.search(n) or n in manual:
        continue
    cleaned = clean(n)
    if not cleaned or len(cleaned) < 2:
        continue
    res = search_591(cleaned)
    if res:
        city, dist, bn = res
        manual[n] = {
            'city': city, 'district': dist,
            'manual': True,
            'auto': '591-round2-' + time.strftime('%Y-%m-%d'),
            'matched_591': bn,
        }
        hits_591 += 1
        print(f'  ✅[591] {n} -> {city}/{dist} ({bn})')
    else:
        google_pending.append((n, cleaned))
    time.sleep(0.3)

# 2) 我直接判定（地名嵌在案名裡）
MANUAL_JUDGE = {
    '中正區 正隆官邸空拍': ('台北', '中正'),    # 「中正區」明示
    '關渡左岸': ('台北', '北投'),                 # 關渡屬北投區
    '新庄綻': ('新北', '新莊'),                   # 新庄=新莊
    '草漯和境寓見': ('桃園', '觀音'),             # 草漯重劃區屬觀音區
    '南科悅楊': ('台南', '善化'),                 # 南科主要在善化
    '勤樸大道 Alfa Safe': ('桃園', '中壢'),       # 勤樸建設大道系列在桃園
    '勤樸富邑': ('桃園', '中壢'),                 # 勤樸建設
}
hits_judge = 0
for n, (city, dist) in MANUAL_JUDGE.items():
    if n in manual:
        continue
    manual[n] = {
        'city': city, 'district': dist,
        'manual': True,
        'auto': 'claude-judge-' + time.strftime('%Y-%m-%d'),
        'note': '依案名地名線索判斷',
    }
    hits_judge += 1
    print(f'  ✅[judge] {n} -> {city}/{dist}')

# 3) 寫回
with open(MANUAL, 'w', encoding='utf-8') as f:
    json.dump(manual, f, ensure_ascii=False, indent=2)

# 4) 剩餘清單給 Google Maps 用
remaining = [n for n, _ in google_pending if n not in manual]
out = os.path.join(BASE, 'google_maps_pending.json')
with open(out, 'w', encoding='utf-8') as f:
    json.dump(remaining, f, ensure_ascii=False, indent=2)

print()
print(f'591 round2 命中: {hits_591}')
print(f'我判定: {hits_judge}')
print(f'剩 {len(remaining)} 筆待 Google Maps: {out}')

# 5) apply
print('\n--- 跑 extract_locations.py apply ---')
subprocess.run([sys.executable, os.path.join(BASE, 'extract_locations.py'), 'apply'])
