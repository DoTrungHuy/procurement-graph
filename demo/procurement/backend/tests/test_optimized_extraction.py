import asyncio
import json
import hashlib
from collections import Counter
from dataclasses import replace

import httpx
import pytest

from app import extractor
from app.config import settings
from app.extraction_plan import _batch_grounded, _ground
from app.models import ItemExtract, LotExtract, ProjectExtract


HEADER = '[TABLE:1 ROW:1] 序号 | 货物名称 | 品目名称 | 品牌 | 规格型号 | 单价（元） | 数量（台） | 总价（元）'
ROW = '[TABLE:1 ROW:2] 1 | 服务器 | 服务器设备 | 华为 | ABC-123 | 100 | 1 | 100'


def fixture(tmp_path, monkeypatch, texts):
    configuration = replace(settings, deepseek_api_key='fixture-only', model_network_enabled=False,
        model_cache_dir='', document_cache_dir=str(tmp_path / 'pieces'),
        extract_blank_templates=True, extract_standard_tables=True, extract_batch_attachments=True)
    files = [tmp_path / 'notice.html'] + [tmp_path / f'附件{index}.txt' for index in range(len(texts))]
    for path, text in zip(files, ['项目公告\n采购包1'] + texts):
        path.write_text(text, encoding='utf-8')
    monkeypatch.setattr(extractor, 'parse_file', lambda path: path.read_text('utf-8'))
    return configuration, files


def item_result(name='服务器', model='ABC-123', price=100, quantity=1):
    return ProjectExtract(lots=[LotExtract(lot_name='采购包1', items=[ItemExtract(
        product_name=name, brand='华为', model=model, unit_price=price, quantity=quantity,
        total_price=price * quantity, line_id='TABLE:1 ROW:2')])])


def install_model(monkeypatch, single=None, batch=None):
    calls = Counter()

    async def call(self, text, names, *, decoder=None, **kwargs):
        if decoder is not None:
            calls['batch'] += 1
            data = batch(names) if batch else {'documents': [
                {'file_id': name, 'extraction': item_result().model_dump()} for name in names]}
            return decoder(json.dumps(data, ensure_ascii=False))
        calls[names[0]] += 1
        if names[0].startswith('doc-1:'):
            return ProjectExtract(project_name='项目公告', lot_name='采购包1')
        return single(names[0]) if single else item_result()

    monkeypatch.setattr(extractor.DeepSeekExtractor, '_call', call)
    return calls


def run(configuration, files):
    model = extractor.DeepSeekExtractor(configuration=configuration)
    return asyncio.run(model.extract(files)), model


def test_all_routes_preserve_files_and_grounding(tmp_path, monkeypatch):
    # Explicit table uses columns; incomplete headers stay with the model.
    partial = ROW + '\n数量、单价均见本行'
    configuration, files = fixture(tmp_path, monkeypatch, ['其他材料', HEADER + '\n' + ROW, partial, partial + '。'])
    calls = install_model(monkeypatch)
    result, model = run(configuration, files)
    assert result.processing_status == 'complete'
    counts = result.model_usage['optimization_counts']
    assert counts['blank_templates'] == counts['standard_tables'] == counts['batch_groups'] == 1
    assert counts['batch_documents'] == 2
    assert calls == {'doc-1:notice.html': 1, 'batch': 1}
    assert len(result.model_usage['documents']) == len(files)
    assert all(path.exists() for path in files)
    items = [item for lot in result.package_records() for item in lot.items]
    assert len(items) == 3
    assert all(item.source_context and item.line_id == 'TABLE:1 ROW:2' for item in items)
    assert {item.source_file for item in items} == {'doc-3:附件1.txt', 'doc-4:附件2.txt', 'doc-5:附件3.txt'}
    warm, _ = run(configuration, files)
    assert calls == {'doc-1:notice.html': 1, 'batch': 1}
    assert warm.model_usage['document_cache_hits'] == 3
    assert warm.model_dump(exclude={'model_usage'}) == result.model_dump(exclude={'model_usage'})


@pytest.mark.parametrize('text', ['其他材料\n供应商：甲公司', '其他材料\n报价100元',
    HEADER + '\n' + ROW + '\n投标人甲公司报价100元', '[PARSE_WARNING]不可解析\n其他材料'])
def test_filled_mixed_and_warning_sources_are_not_skipped(tmp_path, monkeypatch, text):
    configuration, files = fixture(tmp_path, monkeypatch, [text])
    calls = install_model(monkeypatch, single=lambda name: ProjectExtract(no_target_reason='原文保留'))
    result, _ = run(configuration, files)
    assert calls['doc-2:附件0.txt'] == 1
    assert not result.model_usage['documents'][1]['skipped_reason']
    assert not result.model_usage['optimization_counts'].get('standard_tables')


@pytest.mark.parametrize('failure', ['empty', 'missing', 'foreign-source', 'wrong-number'])
def test_rejected_batch_falls_back_to_every_single_source(tmp_path, monkeypatch, failure):
    configuration, files = fixture(tmp_path, monkeypatch, ['数量、单价\n' + ROW, '数量、单价\n' + ROW + '。'])

    def bad(names):
        entries = [{'file_id': name, 'extraction': item_result().model_dump()} for name in names]
        if failure == 'empty':
            entries[0]['extraction'] = {'items': [], 'bids': [], 'no_target_reason': '无标的'}
        elif failure == 'missing':
            entries.pop()
        elif failure == 'foreign-source':
            entries[0]['extraction']['lots'][0]['items'][0]['source_file'] = names[1]
        else:
            entries[0]['extraction'] = item_result(price=200, quantity=2).model_dump()
        return {'documents': entries}

    calls = install_model(monkeypatch, batch=bad)
    result, model = run(configuration, files)
    assert result.processing_status == 'complete'
    assert calls == {'doc-1:notice.html': 1, 'batch': 1, 'doc-2:附件0.txt': 1, 'doc-3:附件1.txt': 1}
    assert model.optimization_counts['batch_fallbacks'] == 1
    assert model.optimization_counts['batch_documents'] == 0
    assert model.batch_disabled_reason


