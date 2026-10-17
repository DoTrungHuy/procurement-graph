<script setup>
import { computed, defineAsyncComponent, nextTick, ref, watch } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { money, number } from '../utils/presentation'
import EmptyState from './EmptyState.vue'
import PaginationBar from './PaginationBar.vue'
import AnalysisSummary from './AnalysisSummary.vue'
import BidSummary from './BidSummary.vue'
import RelationshipCharts from './RelationshipCharts.vue'
const RelationNetwork = defineAsyncComponent(() => import('./RelationNetwork.vue'))
const w = useWorkspace(), group = ref('primary'), page = ref(1)
watch(() => w.result, () => { group.value = 'primary'; page.value = 1 })
watch(group, () => { page.value = 1 })
const groups = computed(() => w.scenario === 'purchaser-partners' ? ['中标供应商', '产品品牌'] : w.scenario === 'purchaser-bidders' ? ['高频投标主体', '共同投标组合'] : w.scenario === 'supplier-competitors' ? ['高频主体 Top 5', '全部参与方'] : [])
const rows = computed(() => {
  const result = w.result || {}, secondary = group.value === 'secondary'
  if (w.scenario === 'purchaser-partners') return (secondary ? result.product_suppliers : result.winning_suppliers) || []
  if (w.scenario === 'purchaser-bidders') return (secondary ? result.collaborative_pairs : result.top_bidders) || []
  if (w.scenario === 'supplier-competitors') return (secondary ? result.all_participants : result.top5) || []
  if (w.scenario === 'common-purchasers') return result.purchasers || []
  return result.projects || []
})
const column = (key, title, type = 'text') => ({ key, title, type })
const columns = computed(() => {
  if (w.scenario === 'purchaser-partners') return [column('supplier', group.value === 'secondary' ? '品牌' : '中标供应商'), column('cooperation_count', '合作采购包', 'number'), ...(group.value === 'secondary' ? [column('item_count', '标的条数', 'number')] : []), column('total_amount', '已披露金额（元）', 'money'), column('first_date', '首次合作'), column('last_date', '最近合作'), column('unknown_amount_count', '金额未披露包', 'number')]
  if (w.scenario === 'purchaser-bidders' && group.value === 'secondary') return [column('bidder_a', '投标主体 A'), column('bidder_b', '投标主体 B'), column('frequency', '共同出现采购包', 'number')]
  if (['purchaser-bidders', 'supplier-competitors'].includes(w.scenario)) return [column('bidder', '投标主体'), column('frequency', w.scenario === 'purchaser-bidders' ? '参与采购包' : '共同竞标采购包', 'number'), column('wins', '中标次数', 'number'), column('total_bid_amount', '已披露投标金额（元）', 'money'), column('unknown_amount_count', '金额未披露次数', 'number')]
  if (w.scenario === 'common-purchasers') return [column('purchaser', '采购单位'), column('cooperation_count', '合作次数合计', 'number'), column('total_amount', '已披露中标金额（元）', 'money'), column('unknown_amount_count', '金额未披露包', 'number'), column('supplier_details', '各供应商合作情况', 'details')]
  return [column('name', '项目 / 采购包', 'project'), column('purchaser', '采购单位'), column('awarded_amount', '中标金额（元）', 'money'), column('announcement_date', '公告日期'), column('results', '竞标结果', 'bids')]
})
const paged = computed(() => rows.value.slice((page.value - 1) * 15, page.value * 15))
const pairMode = computed(() => w.scenario === 'purchaser-bidders' && group.value === 'secondary')
const highlighted = ref(''), detailTable = ref(null)
function rowName(row) { return pairMode.value ? `${row.bidder_a} / ${row.bidder_b}` : row.supplier || row.bidder || row.purchaser || row.name }
async function locate(name) {
  const index = rows.value.findIndex(row => rowName(row) === name)
  if (index < 0) return
  highlighted.value = name; page.value = Math.floor(index / 15) + 1
  await nextTick()
  detailTable.value?.querySelector('.highlighted-result')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}
watch(() => [w.result, group.value], () => { highlighted.value = '' })
</script>

<template>
  <section class="surface scenario-results" data-testid="scenario-result" :data-scenario="w.scenario" :data-render-ms="w.renderMs" aria-label="关系查询结果">
    <div class="result-overview-heading"><h2>查询结果 <span class="count">{{ number(rows.length) }}</span></h2><span>{{ w.scenarioDefinition.title }} · 当前数据集</span></div>
    <AnalysisSummary />
    <div v-if="groups.length" class="subtabs" aria-label="结果分组"><button v-for="(title, index) in groups" :key="title" :class="{ active: group === (index ? 'secondary' : 'primary') }" :aria-pressed="group === (index ? 'secondary' : 'primary')" @click="group = index ? 'secondary' : 'primary'">{{ title }}</button></div>
    <EmptyState v-if="!rows.length" title="暂未找到符合条件的关系" description="可更换查询主体，或补充当前数据集。"></EmptyState>
    <template v-else>
      <div class="analysis-visuals" :class="{ 'wide-comparison': ['common-purchasers', 'common-projects'].includes(w.scenario) }">
        <RelationNetwork id="relationship-network" :result-group="group" />
        <RelationshipCharts :rows="rows" :pair-mode="pairMode" :group="group" @select="locate" />
      </div>
      <div class="detail-table-heading"><h3>结果明细</h3><p>完整字段与来源核验，按页展示查询结果。</p></div>
      <p class="mobile-table-hint">左右滑动表格查看全部结果字段。</p>
      <div ref="detailTable" class="table-scroll"><table class="relation-table" :class="{ 'common-project-table': w.scenario === 'common-projects' }"><thead><tr><th v-for="col in columns" :key="col.key" :class="{ numeric: ['number', 'money'].includes(col.type) }">{{ col.title }}</th></tr></thead><tbody><tr v-for="(row, index) in paged" :key="row.id || `${page}-${index}`" :class="{ 'highlighted-result': highlighted === rowName(row) }"><td v-for="col in columns" :key="col.key" :class="{ numeric: ['number', 'money'].includes(col.type), 'entity-cell': col.type === 'text' }"><template v-if="col.type === 'money'">{{ money(row[col.key]) }}</template><template v-else-if="col.type === 'number'">{{ row[col.key] == null ? '未记录' : number(row[col.key]) }}</template><template v-else-if="col.type === 'project'"><button class="project-link" @click="w.openProject(row.id)">{{ row.name }}</button><span class="cell-note">{{ row.lot_name }}</span></template><template v-else-if="col.type === 'bids'"><BidSummary :results="row.results" /><button class="text-button" @click="w.openProject(row.id, 'bids')">查看竞标详情</button></template><details v-else-if="col.type === 'details'" class="supplier-details"><summary>查看 {{ row.supplier_details?.length || 0 }} 家供应商</summary><dl><div v-for="supplier in row.supplier_details" :key="supplier.supplier"><dt>{{ supplier.supplier }}</dt><dd>{{ supplier.cooperation_count }} 次 · {{ money(supplier.total_amount) }}</dd></div></dl></details><template v-else>{{ row[col.key] || '未披露' }}</template></td></tr></tbody></table></div>
      <PaginationBar :total="rows.length" :page="page" :page-size="15" label="关系结果" @change="page = $event"></PaginationBar>
    </template>
    <div class="result-footnote"><span>按采购包统计；审查不通过者不计入有效共同竞标。金额未披露时保留缺失。</span><span v-if="w.renderMs != null" class="query-time">查询与明细显示 {{ number(w.renderMs) }} ms</span></div>
  </section>
</template>
