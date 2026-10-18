"""Exercise recovery and failure paths using Bash functions instead of any Docker daemon."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('mode', ['success', 'wrong-api-image', 'copy-failure', 'missing-api-tag',
                                  'snapshot-failure', 'api-run-failure', 'web-commit-failure',
                                  'nginx-failure', 'final-preflight-failure', 'compose-failure'])
def test_prebuilt_recovery_keeps_live_containers_until_images_are_ready(tmp_path, mode):
    bash = os.environ.get('BASH_EXECUTABLE') or shutil.which('bash') or shutil.which('sh')
    if not bash:
        pytest.skip('Bash executable required for recovery-flow tests')
    script = (Path(__file__).resolve().parents[1] / 'finish_site_update.sh').read_text('utf-8')
    script = script.replace('cd /tmp/competition-site-readiness-20261018/competition-site-update',
                            'cd "' + tmp_path.as_posix() + '"')
    prelude = r'''
date() { printf '20261018T014242Z\n'; }
sha256sum() { printf 'fixture-digest  %s\n' "$1"; }
cut() { read -r digest rest; printf '%s\n' "$digest"; }
site_preflights=0
python3() {
  if [ "$1" = scripts/snapshot_api_rootfs.py ]; then
    printf 'snapshot-api-rootfs\n' >> calls.log
    [ "$TEST_MODE" != snapshot-failure ] || return 1
    printf 'procurement-site-api-rollback:container-d5b779260b8f\n'
  else
    printf 'preflight\n' >> calls.log
    site_preflights=$((site_preflights + 1))
    if [ "$TEST_MODE" = final-preflight-failure ] && [ "$site_preflights" = 2 ]; then return 1; fi
  fi
}
docker() {
  printf '%s\n' "$*" >> calls.log
  case "$1" in
    inspect)
      case "$3:$4" in
        *Config.Image*) printf 'sha256:missing-old-image\n' ;;
        *web*) printf 'sha256:web-old\n' ;;
        *) printf 'sha256:api-old\n' ;;
      esac ;;
    image)
      case "$*" in *sha256:*) return 72 ;; esac
      if [ "$TEST_MODE" = missing-api-tag ] && [ "$2:$3" = inspect:procurement-ecs-api:latest ]; then return 1; fi
      return 0 ;;
    run)
      case "$*" in
        *'--entrypoint nginx'*) [ "$TEST_MODE" != nginx-failure ] ;;
        *)
          [ "$TEST_MODE" != api-run-failure ] || return 1
          if [ "$TEST_MODE" = wrong-api-image ]; then printf 'wrong-digest\n'; else printf '%s\n' "$site_expected"; fi ;;
      esac ;;
    create)
      case "$*" in *sha256:*) return 72 ;; esac
      printf 'stopped-staging-container\n' ;;
    cp) [ "$TEST_MODE" != copy-failure ] ;;
    commit)
      case "$2" in procurement-site-web-stage-*) [ "$TEST_MODE" != web-commit-failure ] || return 1 ;; esac
      printf 'sha256:unresolvable-internal-id\n' ;;
    compose) [ "$TEST_MODE" != compose-failure ] ;;
    rm) return 0 ;;
    *) echo 'Unexpected Docker operation' >&2; return 71 ;;
  esac
}
'''
    target = tmp_path / 'payload/demo/procurement/backend/app'
    target.mkdir(parents=True)
    (target / 'exports.py').write_text('tested exports', encoding='utf-8')
    file = tmp_path / 'recovery.sh'
    file.write_text(prelude + '\n' + script, encoding='utf-8', newline='\n')
    result = subprocess.run([bash, str(file)], env={**os.environ, 'TEST_MODE': mode},
                            cwd=tmp_path, text=True, encoding='utf-8', errors='replace', capture_output=True)
    calls = (tmp_path / 'calls.log').read_text('utf-8').splitlines()
    assert not any(line.startswith(('pull ', 'build ')) for line in calls)
    if mode == 'success':
        assert result.returncode == 0, result.stderr
        up = next(line for line in calls if line.startswith('compose '))
        assert '--no-build --pull never' in up
        assert up.endswith('api web')
        assert calls[-1].startswith('rm procurement-site-web-stage-')
        assert any(line.startswith('run --rm --pull never --network none --entrypoint python procurement-ecs-api:latest') for line in calls)
        assert any(line.startswith('commit procurement-ecs-web-1 ') for line in calls)
        assert calls.index('snapshot-api-rootfs') < next(i for i, line in enumerate(calls) if line.startswith('commit '))
        assert not any(line.startswith('commit ') and 'procurement-ecs-api-1' in line for line in calls)
        assert any(line.startswith('commit procurement-site-web-stage-') and line.endswith('procurement-ecs-web:latest') for line in calls)
        assert any('--entrypoint nginx procurement-ecs-web:latest -t' in line for line in calls)
        staging = next(line for line in calls if line.startswith('create '))
        assert '--label com.docker.compose.project= ' in staging
        assert '--label com.docker.compose.service= ' in staging
        assert '--label com.docker.compose.oneoff=True ' in staging
        assert 'container-d5b779260b8f' in result.stdout
    elif mode == 'compose-failure':
        assert result.returncode != 0
        assert any(line.startswith('compose ') for line in calls)
        assert 'service-switch' in result.stderr
        assert 'Rollback images:' in result.stdout
        assert 'Services switched using local images.' not in result.stdout
        assert calls[-1].startswith('rm procurement-site-web-stage-')
    else:
        assert result.returncode != 0
        assert not any(line.startswith('compose ') for line in calls)
        if mode in {'wrong-api-image', 'missing-api-tag', 'snapshot-failure', 'api-run-failure'}:
            assert not any(line.startswith('create ') for line in calls)
            assert not any(line.startswith('commit ') for line in calls)
        else:
            assert calls[-1].startswith('rm procurement-site-web-stage-')
            if mode == 'copy-failure':
                assert not any(line.startswith('commit procurement-site-web-stage-') for line in calls)
        if mode != 'wrong-api-image':
            assert 'Site update stopped during ' in result.stderr
