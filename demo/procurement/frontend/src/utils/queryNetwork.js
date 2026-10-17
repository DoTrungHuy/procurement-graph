// Aggregated scenario edges are labelled as statistics, never invented transaction paths.
export function queryNetwork(scenario, result, parameters, limit = 8, group = 'primary') {
  const nodes = new Map(), links = []
  const add = (type, name, focus = false, level = focus ? 0 : 1, projectId) => {
    const id = `${type}:${projectId ?? name}`
    if (!nodes.has(id)) nodes.set(id, { id, name, type, focus, level, projectId })
    return id
  }
  const edge = (source, target, relation) => links.push({ source, target, relation })
  let total = 0
  if (scenario === 'purchaser-partners') {
    const root = add('purchaser', parameters.purchaser, true)
    const rows = (group === 'secondary' ? result.product_suppliers : result.winning_suppliers) || []; total = rows.length
    for (const row of rows.slice(0, limit)) edge(root, add(group === 'secondary' ? 'brand' : 'supplier', row.supplier), `${group === 'secondary' ? '采购品牌' : '中标合作'} · ${row.cooperation_count} 个采购包`)
  } else if (scenario === 'purchaser-bidders' && group === 'secondary') {
    const rows = result.collaborative_pairs || []; total = rows.length
    for (const row of rows.slice(0, limit)) edge(add('supplier', row.bidder_a, false, 0), add('supplier', row.bidder_b, false, 1), `共同投标 · ${row.frequency} 个采购包（${parameters.purchaser}）`)
  } else if (scenario === 'purchaser-bidders' || scenario === 'supplier-competitors') {
    const root = add(scenario === 'purchaser-bidders' ? 'purchaser' : 'supplier', parameters.purchaser || parameters.supplier, true)
    const rows = (scenario === 'purchaser-bidders' ? result.top_bidders : result.all_participants) || []; total = rows.length
    for (const row of rows.slice(0, limit)) edge(root, add('supplier', row.bidder), `${scenario === 'purchaser-bidders' ? '参与投标' : '共同竞标'} · ${row.frequency} 个采购包`)
  } else if (scenario === 'common-purchasers') {
    for (const supplier of result.suppliers || []) add('supplier', supplier, true)
    const rows = result.purchasers || []; total = rows.length
    for (const row of rows.slice(0, limit)) for (const detail of row.supplier_details || []) edge(add('supplier', detail.supplier, true), add('purchaser', row.purchaser), `中标合作 · ${detail.cooperation_count} 个采购包`)
  } else {
    for (const supplier of result.suppliers || []) add('supplier', supplier, true)
    const rows = result.projects || []; total = rows.length
    for (const row of rows.slice(0, limit)) {
      const project = add('project', `${row.name} · ${row.lot_name || ''}`, false, 1, row.id)
      if (row.purchaser) edge(add('purchaser', row.purchaser, false, 2), project, '采购')
      for (const supplier of result.suppliers || []) {
        const entry = (row.results || '').split('；').find(value => value === `${supplier}：中标` || value === `${supplier}：未中标`)
        if (entry) edge(add('supplier', supplier, true), project, entry.endsWith('：未中标') ? '投标（未中标）' : '中标')
      }
    }
  }
  return { nodes: [...nodes.values()], links, total, shown: Math.min(total, limit) }
}
