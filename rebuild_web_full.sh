#!/bin/bash
# Rebuild katsu-web-v2（新加 page/API 必須重 build）
set -e
cd /volume2/docker-prod/katsu-web-v2/web

echo "=== Rebuilding katsu-web-v2 (5-10 分鐘，包含 Next.js 重新 build) ==="
docker compose build

echo ""
echo "=== Restarting ==="
docker compose down
docker compose up -d

echo ""
echo "=== Wait 15s for startup ==="
sleep 15

echo ""
echo "=== Health check ==="
docker exec katsu-web-v2 env | grep -E "GOOGLE_APPLICATION|NAS_LINUX" || echo "FAIL: env missing"
echo ""
docker exec katsu-web-v2 ls /volume2/homes/ETtomorrow > /dev/null && echo "v1 mount OK" || echo "FAIL v1"
docker exec katsu-web-v2 ls /volume2/homes2/ETtomorrow > /dev/null && echo "v2 mount OK" || echo "FAIL v2"

echo ""
echo "=== DONE ==="
echo "測試： https://randynas.tailb1ff82.ts.net/admin/cleanup-confirm?token=test 應該回「無 pending cleanup」"
