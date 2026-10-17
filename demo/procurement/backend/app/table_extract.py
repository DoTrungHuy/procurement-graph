"""Read only fully explicit, unambiguous attachment item tables.

Returning ``None`` means the whole document must use the normal model path.
This is intentionally not a general table recognizer: no cell, amount, lot or
project fact is guessed and no unknown portion of an attachment is discarded.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .evidence import ground_evidence
from .field_text import interpret_field_text
from .models import ItemExtract, LotExtract, ProjectExtract
from .normalization import lot_identity, money_to_yuan, number_value


TABLE_EXTRACT_VERSION = 'standard-table-v1'
_ROW = re.compile(r'^\[(?P<location>(?:TABLE:\d+|SHEET:[^\]\n]+) ROW:\d+)\]\s*(?P<body>.*)$')
_NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_MONEY_UNITS = {'元': Decimal(1), '千元': Decimal(1000), '万元': Decimal(10000), '亿元': Decimal(100000000)}
_QUANTITY_UNITS = {'台', '套', '个', '件', '辆', '张', '把', '组', '批', '支', '根', '米', '平方米', '立方米',
                   '千克', '公斤', '吨', '册', '本', '盒', '包', '袋', '瓶', '卷', '块', '片', '条', '面', '盏', '对', '项'}
_TEXT_HEADERS = {
    '采购标的': 'product_name', '标的名称': 'product_name', '货物名称': 'product_name',
    '产品名称': 'product_name', '产品服务名称': 'product_name', '名称': 'product_name',
    '品目': 'category', '品目名称': 'category', '类别': 'category', '分类': 'category',
    '品牌': 'brand', '货物品牌': 'brand', '品牌(产品供应商)': 'brand',
    '型号': 'model', '规格型号': 'model', '规格/型号': 'model', '货物型号': 'model',
}
_NUMERIC_HEADERS = {'单价': 'unit_price', '货物单价': 'unit_price', '数量': 'quantity', '货物数量': 'quantity',
                    '总价': 'total_price', '货物总价': 'total_price', '合价': 'total_price'}
_REQUIRED = {'product_name', 'category', 'brand', 'model', 'unit_price', 'quantity', 'total_price'}
_TITLES = {'分项报价表', '分项报价明细表', '报价明细表', '货物清单', '采购清单', '供货清单', '产品清单',
           '货物分项报价表', '货物报价明细表', '主要标的信息', '货物明细表'}


def _compact(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def _header(cells: list[str]) -> dict[str, tuple[int, str, str]] | None:
    """Map known, nonrepeated columns; amount headers must state their unit."""
    columns: dict[str, tuple[int, str, str]] = {}
    for index, raw in enumerate(cells):
        label = _compact(raw)
        if label in _TEXT_HEADERS:
            field, unit, price_unit = _TEXT_HEADERS[label], '', ''
        elif label in {'序号', '编号', '品目号'}:
            field, unit, price_unit = 'serial', '', ''
        elif label in {'单位', '计量单位', '数量单位'}:
            field, unit, price_unit = 'quantity_unit', '', ''
        else:
            match = re.fullmatch(r'([^()]+?)(?:\(([^()]+)\))?', label)
            if not match or match[1] not in _NUMERIC_HEADERS:
                return None
            field = _NUMERIC_HEADERS[match[1]]
            unit, price_unit = match[2] or '', ''
            if field == 'quantity':
                if unit and unit not in _QUANTITY_UNITS:
                    return None
            else:
                parts = unit.split('/')
                if len(parts) > 2 or parts[0] not in _MONEY_UNITS:
                    return None
                unit = parts[0]
                price_unit = parts[1] if len(parts) == 2 else ''
                if price_unit and (field != 'unit_price' or price_unit not in _QUANTITY_UNITS):
                    return None
        if field in columns:
            return None
        columns[field] = (index, unit, price_unit)
    return columns if _REQUIRED <= columns.keys() else None


def _decimal(value: str, suffix: str = '') -> Decimal | None:
    raw = _compact(value)
    if suffix and raw.endswith(suffix):
        raw = raw[:-len(suffix)]
    if len(raw) > 30 or not re.fullmatch(_NUMBER, raw):
        return None
    try:
        number = Decimal(raw.replace(',', ''))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _item(cells: list[str], columns: dict[str, tuple[int, str, str]], location: str,
          source_file: str, lot_name: str, evidence: str) -> ItemExtract | None:
    values: dict[str, object] = {}
    for field in ('product_name', 'category', 'brand', 'model'):
        raw = cells[columns[field][0]].strip()
        # References, missing cells, combined values and cross-cell continuation
        # need interpretation. Leave that entire document to the normal path.
        observed = interpret_field_text(raw, field)
        if not raw or observed.status != 'disclosed' or re.search(r'[;；、\n]|[□☑√]|(?:同上|同前|合计|总计|小计)', raw):
            return None
        values[field] = raw
    quantity_column, quantity_unit, _ = columns['quantity']
    if 'quantity_unit' in columns:
        separate_unit = _compact(cells[columns['quantity_unit'][0]])
        if separate_unit not in _QUANTITY_UNITS or (quantity_unit and quantity_unit != separate_unit):
            return None
        quantity_unit = separate_unit
    quantity_text = _compact(cells[quantity_column])
    inline = re.fullmatch(rf'({_NUMBER})([^\d.,]+)', quantity_text)
    if inline:
        if inline[2] not in _QUANTITY_UNITS or (quantity_unit and quantity_unit != inline[2]):
            return None
        quantity_unit = inline[2]
    if not quantity_unit:
        return None
    quantity = _decimal(quantity_text, quantity_unit)
    if quantity is None or quantity <= 0:
        return None
    money = {}
    for field in ('unit_price', 'total_price'):
        column, money_unit, price_unit = columns[field]
        if price_unit and price_unit != quantity_unit:
            return None
        raw = _decimal(cells[column], money_unit)
        if raw is None:
            return None
        money[field] = raw * _MONEY_UNITS[money_unit]
    # Arithmetic is a guard, not an inferred replacement for a missing value.
    # Permit only normal rounding at the stated total-price display precision.
    total_text = _compact(cells[columns['total_price'][0]])
    total_unit = columns['total_price'][1]
    if total_text.endswith(total_unit):
        total_text = total_text[:-len(total_unit)]
    places = len(total_text.rsplit('.', 1)[1]) if '.' in total_text else 0
    tolerance = Decimal(1).scaleb(-places) * _MONEY_UNITS[total_unit] / 2
    if abs(money['unit_price'] * quantity - money['total_price']) > tolerance:
        return None
    if 'serial' in columns and not re.fullmatch(r'\d+(?:-\d+)*', _compact(cells[columns['serial'][0]])):
        return None
    values.update(unit_price=money_to_yuan(str(money['unit_price'])),
                  quantity=number_value(str(quantity)), total_price=money_to_yuan(str(money['total_price'])))
    if any(values[field] is None for field in ('unit_price', 'quantity', 'total_price')):
        return None
    return ItemExtract(**values, quantity_unit=quantity_unit, lot_name=lot_name, source='attachment',
                       source_file=source_file, source_location=location, line_id=location,
                       evidence=evidence, confidence=1.0)


def extract_standard_table(text: str, source_file: str,
                           announcement: ProjectExtract | None = None) -> ProjectExtract | None:
    """Return all explicit table rows, or decline the entire attachment.

    Only a single already-known procurement lot is eligible. Source facts
    outside the recognized seven-field tables (including bidder information,
    review facts, summary totals and project facts) require model extraction.
    ITEM-marked rows also decline: parser splitting can leave shared totals.
    """
    if announcement is None or announcement.processing_status != 'complete' or not source_file or not text.strip():
        return None
    known = announcement.package_records()
    if len(known) != 1 or not known[0].lot_name:
        return None
    lot_name = known[0].lot_name
    tables: dict[str, tuple[int, dict[str, tuple[int, str, str]]]] = {}
    counts: dict[str, int] = {}
    seen_locations: set[str] = set()
    items: list[ItemExtract] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _ROW.fullmatch(line)
        if match is None:
            title = _compact(line).rstrip(':：')
            if title in _TITLES or (title == _compact(lot_name) and lot_identity(title) == lot_identity(lot_name)):
                continue
            return None
        location = match['location']
        if location in seen_locations:
            return None
        seen_locations.add(location)
        table = location.rsplit(' ROW:', 1)[0]
        cells = [cell.strip() for cell in match['body'].split('|')]
        header = _header(cells)
        if header is not None:
            if table in tables and tables[table] != (len(cells), header):
                return None
            tables[table] = (len(cells), header)
            counts.setdefault(table, 0)
            continue
        if table not in tables or len(cells) != tables[table][0]:
            return None
        item = _item(cells, tables[table][1], location, source_file, lot_name, line)
        if item is None:
            return None
        items.append(item)
        counts[table] += 1
    if not items or any(count == 0 for count in counts.values()):
        return None
    result = ProjectExtract(lot_name=lot_name, lots=[LotExtract(lot_name=lot_name, items=items)],
                            extraction_method=TABLE_EXTRACT_VERSION)
    ground_evidence(result, text)
    if result.processing_status != 'complete' or any(not item.source_context for item in result.lots[0].items):
        return None
    return result
