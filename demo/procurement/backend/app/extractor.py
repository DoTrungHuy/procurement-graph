from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from pydantic import ValidationError

from .config import ROOT, settings
from .checkpoints import PieceCheckpoints, digest
from .document_policy import document_role
from .evidence import align_attachment_packages, ground_evidence
from .models import BidExtract, ItemExtract, LotExtract, ProjectExtract
from .normalization import dedupe_key, lot_identity, money_to_yuan, normalize_org, number_value
from .parsers import PARSE_WARNING_PREFIX, parse_file
from .reconciliation import reconcile_items
from .prompts import system_prompt, user_prompt
from .model_settings import current_settings
from .model_transport import read_model_response

PIPELINE_VERSION = 'package-v2.13'
_usage: ContextVar[dict | None] = ContextVar('extraction_usage', default=None)
_replay_context: ContextVar[dict | None] = ContextVar('replay_context', default=None)


class OutputTruncated(RuntimeError):
    pass


class FatalModelError(RuntimeError):
    pass


def chunk_text(text: str, limit: int) -> list[str]:
    if limit < 128:
        raise ValueError('分块长度至少为 128')
    chunks = []
    start = 0
    while start < len(text):
        end = min(start+limit,len(text))
        if end < len(text):
            boundary = max(text.rfind('\n',start,end),text.rfind('。',start,end))
            if boundary > start+limit//2:
                end = boundary+1
        chunks.append(text[start:end])
        start = end
    return chunks or ['']


