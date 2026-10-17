<script setup>
import { computed } from 'vue'
const props = defineProps({ results: String })
const entries = computed(() => (props.results || '').split('；').filter(Boolean))
const winners = computed(() => entries.value.filter(value => value.endsWith('：中标')).map(value => value.slice(0, -3)))
</script>

<template>
  <div class="bid-summary"><span v-if="winners.length" class="bid-winner"><i></i>{{ winners.join('、') }}</span><span v-else class="muted">中标主体未披露</span><small v-if="entries.length">{{ entries.length }} 家有效参与 · {{ winners.length }} 家中标</small>
    <details v-if="entries.length" class="bid-expansion"><summary>展开参与方</summary><ul><li v-for="(entry, index) in entries" :key="index">{{ entry }}</li></ul></details>
  </div>
</template>
