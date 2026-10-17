<script setup>
import { computed, ref, watch } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { money, number } from '../utils/presentation'
import EmptyState from './EmptyState.vue'
import PaginationBar from './PaginationBar.vue'
import AnalysisSummary from './AnalysisSummary.vue'
import BidSummary from './BidSummary.vue'
const w = useWorkspace(), display = ref('table'), group = ref('primary'), page = ref(1), metric = ref('frequency')
watch(() => w.result, () => { display.value = 'table'; group.value = 'primary'; page.value = 1; metric.value = 'frequency' })
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
const chartRows = computed(() => rows.value.slice(0, 10).map(row => ({
  name: pairMode.value ? `${row.bidder_a} / ${row.bidder_b}` : row.supplier || row.bidder || row.purchaser || `${row.name} · ${row.lot_name || ''}`,
  frequency: row.frequency ?? row.cooperation_count ?? 1,
  amount: row.total_amount ?? row.total_bid_amount ?? row.awarded_amount ?? null
})))
const maximum = computed(() => Math.max(1, ...chartRows.value.map(row => row[metric.value] ?? 0)))
</script>

<template>
  <section class="surface scenario-results" data-testid="scenario-result" :data-scenario="w.scenario" :data-render-ms="w.renderMs" aria-label="关系查询结果">
    <AnalysisSummary />
    <div class="section-heading"><div><h2>查询结果 <span class="count">{{ number(rows.length) }}</span></h2><p v-if="w.scenario === 'common-projects'">共 {{ w.result.summary?.project_count || 0 }} 个采购包，来自 {{ w.result.summary?.notice_count || 0 }} 条公告。</p><p v-else>仅统计当前数据集中的记录。</p></div><div class="segmented" aria-label="结果展示方式"><button :class="{ active: display === 'table' }" :aria-pressed="display === 'table'" @click="display = 'table'">表格</button><button :class="{ active: display === 'chart' }" :aria-pressed="display === 'chart'" @click="display = 'chart'">图表</button></div></div>
    <div v-if="groups.length" class="subtabs" aria-label="结果分组"><button v-for="(title, index) in groups" :key="title" :class="{ active: group === (index ? 'secondary' : 'primary') }" :aria-pressed="group === (index ? 'secondary' : 'primary')" @click="group = index ? 'secondary' : 'primary'">{{ title }}</button></div>
    <EmptyState v-if="!rows.length" title="暂未找到符合条件的关系" description="可更换查询主体，或补充当前数据集。"></EmptyState>
    <template v-else-if="display === 'table'">
      <p class="mobile-table-hint">左右滑动表格查看全部结果字段。</p>
      <div class="table-scroll"><table class="relation-table" :class="{ 'common-project-table': w.scenario === 'common-projects' }"><thead><tr><th v-for="col in columns" :key="col.key" :class="{ numeric: ['number', 'money'].includes(col.type) }">{{ col.title }}</th></tr></thead><tbody><tr v-for="(row, index) in paged" :key="row.id || `${page}-${index}`"><td v-for="col in columns" :key="col.key" :class="{ numeric: ['number', 'money'].includes(col.type), 'entity-cell': col.type === 'text' }"><template v-if="col.type === 'money'">{{ money(row[col.key]) }}</template><template v-else-if="col.type === 'number'">{{ row[col.key] == null ? '未记录' : number(row[col.key]) }}</template><template v-else-if="col.type === 'project'"><button class="project-link" @click="w.openProject(row.id)">{{ row.name }}</button><span class="cell-note">{{ row.lot_name }}</span></template><template v-else-if="col.type === 'bids'"><BidSummary :results="row.results" /><button class="text-button" @click="w.openProject(row.id, 'bids')">查看竞标详情</button></template><details v-else-if="col.type === 'details'" class="supplier-details"><summary>查看 {{ row.supplier_details?.length || 0 }} 家供应商</summary><dl><div v-for="supplier in row.supplier_details" :key="supplier.supplier"><dt>{{ supplier.supplier }}</dt><dd>{{ supplier.cooperation_count }} 次 · {{ money(supplier.total_amount) }}</dd></div></dl></details><template v-else>{{ row[col.key] || '未披露' }}</template></td></tr></tbody></table></div>
      <PaginationBar :total="rows.length" :page="page" :page-size="15" label="关系结果" @change="page = $event"></PaginationBar>
    </template>
    <div v-else class="chart-section"><div class="chart-toolbar"><p class="muted">按结果顺序展示前 {{ chartRows.length }} 项{{ rows.length > 10 ? '，完整记录见表格' : '' }}。</p><label v-if="!pairMode" class="chart-select">比较指标 <select v-model="metric" aria-label="图表比较指标"><option value="frequency">频次</option><option value="amount">已披露金额</option></select></label></div><div class="bar-chart" role="img" :aria-label="metric === 'amount' ? '已披露金额对比图' : '采购包频次对比图'"><div v-for="(row, index) in chartRows" :key="`${row.name}-${index}`" class="bar-row"><span class="bar-label">{{ row.name }}</span><div class="bar-track"><span class="bar-value" :style="{ width: `${(row[metric] ?? 0) / maximum * 100}%` }"></span></div><strong class="bar-number">{{ metric === 'amount' ? money(row.amount) : `${number(row.frequency)} 次` }}</strong></div></div></div>
    <div class="result-footnote"><span>按采购包统计；审查不通过者不计入有效共同竞标。金额未披露时保留缺失。</span><span v-if="w.renderMs != null" class="query-time">查询与显示 {{ number(w.renderMs) }} ms</span></div>
  </section>
</template>
