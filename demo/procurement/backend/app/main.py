from __future__ import annotations

import csv
import io
import json
import re
import secrets
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import analytics
from . import __version__
from .config import ROOT, settings
from .database import init_db, one, rows
from .exports import export_records, workbook_bytes
from .ingestion import local_groups
from .jobs import create_job, get_job, interrupt_running_jobs, process_job, retry_job, save_uploads
from .models import ItemExtract, LocalIngestRequest, LotExtract, ProjectExtract
from .item_hierarchy import analyze_lot
from .normalization import model_match_key, normalize_org
from .parsers import office_tool, seven_zip
from .ocr import available as ocr_available
from .seed import seed_demo, seed_reference
from .graph_store import GraphUnavailable
from .model_settings import ModelConfigurationUpdate, current_settings, public_settings, save_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    interrupt_running_jobs()  # Run a single Uvicorn worker; durable interruption is recoverable by retry.
    if settings.demo_seed:
        seed_demo()
    if settings.reference_seed:
        seed_reference()
    yield
    from .neo4j_sync import close_driver
    close_driver()


app = FastAPI(title='智采图谱 API',version=__version__,lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exception):
    if request.url.path == '/api/settings/model':
        return JSONResponse({'detail':'模型配置格式无效，请检查服务地址、模型名称和Key格式'},status_code=422)
    from fastapi.exception_handlers import request_validation_exception_handler
    return await request_validation_exception_handler(request,exception)


@app.exception_handler(GraphUnavailable)
async def graph_unavailable(_, exception):
    return JSONResponse({'detail':str(exception)},status_code=503)


app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],
                   allow_credentials=True,allow_methods=['GET','POST'],allow_headers=['*'])


@app.middleware('http')
async def access_and_timing(request, call_next):
    if settings.api_token and request.url.path.startswith('/api/') and request.url.path!='/api/health' and request.method!='OPTIONS':
        token = request.headers.get('authorization','').removeprefix('Bearer ')
        if not secrets.compare_digest(token,settings.api_token):
            return JSONResponse({'detail':'需要有效的访问令牌'},status_code=401)
    started = time.perf_counter()
    response = await call_next(request)
    response.headers['X-Response-Time-Ms'] = f'{(time.perf_counter()-started)*1000:.2f}'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def dataset(dataset_id: str=Query('default',min_length=1,max_length=80,pattern=r'^[\w-]+$')) -> str:
    return dataset_id


def require_model_network(mode: str) -> None:
    effective = current_settings(settings)
    if mode == 'extract' and not effective.model_network_enabled:
        raise HTTPException(503,effective.model_settings_error or '模型网络调用已暂停；已有结果可继续查看，解析预检不调用模型')


@app.get('/api/health')
def health():
    effective = current_settings(settings)
    return {'status':'ok','version':__version__,'deepseek_configured':bool(effective.deepseek_api_key),
            'model_network_enabled':effective.model_network_enabled,'model_settings_error':effective.model_settings_error,
            'model_settings_readonly':settings.model_settings_readonly,
            'model':effective.deepseek_model,'neo4j_configured':bool(settings.neo4j_uri),
            'relation_backend':settings.relation_backend,'extraction_strategy':settings.extraction_strategy,
            'auth_required':bool(settings.api_token),'official_data_available':(settings.official_data_dir/'html').is_dir(),
            'archive_tool_available':bool(seven_zip()),'legacy_doc_tool_available':bool(shutil.which('antiword') or office_tool()),
            'ocr_available':ocr_available(),'max_upload_bytes':settings.max_upload_bytes,
            'model_concurrency':settings.model_concurrency,'amount_unit':'元','relation_policy':'明确未通过排除；中标、明确审查通过或原文披露专家评分者计入有效参与，其余未知者暂不计入'}


def settings_access(request: Request):
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403,'请从系统页面修改模型配置')
    if not settings.api_token:
        if not request.client or request.client.host not in {'127.0.0.1','::1'}:
            raise HTTPException(403,'远程管理模型配置需要服务访问令牌')
        origin = request.headers.get('origin')
        if origin:
            try:
                local = urlsplit(origin).hostname in {'127.0.0.1','localhost','::1'}
            except ValueError:
                local = False
            if not local:
                raise HTTPException(403,'请从本机系统页面修改模型配置')


@app.get('/api/settings/model',dependencies=[Depends(settings_access)])
def model_configuration():
    return public_settings(settings)


