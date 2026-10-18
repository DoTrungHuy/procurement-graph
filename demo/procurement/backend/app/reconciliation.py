"""Conservative, one-to-one reconciliation of repeated transaction evidence."""
from __future__ import annotations

from .models import ItemExtract, LotExtract
from .normalization import dedupe_key, normalize_org


def _reference(item: ItemExtract) -> dict[str, str]:
    return {'source_file': item.source_file, 'source_location': item.source_location,
            'evidence': item.evidence, 'source': item.source}


def reconcile_items(lot: LotExtract) -> tuple[int, bool]:
    """Require printed business code, supplier, identity and disclosed numbers.

    Never collapse repeated rows within one file, infer identity from a price,
    or merge contradictory fields. Preserve every source of an accepted match.
    """
    retained: list[ItemExtract] = []
    combined = 0
    uncertain = False
    for item in lot.items:
        candidates = []
        if item.business_line_id and item.source_file and item.supplier and item.product_name and item.model:
            for previous in retained:
                files = {previous.source_file, *(s.get('source_file', '') for s in previous.related_sources)}
                if item.source_file in files or previous.business_line_id != item.business_line_id:
                    continue
                if normalize_org(previous.supplier) != normalize_org(item.supplier):
                    continue
                if dedupe_key(previous.product_name) != dedupe_key(item.product_name):
                    continue
                if dedupe_key(previous.model) != dedupe_key(item.model):
                    uncertain = True
                    continue
                candidates.append(previous)
        matched = None
        for previous in candidates:
            fields_agree = all(not getattr(previous, f) or not getattr(item, f)
                               or dedupe_key(getattr(previous, f)) == dedupe_key(getattr(item, f))
                               for f in ('category', 'brand', 'quantity_unit'))
            # Quantity plus at least one disclosed price must agree. Unknown
            # numeric values cannot prove a match and are never reconstructed.
            numbers = [(getattr(previous, f), getattr(item, f)) for f in ('quantity', 'unit_price', 'total_price')]
            numeric_agreement = all(a is None or b is None or abs(a-b) < 1e-6 for a, b in numbers)
            supported = all(x is not None for x in numbers[0]) and any(all(x is not None for x in pair) for pair in numbers[1:])
            if fields_agree and numeric_agreement and supported:
                matched = previous
                break
        if matched is None:
            uncertain = uncertain or bool(candidates)
            retained.append(item)
            continue
        for field in ('category', 'brand', 'quantity_unit', 'unit_price', 'total_price'):
            if getattr(matched, field) in ('', None) and getattr(item, field) not in ('', None):
                setattr(matched, field, getattr(item, field))
        for reference in [_reference(item), *item.related_sources]:
            if reference not in matched.related_sources:
                matched.related_sources.append(reference)
        combined += 1
    lot.items = retained
    # Unproven cross-file identity is not permission to add both source prices.
    # Keep the source rows and amounts exportable while withholding a business
    # total for the affected brand until their relation is verified.
    for item in retained:
        item.aggregation_eligible, item.aggregation_reason = True, ''
    for index, item in enumerate(retained):
        for other in retained[:index]:
            if not item.source_file or not other.source_file or item.source_file == other.source_file:
                continue
            if not item.product_name or not item.supplier:
                continue
            if (normalize_org(item.supplier), dedupe_key(item.product_name)) != (normalize_org(other.supplier), dedupe_key(other.product_name)):
                continue
            uncertain = True
            for pending in (item, other):
                pending.aggregation_eligible = False
                pending.aggregation_reason = '同包跨来源同名标的关系未确认，原文金额保留，业务合计待核验'
    return combined, uncertain
