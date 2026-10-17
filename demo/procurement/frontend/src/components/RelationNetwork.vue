<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { queryNetwork } from '../utils/queryNetwork'
import EmptyState from './EmptyState.vue'
const props = defineProps({ resultGroup: { type: String, default: 'primary' } })
const w = useWorkspace(), element = ref(null), limit = ref(8), loading = ref(false), error = ref(''), summary = ref(''), empty = ref(false), expanded = ref(false)
const moreMenu = ref(null), helpMenu = ref(null)
let chart, observer, token = 0, disposed = false
const colors = { purchaser: '#5b62c9', project: '#8c86af', supplier: '#358d80', brand: '#aa8752' }
async function draw() {
  const sequence = ++token, dataset = w.dataset
  loading.value = true; error.value = ''; summary.value = ''; empty.value = false; chart?.dispose(); chart = null
  try {
    const [data, { engine }] = await Promise.all([
      Promise.resolve(w.result ? queryNetwork(w.scenario, w.result, w.resultParameters || {}, limit.value, props.resultGroup) : { nodes: [], links: [], total: 0, shown: 0 }), import('../graph')
    ])
    if (disposed || sequence !== token || w.dataset !== dataset) return
    loading.value = false; empty.value = !data.links.length
    summary.value = `${data.nodes.length} 个节点 · ${data.links.length} 条关系` + ` · 展示 ${data.shown} / ${data.total} 项`
    await nextTick()
    if (disposed || sequence !== token || !element.value || empty.value || !element.value.clientWidth || !element.value.clientHeight) return
    const levels = [0, 1, 2].map(level => data.nodes.filter(node => node.level === level))
    chart = engine.init(element.value, null, { renderer: 'canvas' })
    chart.setOption({ animation: false, tooltip: { renderMode: 'richText', confine: true, backgroundColor: '#fff', borderColor: '#e3e7f0', textStyle: { color: '#263a56', fontSize: 12 }, formatter: params => params.dataType === 'edge' ? params.data.relation : `${params.data.name}\n${params.data.focus ? '当前查询主体' : { purchaser: '采购单位', project: '采购包', supplier: '供应商', brand: '产品品牌' }[params.data.type]}` }, series: [{ type: 'graph', layout: 'none', top: 70, bottom: 20, roam: true, draggable: true, scaleLimit: { min: .35, max: 3 }, data: levels.flatMap((nodes, level) => nodes.map((node, index) => ({ ...node, x: level * 340 + 70, y: nodes.length === 1 ? 180 : index * (360 / Math.max(1, nodes.length - 1)), symbol: node.type === 'project' ? 'roundRect' : 'circle', symbolSize: node.focus ? 38 : node.type === 'project' ? 24 : 20, itemStyle: { color: colors[node.type], borderColor: node.focus ? '#d4d4f6' : '#fff', borderWidth: node.focus ? 7 : 3 }, label: { show: node.focus || data.nodes.length <= 18, color: '#405371', fontSize: 11, position: 'right', width: 155, overflow: 'truncate', backgroundColor: '#ffffffdd', padding: [3, 5], borderRadius: 4 } }))), links: data.links.map(link => ({ ...link, lineStyle: { color: link.relation.includes('中标') ? '#60a99a' : '#a2acc9', type: link.relation.startsWith('投标') || link.relation.startsWith('参与投标') || link.relation.startsWith('共同竞标') || link.relation.startsWith('共同投标') ? 'dashed' : 'solid', width: 1.5, opacity: .7, curveness: .08 } })), emphasis: { focus: 'adjacency', label: { show: true, width: 240, overflow: 'break', color: '#182a45' }, lineStyle: { width: 3, opacity: 1 } }, blur: { itemStyle: { opacity: .18 }, lineStyle: { opacity: .08 }, label: { opacity: .2 } } }] })
    chart.on('click', params => { if (params.dataType === 'node' && params.data.type === 'project') { const id = params.data.projectId ?? Number(String(params.data.id).replace('project:', '')); if (Number.isFinite(id)) w.openProject(id) } })
    chart.resize()
  } catch (cause) { if (sequence === token && !disposed) { error.value = cause.message; loading.value = false } }
}
function resize() { if (!disposed && chart && !chart.isDisposed() && element.value?.clientWidth > 0 && element.value?.clientHeight > 0) chart.resize() }
function fit() { chart?.setOption({ series: [{ center: ['50%', '50%'], zoom: 1 }] }); resize() }
async function toggleExpanded() { expanded.value = !expanded.value; await nextTick(); resize() }
function closeMenus() { if (moreMenu.value) moreMenu.value.open = false; if (helpMenu.value) helpMenu.value.open = false }
function outside(event) { if (!moreMenu.value?.contains(event.target) && !helpMenu.value?.contains(event.target)) closeMenus() }
function reset() { closeMenus(); draw() }
function escape(event) { if (event.key !== 'Escape') return; if (moreMenu.value?.open || helpMenu.value?.open) closeMenus(); else if (expanded.value) toggleExpanded() }
watch(() => [w.dataset, limit.value, props.resultGroup], draw)
watch(() => w.result, draw)
onMounted(() => { observer = new ResizeObserver(() => { if (!disposed && element.value?.clientWidth > 0 && element.value?.clientHeight > 0) { if (chart) resize(); else if (!loading.value && !empty.value && !error.value) draw() } }); observer.observe(element.value); window.addEventListener('keydown', escape); document.addEventListener('pointerdown', outside); draw() })
onBeforeUnmount(() => { disposed = true; ++token; observer?.disconnect(); chart?.dispose(); chart = null; window.removeEventListener('keydown', escape); document.removeEventListener('pointerdown', outside) })
</script>