class DeepSeekExtractor:
    def __init__(self, cache_only: bool = False, configuration=None) -> None:
        self.settings = current_settings(configuration if configuration is not None else settings)
        self.api_key = self.settings.deepseek_api_key
        self.cache_only = cache_only
        self.client: httpx.AsyncClient | None = None
        self.calls = 0
        self.system_prompt = system_prompt(self.settings.extraction_strategy)
        self.fatal_error = ''
        self.total_usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        self.response_cache_hits = 0
        self.response_cache_keys: set[str] = set()
        self.document_cache_hits = 0
        self.document_cache_writes = 0
        self.checkpoints = PieceCheckpoints(self.settings.document_cache_dir)
        self.request_gate = asyncio.Semaphore(self.settings.model_concurrency)
        self.optimization_counts = {'blank_templates': 0, 'standard_tables': 0, 'batch_groups': 0,
                                    'batch_documents': 0, 'batch_fallbacks': 0, 'batch_model_calls': 0}
        self.batch_disabled_reason = ''

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    def _payload(self, text, names, system_override=None):
        settings = self.settings
        payload = {'model': settings.deepseek_model, 'messages': [
            {'role': 'system', 'content': system_override or self.system_prompt},
            {'role': 'user', 'content': user_prompt(text, names)}],
            'response_format': {'type': 'json_object'}, 'temperature': 0.1,
            'max_tokens': settings.model_max_tokens, 'stream': False}
        if settings.deepseek_model.startswith('deepseek-'):
            payload['thinking'] = {'type': 'enabled' if settings.model_thinking else 'disabled'}
        return payload

    def _chunk_prefix(self, text, prefix, legacy, names, label):
        mode = self.settings.model_replay_chunking
        if not self.cache_only or mode == 'current':
            return prefix
        if mode != 'auto' or self.settings.extraction_strategy == 'rag':
            return legacy
        directories = [Path(self.settings.model_cache_dir),
                       *(Path(p) for p in self.settings.model_cache_read_dirs), ROOT / 'data/demo/cache']
        # Select an exact saved request partition, never an old prompt or a
        # previously exported prediction. Mixed historical runs can use both.
        def present(part):
            for source, source_names in ((prefix, [label]), (legacy, names)):
                payload = self._payload(source + part, source_names)
                key = hashlib.sha256((self.settings.deepseek_base_url + json.dumps(payload, sort_keys=True, ensure_ascii=False)).encode()).hexdigest() + '.json'
                if any((directory / key).is_file() for directory in directories):
                    return True
            return False
        for candidate in dict.fromkeys([prefix, legacy]):
            if all(present(part) for part in chunk_text(text, max(128, self.settings.max_llm_chars - len(candidate)))):
                return candidate
        return prefix

    def _document_key(self, text: str, prefix: str, chunk_prefix: str, names: list[str]) -> str | None:
        if self.checkpoints.directory is None:
            return None
        settings = self.settings
        identity = {'pipeline': PIPELINE_VERSION, 'system': self.system_prompt,
                    'model': settings.deepseek_model, 'base_url': settings.deepseek_base_url,
                    'max_tokens': settings.model_max_tokens, 'thinking': settings.model_thinking,
                    'strategy': settings.extraction_strategy, 'prefix': prefix,
                    'chunk_prefix': chunk_prefix, 'max_chars': settings.max_llm_chars,
                    'source': hashlib.sha256(text.encode()).hexdigest(),
                    'require_free': settings.model_require_free,
                    'optimizations': [settings.extract_blank_templates, settings.extract_standard_tables,
                                      settings.extract_batch_attachments],
                    'legacy_names': names if self.cache_only else []}
        if settings.extraction_strategy == 'rag':
            from .retrieval import KNOWLEDGE
            try:
                identity['knowledge'] = hashlib.sha256(KNOWLEDGE.read_bytes()).hexdigest()
            except OSError:
                return None
        return digest(identity)

    async def _call(self, text: str, names: list[str], *, decoder=None, system_override: str | None = None,
                    cache_only_override: bool | None = None):
        settings = self.settings
        cache_only = self.cache_only if cache_only_override is None else cache_only_override
        if not self.api_key and not cache_only:
            raise RuntimeError('未配置抽取模型')
        if self.fatal_error:
            raise FatalModelError(self.fatal_error)
        prompt_text = text
        if settings.extraction_strategy == 'rag':
            from .retrieval import augment
            prompt_text, knowledge_ids, knowledge_hash = augment(text)
            if _usage.get() is not None:
                _usage.get().setdefault('retrieval', []).append({'knowledge_ids':knowledge_ids,'sha256':knowledge_hash})
        payload = self._payload(prompt_text, names, system_override)
        cache = None
        cached = None
        cached_path = None
        if settings.model_cache_dir:
            directory = Path(settings.model_cache_dir).resolve()
            cache = directory / (hashlib.sha256((settings.deepseek_base_url + json.dumps(payload, sort_keys=True, ensure_ascii=False)).encode()).hexdigest() + '.json')
            directories = list(dict.fromkeys([directory, *(Path(p).resolve() for p in settings.model_cache_read_dirs),
                                              ROOT/'data/demo/cache']))
            for location in directories:
                candidate = location/cache.name
                if candidate.is_file():
                    try:
                        cached = json.loads(candidate.read_text('utf-8'))
                        cached_path = candidate
                        break
                    except (ValueError,OSError):
                        pass
            # Reconstruct legacy document context only with the CURRENT
            # system prompt. Never fall back to old extraction rules.
            context = _replay_context.get()
            if self.cache_only and settings.extraction_strategy != 'rag' and cached is None and context and text.startswith(context['prefix']):
                legacy = json.loads(json.dumps(payload))
                legacy['messages'][1]['content'] = user_prompt(context['legacy_prefix']+text[len(context['prefix']):],context['names'])
                name = hashlib.sha256((settings.deepseek_base_url+json.dumps(legacy,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()+'.json'
                for location in directories:
                    candidate = location/name
                    if candidate.is_file():
                        try:
                            cached = json.loads(candidate.read_text('utf-8'))
                            cached_path = candidate
                            if _usage.get() is not None:
                                _usage.get()['historical_context_cache_hits'] += 1
                            break
                        except (ValueError,OSError):
                            pass
        last = '模型未返回有效 JSON'
        for attempt in range(3):
            try:
                usage = _usage.get()
                from_cache = cached is not None
                if from_cache:
                    envelope, cached = cached, None
                    if cached_path is not None:
                        self.response_cache_keys.add(cached_path.name)
                    self.response_cache_hits += 1
                    if usage is not None:
                        usage['response_cache_hits'] += 1
                else:
                    if cache_only:
                        raise FatalModelError('历史响应缓存未命中当前规则，请使用模型抽取重新生成结果；回放模式禁止联网，未产生模型请求')
                    if not settings.model_network_enabled:
                        raise FatalModelError('模型网络调用已暂停；请先确定样本与预算，再明确启用 MODEL_NETWORK_ENABLED')
                    if self.client is None:
                        loopback = httpx.URL(settings.deepseek_base_url).host in {'127.0.0.1', 'localhost', '::1'}
                        self.client = httpx.AsyncClient(timeout=httpx.Timeout(180,connect=20), trust_env=not loopback)
                    async with self.request_gate:
                        if self.fatal_error:
                            raise FatalModelError(self.fatal_error)
                        self.calls += 1
                        if decoder is not None:
                            self.optimization_counts['batch_model_calls'] += 1
                        if usage is not None:
                            usage['requests'] += 1
                            if decoder is not None:
                                usage.setdefault('optimization_counts', {}).setdefault('batch_model_calls', 0)
                                usage['optimization_counts']['batch_model_calls'] += 1
                        response = await self.client.post(settings.deepseek_base_url+'/chat/completions',
                            headers={'Authorization':'Bearer '+self.api_key},json=payload)
                    if response.status_code in {429,500,502,503,504}:
                        last = f'模型服务 HTTP {response.status_code}'
                        if attempt < 2:
                            await asyncio.sleep(2**attempt)
                            continue
                    if response.status_code >= 400:
                        if response.status_code in {401, 402, 403, 404}:
                            self.fatal_error = f'模型服务 HTTP {response.status_code}；请核对模型、凭据或余额'
                            raise FatalModelError(self.fatal_error)
                        raise RuntimeError(f'模型服务 HTTP {response.status_code}')
                    envelope = read_model_response(response)
                    if settings.model_require_free:
                        try:
                            billing = envelope.get('usage') or {}
                            credit = Decimal(str(billing.get('credit')))
                            free = credit.is_finite() and credit == 0
                        except InvalidOperation:
                            free = False
                        if not free:
                            self.fatal_error = '免费模式下上游未确认零积分消耗，已停止后续模型请求'
                            raise FatalModelError(self.fatal_error)
                    for key in self.total_usage:
                        value = int(envelope.get('usage', {}).get(key, 0) or 0)
                        self.total_usage[key] += value
                        if usage is not None:
                            usage[key] += value
                    if cache is not None:
                        temporary = cache.with_name(cache.name + '.' + uuid.uuid4().hex + '.part')
                        try:
                            cache.parent.mkdir(parents=True, exist_ok=True)
                            temporary.write_text(json.dumps(envelope, ensure_ascii=False), encoding='utf-8')
                            temporary.replace(cache)
                        except OSError:
                            pass
                        finally:
                            if temporary.exists() and temporary.resolve().is_relative_to(cache.parent):
                                temporary.unlink()
                if usage is not None and envelope.get('model'):
                    usage['response_models'] = sorted(set(usage['response_models']) | {envelope['model']})
                choice = envelope['choices'][0]
                if choice.get('finish_reason') == 'length':
                    raise OutputTruncated('模型输出达到上限，需要缩短文本重新提取')
                content = choice['message']['content']
                if decoder is not None:
                    return decoder(content)
                data = json.loads(content)
                data['extraction_method'] = f'model:{settings.deepseek_model}'
                return ProjectExtract.model_validate(data)
            except ValidationError as exc:
                issues = ['.'.join(map(str, e['loc'])) + ':' + e['type'] for e in exc.errors(include_input=False)]
                last = '结构字段不符合约定: ' + '; '.join(issues[:8])
                if attempt < 2:
                    payload['messages'].append({'role': 'assistant', 'content': content})
                    payload['messages'].append({'role': 'user', 'content': last + '。只修正JSON字段类型，未知字符串为空，未知列表为空数组；保留原文事实。'})
            except (httpx.TimeoutException,httpx.NetworkError,json.JSONDecodeError,KeyError,TypeError,ValueError) as exc:
                last = '模型调用或结构校验失败: '+type(exc).__name__
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(last)

    async def extract(self, files: list[Path]) -> ProjectExtract:
        settings = self.settings
        usage = {'requested_model': settings.deepseek_model, 'thinking': settings.model_thinking,
                 'strategy': settings.extraction_strategy,
                 'requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
                 'response_cache_hits': 0,
                 'historical_context_cache_hits': 0, 'cache_only': self.cache_only,
                 'document_cache_hits': 0, 'document_cache_writes': 0,
                 'optimization_counts': {},
                 'documents': [],
                 'response_models': [], 'parse_seconds': 0.0, 'model_seconds': 0.0}
        token = _usage.set(usage)
        replay_token = _replay_context.set(None)
        started = time.perf_counter()
        try:
            result = await self._extract_files(files)
            usage['elapsed_seconds'] = round(time.perf_counter() - started, 4)
            result.model_usage = usage
            return result
        finally:
            _usage.reset(token)
            _replay_context.reset(replay_token)

    async def _extract_piece(self, prefix: str, part: str, names: list[str], depth: int = 0) -> list[ProjectExtract]:
        try:
            return [await self._call(prefix + part, names)]
        except OutputTruncated:
            if depth >= 3 or len(part) < 256:
                raise
            results = []
            for piece in chunk_text(part, max(128, len(part) // 2)):
                results.extend(await self._extract_piece(prefix, piece, names, depth + 1))
            return results

    async def _extract_files(self, files: list[Path]) -> ProjectExtract:
        if any((self.settings.extract_blank_templates, self.settings.extract_standard_tables,
                self.settings.extract_batch_attachments)):
            from .extraction_plan import extract_documents
            return await extract_documents(self, files, _usage.get())
        return await self._extract_files_original(files)

    async def _extract_files_original(self, files: list[Path]) -> ProjectExtract:
        settings = self.settings
        results = []
        errors = []
        parsed = []
        package_sources = {}
        names = [f'doc-{index+1}:{path.name}' for index,path in enumerate(files)]
        announcement_context = ''
        announcement_result = None
        for index,path in enumerate(files):
            source = 'announcement' if index == 0 and path.suffix.lower() in {'.html','.htm'} else 'attachment'
            parse_started = time.perf_counter()
            try:
                text = await asyncio.to_thread(parse_file,path)
                parsed.append(text)
                package_sources[names[index]] = text
                errors.extend(f'{names[index]}: {line[len(PARSE_WARNING_PREFIX):]}'
                              for line in text.splitlines() if line.startswith(PARSE_WARNING_PREFIX))
            except Exception as exc:
                errors.append(f'{names[index]}: {type(exc).__name__}: {str(exc)[:180]}')
                continue
            finally:
                if _usage.get() is not None:
                    _usage.get()['parse_seconds'] += round(time.perf_counter() - parse_started, 4)
            if source == 'announcement':
                announcement_context = text[:1600]
            role, skip_reason = ('announcement','') if source == 'announcement' else document_role(path.name,text)
            if _usage.get() is not None:
                _usage.get()['documents'].append({'file':names[index],'role':role,'characters':len(text),'skipped_reason':skip_reason})
            if skip_reason:
                continue
            if not self.api_key and not self.cache_only:
                continue
            context = announcement_context
            if source == 'attachment' and announcement_result is not None:
                context = json.dumps({'project_name':announcement_result.project_name,'project_code':announcement_result.project_code,
                    'purchaser':announcement_result.purchaser,'packages':[{'lot_name':l.lot_name,
                        'winners':[b.bidder for b in l.bids if b.is_winner and b.eligible]} for l in announcement_result.package_records()]},ensure_ascii=False)
            prefix = (f'[FILE:{names[index]} SOURCE:{source}]\n'
                      f'[上下文，仅用于理解字段，不重复抽取；采购包必须由当前段落证明]\n公告：{context}\n文件开头：{text[:1200]}\n[当前段落]\n')
            legacy_prefix = (f'[FILE:{names[index]} SOURCE:{source}]\n'
                      f'[上下文，仅用于理解字段，不重复抽取；采购包必须由当前段落证明]\n公告：{announcement_context}\n文件开头：{text[:1200]}\n[当前段落]\n')
            _replay_context.set({'prefix':prefix,'legacy_prefix':legacy_prefix,'names':names})
            chunk_prefix = self._chunk_prefix(text, prefix, legacy_prefix, names, names[index])
            # Checkpoints follow current grounding rules, the entire source,
            # announcement context and exact chunking. Never use an old final
            # result to bypass a changed prompt, source, model or RAG corpus.
            document_key = self._document_key(text, prefix, chunk_prefix, names)
            for part in chunk_text(text,max(128,settings.max_llm_chars-len(chunk_prefix))):
                checkpoint_key = digest([document_key, part]) if document_key else None
                try:
                    cached_piece = await asyncio.to_thread(self.checkpoints.load, checkpoint_key) if checkpoint_key else None
                    if cached_piece is not None:
                        pieces, metadata = cached_piece
                        self.document_cache_hits += 1
                        if _usage.get() is not None:
                            usage = _usage.get()
                            usage['document_cache_hits'] += 1
                            usage['response_models'] = sorted(set(usage['response_models']) | set(metadata.get('response_models', [])))
                            usage.setdefault('retrieval', []).extend(metadata.get('retrieval', []))
                        results.extend(pieces)
                        continue
                    retrieval_start = len((_usage.get() or {}).get('retrieval', []))
                    model_started = time.perf_counter()
                    try:
                        pieces = await self._extract_piece(prefix, part, [names[index]])
                    finally:
                        if _usage.get() is not None:
                            _usage.get()['model_seconds'] += round(time.perf_counter() - model_started, 4)
                    for result in pieces:
                        for lot in result.package_records():
                            for item in lot.items:
                                item.source = source
                                item.source_file = names[index]
                                item.source_location = item.source_location or item.line_id
                            for bid in lot.bids:
                                bid.source_file = names[index]
                        ground_evidence(result, text)
                        if source == 'attachment' and announcement_result is not None:
                            align_attachment_packages(result,announcement_result,path.name,text)
                        results.append(result)
                    usage = _usage.get() or {}
                    metadata = {'response_models': usage.get('response_models', []),
                                'retrieval': usage.get('retrieval', [])[retrieval_start:]}
                    if checkpoint_key and await asyncio.to_thread(self.checkpoints.save, checkpoint_key, pieces, metadata):
                        self.document_cache_writes += 1
                        if _usage.get() is not None:
                            _usage.get()['document_cache_writes'] += 1
                except FatalModelError:
                    raise
                except Exception as exc:
                    errors.append(f'{names[index]}: {type(exc).__name__}: {str(exc)[:180]}')
            if source == 'announcement' and results:
                announcement_result = merge_results(results)
        if not parsed:
            raise ValueError('没有可解析的正文或附件；'+ '; '.join(errors[:5]))
        if not results:
            fallback = heuristic_extract('\n'.join(parsed))
            fallback.processing_status = 'fallback'
            fallback.warnings.extend(errors or ['未配置模型；仅返回离线项目字段，不代表七字段抽取完成'])
            return fallback
        result = merge_results(results)
        from .package_identity import resolve_package_labels
        resolve_package_labels(result, package_sources)
        result.warnings.extend(errors)
        result.processing_status = 'partial' if errors or result.processing_status == 'partial' else 'complete'
        validate_result(result)
        return result


def _fill(target, source, fields: list[str], warnings: list[str], label: str) -> None:
    def missing(value):
        return value is None or value == '' or value == [] or value == {}
    for field in fields:
        if field == 'model_key':
            continue
        old, new = getattr(target,field), getattr(source,field)
        if missing(old) and not missing(new):
            setattr(target,field,new)
        elif not missing(old) and not missing(new) and old != new:
            warnings.append(f'{label}字段冲突: {field}，保留先前原文值待复核')
    if isinstance(target, ItemExtract):
        target.align_model_key()


def merge_results(results: list[ProjectExtract]) -> ProjectExtract:
    if not results:
        return ProjectExtract(processing_status='failed')
    merged = ProjectExtract(extraction_method=results[0].extraction_method)
    lots: dict[str,LotExtract] = {}
    item_positions = {}
    bids = {}
    for result in results:
        if result.processing_status in {'partial','failed'}:
            merged.processing_status = 'partial'
        _fill(merged,result,['project_name','project_code','purchaser','announcement_date','budget_amount','awarded_amount','no_target_reason'],merged.warnings,'项目')
        for part in result.package_records():
            if not part.lot_name and not part.items and not part.bids:
                continue
            key = lot_identity(part.lot_name) or f'unassigned:{len(lots)+1}'
            if key in lots and lots[key].lot_name != part.lot_name:
                merged.warnings.append(f'采购包编号别名已对齐：{lots[key].lot_name} / {part.lot_name}')
                descriptions = [re.search(r'[（(]([^（）()]+)[）)]', name) for name in (lots[key].lot_name, part.lot_name)]
                if all(descriptions) and descriptions[0].group(1) != descriptions[1].group(1):
                    merged.processing_status = 'partial'
                    merged.warnings.append('同一包编号出现不同描述，保留原标签待核验')
            lot = lots.setdefault(key,LotExtract(lot_name=part.lot_name,lot_key=key))
            _fill(lot,part,['budget_amount','awarded_amount'],merged.warnings,'采购包')
            for item in part.items:
                item = item.model_copy(deep=True)
                # Only a source row identity can justify merging. Values alone cannot.
                page_only = re.fullmatch(r'\[?PAGE:\s*\d+\]?', item.line_id, re.I)
                item_key = (lot.lot_key,item.source_file,item.line_id,normalize_org(item.supplier)) if item.source_file and item.line_id and not page_only else None
                bucket = item_positions.get(item_key, []) if item_key else []
                compatible = [previous for previous in bucket if all(
                    not getattr(previous, field) or not getattr(item, field)
                    or dedupe_key(getattr(previous, field)) == dedupe_key(getattr(item, field))
                    for field in ('product_name', 'model', 'brand', 'category', 'quantity_unit'))
                    and all(getattr(previous, field) is None or getattr(item, field) is None
                            or getattr(previous, field) == getattr(item, field)
                            for field in ('quantity', 'unit_price', 'total_price'))]
                if len(compatible) == 1:
                    _fill(compatible[0],item,list(ItemExtract.model_fields),merged.warnings,'标的')
                else:
                    lot.items.append(item)
                    if item_key:
                        if bucket:
                            merged.processing_status = 'partial'
                            merged.warnings.append('同一原文行对应多件或身份不明确的货物，保留独立记录，不按共同定位强行合并，需核验')
                        item_positions.setdefault(item_key, []).append(item)
            for bid in part.bids:
                bid = bid.model_copy(deep=True)
                bid.bidder = normalize_org(bid.bidder)
                if not bid.bidder:
                    continue
                key = (lot.lot_key,bid.bidder,bid.consortium_id)
                if key in bids:
                    current = bids[key]
                    _fill(current,bid,['bid_amount','score','rank','evidence','source_file','review_reason','non_winning_reason','evaluation_scores'],merged.warnings,'投标')
                    current.is_winner = current.is_winner or bid.is_winner
                    for field in ['review_status','qualification_status','compliance_status']:
                        if getattr(bid,field) == '未通过' or getattr(current,field) == '未披露':
                            setattr(current,field,getattr(bid,field))
                else:
                    bids[key] = bid
                    lot.bids.append(bid)
        merged.warnings.extend(result.warnings)
    merged.lots = list(lots.values())
    if any(not lot.lot_name for lot in merged.lots) and len(merged.lots) > 1:
        merged.processing_status = 'partial'
        merged.warnings.append('存在未明确归属的采购包片段，已分别保留，禁止自动合成同包关系；需核验包归属')
    # Compatibility for callers that inspect a single flat notice.
    if len(merged.lots) == 1:
        merged.items = merged.lots[0].items
        merged.bids = merged.lots[0].bids
    return merged


def validate_result(result: ProjectExtract) -> None:
    result.collect_numeric_warnings()
    result.purchaser = normalize_org(result.purchaser)
    for lot in result.assign_lot_keys():
        lot.lot_name = lot.lot_name.strip()
        winners = {normalize_org(b.bidder) for b in lot.bids if b.is_winner and b.eligible}
        for bid in lot.bids:
            bid.bidder = normalize_org(bid.bidder)
            bid.lot_name = lot.lot_name
            if bid.is_winner and not bid.eligible:
                result.warnings.append('投标记录同时标记中标和审查失败，已排除有效关系，待复核')
        for item in lot.items:
            item.align_model_key()
            item.derived_values = {}
            item.lot_name = lot.lot_name
            item.supplier = normalize_org(item.supplier)
            if not item.supplier and len(winners) == 1:
                item.supplier = next(iter(winners))
            if item.quantity is not None and (item.quantity <= 0 or item.quantity != int(item.quantity)):
                result.warnings.append('数量不是正整数，保留原值待核验')
            if any(value is not None and value < 0 for value in (item.unit_price,item.total_price)):
                result.warnings.append('原文金额为负值，保留原值待核验')
            if item.unit_price is None and item.total_price is not None and item.quantity and item.quantity > 0:
                item.derived_values['unit_price'] = item.total_price/item.quantity
            if item.total_price is None and item.unit_price is not None and item.quantity is not None:
                item.derived_values['total_price'] = item.unit_price*item.quantity
            if all(x is not None for x in (item.unit_price,item.quantity,item.total_price)):
                expected = item.unit_price*item.quantity
                if abs(expected-item.total_price) > max(abs(expected),abs(item.total_price),1)*0.02:
                    result.warnings.append(f'标的“{item.product_name}”金额不一致，保留原值')
        combined, uncertain = reconcile_items(lot)
        if combined:
            result.warnings.append(f'跨文件重复明细按原文品目号、商品型号、供应商和已披露数量金额一对一合并 {combined} 行；保留全部来源')
        if uncertain:
            result.processing_status = 'partial'
            result.warnings.append('跨文件同品目同名商品存在型号/字段冲突或数值依据不足，保留独立明细待复核；金额可能重复，不宜直接汇总')
    if len(result.lots) == 1:
        result.items, result.bids = result.lots[0].items, result.lots[0].bids
    result.warnings = list(dict.fromkeys(result.warnings))


def _first(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern,text,re.I)
        if match:
            return match.group(1).strip(' ：:|\t')
    return ''


def heuristic_extract(text: str) -> ProjectExtract:
    result = ProjectExtract(
        project_name=_first([r'项目名称\s*[：:|]\s*([^\n|]{3,160})'],text),
        project_code=_first([r'项目编号\s*[：:|]\s*([\w\-（）()]+)',r'采购编号\s*[：:|]\s*([\w\-（）()]+)'],text),
        purchaser=normalize_org(_first([r'采购人(?:信息|名称)?\s*[：:|]\s*([^\n|]{3,100})',r'采购单位\s*[：:|]\s*([^\n|]{3,100})'],text)),
        extraction_method='heuristic-fallback',processing_status='fallback')
    result.budget_amount = money_to_yuan(_first([r'预算金额\s*[：:|]\s*([^\n|]{1,40})'],text))
    result.awarded_amount = money_to_yuan(_first([r'(?:中标|成交)(?:总)?金额\s*[：:|]\s*([^\n|]{1,40})'],text))
    for match in re.finditer(r'(?:中标|成交)供应商(?:名称)?\s*[：:|]\s*([^\n|]{3,100})',text):
        result.bids.append(BidExtract(bidder=normalize_org(match.group(1)),is_winner=True,evidence=match.group(0)[:120]))
    result.warnings.append('离线兜底未完成标的明细抽取；需配置赛事允许的模型重跑')
    return result
