"""Deployment preflight regression; all Docker calls are replaced, no target service is touched."""
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('mode', ['validate', 'correct-root', 'wrong-root'])
def test_preflight_uses_the_actual_running_directory(tmp_path, monkeypatch, capsys, mode):
    root = tmp_path / 'procurement-project-next'
    directory = root / 'demo/procurement'
    directory.mkdir(parents=True)
    compose = directory / 'docker-compose.yml'
    override = directory / 'docker-compose.ecs.yml'
    for file in (compose, override):
        file.write_text('services: {}', encoding='utf-8')
    package = tmp_path / 'package'
    (package / 'scripts').mkdir(parents=True)
    (package / 'site-update-manifest.json').write_text(json.dumps({'files': {}}), encoding='utf-8')
    script = package / 'scripts/apply_site_update.py'
    script.write_bytes((Path(__file__).resolve().parents[1] / 'apply_site_update.py').read_bytes())
    labels = {'com.docker.compose.project.working_dir': str(directory),
              'com.docker.compose.project.config_files': str(compose) + ',' + str(override),
              'com.docker.compose.project': 'procurement-ecs'}
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        if args[:3] == ('docker', 'ps', '-q'):
            output = 'test-api\n'
        elif args[:2] == ('docker', 'inspect'):
            output = json.dumps([{'Name': '/procurement-ecs-api-1', 'Config': {'Labels': labels}}])
        elif args[:2] == ('docker', 'exec'):
            output = '0\n'
        else:
            raise AssertionError('Unexpected mutation command: ' + str(args))
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr('subprocess.run', docker)
    if mode == 'validate':
        monkeypatch.setattr(sys, 'argv', [str(script), '--validate-only'])
        runpy.run_path(str(script), run_name='__main__')
        assert not calls
        assert json.loads(capsys.readouterr().out)['valid']
    elif mode == 'correct-root':
        monkeypatch.setattr(sys, 'argv', [str(script), '--root', str(root)])
        runpy.run_path(str(script), run_name='__main__')
        assert json.loads(capsys.readouterr().out)['apply'] is False
    else:
        wrong = tmp_path / 'procurement-project'
        wrong.mkdir()
        monkeypatch.setattr(sys, 'argv', [str(script), '--root', str(wrong)])
        with pytest.raises(RuntimeError, match='no files changed') as error:
            runpy.run_path(str(script), run_name='__main__')
        diagnostics = json.loads(str(error.value).partition('Observed Compose directories: ')[2])
        assert diagnostics[0]['working_directory'] == str(directory)
