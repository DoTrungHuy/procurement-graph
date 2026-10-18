"""Apply a source-reviewed package plan to a NEW database copy, with no model calls."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'demo/procurement/backend'))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--official-data', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.database.resolve(), args.output.resolve()
    if output.exists():
        raise ValueError('输出目录已存在；只允许创建新的修复副本')
    if source.parent == output or source.is_relative_to(output):
        raise ValueError('修复目录不能包含原始数据库')
    plan = json.loads(args.review.read_text('utf-8'))
    if plan.get('dataset') != args.dataset or plan.get('version') != 1:
        raise ValueError('复核清单的数据集或版本不一致')
    reviews = plan['notices']
    if not reviews or len({r['notice_uuid'] for r in reviews}) != len(reviews):
        raise ValueError('复核清单为空或有重复公告')
    original_hash = sha256(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ.update(MODEL_NETWORK_ENABLED='false', DEEPSEEK_API_KEY='', DEMO_SEED='false', REFERENCE_SEED='false')
    with tempfile.TemporaryDirectory(prefix='package-repair-', dir=output.parent) as temporary:
        work = Path(temporary)
        database_path = work / 'platform.db'
        os.environ['DATABASE_PATH'] = str(database_path)
        from app import analytics, database
        from app.acceptance import check_workbook
        from app.exports import workbook_bytes
        from app.models import ProjectExtract
        from app.package_identity import apply_reviewed_labels
        from app.parsers import expand_archives, parse_file
        database.settings = replace(database.settings, database_path=database_path)
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(database_path)) as target:
                original.backup(target)
        database.init_db()
        before_snapshots, audits = [], []
        before_all = {row['notice_uuid']: row['result_json'] for row in database.rows(
            'SELECT notice_uuid,result_json FROM notices WHERE dataset_id=?', (args.dataset,))}
        for review in reviews:
            uid = review['notice_uuid']
            if uid not in before_all:
                raise ValueError('原库没有复核公告: ' + uid)
            archive = args.official_data / 'file' / (uid + '.zip')
            if sha256(archive) != review['archive_sha256']:
                raise ValueError('公告原始附件包已变化: ' + uid)
            with tempfile.TemporaryDirectory(prefix=uid + '-', dir=work) as extraction:
                files = [args.official_data / 'html' / (uid + '.html')]
                files += expand_archives([archive], Path(extraction))
                required = {proof['filename'] for proof in review['evidence']}
                sources = {}
                for file in files:
                    if file.name not in required:
                        continue
                    entry = {'sha256': sha256(file), 'text': parse_file(file)}
                    if file.name in sources and sources[file.name] != entry:
                        raise ValueError('同名原文不能唯一确定: ' + file.name)
                    sources[file.name] = entry
                record, audit = apply_reviewed_labels(before_all[uid], review, sources)
            before_snapshots.append(json.loads(before_all[uid]))
            stored = database.one('SELECT source_files FROM projects WHERE dataset_id=? AND notice_uuid=?', (args.dataset, uid))
            database.upsert_notice(record, stored['source_files'].split(' | '), args.dataset)
            audits.append(audit)
            print(uid, audit['before']['packages'], '->', audit['after']['packages'], flush=True)
        after_all = {row['notice_uuid']: row['result_json'] for row in database.rows(
            'SELECT notice_uuid,result_json FROM notices WHERE dataset_id=?', (args.dataset,))}
        targets = {review['notice_uuid'] for review in reviews}
        if set(before_all) != set(after_all) or any(before_all[uid] != after_all[uid] for uid in before_all if uid not in targets):
            raise ValueError('修复改变了复核范围之外的公告')
        collisions = database.rows('''SELECT notice_uuid,lot_name,COUNT(*) n FROM projects
            WHERE dataset_id=? GROUP BY notice_uuid,lot_name HAVING COUNT(*)>1''', (args.dataset,))
        if collisions:
            raise ValueError('修复后仍有导出采购包键冲突: ' + json.dumps(collisions))
        records = [ProjectExtract.model_validate_json(value) for value in after_all.values()]
        workbook = workbook_bytes(records)
        schema = args.official_data / 'indexes/output-schema.json'
        acceptance = check_workbook(workbook, schema, set(after_all))
        if not acceptance['valid']:
            raise ValueError('修复后三表结构校验失败: ' + json.dumps(acceptance['errors']))
        # A concrete non-partitioned example must count its award once. Named
        # packages remain separate even when their sources were anonymous.
        relation_checks, partners = [], {}
        for record in records:
            if record.notice_uuid not in targets:
                continue
            for lot in record.lots:
                for bid in lot.bids:
                    if not bid.is_winner or not bid.eligible:
                        continue
                    if record.purchaser not in partners:
                        partners[record.purchaser] = analytics.purchaser_partners(record.purchaser, args.dataset)
                    found = next(row for row in partners[record.purchaser]['winning_suppliers']
                                 if row['supplier'] == bid.bidder)
                    expected = database.one('''SELECT COUNT(DISTINCT p.id) n FROM projects p
                        JOIN bids b ON b.project_id=p.id WHERE p.dataset_id=? AND p.purchaser=?
                        AND b.bidder=? AND b.is_winner=1 AND b.eligible=1''',
                        (args.dataset, record.purchaser, bid.bidder))['n']
                    if found['cooperation_count'] != expected:
                        raise ValueError('修复后的关系查询采购包归属不一致')
                    relation_checks.append({'notice_uuid': record.notice_uuid, 'supplier': bid.bidder,
                                            'expected_cooperation_count': expected, 'passed': True})
            pair = list(dict.fromkeys(b.bidder for lot in record.lots for b in lot.bids if b.eligible))[:2]
            if len(pair) == 2:
                actual = analytics.common_projects(pair, args.dataset)
                found = {p['lot_key'] for p in actual['projects'] if p['notice_uuid'] == record.notice_uuid}
                expected = {lot.lot_key for lot in record.lots
                            if set(pair) <= {b.bidder for b in lot.bids if b.eligible}}
                if found != expected:
                    raise ValueError('修复后的共同竞标跨包关系不一致')
                relation_checks.append({'notice_uuid': record.notice_uuid, 'suppliers': pair,
                                        'expected_package_keys': sorted(expected), 'passed': True})
        source_unchanged = sha256(source) == original_hash
        if not source_unchanged:
            raise ValueError('原库校验值在修复过程中变化，停止交付副本')
        # Ship a self-contained SQLite file, without live WAL/SHM sidecars.
        with closing(sqlite3.connect(database_path)) as final_database:
            final_database.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            final_database.execute('PRAGMA journal_mode=DELETE')
        report = {'dataset': args.dataset, 'source_database_sha256': original_hash,
                  'source_unchanged': source_unchanged, 'reviewed_notices': len(reviews),
                  'untouched_notices': len(before_all) - len(reviews), 'export_key_collisions': collisions,
                  'workbook': acceptance, 'relationship_checks': relation_checks,
                  'llm_requests': 0, 'official_score': False, 'submission_ready': False,
                  'scope': 'Reviewed package identity and three-sheet structure only; other extraction warnings remain.',
                  'notices': audits}
        (work / 'repair-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
        (work / 'before-notices.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in before_snapshots), 'utf-8')
        (work / 'predictions.jsonl').write_text(''.join(value + '\n' for value in after_all.values()), 'utf-8')
        (work / 'procurement-tables.xlsx').write_bytes(workbook)
        output.mkdir()
        for file in work.iterdir():
            if file.is_file():
                file.replace(output / file.name)
        print(json.dumps({'output': str(output), 'workbook_valid': True, 'source_unchanged': True,
                          'reviewed_notices': len(reviews), 'official_score': False}), flush=True)


if __name__ == '__main__':
    main()
