"""Persistence, live application and credential boundaries; no provider HTTP."""
import asyncio
from dataclasses import replace
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app import extractor, main
from app.config import settings
from app.model_settings import current_settings

_AsyncClient = httpx.AsyncClient


@pytest.fixture
def model_base(tmp_path,monkeypatch):
    base = replace(settings,model_settings_path=str(tmp_path/'model-settings.local.json'),
        deepseek_api_key='fixture-original-credential',deepseek_base_url='https://original.invalid/v1',
        deepseek_model='original-model',model_network_enabled=False,api_token='')
    monkeypatch.setattr(main,'settings',base)
    monkeypatch.setattr(extractor,'settings',base)
    def forbidden(*args,**kwargs):
        raise AssertionError('Saving configuration must never create a provider HTTP client')
    monkeypatch.setattr(httpx,'AsyncClient',forbidden)
    return base


def payload(**values):
    return {'base_url':'https://configured.invalid/v1','model':'configured-model','enabled':True,**values}


def test_saved_configuration_survives_fresh_settings_and_never_returns_key(client,model_base):
    response = client.post('/api/settings/model',json=payload(api_key='fixture-new-credential'))
    assert response.status_code == 200
    assert response.json()['api_key_configured'] is True
    assert 'api_key' not in response.json() and 'fixture-new-credential' not in response.text
    fresh = current_settings(replace(model_base,deepseek_api_key='',deepseek_model='different-default'))
    assert fresh.deepseek_api_key == 'fixture-new-credential' and fresh.deepseek_model == 'configured-model'
    status = client.get('/api/settings/model')
    assert status.status_code == 200 and 'fixture-new-credential' not in status.text
    assert client.get('/api/health').json()['model'] == 'configured-model'


@pytest.mark.parametrize('blank',[None,'','   '])
def test_blank_key_keeps_existing_credential(client,model_base,blank):
    assert client.post('/api/settings/model',json=payload(api_key='fixture-saved-credential')).status_code == 200
    response = client.post('/api/settings/model',json=payload(model='second-model',api_key=blank))
    assert response.status_code == 200
    assert current_settings(model_base).deepseek_api_key == 'fixture-saved-credential'
    assert current_settings(model_base).deepseek_model == 'second-model'


def test_environment_key_is_preserved_on_first_save_without_reentry(client,model_base):
    response = client.post('/api/settings/model',json=payload())
    assert response.status_code == 200
    assert current_settings(model_base).deepseek_api_key == 'fixture-original-credential'


@pytest.mark.parametrize('address',[
    'file:///tmp/key','ftp://example.test','https://user:private-value@example.test',
    'https://example.test?key=private-value','https://example.test/#private-value',
    'https://example.test:invalid','https://bad host.test',
])
def test_invalid_addresses_do_not_leak_credentials_or_replace_previous_configuration(client,model_base,address):
    assert client.post('/api/settings/model',json=payload()).status_code == 200
    before = current_settings(model_base)
    response = client.post('/api/settings/model',json=payload(base_url=address,api_key='fixture-secret-value'))
    assert response.status_code == 422
    assert 'private-value' not in response.text and 'fixture-secret-value' not in response.text
    assert current_settings(model_base) == before


def test_key_validation_errors_do_not_echo_input(client,model_base):
    response = client.post('/api/settings/model',json=payload(api_key='fixture-sensitive\ninvalid'))
    assert response.status_code == 422 and 'fixture-sensitive' not in response.text


def test_full_chat_endpoint_is_normalized_without_network_request(client,model_base):
    response = client.post('/api/settings/model',json=payload(base_url='https://configured.invalid/v1/chat/completions/'))
    assert response.status_code == 200 and response.json()['base_url'] == 'https://configured.invalid/v1'


