"""Resolve package fragments only with source evidence or an exact reviewed plan.

Equal prices, one supplier, and a single anonymous fragment are not package proof.
Original snapshots belong in the repair audit; this module never changes fields
other than package labels and the grouping they explicitly establish.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from .evidence import LOCATION
from .models import ProjectExtract
from .normalization import dedupe_key, lot_identity, normalize_org

HEADING = re.compile(r'^(第[0-9一二三四五六七八九十]+包|(?:合同包|采购包|标段)\s*[0-9]+)\s*[:：]?$')
COUNT = re.compile(r'(?:分包数量|采购包数量|标段数量)\s*[:：|]\s*(\d+)(?:个|包|\s|$)')


def _regroup(record: ProjectExtract) -> None:
    # Import locally: extraction calls this module after all source pieces exist.
    from .extractor import merge_results, validate_result
    merged = merge_results([record])
    record.lots = merged.lots
    record.warnings = list(dict.fromkeys([*record.warnings, *merged.warnings]))
    if merged.processing_status == 'partial':
        record.processing_status = 'partial'
    validate_result(record)


def resolve_package_labels(record: ProjectExtract, sources: dict[str, str]) -> list[dict]:
    """Use project-bound award headings or a directly printed package count.

    A supplier must occur in an award block with an explicit package heading.
    A known supplier winning multiple packages remains ambiguous. Conflicting
    package counts or numbered packages disable the single-package route.
    """
    identities = [dedupe_key(value) for value in (record.project_code, record.project_name) if value]
    if not identities:
        return []
    record.lots = record.package_records()
    suppliers = {normalize_org(b.bidder) for lot in record.package_records() for b in lot.bids if b.is_winner}
    catalogue, existing = defaultdict(set), defaultdict(set)
    for lot in record.lots:
        if lot.lot_name:
            for bid in lot.bids:
                if bid.is_winner:
                    existing[normalize_org(bid.bidder)].add(lot_identity(lot.lot_name))
    counts = set()
    for name, text in sources.items():
        if not any(identity in dedupe_key(text) for identity in identities):
            continue
        lines = [LOCATION.sub('', line).strip() for line in text.splitlines()]
        for line in lines:
            counts.update(int(match.group(1)) for match in COUNT.finditer(line))
        boundaries = [(index, HEADING.fullmatch(line)) for index, line in enumerate(lines)]
        boundaries = [(index, match.group(1)) for index, match in boundaries if match]
        for index, (start, label) in enumerate(boundaries):
            end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(lines)
            block = '\n'.join(lines[start + 1:end])
            if not re.search(r'供应商名称|中标供应商|成交供应商|中标人', block):
                continue
            # Only the immediately following supplier field supplies identity;
            # a later table or another award section cannot lend its supplier.
            match = re.search(r'(?:供应商名称|中标供应商|成交供应商|中标人)\s*[:：]\s*([^\n|；;]+)', block)
            if not match:
                continue
            owner = normalize_org(match.group(1))
            if owner in suppliers:
                catalogue[owner].add(label)
    for owner in list(catalogue):
        observed = {lot_identity(label) for label in catalogue[owner]}
        if len(existing[owner] | observed) > 1:
            del catalogue[owner]
    named = {lot_identity(lot.lot_name) for lot in record.package_records() if lot.lot_name}
    single = counts == {1} and len(suppliers) == 1 and not catalogue and not (named - {'single'})
    changed = []
    for lot in record.package_records():
        if lot.lot_name:
            continue
        owners = {normalize_org(b.bidder) for b in lot.bids if b.is_winner}
        owners |= {normalize_org(item.supplier) for item in lot.items if item.supplier}
        matches = [catalogue.get(owner, set()) for owner in owners if owner]
        # Every disclosed owner must resolve to the same uniquely named package.
        labels = set.union(*matches) if matches else set()
        if matches and all(len(value) == 1 for value in matches) and len(labels) == 1:
            label = next(iter(labels))
        elif single:
            label = '未分包'
        else:
            continue
        lot.lot_name = label
        changed.append({'previous_key': lot.lot_key, 'lot_name': label})
    if changed:
        record.warnings.append('采购包归属依据同项目原文包号及中标供应商或明确单包数量核验；保留各来源记录')
        _regroup(record)
    return changed


def apply_reviewed_labels(snapshot: str, review: dict, sources: dict[str, dict]) -> tuple[ProjectExtract, dict]:
    """Apply a local source review to its exact snapshot; refuse stale inputs.

    This is an explicit, offline repair operation, never a model-provided plan.
    A review does not sign off extraction accuracy or change completion flags.
    """
    if hashlib.sha256(snapshot.encode()).hexdigest() != review['snapshot_sha256']:
        raise ValueError('采购包复核对应的原始结果已变化，拒绝套用旧方案')
    record = ProjectExtract.model_validate_json(snapshot)
    if record.notice_uuid != review['notice_uuid']:
        raise ValueError('复核公告标识不一致')
    evidence = review['evidence']
    if not evidence:
        raise ValueError('采购包复核缺少原文依据')
    for proof in evidence:
        source = sources.get(proof['filename'])
        if not source or source['sha256'] != proof['sha256']:
            raise ValueError('复核来源文件缺失或已变化: ' + proof['filename'])
        if not proof['quote'] or proof['quote'] not in source['text']:
            raise ValueError('复核摘录无法在原文中核验: ' + proof['filename'])
    lots = {lot.lot_key: lot for lot in record.assign_lot_keys()}
    assignments = review['assignments']
    if not assignments or len({a['lot_key'] for a in assignments}) != len(assignments):
        raise ValueError('采购包复核映射为空或重复')
    for assignment in assignments:
        key, label = assignment['lot_key'], assignment['lot_name']
        references = assignment.get('evidence', [])
        if key not in lots or lots[key].lot_name or not label or not references:
            raise ValueError('复核只允许对有原文依据的现存匿名片段指定真实包名')
        if any(type(index) is not int or not 0 <= index < len(evidence) for index in references):
            raise ValueError('采购包复核的来源引用无效')
        selected = [evidence[index] for index in references]
        if label == '未分包':
            if not review.get('single_package_review'):
                raise ValueError('未分包需要整篇公告及附件的明确复核结论')
        elif not any(label in proof['quote'] or label in proof['filename'] for proof in selected):
            raise ValueError('指定包名没有出现在引用原文或原始文件名中')
        lots[key].lot_name = label
    previous_status = record.processing_status
    before = {'packages': len(lots), 'items': sum(len(l.items) for l in lots.values()),
              'bids': sum(len(l.bids) for l in lots.values())}
    _regroup(record)
    if record.processing_status != 'partial':
        record.processing_status = previous_status  # Identity is not an accuracy sign-off.
    record.warnings.append('采购包归属已按有哈希及摘录的原文复核清单修复；其余质量警告与原始快照保留')
    return record, {'notice_uuid': record.notice_uuid, 'before': before,
        'after': {'packages': len(record.lots), 'items': sum(len(l.items) for l in record.lots),
                  'bids': sum(len(l.bids) for l in record.lots)},
        'assignments': assignments, 'evidence': evidence, 'reason': review['reason'],
        'processing_status': record.processing_status, 'official_score': False}
