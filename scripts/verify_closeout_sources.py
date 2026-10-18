"""Read-only check of the reviewed package plan and source-backed P1 money/missing-row repairs."""
from __future__ import annotations
import argparse
from contextlib import closing
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'demo/procurement/backend'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from app.normalization import lot_identity
    from app.parsers import expand_archives,parse_file
    data=ROOT/'data/official-benchmark'
    source=ROOT/'results/competition-platform-20261018/platform.db'
    database=args.database.resolve();before=digest(database);dataset='official-full-20261018'
    reviews=json.loads((ROOT/'docs/verification/package-identity-review-20261018.json').read_text('utf-8'))['notices']
    saved={row['notice_uuid']:{doc['filename']:doc for doc in row['documents']}
           for row in json.loads((ROOT/'results/package-identity-20261018/source-review.json').read_text('utf-8'))}
    report={'dataset':dataset,'passed':False,'package_notices':0,'package_assignments':0,
            'source_proofs':0,'money_fields':0,'missing_rows':0,'cases':[],
            'official_score':False,'independent_human_gold':False,
            'scope':'Original file hashes, stored review excerpts, explicit source money and retained source-row checks; not a precision/recall score.'}
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db, closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
        records={uid:json.loads(raw) for uid,raw in db.execute('SELECT notice_uuid,result_json FROM notices WHERE dataset_id=?',(dataset,))}
        for review in reviews:
            uid=review['notice_uuid'];archive=data/'file'/(uid+'.zip')
            raw=original.execute('SELECT result_json FROM notices WHERE dataset_id=? AND notice_uuid=?',(dataset,uid)).fetchone()[0]
            assert hashlib.sha256(raw.encode()).hexdigest()==review['snapshot_sha256']
            assert digest(archive)==review['archive_sha256']
            with tempfile.TemporaryDirectory(prefix='closeout-proof-',dir=args.output.parent) as tmp:
                files=[data/'html'/(uid+'.html'),*expand_archives([archive],Path(tmp))]
                for proof in review['evidence']:
                    candidates=[p for p in files if p.name==proof['filename']]
                    assert candidates and all(digest(p)==proof['sha256'] for p in candidates)
                    assert proof['quote'] in saved[uid][proof['filename']]['text']
                    report['source_proofs']+=1
            labels={lot_identity(lot['lot_name']) for lot in records[uid]['lots']}
            assert all(lot_identity(a['lot_name']) in labels for a in review['assignments'])
            assert all(lot['lot_name'] for lot in records[uid]['lots'])
            report['package_notices']+=1;report['package_assignments']+=len(review['assignments'])
        audits=[json.loads(line) for line in (ROOT/'results/result-quality-20261018/notice-audit.jsonl').read_text('utf-8').splitlines()]
        for audit in audits:
            selected=[ch for ch in audit['changes'] if ch['action']=='missing_source_row' or
                      (ch['action']=='source_cell' and ch['field']=='awarded_amount')]
            if not selected:continue
            uid=audit['notice_uuid'];path=data/'html'/(uid+'.html');text=parse_file(path)
            proof=next(p for p in audit['sources'] if p['filename']==path.name)
            assert digest(path)==proof['sha256']
            for change in selected:
                assert change['evidence'] in text
                if change['action']=='source_cell':
                    quote=unicodedata.normalize('NFKC',change['evidence'])
                    amounts={Decimal(n.replace(',',''))*{'亿元':Decimal(10**8),'万元':Decimal(10**4),'千元':Decimal(1000),'元':Decimal(1)}[u]
                             for n,u in re.findall(r'(\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|千元|元)',quote)}
                    assert Decimal(str(change['after'])) in amounts
                    record=records[uid]
                    targets=[record] if change['scope']=='project' else [lot for lot in record['lots'] if lot['lot_name']==change['scope']]
                    assert len(targets)==1 and targets[0]['awarded_amount']==change['after']
                    report['money_fields']+=1
                else:
                    found=[item for lot in records[uid]['lots'] for item in lot['items']
                           if item['source_file']==change['source_file'] and item['line_id']==change['location']]
                    assert found
                    report['missing_rows']+=1
            report['cases'].append({'notice_uuid':uid,'checked_changes':len(selected)})
        assert report['package_notices']==10 and report['package_assignments']==40
        assert report['money_fields']==13 and report['missing_rows']==28
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert digest(database)==before
    report.update(passed=True,database_unchanged=True,database_sha256=before)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps({k:report[k] for k in ['passed','package_notices','package_assignments','source_proofs','money_fields','missing_rows']}))


if __name__=='__main__':main()
