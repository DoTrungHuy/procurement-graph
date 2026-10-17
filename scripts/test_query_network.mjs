import assert from 'node:assert/strict'
import { queryNetwork } from '../demo/procurement/frontend/src/utils/queryNetwork.js'

const common = queryNetwork('common-projects', {
  suppliers: ['供应商甲', '供应商乙', '未记录主体'],
  projects: [{ id: 7, name: '项目一', purchaser: '采购单位', results: '供应商甲：中标；供应商乙：未中标' }]
}, {}, 8)
assert.equal(common.links.length, 3)
assert.equal(common.links.find(edge => edge.source === 'supplier:供应商甲').relation, '中标')
assert.equal(common.links.find(edge => edge.source === 'supplier:供应商乙').relation, '投标（未中标）')
assert(!common.links.some(edge => edge.source === 'supplier:未记录主体'))
assert.equal(common.nodes.find(node => node.type === 'project').projectId, 7)
const limited = queryNetwork('supplier-competitors', {
  top5: [{ bidder: '乙', frequency: 4 }],
  all_participants: [{ bidder: '乙', frequency: 4 }, { bidder: '丙', frequency: 2 }]
}, { supplier: '甲' }, 1, 'secondary')
assert.equal(limited.total, 2)
assert.equal(limited.shown, 1)
assert.equal(limited.links.length, 1)
assert.match(limited.links[0].relation, /共同竞标 · 4 个采购包/)
assert(limited.nodes.find(node => node.name === '甲').focus)
const topOnly = queryNetwork('supplier-competitors', {
  top5: [{ bidder: '乙', frequency: 4 }],
  all_participants: [{ bidder: '乙', frequency: 4 }, { bidder: '丙', frequency: 2 }]
}, { supplier: '甲' }, 8)
assert.equal(topOnly.total, 1, 'Primary network must follow the Top 5 result group')
assert(!topOnly.nodes.some(node => node.name === '丙'))
const matrix = queryNetwork('common-purchasers', {
  suppliers: ['甲', '乙'], purchasers: [{ purchaser: '单位', supplier_details: [{ supplier: '甲', cooperation_count: 3 }, { supplier: '乙', cooperation_count: 1 }] }]
}, {}, 8)
assert.equal(matrix.links.length, 2)
for (const graph of [common, limited, matrix]) {
  const ids = new Set(graph.nodes.map(node => node.id))
  assert.equal(ids.size, graph.nodes.length)
  assert(graph.links.every(edge => ids.has(edge.source) && ids.has(edge.target)))
}
assert.equal(queryNetwork('purchaser-partners', { winning_suppliers: [] }, { purchaser: '单位' }).links.length, 0)
console.log('Query network: source-grounded edges, outcome distinction, limits and unique nodes passed')
