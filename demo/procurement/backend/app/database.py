from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings
from .models import ProjectExtract
from .normalization import lot_identity, model_match_key

# A projects row is a procurement package. notice_uuid groups its parent notice.
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_code TEXT NOT NULL DEFAULT '',
 name TEXT NOT NULL, purchaser TEXT NOT NULL DEFAULT '', budget_amount REAL,
 awarded_amount REAL, announcement_date TEXT NOT NULL DEFAULT '',
 source_files TEXT NOT NULL DEFAULT '', extraction_method TEXT NOT NULL DEFAULT '',
 warnings TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bids (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 bidder TEXT NOT NULL, is_winner INTEGER NOT NULL DEFAULT 0, bid_amount REAL,
 score REAL, rank INTEGER, evidence TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 winner_supplier TEXT NOT NULL DEFAULT '', product_name TEXT NOT NULL DEFAULT '',
 category TEXT NOT NULL DEFAULT '', brand TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
 unit_price REAL, quantity REAL, total_price REAL, source TEXT NOT NULL DEFAULT 'announcement',
 evidence TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notices (
 dataset_id TEXT NOT NULL, notice_uuid TEXT NOT NULL, input_hash TEXT NOT NULL DEFAULT '',
 result_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(dataset_id,notice_uuid)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, status TEXT NOT NULL,
 total INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
 succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
 partial INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0,
 errors TEXT NOT NULL DEFAULT '[]', manifest TEXT NOT NULL DEFAULT '[]',
 workdir TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'extract',
 started_at TEXT, completed_at TEXT, elapsed_seconds REAL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS job_documents (
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, notice_uuid TEXT NOT NULL,
 status TEXT NOT NULL, result_json TEXT, errors TEXT NOT NULL DEFAULT '[]',
 elapsed_seconds REAL, PRIMARY KEY(job_id,notice_uuid)
);
"""
ADDITIONS = {
 'jobs': {'metrics': "TEXT NOT NULL DEFAULT '{}'"},
 'projects': {'notice_uuid': "TEXT NOT NULL DEFAULT ''", 'lot_name': "TEXT NOT NULL DEFAULT ''",
  'lot_key': "TEXT NOT NULL DEFAULT ''",
  'dataset_id': "TEXT NOT NULL DEFAULT 'default'", 'source_url': "TEXT NOT NULL DEFAULT ''",
  'input_hash': "TEXT NOT NULL DEFAULT ''", 'processing_status': "TEXT NOT NULL DEFAULT 'complete'",
  'notice_awarded_amount': 'REAL'},
 'bids': {'review_status': "TEXT NOT NULL DEFAULT '未披露'", 'qualification_status': "TEXT NOT NULL DEFAULT '未披露'",
  'evaluation_scores': "TEXT NOT NULL DEFAULT '[]'",
  'compliance_status': "TEXT NOT NULL DEFAULT '未披露'", 'eligible': 'INTEGER NOT NULL DEFAULT 0',
  'review_reason': "TEXT NOT NULL DEFAULT ''", 'non_winning_reason': "TEXT NOT NULL DEFAULT ''",
  'consortium_id': "TEXT NOT NULL DEFAULT ''", 'source_file': "TEXT NOT NULL DEFAULT ''",
  'related_sources': "TEXT NOT NULL DEFAULT '[]'", 'identity_confirmed':'INTEGER NOT NULL DEFAULT 1'},
 'items': {'line_id': "TEXT NOT NULL DEFAULT ''", 'source_file': "TEXT NOT NULL DEFAULT ''",
  'source_location': "TEXT NOT NULL DEFAULT ''", 'quantity_unit': "TEXT NOT NULL DEFAULT ''",
  'derived_values': "TEXT NOT NULL DEFAULT '{}'", 'business_line_id': "TEXT NOT NULL DEFAULT ''",
  'related_sources': "TEXT NOT NULL DEFAULT '[]'",
  'aggregation_eligible': 'INTEGER NOT NULL DEFAULT 1', 'aggregation_reason': "TEXT NOT NULL DEFAULT ''",
   'model_key': "TEXT NOT NULL DEFAULT ''"},
}


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(settings.database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        old = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()
        old_columns = {r['name'] for r in c.execute('PRAGMA table_info(projects)')} if old else set()
        migration = old and 'notice_uuid' not in old_columns
        key_migration = old and 'lot_key' not in old_columns
        if migration or key_migration:
            backup = settings.database_path.with_suffix('.pre-v2.db' if migration else '.pre-v3.db')
            if not backup.exists():
                target = sqlite3.connect(backup)
                try:
                    c.backup(target)
                finally:
                    target.close()
        c.executescript(SCHEMA)
        for table, additions in ADDITIONS.items():
            present = {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            for name, declaration in additions.items():
                if name not in present:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        if migration:
            for row in c.execute('SELECT id,source_files,extraction_method FROM projects').fetchall():
                notice = Path(row['source_files'].split(' | ')[0]).stem if row['source_files'] else ''
                demo = row['extraction_method'] == 'demo-seed'
                dataset = 'demo' if demo else 'default'
                # Preserve duplicate historical rows in a separate, explicit namespace.
                duplicate = c.execute('SELECT 1 FROM projects WHERE dataset_id=? AND notice_uuid=? AND id<>?',
                                      (dataset, notice, row['id'])).fetchone() if notice else None
                if duplicate:
                    dataset = f"legacy-duplicate-{row['id']}"
                c.execute('UPDATE projects SET notice_uuid=?,dataset_id=?,lot_name=? WHERE id=?',
                          (notice, dataset, '未分包' if demo else '', row['id']))
                if demo:
                    c.execute("UPDATE bids SET review_status='通过',eligible=1 WHERE project_id=?", (row['id'],))
                else:
                    c.execute('UPDATE bids SET eligible=is_winner WHERE project_id=?', (row['id'],))
        if key_migration:
            seen = set()
            for row in c.execute('SELECT id,dataset_id,notice_uuid,lot_name FROM projects ORDER BY id').fetchall():
                key = lot_identity(row['lot_name']) or f"unassigned:{row['id']}"
                identity = (row['dataset_id'], row['notice_uuid'], key)
                if identity in seen:
                    key = f"legacy:{row['id']}"
                    c.execute("UPDATE projects SET processing_status='partial',warnings=warnings || ' | 旧库包编号别名重复，保留待重跑核验' WHERE id=?", (row['id'],))
                seen.add(identity)
                c.execute('UPDATE projects SET lot_key=? WHERE id=?', (key, row['id']))
        c.executescript("""
        DROP INDEX IF EXISTS idx_notice_lot;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_notice_lot_key ON projects(dataset_id,notice_uuid,lot_key) WHERE notice_uuid<>'';
        CREATE INDEX IF NOT EXISTS idx_projects_purchaser ON projects(purchaser);
        CREATE INDEX IF NOT EXISTS idx_projects_dataset ON projects(dataset_id,notice_uuid);
        CREATE INDEX IF NOT EXISTS idx_bids_project ON bids(project_id);
        CREATE INDEX IF NOT EXISTS idx_bids_bidder ON bids(bidder);
        CREATE INDEX IF NOT EXISTS idx_bids_winner ON bids(is_winner,bidder);
        CREATE INDEX IF NOT EXISTS idx_items_project ON items(project_id);
        CREATE INDEX IF NOT EXISTS idx_items_search ON items(product_name,category,brand);
        CREATE INDEX IF NOT EXISTS idx_items_model_key ON items(model_key);
        PRAGMA user_version=5;
        """)
        for row in c.execute('SELECT id, model, model_key FROM items').fetchall():
            key = model_match_key(row['model'])
            if key != row['model_key']:
                c.execute('UPDATE items SET model_key=? WHERE id=?', (key, row['id']))


def upsert_notice(result: ProjectExtract, source_files: list[str], dataset_id: str = 'default') -> list[int]:
    from .extractor import validate_result
    result = result.model_copy(deep=True)
    validate_result(result)
    if not result.notice_uuid:
        result.notice_uuid = Path(source_files[0]).stem if source_files else ''
    if not result.notice_uuid:
        raise ValueError('公告缺少来源标识，不能入库')
    lots = result.package_records()
    keys = [lot.lot_key for lot in lots]
    if len(keys) != len(set(keys)):
        raise ValueError('同一公告存在重复采购包编号，须先完成合并')
    ids = []
    with connect() as c:
        old_rows = c.execute('SELECT id,lot_key FROM projects WHERE dataset_id=? AND notice_uuid=?',
                             (dataset_id, result.notice_uuid)).fetchall()
        for old in old_rows:
            if old['lot_key'] not in keys:
                c.execute('DELETE FROM projects WHERE id=?', (old['id'],))
        for lot in lots:
            values = (result.project_code, result.project_name or result.notice_uuid, result.purchaser,
                      lot.budget_amount, lot.awarded_amount, result.announcement_date, ' | '.join(source_files),
                      result.extraction_method, ' | '.join(dict.fromkeys(result.warnings)), result.source_url,
                      result.input_hash, result.processing_status, result.awarded_amount, lot.lot_name)
            found = c.execute('SELECT id FROM projects WHERE dataset_id=? AND notice_uuid=? AND lot_key=?',
                              (dataset_id, result.notice_uuid, lot.lot_key)).fetchone()
            columns = 'project_code,name,purchaser,budget_amount,awarded_amount,announcement_date,source_files,extraction_method,warnings,source_url,input_hash,processing_status,notice_awarded_amount,lot_name'
            if found:
                project_id = found['id']
                c.execute('UPDATE projects SET '+','.join(x+'=?' for x in columns.split(','))+' WHERE id=?', values+(project_id,))
                c.execute('DELETE FROM bids WHERE project_id=?', (project_id,))
                c.execute('DELETE FROM items WHERE project_id=?', (project_id,))
            else:
                cursor = c.execute(f'INSERT INTO projects({columns},dataset_id,notice_uuid,lot_key) VALUES({",".join("?" for _ in range(len(values)+3))})',
                                   values+(dataset_id,result.notice_uuid,lot.lot_key))
                project_id = int(cursor.lastrowid)
            ids.append(project_id)
            c.executemany("""INSERT INTO bids(project_id,bidder,is_winner,bid_amount,score,rank,evidence,review_status,
                qualification_status,compliance_status,eligible,review_reason,non_winning_reason,consortium_id,source_file,evaluation_scores,related_sources,identity_confirmed)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                (project_id,b.bidder,int(b.is_winner),b.bid_amount,b.score,b.rank,b.evidence,b.review_status,
                  b.qualification_status,b.compliance_status,int(b.eligible),b.review_reason,b.non_winning_reason,b.consortium_id,b.source_file,json.dumps(b.evaluation_scores),json.dumps(b.related_sources,ensure_ascii=False),int(b.identity_confirmed))
                for b in lot.bids if b.bidder])
            c.executemany("""INSERT INTO items(project_id,winner_supplier,product_name,category,brand,model,model_key,unit_price,
                quantity,total_price,source,evidence,confidence,line_id,source_file,source_location,quantity_unit,derived_values,business_line_id,related_sources,aggregation_eligible,aggregation_reason)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                (project_id,i.supplier,i.product_name,i.category,i.brand,i.model,i.model_key,i.unit_price,i.quantity,i.total_price,
                 i.source,i.evidence,i.confidence,i.line_id,i.source_file,i.source_location,i.quantity_unit,
                 json.dumps(i.derived_values,ensure_ascii=False),i.business_line_id,
                  json.dumps(i.related_sources,ensure_ascii=False),int(i.aggregation_eligible),i.aggregation_reason) for i in lot.items])
        c.execute("""INSERT INTO notices(dataset_id,notice_uuid,input_hash,result_json) VALUES(?,?,?,?)
            ON CONFLICT(dataset_id,notice_uuid) DO UPDATE SET input_hash=excluded.input_hash,
            result_json=excluded.result_json,updated_at=CURRENT_TIMESTAMP""",
            (dataset_id,result.notice_uuid,result.input_hash,result.model_dump_json()))
    return ids


def insert_project(result: ProjectExtract, source_files: list[str], dataset_id: str = 'default') -> int:
    return upsert_notice(result,source_files,dataset_id)[0]


def rows(query: str, params: tuple = ()) -> list[dict]:
    with connect() as c:
        return [dict(row) for row in c.execute(query,params).fetchall()]


def one(query: str, params: tuple = ()) -> dict:
    result = rows(query,params)
    return result[0] if result else {}
