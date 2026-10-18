<script setup>
import { computed, ref, watch } from 'vue'
import { money, number } from '../utils/presentation'
import { useWorkspace } from '../composables/useWorkspace'
const props = defineProps({ rows: Array, pairMode: Boolean, group: String })
const emit = defineEmits(['select'])
const w = useWorkspace(), metric = ref('frequency'), rankLimit = ref(5)
watch(() => [w.result, props.group], () => { metric.value = 'frequency' })
const isProjects = computed(() => w.scenario === 'common-projects')
const isMatrix = computed(() => w.scenario === 'common-purchasers')
const suppliers = computed(() => w.result?.suppliers || [])
const ranked = computed(() => props.rows.map(row => ({
  name: props.pairMode ? `${row.bidder_a} / ${row.bidder_b}` : row.supplier || row.bidder || row.purchaser || row.name,
  frequency: row.frequency ?? row.cooperation_count ?? null,
  amount: row.total_amount ?? row.total_bid_amount ?? null,
  amountStatus: row.amount_status
})).sort((a, b) => (b[metric.value] ?? -Infinity) - (a[metric.value] ?? -Infinity)).slice(0, rankLimit.value))
const maximum = computed(() => Math.max(1, ...ranked.value.map(row => row[metric.value] ?? 0)))
const title = computed(() => ({ 'purchaser-partners': '合作供应商与产品品牌', 'purchaser-bidders': props.pairMode ? '共同投标组合' : '投标主体', 'supplier-competitors': '共同竞标主体' }[w.scenario] || '查询结果') + (metric.value === 'amount' ? '金额排名' : '频次排名'))
function cooperation(row, supplier) { return row.supplier_details?.find(item => item.supplier === supplier) }
function bid(row, supplier) {
  const entry = (row.results || '').split('；').find(value => value === `${supplier}：中标` || value === `${supplier}：未中标`)
  return entry?.endsWith('：未中标') ? '未中标' : entry ? '中标' : '未记录'
}
</script>

<template>
  <div class="chart-section enhanced-chart">
    <div class="chart-toolbar"><div><h3>{{ isProjects ? '共同竞标结果' : isMatrix ? '共同采购单位合作对比' : title }}</h3><p class="muted">{{ isProjects || isMatrix ? `按结果顺序展示前 ${Math.min(rows.length, 10)} 项，完整记录见表格。` : `按${metric === 'amount' ? '已披露金额' : '频次'}排序 · 前 ${ranked.length} 项` }}</p></div>
      <div v-if="!isProjects && !isMatrix && rows.length > 5" class="rank-limit"><label>显示 <select v-model.number="rankLimit" aria-label="排名显示数量"><option :value="5">前 5 项</option><option :value="10">前 10 项</option></select></label></div><div v-if="!isProjects && !pairMode" class="segmented metric-switch" aria-label="图表比较指标"><button :class="{ active: metric === 'frequency' }" :aria-pressed="metric === 'frequency'" @click="metric = 'frequency'">频次</button><button :class="{ active: metric === 'amount' }" :aria-pressed="metric === 'amount'" @click="metric = 'amount'">已披露金额</button></div>
    </div>
    <div v-if="isProjects || isMatrix" class="table-scroll comparison-matrix"><table><thead><tr><th>{{ isProjects ? '项目 / 采购包' : '采购单位' }}</th><th v-for="supplier in suppliers" :key="supplier" :title="supplier"><span>{{ supplier }}</span></th><th v-if="isProjects" class="numeric">中标金额（元）</th></tr></thead><tbody><tr v-for="row in rows.slice(0, 10)" :key="row.id || row.purchaser"><td><button v-if="isProjects" class="project-link" @click="w.openProject(row.id, 'bids')">{{ row.name }}</button><strong v-else>{{ row.purchaser }}</strong><span v-if="isProjects" class="cell-note">{{ row.lot_name }}</span></td><td v-for="supplier in suppliers" :key="supplier" class="matrix-cell"><span v-if="isProjects" class="status" :class="bid(row, supplier) === '中标' ? 'success' : 'neutral'">{{ bid(row, supplier) }}</span><template v-else><strong>{{ metric === 'amount' ? money(cooperation(row, supplier)?.total_amount) : cooperation(row, supplier)?.cooperation_count == null ? '未记录' : `${number(cooperation(row, supplier).cooperation_count)} 次` }}</strong></template></td><td v-if="isProjects" class="numeric">{{ money(row.awarded_amount) }}</td></tr></tbody></table></div>
    <div v-else class="bar-chart" role="group" :aria-label="title"><div v-for="(row, index) in ranked" :key="row.name" class="bar-row" :class="{ leading: index === 0 }"><span class="bar-label" :title="row.name"><i>{{ String(index + 1).padStart(2, '0') }}</i><button class="chart-entity" :title="`定位明细：${row.name}`" @click="emit('select', row.name)">{{ row.name }}</button></span><div class="bar-track" :class="{ undisclosed: row[metric] == null }"><span v-if="row[metric] != null" class="bar-value" :style="{ width: `${row[metric] / maximum * 100}%` }"></span></div><strong class="bar-number">{{ metric === 'amount' ? row.amountStatus === 'pending_review' ? '待核对' : money(row.amount) : row.frequency == null ? '未记录' : `${number(row.frequency)} 次` }}</strong></div></div>
    <p class="chart-scope">{{ isProjects ? '仅展示所选供应商的有效共同竞标结果；点击项目名称查看全部参与方。金额为采购包中标金额。' : isMatrix ? '每格为对应供应商的合作频次或已披露中标金额；个人份额未知时保留未披露。' : '按采购包统计，仅比较当前数据集。金额未披露与实际金额为零分别显示。' }}</p>
  </div>
</template>
