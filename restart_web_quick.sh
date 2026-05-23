#!/bin/bash
# 套用 katsu-web-v2 新的 mount + env（不需要 rebuild，30 秒搞定）
set -e
cd /volume2/docker-prod/katsu-web-v2/web

echo "=== Stopping katsu-web-v2 ==="
docker compose down

echo ""
echo "=== Starting with new mounts/env ==="
docker compose up -d

echo ""
echo "=== Waiting 15s for container to be ready ==="
sleep 15

echo ""
echo "=== Verifying env vars (should see GOOGLE_APPLICATION_CREDENTIALS) ==="
docker exec katsu-web-v2 env | grep -E "GOOGLE_APPLICATION|NAS_LINUX|NAS_HOME" || echo "FAIL: env missing"

echo ""
echo "=== Verifying mounts (should see rw on homes/ETtomorrow + ro on volume2) ==="
docker exec katsu-web-v2 mount | grep -E "/volume2" || echo "FAIL: mount missing"

echo ""
echo "=== Verifying write access to /volume2/homes/ETtomorrow ==="
docker exec katsu-web-v2 touch /volume2/homes/ETtomorrow/.write_test_$$ 2>&1 &&   docker exec katsu-web-v2 rm /volume2/homes/ETtomorrow/.write_test_$$ &&   echo "v1 RW: OK" || echo "FAIL: cannot write to /volume2/homes/ETtomorrow"

echo ""
echo "=== Verifying write access to /volume2/homes2/ETtomorrow ==="
docker exec katsu-web-v2 touch /volume2/homes2/ETtomorrow/.write_test_$$ 2>&1 &&   docker exec katsu-web-v2 rm /volume2/homes2/ETtomorrow/.write_test_$$ &&   echo "v2 RW: OK" || echo "FAIL: cannot write to /volume2/homes2/ETtomorrow"

echo ""
echo "=== DONE ==="
