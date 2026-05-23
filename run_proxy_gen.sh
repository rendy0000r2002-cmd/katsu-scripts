#!/bin/bash
export SUPABASE_URL='http://192.168.18.6:3001'
export SUPABASE_SERVICE_ROLE_KEY='***REDACTED-SUPABASE-SR***'
export SUPABASE_ANON_KEY='***REDACTED-SUPABASE-ANON***'
export SUDO_PASS='***REDACTED-NAS-PASS***'
cd /volume2/docker-prod/scripts/原初映像片庫
rm -f /volume2/docker-prod/scripts/原初映像片庫/proxy_gen.log /volume2/docker-prod/scripts/原初映像片庫/proxy_gen.done
/usr/bin/python3 nas_generate_proxies.py > /volume2/docker-prod/scripts/原初映像片庫/proxy_gen.log 2>&1
touch /volume2/docker-prod/scripts/原初映像片庫/proxy_gen.done
