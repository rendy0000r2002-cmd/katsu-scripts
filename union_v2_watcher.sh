#!/bin/bash
# Bind mount V2→V1 透明 union 維護器（cron 每分鐘跑）
#
# 目的：讓剪輯/團隊從舊 SMB 路徑 (Y:\home, /volume2/homes/ETtomorrow) 也能存取 v2 新案件。
#
# 為什麼用 bind mount 不用 symlink？
#   Samba 4.15 對「跨 share 的 symlink」會從 directory listing 過濾掉。
#   剪輯打開 Y:\home\12_女子開箱\ 看不到 symlink → 沒用。
#   bind mount 從 SMB 來看就是真實資料夾，可正常列出。
#
# 機制：
#   1. 掃 v2 每個 channel 底下的 case 資料夾
#   2. v1 對應位置若沒同名資料夾 → 建空 dir + bind mount v2→v1
#   3. v1 對應位置已有 bind mount 且指向正確 → skip
#   4. v1 對應位置有「真實資料夾」（非 mount point）→ 跳過（不蓋既有資料）
#   5. v2 case 被刪掉 → 對應的 v1 bind mount 取消 + rmdir
#
# NAS 重開後 bind mount 全消失，但 cron 1 分鐘後會自動補上
#
# Log：/volume2/docker-prod/scripts/原初映像片庫/logs/union_watcher_$(date +%Y%m%d).log

V1_BASE=/volume2/homes/ETtomorrow
V2_BASE=/volume2/homes2/ETtomorrow
LOG_DIR=/volume2/docker-prod/scripts/原初映像片庫/logs
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/union_watcher_$(date +%Y%m%d).log"

# File lock 避免 cron 重疊執行
LOCK_FILE=/tmp/union_v2_watcher.lock
exec 9>"$LOCK_FILE" 2>/dev/null
if command -v flock >/dev/null 2>&1; then
  flock -n 9 2>/dev/null || exit 0
fi

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $1" >> "$LOG"; }

# 預先建一份 /proc/mounts 過濾後的快取，加快 ismount 判斷
MOUNTS_CACHE=$(awk '{print $2}' /proc/mounts | grep "^$V1_BASE/" || true)

is_mounted() {
  # 用 mountpoint 指令（NAS 內建）；備援 grep mounts
  if command -v mountpoint >/dev/null 2>&1; then
    mountpoint -q "$1" 2>/dev/null
  else
    echo "$MOUNTS_CACHE" | grep -qFx "$1"
  fi
}

mount_source() {
  # 回傳該 mount point 的來源路徑
  awk -v mp="$1" '$2 == mp { print $1 }' /proc/mounts | head -1
}

log "watcher tick"

created=0
removed=0
conflicts=0
existing=0
mount_failed=0

shopt -s nullglob
for v2_channel in "$V2_BASE"/*/; do
  channel_name=$(basename "$v2_channel")
  case "$channel_name" in
    "#"*|"_"*|".cache"|"@"*) continue ;;
  esac

  v1_channel="$V1_BASE/$channel_name"
  [ -d "$v1_channel" ] || continue

  for v2_case in "$v2_channel"*/; do
    case_name=$(basename "$v2_case")
    case "$case_name" in
      "#"*|"_"*|".cache"|"@"*) continue ;;
    esac

    v1_case="$v1_channel/$case_name"
    v2_case_clean="${v2_case%/}"   # 去尾斜線

    if is_mounted "$v1_case"; then
      # 已是 mount point，檢查來源正確
      src=$(mount_source "$v1_case")
      if [ "$src" = "$v2_case_clean" ]; then
        existing=$((existing+1))
        continue
      fi
      # 來源錯了：umount 後重 mount
      umount "$v1_case" 2>/dev/null || { log "FIX-UMOUNT failed: $v1_case (src=$src)"; mount_failed=$((mount_failed+1)); continue; }
      mount --bind "$v2_case_clean" "$v1_case" 2>/dev/null && {
        log "FIXED bind: $v1_case <- $v2_case_clean"
        created=$((created+1))
      } || {
        log "FIX-MOUNT failed: $v1_case <- $v2_case_clean"
        mount_failed=$((mount_failed+1))
      }
    elif [ -d "$v1_case" ]; then
      # v1 已有真實資料夾，不蓋
      conflicts=$((conflicts+1))
    elif [ -e "$v1_case" ] || [ -L "$v1_case" ]; then
      # 其他東西（檔案 / 殘留 symlink）→ 跳過
      conflicts=$((conflicts+1))
    else
      # 建空 dir + bind mount
      mkdir -p "$v1_case" 2>/dev/null || { log "MKDIR failed: $v1_case"; mount_failed=$((mount_failed+1)); continue; }
      chown ETtomorrow:users "$v1_case" 2>/dev/null
      mount --bind "$v2_case_clean" "$v1_case" 2>/dev/null && {
        log "CREATED bind: $v1_case <- $v2_case_clean"
        created=$((created+1))
      } || {
        log "MOUNT failed: $v1_case <- $v2_case_clean"
        # 失敗就把空 dir 刪掉
        rmdir "$v1_case" 2>/dev/null
        mount_failed=$((mount_failed+1))
      }
    fi
  done

  # 清理懸空 mount（v2 已不存在）
  for v1_entry in "$v1_channel"/*; do
    [ -d "$v1_entry" ] || continue
    is_mounted "$v1_entry" || continue
    src=$(mount_source "$v1_entry")
    case "$src" in
      "$V2_BASE"/*)
        if [ ! -d "$src" ]; then
          umount "$v1_entry" 2>/dev/null && {
            rmdir "$v1_entry" 2>/dev/null
            log "REMOVED stale: $v1_entry (src $src gone)"
            removed=$((removed+1))
          }
        fi
        ;;
    esac
  done
done

if [ $((created + removed + mount_failed)) -gt 0 ]; then
  log "SUMMARY created=$created removed=$removed existing=$existing conflicts=$conflicts mount_failed=$mount_failed"
fi
