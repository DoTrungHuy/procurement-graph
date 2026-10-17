"""Check both Compose deployments with disposable volumes and offline sample replay.

Requires Docker Compose. Never mounts the working database, private configuration
or .env. API and Neo4j have no external route; nginx exposes the test entrypoint.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / 'demo/procurement/docker-compose.yml'
FAKE_TOKEN = 'deployment-fixture-access'
FAKE_KEY = 'deployment-fixture-credential'
FIXTURE_ENV = {
    # Historical snapshots are demonstration data; stale replay must refuse HTTP.
    'DEEPSEEK_API_KEY': '', 'DEEPSEEK_BASE_URL': 'https://api.deepseek.com',
    'DEEPSEEK_MODEL': 'deepseek-flash', 'MODEL_NETWORK_ENABLED': 'false',
    'EXTRACTION_STRATEGY': 'schema', 'RELATION_BACKEND': 'sqlite', 'DEMO_SEED': 'false',
    'APP_API_TOKEN': FAKE_TOKEN, 'MODEL_CONCURRENCY': '1', 'NEO4J_URI': '',
    'NEO4J_USER': 'neo4j', 'NEO4J_PASSWORD': 'deployment-fixture-graph',
    'WEB_BIND_ADDRESS': '127.0.0.1', 'WEB_PORT': '5173',
}


def run(command: list[str], env: dict, timeout: int = 420) -> str:
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{command[0]} exited {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def isolate(config: dict, project: str, scratch: Path, images: dict, graph: bool) -> dict:
    """Replace every host mount and port; keep the shipped healthcheck unchanged."""
    if not re.fullmatch(r'procurement-check-[a-f0-9]{12}-(sqlite|neo4j)', project):
        raise ValueError('Invalid disposable project name')
    config['name'] = project
    config['networks'] = {
        'default': {'name': project + '-network', 'internal': True},
        'ingress': {'name': project + '-ingress'},
    }
    config['volumes'] = {'app-data': {'name': project + '-data'}}
    if graph:
        config['volumes']['graph-data'] = {'name': project + '-graph'}
    for name, service in config['services'].items():
        service['networks'] = {'default': None}
        service['ports'] = []
        if name in images:
            service['image'] = images[name]
    api = config['services']['api']
    api['volumes'] = [
        {'type': 'volume', 'source': 'app-data', 'target': '/app/data'},
        {'type': 'bind', 'source': str(scratch / 'config'), 'target': '/app/config', 'read_only': True},
        {'type': 'bind', 'source': str(scratch / 'official'), 'target': '/app/official', 'read_only': True},
    ]
    api['environment'].update({key: value for key, value in FIXTURE_ENV.items()
                               if key not in {'WEB_BIND_ADDRESS', 'WEB_PORT'}})
    api['environment'].update(REFERENCE_SEED='true', MODEL_RESPONSE_CACHE_DIR='/app/data/demo/cache',
                              PARSE_CACHE_DIR='/app/data/parsed')
    if graph:
        api['environment'].update(NEO4J_URI='bolt://neo4j:7687', RELATION_BACKEND='neo4j')
    web = config['services']['web']
    web['networks']['ingress'] = None
    web['ports'] = [{'target': 80, 'published': '0', 'host_ip': '127.0.0.1', 'protocol': 'tcp'}]
    return config


class Deployment:
    def __init__(self, compose: list[str], env: dict):
        self.compose, self.env, self.origin = compose, env, ''

    def command(self, *args: str, timeout: int = 420) -> str:
        return run(self.compose + list(args), self.env, timeout)

    def request(self, path: str, payload=None, token: bool = True):
        headers = {'Authorization': 'Bearer ' + FAKE_TOKEN} if token else {}
        data = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(payload).encode()
        with urlopen(Request(self.origin + path, data=data, headers=headers), timeout=20) as response:
            content = response.read()
            if 'application/json' in response.headers.get('Content-Type', ''):
                return json.loads(content)
            return response.headers, content

    def ready(self, timeout: int = 180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                health = self.request('/api/health')
                if health['status'] == 'ok':
                    return health
            except (OSError, URLError, ValueError):
                pass
            time.sleep(2)
        raise RuntimeError('Application did not become ready')

    def replay(self) -> dict:
        job = self.request('/api/samples/replay', {'dataset_id': 'deployment-check'})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = self.request('/api/jobs/' + job['id'])
            if state['finished'] and 'model_calls' in state.get('metrics', {}):
                assert state['failed'] == 20, state
                assert state['metrics']['model_calls'] == 0, state
                assert state['metrics']['response_cache_hits'] == 0, state
                assert all('未命中当前规则' in error for error in state['errors']), state
                return state
            time.sleep(1)
        raise RuntimeError('Offline replay did not finish')

    def workbook(self):
        query = urlencode({'dataset_id': 'deployment-check'})
        headers, content = self.request('/api/export/standard.xlsx?' + query)
        assert headers['Content-Disposition'] == 'attachment; filename=procurement-tables.xlsx'
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            workbook = archive.read('xl/workbook.xml').decode()
            for name in ['总表', '标的表', '投标人表']:
                assert f'name="{name}"' in workbook
        return len(content)


def verify_mode(mode: str, run_id: str, scratch: Path, env: dict, images: dict, build: bool) -> dict:
    graph = mode == 'neo4j'
    project = f'procurement-check-{run_id}-{mode}'
    base = ['docker', 'compose', '--project-name', project, '--env-file', str(scratch / 'empty.env'), '-f', str(COMPOSE)]
    if graph:
        base += ['-f', str(COMPOSE.with_name('docker-compose.graph.yml'))]
    config = isolate(json.loads(run(base + ['config', '--format', 'json'], env)), project, scratch, images, graph)
    path = scratch / (mode + '.json')
    path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    deployment = Deployment(['docker', 'compose', '--project-name', project, '--env-file',
                             str(scratch / 'empty.env'), '-f', str(path)], env)
    result = {'mode': mode, 'checks': [], 'model_requests': 0}
    try:
        if build:
            print('Building application images', flush=True)
            deployment.command('build', timeout=1200)
        print('Starting isolated ' + mode + ' deployment', flush=True)
        deployment.command('up', '-d', '--no-build')
        address = deployment.command('port', 'web', '80').strip()
        assert re.fullmatch(r'127\.0\.0\.1:\d+', address), address
        deployment.origin = 'http://' + address
        deployment.ready()
        deployment.command('cp', str(ROOT / 'demo/procurement/data/demo'), 'api:/app/data/')
        deployment.command('restart', 'api')
        health = deployment.ready()
        assert health['relation_backend'] == mode and not health['model_network_enabled'], health
        assert not health['official_data_available'] and not health['deepseek_configured'], health
        result['version'] = health['version']
        _, html = deployment.request('/')
        assert b'id="app"' in html
        assets = re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', html)
        assert assets
        for asset in assets:
            headers, content = deployment.request(asset.decode())
            assert content and 'text/html' not in headers['Content-Type']
        result['checks'].append('nginx page, built assets and API proxy')
        try:
            deployment.request('/api/stats', token=False)
            raise AssertionError('Missing application token accepted')
        except HTTPError as error:
            assert error.code == 401
        result['checks'].append('application token required')
        if graph:
            report = json.loads(deployment.command('exec', '-T', 'api', 'python', '-m', 'app.graph_cli', '--dataset', 'official-reference').strip().splitlines()[-1])
            assert report['passed'] and report['synced_notices'] == 20 and report['parity_cases'] > 0, report
            result['graph_parity'] = report
        job = deployment.replay()
        result['checks'].append('20 stale sample responses rejected; zero cache hits and zero model requests')
        assert deployment.request('/api/stats?dataset_id=deployment-check')['notices'] == 0
        # Import the disclosed snapshot explicitly. This is not a model replay.
        # A distinct dataset proves persistence without startup seeding masking loss.
        program = """from pathlib import Path
