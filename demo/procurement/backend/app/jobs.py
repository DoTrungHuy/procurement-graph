from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from .config import settings
from .database import connect, one, rows, upsert_notice
from .extractor import DeepSeekExtractor, PIPELINE_VERSION
from .ingestion import discover_uploads, input_digest, notice_metadata
from .models import JobState, ProjectExtract
from .parsers import PARSER_VERSION, PARSE_WARNING_PREFIX, expand_archives, parse_file
from .prompts import SYSTEM_PROMPT

TERMINAL = {'completed','completed_with_warnings','completed_with_errors','failed','interrupted'}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_job(job_id: str) -> JobState | None:
    record = one('SELECT * FROM jobs WHERE id=?',(job_id,))
    if not record:
        return None
    record['errors'] = json.loads(record['errors'])
    record['metrics'] = json.loads(record.get('metrics') or '{}')
    record['finished'] = record['status'] in TERMINAL
    return JobState.model_validate(record)


def create_job(job_id: str, paths: list[Path], workdir: Path, dataset_id: str='default',
               groups: list[list[Path]] | None=None, mode: str='extract') -> JobState:
    manifest = {'uploads':[str(p) for p in paths],'groups':[[str(p) for p in group] for group in groups] if groups is not None else None}
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        # One active batch per service keeps CPU/OCR and quota bounded on 2vCPU/4GB.
        if c.execute("SELECT 1 FROM jobs WHERE status IN ('queued','processing')").fetchone():
            raise ValueError('已有处理中的批次，请等待结束后再提交')
        c.execute('INSERT INTO jobs(id,dataset_id,status,manifest,workdir,mode) VALUES(?,?,?,?,?,?)',
                  (job_id,dataset_id,'queued',json.dumps(manifest),str(workdir),mode))
    return get_job(job_id)


def interrupt_running_jobs() -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET status='interrupted',completed_at=? WHERE status IN ('queued','processing')",(timestamp(),))


async def save_uploads(files: list[UploadFile]) -> tuple[str,list[Path],Path]:
    job_id = uuid.uuid4().hex
    workdir = settings.upload_dir/job_id
    workdir.mkdir(parents=True,exist_ok=False)
    saved = []
    total = 0
    try:
        for index,upload in enumerate(files):
            name = Path((upload.filename or 'upload.bin').replace('\\','/')).name
            if name in {'','.','..'}:
                raise ValueError('上传文件名不正确')
            target = workdir/str(index)/name
            target.parent.mkdir()
            size = 0
            with target.open('wb') as output:
                while chunk := await upload.read(1024**2):
                    size += len(chunk); total += len(chunk)
                    if size > settings.max_upload_bytes or total > settings.max_expanded_bytes:
                        raise ValueError('上传体积超出服务配置限制')
                    if shutil.disk_usage(workdir).free < len(chunk)+128*1024**2:
                        raise ValueError('磁盘空间不足，上传已停止')
                    await asyncio.to_thread(output.write,chunk)
            saved.append(target)
            await upload.close()
        return job_id,saved,workdir
    except BaseException:
        shutil.rmtree(workdir,ignore_errors=True)
        raise


def _record(job_id: str, notice_uuid: str, status: str, result: dict | None, errors: list[str], elapsed: float) -> None:
    success = status in {'complete','cached','parsed'}
    partial = status in {'partial','fallback'}
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO job_documents(job_id,notice_uuid,status,result_json,errors,elapsed_seconds) VALUES(?,?,?,?,?,?)',
                  (job_id,notice_uuid,status,json.dumps(result,ensure_ascii=False) if result is not None else None,
                   json.dumps(errors,ensure_ascii=False),round(elapsed,4)))
        previous = json.loads(c.execute('SELECT errors FROM jobs WHERE id=?',(job_id,)).fetchone()['errors'])
        previous.extend(f'{notice_uuid}: {error}' for error in errors)
        c.execute('UPDATE jobs SET completed=completed+1,succeeded=succeeded+?,partial=partial+?,failed=failed+?,skipped=skipped+?,errors=? WHERE id=?',
                  (int(success),int(partial),int(not success and not partial),int(status=='cached'),json.dumps(previous[:200],ensure_ascii=False),job_id))


