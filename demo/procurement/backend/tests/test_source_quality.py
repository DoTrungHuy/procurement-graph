import io

import pytest
from openpyxl import load_workbook

from app import analytics
from app.database import connect, one, rows, upsert_notice
from app.document_policy import document_role
from app.exports import export_records, workbook_bytes
from app.extractor import merge_results, validate_result
from app.models import BidExtract, ItemExtract, LotExtract, ProjectExtract
from app.source_facts import money, read_notice_facts, reconcile_notice_facts
from app.bid_identity import verify_bid_identities

NOTICE = '''三、采购结果
合同包1(设备):
[TABLE:2 ROW:1] 供应商名称 | 供应商地址 | 中标（成交）金额
[TABLE:2 ROW:2] 甲公司 | 地址 | 1,200.00元
四、主要标的信息
合同包1(设备):
货物类（甲公司）
[TABLE:3 ROW:1] 品目号 | 品目名称 | 采购标的 | 品牌 | 规格型号 | 数量（单位） | 单价(元) | 总价(元)
[TABLE:3 ROW:2] 1-1 | 设备 | 服务器 | 品牌甲 | S1 | 2.0000(台) | 500 | 1000
[TABLE:3 ROW:3] 1-2 | 设备 | 配件 | 品牌乙 | X1 | 1(套) | 200 | 200
八、其他补充事宜
合同包1(设备):
[TABLE:5 ROW:1] 供应商 | 评审价格 | 最终报价
[TABLE:5 ROW:2] 甲公司 | 1080元 | 1200元
'''


def record():
    return ProjectExtract(notice_uuid='n', purchaser='买方', lots=[LotExtract(lot_name='合同包1(设备)',
        bids=[BidExtract(bidder='甲公司',is_winner=True,bid_amount=1000)],
        items=[ItemExtract(product_name='服务器',brand='错误品牌',model='S1',source_file='doc-1:n.html',
                           line_id='TABLE:3 ROW:2')])])


def test_money_conflicts_do_not_select_first_or_later_value():
    pieces = [ProjectExtract(lot_name='包1',awarded_amount=v,
        bids=[BidExtract(bidder='甲',is_winner=True,bid_amount=v)]) for v in [100,200,100]]
    result = merge_results(pieces)
    assert result.awarded_amount is None and result.lots[0].awarded_amount is None
    assert result.field_conflicts['awarded_amount'] == [100,200]
    assert result.lots[0].bids[0].bid_amount is None
    assert result.lots[0].bids[0].field_conflicts['bid_amount'] == [100,200]
    validate_result(result)
    assert result.processing_status == 'partial'
    upsert_notice(ProjectExtract(**{**result.model_dump(),'notice_uuid':'n'}),['n.html'])
    assert one('SELECT awarded_amount FROM projects')['awarded_amount'] is None


def test_distinct_evidence_is_provenance_not_a_business_conflict():
    pieces = [ProjectExtract(lot_name='包1',bids=[BidExtract(bidder='甲',is_winner=True,
              source_file=file,evidence=quote)]) for file,quote in [('a','原文A'),('b','原文B')]]
    result = merge_results(pieces)
    bid = result.lots[0].bids[0]
    assert bid.source_file == 'a' and bid.related_sources == [{'source_file':'b','evidence':'原文B'}]
    assert not any('evidence' in w or 'source_file' in w for w in result.warnings)
    result.notice_uuid = 'n'
    upsert_notice(result,['n.html'])
    assert '原文B' in one('SELECT related_sources FROM bids')['related_sources']


def test_legacy_export_restores_all_preserved_source_references():
    result=record()
    result.lots[0].items[0].related_sources=[{'source_file':'quote.pdf','evidence':'明细原文'}]
    result.lots[0].bids[0].related_sources=[{'source_file':'score.pdf','evidence':'评审原文'}]
    upsert_notice(result,['n.html'])
    with connect() as connection:
        connection.execute('DELETE FROM notices')  # Exercise legacy rows without a JSON snapshot.
    records,_=export_records('default')
    assert records[0].lots[0].items[0].related_sources[0]['source_file']=='quote.pdf'
    assert records[0].lots[0].bids[0].related_sources[0]['source_file']=='score.pdf'


