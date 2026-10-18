"""Read explicitly labelled current-notice tables; never calculate source facts.

Only the result section establishes awards. Tender budgets, agent fees,
evaluation prices, and historical contracts are not final awards or bids.
Unmapped packages and non-currency quotations remain unresolved.
"""
from __future__ import annotations

import re
import unicodedata

from .evidence import ground_evidence
from .field_text import interpret_field_text
from .models import BidExtract, ItemExtract, LotExtract, ProjectExtract
from .normalization import dedupe_key, lot_identity, money_to_yuan, normalize_org, number_value

ROW = re.compile(r'^\[(?P<location>TABLE:\d+ ROW:\d+(?: ITEM:\d+)?)\]\s*(?P<body>.*)$')
PACKAGE = re.compile(r'^(?:合同包|采购包|标包|标段|第\s*\d+\s*包)')
TEXT_FIELDS = {'采购标的':'product_name','标的名称':'product_name','货物名称':'product_name',
               '产品名称':'product_name','产品服务名称':'product_name','商品名称':'product_name',
               '服务名称':'product_name','品目名称':'category','品牌':'brand','货物品牌':'brand',
               '规格型号':'model','规格/型号':'model','货物型号':'model','型号':'model'}


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def money(raw, header=''):
    value = compact(raw).replace('（人民币）','').replace('(人民币)','')
    if any(word in value + header for word in ('%', '％', '费率', '折扣', '下浮', '评审价格', '得分')):
        return None
    match = re.fullmatch(r'(?:人民币|[¥￥])?(-?\d[\d,]*(?:\.\d+)?)(亿元|万元|千元|元)?', value)
    if not match:
        return None
    unit = match[2] or next((u for u in ('亿元','万元','千元','元') if u in header), '')
    return money_to_yuan(match[1] + unit) if unit else None


