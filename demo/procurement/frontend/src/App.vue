<script setup>
import { computed, ref } from 'vue'
import { createWorkspace } from './composables/useWorkspace'
import { useInterfacePolish } from './composables/useInterfacePolish'
import { datasetName } from './utils/presentation'
import OverviewView from './views/OverviewView.vue'
import ItemsView from './views/ItemsView.vue'
import ImportView from './views/ImportView.vue'
import RelationsView from './views/RelationsView.vue'
import ProjectPanel from './components/ProjectPanel.vue'
import EvidencePanel from './components/EvidencePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import AppIcon from './components/AppIcon.vue'
const w = createWorkspace()
const { scrolled, showBackToTop, backToTop } = useInterfacePolish()
const mainContent = ref(null)
const navigation = [{ id: 'overview', title: '工作台', icon: 'dashboard' }, { id: 'import', title: '数据接入', icon: 'files' }, { id: 'items', title: '标的检索', icon: 'search' }, { id: 'relations', title: '关系分析', icon: 'relations' }]
const currentView = computed(() => ({ overview: OverviewView, items: ItemsView, import: ImportView, relations: RelationsView }[w.page]))
</script>

<template>
  <a class="skip-link" href="#main-content" @click.prevent="mainContent?.focus()">跳到主要内容</a>
  <div class="app-shell">
    <aside class="sidebar" aria-label="主导航">
      <a class="brand" href="#/overview" aria-label="智采图谱工作台"><span class="brand-symbol"><AppIcon name="relations"></AppIcon></span><span>智采图谱</span></a>
      <nav v-tab-indicator class="primary-nav" aria-label="功能导航"><a v-for="item in navigation" :key="item.id" :href="`#/${item.id}`" :aria-current="w.page === item.id ? 'page' : undefined" :class="{ active: w.page === item.id }"><AppIcon :name="item.icon"></AppIcon><span>{{ item.title }}</span></a></nav>
      <div class="sidebar-footer"><span class="connection-status"><i :class="w.connection" aria-hidden="true"></i>{{ w.connection === 'online' ? '服务已连接' : w.connection === 'connecting' ? '正在连接服务' : '服务连接失败' }}</span><button class="sidebar-settings" @click="w.settingsOpen = true"><AppIcon name="settings"></AppIcon>连接设置</button></div>
    </aside>
    <div class="workspace">
      <header class="topbar" :class="{ 'is-scrolled': scrolled }">
        <div class="topbar-actions">
          <label class="dataset-control"><span>数据集</span><select v-model="w.dataset" :disabled="w.booting || w.jobActive" aria-label="当前数据集"><option v-for="dataset in w.visibleDatasets" :key="dataset.id" :value="dataset.id">{{ datasetName(dataset.id) }}</option><option v-if="!w.datasets.length" :value="w.dataset">{{ datasetName(w.dataset) }}</option></select></label>
          <span class="toolbar-divider" aria-hidden="true"></span>
          <button class="text-button quality-export" aria-label="处理说明" :disabled="w.exporting || w.datasetLoading || w.booting" @click="w.download('quality')"><span class="desktop-label">处理说明</span><span class="mobile-label">说明</span></button>
          <button class="button export-button" :disabled="w.exporting || w.datasetLoading || w.booting" @click="w.download('standard')">{{ w.exporting ? '准备下载…' : '导出三表' }} <span aria-hidden="true">↓</span></button>
        </div>
      </header>
      <main id="main-content" ref="mainContent" class="main-content" tabindex="-1">
        <div v-if="w.error" class="notice danger global-error" role="alert"><span>{{ w.error }}</span><div><button class="text-button" :disabled="w.booting || w.datasetLoading" @click="w.initialize()">重新连接</button><button class="text-button" aria-label="关闭提示" @click="w.error = ''">关闭</button></div></div>
        <div v-if="w.booting" class="boot-state" role="status"><span class="spinner" aria-hidden="true"></span>正在读取数据…</div>
        <Transition name="workspace-view" mode="out-in"><component :is="currentView" v-if="!w.booting" :key="w.page"></component></Transition>
      </main>
    </div>
  </div>
  <div class="toast-region" role="status" aria-live="polite"><p v-if="w.toast" class="toast">{{ w.toast }}</p></div>
  <Transition name="utility"><button v-if="showBackToTop && !w.projectPanelOpen && !w.evidence && !w.settingsOpen" class="back-to-top" :class="{ 'above-toast': !!w.toast }" @click="backToTop">回到顶部 <span aria-hidden="true">↑</span></button></Transition>
  <ProjectPanel></ProjectPanel><EvidencePanel></EvidencePanel>
  <SettingsPanel />
</template>
