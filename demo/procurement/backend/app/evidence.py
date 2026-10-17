"""Ground model row identities and package aliases in the actual input text.

This module locates already extracted facts. It never generates product fields
or uses a price to decide whether two source rows are the same item.
"""
from __future__ import annotations

import re

from .models import ProjectExtract
from .normalization import dedupe_key, lot_identity, normalize_org
from .item_hierarchy import make_context
from .field_text import BUSINESS_TEXT_POLICY, TEXT_STATUS_NOTES, interpret_field_text

LOCATION = re.compile(r'\[?((?:TABLE:\s*\d+\s+ROW:\s*\d+|SHEET:[^\]\n]+?\s+ROW:\s*\d+)(?:\s+ITEM:\s*\d+)?|PAGE:\s*\d+\s+LINE:\s*\d+|IMAGE:\s*\d+\s+LINE:\s*\d+)\]?', re.I)
PACKAGE = re.compile(r'^(?:(?:合同包|采购包|包组|标包|标段|包)\s*[（(]?[0-9A-Za-z一二三四五六七八九十百零〇两]+|第?[0-9A-Za-z一二三四五六七八九十百零〇两]+(?:标段|标包|包))')
_HEADERS = {'品牌': 'brand', '货物品牌': 'brand', '品牌(产品供应商)': 'brand',
            '规格型号': 'model', '规格/型号': 'model', '货物型号': 'model',
            '品目': 'category', '品目名称': 'category', '采购标的': 'product_name',
            '货物名称': 'product_name', '产品服务名称': 'product_name'}


def canonical_location(value: str) -> str:
    match = LOCATION.search(value or '')
    return re.sub(r':\s+', ':', re.sub(r'\s+', ' ', match.group(1))).strip() if match else ''