async def process_job(job_id: str, uploads: list[Path] | None=None, workdir: Path | None=None) -> None:
    record = one('SELECT * FROM jobs WHERE id=?',(job_id,))
    if not record:
        return
    workdir = Path(record['workdir'])
    workdir.mkdir(parents=True,exist_ok=True)
    manifest = json.loads(record['manifest'])
    started = time.perf_counter()
    extractor = DeepSeekExtractor(configuration=settings)
    model_settings = extractor.settings
    extractor.cache_only = record['mode']=='replay'
    discovery_errors = []
    try:
        with connect() as c:
            c.execute("UPDATE jobs SET status='processing',started_at=? WHERE id=?",(timestamp(),job_id))
        if manifest['groups'] is not None:
            groups = [[Path(p) for p in group] for group in manifest['groups']]
        else:
            groups = await asyncio.to_thread(discover_uploads,[Path(p) for p in manifest['uploads']],workdir/'discovery',discovery_errors)
        if not groups:
            raise ValueError('未发现可处理公告；'+ '; '.join(discovery_errors[:5]))
        if len({group[0].stem for group in groups}) != len(groups):
            raise ValueError('输入包含重复公告标识，无法可靠保存批次快照；请分开处理不同版本')
        with connect() as c:
            c.execute('UPDATE jobs SET total=?,errors=? WHERE id=?',(len(groups),json.dumps(discovery_errors[:200],ensure_ascii=False),job_id))
        # Parsing and model requests have separate bounds. A slow attachment
        # must not occupy the only notice slot able to issue a model request.
        notice_limit = settings.notice_concurrency or min(8, settings.model_concurrency + settings.parse_concurrency)
        gate = asyncio.Semaphore(notice_limit)
        pipeline = PIPELINE_VERSION+PARSER_VERSION+hashlib.sha256(extractor.system_prompt.encode()).hexdigest()+json.dumps({
            'model':model_settings.deepseek_model,'base_url':model_settings.deepseek_base_url,'model_enabled':bool(extractor.api_key),
            'max_chars':settings.max_llm_chars,'pdf_pages':settings.max_pdf_pages,
            'optimizations':[settings.extract_blank_templates, settings.extract_standard_tables, settings.extract_batch_attachments],
            'max_tokens':model_settings.model_max_tokens,'thinking':model_settings.model_thinking},sort_keys=True)
        if model_settings.extraction_strategy == 'rag':
            from .retrieval import KNOWLEDGE
            pipeline += 'rag:' + hashlib.sha256(KNOWLEDGE.read_bytes()).hexdigest()

        async def process_one(group: list[Path], index: int) -> None:
            async with gate:
                notice_id = group[0].stem
                before = time.perf_counter()
                transient = workdir/'notices'/str(index)
                errors = []
                try:
                    metadata = await asyncio.to_thread(notice_metadata,group[0])
                    fingerprint = await asyncio.to_thread(input_digest,group,pipeline)
                    old = one('SELECT input_hash,result_json FROM notices WHERE dataset_id=? AND notice_uuid=?',
                              (record['dataset_id'],notice_id))
                    if record['mode'] in {'extract','replay'} and old and old['input_hash']==fingerprint:
                        cached = json.loads(old['result_json'])
                        if cached.get('processing_status')=='complete':
                            _record(job_id,notice_id,'cached',cached,[],time.perf_counter()-before)
                            return
                    html = group[0] if group[0].suffix.lower() in {'.html','.htm'} else None
                    attachments = group[1:] if html else group
                    expanded = await asyncio.to_thread(expand_archives,attachments,transient,errors)
                    files = ([html] if html else [])+expanded
                    if record['mode']=='parse-only':
                        details = []
                        for path in files:
                            try:
                                text = await asyncio.to_thread(parse_file,path)
                                details.append({'file':path.name,'characters':len(text)})
                                errors.extend(f'{path.name}: {line[len(PARSE_WARNING_PREFIX):]}'
                                              for line in text.splitlines() if line.startswith(PARSE_WARNING_PREFIX))
                            except Exception as exc:
                                errors.append(f'{path.name}: {type(exc).__name__}: {str(exc)[:160]}')
                        if not details:
                            raise ValueError('没有可解析的文件')
                        _record(job_id,notice_id,'partial' if errors else 'parsed',{'files':details},errors,time.perf_counter()-before)
                        return
                    result = await extractor.extract(files)
                    result.notice_uuid = notice_id
                    result.source_url = metadata.get('source_url','')
                    if not result.project_name:
                        result.project_name = metadata.get('project_name','')
                    if not result.announcement_date:
                        result.announcement_date = metadata.get('announcement_date','')
                    if metadata.get('url_provenance')=='reconstructed_from_ccgp_breadcrumb':
                        result.warnings.append('URL依据政府采购网栏目和公告UUID重建；原HTML无canonical链接')
                    result.input_hash = fingerprint
                    if errors:
                        result.warnings.extend(errors)
                        if result.processing_status=='complete':
                            result.processing_status='partial'
                    if not any(lot.items for lot in result.package_records()) and not result.no_target_reason and result.processing_status=='complete':
                        result.processing_status='partial'
                        result.warnings.append('未返回标的明细，且没有合法空结果的原文依据')
                    await asyncio.to_thread(upsert_notice,result,[p.name for p in group],record['dataset_id'])
                    if settings.neo4j_uri:
                        try:
                            from .neo4j_sync import sync_notice
                            await asyncio.to_thread(sync_notice,record['dataset_id'],notice_id)
                        except Exception as exc:
                            result.warnings.append(f'可选Neo4j镜像同步失败：{type(exc).__name__}；SQLite结果已保留')
                    _record(job_id,notice_id,result.processing_status,result.model_dump(),result.warnings,time.perf_counter()-before)
                except Exception as exc:
                    _record(job_id,notice_id,'failed',None,[f'{type(exc).__name__}: {str(exc)[:240]}'],time.perf_counter()-before)
                finally:
                    if transient.exists():
                        await asyncio.to_thread(shutil.rmtree,transient,True)
        await asyncio.gather(*(process_one(group,index) for index,group in enumerate(groups)))
        state = get_job(job_id)
        status = 'completed_with_errors' if state.failed else 'completed_with_warnings' if state.partial or state.errors else 'completed'
        with connect() as c:
            c.execute('UPDATE jobs SET status=?,completed_at=?,elapsed_seconds=? WHERE id=?',
                      (status,timestamp(),round(time.perf_counter()-started,4),job_id))
    except asyncio.CancelledError:
        with connect() as c:
            c.execute("UPDATE jobs SET status='interrupted',completed_at=? WHERE id=?",(timestamp(),job_id))
        raise
    except Exception as exc:
        with connect() as c:
            c.execute("UPDATE jobs SET status='failed',completed_at=?,errors=?,elapsed_seconds=? WHERE id=?",
                      (timestamp(),json.dumps([f'{type(exc).__name__}: {str(exc)[:240]}'],ensure_ascii=False),round(time.perf_counter()-started,4),job_id))
    finally:
        await extractor.close()
        with connect() as c:
            c.execute('UPDATE jobs SET metrics=? WHERE id=?', (json.dumps({
                'model_calls': extractor.calls, 'token_usage': extractor.total_usage,
                'cache_only':record['mode']=='replay',
                'response_cache_hits': extractor.response_cache_hits,
                'document_cache_hits': extractor.document_cache_hits,
                'document_cache_writes': extractor.document_cache_writes,
                'optimization_counts': extractor.optimization_counts,
                'batch_disabled_reason': extractor.batch_disabled_reason,
                'model_concurrency': model_settings.model_concurrency,
                'parse_concurrency': settings.parse_concurrency,
                'notice_concurrency': settings.notice_concurrency or min(8, settings.model_concurrency + settings.parse_concurrency),
                'response_cache_keys': sorted(extractor.response_cache_keys),
                'requested_model': model_settings.deepseek_model, 'thinking': model_settings.model_thinking,
                'pipeline_version': PIPELINE_VERSION,
                'parser_version': PARSER_VERSION, 'strategy': model_settings.extraction_strategy,
                'parse_cache_enabled': bool(settings.parse_cache_dir),
                'timing_scope': '公告发现、解包、解析、模型与入库；不含浏览器渲染',
            }, ensure_ascii=False), job_id))
        discovery = workdir/'discovery'
        if discovery.exists():
            await asyncio.to_thread(shutil.rmtree,discovery,True)


def retry_job(job_id: str) -> JobState:
    original = one('SELECT * FROM jobs WHERE id=?',(job_id,))
    if not original or original['status'] not in TERMINAL:
        raise ValueError('任务不存在或尚未结束')
    manifest = json.loads(original['manifest'])
    paths = [Path(p) for p in manifest['uploads']]
    groups = [[Path(p) for p in group] for group in manifest['groups']] if manifest['groups'] is not None else None
    all_paths = paths+[p for group in (groups or []) for p in group]
    if not all_paths or any(not p.is_file() for p in all_paths):
        raise ValueError('原始输入已不可用，请重新上传')
    new_id = uuid.uuid4().hex
    return create_job(new_id,paths,settings.upload_dir/new_id,original['dataset_id'],groups,original['mode'])
