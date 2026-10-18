#!/usr/bin/env bash
# Recover this update after API build succeeded and the Node image mirror failed.
set -euo pipefail
cd /tmp/competition-site-readiness-20261018/competition-site-update
site_root=/opt/procurement-project-next
python3 scripts/apply_site_update.py --root "$site_root"

site_stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
site_web_base=$(docker inspect --format '{{.Image}}' procurement-ecs-web-1)
site_web_image=$(docker inspect --format '{{.Config.Image}}' procurement-ecs-web-1)
site_api_image=$(docker inspect --format '{{.Config.Image}}' procurement-ecs-api-1)
docker image inspect "$site_api_image" >/dev/null
site_expected=$(sha256sum payload/demo/procurement/backend/app/exports.py | cut -d ' ' -f 1)
site_actual=$(docker run --rm --pull never --network none --entrypoint python "$site_api_image" -c 'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("/app/app/exports.py").read_bytes()).hexdigest())')
if [ "$site_actual" != "$site_expected" ]; then
  echo 'The built API image does not contain this update; no containers were changed.' >&2
  exit 1
fi

docker image tag "$site_web_base" "procurement-site-web-rollback:$site_stamp"
docker image tag "$(docker inspect --format '{{.Image}}' procurement-ecs-api-1)" "procurement-site-api-rollback:$site_stamp"
site_stage="procurement-site-web-stage-$site_stamp"
docker create --pull never --name "$site_stage" "$site_web_base" >/dev/null
trap 'docker rm "$site_stage" >/dev/null 2>&1 || true' EXIT
docker cp payload/demo/procurement/frontend/dist/. "$site_stage:/usr/share/nginx/html/"
docker cp payload/demo/procurement/frontend/nginx.conf "$site_stage:/etc/nginx/conf.d/default.conf"
site_new_image=$(docker commit "$site_stage")
docker image tag "$site_new_image" "$site_web_image"

python3 scripts/apply_site_update.py --root "$site_root"
docker compose --project-name procurement-ecs --env-file "$site_root/.env" \
  -f "$site_root/demo/procurement/docker-compose.yml" \
  -f "$site_root/demo/procurement/docker-compose.ecs.yml" \
  up -d --no-build --pull never --wait --wait-timeout 240 api web
echo "Updated using local images. Previous images: procurement-site-web-rollback:$site_stamp and procurement-site-api-rollback:$site_stamp"
