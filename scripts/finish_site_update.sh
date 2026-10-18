#!/usr/bin/env bash
# Recover this update after API build succeeded and the Node image mirror failed.
set -euo pipefail
cd /tmp/competition-site-readiness-20261018/competition-site-update
site_root=/opt/procurement-project-next
python3 scripts/apply_site_update.py --root "$site_root"

site_stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
# Use Compose service tags throughout. Internal container/config image digests
# are not necessarily accepted by docker image inspect on this image store.
site_web_image=procurement-ecs-web:latest
site_api_image=procurement-ecs-api:latest
docker image inspect "$site_api_image" >/dev/null
site_expected=$(sha256sum payload/demo/procurement/backend/app/exports.py | cut -d ' ' -f 1)
site_actual=$(docker run --rm --pull never --network none --entrypoint python "$site_api_image" -c 'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("/app/app/exports.py").read_bytes()).hexdigest())')
if [ "$site_actual" != "$site_expected" ]; then
  echo 'The built API image does not contain this update; no containers were changed.' >&2
  exit 1
fi

# Commit directly to named tags; do not reuse commit's returned digest either.
site_web_base="procurement-site-web-rollback:$site_stamp"
docker commit procurement-ecs-web-1 "$site_web_base" >/dev/null
docker commit --change 'ENV APP_API_TOKEN="" DEEPSEEK_API_KEY="" NEO4J_PASSWORD=""' \
  procurement-ecs-api-1 "procurement-site-api-rollback:$site_stamp" >/dev/null
site_stage="procurement-site-web-stage-$site_stamp"
docker create --pull never --name "$site_stage" "$site_web_base" >/dev/null
trap 'docker rm "$site_stage" >/dev/null 2>&1 || true' EXIT
docker cp payload/demo/procurement/frontend/dist/. "$site_stage:/usr/share/nginx/html/"
docker cp payload/demo/procurement/frontend/nginx.conf "$site_stage:/etc/nginx/conf.d/default.conf"
docker commit "$site_stage" "$site_web_image" >/dev/null

python3 scripts/apply_site_update.py --root "$site_root"
docker compose --project-name procurement-ecs --env-file "$site_root/.env" \
  -f "$site_root/demo/procurement/docker-compose.yml" \
  -f "$site_root/demo/procurement/docker-compose.ecs.yml" \
  up -d --no-build --pull never --wait --wait-timeout 240 api web
echo "Updated using local images. Previous images: procurement-site-web-rollback:$site_stamp and procurement-site-api-rollback:$site_stamp"
