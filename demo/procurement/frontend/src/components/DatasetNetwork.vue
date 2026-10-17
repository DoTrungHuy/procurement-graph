<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { useWorkspace } from '../composables/useWorkspace'
import { number } from '../utils/presentation'
const w = useWorkspace(), canvas = ref(null), loading = ref(true), error = ref(''), data = ref(null)
const search = ref(''), role = ref('all'), limit = ref(160)
const colors = { purchaser: '#6265c5', supplier: '#399687', project: '#a298b9' }
let chart, observer, sequence = 0, renderSequence = 0, disposed = false
const inventory = computed(() => (data.value?.nodes || []).filter(node => node.type !== 'project'))
const matches = computed(() => inventory.value.filter(node => (role.value === 'all' || node.type === role.value) && node.name.toLowerCase().includes(search.value.trim().toLowerCase())))
const visible = computed(() => {
  const links = data.value?.links || [], nodes = data.value?.nodes || []
  if (!search.value.trim() && role.value === 'all' && nodes.length <= limit.value) return { nodes, links }
  const degree = new Map(), adjacency = new Map()
  for (const link of links) for (const [a, b] of [[link.source, link.target], [link.target, link.source]]) {
    degree.set(a, (degree.get(a) || 0) + 1)
    if (!adjacency.has(a)) adjacency.set(a, [])
    adjacency.get(a).push(b)
  }
  const seeds = [...matches.value].sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0) || a.name.localeCompare(b.name, 'zh-CN'))
  const ids = new Set(seeds.slice(0, Math.max(1, Math.floor(limit.value / 4))).map(node => node.id))
  const queue = [...ids]
  for (let i = 0; i < queue.length && ids.size < limit.value; i++) for (const id of adjacency.get(queue[i]) || []) {
    if (ids.size >= limit.value) break
    if (!ids.has(id)) { ids.add(id); queue.push(id) }
  }
  for (const node of seeds) { if (ids.size >= limit.value) break; ids.add(node.id) }
  return { nodes: nodes.filter(node => ids.has(node.id)), links: links.filter(link => ids.has(link.source) && ids.has(link.target)) }
})
async function load() {
  const token = ++sequence, dataset = w.dataset
  ++renderSequence; chart?.dispose(); chart = null; loading.value = true; data.value = null; error.value = ''; search.value = ''; role.value = 'all'
  try { const result = await api.graphOverview({ dataset_id: dataset }); if (!disposed && token === sequence && dataset === w.dataset) data.value = result }
  catch (cause) { if (token === sequence && !disposed) error.value = cause.message }
  finally { if (token === sequence && !disposed) loading.value = false }
}
async function draw() {
  const token = ++renderSequence
  chart?.dispose(); chart = null
  if (!data.value || !visible.value.nodes.length || loading.value) return
  const { engine } = await import('../graph')
  await nextTick()
  if (disposed || token !== renderSequence || !canvas.value?.clientWidth || !canvas.value?.clientHeight) return
  const nodes = visible.value.nodes
  chart = engine.init(canvas.value, null, { renderer: 'canvas' })
  chart.setOption({ animation: false, tooltip: { renderMode: 'richText', confine: true, formatter: item => item.dataType === 'edge' ? item.data.relation : `${item.data.name}\n${{ supplier: '供应商', purchaser: '采购单位', project: '采购包' }[item.data.type]}` }, series: [{ type: 'graph', layout: 'none', roam: true, draggable: true, scaleLimit: { min: .25, max: 5 }, data: nodes.map((node, index) => { const angle = index * 2.399963, radius = 28 * Math.sqrt(index + 1); return { ...node, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, symbol: node.type === 'project' ? 'roundRect' : 'circle', symbolSize: nodes.length < 30 ? 20 : node.type === 'project' ? 7 : 11, itemStyle: { color: colors[node.type], borderWidth: 1, borderColor: '#fff' }, label: { show: nodes.length <= 16, position: 'right', width: 160, overflow: 'truncate', fontSize: 11, color: '#334a69' } } }), links: visible.value.links.map(link => ({ ...link, lineStyle: { color: link.relation === '中标' ? '#6cae9f' : '#b0b8d0', type: link.relation === '投标' ? 'dashed' : 'solid', opacity: .45 } })), emphasis: { focus: 'adjacency', label: { show: true, width: 200, overflow: 'break', backgroundColor: '#fff', padding: 5 }, lineStyle: { opacity: 1, width: 2 } }, blur: { itemStyle: { opacity: .12 }, lineStyle: { opacity: .04 } } }] })
}
function resize() { if (!disposed && chart && !chart.isDisposed() && canvas.value?.clientWidth && canvas.value?.clientHeight) chart.resize() }
function fit() { chart?.setOption({ series: [{ center: ['50%', '50%'], zoom: 1 }] }); resize() }
watch(() => w.dataset, load)
watch([visible, loading], draw)
onMounted(() => { observer = new ResizeObserver(() => { if (chart) resize(); else if (!loading.value) draw() }); observer.observe(canvas.value); load() })
onBeforeUnmount(() => { disposed = true; ++sequence; ++renderSequence; observer?.disconnect(); chart?.dispose(); chart = null })
</script>

<template>
  <div class="dataset-overview">
    <p class="overview-scope-note">企业名录覆盖当前数据集的采购单位及全部已记录投标主体，关系仅来自有效建模记录。企业未连线不代表不存在业务关系。</p>
    <div class="overview-totals"><div><span>全部企业 / 单位（名称去重）</span><strong>{{ data ? number(data.totals.enterprises) : '—' }}</strong></div><div><span>供应商角色</span><strong>{{ data ? number(data.totals.suppliers) : '—' }}</strong></div><div><span>采购单位角色</span><strong>{{ data ? number(data.totals.purchasers) : '—' }}</strong></div><div><span>有效建模关系</span><strong>{{ data ? number(data.totals.links) : '—' }}</strong></div></div>
    <div class="overview-filter"><label class="field"><span>企业 / 单位名称</span><input v-model="search" type="search" aria-label="搜索总体网络企业" placeholder="搜索全部企业名录" :disabled="loading" /></label><label class="field"><span>检索角色</span><select v-model="role" aria-label="总览角色"><option value="all">全部角色</option><option value="supplier">供应商</option><option value="purchaser">采购单位</option></select></label><label class="field"><span>画布节点上限</span><select v-model.number="limit" aria-label="总览节点上限"><option v-for="n in [80, 160, 300]" :key="n" :value="n">{{ n }}</option></select></label><button class="button" @click="fit">适应画布</button></div>
    <div class="overview-coverage" aria-live="polite">匹配 {{ number(new Set(matches.map(node => node.name)).size) }} 家企业 / 单位 · 当前画布 {{ number(visible.nodes.length) }} 个节点、{{ number(visible.links.length) }} 条关系。关联节点保留，不等于全量关系均已绘出。</div>
    <div v-if="error" class="notice danger" role="alert">{{ error }}<button class="text-button" @click="load">重新加载总览</button></div>
    <p v-else-if="loading" class="overview-loading" role="status">正在读取全部企业名录与关系…</p>
    <p v-else-if="!visible.nodes.length" class="overview-loading">没有匹配的企业 / 单位，请调整搜索条件。</p>
    <div ref="canvas" class="dataset-network-canvas" :class="{ 'is-hidden': loading || !!error || !visible.nodes.length }" aria-label="总体企业关系网络" />
    <p class="footnote">紫色：采购单位 · 青绿：供应商 · 方形：采购包；实线为采购或中标，虚线为投标。默认从关联较多的主体展开，可搜索定位未在初始画布中显示的企业。</p>
  </div>
</template>
