<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useWorkspace } from '../composables/useWorkspace'
import { lotLabel, money, statusName, statusTone } from '../utils/presentation'
const w = useWorkspace()
const graph = ref({ nodes: [], links: [] }), loading = ref(false), error = ref('')
let sequence = 0
const recent = ref([]), selected = ref(null)
function selectProject(project) { selected.value = project.id }
function imported(value) {
  if (!value) return '入库时间未记录'
  const raw = String(value).replace(' ', 'T')
  const date = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw}Z`)
  return Number.isNaN(date.getTime()) ? '入库时间未记录' : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
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
  loading.value = true; error.value = ''; graph.value = { nodes: [], links: [] }; recent.value = []; selected.value = null
  try {
    const response = await api.projects({ dataset_id: dataset, sort: 'imported_at', page: 1, page_size: 3 })
    if (token !== sequence || dataset !== w.dataset) return
    recent.value = response.data
    const preview = new Map(), links = []
    for (const project of recent.value) {
      const id = `project:${project.id}`
      preview.set(id, { id, name: `${project.name} ${project.lot_name || ''}`, type: 'project', value: project.awarded_amount })
      if (project.processing_status === 'fallback') continue
      if (project.purchaser) {
        const buyer = `purchaser:${project.purchaser}`
        preview.set(buyer, { id: buyer, name: project.purchaser, type: 'purchaser' })
        links.push({ source: buyer, target: id, relation: '采购' })
      }
      for (const name of (project.winners || '').split('；').filter(Boolean)) {
        const supplier = `supplier:${name}`
        preview.set(supplier, { id: supplier, name, type: 'supplier' })
        links.push({ source: supplier, target: id, relation: '中标' })
      }
    }
    graph.value = { nodes: [...preview.values()], links }

  } catch (cause) { if (token === sequence) error.value = cause.message }
  finally { if (token === sequence) loading.value = false }
}
watch(() => [w.dataset, w.stats.projects, w.datasetLoading], () => {
  if (!w.datasetLoading) load()
  else { ++sequence; loading.value = true; error.value = ''; graph.value = { nodes: [], links: [] }; recent.value = []; selected.value = null }
}, { immediate: true })
onBeforeUnmount(() => { ++sequence })
function open(node) { if (node.type === 'project') w.openProject(Number(node.id.replace('project:', ''))) }
</script>

<template>

  <div class="overview-grid">
    <section class="surface overview-network" aria-label="采购关系概览">
      <div class="section-heading"><div><h2>采购关系概览</h2><p>展示采购单位、采购包与中标供应商的关联关系。</p></div><button class="text-button" @click="w.navigate('relations')">进入关系分析 →</button></div>
      <div v-if="loading" class="map-empty" role="status">正在读取关系…</div>
      <div v-else-if="error" class="map-empty" role="alert"><p>{{ error }}</p><button class="text-button" @click="load">重新加载概览</button></div>
      <div v-else-if="!nodes.length" class="map-empty">当前数据集暂无可展示的中标关系</div>
      <div v-else class="map-scroll" tabindex="0" aria-label="采购关系概览，可滚动查看"><svg class="overview-map" :viewBox="`0 0 700 ${mapHeight}`" role="group" aria-label="最近入库的三个采购包及其中标关系，采购包可点击查看详情">
        <text v-for="column in columns" :key="column.type" :x="column.x" y="26" class="map-column">{{ column.label }}</text>
        <path v-for="(edge, index) in edges" :key="index" :d="edge.path" class="map-edge" />
        <g v-for="node in nodes" :key="node.id" :class="['map-node', node.type, { selected: node.id === `project:${selected}` }]" :transform="`translate(${node.x}, ${node.y})`" :role="node.type === 'project' ? 'button' : undefined" :tabindex="node.type === 'project' ? 0 : undefined" :aria-label="node.type === 'project' ? `查看采购包：${node.name}` : undefined" @click="open(node)" @keydown.enter.prevent="open(node)" @keydown.space.prevent="open(node)">
          <title>{{ node.name }}{{ node.type === 'project' ? ` · ${money(node.value)}` : '' }}</title>
          <rect :width="node.width" height="41" rx="9" /><circle cx="14" cy="21" r="3" />
          <text x="25" y="25">{{ node.name.length > (node.type === 'project' ? 13 : 11) ? node.name.slice(0, node.type === 'project' ? 13 : 11) + '…' : node.name }}</text>
        </g>
      </svg></div>
      <div class="map-caption"><span><i></i> 最近入库的 3 个采购包 · 规则兜底记录仅显示项目节点</span><span>点击采购包核验来源</span></div>
    </section>
    <section class="surface recent-projects" aria-label="近期项目">
      <div class="section-heading"><div><h2>近期项目</h2><p>按首次入库时间排序（北京时间）· 最近 3 个采购包</p></div></div>
      <p v-if="loading" class="footnote" role="status">正在读取项目…</p>
      <div v-else-if="error" class="notice danger" role="alert">{{ error }}<button class="text-button" @click="load">重新加载项目</button></div>
      <p v-else-if="!recent.length" class="footnote">当前数据集暂无采购项目。</p>
      <ol v-else class="recent-project-list">
        <li v-for="project in recent" :key="project.id" :class="{ selected: selected === project.id }">
          <button class="recent-project-select" :aria-pressed="selected === project.id" :title="project.name" @click="selectProject(project)">{{ project.name }}</button>
          <span class="cell-note">{{ lotLabel(project.lot_name) }}</span>
          <div class="recent-project-meta"><time :datetime="project.created_at || undefined">{{ imported(project.created_at) }}</time><span class="status" :class="statusTone(project.processing_status)" :title="project.processing_status === 'fallback' ? '规则提取记录，不纳入关系分析' : project.processing_status === 'partial' ? '部分内容待原文核对' : statusName(project.processing_status)">{{ statusName(project.processing_status) }}</span><button class="text-button" :aria-label="`查看详情：${project.name}`" @click="w.openProject(project.id)">查看详情</button></div>
        </li>
      </ol>
      <p class="footnote recent-note">点击项目名称可定位左侧节点；规则兜底记录不纳入关系分析。</p>
      <details class="recent-status-help"><summary>状态说明</summary><dl class="status-definitions"><div><dt><span class="status success">完成</span></dt><dd>已完成抽取与入库，记录可供检索。</dd></div><div><dt><span class="status warning">待核对</span></dt><dd>部分内容处理未完成，需结合原文核对。</dd></div><div><dt><span class="status warning">规则兜底</span></dt><dd>规则提取记录，不纳入关系分析。</dd></div><div><dt><span class="status danger">失败</span></dt><dd>失败原因可在处理记录中查看。</dd></div></dl></details>
    </section>
  </div>

</template>