<template>
  <section class="surface network-surface" :class="{ 'network-expanded': expanded }" aria-label="关系网络">
    <div class="section-heading network-heading"><div class="network-title"><h2>关系网络</h2><details ref="helpMenu" class="network-help"><summary role="button" aria-label="关系网络使用说明" title="使用说明">?</summary><p>拖动节点或缩放画布查看联系。悬停可读取完整名称与关系说明，点击采购包打开详情。图中仅展示当前查询结果；完整企业名录请使用页面顶部的“数据集关系总览”。</p></details></div><span class="network-scope-tag">当前查询</span></div>
    <div class="network-viewport">
      <div class="network-floating-tools" aria-label="画布工具">
        <button class="network-icon-button" aria-label="适应画布" title="适应画布" :disabled="loading || empty || !!error" @click="fit"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 8h8v8H8z" /></svg></button>
        <button class="network-icon-button" :aria-label="expanded ? '退出全屏' : '全屏查看'" :title="expanded ? '退出全屏' : '全屏查看'" :aria-pressed="expanded" @click="toggleExpanded"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 3l5 5m13-5-5 5M3 21l5-5m13 5-5-5" /></svg></button>
        <details ref="moreMenu" class="network-more"><summary role="button" class="network-icon-button" aria-label="更多画布操作" title="更多操作"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg></summary><div class="network-more-menu"><button :disabled="loading || empty || !!error" @click="reset">重置视图</button></div></details>
      </div>
    <div v-if="error" class="notice danger inset" role="alert">{{ error }}<button class="text-button" @click="draw">重新加载</button></div>
    <EmptyState v-if="loading" busy title="正在绘制关系网络" />
    <EmptyState v-else-if="empty && !error" :title="!w.result ? '请先执行关系查询' : '暂无可展示的关系'" description="可调整查询条件，或查看页面顶部的数据集关系总览。" />
    <div ref="element" class="network-canvas" :class="{ 'is-hidden': loading || error || empty }" aria-label="可拖动的采购关系网络" />
    </div>
    <div class="network-bottom-legend"><div class="network-legend"><span><i :style="{ background: colors.purchaser }"></i>采购单位</span><span><i class="square" :style="{ background: colors.project }"></i>采购包</span><span><i :style="{ background: colors.supplier }"></i>供应商</span><span><i :style="{ background: colors.brand }"></i>产品品牌</span><span>实线：采购 / 中标</span><span>虚线：投标 / 共同竞标</span></div></div>
    <div class="network-footer network-display-footer"><span data-testid="network-summary">{{ summary }}</span><label class="network-display-limit"><span class="sr-only">关系网络显示数量</span><select v-model.number="limit" aria-label="关系网络显示数量"><option v-for="value in [8, 16, 30, 80]" :key="value" :value="value">显示前 {{ value }} 项</option></select></label></div>

  </section>
</template>
