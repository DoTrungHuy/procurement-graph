<script setup>
import { defineAsyncComponent, ref, watch } from 'vue'
import { api } from '../api'
import { useWorkspace } from '../composables/useWorkspace'
import { lotLabel, money, number, statusName, statusTone } from '../utils/presentation'
import AppIcon from '../components/AppIcon.vue'
import EmptyState from '../components/EmptyState.vue'
import PaginationBar from '../components/PaginationBar.vue'
const RelationNetwork = defineAsyncComponent(() => import('../components/RelationNetwork.vue'))
const w = useWorkspace()
const recentProjects = ref([]), recentLoading = ref(true), recentError = ref('')
const recentVersion = ref(0)
function refreshRecent() { recentVersion.value++ }
watch(() => [w.dataset, w.datasetLoading, recentVersion.value], async ([dataset, loading], previous, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  recentProjects.value = []; recentError.value = ''; recentLoading.value = true
  if (loading) return
  try {
    const response = await api.projects({ dataset_id: dataset, sort: 'announcement_date', page: 1, page_size: 5 })
    if (active) recentProjects.value = response.data
  } catch (error) { if (active) recentError.value = error.message }
  finally { if (active) recentLoading.value = false }
}, { immediate: true })
function page(value) { w.projectPage = value; w.loadProjects(false) }
</script>

