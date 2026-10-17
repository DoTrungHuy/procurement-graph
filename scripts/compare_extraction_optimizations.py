"""Isolated full/sampled comparison against a frozen local extraction run.

Singles replay saved raw responses unless --live-misses explicitly regenerates
missing responses. --live-batches permits grouped requests. Both keep the original
zero-credit gate. This does not measure cold full inference or official accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('product_name', 'category', 'brand', 'model', 'unit_price', 'quantity', 'total_price')


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def records(path):
    return {r['notice_uuid']: r for line in path.read_text('utf-8').splitlines() if line.strip()
            for r in [json.loads(line)]}


def lots(record):
    return record.get('lots') or [record]


def rows(record, kind):
    result = []
    for lot in lots(record):
        for row in lot.get(kind, []):
            if kind == 'items':
                entry = {field: row.get(field) for field in FIELDS}
                entry.update(lot_name=lot.get('lot_name', ''), source_file=row.get('source_file', ''),
                             line_id=row.get('line_id', ''), supplier=row.get('supplier', ''),
                             quantity_unit=row.get('quantity_unit', ''))
            else:
                entry = {key: value for key, value in row.items() if key not in {'evidence', 'source_file'}}
                entry['lot_name'] = lot.get('lot_name', '')
            result.append(json.dumps(entry, sort_keys=True, ensure_ascii=False))
    return Counter(result)


def compare(before, after):
    differences, totals = [], Counter()
    for uid in sorted(set(before) | set(after)):
        if uid not in before or uid not in after:
            differences.append({'notice_uuid': uid, 'missing_side': 'baseline' if uid not in before else 'candidate'})
            continue
        entry = {'notice_uuid': uid}
        for kind in ('items', 'bids'):
            old, new = rows(before[uid], kind), rows(after[uid], kind)
            removed, added = list((old - new).elements()), list((new - old).elements())
            totals[kind + '_retained_exactly'] += sum((old & new).values())
            totals[kind + '_removed_or_changed'] += len(removed)
            totals[kind + '_added_or_changed'] += len(added)
            if removed or added:
                entry[kind] = {'removed_or_changed': [json.loads(x) for x in removed],
                               'added_or_changed': [json.loads(x) for x in added]}
        metadata = ('project_name', 'project_code', 'purchaser', 'awarded_amount', 'budget_amount')
        changes = {field: {'before': before[uid].get(field), 'after': after[uid].get(field)} for field in metadata
                   if before[uid].get(field) != after[uid].get(field)}
        old_lots = [{k: l.get(k) for k in ('lot_name', 'awarded_amount', 'budget_amount')} for l in lots(before[uid])]
        new_lots = [{k: l.get(k) for k in ('lot_name', 'awarded_amount', 'budget_amount')} for l in lots(after[uid])]
        if old_lots != new_lots:
            changes['lots'] = {'before': old_lots, 'after': new_lots}
        if changes:
            entry['project_or_lot_changes'] = changes
        if len(entry) > 1:
            differences.append(entry)
    return {'notice_count_before': len(before), 'notice_count_after': len(after),
            'notices_with_business_changes': len(differences), 'totals': dict(totals), 'differences': differences}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-run', type=Path, required=True)
    parser.add_argument('--official-data', type=Path, required=True)
    parser.add_argument('--parse-cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--notice-id', action='append', default=[])
    parser.add_argument('--live-batches', action='store_true')
    parser.add_argument('--live-misses', action='store_true', help='Regenerate exact raw-cache misses with the zero-credit gate')
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--response-cache', type=Path, action='append', default=[], help='Additional read-only saved responses')
    parser.add_argument('--tools-root', type=Path, help='Existing local 7zip/LibreOffice directory, shared with the baseline')
    parser.add_argument('--work-root', type=Path, help='Short project-local scratch path for Windows archive member limits')
    parser.add_argument('--control', action='store_true', help='Disable all three new routes')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = read(args.original_run / 'run-manifest.json')
    baseline = records(args.original_run / 'full/predictions.jsonl')
    ids = args.notice_id or sorted(baseline)
    selected = {uid: baseline[uid] for uid in ids}
    source_hashes = {name: hashlib.sha256((args.original_run / 'full' / name).read_bytes()).hexdigest()
                     for name in ('predictions.jsonl', 'run-report.json', 'procurement-tables.xlsx')}
    manifest = output / 'sample.json'
    manifest.write_text(json.dumps({'notices': ids}), encoding='utf-8')
    live = args.live_batches or args.live_misses
    profile = read(args.profile) if live and args.profile else {}
    if live and not profile.get('api_key'):
        raise ValueError('Live requests require an existing profile; no credential is logged')
    if args.tools_root:
        tools = args.tools_root.resolve()
        paths = [tools / 'sevenzip', tools / 'libreoffice/program', tools / 'libreoffice/LibreOffice/program']
        os.environ['PATH'] = os.pathsep.join([*(str(p) for p in paths if p.is_dir()), os.environ.get('PATH', '')])
    os.environ.update(PYTHONDONTWRITEBYTECODE='1', MODEL_NETWORK_ENABLED=str(live),
        MODEL_REQUIRE_FREE='true', DEEPSEEK_API_KEY=profile.get('api_key', ''),
        MODEL_PROFILE_PATH=str(output / 'absent-profile.json'), MODEL_SETTINGS_PATH=str(output / 'absent-settings.json'),
        DEEPSEEK_BASE_URL=original['base_url'], DEEPSEEK_MODEL=original['model'],
        MODEL_MAX_TOKENS=str(original['settings']['model_max_tokens']), MAX_LLM_CHARS=str(original['settings']['max_llm_chars']),
        MODEL_THINKING=str(original['settings']['thinking']), MODEL_CONCURRENCY='4', PARSE_CONCURRENCY='2',
        MODEL_RESPONSE_CACHE_DIR=str(output / 'responses'),
        MODEL_RESPONSE_CACHE_READ_DIRS=os.pathsep.join(str(p.resolve()) for p in [*args.response_cache, args.original_run / 'responses']),
        MODEL_REPLAY_CHUNKING='auto',
        MODEL_DOCUMENT_CACHE_DIR=str(output / 'checkpoints'), PARSE_CACHE_DIR=str(args.parse_cache.resolve()),
        OFFICIAL_DATA_DIR=str(args.official_data.resolve()), REFERENCE_SEED='false', DEMO_SEED='false', NEO4J_URI='',
        DATABASE_PATH=str(output / 'extraction.db'), UPLOAD_DIR=str(args.work_root.resolve() if args.work_root else output / 'work'),
        EXTRACT_BLANK_TEMPLATES=str(not args.control), EXTRACT_STANDARD_TABLES=str(not args.control),
        EXTRACT_BATCH_ATTACHMENTS=str(not args.control))
    sys.path.insert(0, str(ROOT / 'demo/procurement/backend'))
    from app import batch, jobs
    from app.extractor import DeepSeekExtractor, FatalModelError
    from app.parsers import seven_zip, office_tool
    tool_paths = {'7zip': seven_zip(), 'office': office_tool()}
    if args.tools_root and not all(tool_paths.values()):
        raise ValueError('The supplied baseline tool directory is incomplete; abort before processing')

    class HybridExtractor(DeepSeekExtractor):
        async def _call(self, text, names, **kwargs):
            # self.cache_only remains True for exact historical single-file context.
            try:
                return await super()._call(text, names, cache_only_override=not (args.live_batches and kwargs.get('decoder')), **kwargs)
            except FatalModelError:
                if self.fatal_error or not args.live_misses or kwargs.get('decoder'):
                    raise
                return await super()._call(text, names, cache_only_override=False, **kwargs)

    jobs.DeepSeekExtractor = HybridExtractor
    configuration = argparse.Namespace(mode='replay', run_model=False, retry_job=None, manifest=manifest,
        notice_id=[], limit=None, announcement_only=False, dataset='optimization-comparison', output=output)
    started = time.perf_counter()
    report = asyncio.run(batch.execute(configuration))
    after = records(output / 'predictions.jsonl')
    comparison = compare(selected, after)
    comparison.update(scope='Local hybrid evaluation with saved single responses and explicit live switches. '
        'Optional exact cache-miss regeneration is counted as new requests. Not cold full-inference timing or official accuracy.',
        live_batches=args.live_batches, live_misses=args.live_misses, control=args.control,
        parser_tools=tool_paths,
        wall_seconds=round(time.perf_counter() - started, 4),
        baseline_sha256=source_hashes, baseline_unchanged=all(hashlib.sha256((args.original_run / 'full' / name).read_bytes()).hexdigest() == value
            for name, value in source_hashes.items()), job=report['job'], results=report['results'])
    # The job report is already saved separately; keep comparison easier to inspect.
    comparison['job'] = {key: comparison['job'][key] for key in
        ('status', 'completed', 'total', 'succeeded', 'partial', 'failed', 'elapsed_seconds', 'metrics')}
    (output / 'comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in comparison.items() if key not in {'differences', 'job'}}, ensure_ascii=False), flush=True)
    compact = dict(comparison['job'])
    compact['metrics'] = {k:v for k,v in compact['metrics'].items() if k != 'response_cache_keys'}
    print(json.dumps(compact, ensure_ascii=False), flush=True)
    if report['job']['failed'] or not comparison['baseline_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