from app.database import upsert_notice
from app.models import ProjectExtract
from app.neo4j_sync import sync_notice
from app.config import settings
records=[ProjectExtract.model_validate_json(line) for line in Path('/app/data/demo/sample-results.jsonl').read_text('utf-8').splitlines() if line.strip()]
for record in records:
    upsert_notice(record,[], 'deployment-check')
    if settings.neo4j_uri:
        sync_notice('deployment-check',record.notice_uuid)
print(len(records))
"""
        assert deployment.command('exec','-T','api','python','-c',program).strip().splitlines()[-1] == '20'
        result['data_source'] = 'explicit import of bundled historical snapshot; not current-rule extraction'
        stats = deployment.request('/api/stats?dataset_id=deployment-check')
        assert stats['notices'] == 20 and stats['projects'] == 26 and stats['items'] == 138, stats
        result['workbook_bytes'] = deployment.workbook()
        result['checks'].append('historical snapshot download contains all three standard sheets')
        if graph:
            # The explicitly imported snapshot must populate the live graph.
            result['graph_before_restart'] = deployment.request('/api/graph?dataset_id=deployment-check')
            assert result['graph_before_restart']['nodes']
        saved = deployment.request('/api/settings/model', {
            'base_url': 'https://deployment-fixture.invalid/v1', 'model': 'fixture-saved-model',
            'api_key': FAKE_KEY, 'enabled': True})
        assert saved['api_key_configured'] and FAKE_KEY not in json.dumps(saved), saved
        # Removing and recreating containers is stricter than a process restart.
        deployment.command('down', '--timeout', '20')
        deployment.command('up', '-d', '--no-build')
        deployment.origin = 'http://' + deployment.command('port', 'web', '80').strip()
        health = deployment.ready()
        assert health['model'] == 'fixture-saved-model' and health['deepseek_configured'] and health['model_network_enabled'], health
        assert deployment.request('/api/stats?dataset_id=deployment-check') == stats
        assert deployment.request('/api/jobs/' + job['id'])['metrics']['model_calls'] == 0
        assert deployment.workbook() > 0
        stored = deployment.command('exec', '-T', 'api', 'python', '-c',
                                    "import json; from pathlib import Path; d=json.loads(Path('/app/data/model-settings.local.json').read_text()); assert d['api_key']=='deployment-fixture-credential'; print('saved-credential-ok')")
        assert 'saved-credential-ok' in stored
        result['checks'].append('database, job, download and saved credential survive container recreation')
        if graph:
            assert deployment.request('/api/graph?dataset_id=deployment-check') == result.pop('graph_before_restart')
            result['checks'].append('Neo4j volume and live graph query survive container recreation')
        result['passed'] = True
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result
    except Exception:
        print(deployment.command('logs', '--no-color', '--tail', '80'), flush=True)
        raise
    finally:
        # Only the generated project owns these volumes; working data is never mounted.
        deployment.command('down', '--volumes', '--remove-orphans', '--timeout', '20')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['all', 'sqlite', 'neo4j'], default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'results/deployment-check/verification.json')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error('Report output must stay inside the project')
    env = {**os.environ, **FIXTURE_ENV}
    run(['docker', 'compose', 'version'], env)
    run_id = uuid.uuid4().hex[:12]
    images = {name: f'procurement-check-{run_id}-{name}' for name in ['api', 'web']}
    staging = ROOT / '.cache/deployment-check'
    staging.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'Disposable Compose deployments; fake credentials; internal network; historical snapshot and stale replay rejection', 'passed': False, 'deployments': []}
    try:
        with tempfile.TemporaryDirectory(prefix='compose-', dir=staging) as temporary:
            scratch = Path(temporary).resolve()
            (scratch / 'empty.env').touch()
            for directory in ['config', 'official']:
                (scratch / directory).mkdir()
            modes = ['sqlite', 'neo4j'] if args.mode == 'all' else [args.mode]
            for index, mode in enumerate(modes):
                report['deployments'].append(verify_mode(mode, run_id, scratch, env, images, build=index == 0))
        report['passed'] = True
    finally:
        cleanup = subprocess.run(['docker', 'image', 'rm', '--force', *images.values()],
                                 cwd=ROOT, env=env, capture_output=True, timeout=60)
        report['temporary_image_cleanup_returncode'] = cleanup.returncode
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'deployments': len(report['deployments']), 'model_requests': 0}))


if __name__ == '__main__':
    main()
