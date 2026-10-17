"""Compare disabled, cold and warm checkpoints using saved responses only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def business_results(path):
    return [{key: value for key, value in json.loads(line).items() if key != 'model_usage'}
            for line in path.read_text('utf-8').splitlines() if line.strip()]


def workbook_values(path):
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        return {sheet.title: list(sheet.values) for sheet in book}
    finally:
        book.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-run', type=Path, required=True)
    parser.add_argument('--official-data', type=Path, required=True)
    parser.add_argument('--parse-cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = read(args.original_run / 'run-manifest.json')
    ids = read(ROOT / 'demo/procurement/data/demo/manifest.json')['notice_ids']
    manifest = output / 'sample.json'
    manifest.write_text(json.dumps({'notices': ids}, indent=2), encoding='utf-8')
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
        PYTHONPATH=str(ROOT / 'demo/procurement/backend'),
        MODEL_NETWORK_ENABLED='false', DEEPSEEK_API_KEY='',
        MODEL_PROFILE_PATH=str(output / 'absent-profile.json'),
        MODEL_SETTINGS_PATH=str(output / 'absent-settings.json'),
        DEEPSEEK_BASE_URL=original['base_url'], DEEPSEEK_MODEL=original['model'],
        MODEL_MAX_TOKENS=str(original['settings']['model_max_tokens']),
        MAX_LLM_CHARS=str(original['settings']['max_llm_chars']),
        MODEL_THINKING=str(original['settings']['thinking']),
        MODEL_CONCURRENCY='4', PARSE_CONCURRENCY='2',
        MODEL_RESPONSE_CACHE_DIR=str((args.original_run / 'responses').resolve()),
        PARSE_CACHE_DIR=str(args.parse_cache.resolve()),
        OFFICIAL_DATA_DIR=str(args.official_data.resolve()),
        REFERENCE_SEED='false', DEMO_SEED='false', NEO4J_URI='')
    summaries = {}
    for phase in ('disabled', 'cold', 'warm'):
        phase_dir = output / phase
        environment['MODEL_DOCUMENT_CACHE_DIR'] = '' if phase == 'disabled' else str(output / 'checkpoints')
        environment['NOTICE_CONCURRENCY'] = '4' if phase == 'disabled' else '0'
        started = time.perf_counter()
        completed = subprocess.run([sys.executable, '-B', '-m', 'app.batch', '--mode', 'replay',
            '--dataset', 'resume-benchmark', '--manifest', str(manifest),
            '--database', str(output / (phase + '.db')), '--output', str(phase_dir)],
            env=environment, capture_output=True, text=True, encoding='utf-8', timeout=3600)
        seconds = time.perf_counter() - started
        (output / (phase + '.log')).write_text(completed.stdout + completed.stderr, encoding='utf-8')
        report_path = phase_dir / 'run-report.json'
        if not report_path.exists():
            raise RuntimeError(f'{phase} did not produce a report; see {phase}.log')
        report = read(report_path)
        job = report['job']
        if job['failed'] or job['completed'] != len(ids) or job['metrics']['model_calls']:
            raise RuntimeError(f'{phase} failed its zero-network completeness check; see report')
        records = business_results(phase_dir / 'predictions.jsonl')
        summaries[phase] = {'wall_seconds_including_exports': round(seconds, 4),
            'job_seconds': job['elapsed_seconds'], 'complete': job['succeeded'], 'partial': job['partial'],
            'failed': job['failed'], 'model_calls': job['metrics']['model_calls'],
            'response_cache_hits': job['metrics']['response_cache_hits'],
            'document_cache_hits': job['metrics']['document_cache_hits'],
            'document_cache_writes': job['metrics']['document_cache_writes'],
            'notice_concurrency': job['metrics']['notice_concurrency'],
            'results': report['results'], 'business_sha256': hashlib.sha256(json.dumps(records,
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
        print(json.dumps({'phase': phase, **summaries[phase]}, ensure_ascii=False), flush=True)
    baseline = business_results(output / 'disabled/predictions.jsonl')
    workbook = workbook_values(output / 'disabled/procurement-tables.xlsx')
    business_equal = all(business_results(output / phase / 'predictions.jsonl') == baseline
                         for phase in ('cold', 'warm'))
    workbooks_equal = all(workbook_values(output / phase / 'procurement-tables.xlsx') == workbook
                          for phase in ('cold', 'warm'))
    source_files = ['app/checkpoints.py', 'app/extractor.py', 'app/parsers.py', 'app/jobs.py']
    report = {'scope': '20-notice saved-response benchmark with an existing parse cache; '
                       'not fresh inference, first-run full-data speed, or official accuracy',
        'notice_ids': ids, 'phases': summaries, 'business_results_identical': business_equal,
        'three_sheet_values_identical': workbooks_equal, 'sheet_names': list(workbook),
        'code_sha256': {name: hashlib.sha256((ROOT / 'demo/procurement/backend' / name).read_bytes()).hexdigest()
                        for name in source_files},
        'warm_speedup_over_disabled': round(summaries['disabled']['wall_seconds_including_exports'] /
                                             summaries['warm']['wall_seconds_including_exports'], 3)}
    (output / 'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not business_equal or not workbooks_equal:
        raise RuntimeError('Business results or workbook values changed')


if __name__ == '__main__':
    main()
