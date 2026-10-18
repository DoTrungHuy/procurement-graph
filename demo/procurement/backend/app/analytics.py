from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from itertools import combinations

from .database import rows
from .normalization import normalize_org


def _data(dataset_id: str, include_items: bool=False):
    from .config import settings
    if settings.relation_backend == 'neo4j':
        from .graph_store import read_dataset
        return read_dataset(dataset_id, include_items)
    if settings.relation_backend != 'sqlite':
        raise ValueError('RELATION_BACKEND must be sqlite or neo4j')
    packages = {p['id']:p for p in rows("SELECT * FROM projects WHERE dataset_id=? AND processing_status<>'fallback'",(dataset_id,))}
    bids = rows("SELECT b.* FROM bids b JOIN projects p ON p.id=b.project_id WHERE p.dataset_id=? AND p.processing_status<>'fallback'",(dataset_id,))
    items = rows("SELECT i.* FROM items i JOIN projects p ON p.id=i.project_id WHERE p.dataset_id=? AND p.processing_status<>'fallback'",(dataset_id,)) if include_items else []
    return packages,bids,items


def _bid_index(bids):
    grouped = defaultdict(list)
    for bid in bids:
        grouped[bid['project_id']].append(bid)
    return grouped


def _sum(values):
    values = [Decimal(str(x)) for x in values if x is not None]
    return float(sum(values).quantize(Decimal('.01'))) if values else None


def _package_amount(package, bids):
    if package['awarded_amount'] is not None:
        return package['awarded_amount']
    winners = [b for b in bids if b['project_id']==package['id'] and b['is_winner'] and b['eligible']]
    facts = {}
    for bid in winners:
        key = bid['consortium_id'] or 'supplier:'+bid['bidder']
        facts.setdefault(key,[]).append(bid['bid_amount'])
    values = []
    for amounts in facts.values():
        distinct = {x for x in amounts if x is not None}
        if len(distinct) != 1:
            return None
        values.append(next(iter(distinct)))
    return _sum(values)


def _supplier_amount(bid, package, bids):
    if bid['consortium_id']:
        members = [b for b in bids if b['project_id']==package['id'] and b['consortium_id']==bid['consortium_id']]
        if len(members)>1:
            return None  # A group award is not an individual member's share.
    winners = [b for b in bids if b['project_id']==package['id'] and b['is_winner'] and b['eligible']]
    if len(winners)==1 and package['awarded_amount'] is not None:
        return package['awarded_amount']
    return bid['bid_amount']


def purchaser_partners(purchaser: str, dataset_id: str='default') -> dict:
    packages,bids,items = _data(dataset_id,include_items=True)
    by_package = _bid_index(bids)
    selected = {k:p for k,p in packages.items() if p['purchaser']==purchaser}
    grouped = defaultdict(list)
    for bid in bids:
        if bid['project_id'] in selected and bid['is_winner'] and bid['eligible']:
            grouped[bid['bidder']].append(bid)
    suppliers = []
    for name,records in grouped.items():
        amounts = [_supplier_amount(b,selected[b['project_id']],by_package[b['project_id']]) for b in records]
        dates = [selected[b['project_id']]['announcement_date'] for b in records if selected[b['project_id']]['announcement_date']]
        suppliers.append({'supplier':name,'cooperation_count':len({b['project_id'] for b in records}),
                          'total_amount':_sum(amounts),'unknown_amount_count':sum(x is None for x in amounts),
                          'first_date':min(dates) if dates else '', 'last_date':max(dates) if dates else ''})
    suppliers.sort(key=lambda x:(-x['cooperation_count'],-(x['total_amount'] or 0),x['supplier']))
    brands = defaultdict(list)
    for item in items:
        if item['project_id'] in selected and item['brand']:
            brands[item['brand']].append(item)
    products = [{'supplier':name,'cooperation_count':len({i['project_id'] for i in records}),
                 'item_count':len(records),
                 'total_amount':None if any(not i.get('aggregation_eligible',1) for i in records) else _sum(i['total_price'] for i in records),
                 'unconfirmed_amount_item_count':sum(not i.get('aggregation_eligible',1) for i in records),
                 'amount_status':'pending_review' if any(not i.get('aggregation_eligible',1) for i in records) else 'disclosed',
                 'unknown_amount_count':sum(i['total_price'] is None for i in records)} for name,records in brands.items()]
    products.sort(key=lambda x:(-x['cooperation_count'],-(x['total_amount'] or 0),x['supplier']))
    return {'purchaser':purchaser,'winning_suppliers':suppliers,'product_suppliers':products,
            'count_unit':'采购包','amount_note':'个人份额未知时留空；联合体总额不按成员重复归属'}


