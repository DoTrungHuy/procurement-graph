import asyncio
from dataclasses import replace
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from app.config import settings
from app.extractor import DeepSeekExtractor, FatalModelError, OutputTruncated
from app.model_transport import read_model_response


def stream_response(text='{"project_name":"采购项目","lots":[]}', *, finish='stop', credit=0):
    events = [
        {'model': 'deepseek-v4.1-flash', 'choices': [{'index': 0, 'delta': {'reasoning_content': '不属于抽取结果'}, 'finish_reason': ''}]},
        *[{'choices': [{'index': 0, 'delta': {'content': part}, 'finish_reason': ''}]}
          for part in (text[:8], text[8:])],
    ]
    if finish is not None:
        events.append({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})
    events.append({'choices': [], 'usage': {'prompt_tokens': 30, 'completion_tokens': 10,
                                           'total_tokens': 40, 'credit': credit}})
    body = ': heartbeat\r\n\r\n' + ''.join('data: '+json.dumps(event, ensure_ascii=False)+'\r\n\r\n' for event in events)
    return httpx.Response(200, headers={'content-type': 'text/event-stream; charset=utf-8'},
                          content=(body+'data: [DONE]\r\n\r\n').encode())


def test_json_response_is_preserved():
    body = {'model': 'direct-service', 'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]}
    assert read_model_response(httpx.Response(200, json=body)) == body


def test_gateway_stream_preserves_chinese_text_usage_and_finish_event():
    body = read_model_response(stream_response())
    assert json.loads(body['choices'][0]['message']['content'])['project_name'] == '采购项目'
    assert body['choices'][0]['finish_reason'] == 'stop'
    assert body['usage'] == {'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40, 'credit': 0}
    assert body['model'] == 'deepseek-v4.1-flash'


def test_multiline_sse_data_and_usage_after_finish_are_supported():
    body = 'data: {"choices":\ndata: [{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n\n'
    body += 'data: {"usage":{"credit":0}}\n\ndata: [DONE]\n\n'
    response = httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=body)
    envelope = read_model_response(response)
    assert envelope['choices'][0]['message']['content'] == '{}'
    assert envelope['usage']['credit'] == 0


def test_leaked_control_marker_is_removed_only_outside_json_strings():
    raw = '{"flag":<|endoftext|>true,"evidence":"原文含\\\"<|endoftext|>\\\""}'
    body = read_model_response(stream_response(raw))
    parsed = json.loads(body['choices'][0]['message']['content'])
    assert parsed == {'flag': True, 'evidence': '原文含"<|endoftext|>"'}
    assert body['_transport']['raw_content'] == raw
    assert body['_transport']['removed_end_markers'] == 1


@pytest.mark.parametrize('finish', [None, 'error', 'content_filter'])
def test_incomplete_or_rejected_stream_is_not_accepted_even_with_valid_json(finish):
    with pytest.raises(ValueError):
        read_model_response(stream_response(finish=finish))


def test_error_event_is_not_hidden_by_done_marker():
    response = httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              text='data: {"error":{"message":"upstream failed"}}\n\ndata: [DONE]\n\n')
    with pytest.raises(ValueError, match='错误事件'):
        read_model_response(response)


def configuration(tmp_path, **overrides):
    return replace(settings, deepseek_api_key='local-test-placeholder',
                   deepseek_base_url='http://workbuddy.invalid/v1', deepseek_model='deepseek-v4.1-flash',
                   model_network_enabled=True, model_require_free=True,
                   model_cache_dir=str(tmp_path/'responses'), model_settings_path=str(tmp_path/'missing.json'),
                   **overrides)


def test_stream_result_can_be_replayed_without_network(tmp_path):
    config = configuration(tmp_path)
    async def run():
        model = DeepSeekExtractor(configuration=config)
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: stream_response()))
        try:
            result = await model._call('原文', ['notice.html'])
            assert result.project_name == '采购项目'
            assert model.total_usage['total_tokens'] == 40
        finally:
            await model.close()
        replay = DeepSeekExtractor(cache_only=True, configuration=config)
        replay.api_key = ''
        result = await replay._call('原文', ['notice.html'])
        assert result.project_name == '采购项目'
        assert replay.calls == 0 and replay.response_cache_hits == 1
    asyncio.run(run())


@pytest.mark.parametrize('credit', [None, '0.01', -1, 'NaN'])
def test_free_run_stops_on_unknown_or_nonzero_credit_and_never_retries(tmp_path, credit):
    async def run():
        model = DeepSeekExtractor(configuration=configuration(tmp_path))
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: stream_response(credit=credit)))
        try:
            for _ in range(2):
                with pytest.raises(FatalModelError, match='零积分'):
                    await model._call('原文', ['notice.html'])
            assert model.calls == 1
            assert not list((tmp_path/'responses').glob('*.json'))
        finally:
            await model.close()
    asyncio.run(run())


def test_length_finish_still_triggers_existing_chunk_split_behavior(tmp_path):
    async def run():
        model = DeepSeekExtractor(configuration=configuration(tmp_path))
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: stream_response(finish='length')))
        try:
            with pytest.raises(OutputTruncated):
                await model._call('原文', ['notice.html'])
        finally:
            await model.close()
    asyncio.run(run())


def test_loopback_gateway_remains_reachable_with_an_unusable_system_proxy(tmp_path, monkeypatch):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.setenv(name, 'http://127.0.0.1:9')
    for name in ('NO_PROXY', 'no_proxy'):
        monkeypatch.setenv(name, '')
    received = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body = json.dumps({'choices': [{'message': {'content': '{"project_name":"本机网关","lots":[]}'},
                                            'finish_reason': 'stop'}], 'usage': {'credit': 0}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def run():
        config = replace(configuration(tmp_path), deepseek_base_url=f'http://127.0.0.1:{server.server_port}/v1')
        model = DeepSeekExtractor(configuration=config)
        try:
            result = await model._call('原文', ['notice.html'])
            assert result.project_name == '本机网关'
            assert len(received) == 1
        finally:
            await model.close()
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
