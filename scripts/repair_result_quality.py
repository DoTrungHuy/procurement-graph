"""Source-grounded quality repair on a new copy; originals and excluded facts stay auditable."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'demo/procurement/backend'))


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def basename(label):
    return ntpath.basename(re.sub(r'^doc-\d+:', '', label))


def reasons(record):
    groups = []
    if any(re.search(r'字段冲突: (?:awarded_amount|bid_amount)', w) for w in record.warnings): groups.append('money_conflict')
    if any('跨文件同品目同名' in w for w in record.warnings): groups.append('cross_file')
    if any(not lot.lot_name for lot in record.lots): groups.append('unknown_package')
    if any(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{5,}',bid.bidder) for lot in record.lots for bid in lot.bids):
        groups.append('anonymous_bid_identity')
    labels = {obj.source_file for lot in record.lots for obj in [*lot.items,*lot.bids]}
    if any(re.search(r'声明|劳务|报酬|业绩|本国-|中企业|中小企', label) for label in labels): groups.append('supporting_source')
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--official-data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',action='store_true',help='Resume a verified local repair, keeping its original source hash')
    parser.add_argument('--recheck-notices',nargs='*',default=[])
    args = parser.parse_args()
    source, output = args.database.resolve(), args.output.resolve()
    if (output.exists() and not args.resume) or source.is_relative_to(output):
        raise ValueError('只允许创建不包含原库的新输出目录')
    source_hash = sha256(source)
    previous = None
    if args.resume:
        previous=json.loads((output/'repair-report.json').read_text('utf-8'))
        if previous['source_database_sha256']!=source_hash or previous['dataset']!=args.dataset:
            raise ValueError('续作的源库或数据集发生变化')
    else:
        output.mkdir(parents=True)
    os.environ.update(MODEL_NETWORK_ENABLED='false', DEEPSEEK_API_KEY='', DEMO_SEED='false', REFERENCE_SEED='false')
    os.environ['DATABASE_PATH'] = str(output/'platform.db')
    from app import database
    from app.acceptance import check_workbook
    from app.bid_identity import verify_bid_identities
    from app.document_policy import document_role
    from app.evidence import ground_evidence
    from app.exports import workbook_bytes
    from app.extractor import validate_result
    from app.models import LotExtract, ProjectExtract
    from app.package_identity import resolve_package_labels
    from app.parsers import expand_archives, parse_file
    from app.source_facts import reconcile_notice_facts
    database.settings = replace(database.settings,database_path=output/'platform.db')
    if not args.resume:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
            with closing(sqlite3.connect(output/'platform.db')) as target:
                original.backup(target)
    database.init_db()
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
        before = dict(original.execute('SELECT notice_uuid,result_json FROM notices WHERE dataset_id=?',(args.dataset,)))
    selected = [(uid, ProjectExtract.model_validate_json(raw)) for uid,raw in before.items()]
    selected = [(uid,record) for uid,record in selected if reasons(record)]
    actions, counts, changed, quarantined = [], Counter(), [], []
    output_audit = output/'notice-audit.jsonl'
    if args.resume:
        actions=[json.loads(line) for line in output_audit.read_text('utf-8').splitlines()]
        reviewed={a['notice_uuid'] for a in actions}
        redo=set(args.recheck_notices)
        if not redo <= set(before):raise ValueError('续作复核清单包含未知公告')
        selected=[(uid,record) for uid,record in selected if uid not in reviewed or uid in redo]
        actions=[a for a in actions if a['notice_uuid'] not in redo]
        changed=[uid for uid in previous['changed_ids'] if uid not in redo]
        quarantined=[json.loads(line) for line in (output/'quarantined-records.jsonl').read_text('utf-8').splitlines()]
        quarantined=[row for row in quarantined if row['notice_uuid'] not in redo]
        counts.update(change['action'] for a in actions for change in a['changes'])
        output_audit.write_text(''.join(json.dumps(a,ensure_ascii=False)+'\n' for a in actions),'utf-8')
    for number,(uid,record) in enumerate(selected,1):
        audit = {'notice_uuid':uid,'categories':reasons(record),'sources':[],'changes':[],'applied':True,
                 'source_errors':[],'unresolved':[],'official_score':False}
        quarantine_start = len(quarantined)
        initial = record.model_dump()
        with tempfile.TemporaryDirectory(prefix='quality-source-',dir=output) as temporary:
            primary = args.official_data/'html'/(uid+'.html')
            files = [primary]
            archive = args.official_data/'file'/(uid+'.zip')
            try:
                files += expand_archives([archive],Path(temporary))
            except Exception as exc:
                audit['source_errors'].append(f'{type(exc).__name__}: {str(exc)[:220]}')
            sources, ambiguous = {}, set()
            for file in files:
                try:
                    digest = sha256(file)
                    if file.name in sources:
                        if sources[file.name]['sha256'] != digest: ambiguous.add(file.name)
                        continue
                    text = parse_file(file)
                    role, explanation = ('announcement','') if file==primary else document_role(file.name,text)
                    entry = {'filename':file.name,'sha256':digest,'role':role,'reason':explanation,'characters':len(text)}
                    audit['sources'].append(entry)
                    sources[file.name] = {**entry,'text':text}
                except Exception as exc:
                    audit['source_errors'].append(file.name+': '+type(exc).__name__+': '+str(exc)[:200])
            for name in ambiguous:
                sources.pop(name,None)
                audit['source_errors'].append('同名原文不能唯一确定: '+name)
            labels = {obj.source_file for lot in record.lots for obj in [*lot.items,*lot.bids]}
            primary_label = next((label for label in labels if basename(label)==primary.name), 'doc-1:'+primary.name)
            original_text = sources.get(primary.name,{}).get('text','')
            if original_text:
                audit['changes'] += reconcile_notice_facts(record,original_text,primary_label)
            for lot in record.lots:
                for kind in ('items','bids'):
                    retained = []
                    for obj in getattr(lot,kind):
                        item_source = sources.get(basename(obj.source_file))
                        if item_source and item_source['reason']:
                            candidates = [ref for ref in obj.related_sources if basename(ref.get('source_file','')) in sources
                                          and not sources[basename(ref['source_file'])]['reason']]
                            if candidates:
                                previous = {'source_file':obj.source_file,'evidence':obj.evidence}
                                replacement = candidates[0]
                                obj.source_file,obj.evidence = replacement['source_file'],replacement.get('evidence','')
                                if kind=='items':
                                    obj.source_location = replacement.get('source_location','')
                                    obj.line_id = obj.source_location
                                if previous not in obj.related_sources:obj.related_sources.append(previous)
                                audit['changes'].append({'action':'primary_source','lot_name':lot.lot_name,'kind':kind,
                                                         'before':previous,'after':replacement})
                            else:
                                quarantined.append({'notice_uuid':uid,'lot_name':lot.lot_name,'kind':kind,
                                                    'source_sha256':item_source['sha256'],'reason':item_source['reason'],
                                                    'record':obj.model_dump()})
                                audit['changes'].append({'action':'quarantine_supporting','kind':kind,'lot_name':lot.lot_name,
                                                         'source_file':obj.source_file,'reason':item_source['reason']})
                                continue
                        retained.append(obj)
                    setattr(lot,kind,retained)
            # Supporting-only anonymous fragments are not additional packages.
            record.lots = [lot for lot in record.lots if lot.lot_name or lot.items or lot.bids]
            if not record.lots:
                audit['unresolved'].append('全部记录来自辅助材料，缺少可确认的成交采购包；本次不覆盖原结果')
                record = ProjectExtract.model_validate_json(before[uid])
                audit['applied'] = False
                audit['proposed_changes'], audit['changes'] = audit['changes'], []
                del quarantined[quarantine_start:]
            else:
                audit['changes'] += [{'action':'package_label',**change} for change in resolve_package_labels(
                    record,{name:entry['text'] for name,entry in sources.items()})]
                if original_text:
                    audit['changes'] += reconcile_notice_facts(record,original_text,primary_label)
                for lot in record.lots:
                    for label in {bid.source_file for bid in lot.bids}:
                        info = sources.get(basename(label))
                        if info:
                            audit['changes'] += verify_bid_identities([bid for bid in lot.bids if bid.source_file==label],info['text'])
                    for label in {item.source_file for item in lot.items}:
                        info = sources.get(basename(label))
                        if info:
                            items = [item for item in lot.items if item.source_file==label]
                            ground_evidence(ProjectExtract(lots=[LotExtract(lot_name=lot.lot_name,items=items)]),info['text'])
                validate_result(record)
                if audit['source_errors']:
                    record.processing_status='partial'
                if record.model_dump()!=initial:
                    changed.append(uid)
                    record.warnings.append('本轮按原始公告及附件执行质量修复，辅助记录与修复前快照保存在审计产物中')
                    stored = database.one('SELECT source_files FROM projects WHERE dataset_id=? AND notice_uuid=?',(args.dataset,uid))
                    database.upsert_notice(record,stored['source_files'].split(' | '),args.dataset)
            audit['unresolved'] += ['采购包仍未确认: '+lot.lot_key for lot in record.lots if not lot.lot_name]
            audit['unresolved'] += ['存在跨来源同名标的，业务金额暂不汇总'] if any(not item.aggregation_eligible for lot in record.lots for item in lot.items) else []
            counts.update(change['action'] for change in audit['changes'])
            actions.append(audit)
        with output_audit.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(audit,ensure_ascii=False)+'\n')
        if number%10==0 or number==len(selected):
            print(json.dumps({'processed':number,'selected':len(selected),'changed':len(changed),'actions':dict(counts)},ensure_ascii=False),flush=True)
    after = {row['notice_uuid']:row['result_json'] for row in database.rows(
        'SELECT notice_uuid,result_json FROM notices WHERE dataset_id=?',(args.dataset,))}
    if set(before)!=set(after) or any(before[uid]!=after[uid] for uid in before if uid not in changed):
        raise ValueError('修复范围之外的公告发生变化')
    # Upsert validates again; only persisted JSON differences count as changed.
    changed = [uid for uid in before if before[uid] != after[uid]]
    records=[ProjectExtract.model_validate_json(raw) for raw in after.values()]
    workbook=workbook_bytes(records)
    acceptance=check_workbook(workbook,args.official_data/'indexes/output-schema.json',set(after))
    if not acceptance['valid']:raise ValueError('修复后三表结构校验失败: '+str(acceptance['errors'][:5]))
    if sha256(source)!=source_hash:raise ValueError('源库哈希发生变化')
    (output/'before-notices.jsonl').write_text(''.join(before[uid]+'\n' for uid in changed),'utf-8')
    (output/'predictions.jsonl').write_text(''.join(raw+'\n' for raw in after.values()),'utf-8')
    (output/'quarantined-records.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in quarantined),'utf-8')
    (output/'procurement-tables.xlsx').write_bytes(workbook)
    report={'dataset':args.dataset,'source_database_sha256':source_hash,'source_unchanged':True,
            'selected_notices':len(actions),'changed_notices':len(changed),'changed_ids':changed,'actions':dict(counts),
            'quarantined_records':len(quarantined),'source_errors':sum(bool(a['source_errors']) for a in actions),
            'unresolved_notices':sum(bool(a['unresolved']) for a in actions),'workbook':acceptance,
            'notice_statuses':dict(Counter(r.processing_status for r in records)),
            'pending_amount_items':sum(not i.aggregation_eligible for r in records for l in r.lots for i in l.items),
            'llm_requests':0,'official_score':False,'submission_ready':False,
            'scope':'Targeted source validation, not a human-signed gold set or hidden-score evaluation.'}
    (output/'repair-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    with closing(sqlite3.connect(output/'platform.db')) as final:
        final.execute('pragma wal_checkpoint(TRUNCATE)');final.execute('pragma journal_mode=DELETE')
        if final.execute('pragma integrity_check').fetchone()[0]!='ok':raise ValueError('数据库完整性检查失败')
    print(json.dumps({k:report[k] for k in ['selected_notices','changed_notices','actions','source_errors','pending_amount_items']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
