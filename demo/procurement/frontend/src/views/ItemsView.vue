<script setup>
import { computed, ref } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { datasetName, money, number } from '../utils/presentation'
import EmptyState from '../components/EmptyState.vue'
import PaginationBar from '../components/PaginationBar.vue'
import AppIcon from '../components/AppIcon.vue'
const w = useWorkspace(), advanced = ref(false)
const applied = computed(() => Object.entries(w.itemApplied).filter(([, value]) => value !== '' && value != null).length)
function page(value) { w.itemPage = value; w.loadItems(false) }
function size(value) { w.itemPageSize = value; w.loadItems() }
const ranges = [{ title: '单价（元）', low: 'min_price', high: 'max_price' }, { title: '数量', low: 'min_quantity', high: 'max_quantity' }, { title: '总价（元）', low: 'min_total', high: 'max_total' }]
</script>

<template>
  <section aria-labelledby="items-title">
    <div class="page-heading"><div><h1 id="items-title">标的检索</h1><p>按产品、品目、品牌及规格型号检索抽取记录。</p></div><span class="heading-meta">{{ number(w.itemTotal) }} 条明细</span></div>
    <section class="surface" aria-label="标的筛选与结果">
      <form class="filter-form" @submit.prevent="w.loadItems()">
        <div class="search-row"><label class="search-field has-search-icon"><AppIcon name="magnify"></AppIcon><span class="sr-only">搜索标的</span><input v-model="w.itemSearch" type="search" placeholder="搜索标的、项目、采购单位或中标供应商" :disabled="w.datasetLoading"></label><button class="button primary" :disabled="w.itemLoading || w.datasetLoading">检索</button></div>
        <div class="filter-row"><label class="field"><span>品目名称</span><select v-model="w.filters.category" aria-label="品目名称" :disabled="w.datasetLoading"><option value="">全部品目</option><option v-for="name in w.categories" :key="name">{{ name }}</option></select></label><label class="field"><span>品牌</span><select v-model="w.filters.brand" aria-label="品牌" :disabled="w.datasetLoading"><option value="">全部品牌</option><option v-for="name in w.brands" :key="name">{{ name }}</option></select></label><label class="field model-field"><span>规格型号</span><input v-model="w.filters.model" aria-label="规格型号" placeholder="输入完整型号" :disabled="w.datasetLoading"></label><button type="button" class="text-button advanced-toggle" :aria-expanded="advanced" aria-controls="range-filters" @click="advanced = !advanced">{{ advanced ? '收起范围筛选' : '金额与数量筛选' }} <span aria-hidden="true">{{ advanced ? '−' : '+' }}</span></button><button type="button" class="text-button" :disabled="w.itemLoading || w.datasetLoading" @click="w.clearFilters()">重置</button></div>
        <div v-if="advanced" id="range-filters" class="range-filters"><fieldset v-for="range in ranges" :key="range.low"><legend>{{ range.title }}</legend><div class="range-inputs"><input v-model="w.filters[range.low]" type="number" min="0" step="any" placeholder="最低" :aria-label="`${range.title}下限`"><span>至</span><input v-model="w.filters[range.high]" type="number" min="0" step="any" placeholder="最高" :aria-label="`${range.title}上限`"></div></fieldset></div>
      </form>
      <div class="result-summary"><span>检索结果 <strong>{{ number(w.itemTotal) }}</strong> 条<span v-if="applied" class="muted"> · {{ applied }} 项条件已应用</span><span class="muted"> · {{ datasetName(w.dataset) }}</span></span><span class="muted">金额单位：人民币元</span></div>
      <div v-if="w.itemError" class="notice danger inset" role="alert">{{ w.itemError }}<button class="text-button" @click="w.loadItems(false)">重试</button></div>
      <p class="mobile-table-hint">左右滑动表格查看单价、数量和来源。</p>
      <div class="table-scroll" :aria-busy="w.itemLoading || w.datasetLoading"><table class="items-table"><thead><tr><th>标的 / 采购项目</th><th>品目</th><th>品牌 / 规格型号</th><th class="numeric">单价</th><th class="numeric">数量</th><th class="numeric">总价</th><th>来源</th></tr></thead><tbody><tr v-for="item in w.items" :key="item.id"><td><strong class="cell-title">{{ item.product_name || '未披露标的名称' }}</strong><button class="cell-note project-reference" @click="w.openProject(item.project_id)">{{ item.project_name }}</button><span class="cell-note">{{ item.lot_name }}</span></td><td>{{ item.category || '未披露' }}</td><td><span>{{ item.brand || '品牌未披露' }}</span><span class="cell-note model-text">{{ item.model || '型号未披露' }}</span></td><td class="numeric">{{ money(item.unit_price) }}</td><td class="numeric">{{ item.quantity == null ? '未披露' : number(item.quantity) }}<span class="unit">{{ item.quantity_unit }}</span></td><td class="numeric">{{ money(item.total_price) }}</td><td><button class="text-button" @click="w.evidence = item">查看原文</button></td></tr></tbody></table></div>
      <EmptyState v-if="!w.items.length" :busy="w.itemLoading || w.datasetLoading" :title="w.itemLoading || w.datasetLoading ? '正在检索标的' : '没有匹配的标的'" :description="`当前仅检索“${datasetName(w.dataset)}”。型号需完整匹配，可清除筛选或切换数据集。`"><button v-if="!w.itemLoading && !w.datasetLoading && applied" class="button" @click="w.clearFilters()">清除筛选</button></EmptyState>
      <PaginationBar :total="w.itemTotal" :page="w.itemPage" :page-size="w.itemPageSize" :busy="w.itemLoading || w.datasetLoading" :sizes="[20, 50, 100]" label="标的" @change="page" @size="size"></PaginationBar>
    </section>
  </section>
</template>