def test_primary_cells_repair_money_brand_units_and_missing_rows():
    result = record()
    audit = reconcile_notice_facts(result,NOTICE,'doc-1:n.html')
    assert result.lots[0].awarded_amount == 1200
    assert result.lots[0].bids[0].bid_amount == 1200  # final price, not evaluation price
    assert len(result.lots[0].items) == 2
    assert result.lots[0].items[0].brand == '品牌甲'
    assert result.lots[0].items[0].quantity_unit == '台'
    assert any(row['action']=='missing_source_row' for row in audit)
    assert reconcile_notice_facts(result,NOTICE,'doc-1:n.html') == []


def test_conflicting_primary_amounts_stay_unresolved_instead_of_last_row_winning():
    result = record()
    text = NOTICE.replace('[TABLE:2 ROW:2] 甲公司 | 地址 | 1,200.00元',
                          '[TABLE:2 ROW:2] 甲公司 | 地址 | 1,200.00元\n[TABLE:2 ROW:3] 甲公司 | 地址 | 1300元')
    text += '\n[TABLE:5 ROW:3] 甲公司 | 1170元 | 1300元'
    text = '三、中标信息\n总中标金额：1200元\n总中标金额：1300元\n' + text
    reconcile_notice_facts(result,text,'doc-1:n.html')
    assert result.awarded_amount is None and result.field_conflicts['awarded_amount']==[1200,1300]
    assert result.lots[0].awarded_amount is None and result.lots[0].field_conflicts['awarded_amount']==[1200,1300]
    bid=result.lots[0].bids[0]
    assert bid.bid_amount is None and set(bid.field_conflicts['bid_amount'])=={1000,1200,1300}
    assert reconcile_notice_facts(result,text,'doc-1:n.html') == []
    validate_result(result)
    assert result.processing_status=='partial'


def test_repeated_goods_rows_are_not_overwritten_by_name_fallback():
    result = record()
    text = NOTICE.replace('配件 | 品牌乙 | X1', '服务器 | 品牌甲 | S1')
    reconcile_notice_facts(result,text,'doc-1:n.html')
    assert len(result.lots[0].items) == 2
    assert {i.line_id for i in result.lots[0].items} == {'TABLE:3 ROW:2','TABLE:3 ROW:3'}


def test_grounded_core_marker_repairs_name_instead_of_adding_duplicate():
    result=record()
    text=NOTICE.replace('服务器 | 品牌甲','△服务器 | 品牌甲')
    audit=reconcile_notice_facts(result,text,'doc-1:n.html')
    assert result.lots[0].items[0].product_name=='△服务器'
    assert sum(a['action']=='missing_source_row' for a in audit)==1  # Only the genuinely absent accessory.


def test_known_package_uses_one_declared_supplier_and_matching_product_group():
    result=ProjectExtract(lots=[LotExtract(lot_name='13包',
        items=[ItemExtract(product_name='型号甲 长名称实验设备',supplier='')],
        bids=[BidExtract(bidder='甲公司',is_winner=False)])])
    text='三、中标信息\n供应商名称：甲公司\n中标金额：100万元\n四、主要标的信息\n[TABLE:2 ROW:1] 供应商 | 商品名称 | 规格型号 | 数量 | 单价 | 总价\n[TABLE:2 ROW:2] 甲公司 | 长名称实验设备 | 型号甲 | 1 | 100万元 | 100万元'
    reconcile_notice_facts(result,text,'notice.html')
    assert result.lots[0].awarded_amount==1000000
    assert result.lots[0].bids[0].is_winner


def test_printed_package_is_independent_of_non_currency_award():
    result=record();result.lots[0].lot_name=''
    reconcile_notice_facts(result,NOTICE.replace('1,200.00元','90%'),'doc-1:n.html')
    assert result.lots[0].lot_name=='合同包1(设备)'
    assert result.lots[0].awarded_amount is None


@pytest.mark.parametrize('raw,header', [('90%','中标金额'),('0.9','费率'),('1.2','最终报价'),('100元','评审价格')])
def test_non_currency_or_evaluation_values_not_interpreted_as_money(raw,header):
    assert money(raw,header) is None


def test_explicit_wan_unit_and_notice_total():
    text='[TABLE:1 ROW:1] 总中标金额 | ￥12.000000 万元（人民币）\n三、中标（成交）信息\n[TABLE:2 ROW:1] 包号 | 中标供应商名称 | 中标金额(万元)\n[TABLE:2 ROW:2] 包A | 甲公司 | 12'
    facts=read_notice_facts(text,'notice')
    assert facts['totals'][0]['value']==120000 and facts['awards'][0]['value']==120000


