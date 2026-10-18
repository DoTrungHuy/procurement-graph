#!/usr/bin/env bash
# Recover this update after API build succeeded and the Node image mirror failed.
set -Eeuo pipefail
site_phase=preflight
trap 'site_status=$?; printf "Site update stopped during %s (exit %s). Keep running containers and paste this output.\n" "$site_phase" "$site_status" >&2; exit "$site_status"' ERR
cd /tmp/competition-site-readiness-20261018/competition-site-update
site_root=/opt/procurement-project-next
printf '[1/7] Validate project and confirm extraction is idle\n'
python3 scripts/apply_site_update.py --root "$site_root"

site_stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
# Use Compose service tags throughout. Internal container/config image digests
# are not necessarily accepted by docker image inspect on this image store.
site_web_image=procurement-ecs-web:latest
site_api_image=procurement-ecs-api:latest
site_phase=api-image-check
printf '[2/7] Verify the already-built API image\n'
docker image inspect "$site_api_image" >/dev/null
site_expected=$(sha256sum payload/demo/procurement/backend/app/exports.py | cut -d ' ' -f 1)
site_actual=$(docker run --rm --pull never --network none --entrypoint python "$site_api_image" -c 'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("/app/app/exports.py").read_bytes()).hexdigest())')
if [ "$site_actual" != "$site_expected" ]; then
  echo 'The built API image does not contain this update; no containers were changed.' >&2
  exit 1
fi

# Snapshot the live filesystem instead of committing the old API container:
# the observed commit fails with a missing-content error on containerd.
site_phase=api-rootfs-snapshot
printf '[3/7] Prepare and verify the old API rollback image\n'
site_api_rollback=$(python3 scripts/snapshot_api_rootfs.py)
case "$site_api_rollback" in
  procurement-site-api-rollback:container-*) ;;
  *) echo 'Unexpected API rollback tag; no containers were switched.' >&2; exit 1 ;;
esac

# Commit directly to named tags; do not reuse commit's returned digest either.
site_phase=web-snapshot
printf '[4/7] Save old web image and prepare updated assets\n'
site_web_base="procurement-site-web-rollback:$site_stamp"
docker commit procurement-ecs-web-1 "$site_web_base" >/dev/null
site_stage="procurement-site-web-stage-$site_stamp"
printf 'Rollback images: %s and %s\n' "$site_api_rollback" "$site_web_base"
docker create --pull never --name "$site_stage" "$site_web_base" >/dev/null
trap 'docker rm "$site_stage" >/dev/null 2>&1 || true' EXIT
docker cp payload/demo/procurement/frontend/dist/. "$site_stage:/usr/share/nginx/html/"
docker cp payload/demo/procurement/frontend/nginx.conf "$site_stage:/etc/nginx/conf.d/default.conf"
site_phase=web-image-check
printf '[5/7] Commit and check the new web image\n'
docker commit "$site_stage" "$site_web_image" >/dev/null
docker run --rm --pull never --network none --add-host api:127.0.0.1 \
  --entrypoint nginx "$site_web_image" -t

site_phase=final-preflight
printf '[6/7] Recheck task state before switching services\n'
python3 scripts/apply_site_update.py --root "$site_root"
site_phase=service-switch
printf '[7/7] Switch services using local images\n'
docker compose --project-name procurement-ecs --env-file "$site_root/.env" \
  -f "$site_root/demo/procurement/docker-compose.yml" \
  -f "$site_root/demo/procurement/docker-compose.ecs.yml" \
  up -d --no-build --pull never --wait --wait-timeout 240 api web
echo "Services switched using local images. Rollback images: $site_web_base and $site_api_rollback"
echo 'Verify public HTTPS, the selected full dataset, and exports before accepting the deployment.'
