from app.database import connect, insert_project


def test_overview_covers_more_than_legacy_graph_limit(client, notice):
    insert_project(notice(uid='overview-0'), ['seed.html'])
    with connect() as connection:
        seed = connection.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        connection.execute("UPDATE bids SET bidder='供应商0' WHERE is_winner=1")
        project_columns = [row[1] for row in connection.execute('PRAGMA table_info(projects)') if row[1] != 'id']
        bid_columns = [row[1] for row in connection.execute('PRAGMA table_info(bids)') if row[1] != 'id']
        for index in range(1, 305):
            expressions = ['?' if column == 'notice_uuid' else column for column in project_columns]
            cursor = connection.execute(f"INSERT INTO projects ({','.join(project_columns)}) SELECT {','.join(expressions)} FROM projects WHERE id=?", (f'overview-{index}', seed))
            expressions = ['?' if column == 'project_id' else "CASE WHEN is_winner=1 THEN ? ELSE bidder END" if column == 'bidder' else column for column in bid_columns]
            # Values follow the schema's column order (project_id precedes bidder).
            connection.execute(f"INSERT INTO bids ({','.join(bid_columns)}) SELECT {','.join(expressions)} FROM bids WHERE project_id=?", (cursor.lastrowid, f'供应商{index}', seed))
    response = client.get('/api/graph/overview?dataset_id=default')
    assert response.status_code == 200
    data = response.json()
    assert data['totals']['projects'] == 305
    assert data['totals']['suppliers'] == 306
    assert data['totals']['links'] == 915
    assert any(n['name'] == '供应商0' for n in data['nodes'])
    assert client.get('/api/graph/overview?dataset_id=empty').json()['nodes'] == []


def test_overview_inventory_does_not_invent_fallback_links(client, notice):
    record = notice(uid='fallback-overview', processing_status='fallback')
    record.purchaser = '仅规则提取单位'
    record.bids[0].bidder = '仅规则提取企业'
    insert_project(record, ['fallback.html'])
    graph = client.get('/api/graph/overview?dataset_id=default').json()
    assert graph['totals']['enterprises'] == 3
    assert graph['links'] == []
    assert any(n['name'] == '仅规则提取企业' for n in graph['nodes'])
