<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useWorkspace } from '../composables/useWorkspace'
import { modeName, money, number, statusName } from '../utils/presentation'
const w = useWorkspace()
const graph = ref({ nodes: [], links: [] }), loading = ref(false), error = ref('')
let sequence = 0
const latest = computed(() => w.jobHistory[0])
const review = computed(() => (w.stats.partial_packages || 0) + (w.stats.fallback_packages || 0))
const columns = [
  { type: 'purchaser', label: '采购单位', x: 12, width: 174 },
  { type: 'project', label: '采购包', x: 247, width: 204 },
  { type: 'supplier', label: '中标供应商', x: 512, width: 174 }
]
const mapHeight = computed(() => Math.max(260, ...columns.map(column => graph.value.nodes.filter(node => node.type === column.type).length * 53 + 65)))
// A small, real-data graph keeps the overview fast; the full graph stays lazy-loaded.
const nodes = computed(() => columns.flatMap(column => {
  const visible = graph.value.nodes.filter(node => node.type === column.type)
  return visible.map((node, index) => ({ ...node, ...column, y: 52 + index * ((mapHeight.value - 105) / Math.max(1, visible.length - 1)) }))
}))
const edges = computed(() => graph.value.links.flatMap(link => {
  const a = nodes.value.find(node => node.id === link.source), b = nodes.value.find(node => node.id === link.target)
  if (!a || !b) return []
  const left = a.x < b.x ? a : b, right = a.x < b.x ? b : a
  return [{ ...link, path: `M ${left.x + left.width} ${left.y + 20} C ${left.x + left.width + 35} ${left.y + 20}, ${right.x - 35} ${right.y + 20}, ${right.x} ${right.y + 20}` }]
}))
async function load() {
  const token = ++sequence, dataset = w.dataset
  loading.value = true; error.value = ''; graph.value = { nodes: [], links: [] }
  try {
    const data = await api.graph({ dataset_id: dataset, limit: 3 })
    if (token !== sequence || dataset !== w.dataset) return
    // Show awarded links only, keeping the preview readable without hiding a random subset.
    const links = data.links.filter(link => link.relation !== '投标')
    const ids = new Set(links.flatMap(link => [link.source, link.target]))
    graph.value = { nodes: data.nodes.filter(node => ids.has(node.id)), links }
  } catch (cause) { if (token === sequence) error.value = cause.message }
  finally { if (token === sequence) loading.value = false }
}
watch(() => [w.dataset, w.stats.projects, w.datasetLoading], () => {
  if (!w.datasetLoading) load()
  else { ++sequence; loading.value = true; error.value = ''; graph.value = { nodes: [], links: [] } }
}, { immediate: true })
onBeforeUnmount(() => { ++sequence })
function open(node) { if (node.type === 'project') w.openProject(Number(node.id.replace('project:', ''))) }
</script>

<template>
  <div class="overview-grid">
    <section class="surface overview-network" aria-label="采购关系概览">
      <div class="section-heading"><div><span class="eyebrow">RELATIONSHIP MAP</span><h2>数据之间，建立联系</h2><p>采购单位、采购包与中标供应商的真实关联</p></div><button class="text-button" @click="w.navigate('relations')">进入关系分析 →</button></div>
      <div v-if="loading" class="map-empty" role="status">正在读取关系…</div>
      <div v-else-if="error" class="map-empty" role="alert"><p>{{ error }}</p><button class="text-button" @click="load">重新加载概览</button></div>
      <div v-else-if="!nodes.length" class="map-empty">当前数据集暂无可展示的中标关系</div>
      <div v-else class="map-scroll" tabindex="0" aria-label="采购关系概览，可滚动查看"><svg class="overview-map" :viewBox="`0 0 700 ${mapHeight}`" role="group" aria-label="最近入库的三个采购包及其中标关系，采购包可点击查看详情">
        <text v-for="column in columns" :key="column.type" :x="column.x" y="26" class="map-column">{{ column.label }}</text>
        <path v-for="(edge, index) in edges" :key="index" :d="edge.path" class="map-edge" />
        <g v-for="node in nodes" :key="node.id" :class="['map-node', node.type]" :transform="`translate(${node.x}, ${node.y})`" :role="node.type === 'project' ? 'button' : undefined" :tabindex="node.type === 'project' ? 0 : undefined" :aria-label="node.type === 'project' ? `查看采购包：${node.name}` : undefined" @click="open(node)" @keydown.enter.prevent="open(node)" @keydown.space.prevent="open(node)">
          <title>{{ node.name }}{{ node.type === 'project' ? ` · ${money(node.value)}` : '' }}</title>
          <rect :width="node.width" height="41" rx="9" /><circle cx="14" cy="21" r="3" />
          <text x="25" y="25">{{ node.name.length > (node.type === 'project' ? 13 : 11) ? node.name.slice(0, node.type === 'project' ? 13 : 11) + '…' : node.name }}</text>
        </g>
      </svg></div>
      <div class="map-caption"><span><i></i> 最近入库的 3 个采购包 · 仅展示中标关系</span><span>点击采购包核验来源</span></div>
    </section>
    <section class="surface quality-card" aria-label="数据质量与处理状态">
      <span class="eyebrow">DATA HEALTH</span><h2>每一条数据，都有依据</h2>
      <div class="quality-primary"><strong>{{ w.datasetLoading ? '—' : number(review) }}</strong><div><span>采购包待核对</span><small>含部分完成与规则兜底</small></div></div>
      <div class="quality-row"><span>金额未披露</span><strong>{{ w.datasetLoading ? '—' : number(w.stats.unknown_amount_count) }} <small>包</small></strong></div>
      <div class="quality-row"><span>最近处理方式</span><strong>{{ latest ? modeName(latest.mode) : '暂无任务' }}</strong></div>
      <div class="quality-row"><span>最近任务状态</span><strong>{{ latest ? statusName(latest.status) : '—' }}</strong></div>
      <p class="quality-note">{{ w.dataset === 'official-reference' ? '当前为公告正文核验样本，仍待独立人工复核；不代表官方全量成绩。' : '状态与缺失情况来自当前数据集，不等同于抽取准确率。' }}</p>
      <button class="text-button" @click="w.navigate('import')">查看处理记录与说明 →</button>
    </section>
  </div>
  <nav class="workflow-strip" aria-label="分析流程">
    <button @click="w.navigate('import')"><span>01</span><div><strong>接入公告</strong><small>正文与多格式附件</small></div><b>→</b></button>
    <button @click="w.navigate('import')"><span>02</span><div><strong>抽取与入库</strong><small>任务进度与异常追踪</small></div><b>→</b></button>
    <button @click="w.navigate('items')"><span>03</span><div><strong>核验七类字段</strong><small>结构化结果与原文证据</small></div><b>→</b></button>
    <button @click="w.navigate('relations')"><span>04</span><div><strong>探索业务关系</strong><small>五类场景与图表分析</small></div><b>↗</b></button>
  </nav>
</template>
