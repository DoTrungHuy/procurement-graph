<script setup>
import { defineAsyncComponent } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { scenarios } from '../utils/presentation'
import SupplierPicker from '../components/SupplierPicker.vue'
import ScenarioResults from '../components/ScenarioResults.vue'
const RelationNetwork = defineAsyncComponent(() => import('../components/RelationNetwork.vue'))
const w = useWorkspace()
</script>

<template>
  <section class="relations-view" aria-labelledby="relations-title">
    <div class="page-heading"><h1 id="relations-title">关系分析</h1></div>
    <section class="surface query-surface" aria-label="关系查询条件">
      <div v-tab-indicator class="tabs scenario-tabs" aria-label="关系分析场景"><button v-for="scenario in scenarios" :key="scenario.id" :data-scenario-tab="scenario.id" :class="{ active: w.scenario === scenario.id }" :aria-pressed="w.scenario === scenario.id" @click="w.scenario = scenario.id">{{ scenario.title }}</button></div>
      <div class="query-body"><h2>{{ w.scenarioDefinition.heading }}</h2><p class="muted">{{ w.scenarioDefinition.description }}</p>
        <form class="query-form" @submit.prevent="w.runScenario()">
          <label v-if="w.scenarioDefinition.kind === 'purchaser'" class="field query-entity"><span>采购单位</span><select v-model="w.purchaser" aria-label="采购单位" :disabled="w.datasetLoading"><option v-if="!w.purchasers.length" value="">暂无采购单位</option><option v-for="name in w.purchasers" :key="name">{{ name }}</option></select></label>
          <label v-else-if="w.scenarioDefinition.kind === 'supplier'" class="field query-entity"><span>供应商</span><select v-model="w.supplier" aria-label="供应商" :disabled="w.datasetLoading"><option v-if="!w.suppliers.length" value="">暂无供应商</option><option v-for="name in w.suppliers" :key="name">{{ name }}</option></select></label>
          <div v-else class="field query-entity"><span id="supplier-picker-label">供应商 <span class="muted">选择2至30家</span></span><SupplierPicker v-model="w.selectedSuppliers" :options="w.suppliers" :disabled="w.datasetLoading" aria-labelledby="supplier-picker-label"></SupplierPicker></div>
          <div class="query-actions"><button type="submit" class="button primary" :disabled="!w.canQuery">{{ w.resultLoading ? '查询中…' : '查询关系' }}</button><button v-if="w.canUseExample" type="button" class="text-button" :disabled="w.datasetLoading || w.resultLoading" @click="w.useExample()">填入样本示例</button></div>
        </form>
      </div>
    </section>
    <div v-if="w.resultError" class="notice danger query-error" role="alert">{{ w.resultError }}<button class="text-button" @click="w.runScenario()">重新查询</button></div>
    <ScenarioResults><template #network><RelationNetwork id="relationship-network"></RelationNetwork></template></ScenarioResults>
  </section>
</template>