@app.post('/api/settings/model',dependencies=[Depends(settings_access)])
def update_model_configuration(update: ModelConfigurationUpdate):
    try:
        return save_settings(settings,update)
    except ValueError as exception:
        raise HTTPException(422,str(exception)) from None
    except OSError:
        raise HTTPException(503,'模型配置未保存，请检查本机配置目录的写入权限') from None


@app.get('/api/knowledge')
def knowledge(q: str=Query(min_length=1,max_length=4000),top_k: int=Query(3,ge=1,le=6)):
    from .retrieval import retrieve
    return {'data':retrieve(q,top_k),'scope':'领域提取规则；不包含官方答案或当前公告事实'}


@app.get('/api/datasets')
def datasets():
    existing = rows('SELECT dataset_id id,COUNT(*) packages,COUNT(DISTINCT notice_uuid) notices FROM projects GROUP BY dataset_id')
    for name in ('default','official','demo'):
        if not any(x['id']==name for x in existing):
            existing.append({'id':name,'packages':0,'notices':0})
    return existing


@app.get('/api/stats')
def stats(dataset_id: str=Depends(dataset)):
    result = one("""SELECT COUNT(*) projects,COUNT(DISTINCT notice_uuid) notices,
        (SELECT COUNT(*) FROM items i JOIN projects p ON p.id=i.project_id WHERE p.dataset_id=?) items,
        (SELECT COUNT(DISTINCT bidder) FROM bids b JOIN projects p ON p.id=b.project_id WHERE p.dataset_id=? AND b.identity_confirmed=1) suppliers,
        COUNT(DISTINCT NULLIF(purchaser,'')) purchasers,ROUND(SUM(awarded_amount),2) total_amount,
        SUM(awarded_amount IS NULL) unknown_amount_count,
        SUM(processing_status='fallback') fallback_packages,SUM(processing_status='partial') partial_packages,
        (SELECT ROUND(AVG(confidence)*100,1) FROM items i JOIN projects p ON p.id=i.project_id WHERE p.dataset_id=?) avg_confidence
        FROM projects WHERE dataset_id=?""",(dataset_id,)*4)
    result['suppliers'] = len(entities(type='supplier',dataset_id=dataset_id))
    return result


@app.post('/api/ingest',status_code=202)
async def ingest(background_tasks: BackgroundTasks,files: list[UploadFile]=File(...),
                 dataset_id: str=Form('default',min_length=1,max_length=80,pattern=r'^[\w-]+$'),
                 mode: str=Form('extract',pattern='^(extract|parse-only|replay)$')):
    require_model_network(mode)
    if not files:
        raise HTTPException(400,'至少上传一个文件')
    if one("SELECT id FROM jobs WHERE status IN ('queued','processing')"):
        raise HTTPException(409,'已有处理中的批次')
    workdir = None
    try:
        job_id,saved,workdir = await save_uploads(files)
        state = create_job(job_id,saved,workdir,dataset_id,mode=mode)
    except ValueError as exc:
        if workdir:
            shutil.rmtree(workdir,ignore_errors=True)
        raise HTTPException(409 if '批次' in str(exc) else 413,str(exc)) from exc
    background_tasks.add_task(process_job,job_id)
    return state


@app.post('/api/ingest/local',status_code=202)
def ingest_local(request: LocalIngestRequest,background_tasks: BackgroundTasks):
    require_model_network(request.mode)
    try:
        groups = local_groups(request.limit,request.notice_ids)
        if request.announcement_only:
            groups = [[group[0]] for group in groups]
        if not groups:
            raise ValueError('没有匹配的官方公告')
        job_id = uuid.uuid4().hex
        state = create_job(job_id,[],settings.upload_dir/job_id,request.dataset_id,groups,request.mode)
    except ValueError as exc:
        raise HTTPException(409 if '批次' in str(exc) else 400,str(exc)) from exc
    background_tasks.add_task(process_job,job_id)
    return state


@app.get('/api/samples')
def samples():
    path = ROOT/'data/demo/manifest.json'
    return json.loads(path.read_text('utf-8')) if path.is_file() else {'notice_ids':[],'available':False}


