"""Check streamed recovery metadata and failure handling; no Docker daemon used."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def snapshot_module():
    file = Path(__file__).resolve().parents[1] / 'snapshot_api_rootfs.py'
    spec = importlib.util.spec_from_file_location('snapshot_api_rootfs', file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration():
    return {'Cmd': ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000'],
            'Entrypoint': None, 'WorkingDir': '/app', 'User': '', 'StopSignal': 'SIGTERM',
            'Env': ['PATH=/opt/venv/bin:/usr/bin', 'PYTHONUNBUFFERED=1',
                    'APP_API_TOKEN=private-access-token', 'DEEPSEEK_API_KEY=private-model-key',
                    'NEO4J_PASSWORD=private-password', 'CUSTOM_SECRET=other-private-value'],
            'Labels': {'com.docker.compose.project': 'procurement-ecs'},
            'Healthcheck': {'Test': ['CMD-SHELL', 'python -c "print(1)"'],
                            'Interval': 15_000_000_000, 'Timeout': 5_000_000_000,
                            'StartPeriod': 60_000_000_000, 'Retries': 3}}


def test_import_keeps_launch_and_health_metadata_without_runtime_secrets(snapshot_module):
    changes = snapshot_module.image_changes(configuration(), 'a' * 64, 'b' * 64)
    text = '\n'.join(changes)
    assert 'WORKDIR /app' in changes
    assert 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]' in changes
    assert 'ENV PATH="/opt/venv/bin:/usr/bin"' in changes
    assert 'ENV PYTHONUNBUFFERED="1"' in changes
    assert 'STOPSIGNAL SIGTERM' in changes
    assert ('HEALTHCHECK --interval=15000000000ns --timeout=5000000000ns '
            '--start-period=60000000000ns --retries=3 CMD python -c "print(1)"') in changes
    for private in ('private-access-token', 'private-model-key', 'private-password',
                    'other-private-value', 'com.docker.compose.project'):
        assert private not in text


@pytest.mark.parametrize('test, expected', [(['NONE'], 'HEALTHCHECK NONE'),
                                          (['CMD', 'python', '-V'], 'CMD ["python", "-V"]')])
def test_healthcheck_forms_are_preserved(snapshot_module, test, expected):
    config = configuration()
    config['Healthcheck']['Test'] = test
    changes = snapshot_module.image_changes(config, 'a' * 64, 'b' * 64)
    assert any(value.endswith(expected) for value in changes)


@pytest.mark.parametrize('mode', ['success', 'reuse', 'conflicting-tag', 'import-failure',
                                  'export-failure', 'hash-mismatch', 'low-disk',
                                  'inspect-failure', 'bad-healthcheck'])
def test_snapshot_failures_preserve_the_running_service(snapshot_module, tmp_path, monkeypatch, mode):
    module = snapshot_module
    identifier, digest = 'a' * 64, 'b' * 64
    tag = 'procurement-site-api-rollback:container-' + identifier[:12]
    config = configuration()
    if mode == 'bad-healthcheck':
        config['Healthcheck']['Test'] = ['UNSUPPORTED']
    calls = []
    exporters = []

    class Exporter:
        def __init__(self, args, **kwargs):
            calls.append(args)
            self.stdout = io.BytesIO(b'root filesystem fixture')
            self.status = None
            self.terminated = False
            exporters.append(self)
            if mode == 'export-failure':
                kwargs['stderr'].write(b'fixture export content error')

        def poll(self):
            return self.status

        def terminate(self):
            self.terminated = True
            self.status = -15

        def wait(self):
            self.status = self.status if self.status is not None else (1 if mode == 'export-failure' else 0)
            return self.status

    def docker(args, **kwargs):
        calls.append(args)
        returncode, stdout, stderr = 0, '', ''
        if args[1] == 'inspect':
            stdout = json.dumps([{'Id': identifier, 'State': {'Running': True}, 'Config': config}])
        elif args[1] == 'exec':
            stdout = digest + '\n'
        elif args[1:3] == ['image', 'inspect']:
            if mode in {'reuse', 'conflicting-tag'}:
                stdout = json.dumps({module.SOURCE_LABEL: identifier,
                                     module.HASH_LABEL: digest if mode == 'reuse' else 'c' * 64})
            else:
                returncode = 1
                stderr = 'content digest not found' if mode == 'inspect-failure' else 'Error: No such image: ' + tag
        elif args[1] == 'info':
            stdout = json.dumps(str(tmp_path))
        elif args[1] == 'import':
            assert kwargs['stdin'].read() == b'root filesystem fixture'
            assert args[-2:] == ['-', tag]
            returncode = 1 if mode == 'import-failure' else 0
            stderr = 'fixture import error' if returncode else ''
        elif args[1] == 'run':
            assert '--network' in args and 'none' in args and '--read-only' in args
            assert '--mount' not in args and '-v' not in args
            stdout = 'c' * 64 if mode == 'hash-mismatch' else digest
        elif args[1:3] == ['image', 'rm']:
            assert args[-1] == tag
        else:
            raise AssertionError('Unexpected Docker operation: ' + repr(args))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(module.subprocess, 'run', docker)
    monkeypatch.setattr(module.subprocess, 'Popen', Exporter)
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda path: SimpleNamespace(
        free=(2 if mode == 'low-disk' else 10) * 1024**3))
    if mode in {'success', 'reuse'}:
        assert module.snapshot() == tag
        assert not any('rm' in args for args in calls)
        if mode == 'reuse':
            assert not exporters
        else:
            assert exporters and not exporters[0].terminated
    else:
        with pytest.raises(RuntimeError):
            module.snapshot()
        if mode in {'import-failure', 'export-failure', 'hash-mismatch'}:
            assert calls[-1] == ['docker', 'image', 'rm', tag]
        else:
            assert not exporters
            assert not any('rm' in args for args in calls)
    assert not any(args[1] in {'stop', 'restart', 'commit', 'compose', 'cp'} for args in calls)
    assert all('private-access-token' not in str(args) and 'private-model-key' not in str(args) for args in calls)
