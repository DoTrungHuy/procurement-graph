"""Versioned interpretation of disclosed text; source wording stays auditable."""
from __future__ import annotations

from dataclasses import dataclass
import re


BUSINESS_TEXT_POLICY = 'business-v1'
TEXT_FIELDS = frozenset({'product_name', 'category', 'brand', 'model'})
TEXT_STATUS_NOTES = {
    'reference_only': '原文为查阅指引，当前来源未披露具体值，需核查引用材料',
    'not_disclosed': '当前原文未披露具体值',
    'mixed_reference': '指引与其他文字混合，保留原文待核验，不自动清空',
    'disclosed_with_reference': '原文同时含查阅指引和明确字段值，保留明确值及原文',
}
_MISSING = {'无', '/', '-', '—', '未提供', '未披露', '不适用', '暂无'}
_REFERENCE_START = r'(?:详见|具体见|参见|见|详阅|参考)'
_TARGET = r'(?:附件|附表|清单|文件|报价表|明细表|明细|参数表|技术要求)'
_REFERENCE = re.compile(
    rf'{_REFERENCE_START}[^，,；;。\n]*{_TARGET}'
    r'(?:\s*\d+)?(?:\s*[“「《（(][^”」》）)]+[”」》）)])?'
    r'(?:中的?(?:相关)?(?:内容|说明|要求|明细|清单|参数|品牌|型号))?'
    r'(?:为准|执行)?'
)
_REQUIREMENT = re.compile(r'(?:满足|符合|响应|按照|按)(?:采购|招标|投标|响应|磋商|谈判|询价)?文件(?:要求)?(?:执行|为准)?')
_LABELS = {'brand': r'品牌(?:名称|名)?', 'model': r'(?:规格型号|规格/型号|型号)',
           'category': r'(?:品目名称|品目)', 'product_name': r'(?:产品服务名称|采购标的|货物名称|产品名称)'}


@dataclass(frozen=True)
class FieldText:
    raw: str
    value: str
    status: str


def interpret_field_text(value: object, field: str = '') -> FieldText:
    text = '' if value is None else str(value).strip()
    if not text or text in _MISSING or re.fullmatch(r'[-—–/\s]+', text):
        return FieldText(text, '', 'not_disclosed')
    clauses = [part.strip() for part in re.split(r'[，,；;。\n]+', text) if part.strip()]
    references = [bool(_REFERENCE.fullmatch(part) or _REQUIREMENT.fullmatch(part)) for part in clauses]
    if references and all(references):
        return FieldText(text, '', 'reference_only')
    if any(references):
        # Only an explicit label for this field can remove surrounding prose.
        # Conflicting values or unlabelled mixed text remain intact for review.
        label = _LABELS.get(field)
        values = []
        if label:
            for part, reference in zip(clauses, references):
                if reference:
                    continue
                match = re.fullmatch(rf'{label}\s*(?:[：:]|为|是)\s*(.+)', part)
                if match:
                    candidate = match.group(1).strip()
                    if candidate not in _MISSING and not (_REFERENCE.fullmatch(candidate) or _REQUIREMENT.fullmatch(candidate)):
                        values.append(candidate)
        if len(set(values)) == 1 and len(values) == sum(not r for r in references):
            return FieldText(text, values[0], 'disclosed_with_reference')
        return FieldText(text, text, 'mixed_reference')
    if re.match(_REFERENCE_START, text) and re.search(_TARGET, text):
        return FieldText(text, text, 'mixed_reference')
    return FieldText(text, text, 'disclosed')