@pytest.mark.parametrize('overview', ['0.000000','0.120000'])
def test_overview_zero_or_rounding_does_not_overwrite_precise_result(overview):
    result=record();result.awarded_amount=1200.3
    text=f'[TABLE:1 ROW:1] 总中标金额 | ￥{overview} 万元（人民币）\n'+NOTICE.replace('1,200.00元','1,200.30元')
    reconcile_notice_facts(result,text,'doc-1:n.html')
    assert result.awarded_amount==1200.3 and result.lots[0].awarded_amount==1200.3


def test_source_award_cannot_cross_ambiguous_packages():
    result=record();result.lots.append(result.lots[0].model_copy(deep=True));result.lots[1].lot_name='包2'
    text=NOTICE.replace('合同包1(设备):','')
    assert reconcile_notice_facts(result,text,'doc-1:n.html')==[]
    assert all(l.awarded_amount is None for l in result.lots)


@pytest.mark.parametrize('name,text', [
    ('本国-甲公司.pdf','关于符合本国产品标准的声明函'),
    ('中小企声明函.pdf','中小企业声明函'),
    ('扫描.pdf','[PAGE:1 LINE:1] 中小企业声明函'),
    ('评审劳务报酬支付表.pdf','项目名称 分包数量1'),
    ('业绩（平台）.pdf','旧项目合同金额1000万元')])
def test_supporting_sources_cannot_create_current_transaction_records(name,text):
    assert document_role(name,text)[1]


@pytest.mark.parametrize('name', ['本国-甲公司.pdf','业绩及报价附件.pdf','劳务报酬及报价附件.pdf'])
def test_mixed_quote_sources_remain_available(name):
    assert not document_role(name,'中小企业声明函\n分项报价表 品名 单价 数量 总价')[1]


def test_combined_declaration_and_review_is_not_discarded():
    assert not document_role('中小企业声明及审查表.pdf','中小企业声明函\n供应商 综合得分 资格性审查')[1]


def test_uncertain_source_duplicates_keep_export_prices_but_not_brand_sum():
    result=record();result.lots[0].items=[ItemExtract(product_name='设备',brand='品牌甲',supplier='甲公司',
         source_file=file,total_price=100,source='attachment') for file in ['a.pdf','b.pdf']]
    validate_result(result)
    assert len(result.lots[0].items)==2 and all(not i.aggregation_eligible for i in result.lots[0].items)
    upsert_notice(result,['n.html'])
    brand=analytics.purchaser_partners('买方')['product_suppliers'][0]
    assert brand['total_amount'] is None and brand['unconfirmed_amount_item_count']==2
    wb=load_workbook(io.BytesIO(workbook_bytes([result])))
    assert wb['标的表']['J2'].value==0.01 and wb['标的表']['J3'].value==0.01
    assert all(not i['aggregation_eligible'] for i in rows('SELECT * FROM items'))


def test_anonymous_ids_are_retained_but_cannot_duplicate_real_competitors():
    bids=[BidExtract(bidder='GC-HGX260129-20260506VQOON',is_winner=True,score=97),BidExtract(bidder='甲公司',is_winner=True)]
    text='[TABLE:1 ROW:1] 投标人编号 | 得分 | 名次\n[TABLE:1 ROW:2] GC-HGX260129-20260506VQOON | 97 | 1'
    assert len(verify_bid_identities(bids,text))==1
    assert len(bids)==2 and not bids[0].eligible and bids[0].role=='其他' and bids[1].eligible
    result=ProjectExtract(notice_uuid='n',purchaser='买方',lots=[LotExtract(lot_name='包1',bids=bids)])
    upsert_notice(result,['n.html'])
    assert one('SELECT COUNT(*) n FROM bids')['n']==2
    assert len(analytics.purchaser_bidders('买方')['top_bidders'])==1
    assert not any(node['name'].startswith('GC-') for node in analytics.relationship_overview()['nodes'])


def test_partial_parse_cannot_prove_whole_file_is_supporting_only():
    assert not document_role('中小企业声明及报价.pdf','中小企业声明函\n[PARSE_WARNING] PDF page unavailable')[1]


def test_explicit_descriptive_package_name_is_not_replaced_by_unassigned():
    result=record();result.lots[0].lot_name=''
    reconcile_notice_facts(result,'标段名称：视频图像实战应用平台\n三、中标信息','notice.html')
    assert result.lots[0].lot_name=='视频图像实战应用平台'
