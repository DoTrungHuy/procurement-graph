"""Local, row-aligned diagnostics. This is not the organizer's unreleased evaluator."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import time
import tempfile
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .evidence import canonical_location
from .extractor import DeepSeekExtractor, PIPELINE_VERSION
from .models import ProjectExtract
from .normalization import dedupe_key, lot_identity
from .parsers import expand_archives
from .field_text import BUSINESS_TEXT_POLICY, TEXT_FIELDS, interpret_field_text

FIELDS = ['product_name','category','brand','model','unit_price','quantity','total_price']
NUMERIC = {'unit_price','quantity','total_price','bid_amount','awarded_amount'}
FIELD_POLICIES = ('literal-v1', BUSINESS_TEXT_POLICY)


def value_key(value, field='', field_policy='literal-v1'):
    if field_policy == BUSINESS_TEXT_POLICY and field in TEXT_FIELDS:
        value = interpret_field_text(value, field).value
    if value is None or value == '':
        return ''
    if field in NUMERIC or isinstance(value,(int,float,Decimal)):
        try:
            number = Decimal(str(value).replace(',',''))
            return format(number.normalize(),'f') if number.is_finite() else ''
        except InvalidOperation:
            pass
    return dedupe_key(value)


def _assign(weights):
    """Maximum-weight one-to-one assignment; padding represents unmatched rows."""
    n = len(weights)
    if not n:
        return []
    m = len(weights[0])
    size = max(n,m)
    cost = [[-weights[i][j] if i<n and j<m else 0 for j in range(size)] for i in range(size)]
    u,v,p,way = [0]*(size+1),[0]*(size+1),[0]*(size+1),[0]*(size+1)
    for i in range(1,size+1):
        p[0] = i; j0 = 0
        minimum,used = [float('inf')]*(size+1),[False]*(size+1)
        while True:
            used[j0] = True; i0 = p[j0]; delta = float('inf'); j1 = 0
            for j in range(1,size+1):
                if not used[j]:
                    current = cost[i0-1][j-1]-u[i0]-v[j]
                    if current<minimum[j]: minimum[j],way[j] = current,j0
                    if minimum[j]<delta: delta,j1 = minimum[j],j
            for j in range(size+1):
                if used[j]: u[p[j]] += delta; v[j] -= delta
                else: minimum[j] -= delta
            j0 = j1
            if p[j0] == 0: break
        while True:
            j1 = way[j0]; p[j0] = p[j1]; j0 = j1
            if j0 == 0: break
    return [(p[j]-1,j-1) for j in range(1,size+1) if 0<p[j]<=n and j<=m and weights[p[j]-1][j-1]>0]


def _row_weight(expected,actual,field_policy='literal-v1'):
    if lot_identity(expected.get('lot_name', '')) != lot_identity(actual.get('lot_name', '')):
        return 0
    if expected.get('line_id') and actual.get('line_id'):
        return 1000 if (canonical_location(expected['line_id']) or expected['line_id'],expected.get('source_file','')) == (canonical_location(actual['line_id']) or actual['line_id'],actual.get('source_file','')) else 0
    names = [value_key(x.get('product_name'),'product_name',field_policy) for x in (expected,actual)]
    models = [value_key(x.get('model'),'model',field_policy) for x in (expected,actual)]
    if all(names) and names[0]!=names[1]:
        return 0
    if names[0] and names[0]==names[1]:
        # Amounts never determine row identity.
        return 100+(20 if models[0] and models[0]==models[1] else 0)
    if models[0] and models[0]==models[1]:
        return 20
    return 0


def _metrics(count):
    tp,fp,fn,tn = (count[x] for x in ('tp','fp','fn','tn'))
    p = tp/(tp+fp) if tp+fp else 0
    r = tp/(tp+fn) if tp+fn else 0
    denominator = tp+fp+fn+tn
    return {**count,'precision':round(p,6),'recall':round(r,6),
            'f1':round(2*p*r/(p+r),6) if p+r else 0,
            'cell_accuracy':round((tp+tn)/denominator,6) if denominator else 0}


def score_items(expected: list[dict], actual: list[dict], field_policy: str='literal-v1') -> dict:
    if field_policy not in FIELD_POLICIES:
        raise ValueError(f'Unknown field policy: {field_policy}')
    counts = {f:{'tp':0,'fp':0,'fn':0,'tn':0,'wrong_value':0} for f in FIELDS}
    warnings = []
    for label,records in [('gold',expected),('prediction',actual)]:
        identities = Counter((r.get('lot_name',''),r.get('product_name',''),r.get('model','')) for r in records if not r.get('line_id'))
        if any(n>1 for n in identities.values()):
            warnings.append(f'{label}: 重复标的缺少源行ID，同名行的对应关系有歧义；请补充line_id/source_file')
    matches = _assign([[_row_weight(a,b,field_policy) for b in actual] for a in expected]) if expected and actual else []
    matched_gold,matched_pred = {a for a,b in matches},{b for a,b in matches}
    pairs = [(expected[a],actual[b]) for a,b in matches]
    pairs += [(r,None) for a,r in enumerate(expected) if a not in matched_gold]
    pairs += [(None,r) for b,r in enumerate(actual) if b not in matched_pred]
    errors = []
    normalizations = []
    if field_policy == BUSINESS_TEXT_POLICY:
        for side, records in [('reference',expected),('prediction',actual)]:
            for index, record in enumerate(records):
                for field in sorted(TEXT_FIELDS):
                    observed = interpret_field_text(record.get(field), field)
                    if observed.raw != observed.value or observed.status == 'mixed_reference':
                        normalizations.append({'side':side,'row':index,'field':field,'raw':observed.raw,
                            'value':observed.value,'status':observed.status,'source_file':record.get('source_file',''),
                            'location':record.get('line_id','')})
    for gold,predicted in pairs:
        for field in FIELDS:
            e = value_key(gold.get(field),field,field_policy) if gold is not None else ''
            a = value_key(predicted.get(field),field,field_policy) if predicted is not None else ''
            if e and a and e==a: counts[field]['tp'] += 1
            elif e and a:
                counts[field]['fp'] += 1; counts[field]['fn'] += 1; counts[field]['wrong_value'] += 1
            elif e: counts[field]['fn'] += 1
            elif a: counts[field]['fp'] += 1
            elif gold is not None and predicted is not None: counts[field]['tn'] += 1
            if e != a:
                errors.append({'field':field,'expected':gold.get(field) if gold else None,
                    'actual':predicted.get(field) if predicted else None,
                    'expected_location':gold.get('line_id','') if gold else '',
                    'actual_location':predicted.get('line_id','') if predicted else ''})
            # Unmatched blank slots do not inflate true negatives.
    total = {k:sum(c[k] for c in counts.values()) for k in next(iter(counts.values()))}
    return {'fields':{f:_metrics(c) for f,c in counts.items()},'micro':_metrics(total),
            'matched_rows':len(matches),'missing_rows':len(expected)-len(matches),'extra_rows':len(actual)-len(matches),
            'warnings':warnings,'cell_errors':errors,'field_normalizations':normalizations}


def _items(record):
    if record.get('lots'):
        return [{**i,'lot_name':lot.get('lot_name','')} for lot in record['lots'] for i in lot.get('items',[])]
    return [{**i,'lot_name':i.get('lot_name') or record.get('lot_name','')} for i in record.get('items',[])]


def labelled_rows(record, actual):
    """Exclude known, unlabelled tables; keep unlocatable rows in diagnostics.

    A partial reference cannot judge unlabelled detail tables or unlocatable
    extra rows. Neither type earns a source-row match; both remain in the audit.
    """
    def table_key(item):
        location = canonical_location(item.get('line_id',''))
        return (item.get('source_file',''), location.split(' ROW:', 1)[0]) if location.startswith('TABLE:') else None
    expected_tables = {table_key(item) for item in _items(record)} - {None}
    kept, excluded = [], []
    for item in actual:
        key = table_key(item)
        (kept if key in expected_tables else excluded).append(item)
    return kept, excluded


async def evaluate(gold_path: Path,predictions_path: Path | None=None,run_model: bool=False,
                   labelled_tables_only: bool=False,field_policy: str='literal-v1') -> dict:
    if field_policy not in FIELD_POLICIES:
        raise ValueError(f'Unknown field policy: {field_policy}')
    gold_bytes = gold_path.read_bytes()
    predictions_bytes = predictions_path.read_bytes() if predictions_path else None
    gold = [json.loads(line) for line in gold_bytes.decode('utf-8-sig').splitlines() if line.strip()]
    predictions = [json.loads(line) for line in predictions_bytes.decode('utf-8-sig').splitlines() if line.strip()] if predictions_bytes is not None else None
    if predictions is None and not run_model:
        raise ValueError('请提供--predictions；只有明确指定--run-model才调用模型')
    ids = [record.get('notice_uuid', '') for record in gold]
    if any(ids) and (not all(ids) or len(ids) != len(set(ids))):
        raise ValueError('标注的公告UUID不完整或重复')
    by_id = None
    if predictions is not None:
        if all(ids) and ids:
            by_id = {}
            for predicted in predictions:
                uid = predicted.get('notice_uuid', '')
                if not uid or uid in by_id:
                    raise ValueError('预测公告UUID为空或重复')
                by_id[uid] = predicted
        elif len(gold) != len(predictions):
            raise ValueError('缺少UUID时，标注与预测公告数必须相同')
    extractor = DeepSeekExtractor() if run_model else None
    if extractor and not extractor.api_key:
        await extractor.close()
        raise ValueError('模型评测需要已配置API；离线兜底不代表标的抽取结果')
    counts = {f:{'tp':0,'fp':0,'fn':0,'tn':0,'wrong_value':0} for f in FIELDS}
    diagnostics,timings = [],[]
    try:
        for index,record in enumerate(gold):
            if predictions is None:
                before = time.perf_counter()
                settings.upload_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='evaluation-', dir=settings.upload_dir) as directory:
                    files = []
                    for path in map(Path,record['files']):
                        files.extend([path] if path.suffix.lower() in {'.html','.htm'} else expand_archives([path],Path(directory)))
                    predicted = (await extractor.extract(files)).model_dump()
                predicted['notice_uuid'] = record.get('notice_uuid', '')
                timings.append(time.perf_counter()-before)
            else:
                predicted = by_id.get(record['notice_uuid'], {'notice_uuid': record['notice_uuid'], 'items': []}) if by_id is not None else predictions[index]
            if record.get('notice_uuid') and record['notice_uuid']!=predicted.get('notice_uuid'):
                raise ValueError(f'第{index+1}条公告UUID不一致')
            actual = _items(predicted)
            excluded = []
            if labelled_tables_only:
                actual, excluded = labelled_rows(record,actual)
            report = score_items(_items(record),actual,field_policy)
            for f in FIELDS:
                for k in counts[f]: counts[f][k] += report['fields'][f][k]
            diagnostics.append({'record':index,'notice_uuid':record.get('notice_uuid',''),
                                'unlabelled_table_rows':len(excluded),
                                'unlocatable_excluded_rows':sum(not canonical_location(i.get('line_id','')) for i in excluded),
                                **{k:report[k] for k in ('matched_rows','missing_rows','extra_rows','warnings','cell_errors','field_normalizations')}})
    finally:
        if extractor: await extractor.close()
    total = {k:sum(c[k] for c in counts.values()) for k in next(iter(counts.values()))}
    micro = _metrics(total)
    micro['local_weighted_indicator'] = round(.4*micro['cell_accuracy']+.3*micro['precision']+.3*micro['recall'],6)
    source = Path(__file__).parent
    provenance = {'evaluated_at':datetime.now(timezone.utc).isoformat(),
                  'gold_sha256':hashlib.sha256(gold_bytes).hexdigest(),
                  'predictions_sha256':hashlib.sha256(predictions_bytes).hexdigest() if predictions_bytes is not None else None,
                  'prediction_source':'live-model' if predictions is None else 'saved-predictions',
                  'requested_model':extractor.settings.deepseek_model if extractor else None,
                  'pipeline_version':PIPELINE_VERSION,
                  'scorer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'field_policy_sha256':hashlib.sha256((source/'field_text.py').read_bytes()).hexdigest(),
                  'labelled_tables_only':labelled_tables_only}
    return {'evaluator':'local-row-aligned-v3','official_score':False,'records':len(gold),
            'field_policy':field_policy,'provenance':provenance,
            'field_policy_definition':('保留原文作证据；纯指引/缺失文本不作为具体字段值；正确留空计TN，真实值遗漏仍计FN，编造具体值计FP；不豁免缺行或附件漏提取'
                                       if field_policy==BUSINESS_TEXT_POLICY else '按原文文本和数值比较；保持历史字面口径，不进行业务字段清洗'),
            'reference_scope':'labelled-standard-goods-tables' if labelled_tables_only else 'all-predicted-rows-vs-reference',
            'reference_status':'local-source-reference-not-official-gold',
            'scope_limit':'部分标注只评价已标注表格，未标注及无法定位的多余行不判对错；缺失的参考行仍计FN。必须同时审阅排除行数量。' if labelled_tables_only else '',
            'missing_notice_ids': [uid for uid in ids if uid not in by_id] if by_id is not None else [],
            'unlabelled_prediction_notices': len(set(by_id) - set(ids)) if by_id is not None else 0,
            'coverage':'标的表7个业务字段；不代表三表22列全部正确或官方得分',
            'definition':'先按包和标的身份一一对齐；错误值计FP与FN，双方空值计TN；重复行按实例计数',
            'fields':{f:_metrics(c) for f,c in counts.items()},'micro':micro,'diagnostics':diagnostics,
            'model_calls':extractor.calls if extractor else 0,
            'average_extraction_seconds':round(sum(timings)/len(timings),4) if timings else None,
            'timing_scope':'仅解析和模型抽取；不含入库与页面渲染，非官方全流程耗时'}


if __name__=='__main__':
    parser = argparse.ArgumentParser(description='本地逐行评测；官方计分待主办方确认')
    parser.add_argument('--gold',type=Path,required=True)
    parser.add_argument('--predictions',type=Path)
    parser.add_argument('--run-model',action='store_true')
    parser.add_argument('--labelled-tables-only',action='store_true',help='Score only source tables that the partial reference actually labels')
    parser.add_argument('--field-policy',choices=FIELD_POLICIES,default='literal-v1',
                        help='literal-v1 preserves historical scoring; business-v1 explicitly interprets disclosed text')
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.gold,args.predictions,args.run_model,args.labelled_tables_only,args.field_policy))
    output = json.dumps(report,ensure_ascii=False,indent=2)
    if args.output: args.output.write_text(output,encoding='utf-8')
    print(output)
