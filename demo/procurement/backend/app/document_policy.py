"""Identify supporting documents that do not establish a transaction line.

All files are still parsed and retained in the audit/cache. Supplier policy
declarations and blank tender templates are not additional awarded items or a
bidder/award list. Unclassified attachments remain eligible for model extraction.
"""
from __future__ import annotations

import re
import unicodedata


def document_role(filename: str, text: str) -> tuple[str, str]:
    if '[PARSE_WARNING]' in text:
        return 'incomplete-source', ''  # Unread pages may contain a mixed quotation.
    name = re.sub(r'\s+', '', unicodedata.normalize('NFKC', filename))
    # A combined attachment can contain a declaration and a real quote. Keep it
    # eligible when its text contains both a quote heading and price columns.
    compact = re.sub(r'\s+', '', re.sub(r'\[(?:PAGE|IMAGE|TABLE|SHEET):[^\]]+\]', '', text))
    heading = compact[:3000]
    declaration = any(word in name for word in ('中小企业声明', '中小企声明', '中企业声明', '本国产品声明', '残疾人福利', '监狱企业', '政策适用性说明')) or bool(re.search(r'本国产品.{0,30}声明', name))
    declaration = declaration or bool(re.search(r'中小企业声明函|关于符合本国产品标准的声明函', heading))
    mixed_quote = bool(re.search(r'分项报价表|报价明细表|报价明细附件|报价清单', compact) and re.search(r'单价.{0,120}(?:金额|总价|合计|小计)', compact))
    mixed_review = bool(re.search(r'(?:供应商|投标人|投标单位).{0,80}(?:综合得分|最终得分|资格性审查|符合性审查)', compact))
    supporting_payment = bool(re.search(r'劳务报酬|专家报酬|评审费用支付', name + heading[:300]))
    supporting_history = bool(re.search(r'业绩一览|类似业绩|业绩证明|历史合同|业绩[（(]', name))
    if (supporting_payment or supporting_history) and (mixed_quote or mixed_review):
        return 'mixed-transaction-evidence', ''
    if supporting_payment:
        return 'supporting-payment', '专家劳务报酬表不证明商品明细或供应商投标事实；保留原文供复核'
    if supporting_history:
        return 'supporting-history', '历史业绩与合同证明不作为本次成交、报价或共同投标事实；保留原文供复核'
    if declaration and (mixed_quote or mixed_review):
        return 'mixed-transaction-evidence', ''
    if declaration:
        return 'supporting-declaration', '政策资格声明不作为新增成交标的或中标、报价事实；保留原文解析供复核'
    if any(word in name for word in ('分项报价','报价明细','明细附件','成交明细','中标明细','评审','得分','未中标','中标结果','成交结果')):
        return 'transaction-evidence', ''
    if any(word in name for word in ('招标文件','采购文件','磋商文件','谈判文件','询价文件','征求意见稿','采购需求','投标须知')):
        return 'requirements-template', '采购需求和招标模板不证明已成交商品、实际投标报价或审查结果；保留原文解析供复核'
    heading = re.sub(r'\s+', '', text[:1800])
    if ('招标文件' in heading or '竞争性磋商文件' in heading) and re.search(r'投标人须知|供应商须知|第一章|目录', heading):
        return 'requirements-template', '文档首页明确为招标/磋商文件；不把需求或空白模板写入成交事实'
    return 'unclassified-attachment', ''