def read_notice_facts(text: str, source_file: str) -> dict:
    facts = {'awards': [], 'bids': [], 'items': [], 'totals': [], 'packages': []}
    phase, package, supplier = '', '', ''
    headers = {}
    for line in text.splitlines():
        line = line.strip()
        direct_label = re.fullmatch(r'(?:标段名称|合同包名称|采购包名称)\s*[:：]\s*(.{1,300})',line)
        if direct_label and interpret_field_text(direct_label[1],'product_name').value:
            facts['packages'].append({'lot_name':direct_label[1].strip(),'evidence':line})
        section = re.match(r'^[一二三四五六七八九十]+[、．.]\s*(.*)', line)
        if section:
            title = section[1]
            phase = 'items' if '主要标的信息' in title else 'awards' if re.search(r'采购结果|(?:中标|成交).*信息', title) else ''
            package, supplier = '', ''
        if PACKAGE.match(line) and not ROW.match(line) and len(line) < 400:
            package = line.rstrip('：:').strip()
            if phase in {'awards','items'}:
                facts['packages'].append({'lot_name':package,'evidence':line})
        row = ROW.match(line)
        if row:
            cells = [cell.strip() for cell in row['body'].split('|')]
            names = [compact(c).replace('（','(').replace('）',')') for c in cells]
            table = row['location'].split(' ROW:')[0]
            # A source total is recorded only where its label is explicit.
            if names and re.fullmatch(r'总中标(?:成交)?金额|中标成交总金额', names[0]):
                values = {money(c, names[0]) for c in cells[1:]}
                values.discard(None)
                if len(values) == 1:
                    facts['totals'].append({'value':next(iter(values)), 'evidence':line,
                                            'scope':'result' if phase=='awards' else 'metadata'})
            if any(re.search(r'供应商|投标人|投标单位', name) for name in names) and any(re.search(r'中标.*金额|最终报价|投标报价', name) for name in names):
                headers[table] = ('awards' if phase == 'awards' else 'bids', cells)
                continue
            if phase == 'items' and any(name in TEXT_FIELDS and TEXT_FIELDS[name] == 'product_name' for name in names):
                headers[table] = ('items', cells)
                continue
            if table not in headers or len(cells) != len(headers[table][1]):
                continue
            kind, labels = headers[table]
            labels = [compact(c).replace('（','(').replace('）',')') for c in labels]
            def column(pattern):
                found = [i for i, label in enumerate(labels) if re.fullmatch(pattern, label)]
                return found[0] if len(found) == 1 else None
            owner = column(r'(?:中标|成交|中标成交)?供应商(?:名称)?|投标人(?:名称)?|投标单位|供应商')
            lot_column = column(r'包号|包组|包组号|标段/合同包|标包')
            label = cells[lot_column] if lot_column is not None else package
            who = normalize_org(cells[owner]) if owner is not None else supplier
            if kind == 'awards' and lot_column is not None and label:
                facts['packages'].append({'lot_name':label,'evidence':line})
            if kind in {'awards','bids'}:
                price = column(r'(?:中标(?:\(成交\))?|中标成交|成交)金额(?:\([^)]*\))?' if kind == 'awards' else r'最终报价(?:\([^)]*\))?|投标报价(?:\([^)]*\))?')
                if who and price is not None:
                    value = money(cells[price], labels[price])
                    if value is not None:
                        facts[kind].append({'lot_name':label,'supplier':who,'value':value,'evidence':line})
                continue
            values = {}
            for i, header in enumerate(labels):
                if header in TEXT_FIELDS:
                    field = TEXT_FIELDS[header]
                    values[field] = interpret_field_text(cells[i], field).value
                elif re.fullmatch(r'(?:货物)?数量(?:\([^)]*\))?', header):
                    raw = compact(cells[i])
                    match = re.fullmatch(r'(\d[\d,]*(?:\.\d+)?)(?:\(?([^\d()]+)\)?)?', raw)
                    if match:
                        values['quantity'] = number_value(match[1])
                        header_unit = re.search(r'\(([^)]+)\)', header)
                        unit = match[2] or (header_unit[1] if header_unit and header_unit[1] != '单位' else '')
                        if unit:
                            values['quantity_unit'] = unit
                elif re.fullmatch(r'(?:单价|货物单价|总价|货物总价|金额|合价)(?:\([^)]*\))?', header):
                    field = 'unit_price' if '单价' in header else 'total_price'
                    values[field] = money(cells[i], header)
            if not values.get('product_name'):
                continue
            facts['items'].append({'lot_name':label,'supplier':who,'values':values,
                                   'location':row['location'],'evidence':line})
            continue
        if phase == 'awards':
            match = re.match(r'(?:中标成交|中标|成交)?供应商名称\s*[：:]\s*(.+)', line)
            if match:
                supplier = normalize_org(match[1])
            total = re.match(r'(?:总中标成交金额|总中标金额|中标成交总金额)\s*[:：]\s*(.+)', line)
            if total:
                value = money(total[1])
                if value is not None:
                    facts['totals'].append({'value':value,'evidence':line,'scope':'result'})
            amount = re.match(r'(?:中标(?:（成交）|\(成交\))?|成交)金额(?:[（(]元[）)])?\s*[:：]\s*(.+)', line)
            if amount and supplier:
                value = money(amount[1], line.split(':',1)[0].split('：',1)[0])
                if value is not None:
                    facts['awards'].append({'lot_name':package,'supplier':supplier,'value':value,'evidence':line})
        if phase == 'items':
            match = re.match(r'(?:货物|服务|工程)类[（(]([^）)]+)[）)]', line)
            if match:
                supplier = normalize_org(match[1])
    facts['source_file'] = source_file
    return facts


