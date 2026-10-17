"""Conservative optimized routing with the original single-file fallback."""
from __future__ import annotations

import asyncio
import json
import re
import time
from decimal import Decimal
from pathlib import Path

from .blank_templates import classify_blank_template
from .checkpoints import digest
from .document_batching import (BatchDocument, BatchResponseError, batch_system_prompt,
                               build_batch_payload, decode_batch_response, group_small_documents)
from .document_policy import document_role
from .evidence import LOCATION, align_attachment_packages, ground_evidence
from .models import ProjectExtract
from .normalization import lot_identity, normalize_org
from .parsers import PARSE_WARNING_PREFIX
from .table_extract import extract_standard_table


def _count(model, usage, name, amount=1):
    model.optimization_counts[name] += amount
    if usage is not None:
        counts = usage.setdefault('optimization_counts', {})
        counts[name] = counts.get(name, 0) + amount


def _prompts(model, doc, names, announcement_context, announcement):
    context = announcement_context
    if doc['source'] == 'attachment' and announcement is not None:
        context = json.dumps({'project_name': announcement.project_name, 'project_code': announcement.project_code,
            'purchaser': announcement.purchaser, 'packages': [{'lot_name': lot.lot_name,
                'winners': [bid.bidder for bid in lot.bids if bid.is_winner and bid.eligible]}
                for lot in announcement.package_records()]}, ensure_ascii=False)
    label, text = doc['label'], doc['text']
    prefix = (f'[FILE:{label} SOURCE:{doc["source"]}]\n'
        f'[上下文，仅用于理解字段，不重复抽取；采购包必须由当前段落证明]\n公告：{context}\n文件开头：{text[:1200]}\n[当前段落]\n')
    legacy = (f'[FILE:{label} SOURCE:{doc["source"]}]\n'
        f'[上下文，仅用于理解字段，不重复抽取；采购包必须由当前段落证明]\n公告：{announcement_context}\n文件开头：{text[:1200]}\n[当前段落]\n')
    chunk_prefix = model._chunk_prefix(text, prefix, legacy, names, doc['label'])
    return prefix, legacy, chunk_prefix, model._document_key(text, prefix, chunk_prefix, names)


def _ground(result, doc, announcement):
    for lot in result.package_records():
        for item in lot.items:
            item.source = doc['source']
            item.source_file = doc['label']
            item.source_location = item.source_location or item.line_id
        for bid in lot.bids:
            bid.source_file = doc['label']
    ground_evidence(result, doc['text'])
    if doc['source'] == 'attachment' and announcement is not None:
        align_attachment_packages(result, announcement, doc['path'].name, doc['text'])


def _consume_checkpoint(model, usage, cached):
    pieces, metadata = cached
    model.document_cache_hits += 1
    if usage is not None:
        usage['document_cache_hits'] += 1
        usage['response_models'] = sorted(set(usage['response_models']) | set(metadata.get('response_models', [])))
        usage.setdefault('retrieval', []).extend(metadata.get('retrieval', []))
    return pieces


async def _save(model, usage, key, pieces, retrieval_start=0):
    if not key:
        return
    metadata = {'response_models': (usage or {}).get('response_models', []),
                'retrieval': (usage or {}).get('retrieval', [])[retrieval_start:]}
    if await asyncio.to_thread(model.checkpoints.save, key, pieces, metadata):
        model.document_cache_writes += 1
        if usage is not None:
            usage['document_cache_writes'] += 1


async def _single(model, doc, names, announcement_context, announcement, usage):
    from .extractor import FatalModelError, _replay_context, chunk_text
    if not model.api_key and not model.cache_only:
        return [], []
    prefix, legacy, chunk_prefix, document_key = _prompts(model, doc, names, announcement_context, announcement)
    _replay_context.set({'prefix': prefix, 'legacy_prefix': legacy, 'names': names})
    results, errors = [], []
    for part in chunk_text(doc['text'], max(128, model.settings.max_llm_chars - len(chunk_prefix))):
        key = digest([document_key, part]) if document_key else None
        try:
            cached = await asyncio.to_thread(model.checkpoints.load, key) if key else None
            if cached is not None:
                results.extend(_consume_checkpoint(model, usage, cached))
                continue
            retrieval_start = len((usage or {}).get('retrieval', []))
            started = time.perf_counter()
            try:
                pieces = await model._extract_piece(prefix, part, [doc['label']])
            finally:
                if usage is not None:
                    usage['model_seconds'] += round(time.perf_counter() - started, 4)
            for result in pieces:
                _ground(result, doc, announcement)
                results.append(result)
            await _save(model, usage, key, pieces, retrieval_start)
        except FatalModelError:
            raise
        except Exception as exc:
            errors.append(f'{doc["label"]}: {type(exc).__name__}: {str(exc)[:180]}')
    return results, errors