def _bidder_stats(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[record['bidder']].append(record)
    result = []
    for name,items in grouped.items():
        unique = {b['project_id']:b for b in items}
        result.append({'bidder':name,'frequency':len(unique),'wins':sum(bool(x['is_winner']) for x in unique.values()),
                       'total_bid_amount':_sum(x['bid_amount'] for x in unique.values()),
                       'unknown_amount_count':sum(x['bid_amount'] is None for x in unique.values())})
    return sorted(result,key=lambda x:(-x['frequency'],-(x['total_bid_amount'] or 0),x['bidder']))


def purchaser_bidders(purchaser: str, dataset_id: str='default') -> dict:
    packages,bids,_ = _data(dataset_id)
    records = [b for b in bids if packages[b['project_id']]['purchaser']==purchaser and b['eligible']]
    by_package = defaultdict(set)
    for bid in records:
        if packages[bid['project_id']]['lot_name']:
            by_package[bid['project_id']].add(bid['bidder'])
    pairs = defaultdict(int)
    for names in by_package.values():
        for pair in combinations(sorted(names),2):
            pairs[pair] += 1
    return {'purchaser':purchaser,'top_bidders':_bidder_stats(records)[:5],
            'collaborative_pairs':[{'bidder_a':pair[0],'bidder_b':pair[1],'frequency':count}
                for pair,count in sorted(pairs.items(),key=lambda x:(-x[1],x[0]))[:10]],
            'count_unit':'同一采购包；中标者包含在有效竞标集合内'}


def supplier_competitors(supplier: str, dataset_id: str='default', history_scope: str='all') -> dict:
    packages,bids,_ = _data(dataset_id)
    own = {b['project_id'] for b in bids if b['bidder']==supplier and b['eligible']
           and packages[b['project_id']]['lot_name'] and (history_scope!='winning' or b['is_winner'])}
    result = _bidder_stats([b for b in bids if b['project_id'] in own and b['eligible'] and b['bidder']!=supplier])
    return {'supplier':supplier,'top5':result[:5],'all_participants':result,'history_scope':history_scope,
            'amount_note':'累计已披露投标报价，缺失额不按零推定'}


def _names(suppliers):
    return list(dict.fromkeys(normalize_org(name) for name in suppliers if normalize_org(name)))


def common_purchasers(suppliers: list[str], dataset_id: str='default') -> dict:
    suppliers = _names(suppliers)
    if len(suppliers)<2:
        return {'suppliers':suppliers,'purchasers':[]}
    packages,bids,_ = _data(dataset_id)
    grouped = defaultdict(list)
    by_package = _bid_index(bids)
    for bid in bids:
        if bid['bidder'] in suppliers and bid['is_winner'] and bid['eligible'] and packages[bid['project_id']]['purchaser']:
            grouped[packages[bid['project_id']]['purchaser']].append(bid)
    result = []
    for buyer,records in grouped.items():
        if {b['bidder'] for b in records} != set(suppliers):
            continue
        ids = sorted({b['project_id'] for b in records})
        amounts = [_package_amount(packages[i],by_package[i]) for i in ids]
        details = [{'supplier':name,'cooperation_count':len({b['project_id'] for b in records if b['bidder']==name}),
                    'total_amount':_sum(_supplier_amount(b,packages[b['project_id']],by_package[b['project_id']]) for b in records if b['bidder']==name)} for name in suppliers]
        result.append({'purchaser':buyer,'matched_suppliers':len(suppliers),'cooperation_count':len(ids),
                       'total_amount':_sum(amounts),'unknown_amount_count':sum(x is None for x in amounts),
                       'supplier_details':details})
    result.sort(key=lambda x:(-x['cooperation_count'],-(x['total_amount'] or 0),x['purchaser']))
    return {'suppliers':suppliers,'purchasers':result,'amount_note':'关联采购包金额去重汇总，并非重复累加各主体的项目总额'}


def common_projects(suppliers: list[str], dataset_id: str='default') -> dict:
    suppliers = _names(suppliers)
    packages,bids,_ = _data(dataset_id)
    grouped = defaultdict(list)
    for bid in bids:
        if bid['eligible']:
            grouped[bid['project_id']].append(bid)
    result = []
    if len(suppliers)>=2:
        for project_id,records in grouped.items():
            package = packages[project_id]
            if not package['lot_name'] or not set(suppliers).issubset({b['bidder'] for b in records}):
                continue
            data = dict(package)
            data['awarded_amount'] = _package_amount(package,records)
            data['results'] = '；'.join(b['bidder']+('：中标' if b['is_winner'] else '：未中标') for b in records)
            result.append(data)
    result.sort(key=lambda x:(x['announcement_date'],x['notice_uuid'],x['lot_name']),reverse=True)
    amounts = [p['awarded_amount'] for p in result]
    return {'suppliers':suppliers,'summary':{'project_count':len(result),'notice_count':len({p['notice_uuid'] for p in result}),
             'total_amount':_sum(amounts),'unknown_amount_count':sum(x is None for x in amounts)},'projects':result,'count_unit':'采购包'}


def relationship_graph(limit: int | None=80, dataset_id: str='default') -> dict:
    packages,bids,_ = _data(dataset_id)
    ids = set(sorted(packages,reverse=True)[:limit])
    nodes,links = {},[]
    for project_id in ids:
        p = packages[project_id]
        key,buyer = f'project:{project_id}','purchaser:'+p['purchaser']
        nodes[key] = {'id':key,'name':p['name']+' '+p['lot_name'],'type':'project','value':p['awarded_amount']}
        if p['purchaser']:
            nodes[buyer] = {'id':buyer,'name':p['purchaser'],'type':'purchaser'}
            links.append({'source':buyer,'target':key,'relation':'采购'})
    for bid in bids:
        if bid['project_id'] not in ids or not bid['eligible']:
            continue
        key = 'supplier:'+bid['bidder']
        nodes[key] = {'id':key,'name':bid['bidder'],'type':'supplier'}
        links.append({'source':key,'target':f"project:{bid['project_id']}",
                      'relation':'中标' if bid['is_winner'] else '投标','value':bid['bid_amount']})
    return {'nodes':list(nodes.values()),'links':links}


def relationship_overview(dataset_id: str='default') -> dict:
    """Full entity inventory; only validated model records contribute edges."""
    graph = relationship_graph(None, dataset_id)
    nodes = {node['id']: node for node in graph['nodes']}
    for row in rows("SELECT DISTINCT purchaser name FROM projects WHERE dataset_id=? AND purchaser<>''", (dataset_id,)):
        key = 'purchaser:' + row['name']
        nodes.setdefault(key, {'id': key, 'name': row['name'], 'type': 'purchaser'})
    for row in rows("SELECT DISTINCT b.bidder name FROM bids b JOIN projects p ON p.id=b.project_id WHERE p.dataset_id=? AND b.bidder<>'' AND b.identity_confirmed=1", (dataset_id,)):
        key = 'supplier:' + row['name']
        nodes.setdefault(key, {'id': key, 'name': row['name'], 'type': 'supplier'})
    graph['nodes'] = list(nodes.values())
    graph['totals'] = {
        'enterprises': len({n['name'] for n in nodes.values() if n['type'] != 'project'}),
        'suppliers': sum(n['type'] == 'supplier' for n in nodes.values()),
        'purchasers': sum(n['type'] == 'purchaser' for n in nodes.values()),
        'projects': sum(n['type'] == 'project' for n in nodes.values()),
        'links': len(graph['links'])
    }
    return graph
