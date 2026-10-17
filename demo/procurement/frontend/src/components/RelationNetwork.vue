<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { useWorkspace } from '../composables/useWorkspace'
import EmptyState from './EmptyState.vue'
defineProps({ title: { type: String, default: '关系网络' } })
const w = useWorkspace(), element = ref(null), limit = ref(8), loading = ref(true), error = ref(''), summary = ref(''), empty = ref(false)
let chart, observer, token = 0, disposed = false
const types = ['purchaser', 'project', 'supplier'], colors = ['#3567b2', '#9b7949', '#578879']
function fitGraph() {
  if (!chart || chart.isDisposed() || !chart.getWidth() || !chart.getHeight()) return
  const series = chart.getModel()?.getSeriesByIndex(0), points = []
  if (!series?.coordinateSystem) return
  series.getData().each(index => {
    const point = series.getData().getItemLayout(index)
    if (point?.every(Number.isFinite)) points.push(point)
  })
  if (!points.length) return
  const pixels = points.map(point => chart.convertToPixel({ seriesIndex: 0 }, point))
  if (pixels.some(point => !point || !point.every(Number.isFinite))) return
  const min = [Math.min(...points.map(point => point[0])), Math.min(...points.map(point => point[1]))]
  const max = [Math.max(...points.map(point => point[0])), Math.max(...points.map(point => point[1]))]
  const width = Math.max(...pixels.map(point => point[0])) - Math.min(...pixels.map(point => point[0]))
  const height = Math.max(...pixels.map(point => point[1])) - Math.min(...pixels.map(point => point[1]))
  // Reserve room for the existing node sizes and purchaser labels at the canvas edges.
  const scale = Math.min(Math.max(1, chart.getWidth() - 150) / Math.max(1, width), Math.max(1, chart.getHeight() - 80) / Math.max(1, height))
  const zoom = Math.max(0.01, Math.min(1, (series.get('zoom') || 1) * scale))
  chart.setOption({ series: [{ center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2], zoom }] })
}
function resizeGraph() { if (chart && !chart.isDisposed()) { chart.resize(); fitGraph() } }
async function draw() {
  const sequence = ++token, dataset = w.dataset
  loading.value = true; error.value = ''; summary.value = ''; empty.value = false; chart?.dispose(); chart = null
  try {
    const [data, { engine }] = await Promise.all([api.graph({ dataset_id: dataset, limit: limit.value }), import('../graph')])
    if (disposed || sequence !== token || w.dataset !== dataset) return
    loading.value = false
    await nextTick()
    if (disposed || sequence !== token || !element.value) return
    summary.value = `${data.nodes.length} 个主体 · ${data.links.length} 条关系`
    empty.value = !data.nodes.length
    if (empty.value) return
    chart = engine.init(element.value, null, { renderer: 'canvas' })
    chart.setOption({ animation: false, tooltip: { renderMode: 'richText', confine: true, formatter: params => params.dataType === 'edge' ? params.data.relation : params.data.name }, series: [{ type: 'graph', layout: 'force', roam: true, draggable: true, labelLayout: { hideOverlap: true }, force: { repulsion: 170, edgeLength: [65, 120], gravity: 0.12, layoutAnimation: false }, data: data.nodes.map(node => ({ ...node, category: types.indexOf(node.type), itemStyle: { color: colors[types.indexOf(node.type)] || colors[2] }, symbolSize: node.type === 'purchaser' ? 24 : node.type === 'project' ? 16 : 10, label: { show: node.type === 'purchaser', color: '#334155', fontSize: 12, position: 'bottom', width: 140, overflow: 'truncate' } })), links: data.links, lineStyle: { color: '#c5cdd6', width: 1, opacity: 0.65 }, emphasis: { focus: 'adjacency', label: { show: true, backgroundColor: '#fff', padding: 5, width: 200, overflow: 'break', color: '#172235' }, lineStyle: { width: 2, opacity: 1 } } }] })
    chart.on('click', params => { if (params.dataType === 'node' && params.data.type === 'project') { const id = Number(String(params.data.id).replace('project:', '')); if (Number.isFinite(id)) w.openProject(id) } })
    resizeGraph()
  } catch (cause) { if (sequence === token && !disposed) { error.value = cause.message; loading.value = false } }
}
watch(() => [w.dataset, limit.value, w.datasetLoading], () => {
  if (w.datasetLoading) { ++token; loading.value = true; error.value = ''; summary.value = ''; chart?.dispose(); chart = null }
  else draw()
})
onMounted(() => { observer = new ResizeObserver(resizeGraph); observer.observe(element.value); if (!w.datasetLoading) draw() })
onBeforeUnmount(() => { disposed = true; ++token; observer?.disconnect(); chart?.dispose(); chart = null })
</script>

<template>
  <section class="surface network-surface" aria-label="关系网络" :aria-busy="loading">
    <div class="section-heading"><div><h2>{{ title }}</h2><p>当前数据集概览，不随关系查询条件变化。</p></div><label class="chart-select">采购包数 <select v-model.number="limit" aria-label="关系网络采购包数" :disabled="w.datasetLoading"><option v-for="value in [8, 16, 30, 80]" :key="value" :value="value">{{ value }}</option></select></label></div>
    <div class="network-legend"><span><i :style="{ background: colors[0] }"></i>采购单位</span><span><i :style="{ background: colors[1] }"></i>采购包</span><span><i :style="{ background: colors[2] }"></i>供应商</span><span class="muted" data-testid="network-summary">{{ summary }}</span></div>
    <div v-if="error" class="notice danger inset" role="alert">{{ error }}<button class="text-button" @click="draw">重新加载</button></div>
    <EmptyState v-if="loading" busy title="正在绘制关系网络"></EmptyState>
    <EmptyState v-else-if="!error && empty" title="暂无可展示的关系" description="可切换数据集，或在数据接入页导入公告。"></EmptyState>
    <div ref="element" class="network-canvas" :class="{ 'is-hidden': loading || error || empty }" aria-label="可拖动的采购关系网络"></div>
    <div class="footnote network-note"><button class="text-button" aria-label="关系网络适应视图" :disabled="loading || !!error || empty" @click="fitGraph">适应视图</button><span>仅显示所选数量的采购包。可拖动、缩放，悬停读取名称，点击采购包打开详情。</span></div>
  </section>
</template>
