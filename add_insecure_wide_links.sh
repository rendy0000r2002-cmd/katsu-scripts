#!/bin/bash
# 加 'allow insecure wide links = yes' 到 smb.conf，Samba 4.x 跨 share symlink 必備
LOG=/volume2/docker-prod/scripts/原初映像片庫/logs/add_insecure_wide_links.log
{
  echo "=== @ $(date) ==="

  SMB_CONF=/etc/samba/smb.conf
  TS=$(date +%Y%m%d_%H%M%S)
  cp "$SMB_CONF" "/volume2/docker-prod/scripts/原初映像片庫/smb.conf.bak.${TS}_pre_insecure"

  if grep -q "allow insecure wide links" "$SMB_CONF"; then
    echo "已存在 allow insecure wide links，略過"
  else
    # 在 unix extensions = no 後面插入
    sed -i '/unix extensions = no/a\\tallow insecure wide links = yes' "$SMB_CONF"
    echo "已加入 allow insecure wide links = yes"
  fi

  echo ""
  echo "--- 現有 wide-link 相關設定 ---"
  grep -niE "follow.symlinks|wide.links|unix.extensions|allow.insecure" "$SMB_CONF"

  echo ""
  echo "--- 重啟 SMB ---"
  synopkg restart SMBService 2>&1

  sleep 3

  echo ""
  echo "--- testparm 驗證 ---"
  /var/packages/SMBService/target/usr/bin/testparm -s 2>/dev/null | grep -iE "wide|follow|unix.ext|allow.insecure" || echo "  沒匹配"
} > "$LOG" 2>&1
cat "$LOG"
