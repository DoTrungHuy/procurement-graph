import pytest

from app.models import LotExtract, ProjectExtract
from app.table_extract import TABLE_EXTRACT_VERSION, extract_standard_table


HEADER = '[TABLE:1 ROW:1] 序号 | 货物名称 | 品目名称 | 品牌 | 规格型号 | 单价（元） | 数量（台） | 总价（元）'
ROW = '[TABLE:1 ROW:2] 1 | 服务器 | 服务器设备 | 华为 | ABC-123 | 1000.50 | 2 | 2001.00'


def read(text, announcement=None):
    return extract_standard_table(text, 'doc-2:报价明细.docx',
                                  announcement or ProjectExtract(lot_name='采购包1'))


def test_explicit_table_returns_source_grounded_items():
    result = read('分项报价表\n采购包1\n' + HEADER + '\n' + ROW)
    assert result.extraction_method == TABLE_EXTRACT_VERSION
    assert not result.bids
    item = result.lots[0].items[0]
    assert item.model_dump() | {} == item.model_dump()
    assert (item.product_name, item.category, item.brand, item.model) == ('服务器', '服务器设备', '华为', 'ABC-123')
    assert (item.unit_price, item.quantity, item.total_price, item.quantity_unit) == (1000.5, 2, 2001, '台')
    assert item.source == 'attachment'
    assert item.source_file == 'doc-2:报价明细.docx'
    assert item.source_location == item.line_id == 'TABLE:1 ROW:2'
    assert item.source_context
    assert item.evidence == ROW


def test_sheet_units_are_converted_without_guessing():
    text = '[SHEET:报价明细 ROW:1] 产品名称 | 类别 | 品牌 | 型号 | 单价(万元/套) | 数量 | 单位 | 总价(万元)\n' \
           '[SHEET:报价明细 ROW:2] 软件平台 | 应用软件 | 甲品牌 | V1.0 | 1.23 | 2套 | 套 | 2.46'
    item = read(text).lots[0].items[0]
    assert (item.unit_price, item.quantity, item.total_price) == (12300, 2, 24600)
    assert item.quantity_unit == '套'
    assert item.source_location == 'SHEET:报价明细 ROW:2'


@pytest.mark.parametrize('header,row', [
    (HEADER.replace('品目名称 | ', ''), ROW.replace('服务器设备 | ', '')),
    (HEADER.replace('总价（元）', '预算金额（元）'), ROW),
    (HEADER.replace('单价（元）', '单价'), ROW),
    (HEADER.replace('数量（台）', '数量'), ROW),
    (HEADER.replace('总价（元）', '总价（美元）'), ROW),
    (HEADER.replace('货物名称', '品牌'), ROW),
    (HEADER + ' | 备注', ROW + ' | 项目经理：张三'),
    (HEADER, ROW.replace(' | 华为 | ', ' | 详见投标文件 | ')),
    (HEADER, ROW.replace(' | ABC-123 | ', ' | 同上 | ')),
    (HEADER, ROW.replace('服务器 | ', '服务器；交换机 | ')),
    (HEADER, ROW.replace('1000.50 | 2', '1000.50；200 | 2')),
    (HEADER, ROW.replace('2001.00', '2000.00')),
    (HEADER, ROW.replace('1000.50 | 2', '1万元 | 2')),
    (HEADER, ROW.replace(' | 2 | ', ' | 2套 | ')),
    (HEADER, ROW.replace(' | 2 | ', ' | -2 | ')),
    (HEADER, ROW.replace('1000.50', '1000至2000')),
    (HEADER, ROW.replace('2001.00', '2.001e3')),
    (HEADER, ROW.replace('服务器设备 | ', '')),
])
def test_ambiguous_or_incomplete_document_falls_back(header, row):
    assert read(header + '\n' + row) is None


@pytest.mark.parametrize('extra', [
    '供应商名称：华为技术有限公司', '项目编号：TEST001', '综合得分：98.5', '中标总金额：2001元',
    '[TABLE:2 ROW:1] 中标人 | 得分\n[TABLE:2 ROW:2] 公司甲 | 99',
    '[TABLE:1 ROW:3] 合计 | | | | | | | 2001.00',
    '采购包2', '[解析警告] 合并单元格无法对应',
])
def test_never_discards_facts_or_unknown_rows(extra):
    assert read(HEADER + '\n' + ROW + '\n' + extra) is None


def test_merged_item_splits_do_not_assume_shared_totals():
    assert read(HEADER + '\n' + ROW.replace('ROW:2]', 'ROW:2 ITEM:1]')) is None
    assert read(HEADER + '\n' + ROW + '\n' + ROW) is None


def test_requires_single_known_lot_and_nonempty_source():
    text = HEADER + '\n' + ROW
    assert extract_standard_table(text, 'items.docx', None) is None
    assert extract_standard_table(text, 'items.docx', ProjectExtract()) is None
    two = ProjectExtract(lots=[LotExtract(lot_name='采购包1'), LotExtract(lot_name='采购包2')])
    assert extract_standard_table(text, 'items.docx', two) is None
    assert extract_standard_table(text, '', ProjectExtract(lot_name='采购包1')) is None


def test_header_only_and_orphan_tables_fall_back():
    assert read(HEADER) is None
    assert read(HEADER + '\n' + ROW + '\n' + HEADER.replace('TABLE:1', 'TABLE:2')) is None
    assert read(ROW) is None


def test_repeated_header_and_separate_explicit_tables_are_safe():
    text = '\n'.join([HEADER, ROW, HEADER.replace('ROW:1', 'ROW:3'),
                      ROW.replace('ROW:2', 'ROW:4').replace('ABC-123', 'ABC-124'),
                      HEADER.replace('TABLE:1', 'TABLE:2'), ROW.replace('TABLE:1', 'TABLE:2')])
    assert len(read(text).lots[0].items) == 3


def test_explicit_zero_price_is_not_treated_as_missing():
    row = ROW.replace('1000.50', '0').replace('2001.00', '0')
    item = read(HEADER + '\n' + row).lots[0].items[0]
    assert item.unit_price == item.total_price == 0


def test_rounding_tolerance_uses_declared_total_precision():
    text = HEADER + '\n' + ROW.replace('1000.50', '1.234').replace('2001.00', '2.47')
    assert read(text).lots[0].items[0].total_price == 2.47
    assert read(text.replace('2.47', '2.46')) is None
