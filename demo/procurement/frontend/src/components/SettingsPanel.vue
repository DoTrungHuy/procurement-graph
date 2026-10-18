<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { useWorkspace } from '../composables/useWorkspace'
import SidePanel from './SidePanel.vue'

const w = useWorkspace()
const section = ref('access')
const loading = ref(false), saving = ref(false), error = ref(''), saved = ref(false), showKey = ref(false)
const configured = ref(false)
const form = ref({ base_url: '', model: '', api_key: '', enabled: true })
let sequence = 0
const keyHint = computed(() => configured.value ? '已配置 · 留空保留，填写新Key可替换' : '尚未配置')
async function load() {
  const token = ++sequence
  loading.value = true; error.value = ''; saved.value = false; form.value.api_key = ''; showKey.value = false
  try {
    const value = await api.modelSettings()
    if (token !== sequence || !w.settingsOpen) return
    form.value = { base_url: value.base_url, model: value.model, api_key: '', enabled: value.enabled }
    configured.value = value.api_key_configured
    error.value = value.configuration_error || ''
  } catch (exception) { if (token === sequence) error.value = exception.message }
  finally { if (token === sequence) loading.value = false }
}
async function save() {
  const token = sequence
  saving.value = true; error.value = ''; saved.value = false
  const payload = { ...form.value, api_key: form.value.api_key.trim() || null }
  try {
    const value = await api.saveModelSettings(payload)
    const health = await api.health()
    w.health = health
    if (!health.model_network_enabled && w.processingMode === 'extract') w.processingMode = 'parse-only'
    if (token !== sequence || !w.settingsOpen) return
    form.value = { base_url: value.base_url, model: value.model, api_key: '', enabled: value.enabled }
    configured.value = value.api_key_configured; showKey.value = false; saved.value = true
  } catch (exception) { if (token === sequence) error.value = exception.message }
  finally { saving.value = false }
}
watch(() => w.settingsOpen, open => { if (open) section.value = 'access' })
watch(() => [w.settingsOpen, section.value], ([open, active]) => {
  if (open && active === 'model') load()
  else { ++sequence; form.value.api_key = ''; showKey.value = false; loading.value = false }
})
</script>

<template>
  <SidePanel :open="w.settingsOpen" title="连接设置" @close="w.settingsOpen = false">
    <div class="settings-status"><span class="status" :class="w.connection === 'online' ? 'success' : 'warning'">{{ w.connection === 'online' ? '服务可达' : '等待连接' }}</span><span class="muted">版本 {{ w.health.version || '未读取' }}</span></div>
    <div class="tabs settings-tabs" aria-label="连接设置分类"><button :aria-pressed="section === 'access'" :class="{ active: section === 'access' }" @click="section = 'access'">服务访问</button><button :aria-pressed="section === 'model'" :class="{ active: section === 'model' }" :disabled="w.health.auth_required && !w.authorized" @click="section = 'model'">模型服务</button></div>
    <section v-if="section === 'access'" class="detail-section">
      <h3>连接网站服务</h3><p class="form-help">访问令牌用于进入网站，由管理员提供。</p>
      <p v-if="w.error" class="notice danger" role="alert">{{ w.error }}</p>
      <p class="status" :class="w.authorized ? 'success' : 'warning'">{{ w.authorized ? '网站访问已连接' : w.health.auth_required ? '请先填写访问令牌' : '无需访问令牌' }}</p>
      <form @submit.prevent="w.applyToken()"><label class="field"><span>服务访问令牌 <span v-if="!w.health.auth_required" class="muted">可选</span></span><input v-model="w.accessToken" aria-label="服务访问令牌" type="password" autocomplete="off" placeholder="填写管理员提供的访问令牌"></label><p class="form-help">令牌仅保存在当前浏览器会话中。</p><button class="button primary" :disabled="w.settingsBusy" type="submit">{{ w.settingsBusy ? '连接中…' : '保存并连接' }}</button></form>
      <label class="model-enabled"><input v-model="w.showEmptyDatasets" type="checkbox" aria-label="显示空数据集"><span>显示空数据集（用于导入新数据）</span></label>
      <p class="footnote">首次进入优先展示最新的全量数据；切换后会记住你的选择。模型配置请进入“模型服务”。</p>
    </section>
    <section v-else class="detail-section model-settings"><h3>模型服务</h3>
      <p class="form-help">填写后保存，下次打开会保留地址和模型名称。已保存的Key不会显示，留空即可继续使用。</p>
      <p v-if="loading" class="form-help" role="status">正在读取模型配置…</p>
      <p v-if="error" class="notice danger" role="alert">{{ error }} <button v-if="!saving" class="text-button" type="button" @click="load">重新读取</button></p>
      <p v-if="saved" class="notice success" role="status">配置已保存，新任务立即生效。</p>
      <form @submit.prevent="save">
        <fieldset :disabled="loading || saving" class="model-fields">
          <label class="field"><span>服务地址</span><input v-model="form.base_url" type="url" required maxlength="2048" autocomplete="off" placeholder="https://api.deepseek.com" aria-label="模型服务地址"></label>
          <p class="form-help">填写兼容OpenAI接口的基础地址，例如 https://api.deepseek.com 或服务商提供的 /v1 地址。</p>
          <label class="field"><span>模型名称</span><input v-model="form.model" type="text" required maxlength="200" autocomplete="off" placeholder="服务商提供的模型标识" aria-label="模型名称"></label>
          <label class="field"><span class="key-label">API Key <span class="muted" data-testid="model-key-status">{{ keyHint }}</span></span><span class="key-input"><input v-model="form.api_key" :type="showKey ? 'text' : 'password'" maxlength="8192" autocomplete="new-password" :placeholder="configured ? '留空保留已保存的Key' : '填写API Key'" aria-label="模型API Key"><button type="button" class="text-button" :aria-pressed="showKey" @click="showKey = !showKey">{{ showKey ? '隐藏' : '显示' }}</button></span></label>
          <label class="model-enabled"><input v-model="form.enabled" type="checkbox" aria-label="启用模型抽取"><span>启用模型抽取</span></label>
          <p class="form-help">保存配置不会请求模型。只有手动提交模型抽取任务才会调用服务；正在运行的任务继续使用原配置。</p>
          <button class="button primary" type="submit">{{ saving ? '保存中…' : '保存模型配置' }}</button>
        </fieldset>
      </form>
      <p class="footnote">Key保存在当前服务端，不写入浏览器缓存。模型请求可能产生服务商费用。</p>
      <p class="notice" :class="w.health.model_network_enabled ? 'warning' : 'neutral'">{{ w.health.model_network_enabled ? '模型抽取已启用，提交抽取任务可能产生费用。' : '模型调用已暂停。检索、关系分析、历史响应回放和导出仍可使用。' }}</p>
    </section>
  </SidePanel>
</template>

<style scoped>
.model-settings { margin-top: 20px; }
.settings-tabs { margin-top: 20px; }
.model-fields { border: 0; padding: 0; margin: 0; min-width: 0; }
.model-fields .field { margin-top: 14px; }
.key-label { display: flex; gap: 8px; flex-wrap: wrap; }
.key-label .muted { font-size: 11px; }
.key-input { display: flex; align-items: center; gap: 10px; }
.key-input input { flex: 1; min-width: 0; }
.key-input button { flex-shrink: 0; }
.model-enabled { display: flex; align-items: center; gap: 8px; margin: 18px 0 8px; font-size: 13px; }
.model-enabled input { width: 15px; height: 15px; accent-color: var(--blue); }
.model-fields > .button { margin-top: 12px; }
</style>