def _observed_number(value, text, *, money=False, document=''):
    if value is None:
        return True
    body = LOCATION.sub('', text).replace(',', '').replace('，', '')
    numbers = {Decimal(raw) for raw in re.findall(r'(?<![0-9A-Za-z_.])-?\d+(?:\.\d+)?(?![0-9A-Za-z_.])', body)}
    if money:
        # A header can declare the unit; do not assume scaling from magnitude.
        for unit, factor in [('千元', 1000), ('万元', 10000), ('亿元', 100000000)]:
            if unit in text or re.search(r'(?:单价|总价|金额|报价)[^\n]{0,12}' + unit, document):
                numbers |= {number * factor for number in tuple(numbers)}
    return Decimal(str(value)) in numbers


def _batch_grounded(result, doc):
    if result.processing_status != 'complete':
        return False
    text = normalize_org(doc['text'])
    for field in ('project_name', 'project_code', 'purchaser'):
        value = normalize_org(getattr(result, field))
        if value and value not in text:
            return False
    if any(not _observed_number(getattr(result, field), doc['text'], money=True)
           for field in ('budget_amount', 'awarded_amount')):
        return False
    count = 0
    for lot in result.package_records():
        if any(not _observed_number(getattr(lot, field), doc['text'], money=True)
               for field in ('budget_amount', 'awarded_amount')):
            return False
        for item in lot.items:
            if not item.source_context:
                return False
            row = item.source_context['text']
            for field in ('product_name', 'category', 'brand', 'model', 'supplier'):
                value = normalize_org(getattr(item, field))
                if value and value not in normalize_org(row):
                    return False
            if any(not _observed_number(getattr(item, field), row, money=field != 'quantity', document=doc['text'])
                   for field in ('quantity', 'unit_price', 'total_price')):
                return False
            count += 1
        for bid in lot.bids:
            evidence = normalize_org(bid.evidence)
            if not evidence or evidence not in text or normalize_org(bid.bidder) not in evidence:
                return False
            if not _observed_number(bid.bid_amount, bid.evidence, money=True, document=doc['text']) or not _observed_number(bid.score, bid.evidence):
                return False
            # Rich review/score/consortium structures require the original path.
            if bid.evaluation_scores or bid.consortium_id or bid.rank is not None:
                return False
            if any(getattr(bid, field) != '未披露' for field in ('review_status', 'qualification_status', 'compliance_status')):
                return False
            if any(normalize_org(getattr(bid, field)) not in text for field in ('review_reason', 'non_winning_reason')):
                return False
            if bid.is_winner and not re.search('中标|成交', bid.evidence):
                return False
            count += 1
    # An empty answer cannot independently prove that a source has no facts.
    # Known empty templates have already been classified before this route.
    return count > 0


def _same_lot(doc, lot_key):
    token = r'[0-9A-Za-z一二三四五六七八九十百零〇两]+'
    text = doc['path'].name + '\n' + doc['text']
    references = re.findall(r'(?:合同包|采购包|标包|包组|标段|包号)\s*[:：]?\s*[（(]?(' + token + ')', text)
    references += re.findall(r'第\s*(' + token + r')\s*(?:包|标段)', text)
    references += re.findall(r'(?:^|[\n\s:_：])包\s*[（(]?\s*(' + token + ')', text)
    return all(lot_identity('包' + value) == lot_key for value in references)


async def _batch(model, group, documents, announcement, usage):
    from .extractor import FatalModelError
    class RejectedBatch(RuntimeError):
        pass

    def decode(content):
        try:
            return decode_batch_response(content, group)
        except BatchResponseError as exc:
            raise RejectedBatch(str(exc)) from exc

    _count(model, usage, 'batch_groups')
    started = time.perf_counter()
    try:
        decoded = await model._call(build_batch_payload(group), [d.file_id for d in group],
            decoder=decode, system_override=batch_system_prompt(model.system_prompt))
        prepared = []
        for source in group:
            doc = documents[source.file_id]
            result = decoded[source.file_id]
            result.extraction_method = f'model:{model.settings.deepseek_model}'
            _ground(result, doc, announcement)
            if any(lot_identity(lot.lot_name) != source.lot_key for lot in result.package_records()):
                raise RejectedBatch('批处理包归属不一致，回退单文件')
            if not _batch_grounded(result, doc):
                raise RejectedBatch('批处理结果无法逐文件核验，回退单文件')
            prepared.append(result)
        for source, result in zip(group, prepared):
            await _save(model, usage, documents[source.file_id].get('checkpoint_key'), [result])
        _count(model, usage, 'batch_documents', len(group))
        return prepared
    except FatalModelError:
        if model.fatal_error or (not model.cache_only and model.settings.model_network_enabled):
            raise
        # A cache-only batch miss can use the original cached single requests.
        _count(model, usage, 'batch_fallbacks')
        return None
    except Exception as exc:
        _count(model, usage, 'batch_fallbacks')
        model.batch_disabled_reason = f'{type(exc).__name__}: {str(exc)[:160]}'
        return None
    finally:
        if usage is not None:
            usage['model_seconds'] += round(time.perf_counter() - started, 4)