<template>
  <section aria-labelledby="overview-title">
    <div class="page-heading"><h1 id="overview-title">工作台</h1><button class="button primary" @click="w.navigate('import')">导入数据</button></div>
    <div class="stat-strip" aria-label="当前数据概况" :aria-busy="w.datasetLoading">
      <div><span>公告</span><strong>{{ w.datasetLoading ? '—' : number(w.stats.notices) }}</strong></div>
      <div><span>采购包</span><strong>{{ w.datasetLoading ? '—' : number(w.stats.projects) }}</strong></div>
      <div><span>标的明细</span><strong>{{ w.datasetLoading ? '—' : number(w.stats.items) }}</strong></div>
      <div><span>投标主体</span><strong>{{ w.datasetLoading ? '—' : number(w.stats.suppliers) }}</strong></div>
      <div class="stat-amount"><span>已披露中标金额</span><strong>{{ w.datasetLoading ? '—' : money(w.stats.total_amount) }}</strong><small v-if="w.stats.unknown_amount_count">另有 {{ w.stats.unknown_amount_count }} 个采购包金额未披露</small></div>
    </div>
    <div class="visualization-grid overview-visuals" aria-label="采购关系与近期项目">
      <RelationNetwork title="采购关系网络"></RelationNetwork>
      <section class="surface recent-projects" aria-labelledby="recent-projects-title" :aria-busy="recentLoading">
        <div class="section-heading"><div><h2 id="recent-projects-title">近期项目</h2><p>当前数据集，按公告日期展示最新5个采购包。</p></div></div>
        <div v-if="recentError" class="notice danger" role="alert">{{ recentError }}<button class="text-button" @click="refreshRecent">重新加载近期项目</button></div>
        <EmptyState v-else-if="recentLoading || !recentProjects.length" :busy="recentLoading" :title="recentLoading ? '正在读取近期项目' : '暂无近期项目'" description="完整项目列表及查询条件保留在下方。"></EmptyState>
        <ul v-else class="recent-project-list">
          <li v-for="project in recentProjects" :key="project.id">
            <div><button class="text-button recent-project-link" :title="[project.name, project.lot_name].filter(Boolean).join(' · ')" @click="w.openProject(project.id)">{{ project.name }}</button><span class="cell-note">{{ project.purchaser || '采购单位未披露' }} · {{ lotLabel(project.lot_name) }}</span></div>
            <div class="recent-project-meta"><span class="date-cell">{{ project.announcement_date || '日期未披露' }}</span><span class="numeric">{{ money(project.awarded_amount) }}</span></div>
          </li>
        </ul>
      </section>
    </div>
    <section class="surface" aria-labelledby="projects-title">
      <div class="section-heading"><h2 id="projects-title">采购项目 <span class="count">{{ w.projectTotal }}</span></h2><button class="text-button" :disabled="w.datasetLoading" @click="w.loadDataset()">刷新数据</button></div>
      <form class="table-toolbar project-toolbar" @submit.prevent="w.loadProjects()">
        <label class="search-field has-search-icon"><AppIcon name="magnify"></AppIcon><span class="sr-only">搜索项目</span><input v-model="w.projectSearch" type="search" placeholder="搜索项目名称、编号或采购单位" :disabled="w.datasetLoading"></label>
        <label class="compact-filter"><span class="sr-only">筛选采购单位</span><select v-model="w.projectPurchaser" aria-label="筛选采购单位" :disabled="w.datasetLoading"><option value="">全部采购单位</option><option v-for="name in w.purchasers" :key="name">{{ name }}</option></select></label>
        <label class="project-sort"><span class="sr-only">项目排序</span><select v-model="w.projectSort" aria-label="项目排序" title="导入时间指首次入库时间，重新处理不会改变" :disabled="w.datasetLoading"><option value="announcement_date">公告日期：最新在前</option><option value="imported_at">导入时间：最新在前</option></select></label>
        <button class="button" :disabled="w.datasetLoading || w.projectLoading">查询</button>
      </form>
      <div v-if="w.projectError" class="notice danger inset" role="alert">{{ w.projectError }}<button class="text-button" @click="w.loadProjects(false)">重新加载</button></div>
      <p class="mobile-table-hint">左右滑动表格查看金额和供应商，点击项目查看详情。</p>
      <div class="table-scroll" :aria-busy="w.projectLoading || w.datasetLoading"><table class="project-table"><thead><tr><th>项目 / 采购包</th><th>采购单位</th><th>中标供应商</th><th class="numeric">中标金额（元）</th><th>公告日期</th><th>状态</th></tr></thead><tbody><tr v-for="project in w.projects" :key="project.id"><td><button class="project-link" :title="[project.name, project.lot_name, project.project_code].filter(Boolean).join(' · ')" @click="w.openProject(project.id)">{{ project.name }}</button><span class="cell-note">{{ lotLabel(project.lot_name) }}</span></td><td>{{ project.purchaser || '未披露' }}</td><td>{{ project.winners || '未披露' }}</td><td class="numeric">{{ money(project.awarded_amount) }}</td><td class="date-cell">{{ project.announcement_date || '未披露' }}</td><td><span class="status" :class="statusTone(project.processing_status)">{{ statusName(project.processing_status) }}</span></td></tr></tbody></table></div>
      <EmptyState v-if="!w.projects.length" :busy="w.datasetLoading || w.projectLoading" :title="w.datasetLoading || w.projectLoading ? '正在读取项目' : w.projectSearch || w.projectPurchaser ? '没有匹配的项目' : '这个数据集还没有项目'" :description="w.datasetLoading || w.projectLoading ? '' : '可调整查询条件，或在数据接入页导入公告。'"><button v-if="!w.datasetLoading && !w.projectSearch && !w.projectPurchaser" class="button" @click="w.navigate('import')">前往数据接入</button></EmptyState>
      <PaginationBar :total="w.projectTotal" :page="w.projectPage" :page-size="w.projectPageSize" :busy="w.projectLoading || w.datasetLoading" label="项目" @change="page"></PaginationBar>
    </section>
    <details v-if="w.dataset === 'official-reference'" class="scope-details"><summary><AppIcon name="help"></AppIcon>20条样本 · 仅公告正文</summary><p>当前为历史响应回放结果，每行项目对应一个采购包。已做来源核对，仍待人工复核，不代表官方全量成绩。</p></details>
  </section>
</template>
