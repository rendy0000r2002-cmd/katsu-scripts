#!/bin/bash
# Symlink V2→V1 透明 union 維護器（cron 每分鐘跑）
# 不用 set -e，因為 flock 失敗 / 個別 symlink 失敗都不該整個 exit
#
# 目的：讓剪輯/團隊從舊 SMB 路徑 (Y:\home, /volume2/homes/ETtomorrow) 也能存取 v2 新案件。
#
# 機制：
#   1. 掃 v2 每個 channel 底下的 case 資料夾
#   2. 對應 v1 channel 底下若沒同名資料夾 → 建 symlink 指向 v2
#   3. 對應 v1 已有「真實資料夾」（非 symlink）→ 跳過（不蓋既有資料）
#   4. v2 case 被刪掉 → 對應的 v1 symlink 也清理掉（避免懸空連結）
#
# Samba 跨 share 跟 symlink 需 wide links 設定，請確認 DSM SMB 進階設定已開
#
# Log：/volume2/docker-prod/scripts/原初映像片庫/logs/symlink_watcher_$(date +%Y%m%d).log
V1_BASE=/volume2/homes/ETtomorrow
V2_BASE=/volume2/homes2/ETtomorrow
LOG_DIR=/volume2/docker-prod/scripts/原初映像片庫/logs
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/symlink_watcher_$(date +%Y%m%d).log"

# File lock 避免 cron 重疊執行（放 /tmp，比較沒權限問題）
LOCK_FILE=/tmp/symlink_v2_watcher.lock
exec 9>"$LOCK_FILE" 2>/dev/null
if command -v flock >/dev/null 2>&1; then
  flock -n 9 2>/dev/null || exit 0
fi

# debug log header（每次跑都記一行，方便確認 cron 真的在跑）
echo "[$(date '+%Y-%m-%d %H:%M:%S')] watcher tick" >> "$LOG"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

created=0
removed=0
conflicts=0
existing=0

# 處理每個 channel
shopt -s nullglob
for v2_channel in "$V2_BASE"/*/; do
  channel_name=$(basename "$v2_channel")
  # 跳過 hidden / system
  case "$channel_name" in
    "#"*|"_"*|".cache"|"@"*) continue ;;
  esac

  v1_channel="$V1_BASE/$channel_name"
  if [ ! -d "$v1_channel" ]; then
    # v1 沒這個 channel 就跳過（不在這層做 union）
    continue
  fi

  # 逐個 case 資料夾
  for v2_case in "$v2_channel"*/; do
    case_name=$(basename "$v2_case")
    case "$case_name" in
      "#"*|"_"*|".cache"|"@"*) continue ;;
    esac

    v1_case="$v1_channel/$case_name"

    if [ -L "$v1_case" ]; then
      # 已是 symlink，檢查指向是否正確
      current_target=$(readlink "$v1_case")
      expected_target="$v2_case"
      # 去掉結尾 / 比較
      [ "${current_target%/}" = "${expected_target%/}" ] && { existing=$((existing+1)); continue; }
      # 指向不對 → 修正
      rm "$v1_case"
      ln -s "$v2_case" "$v1_case"
      chown -h ETtomorrow:users "$v1_case" 2>/dev/null
      echo "[$(ts)] FIXED symlink: $v1_case -> $v2_case" >> "$LOG"
      created=$((created+1))
    elif [ -d "$v1_case" ]; then
      # v1 已有真實資料夾，不蓋
      conflicts=$((conflicts+1))
    elif [ -e "$v1_case" ]; then
      # 其他類型存在物（檔案？），跳過
      conflicts=$((conflicts+1))
    else
      # 建新 symlink + 改 owner 給 ETtomorrow（Synology synoacl 要求）
      ln -s "$v2_case" "$v1_case"
      chown -h ETtomorrow:users "$v1_case" 2>/dev/null
      echo "[$(ts)] CREATED: $v1_case -> $v2_case" >> "$LOG"
      created=$((created+1))
    fi
  done

  # 清理懸空 symlink（v2 已不存在）
  for v1_entry in "$v1_channel"/*; do
    [ -L "$v1_entry" ] || continue
    target=$(readlink "$v1_entry")
    # 只清掉指向 v2 的（避免誤清其他用途的 symlink）
    case "$target" in
      "$V2_BASE"/*)
        if [ ! -e "$v1_entry" ]; then
          rm "$v1_entry"
          echo "[$(ts)] REMOVED stale: $v1_entry" >> "$LOG"
          removed=$((removed+1))
        fi
        ;;
    esac
  done
done

# 只在有變動時寫摘要 log（避免 log 過長）
if [ $((created + removed)) -gt 0 ]; then
  echo "[$(ts)] SUMMARY created=$created removed=$removed existing=$existing conflicts=$conflicts" >> "$LOG"
fi
