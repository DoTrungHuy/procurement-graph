<script setup>
import { defineAsyncComponent, ref } from 'vue'
import SidePanel from '../components/SidePanel.vue'
import { useWorkspace } from '../composables/useWorkspace'
import { scenarios } from '../utils/presentation'
import SupplierPicker from '../components/SupplierPicker.vue'
import ScenarioResults from '../components/ScenarioResults.vue'
import EmptyState from '../components/EmptyState.vue'
const w = useWorkspace(), overviewOpen = ref(false)
const DatasetNetwork = defineAsyncComponent(() => import('../components/DatasetNetwork.vue'))
</script>

<template>
  <section class="compact-relations" aria-labelledby="relations-title">
    <div class="page-heading"><div><h1 id="relations-title">关系分析</h1><p>采购单位、供应商及投标参与方的合作与竞争分析。</p></div><button class="button overview-network-entry" @click="overviewOpen = true">数据集关系总览 ↗</button></div>
    <section class="surface query-surface" aria-label="关系查询条件">
      <div v-tab-indicator class="tabs scenario-tabs" aria-label="关系分析场景"><button v-for="scenario in scenarios" :key="scenario.id" :data-scenario-tab="scenario.id" :class="{ active: w.scenario === scenario.id }" :aria-pressed="w.scenario === scenario.id" @click="w.scenario = scenario.id">{{ scenario.title }}</button></div>
      <div class="query-body"><p class="muted">{{ w.scenarioDefinition.description }}</p>
        <form class="query-form" @submit.prevent="w.runScenario()">
          <label v-if="w.scenarioDefinition.kind === 'purchaser'" class="field query-entity"><span>采购单位</span><select v-model="w.purchaser" aria-label="采购单位" :disabled="w.datasetLoading"><option v-if="!w.purchasers.length" value="">暂无采购单位</option><option v-for="name in w.purchasers" :key="name">{{ name }}</option></select></label>
          <label v-else-if="w.scenarioDefinition.kind === 'supplier'" class="field query-entity"><span>供应商</span><select v-model="w.supplier" aria-label="供应商" :disabled="w.datasetLoading"><option v-if="!w.suppliers.length" value="">暂无供应商</option><option v-for="name in w.suppliers" :key="name">{{ name }}</option></select></label>
          <div v-else class="field query-entity"><span id="supplier-picker-label">供应商 <span class="muted">选择2至30家</span></span><SupplierPicker v-model="w.selectedSuppliers" :options="w.suppliers" :disabled="w.datasetLoading" aria-labelledby="supplier-picker-label"></SupplierPicker></div>
          <div class="query-actions"><button type="submit" class="button primary" :disabled="!w.canQuery">{{ w.resultLoading ? '查询中…' : '查询关系' }}</button><button v-if="w.canUseExample" type="button" class="text-button" :disabled="w.datasetLoading || w.resultLoading" @click="w.useExample()">填入样本示例</button></div>
        </form>
      </div>
    </section>
    <div v-if="w.resultError" class="notice danger query-error" role="alert">{{ w.resultError }}<button class="text-button" @click="w.runScenario()">重新查询</button></div>
    <ScenarioResults v-if="w.result"></ScenarioResults>
    <div v-else class="surface query-placeholder"><EmptyState :busy="w.resultLoading" :title="w.resultLoading ? '正在查询关系' : '选择主体后开始查询'" :description="w.resultLoading ? '正在读取当前数据集中的采购包与投标记录。' : '查询后同时展示关系网络、统计图表和结果明细。'"></EmptyState></div>
    <SidePanel :open="overviewOpen" title="数据集关系总览" wide @close="overviewOpen = false"><DatasetNetwork v-if="overviewOpen" /></SidePanel>
  </section>
</template>
