#!/bin/bash
# 嘗試各種方式重啟 Synology Samba
echo "嘗試 1: synoservicectl --restart pkgctl-Samba"
synoservicectl --restart pkgctl-Samba 2>&1 && { echo "✓ OK"; exit 0; }
echo ""
echo "嘗試 2: synoservicectl --restart smbd"
synoservicectl --restart smbd 2>&1 && { echo "✓ OK"; exit 0; }
echo ""
echo "嘗試 3: synoservice --restart smbd"
synoservice --restart smbd 2>&1 && { echo "✓ OK"; exit 0; }
echo ""
echo "嘗試 4: systemctl restart smbd"
systemctl restart smbd 2>&1 && { echo "✓ OK"; exit 0; }
echo ""
echo "嘗試 5: /usr/syno/etc.defaults/rc.subr/smbd.subr restart"
/usr/syno/etc.defaults/rc.subr/smbd.subr restart 2>&1 && { echo "✓ OK"; exit 0; }
echo ""
echo "全部失敗 - 請手動到 DSM 控制台 → 檔案服務 → SMB → 套用"
exit 1
