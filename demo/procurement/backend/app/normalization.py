from __future__ import annotations

import re
import math
import unicodedata
from decimal import Decimal, InvalidOperation

from .field_text import interpret_field_text


MONEY_UNITS = {"亿元": Decimal("100000000"), "亿": Decimal("100000000"), "万元": Decimal("10000"), "万": Decimal("10000"), "千元": Decimal("1000"), "千": Decimal("1000"), "元": Decimal("1")}
_DURATION = r'\d+(?:\.\d+)?\s*(?:个)?(?:工作日|日历日|天|日|月|年)'
_DURATION_NOTE = re.compile(
    rf'(?:(?:交货|交付|供货|质保|保修|工期|报价有效期|有效期|服务期|履约期)(?:期|期限)?\s*[:：]?\s*{_DURATION}(?:以内|内)?'
    rf'|{_DURATION}(?:以内|内)?\s*(?:交货|交付|供货|质保|保修))'
)


def clean_text(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _numeric_text(value: object) -> str:
    # Ignore only explicit, whole-segment duration notes. Unknown numeric
    # annotations remain ambiguous instead of being silently discarded.
    parts = re.split(r'[;；]', unicodedata.normalize('NFKC', str(value)))
    if len(parts) == 1:
        return parts[0]
    return ';'.join(part for part in parts if not _DURATION_NOTE.fullmatch(part.strip()))


def has_parallel_numbers(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = _numeric_text(value).split(';')
    return sum(bool(re.search(r'\d', part)) for part in parts) > 1


def money_to_yuan(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if has_parallel_numbers(value):
        return None
    raw = _numeric_text(value).replace(",", "").replace("¥", "").strip()
    if re.search(r'[%‰]|费率|折扣|下浮|美元|欧元|USD|EUR',raw,re.I) or re.search(r'\d\s*(?:-|—|~|至|到)\s*\d',raw):
        return None  # A percentage/range/foreign currency cannot be treated as a disclosed yuan amount.
    match = re.search(r"(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*[（(]?\s*(亿元|亿|万元|万|千元|千|元)?", raw)
    if not match:
        return None
    try:
        number = Decimal(match.group(1))
        unit = MONEY_UNITS.get(match.group(2) or "元", Decimal("1"))
        result = float(number * unit)
        return result if math.isfinite(result) else None
    except InvalidOperation:
        return None


def number_value(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if has_parallel_numbers(value):
        return None
    match = re.search(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", _numeric_text(value).replace(",", ""))
    result = float(match.group()) if match else None
    return result if result is None or math.isfinite(result) else None


def normalize_org(name: str) -> str:
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or ""))
    return value.strip("，,。;；:：")


def model_match_key(value: object) -> str:
    """Whitespace-insensitive exact-match key. Display text stays unchanged."""
    text = str(value or "").replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", "", text)


def dedupe_key(*values: object) -> str:
    return "|".join(re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(v) if v is not None else "")).lower() for v in values)


def lot_identity(name: str) -> str:
    """Conservative package identity; keep the original label for export.

    Only complete numbered labels are aliases. Descriptive labels are never
    reduced to their first digit, and missing labels have no inferred identity.
    """
    value = normalize_org(name).replace(':', '')
    if not value:
        return ''
    if value in {'未分包', '不分包', '不划分标段'}:
        return 'single'
    # A source label may append the procurement title after its explicit ID.
    described = re.fullmatch(r'([^()]+)\(.+\)', value)
    if described:
        anchor = lot_identity(described.group(1))
        if anchor.startswith('number:'):
            return anchor
    if re.fullmatch(r'[0-9]+|[A-Za-z]', value):
        return 'number:' + (str(int(value)) if value.isdigit() else value.upper())
    token = r'[0-9]+|[一二三四五六七八九十百零〇两]+|[A-Za-z]'
    match = re.fullmatch(rf'(?:合同包|采购包|标包|包组|包|标段)\(?({token})\)?', value)
    if not match:
        match = re.fullmatch(rf'第?({token})(?:合同包|采购包|标包|包|标段)', value)
    if not match:
        return 'name:' + value.casefold()
    number = match.group(1)
    if number.isdigit():
        number = str(int(number))
    elif not number.isascii():
        digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
        if '十' not in number and '百' not in number:
            return 'number:' + str(int(''.join(str(digits[char]) for char in number)))
        total, current = 0, 0
        for char in number:
            if char in digits:
                current = digits[char]
            else:
                total += (current or 1) * {'十': 10, '百': 100}[char]
                current = 0
        number = str(total + current)
    return 'number:' + number.upper()


def disclosed_text(value: object, field: str = '') -> str:
    return interpret_field_text(value, field).value


def yuan_to_wan(value: object, places: int | None = None) -> float | None:
    number = money_to_yuan(value)
    if number is None:
        return None
    from decimal import ROUND_HALF_UP
    converted = Decimal(str(number)) / Decimal("10000")
    if places is not None:
        converted = converted.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return float(converted)


def review_status(value: object) -> str:
    if value is False:
        return '未通过'
    if value is True:
        return '通过'
    text = str(value or "").strip()
    if text.lower() in {'failed','false','rejected','否'} or any(word in text for word in ("未通过", "不通过", "不予通过", "不合格", "失败", "不符合", "否决")):
        return "未通过"
    if text.lower() in {"通过", "合格", "true", "passed", "pass", "是"}:
        return "通过"
    return "未披露"