def ground_evidence(result: ProjectExtract, text: str) -> None:
    rows = {}
    # PDF/OCR extraction often emits one cell (or character) per line. A
    # printed business-code line delimits the source row until the next code.
    spans = []
    current_span = None
    labels = []
    package = ''
    headers = {}
    source_fields = {}
    for line in text.splitlines():
        line = line.strip()
        location = canonical_location(line)
        if location:
            rows[location] = (line, package)
            body = LOCATION.sub('',line).strip()
            table = location.split(' ROW:', 1)[0] if location.startswith(('TABLE:', 'SHEET:')) else ''
            cells = [cell.strip() for cell in body.split('|')]
            names = [re.sub(r'\s+', '', cell).replace('（', '(').replace('）', ')') for cell in cells]
            identified = {index: _HEADERS[name] for index, name in enumerate(names) if name in _HEADERS}
            if table and len(identified) >= 2 and len(set(identified.values())) == len(identified):
                headers[table] = (len(cells), identified)
            elif table in headers and len(cells) == headers[table][0]:
                source_fields[location] = {field: cells[index] for index, field in headers[table][1].items()}
            code = re.match(r'^(\d+(?:-\d+)+)(?=\s|\||$)',body)
            if location.startswith(('PAGE:', 'IMAGE:')) and code:
                current_span = {'code':code.group(1),'locations':[],'text':[]}
                spans.append(current_span)
            if current_span is not None and location.startswith(('PAGE:', 'IMAGE:')):
                current_span['locations'].append(location)
                current_span['text'].append(body)
        elif PACKAGE.match(line) and len(line) < 320:
            candidate = line.rstrip(':：').strip()
            if lot_identity(candidate).startswith('number:'):
                package = candidate
                if candidate not in labels:
                    labels.append(candidate)
        elif re.match(r'^[一二三四五六七八九十]+[、．.]', line):
            package = ''

    def matches(item, row):
        key = dedupe_key(row)
        identifiers = [dedupe_key(item.product_name), dedupe_key(item.model)]
        evidence = dedupe_key(item.evidence)
        return any(len(value) > 1 and value in key for value in identifiers) or (len(evidence) > 10 and evidence in key)

    result.lots = result.package_records()
    for lot in result.lots:
        row_packages = set()
        for item in lot.items:
            # Model-supplied context is never accepted as a verified excerpt.
            item.source_context = {}
            item.field_observations = {}
            proposed = canonical_location(item.line_id)
            if proposed not in rows or not matches(item, rows[proposed][0]):
                candidates = [location for location, (line, _) in rows.items() if matches(item, line)]
                proposed = candidates[0] if len(candidates) == 1 else ''
            if proposed:
                item.line_id = proposed
                item.source_location = proposed
                if rows[proposed][1]:
                    row_packages.add(rows[proposed][1])
            else:
                if item.line_id:
                    result.warnings.append('标的行号无法在输入中唯一核验，保留记录并取消按该行号去重')
                item.source_location = item.source_location or item.line_id
                item.line_id = ''
            item.business_line_id = ''
            # Only a code printed on a grounded source row is acceptable. A
            # business code such as 1-1 may repeat for several different goods.
            row = LOCATION.sub('', rows[proposed][0]).strip() if proposed else ''
            code = re.match(r'(\d+(?:-\d+)+)(?=\s|\||$)', row)
            if code:
                item.business_line_id = code.group(1)
            elif item.product_name and item.model:
                # Require both disclosed identity fields within a single
                # delimited row. Do not carry the code from a nearby row.
                candidates = [span for span in spans if
                    dedupe_key(item.product_name) in dedupe_key(''.join(span['text'])) and
                    dedupe_key(item.model) in dedupe_key(''.join(span['text'])) and
                    (not proposed or proposed in span['locations'])]
                if len(candidates) == 1:
                    span = candidates[0]
                    item.business_line_id = span['code']
                    if not proposed:
                        item.line_id = span['locations'][0]
                        item.source_location = span['locations'][0]+' to '+span['locations'][-1]
            if proposed:
                item.source_context = make_context(item.source_file, item.source_location, rows[proposed][0])
                if item.source_context:
                    for field, raw in source_fields.get(proposed, {}).items():
                        observed = interpret_field_text(raw, field)
                        if observed.status in {'reference_only', 'not_disclosed', 'mixed_reference', 'disclosed_with_reference'}:
                            item.field_observations[field] = {
                                'raw': raw, 'status': observed.status, 'interpreted_value': observed.value,
                                'reason': TEXT_STATUS_NOTES[observed.status],
                                'source_file': item.source_file, 'source_location': proposed,
                                'policy': BUSINESS_TEXT_POLICY,
                            }
                            if observed.status == 'mixed_reference':
                                result.processing_status = 'partial'
                                result.warnings.append(f'{proposed} {field}含无法可靠分离的指引与字段文字，保留原文待核验')
            elif item.business_line_id and item.line_id:
                item.source_context = make_context(item.source_file, item.source_location, '\n'.join(span['text']))
        aliases = [label for label in labels
            if label.split('(', 1)[-1].split('（', 1)[-1].rstrip(')）') == lot.lot_name]
        if len({lot_identity(label) for label in row_packages}) == 1:
            candidate = max(row_packages, key=len)
        elif len(aliases) == 1:
            candidate = aliases[0]
        else:
            candidate = ''
        if candidate and lot_identity(candidate) != lot_identity(lot.lot_name):
            if lot_identity(lot.lot_name).startswith('number:'):
                result.processing_status = 'partial'
                result.warnings.append('模型包编号与原文标的行上下文冲突，需复核')
            else:
                lot.lot_name = candidate
        elif candidate and len(candidate) > len(lot.lot_name):
            lot.lot_name = candidate
        if len({lot_identity(label) for label in row_packages}) > 1:
            result.processing_status = 'partial'
            result.warnings.append('同一模型包包含多个原文包的标的，需复核包归属')


def align_attachment_packages(result: ProjectExtract, announcement: ProjectExtract, filename: str, text: str) -> None:
    """Use explicit file references and unique source aliases, never the first lot."""
    known = {lot_identity(l.lot_name):l for l in announcement.package_records() if lot_identity(l.lot_name).startswith('number:')}
    if not known:
        return
    explicit = re.findall(r'(?:合同包|采购包|标包|包组)\s*([0-9]+)|第\s*([0-9]+)\s*包|包号\s*[：:]\s*([0-9]+)', filename+'\n'+text[:2500])
    referenced = {'number:'+str(int(next(value for value in match if value))) for match in explicit}
    referenced &= set(known)
    result.lots = result.package_records()
    for lot in result.lots:
        key = lot_identity(lot.lot_name)
        if key in known:
            lot.lot_name = known[key].lot_name
            continue
        aliases = [key for key,value in known.items()
            if value.lot_name.split('(',1)[-1].split('（',1)[-1].rstrip(')）') == lot.lot_name]
        candidates = set(aliases) or referenced
        if len(candidates) == 1 and not key.startswith('number:'):
            lot.lot_name = known[next(iter(candidates))].lot_name
            result.warnings.append('附件采购包按原文包号引用/公告内唯一同名描述对齐；原始文件保留供追溯')
