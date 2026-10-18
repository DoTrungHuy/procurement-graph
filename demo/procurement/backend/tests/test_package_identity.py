import hashlib
import json
import asyncio
from dataclasses import replace

import pytest

from app import analytics
from app.database import upsert_notice
from app.models import BidExtract, ItemExtract, LotExtract, ProjectExtract
from app.package_identity import apply_reviewed_labels, resolve_package_labels


def fragments():
    return ProjectExtract(notice_uuid='n', project_name='当前项目', purchaser='采购方', processing_status='partial', lots=[
        LotExtract(awarded_amount=100, bids=[BidExtract(bidder='甲公司', is_winner=True)],
                   items=[ItemExtract(product_name='设备', supplier='甲公司', source_file='notice', line_id='TABLE:1 ROW:1')]),
        LotExtract(awarded_amount=100, bids=[BidExtract(bidder='甲公司', is_winner=True)],
                   items=[ItemExtract(product_name='配件', supplier='甲公司', source_file='quote', line_id='TABLE:1 ROW:1')])])


def test_equal_amounts_and_supplier_do_not_prove_package():
    record = fragments()
    assert resolve_package_labels(record, {'notice': '当前项目，中标人：甲公司，100元'}) == []
    assert len(record.lots) == 2 and all(not lot.lot_name for lot in record.lots)


def test_printed_count_merges_sources_and_counts_award_once():
    record = fragments()
    changed = resolve_package_labels(record, {'fee': '当前项目\n分包数量 | 1个'})
    assert len(changed) == 2 and len(record.lots) == 1
    assert record.lots[0].lot_name == '未分包'
    assert len(record.lots[0].items) == 2 and len(record.lots[0].bids) == 1
    upsert_notice(record, ['n.html'])
    actual = analytics.purchaser_partners('采购方')['winning_suppliers'][0]
    assert actual['cooperation_count'] == 1 and actual['total_amount'] == 100


@pytest.mark.parametrize('text', ['其他项目\n分包数量 | 1', '当前项目\n分包数量 | 10',
                                 '当前项目\n分包数量 | 1\n分包数量 | 2', '当前项目\n不允许分包'])
def test_count_requires_current_project_and_no_conflict(text):
    record = fragments()
    assert resolve_package_labels(record, {'source': text}) == []


def test_award_attachment_resolves_both_directions_and_preserves_distinct_lots():
    record = fragments()
    record.lots[1].bids[0].bidder = '乙公司'
    record.lots[1].items[0].supplier = '乙公司'
    text = '当前项目\n第01包：\n供应商名称：甲公司\n第02包：\n供应商名称：乙公司'
    assert len(resolve_package_labels(record, {'award.docx': text})) == 2
    assert [lot.lot_name for lot in record.lots] == ['第01包', '第02包']
    upsert_notice(record, ['n.html'])
    assert analytics.common_projects(['甲公司', '乙公司'])['summary']['project_count'] == 0


def test_same_supplier_in_two_numbered_packages_is_not_unique():
    record = fragments()
    source = '当前项目\n第01包：\n供应商名称：甲公司\n第02包：\n供应商名称：甲公司'
    assert resolve_package_labels(record, {'award': source}) == []


def test_existing_numbered_packages_prevent_incomplete_source_alias():
    record = fragments()
    record.lots += [LotExtract(lot_name=label, bids=[BidExtract(bidder='甲公司', is_winner=True)])
                    for label in ['第01包', '第02包']]
    source = '当前项目\n第01包：\n供应商名称：甲公司'
    assert resolve_package_labels(record, {'partial-award': source}) == []
    assert not record.lots[0].lot_name and not record.lots[1].lot_name


@pytest.mark.parametrize('optimized', [False, True])
def test_both_extraction_routes_use_full_source_evidence(tmp_path, monkeypatch, optimized):
    from app import extractor
    from app.config import settings
    paths = [tmp_path / 'notice.html', tmp_path / 'quote.txt']
    paths[0].write_text('项目名称：当前项目\n正文成交记录', 'utf-8')
    paths[1].write_text('项目名称：当前项目\n分包数量 | 1个\n附件成交记录', 'utf-8')
    monkeypatch.setattr(extractor, 'parse_file', lambda path: path.read_text('utf-8'))

    async def response(self, text, names, **kwargs):
        index = 0 if names[0].startswith('doc-1:') else 1
        return ProjectExtract(project_name='当前项目', purchaser='采购方', lots=[fragments().lots[index]])

    monkeypatch.setattr(extractor.DeepSeekExtractor, '_call', response)
    config = replace(settings, deepseek_api_key='fixture-only', model_network_enabled=False,
                     extract_blank_templates=optimized, extract_standard_tables=False,
                     extract_batch_attachments=False, model_cache_dir='', document_cache_dir='')
    result = asyncio.run(extractor.DeepSeekExtractor(configuration=config).extract(paths))
    assert len(result.lots) == 1 and result.lots[0].lot_name == '未分包'
    assert {item.source_file for item in result.lots[0].items} == {'doc-1:notice.html', 'doc-2:quote.txt'}


def test_conflicting_item_supplier_blocks_named_assignment():
    record = fragments()
    record.lots[0].items[0].supplier = '乙公司'
    source = '当前项目\n第01包：\n供应商名称：甲公司'
    changed = resolve_package_labels(record, {'award': source})
    assert len(changed) == 1 and record.lots[0].lot_name == ''


def review_inputs():
    record = fragments()
    record.assign_lot_keys()
    snapshot = record.model_dump_json()
    source = {'text': '当前项目\n第01包：\n供应商名称：甲公司', 'sha256': 'source-hash'}
    review = {'notice_uuid': 'n', 'snapshot_sha256': hashlib.sha256(snapshot.encode()).hexdigest(),
              'reason': '原文明确包号', 'evidence': [{'filename': 'award.docx', **source, 'quote': '第01包：\n供应商名称：甲公司'}],
              'assignments': [{'lot_key': 'unassigned:1', 'lot_name': '第01包', 'evidence': [0]},
                              {'lot_key': 'unassigned:2', 'lot_name': '第01包', 'evidence': [0]}]}
    return snapshot, review, {'award.docx': source}


def test_review_relabels_only_and_retains_completion_boundary():
    snapshot, review, sources = review_inputs()
    record, audit = apply_reviewed_labels(snapshot, review, sources)
    assert record.processing_status == 'partial'
    assert audit['before']['items'] == audit['after']['items'] == 2
    assert audit['before']['packages'] == 2 and audit['after']['packages'] == 1
    assert json.loads(snapshot)['lots'][0]['lot_name'] == ''


@pytest.mark.parametrize('change', ['snapshot', 'source', 'quote', 'label', 'single', 'duplicate', 'missing_lot'])
def test_review_refuses_stale_or_unsupported_inputs(change):
    snapshot, review, sources = review_inputs()
    if change == 'snapshot': snapshot += ' '
    elif change == 'source': sources['award.docx']['sha256'] = 'changed'
    elif change == 'quote': review['evidence'][0]['quote'] = '原文没有这句话'
    elif change == 'label': review['assignments'][0]['lot_name'] = '第09包'
    elif change == 'single': review['assignments'][0]['lot_name'] = '未分包'
    elif change == 'duplicate': review['assignments'][1]['lot_key'] = 'unassigned:1'
    elif change == 'missing_lot': review['assignments'][0]['lot_key'] = 'unassigned:99'
    with pytest.raises(ValueError):
        apply_reviewed_labels(snapshot, review, sources)
