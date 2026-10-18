"""Exercise recovery and failure paths using Bash functions instead of any Docker daemon."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('mode', ['success', 'wrong-api-image', 'copy-failure', 'missing-old-image'])
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
python3() { printf 'preflight\n' >> calls.log; }
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
      if [ "$TEST_MODE" = missing-old-image ] && [ "$2:$3" = inspect:sha256:api-old ]; then return 1; fi
      return 0 ;;
    run)
      if [ "$TEST_MODE" = wrong-api-image ]; then printf 'wrong-digest\n'; else printf '%s\n' "$site_expected"; fi ;;
    create) printf 'stopped-staging-container\n' ;;
    cp) [ "$TEST_MODE" != copy-failure ] ;;
    commit)
      if [ "$2" = procurement-ecs-web-1 ]; then printf 'sha256:web-snapshot\n'; else printf 'sha256:web-new\n'; fi ;;
    compose) return 0 ;;
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
    if mode in {'success', 'missing-old-image'}:
        assert result.returncode == 0, result.stderr
        up = next(line for line in calls if line.startswith('compose '))
        assert '--no-build --pull never' in up
        assert up.endswith('api web')
        assert calls[-1].startswith('rm procurement-site-web-stage-')
        assert any(line.startswith('run --rm --pull never --network none --entrypoint python procurement-ecs-api:latest') for line in calls)
        assert any(line.startswith('commit procurement-ecs-web-1 ') for line in calls)
        if mode == 'missing-old-image':
            assert any(line.startswith('commit --change ') for line in calls)
    else:
        assert result.returncode != 0
        assert not any(line.startswith('compose ') for line in calls)
        if mode == 'wrong-api-image':
            assert not any(line.startswith('create ') for line in calls)
            assert not any(line.startswith('commit ') for line in calls)
        else:
            assert calls[-1].startswith('rm procurement-site-web-stage-')
            assert not any(line.startswith('commit procurement-site-web-stage-') for line in calls)
