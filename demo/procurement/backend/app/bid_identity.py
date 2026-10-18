"""Anonymous score-table IDs are source records, not additional named companies."""
from __future__ import annotations

import re
from .normalization import normalize_org


def non_company_name_reason(name: str) -> str:
    """Reject explicit placeholders and known anonymous IDs, not unfamiliar company names."""
    value = normalize_org(name)
    label = value.strip('{}[]()【】<>《》')
    if not value or re.fullmatch(r'[xX×*＿_]{2,}', label) or label in {
        '供应商名称','投标人名称','投标单位名称','供应商全称','投标人全称',
        '请填写供应商名称','请填写投标人名称',
    }:
        return '未填写的供应商模板占位文本'
    if re.fullmatch(r'GC-[A-Z]{3}\d{6}-\d{8}[A-Z0-9]{4,}', value, re.I):
        return '匿名投标编号，未披露企业名称'
    return ''


def verify_bid_identities(bids, text: str) -> list[dict]:
    headers = {}
    identifiers = set()
    for line in text.splitlines():
        row = re.match(r'^\[(TABLE:\d+) ROW:\d+\]\s*(.*)', line.strip())
        if not row:
            continue
        cells = [cell.strip() for cell in row[2].split('|')]
        if any(cell in {'投标人编号','供应商编号','投标单位编号'} for cell in cells):
            # A table that also discloses names requires explicit alias mapping,
            # not the anonymous-only handling here.
            if not any(cell in {'投标人名称','供应商名称','投标单位名称'} for cell in cells):
                headers[row[1]] = next(i for i,c in enumerate(cells) if c in {'投标人编号','供应商编号','投标单位编号'})
            else:
                headers.pop(row[1],None)
            continue
        index = headers.get(row[1])
        if index is not None and index < len(cells):
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{5,}',cells[index]):
                identifiers.add(cells[index])
    audit = []
    for bid in bids:
        if bid.bidder in identifiers and bid.identity_confirmed:
            audit.append({'action':'unconfirmed_bid_identity','bidder':bid.bidder,
                          'source_file':bid.source_file,'previous_is_winner':bid.is_winner,
                          'evidence':bid.evidence})
            bid.identity_confirmed = False
            bid.is_winner = False
    return audit