@pytest.mark.parametrize('reference', ['采购包2', '包(2)', '包号：2', '第二包'])
def test_explicit_other_lot_cannot_join_batch(tmp_path, monkeypatch, reference):
    configuration, files = fixture(tmp_path, monkeypatch, [reference + '\n数量、单价\n' + ROW, '数量、单价\n' + ROW])
    calls = install_model(monkeypatch)
    run(configuration, files)
    assert not calls['batch']
    assert calls['doc-2:附件0.txt'] == calls['doc-3:附件1.txt'] == 1


@pytest.mark.parametrize('text', ['货物名称|品牌|型号|数量|金额\n服务器|华为|ABC|2|200', '投标人|报价\n甲公司|100'])
def test_empty_batch_cannot_prove_absence(text):
    assert not _batch_grounded(ProjectExtract(items=[]), {'text': text})


def test_foreign_numbers_are_rejected_after_grounding(tmp_path):
    doc = {'text': ROW, 'source': 'attachment', 'label': 'a.txt', 'path': tmp_path / 'a.txt'}
    result = item_result(price=200, quantity=2)
    _ground(result, doc, None)
    assert result.lots[0].items[0].source_context
    assert not _batch_grounded(result, doc)


def test_other_document_project_facts_cannot_leak_into_a_valid_item(tmp_path):
    doc = {'text': ROW, 'source': 'attachment', 'label': 'a.txt', 'path': tmp_path / 'a.txt'}
    result = item_result()
    result.project_name = '另一个文件的项目'
    _ground(result, doc, None)
    assert not _batch_grounded(result, doc)


def test_missing_model_keeps_existing_offline_fallback(tmp_path, monkeypatch):
    configuration, files = fixture(tmp_path, monkeypatch, ['其他材料'])
    configuration = replace(configuration, deepseek_api_key='')
    monkeypatch.setattr(extractor.DeepSeekExtractor, '_call', lambda *args, **kwargs: pytest.fail('No model expected'))
    result, _ = run(configuration, files)
    assert result.processing_status == 'fallback'
    assert result.model_usage['requests'] == 0


def test_batch_nonzero_credit_stops_without_single_fallback(tmp_path, monkeypatch):
    configuration, files = fixture(tmp_path, monkeypatch, ['数量、单价\n' + ROW, '数量、单价\n' + ROW + '。'])
    configuration = replace(configuration, model_network_enabled=True, model_require_free=True)
    requested = []

    async def handler(request):
        payload = json.loads(request.content)
        batch = 'small-attachments-v1' in payload['messages'][0]['content']
        requested.append(batch)
        return httpx.Response(200, json={'usage': {'credit': 1 if batch else 0},
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"lot_name":"采购包1"}'}}]})

    async def check():
        model = extractor.DeepSeekExtractor(configuration=configuration)
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(extractor.FatalModelError, match='零积分'):
                await model.extract(files)
            assert model.calls == 2
            assert model.optimization_counts['batch_fallbacks'] == 0
        finally:
            await model.close()

    asyncio.run(check())
    assert requested == [False, True]


@pytest.mark.parametrize('partition', ['current', 'legacy'])
def test_auto_replay_selects_an_exact_saved_partition(tmp_path, partition):
    cache = tmp_path / 'read-only-source'
    cache.mkdir()
    configuration = replace(settings, deepseek_base_url='http://fixture.invalid/v1', model_cache_dir=str(tmp_path / 'new'),
        model_cache_read_dirs=(str(cache),), model_replay_chunking='auto', max_llm_chars=1024)
    model = extractor.DeepSeekExtractor(cache_only=True, configuration=configuration)
    text = '\n'.join(f'{index}原始数据内容' for index in range(200))
    prefix, legacy = '新上下文' * 20, '旧上下文' * 70
    names, label = ['a.txt', 'b.txt'], 'b.txt'
    chosen = prefix if partition == 'current' else legacy
    for part in extractor.chunk_text(text, max(128, configuration.max_llm_chars - len(chosen))):
        payload = model._payload(prefix + part, [label])
        key = hashlib.sha256((configuration.deepseek_base_url + json.dumps(payload, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
        (cache / (key + '.json')).write_text('{}', encoding='utf-8')
    assert model._chunk_prefix(text, prefix, legacy, names, label) == chosen
    assert model._chunk_prefix('changed ' + text, prefix, legacy, names, label) == prefix
    assert not (tmp_path / 'new').exists()


def test_read_only_response_layer_is_never_written(tmp_path):
    cache = tmp_path / 'old'
    cache.mkdir()
    configuration = replace(settings, deepseek_api_key='', model_cache_dir=str(tmp_path / 'new'),
        model_cache_read_dirs=(str(cache),))
    model = extractor.DeepSeekExtractor(cache_only=True, configuration=configuration)
    payload = model._payload('source', ['a.txt'])
    key = hashlib.sha256((configuration.deepseek_base_url + json.dumps(payload, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
    path = cache / (key + '.json')
    path.write_text(json.dumps({'choices': [{'message': {'content': '{"project_name":"采购项目"}'}}]}), encoding='utf-8')
    before = path.read_bytes()
    result = asyncio.run(model._call('source', ['a.txt']))
    assert result.project_name == '采购项目'
    assert path.read_bytes() == before
    assert not (tmp_path / 'new').exists()
    assert model.calls == 0 and model.response_cache_hits == 1
