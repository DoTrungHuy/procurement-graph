"""Package only the reviewed website changes and one verified dataset, without runtime credentials."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import time
import argparse

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'deliverables/deployment/competition-closeout-20261018.tar.gz')
    args=parser.parse_args()
    database=ROOT/'results/competition-closeout-20261018/platform.db'
    temporary=ROOT/'.cache/closeout/delivery.db';temporary.parent.mkdir(parents=True,exist_ok=True)
    if temporary.exists():raise ValueError('Delivery staging database already exists')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(temporary)) as target:
        source.backup(target)
        target.execute('PRAGMA foreign_keys=ON')
        target.execute("DELETE FROM projects WHERE dataset_id<>'official-full-20261018'")
        target.execute("DELETE FROM notices WHERE dataset_id<>'official-full-20261018'")
        target.execute('DELETE FROM job_documents');target.execute('DELETE FROM jobs');target.commit()
        target.execute('VACUUM')
    paths=subprocess.check_output(['git','diff','c2012e6','--name-only','--','demo/procurement/backend/app'],cwd=ROOT,text=True).splitlines()
    # These parser changes belong to other unfinished work, not this release.
    paths=[name for name in paths if name.endswith('.py') and not name.endswith('/parsers.py')]
    tracked=subprocess.check_output(['git','ls-files','demo/procurement/frontend'],cwd=ROOT,text=True).splitlines()
    paths.extend(tracked)
    files={name:(ROOT/name).read_bytes() for name in paths if (ROOT/name).is_file()}
    for path in (ROOT/'demo/procurement/frontend/dist').rglob('*'):
        if path.is_file():files[path.relative_to(ROOT).as_posix()]=path.read_bytes()
    # Newly added source modules are already tracked before packaging this release.
    manifest={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'application_version':'2.4.1','dataset':'official-full-20261018','notice_count':1038,
              'row_counts':[2214,21599,6511],
              'scope':'Reviewed code plus one verified dataset; no credentials, model settings, jobs, uploads or extraction caches',
              'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()},
              'verified_database_sha256':hashlib.sha256(temporary.read_bytes()).hexdigest()}
    output=args.output;output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise ValueError('Closeout archive already exists')
    with tarfile.open(output,'w:gz') as archive:
        def add(name,data):
            entry=tarfile.TarInfo('competition-closeout/'+name);entry.size=len(data);entry.mode=0o644
            entry.mtime=int(time.time())
            archive.addfile(entry,io.BytesIO(data))
        for name,data in files.items():add('payload/'+name,data)
        add('site-update-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
        add('scripts/apply_site_update.py',(ROOT/'scripts/apply_site_update.py').read_bytes())
        add('verified/import_verified_dataset.py',(ROOT/'scripts/import_verified_dataset.py').read_bytes())
        add('verified/platform.db',temporary.read_bytes())
    print(json.dumps({'archive':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),**manifest},ensure_ascii=False))


if __name__=='__main__':main()
