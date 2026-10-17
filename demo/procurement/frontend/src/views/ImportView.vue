<script setup>
import { computed, ref } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { formatBytes, modeName, number, statusName, statusTone } from '../utils/presentation'
import EmptyState from '../components/EmptyState.vue'
import PaginationBar from '../components/PaginationBar.vue'
import AppIcon from '../components/AppIcon.vue'
const w = useWorkspace(), fileInput = ref(null), dragging = ref(false)
const percent = computed(() => w.job?.total ? Math.min(100, Math.round(w.job.completed / w.job.total * 100)) : 0)
const modeDescription = computed(() => w.processingMode === 'parse-only' ? '检查文件能否解析与识别附件，不生成新的抽取结果，也不调用模型。' : w.processingMode === 'replay' ? '只复用已保存的模型响应；找不到匹配缓存时报告失败，不发送模型请求。' : '通过配置的模型服务抽取实体与关系。服务可能产生费用。')
function drop(event) { dragging.value = false; if (!w.jobActive) w.captureFiles(event.dataTransfer.files) }
function remove(index) { w.files = w.files.filter((_, i) => i !== index); if (fileInput.value) fileInput.value.value = '' }
</script>

<template>
  <section aria-labelledby="import-title">
    <div class="page-heading"><div><span class="eyebrow">DATA INGESTION</span><h1 id="import-title">数据接入</h1><p>接入公告与附件，追踪从文件解析到结构化入库的过程。</p></div><span v-if="!w.health.model_network_enabled" class="status neutral">模型调用已暂停</span></div>
    <section class="surface import-surface" aria-label="新建处理任务">
      <div v-tab-indicator class="tabs import-tabs" aria-label="接入方式"><button v-for="method in [{ id: 'upload', title: '上传文件' }, { id: 'local', title: '本地基准数据' }, { id: 'samples', title: '核验样本' }]" :key="method.id" :class="{ active: w.importMethod === method.id }" :aria-pressed="w.importMethod === method.id" :disabled="w.jobActive" @click="w.importMethod = method.id">{{ method.title }}</button></div>
      <div class="import-body">
        <template v-if="w.importMethod !== 'samples'">
          <label class="field mode-field"><span>处理方式</span><select v-model="w.processingMode" aria-label="处理方式" :disabled="w.jobActive"><option value="parse-only">解析预检</option><option value="replay">历史响应回放</option><option value="extract" :disabled="!w.health.model_network_enabled">模型抽取{{ !w.health.model_network_enabled ? '（已暂停）' : '' }}</option></select></label><p class="form-help mode-description">{{ modeDescription }}</p>
          <template v-if="w.importMethod === 'upload'">
            <div class="drop-zone" :class="{ dragging, disabled: w.jobActive }" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="drop">
              <AppIcon class="drop-symbol" name="upload"></AppIcon>
              <strong>把公告与附件拖到这里</strong><p>支持 HTML、PDF、Office 文件、图片和压缩包</p><label class="button file-button" :class="{ disabled: w.jobActive }">选择文件<input ref="fileInput" type="file" multiple accept=".html,.htm,.pdf,.doc,.docx,.wps,.rtf,.xls,.xlsx,.png,.jpg,.jpeg,.webp,.zip,.rar,.7z,.txt,.csv" :disabled="w.jobActive" aria-label="选择公告与附件" @change="w.captureFiles($event.target.files)"></label><small>单文件上限 {{ formatBytes(w.health.max_upload_bytes || 0) }}</small>
            </div>
            <ul v-if="w.files.length" class="file-list"><li v-for="(file, index) in w.files" :key="`${file.name}-${index}`"><span>{{ file.name }}</span><small>{{ formatBytes(file.size) }}</small><button class="text-button" :aria-label="`移除文件${file.name}`" :disabled="w.jobActive" @click="remove(index)">移除</button></li></ul>
            <div class="form-actions"><span class="muted">{{ w.files.length ? `已选 ${w.files.length} 个文件` : '尚未选择文件' }}</span><button class="button primary" :disabled="w.jobActive || !w.files.length || w.datasetLoading" @click="w.upload()">{{ w.jobActive ? '任务处理中…' : '开始处理' }}</button></div>
          </template>
          <template v-else><div v-if="w.health.official_data_available" class="local-source"><h3>使用项目内的官方基准文件</h3><p>按公告编号关联正文及同名附件。先用少量公告检查流程，再逐步扩大处理范围。</p><label class="field limit-field"><span>本次处理公告数</span><input v-model.number="w.localLimit" type="number" min="1" max="2000" step="1" :disabled="w.jobActive"></label><div class="form-actions"><span class="muted">结果写入当前数据集</span><button class="button primary" :disabled="w.jobActive || w.datasetLoading" @click="w.importLocal()">{{ w.jobActive ? '任务处理中…' : '开始处理' }}</button></div></div><EmptyState v-else title="未找到本地基准文件" description="可切换到上传文件，或使用随项目提供的核验样本。"></EmptyState></template>
        </template>
        <div v-else class="sample-intro"><span class="eyebrow">内置样本</span><h2>查看与处理20条样本公告</h2><p>选择“公告核验样本”可直接浏览已有的 {{ number(w.samples.packages) }} 个采购包与 {{ number(w.samples.items) }} 条标的。需要按当前规则重新生成结果时，下载公告后选择“模型抽取”。</p><div class="notice neutral">回放仅接受与当前规则一致的已保存响应；规则更新后可能需要重新抽取。样本仅覆盖公告正文，仍待人工复核，不作为官方测试成绩。</div><div class="form-actions"><button class="button" :disabled="!w.samples.available || w.exporting" @click="w.download('samples')">下载样本公告</button><button class="button primary" :disabled="w.jobActive || !w.samples.available || w.datasetLoading" @click="w.replaySamples()">{{ w.jobActive ? '任务处理中…' : '回放核验样本' }}</button></div></div>
      </div>
    </section>
    <section class="surface job-surface" aria-labelledby="job-title">
      <div class="section-heading"><div><h2 id="job-title">处理记录</h2><p>任务结果、失败原因和每份公告的状态。</p></div><label v-if="w.jobHistory.length" class="history-select"><span class="sr-only">查看历史任务</span><select :value="w.job?.id || ''" :disabled="w.jobActive" aria-label="查看历史任务" @change="w.watchJob($event.target.value)"><option v-if="!w.job" value="">选择历史任务</option><option v-for="job in w.jobHistory" :key="job.id" :value="job.id">{{ modeName(job.mode) }} · {{ job.id.slice(0, 8) }} · {{ statusName(job.status) }}</option></select></label></div>
      <div v-if="w.jobProgressError" class="notice danger inset" role="alert">{{ w.jobProgressError }}<button v-if="w.job" class="text-button" @click="w.watchJob(w.job.id)">重连进度</button></div>
      <div v-if="w.job" class="job-detail">
        <div class="job-heading"><div><span class="status" :class="statusTone(w.job.status)">{{ statusName(w.job.status) }}</span><strong>{{ modeName(w.job.mode) }}</strong><span class="cell-code">{{ w.job.id }}</span></div><span class="muted">{{ w.job.elapsed_seconds == null ? '' : `${number(w.job.elapsed_seconds)} 秒` }}</span></div>
        <progress class="job-progress" :value="w.job.completed || 0" :max="w.job.total || 1" aria-label="任务进度"></progress>
        <div class="job-metrics"><span>{{ w.job.completed || 0 }} / {{ w.job.total || 0 }} 份 · {{ percent }}%</span><span>成功 <b>{{ w.job.succeeded || 0 }}</b></span><span>待核对 <b>{{ w.job.partial || 0 }}</b></span><span>失败 <b>{{ w.job.failed || 0 }}</b></span><span>模型请求 <b>{{ w.job.metrics?.model_calls || 0 }}</b></span><span>缓存命中 <b>{{ w.job.metrics?.response_cache_hits || 0 }}</b></span></div>
        <details v-if="w.job.errors?.length" class="job-errors"><summary>查看 {{ w.job.errors.length }} 条处理说明</summary><ul class="explanation-list"><li v-for="(error, index) in w.job.errors" :key="index">{{ typeof error === 'string' ? error : JSON.stringify(error) }}</li></ul></details>
        <div class="job-actions"><button class="button small" :disabled="w.jobActive || (w.job.mode === 'extract' && !w.health.model_network_enabled)" @click="w.retryJob()">重新处理此批</button><button class="button small" :disabled="!w.job.finished || w.exporting" @click="w.download('standard', w.job.id)">下载此批三表</button><button class="text-button" :disabled="!w.job.finished || w.exporting" @click="w.download('quality', w.job.id)">此批处理说明</button></div>
      </div>
      <EmptyState v-else :busy="w.ingesting" :title="w.ingesting ? '正在创建任务' : '还没有处理记录'" description="提交文件后，处理进度会显示在这里。"></EmptyState>
      <template v-if="w.jobDocuments.length"><div class="table-scroll"><table class="document-table"><thead><tr><th>公告编号</th><th>状态</th><th>处理说明</th><th class="numeric">用时（秒）</th></tr></thead><tbody><tr v-for="doc in w.jobDocuments" :key="doc.uuid || doc.id"><td class="mono">{{ doc.uuid || doc.notice_uuid || doc.id }}</td><td><span class="status" :class="statusTone(doc.status)">{{ statusName(doc.status) }}</span></td><td>{{ Array.isArray(doc.errors) ? doc.errors.join('；') || '无' : doc.error || doc.errors || '无' }}</td><td class="numeric">{{ number(doc.elapsed_seconds) }}</td></tr></tbody></table></div><PaginationBar :total="w.jobDocumentTotal" :page="w.jobDocumentPage" :page-size="20" label="处理文档" @change="w.loadJobDocuments"></PaginationBar></template>
    </section>
  </section>
</template>