async def extract_documents(model, files: list[Path], usage) -> ProjectExtract:
    from . import extractor as original
    parsed, eligible, errors = [], [], []
    names = [f'doc-{index + 1}:{path.name}' for index, path in enumerate(files)]
    announcement_context = ''
    for index, path in enumerate(files):
        source = 'announcement' if index == 0 and path.suffix.lower() in {'.html', '.htm'} else 'attachment'
        started = time.perf_counter()
        try:
            text = await asyncio.to_thread(original.parse_file, path)
            parsed.append(text)
            errors.extend(f'{names[index]}: {line[len(PARSE_WARNING_PREFIX):]}' for line in text.splitlines()
                          if line.startswith(PARSE_WARNING_PREFIX))
        except Exception as exc:
            errors.append(f'{names[index]}: {type(exc).__name__}: {str(exc)[:180]}')
            continue
        finally:
            if usage is not None:
                usage['parse_seconds'] += round(time.perf_counter() - started, 4)
        if source == 'announcement':
            announcement_context = text[:1600]
        role, reason = ('announcement', '') if source == 'announcement' else document_role(path.name, text)
        if source == 'attachment' and model.settings.extract_blank_templates and not reason:
            blank = classify_blank_template(path.name, text)
            if blank:
                role, reason = 'empty-template', blank
                _count(model, usage, 'blank_templates')
        meta = {'file': names[index], 'role': role, 'characters': len(text), 'skipped_reason': reason}
        if usage is not None:
            usage['documents'].append(meta)
        if not reason:
            eligible.append({'path': path, 'source': source, 'text': text, 'label': names[index], 'meta': meta})
    results, announcement = [], None
    if eligible and eligible[0]['source'] == 'announcement':
        pieces, failures = await _single(model, eligible.pop(0), names, announcement_context, None, usage)
        results.extend(pieces)
        errors.extend(failures)
        if pieces:
            announcement = original.merge_results(pieces)
    documents, route = {}, []
    known = announcement.package_records() if announcement is not None else []
    lot_key = lot_identity(known[0].lot_name) if len(known) == 1 and announcement.processing_status == 'complete' else ''
    for doc in eligible:
        if not model.api_key and not model.cache_only:
            continue
        if model.settings.extract_standard_tables:
            direct = extract_standard_table(doc['text'], doc['label'], announcement)
            if direct is not None:
                doc['direct'] = [direct]
                doc['meta']['route'] = 'standard-table'
                _count(model, usage, 'standard_tables')
        can_batch = bool(model.settings.extract_batch_attachments and lot_key and not model.batch_disabled_reason
                         and _same_lot(doc, lot_key) and 'direct' not in doc
                         and re.search(r'品牌|型号|单价|数量|投标人|报价|得分', doc['text']))
        if can_batch:
            prefix, legacy, chunk_prefix, document_key = _prompts(model, doc, names, announcement_context, announcement)
            parts = original.chunk_text(doc['text'], max(128, model.settings.max_llm_chars - len(chunk_prefix)))
            can_batch = len(parts) == 1
            if can_batch and document_key:
                doc['checkpoint_key'] = digest([document_key, parts[0]])
                cached = await asyncio.to_thread(model.checkpoints.load, doc['checkpoint_key'])
                if cached is not None:
                    doc['direct'] = _consume_checkpoint(model, usage, cached)
                    doc['meta']['route'] = 'checkpoint'
                    can_batch = False
        documents[doc['label']] = doc
        route.append(BatchDocument(doc['label'], doc['path'].name, doc['text'], files[0].stem,
                                   lot_key if can_batch else ''))
    for group in group_small_documents(route):
        batch_results = None
        if len(group) > 1 and not model.batch_disabled_reason:
            batch_results = await _batch(model, group, documents, announcement, usage)
        if batch_results is not None:
            results.extend(batch_results)
            for source in group:
                documents[source.file_id]['meta']['route'] = 'batch'
            continue
        for source in group:
            doc = documents[source.file_id]
            if 'direct' in doc:
                results.extend(doc['direct'])
            else:
                pieces, failures = await _single(model, doc, names, announcement_context, announcement, usage)
                results.extend(pieces)
                errors.extend(failures)
    if not parsed:
        raise ValueError('没有可解析的正文或附件；' + '; '.join(errors[:5]))
    if not results:
        result = original.heuristic_extract('\n'.join(parsed))
        result.processing_status = 'fallback'
        result.warnings.extend(errors or ['未配置模型；仅返回离线项目字段，不代表七字段抽取完成'])
        return result
    result = original.merge_results(results)
    result.warnings.extend(errors)
    result.processing_status = 'partial' if errors or result.processing_status == 'partial' else 'complete'
    original.validate_result(result)
    return result
