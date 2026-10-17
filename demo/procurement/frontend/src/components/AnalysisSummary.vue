<script setup>
import { computed } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { money, number } from '../utils/presentation'
const w = useWorkspace()
const cards = computed(() => {
  const r = w.result || {}
  if (w.scenario === 'common-projects') return [
    { label: '共同竞标采购包', value: number(r.summary?.project_count), unit: '包' },
    { label: '来自公告', value: number(r.summary?.notice_count), unit: '条' },
    { label: '已披露中标金额合计', value: money(r.summary?.total_amount), note: `另有 ${number(r.summary?.unknown_amount_count)} 包金额未披露` }
  ]
  if (w.scenario === 'common-purchasers') return [
    { label: '共同合作采购单位', value: number(r.purchasers?.length), unit: '家' },
    { label: '参与比较的供应商', value: number(r.suppliers?.length), unit: '家' },
    { label: '合作最频繁的单位', value: r.purchasers?.[0]?.purchaser || '暂无匹配', note: r.purchasers?.length ? `${number(r.purchasers[0].cooperation_count)} 个关联采购包` : '可更换主体或补充数据' }
  ]
  if (w.scenario === 'purchaser-partners') return [
    { label: '合作中标供应商', value: number(r.winning_suppliers?.length), unit: '家' },
    { label: '关联产品品牌', value: number(r.product_suppliers?.length), unit: '个' },
    { label: '合作最频繁的供应商', value: r.winning_suppliers?.[0]?.supplier || '暂无匹配', note: r.winning_suppliers?.length ? `${number(r.winning_suppliers[0].cooperation_count)} 个采购包 · ${money(r.winning_suppliers[0].total_amount)}` : '仅统计当前数据集' }
  ]
  const bidders = w.scenario === 'purchaser-bidders' ? r.top_bidders : r.top5
  return [
    { label: '高频主体 Top 5', value: number(bidders?.length), unit: '家' },
    { label: w.scenario === 'purchaser-bidders' ? '共同投标组合' : '全部参与主体', value: number(w.scenario === 'purchaser-bidders' ? r.collaborative_pairs?.length : r.all_participants?.length), unit: w.scenario === 'purchaser-bidders' ? '组' : '家' },
    { label: '最高频主体', value: bidders?.[0]?.bidder || '暂无匹配', note: bidders?.length ? `${number(bidders[0].frequency)} 个采购包` : '仅统计当前数据集' }
  ]
})
</script>

<template><div class="analysis-summary" aria-label="关系分析摘要"><div v-for="card in cards" :key="card.label"><span>{{ card.label }}</span><strong :title="String(card.value)">{{ card.value }} <small v-if="card.unit">{{ card.unit }}</small></strong><p v-if="card.note">{{ card.note }}</p></div></div></template>