@app.get('/api/samples/input.zip')
def sample_input():
    path = ROOT/'data/demo/sample-announcements.zip'
    if not path.is_file():
        raise HTTPException(404,'演示样本包尚未准备')
    return Response(path.read_bytes(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename=sample-announcements.zip'})


@app.post('/api/samples/replay',status_code=202)
def replay_samples(request: LocalIngestRequest, background_tasks: BackgroundTasks):
    manifest = samples()
    inputs = ROOT/'data/demo/html'
    ids = manifest.get('notice_ids', [])
    if not ids or any(not (inputs/(uid+'.html')).is_file() for uid in ids):
        raise HTTPException(404,'本地回放样本尚未准备')
    groups = [[inputs/(uid+'.html')] for uid in ids]
    job_id = uuid.uuid4().hex
    try:
        state = create_job(job_id, [], settings.upload_dir/job_id, request.dataset_id, groups, 'replay')
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    background_tasks.add_task(process_job,job_id)
    return state


@app.get('/api/jobs')
def jobs(dataset_id: str=Depends(dataset),limit: int=Query(30,ge=1,le=100)):
    return [get_job(x['id']) for x in rows('SELECT id FROM jobs WHERE dataset_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?',(dataset_id,limit))]


@app.get('/api/jobs/{job_id}')
def job(job_id: str):
    state = get_job(job_id)
    if not state:
        raise HTTPException(404,'任务不存在')
    return state


@app.get('/api/jobs/{job_id}/documents')
def job_documents(job_id: str,page: int=Query(1,ge=1),page_size: int=Query(50,ge=1,le=200)):
    if not get_job(job_id):
        raise HTTPException(404,'任务不存在')
    data = rows('SELECT notice_uuid,status,errors,elapsed_seconds FROM job_documents WHERE job_id=? ORDER BY notice_uuid LIMIT ? OFFSET ?',
                (job_id,page_size,(page-1)*page_size))
    for record in data:
        record['errors'] = json.loads(record['errors'])
    return {'total':one('SELECT COUNT(*) count FROM job_documents WHERE job_id=?',(job_id,))['count'],'data':data}


@app.post('/api/jobs/{job_id}/retry',status_code=202)
def retry(job_id: str,background_tasks: BackgroundTasks):
    original = get_job(job_id)
    if original:
        require_model_network(original.mode)
    try:
        state = retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    background_tasks.add_task(process_job,state.id)
    return state


@app.get('/api/projects')
def projects(q: str='',purchaser: str='',page: int=Query(1,ge=1),page_size: int=Query(20,ge=1,le=100),dataset_id: str=Depends(dataset),sort: Literal['announcement_date','imported_at']='announcement_date'):
    filters,params = ['p.dataset_id=?'],[dataset_id]
    if q:
        filters.append('(p.name LIKE ? OR p.project_code LIKE ? OR p.notice_uuid LIKE ? OR p.lot_name LIKE ?)')
        params.extend([f'%{q}%']*4)
    if purchaser:
        filters.append('p.purchaser=?'); params.append(normalize_org(purchaser))
    where = ' AND '.join(filters)
    order = {'announcement_date':'p.announcement_date DESC,p.id DESC',
             'imported_at':'p.created_at DESC,p.id DESC'}[sort]
    total = one(f'SELECT COUNT(*) count FROM projects p WHERE {where}',tuple(params))['count']
    data = rows(f"""SELECT p.*,(SELECT COUNT(*) FROM bids b WHERE b.project_id=p.id) bidder_count,
        (SELECT GROUP_CONCAT(b.bidder,'；') FROM bids b WHERE b.project_id=p.id AND b.is_winner=1 AND b.eligible=1) winners
        FROM projects p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?""",tuple(params)+(page_size,(page-1)*page_size))
    return {'total':total,'page':page,'page_size':page_size,'data':data}


@app.get('/api/projects/{project_id}')
def project_detail(project_id: int,dataset_id: str=Depends(dataset)):
    record = one('SELECT * FROM projects WHERE id=? AND dataset_id=?',(project_id,dataset_id))
    if not record:
        raise HTTPException(404,'采购包不存在')
    items = rows('SELECT * FROM items WHERE project_id=? ORDER BY id',(project_id,))
    def metadata(value, kind):
        try:
            decoded = json.loads(value) if isinstance(value,str) else value
        except (ValueError,TypeError):
            return kind()
        return decoded if isinstance(decoded,kind) else kind()
    lot = LotExtract(lot_name=record['lot_name'],items=[ItemExtract(**{**item,
        'supplier':item['winner_supplier'],
        'derived_values':metadata(item['derived_values'],dict),
        'related_sources':metadata(item['related_sources'],list)}) for item in items])
    snapshot = one('SELECT result_json FROM notices WHERE dataset_id=? AND notice_uuid=?',
                   (dataset_id,record['notice_uuid']))
    if snapshot:
        try:
            stored = ProjectExtract.model_validate_json(snapshot['result_json'])
            candidate = next((value for value in stored.package_records() if value.lot_key == record['lot_key']),None)
        except (ValueError,TypeError):
            candidate = None
        # Never attach context to a different source row in a legacy/manual DB.
        if candidate and len(candidate.items) == len(lot.items) and all(
            item.model_dump(exclude={'source_context','field_observations','lot_name'}) ==
            row.model_dump(exclude={'source_context','field_observations','lot_name'})
            for item,row in zip(candidate.items,lot.items)):
            lot = candidate
    for item, observed in zip(items,lot.items):
        item['field_observations'] = observed.field_observations
    return {**record,'bids':rows('SELECT * FROM bids WHERE project_id=? ORDER BY id',(project_id,)),
            'items':items,'item_hierarchy':analyze_lot(lot)}


@app.get('/api/items')
def items(q: str='',category: str='',brand: str='',model: str='',source: str='',purchaser: str='',supplier: str='',
          min_price: float | None=Query(None,ge=0,allow_inf_nan=False),max_price: float | None=Query(None,ge=0,allow_inf_nan=False),
          min_quantity: float | None=Query(None,ge=0,allow_inf_nan=False),max_quantity: float | None=Query(None,ge=0,allow_inf_nan=False),
          min_total: float | None=Query(None,ge=0,allow_inf_nan=False),max_total: float | None=Query(None,ge=0,allow_inf_nan=False),
          page: int=Query(1,ge=1),page_size: int=Query(20,ge=1,le=200),dataset_id: str=Depends(dataset)):
    filters,params = ['p.dataset_id=?'],[dataset_id]
    q = q.strip()
    if q:
        columns = ('i.product_name','i.category','i.brand','i.model','i.unit_price','i.quantity','i.total_price',
                   'p.name','p.purchaser','i.winner_supplier')
        filters.append('('+' OR '.join(f'CAST({c} AS TEXT) LIKE ?' for c in columns)+')')
        params.extend([f'%{q}%']*len(columns))
    model_key = model_match_key(model)
    if model_key:
        filters.append('i.model_key=?'); params.append(model_key)
    for column,value in (('i.category',category),('i.brand',brand),('i.source',source),('p.purchaser',purchaser),('i.winner_supplier',supplier)):
        if value:
            filters.append(f'{column}=?'); params.append(value)
    for column,minimum,maximum in (('i.unit_price',min_price,max_price),('i.quantity',min_quantity,max_quantity),('i.total_price',min_total,max_total)):
        if minimum is not None and maximum is not None and minimum>maximum:
            raise HTTPException(400,'筛选下限不能大于上限')
        if minimum is not None:
            filters.append(f'{column}>=?'); params.append(minimum)
        if maximum is not None:
            filters.append(f'{column}<=?'); params.append(maximum)
    where = ' AND '.join(filters)
    total = one(f'SELECT COUNT(*) count FROM items i JOIN projects p ON p.id=i.project_id WHERE {where}',tuple(params))['count']
    data = rows(f"""SELECT i.*,p.name project_name,p.project_code,p.purchaser,p.notice_uuid,p.lot_name,p.processing_status
        FROM items i JOIN projects p ON p.id=i.project_id WHERE {where} ORDER BY i.id DESC LIMIT ? OFFSET ?""",tuple(params)+(page_size,(page-1)*page_size))
    return {'total':total,'page':page,'page_size':page_size,'data':data}


@app.get('/api/entities')
def entities(type: str=Query(pattern='^(purchaser|supplier|brand|category)$'),dataset_id: str=Depends(dataset)):
    columns = {'purchaser':('projects p','p.purchaser'),'supplier':('bids b JOIN projects p ON p.id=b.project_id','b.bidder'),
               'brand':('items i JOIN projects p ON p.id=i.project_id','i.brand'),'category':('items i JOIN projects p ON p.id=i.project_id','i.category')}
    table,column = columns[type]
    identity = ' AND b.identity_confirmed=1' if type=='supplier' else ''
    values = [r['value'] for r in rows(f"SELECT DISTINCT {column} value FROM {table} WHERE p.dataset_id=? AND {column}<>''{identity} ORDER BY value",(dataset_id,))]
    if type=='supplier':
        from .bid_identity import non_company_name_reason
        values = [name for name in values if not non_company_name_reason(name)]
    return values


@app.get('/api/analytics/purchaser-partners')
def api_purchaser_partners(purchaser: str=Query(min_length=1),dataset_id: str=Depends(dataset)):
    return analytics.purchaser_partners(normalize_org(purchaser),dataset_id)


@app.get('/api/analytics/purchaser-bidders')
def api_purchaser_bidders(purchaser: str=Query(min_length=1),dataset_id: str=Depends(dataset)):
    return analytics.purchaser_bidders(normalize_org(purchaser),dataset_id)


@app.get('/api/analytics/supplier-competitors')
def api_supplier_competitors(supplier: str=Query(min_length=1),history_scope: str=Query('all',pattern='^(all|winning)$'),dataset_id: str=Depends(dataset)):
    return analytics.supplier_competitors(normalize_org(supplier),dataset_id,history_scope)


def _suppliers(value: str) -> list[str]:
    result = list(dict.fromkeys(normalize_org(part) for part in value.split(',') if normalize_org(part)))
    if not 2<=len(result)<=30:
        raise HTTPException(400,'请指定2至30个不同的供应商，以英文逗号分隔')
    return result


@app.get('/api/analytics/common-purchasers')
def api_common_purchasers(suppliers: str,dataset_id: str=Depends(dataset)):
    return analytics.common_purchasers(_suppliers(suppliers),dataset_id)


@app.get('/api/analytics/common-projects')
def api_common_projects(suppliers: str,dataset_id: str=Depends(dataset)):
    return analytics.common_projects(_suppliers(suppliers),dataset_id)


@app.get('/api/graph')
def graph(limit: int=Query(80,ge=1,le=300),dataset_id: str=Depends(dataset)):
    return analytics.relationship_graph(limit,dataset_id)


@app.get('/api/graph/overview')
def graph_overview(dataset_id: str=Depends(dataset)):
    return analytics.relationship_overview(dataset_id)


@app.get('/api/export/standard.xlsx')
def export_standard(dataset_id: str=Depends(dataset),job_id: str | None=None,include_notes: bool=False,
                    item_view: str=Query('source',pattern='^(source|detail)$')):
    try:
        records,notes = export_records(dataset_id,job_id)
        content = workbook_bytes(records,notes,include_notes,item_view)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    filename = 'procurement-detail-tables.xlsx' if item_view=='detail' else 'procurement-tables.xlsx'
    if job_id:
        filename = filename.replace('procurement-','procurement-batch-',1)
    return Response(content,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':f'attachment; filename={filename}'})


@app.get('/api/export/quality.json')
def export_quality(dataset_id: str=Depends(dataset),job_id: str | None=None,
                   item_view: str=Query('source',pattern='^(source|detail)$')):
    try:
        records,notes = export_records(dataset_id,job_id)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    report = {'dataset_id':dataset_id,'job_id':job_id,'item_view':item_view,'batch_notes':notes[1:],
              'policy':'官方三表保持模板结构；未知值留空；审查未知非中标者暂不纳入有效共同竞标；规则兜底不进入关系分析',
              'notices':[{'notice_uuid':r.notice_uuid,'processing_status':r.processing_status,
                          'warnings':r.warnings,'no_target_reason':r.no_target_reason,
                          'document_audit':r.model_usage.get('documents',[]),
                          'item_hierarchy':[analyze_lot(lot) for lot in r.package_records()]} for r in records]}
    return Response(json.dumps(report,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':'attachment; filename=quality-report.json'})


@app.get('/api/export/items.csv')
def export_items(dataset_id: str=Depends(dataset)):
    data = rows("""SELECT p.notice_uuid,p.lot_name,p.project_code,p.name project_name,p.purchaser,i.product_name,i.category,i.brand,i.model,
        i.unit_price unit_price_yuan,i.quantity,i.total_price total_price_yuan,i.winner_supplier,i.source,i.confidence,i.evidence
        FROM items i JOIN projects p ON p.id=i.project_id WHERE p.dataset_id=? ORDER BY p.id,i.id""",(dataset_id,))
    output = io.StringIO(); output.write('\ufeff')
    fieldnames = list(data[0]) if data else ['notice_uuid','lot_name','product_name','unit_price_yuan','quantity','total_price_yuan']
    writer = csv.DictWriter(output,fieldnames=fieldnames); writer.writeheader()
    for record in data:
        writer.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in record.items()})
    return StreamingResponse(iter([output.getvalue()]),media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition':'attachment; filename=items-yuan.csv'})
