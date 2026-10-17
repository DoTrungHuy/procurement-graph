<script setup>
import { computed, ref, watch } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { jsonValue, money, number } from '../utils/presentation'
import SidePanel from './SidePanel.vue'
const w = useWorkspace()
const related = computed(() => jsonValue(w.evidence?.related_sources, []))
const derived = computed(() => jsonValue(w.evidence?.derived_values, {}))
const selected = ref('model')
const fields = [
  { key: 'product_name', label: '产品 / 服务名称' }, { key: 'category', label: '品目' },
  { key: 'brand', label: '品牌' }, { key: 'model', label: '规格型号' },
  { key: 'unit_price', label: '单价', money: true }, { key: 'quantity', label: '数量' },
  { key: 'total_price', label: '总价', money: true }
]
const selectedField = computed(() => fields.find(field => field.key === selected.value))
const selectedValue = computed(() => w.evidence?.[selected.value])
const quote = computed(() => w.evidence?.source_context?.text || w.evidence?.evidence || '')
const fragments = computed(() => {
  const value = selectedValue.value
  if (value == null || value === '') return [{ text: quote.value, match: false }]
  // Literal matching never invents an offset or treats a calculated value as printed evidence.
  const needle = String(value), parts = quote.value.split(needle)
  return parts.flatMap((text, index) => index ? [{ text: needle, match: true }, { text, match: false }] : [{ text, match: false }])
})
const matched = computed(() => fragments.value.some(part => part.match))
const derivedLabel = key => fields.find(field => field.key === key)?.label || key
watch(() => w.evidence, () => { selected.value = 'model' })
function fieldValue(field) {
  const value = w.evidence?.[field.key]
  if (value == null || value === '') return '未披露'
  return field.money ? money(value) : field.key === 'quantity' ? `${number(value)} ${w.evidence.quantity_unit || ''}` : value
}
</script>

<template>
  <SidePanel :open="!!w.evidence" title="标的来源" @close="w.evidence = null">
    <template v-if="w.evidence">
      <p class="eyebrow">{{ w.evidence.category || '未披露品目' }}</p>
      <h3 class="detail-title">{{ w.evidence.product_name || '未命名标的' }}</h3>
      <p class="muted">{{ w.evidence.project_name }}<span v-if="w.evidence.lot_name"> · {{ w.evidence.lot_name }}</span></p>
      <p class="evidence-hint">选择字段，对照原文核验。高亮仅表示文字匹配。</p>
      <div class="evidence-fields" aria-label="选择核验字段"><button v-for="field in fields" :key="field.key" :aria-pressed="selected === field.key" :class="{ selected: selected === field.key }" @click="selected = field.key"><span>{{ field.label }}</span><strong>{{ fieldValue(field) }}</strong><small>{{ w.evidence[field.key] == null || w.evidence[field.key] === '' ? '未记录 · 请核验' : '抽取记录' }}</small></button></div>
      <dl class="source-fields"><div><dt>中标供应商</dt><dd>{{ w.evidence.winner_supplier || '未披露' }}</dd></div></dl>
      <section class="detail-section"><h3>原文证据 <span class="muted">正在核验：{{ selectedField.label }}</span></h3><blockquote class="source-quote" :class="{ 'grounded-quote': !!w.evidence.source_context?.text }"><template v-for="(part, index) in fragments" :key="index"><mark v-if="part.match">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template><template v-if="!quote">这条记录暂未保存原文片段，请查看来源文件。</template></blockquote><p class="footnote" aria-live="polite">{{ matched ? '已在当前片段中找到相同文字，请结合上下文确认。' : '当前片段未找到字段原值；数字格式可能不同，请结合来源文件核对。' }} {{ w.evidence.source_context?.location }}</p></section>
      <dl class="source-fields"><div><dt>来源文件</dt><dd>{{ w.evidence.source_file || '未记录' }}</dd></div><div><dt>定位</dt><dd>{{ w.evidence.source_location || w.evidence.line_id || '未记录' }}</dd></div><div v-if="w.evidence.business_line_id"><dt>原文业务行</dt><dd>{{ w.evidence.business_line_id }}</dd></div><div><dt>来源类型</dt><dd>{{ w.evidence.source === 'announcement' ? '公告正文' : w.evidence.source === 'attachment' ? '采购附件' : w.evidence.source || '未记录' }}</dd></div></dl>
      <section v-if="related.length" class="detail-section"><h3>其他关联来源</h3><article v-for="(source, index) in related" :key="index" class="related-source"><strong>{{ source.source_file || '未记录来源文件' }}</strong><p>{{ source.source_location || '未记录定位' }} · {{ source.source === 'announcement' ? '公告正文' : source.source === 'attachment' ? '采购附件' : source.source || '来源未记录' }}</p><blockquote v-if="source.evidence" class="source-quote">{{ source.evidence }}</blockquote></article></section>
      <section v-if="Object.keys(derived).length" class="detail-section"><h3>计算与补充说明 <span class="status warning">辅助推算</span></h3><dl class="source-fields"><div v-for="(value, key) in derived" :key="key"><dt>{{ derivedLabel(key) }}</dt><dd>{{ ['unit_price', 'total_price'].includes(key) ? money(value) : value }}</dd></div></dl><p class="footnote">以上为计算补充值，与原文抽取字段分开保存，不作为原文已披露值。</p></section>
      <p class="footnote">未披露的字段保留为空，不把缺失金额当作零。来源片段用于核对，不能替代原始公告。</p>
    </template>
  </SidePanel>
</template>
