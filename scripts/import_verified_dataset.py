"""Atomically replace one existing dataset from a verified SQLite file; keep jobs and other datasets."""
from __future__ import annotations
import argparse
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


def quoted(name):
    return '"'+name.replace('"','""')+'"'


def import_dataset(source: Path,target: Path,dataset: str,apply=False):
    source,target=source.resolve(),target.resolve()
    if source==target or not source.is_file() or not target.is_file():
        raise ValueError('Verified source and existing target must be different files')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as s, closing(sqlite3.connect(target.as_uri()+'?mode=ro',uri=True)) as t:
        ids={row[0] for row in s.execute('SELECT notice_uuid FROM notices WHERE dataset_id=?',(dataset,))}
        existing={row[0] for row in t.execute('SELECT DISTINCT notice_uuid FROM projects WHERE dataset_id=?',(dataset,))}
        if not ids or ids!=existing:raise ValueError('Existing target notice IDs must equal the verified dataset IDs')
        if t.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('processing','queued')").fetchone()[0]:
            raise ValueError('An extraction task is active; no data changed')
        if s.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or s.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Verified source integrity check failed')
        if s.execute('SELECT 1 FROM projects WHERE dataset_id=? GROUP BY notice_uuid,lot_name HAVING COUNT(*)>1',(dataset,)).fetchone():
            raise ValueError('Verified source has ambiguous export package keys')
        report={'dataset':dataset,'notices':len(ids),'source_sha256':source_hash,'apply':apply,
                'scope':'One verified dataset; original jobs, other datasets, credentials, model settings and uploads are preserved'}
        if not apply:return report
        backup_dir=target.parent/'.backups';backup_dir.mkdir(mode=0o700,exist_ok=True)
        backup=backup_dir/('before-closeout-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.db')
        with closing(sqlite3.connect(backup)) as b:t.backup(b)
    # Use the delivered application's own schema migration, not a separate guessed schema.
    from app import database
    original_settings=database.settings
    try:
        database.settings=replace(original_settings,database_path=target)
        database.init_db()
    finally:
        database.settings=original_settings
    with closing(sqlite3.connect(target,uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
        c.execute('ATTACH DATABASE ? AS verified',(source.as_uri()+'?mode=ro',))
        others=[tuple(row) for row in c.execute('SELECT * FROM notices WHERE dataset_id<>? ORDER BY dataset_id,notice_uuid',(dataset,))]
        try:
            c.execute('BEGIN IMMEDIATE')
            for row in c.execute('SELECT * FROM verified.projects WHERE dataset_id=? ORDER BY id',(dataset,)).fetchall():
                found=c.execute('SELECT id FROM projects WHERE dataset_id=? AND notice_uuid=? AND lot_key=?',
                                (dataset,row['notice_uuid'],row['lot_key'])).fetchone()
                columns=[key for key in row.keys() if key!='id'];values=[row[key] for key in columns]
                if found:
                    project_id=found['id']
                    c.execute('UPDATE projects SET '+','.join(quoted(key)+'=?' for key in columns)+' WHERE id=?',(*values,project_id))
                else:
                    cursor=c.execute('INSERT INTO projects('+','.join(map(quoted,columns))+') VALUES('+','.join('?' for _ in columns)+')',values)
                    project_id=cursor.lastrowid
                for table in ('items','bids'):
                    c.execute('DELETE FROM '+table+' WHERE project_id=?',(project_id,))
                    children=c.execute('SELECT * FROM verified.'+table+' WHERE project_id=? ORDER BY id',(row['id'],)).fetchall()
                    if children:
                        fields=[key for key in children[0].keys() if key not in {'id','project_id'}]
                        c.executemany('INSERT INTO '+table+'(project_id,'+','.join(map(quoted,fields))+') VALUES('+','.join('?' for _ in range(len(fields)+1))+')',
                                      [[project_id,*[child[key] for key in fields]] for child in children])
            c.execute('''DELETE FROM projects WHERE dataset_id=? AND NOT EXISTS (
                SELECT 1 FROM verified.projects v WHERE v.dataset_id=projects.dataset_id
                AND v.notice_uuid=projects.notice_uuid AND v.lot_key=projects.lot_key)''',(dataset,))
            c.execute('DELETE FROM notices WHERE dataset_id=?',(dataset,))
            c.execute('INSERT INTO notices SELECT * FROM verified.notices WHERE dataset_id=?',(dataset,))
            for label,schema in [('actual','main'),('expected','verified')]:
                counts=[c.execute('SELECT COUNT(*) FROM '+schema+'.projects WHERE dataset_id=?',(dataset,)).fetchone()[0]]
                for table in ('items','bids'):
                    counts.append(c.execute('SELECT COUNT(*) FROM '+schema+'.'+table+' child JOIN '+schema+'.projects p ON p.id=child.project_id WHERE p.dataset_id=?',(dataset,)).fetchone()[0])
                report[label]=counts
            if report['actual']!=report['expected']:raise ValueError('Imported row counts differ')
            if others!=[tuple(row) for row in c.execute('SELECT * FROM notices WHERE dataset_id<>? ORDER BY dataset_id,notice_uuid',(dataset,))]:
                raise ValueError('Another dataset changed')
            if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign-key check failed')
            if hashlib.sha256(source.read_bytes()).hexdigest()!=source_hash:raise ValueError('Source file changed')
            c.commit()
        except BaseException:
            c.rollback();raise
    report.update(backup=str(backup),passed=True,other_datasets_unchanged=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    # Both the repository and the API container expose an app package.
    backend=Path(__file__).resolve().parents[1]/'demo/procurement/backend'
    if backend.is_dir():sys.path.insert(0,str(backend))
    print(json.dumps(import_dataset(args.source,args.target,args.dataset,args.apply),ensure_ascii=False))


if __name__=='__main__':main()