def reconcile_notice_facts(record: ProjectExtract, text: str, source_file: str) -> list[dict]:
    """Correct/add only explicit primary-notice cells, preserving all changes."""
    facts = read_notice_facts(text, source_file)
    record.lots = record.package_records()
    audit = []
    labels = {lot_identity(f['lot_name']) for f in facts['packages']}
    if len(record.lots) == 1 and not record.lots[0].lot_name and len(labels) == 1:
        proof = facts['packages'][0]
        record.lots[0].lot_name = proof['lot_name']
        audit.append({'action':'package_label','after':proof['lot_name'],
                      'source_file':source_file,'evidence':proof['evidence']})
    for lot in record.lots:
        items = [item for item in lot.items if item.source_file == source_file]
        if items:
            ground_evidence(ProjectExtract(lots=[LotExtract(lot_name=lot.lot_name,items=items)]),text)
    def target(fact):
        if fact['lot_name']:
            found = [lot for lot in record.lots if lot_identity(lot.lot_name) == lot_identity(fact['lot_name'])]
            labels = {lot_identity(f['lot_name']) for f in facts['awards'] if f['lot_name']}
            if not found and len(record.lots) == 1 and not record.lots[0].lot_name and len(labels) == 1:
                record.lots[0].lot_name = fact['lot_name']
                audit.append({'action':'package_label','after':fact['lot_name'],'source_file':source_file,
                              'evidence':fact['evidence']})
                found = record.lots
        elif len(record.lots) == 1:
            found = record.lots
        else:
            found = [lot for lot in record.lots if any(normalize_org(b.bidder) == fact['supplier'] and b.is_winner for b in lot.bids)]
            if not found:
                products = {dedupe_key(f['values']['product_name']) for f in facts['items']
                            if normalize_org(f['supplier']) == fact['supplier']}
                found = []
                for lot in record.lots:
                    owners = {normalize_org(b.bidder) for b in lot.bids}
                    owners |= {normalize_org(item.supplier) for item in lot.items if item.supplier}
                    if lot.lot_name and any(
                        (normalize_org(item.supplier) == fact['supplier'] or
                         (not item.supplier and owners == {fact['supplier']})) and any(
                            len(name) >= 6 and (name in dedupe_key(item.product_name) or
                            (len(dedupe_key(item.product_name)) >= 6 and dedupe_key(item.product_name) in name))
                            for name in products) for item in lot.items):
                        found.append(lot)
        return found[0] if len(found) == 1 else None
    def assign(obj, field, value, proof, label):
        old = getattr(obj, field)
        conflict = obj.field_conflicts.pop(field, [])
        if old != value or conflict:
            setattr(obj, field, value)
            audit.append({'action':'source_cell','scope':label,'field':field,'before':old,
                          'after':value,'candidates':conflict,'source_file':source_file,'evidence':proof})
    def conflict(obj, field, values, proofs, label):
        old = getattr(obj, field)
        previous = obj.field_conflicts.get(field, [])
        candidates = list(dict.fromkeys([*previous, *sorted(values), *([old] if old is not None else [])]))
        if old is not None or previous != candidates:
            setattr(obj, field, None)
            obj.field_conflicts[field] = candidates
            audit.append({'action':'money_conflict','scope':label,'field':field,'before':old,
                          'after':None,'candidates':candidates,'source_file':source_file,'evidence':proofs})
    explicit = [fact for fact in facts['totals'] if fact['scope']=='result' and fact['value']>0]
    metadata = [fact for fact in facts['totals'] if fact['scope']=='metadata' and fact['value']>0]
    totals = {fact['value'] for fact in explicit}
    if len(totals) == 1:
        assign(record, 'awarded_amount', next(iter(totals)), explicit[0]['evidence'], 'project')
    elif len(totals) > 1:
        conflict(record,'awarded_amount',totals,[f['evidence'] for f in explicit],'project')
    elif not totals and len({fact['value'] for fact in metadata}) == 1:
        value = metadata[0]['value']
        # Platform overview zeros and rounded display totals cannot overwrite
        # a more precise result-table amount. Never infer a missing total by sum.
        disclosed = {fact['value'] for fact in facts['awards']}
        current = record.awarded_amount
        if current is None or (current not in disclosed and abs(current-value)>=1):
            assign(record,'awarded_amount',value,metadata[0]['evidence'],'project')
    grouped = {}
    for fact in facts['awards']:
        lot = target(fact)
        if lot:
            grouped.setdefault(id(lot), (lot, []))[1].append(fact)
    for lot, rows in grouped.values():
        # A single supplier amount proves a package total only when the source
        # identifies that one award. Multiple recipients do not authorize sums.
        values = {row['value'] for row in rows}
        owners = {row['supplier'] for row in rows}
        if len(values) > 1 and len(owners) == 1:
            conflict(lot,'awarded_amount',values,[row['evidence'] for row in rows],lot.lot_name)
        if len(values) == 1 and len(owners) == 1:
            assign(lot, 'awarded_amount', next(iter(values)), rows[0]['evidence'], lot.lot_name)
            owner = next(iter(owners))
            # Do not demote consortium members from a one-line award label.
            if not any(b.consortium_id for b in lot.bids) and not re.search(r'联合体|[、;；]', owner):
                for bid in lot.bids:
                    if normalize_org(bid.bidder) == owner and not bid.is_winner:
                        audit.append({'action':'winner_flag','lot_name':lot.lot_name,'supplier':bid.bidder,
                                      'before':False,'after':True,'evidence':rows[0]['evidence']})
                        bid.is_winner = True
                    if bid.is_winner and normalize_org(bid.bidder) != owner and bid.non_winning_reason:
                        audit.append({'action':'winner_flag','lot_name':lot.lot_name,'supplier':bid.bidder,
                                      'before':True,'after':False,'evidence':rows[0]['evidence']})
                        bid.is_winner = False
                if not any(normalize_org(b.bidder) == owner for b in lot.bids):
                    lot.bids.append(BidExtract(bidder=owner,is_winner=True,source_file=source_file,evidence=rows[0]['evidence']))
                    audit.append({'action':'missing_winner','lot_name':lot.lot_name,'supplier':owner,'evidence':rows[0]['evidence']})
    bid_rows = {}
    for fact in facts['bids']:
        lot = target(fact)
        found = [b for b in lot.bids if normalize_org(b.bidder) == fact['supplier']] if lot else []
        if len(found) == 1:
            bid_rows.setdefault(id(found[0]),(found[0],lot.lot_name,[]))[2].append(fact)
    for bid,label,rows in bid_rows.values():
        values = {row['value'] for row in rows}
        if len(values) == 1:
            assign(bid,'bid_amount',next(iter(values)),rows[0]['evidence'],label+' / '+bid.bidder)
        else:
            conflict(bid,'bid_amount',values,[row['evidence'] for row in rows],label+' / '+bid.bidder)
    source_locations = {fact['location'] for fact in facts['items']}
    for fact in facts['items']:
        lot = target(fact)
        if lot is None:
            continue
        owner = fact['supplier']
        if not owner:
            owners = {normalize_org(b.bidder) for b in lot.bids if b.is_winner}
            owner = next(iter(owners)) if len(owners) == 1 else ''
        if not owner:
            continue
        exact = [item for item in lot.items if item.source_file == source_file and item.line_id == fact['location']
                 and (dedupe_key(item.product_name) == dedupe_key(fact['values']['product_name'])
                      or item.source_context.get('text') == fact['evidence'])]
        if len(exact) > 1:
            continue
        if not exact:
            # A row whose old identity was ungrounded is adopted only when its
            # product name uniquely selects one primary-source record.
            exact = [item for item in lot.items if item.source_file == source_file
                     and item.line_id not in source_locations
                     and dedupe_key(item.product_name) == dedupe_key(fact['values']['product_name'])]
            if len(exact) > 1:
                continue
        if exact:
            item = exact[0]
            for field, value in fact['values'].items():
                if value is not None:
                    assign(item, field, value, fact['evidence'], lot.lot_name + ' / ' + item.product_name)
        else:
            item = ItemExtract(**fact['values'],supplier=owner,source_file=source_file,source='announcement',
                               line_id=fact['location'],source_location=fact['location'],evidence=fact['evidence'])
            lot.items.append(item)
            audit.append({'action':'missing_source_row','lot_name':lot.lot_name,'source_file':source_file,
                          'location':fact['location'],'product_name':item.product_name,'evidence':fact['evidence']})
        item.line_id, item.source_location = fact['location'], fact['location']
    if audit:
        record.warnings.append('已按当前公告明确表格核对金额及标的来源行；其他来源冲突仍待核验')
    return audit