def test_configuration_save_is_protected_by_access_token_and_local_origin(client,model_base,monkeypatch):
    assert client.post('/api/settings/model',json=payload(),headers={'Origin':'https://other-site.invalid'}).status_code == 403
    assert client.post('/api/settings/model',json=payload(),headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
    monkeypatch.setattr(main,'settings',replace(model_base,api_token='fixture-admin-token'))
    assert client.get('/api/settings/model').status_code == 401
    assert client.post('/api/settings/model',json=payload()).status_code == 401
    assert client.post('/api/settings/model',json=payload(),headers={'Authorization':'Bearer fixture-admin-token'}).status_code == 200


def test_remote_configuration_requires_access_protection(model_base,monkeypatch):
    with TestClient(main.app,client=('192.0.2.50',50000)) as remote:
        assert remote.get('/api/settings/model').status_code == 403
        assert remote.post('/api/settings/model',json=payload()).status_code == 403
        monkeypatch.setattr(main,'settings',replace(model_base,api_token='fixture-admin-token'))
        assert remote.post('/api/settings/model',json=payload(),headers={'Authorization':'Bearer fixture-admin-token'}).status_code == 200


def test_new_extractors_use_saved_configuration_and_existing_instance_keeps_snapshot(client,model_base):
    old = extractor.DeepSeekExtractor()
    assert client.post('/api/settings/model',json=payload(api_key='fixture-saved-credential')).status_code == 200
    new = extractor.DeepSeekExtractor()
    assert old.settings.deepseek_model == 'original-model' and old.api_key == 'fixture-original-credential'
    assert new.settings.deepseek_model == 'configured-model' and new.api_key == 'fixture-saved-credential'
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"project_name":"模拟结果","lots":[]}'},'finish_reason':'stop'}]})
    async def run():
        new.client = _AsyncClient(transport=httpx.MockTransport(handle))
        await new._call('测试文本',['fixture.html'])
        await new.close()
    asyncio.run(run())
    assert len(requests) == 1
    assert str(requests[0].url) == 'https://configured.invalid/v1/chat/completions'
    assert requests[0].headers['Authorization'] == 'Bearer fixture-saved-credential'
    assert json.loads(requests[0].content)['model'] == 'configured-model'


def test_invalid_saved_file_keeps_health_usable_and_can_be_replaced(client,model_base):
    from pathlib import Path
    path = Path(model_base.model_settings_path)
    path.write_text('{broken fixture-secret',encoding='utf-8')
    health = client.get('/api/health')
    assert health.status_code == 200 and health.json()['model_network_enabled'] is False
    assert 'fixture-secret' not in health.text
    response = client.post('/api/settings/model',json=payload())
    assert response.status_code == 422
    response = client.post('/api/settings/model',json=payload(api_key='fixture-recovered-credential'))
    assert response.status_code == 200 and not response.json()['configuration_error']


def test_empty_install_can_save_disabled_settings_but_needs_key_to_enable(client,model_base,monkeypatch):
    empty = replace(model_base,deepseek_api_key='')
    monkeypatch.setattr(main,'settings',empty)
    assert client.post('/api/settings/model',json=payload(enabled=False)).status_code == 200
    assert client.post('/api/settings/model',json=payload(enabled=True)).status_code == 422
    assert not current_settings(empty).model_network_enabled


def test_failed_atomic_write_preserves_previous_settings_and_cleans_temporary_file(client,model_base,monkeypatch):
    from pathlib import Path
    assert client.post('/api/settings/model',json=payload()).status_code == 200
    path = Path(model_base.model_settings_path)
    original = path.read_bytes()
    def denied(*args,**kwargs): raise PermissionError('fixture write denied')
    monkeypatch.setattr(Path,'replace',denied)
    response = client.post('/api/settings/model',json=payload(model='replacement-model'))
    assert response.status_code == 503
    assert path.read_bytes() == original
    assert not list(path.parent.glob('.model-settings-*.local.json'))


def test_read_only_deployment_serves_server_configuration_and_rejects_web_writes(client,model_base,monkeypatch):
    locked = replace(model_base,model_settings_readonly=True,api_token='fixture-visitor-token',
                     deepseek_api_key='fixture-server-key',deepseek_base_url='https://server.invalid/v1',
                     deepseek_model='server-model',model_network_enabled=True)
    monkeypatch.setattr(main,'settings',locked)
    monkeypatch.setattr(extractor,'settings',locked)
    headers = {'Authorization':'Bearer fixture-visitor-token'}

    listing = client.get('/api/settings/model',headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body['read_only'] is True and body['base_url'] == 'https://server.invalid/v1'
    assert body['model'] == 'server-model' and body['api_key_configured'] is True
    assert 'fixture-server-key' not in listing.text

    health = client.get('/api/health')
    assert health.status_code == 200 and health.json()['model_settings_readonly'] is True

    rejected = client.post('/api/settings/model',json=payload(api_key='fixture-attacker-key'),headers=headers)
    assert rejected.status_code == 422 and '服务端' in rejected.json()['detail']
    assert client.get('/api/settings/model').status_code == 401


def test_read_only_deployment_ignores_previously_saved_file(client,model_base,monkeypatch):
    from pathlib import Path
    Path(model_base.model_settings_path).write_text(json.dumps({
        'base_url':'https://stale.invalid/v1','model':'stale-model',
        'api_key':'fixture-stale-key','enabled':False}),encoding='utf-8')
    locked = replace(model_base,model_settings_readonly=True)
    monkeypatch.setattr(main,'settings',locked)
    effective = current_settings(locked)
    assert effective.deepseek_model == 'original-model'
    assert effective.deepseek_api_key == 'fixture-original-credential'
    assert effective.model_network_enabled is False
