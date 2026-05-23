"""
為 ProRes（與其他瀏覽器無法播的 codec）產 H.264 mp4 proxy
proxy 存在 NAS 上 _proxies/{sha1(drive_file_id)}.mp4
"""
from __future__ import annotations
import os, subprocess, hashlib, sys
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from supabase import create_client

sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])

# 瀏覽器無法播的 codec（在 .mov 容器內）
NEEDS_PROXY = {'prores', 'prores_ks', 'dnxhd', 'dnxhr', 'cineform', 'mjpeg', 'rawvideo', 'v210', 'v410'}

PROXY_ROOT = r'Y:\homes\ETtomorrow\_proxies'
os.makedirs(PROXY_ROOT, exist_ok=True)

def resolve_path(raw: str) -> str | None:
    if not raw:
        return None
    cands = [
        raw,
        raw.replace('U:/', 'Y:/'),
        raw.replace('Y:/', 'U:/'),
        raw.replace('U:/home/', 'Y:/homes/ETtomorrow/'),
        raw.replace('/volume2/homes/ETtomorrow/', 'Y:/homes/ETtomorrow/'),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None

def proxy_path_for(drive_file_id: str) -> str:
    h = hashlib.sha1(drive_file_id.encode()).hexdigest()
    return os.path.join(PROXY_ROOT, f'{h}.mp4')

def transcode(src: str, dst: str) -> bool:
    tmp = dst + '.tmp.mp4'
    cmd = [
        'ffmpeg', '-y', '-i', src,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-vf', 'scale=if(gt(iw\\,ih)\\,-2\\,720):if(gt(iw\\,ih)\\,720\\,-2)',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        '-loglevel', 'error',
        tmp,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, dst)
            return True
        else:
            print(f'  ffmpeg failed: {r.stderr[-300:]}', flush=True)
            if os.path.exists(tmp): os.remove(tmp)
            return False
    except subprocess.TimeoutExpired:
        print('  TIMEOUT', flush=True)
        if os.path.exists(tmp): os.remove(tmp)
        return False
    except Exception as e:
        print(f'  exception: {e}', flush=True)
        if os.path.exists(tmp): os.remove(tmp)
        return False

def main():
    # 抓所有需要 proxy 的檔
    rows = sb.table('videos').select('drive_file_id,nas_share_url,filename,codec') \
            .in_('codec', list(NEEDS_PROXY)) \
            .not_.is_('nas_share_url', 'null') \
            .execute().data
    print(f'total candidates: {len(rows)}', flush=True)

    done, fail, skip = 0, 0, 0
    for i, row in enumerate(rows, 1):
        drive_id = row['drive_file_id']
        dst = proxy_path_for(drive_id)
        if os.path.exists(dst):
            skip += 1
            continue
        src = resolve_path(row['nas_share_url'])
        if not src:
            print(f'[{i}/{len(rows)}] NOTFOUND: {row["filename"]}', flush=True)
            fail += 1
            continue
        print(f'[{i}/{len(rows)}] {row["filename"]} ({row["codec"]})', flush=True)
        if transcode(src, dst):
            done += 1
            sz = os.path.getsize(dst) // 1024 // 1024
            print(f'  -> {sz} MB', flush=True)
        else:
            fail += 1
    print(f'\nDONE. transcoded:{done} failed:{fail} skipped:{skip}', flush=True)

if __name__ == '__main__':
    main()
