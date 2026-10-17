"""Build a credential-free source bundle with optional demonstration deliverables."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from audit_publish import safe_release_path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'deliverables/procurement-release.zip')
    parser.add_argument('--include-materials',action='store_true')
    parser.add_argument('--results-dir',type=Path,default=ROOT/'results/offline-release')
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(ROOT) or output.suffix!='.zip':
        parser.error('Output must be a ZIP inside the project')
    audit=ROOT/'.cache/maintenance/publish-audit.json'
    subprocess.run([sys.executable,str(ROOT/'scripts/audit_publish.py'),'--output',str(audit)],cwd=ROOT,check=True,capture_output=True)
    report=json.loads(audit.read_text('utf-8'))
    selected={name:ROOT/name for name in report['files']}
    if args.include_materials:
        for path in (ROOT/'deliverables').iterdir():
            if path.suffix.lower() in {'.docx','.pdf','.pptx','.mp4'}:
                selected['materials/'+path.name]=path
    result_names=['procurement-tables.xlsx','predictions.jsonl','relation-model.json','quality-report.json','acceptance.json']
    for name in result_names:
        path=args.results_dir/name
        if name=='procurement-tables.xlsx' and not path.is_file():
            path=args.results_dir/'competition-results.xlsx'  # Read historical runs under their original name.
        if path.is_file():
            selected['sample-results/'+name]=path
    from dotenv import dotenv_values
    secrets=[value.encode() for key,value in dotenv_values(ROOT/'.env').items()
             if value and len(value)>=10 and any(word in key for word in ['KEY','TOKEN','PASSWORD'])]
    def validate(name,data,depth=0):
        if any(value in data for value in secrets) or re.search(rb'\bsk-[A-Za-z0-9_-]{20,}',data):
            raise ValueError('Possible private credential in '+name)
        if depth<2 and data.startswith(b'PK') and Path(name).suffix in {'.zip','.docx','.pptx','.xlsx'}:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for member in archive.infolist():
                    if not member.is_dir() and member.file_size<=32*1024**2:
                        validate(name+'!'+member.filename,archive.read(member),depth+1)
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix('.zip.part')
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'status':'submission-candidate',
              'application_version':re.search(r"__version__\s*=\s*'([^']+)'", (ROOT/'demo/procurement/backend/app/__init__.py').read_text('utf-8')).group(1),
              'scope':'source, 20 bundled notices, historical responses and optional local materials',
              'sample_results_are_full_official_results':False,
              'pending':['live public deployment and model acceptance','stage-specific model policy/evidence','complete official extraction results','final templates if issued'],
              'credentials_included':False,'files':{}}
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,path in sorted(selected.items()):
                if not safe_release_path(name) or Path(name).name=='.env':
                    raise ValueError('Invalid release path: '+name)
                data=path.read_bytes();validate(name,data)
                archive.writestr('procurement-project/'+name,data)
                manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            archive.writestr('procurement-project/release-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({'output':str(output),'files':len(selected),'bytes':output.stat().st_size,'credentials_included':False,'scope':manifest['status']},ensure_ascii=False))


if __name__=='__main__':
    main()
