#!/bin/bash
export SUPABASE_URL='http://192.168.18.6:3001'
export SUPABASE_SERVICE_ROLE_KEY='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoia2F0c3Utc2VsZi1ob3N0IiwiaWF0IjoxNzc4ODAyNTg4LCJleHAiOjIwOTQxNjI1ODh9.-N7ZaqEfybA7Mnyl8W8SQ6MQYskynQgYHh5cMjsLbI8'
export SUPABASE_ANON_KEY='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6ImthdHN1LXNlbGYtaG9zdCIsImlhdCI6MTc3ODgwMjU4OCwiZXhwIjoyMDk0MTYyNTg4fQ.piOShTjKQdUfEP1ADWYl9S22c-fN78EfJhXFuoRi0dI'
export SUDO_PASS='Et666666'
cd /volume2/docker-prod/scripts/原初映像片庫
rm -f /volume2/docker-prod/scripts/原初映像片庫/proxy_gen.log /volume2/docker-prod/scripts/原初映像片庫/proxy_gen.done
/usr/bin/python3 nas_generate_proxies.py > /volume2/docker-prod/scripts/原初映像片庫/proxy_gen.log 2>&1
touch /volume2/docker-prod/scripts/原初映像片庫/proxy_gen.done
