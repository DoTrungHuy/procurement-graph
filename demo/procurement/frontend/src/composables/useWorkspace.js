import { computed, inject, nextTick, onBeforeUnmount, onMounted, provide, reactive, watch } from 'vue'
import { api, readToken, saveToken } from '../api'
import { scenarios, scenarioExamples, preferredDataset } from '../utils/presentation'

const workspaceKey = Symbol('procurement-workspace')
const stored = (key, fallback = '') => { try { return localStorage.getItem(key) || fallback } catch { return fallback } }
const remember = (key, value) => { try { localStorage.setItem(key, value) } catch { /* Storage is optional. */ } }
const emptyFilters = () => ({ category: '', brand: '', model: '', min_price: '', max_price: '', min_quantity: '', max_quantity: '', min_total: '', max_total: '' })
const pages = new Set(['overview', 'items', 'relations', 'import'])
const pageFromHash = () => {
  const value = location.hash.replace(/^#\/?/, '')
  return pages.has(value) ? value : 'overview'
}

export function createWorkspace() {
  const initialDataset = stored('procurement.dataset')
  const initialProjectSort = stored('procurement.projectSort', 'announcement_date')
  const w = reactive({
    page: pageFromHash(), dataset: /^[\p{L}\p{N}_-]{1,80}$/u.test(initialDataset) ? initialDataset : 'official-reference',
    health: {}, datasets: [], samples: {}, stats: {}, purchasers: [], suppliers: [], brands: [], categories: [], authorized: false, showEmptyDatasets: false,
    booting: true, datasetLoading: false, connection: 'connecting', error: '', toast: '',
    settingsOpen: false, settingsBusy: false, accessToken: readToken(), exporting: false,
    projects: [], projectTotal: 0, projectPage: 1, projectPageSize: 10, projectSearch: '', projectPurchaser: '', projectLoading: false, projectError: '',
    projectSort: ['announcement_date', 'imported_at'].includes(initialProjectSort) ? initialProjectSort : 'announcement_date',
    items: [], itemTotal: 0, itemPage: 1, itemPageSize: 20, itemSearch: '', filters: emptyFilters(), itemApplied: {}, itemLoading: false, itemError: '',
    scenario: scenarios[0].id, purchaser: '', supplier: '', selectedSuppliers: [], result: null,
    resultLoading: false, resultError: '', renderMs: null, renderState: '', resultParameters: null, networkOpen: false,
    evidence: null, projectPanelOpen: false, projectDetail: null, projectDetailLoading: false, projectDetailError: '', projectDetailTab: 'items',
    importMethod: 'upload', processingMode: 'parse-only', files: [], localLimit: 5, ingesting: false,
    job: null, jobHistory: [], jobDocuments: [], jobDocumentTotal: 0, jobDocumentPage: 1, jobProgressError: ''
  })
  let stopped = false, scopeSequence = 0, itemSequence = 0, projectSequence = 0, querySequence = 0
  let jobSequence = 0, documentSequence = 0, detailSequence = 0, pollTimer, toastTimer
  let pendingRender = null
  const scope = () => ({ dataset_id: w.dataset })
  const fail = error => error instanceof Error ? error.message : String(error)
  const current = (sequence, dataset) => !stopped && sequence === scopeSequence && dataset === w.dataset

  function notify(message) {
    clearTimeout(toastTimer); w.toast = message
    toastTimer = setTimeout(() => { w.toast = '' }, 4500)
  }
  function navigate(page) {
    if (!pages.has(page)) return
    w.page = page
    if (location.hash !== `#/${page}`) location.hash = `/${page}`
    window.scrollTo({ top: 0, behavior: 'instant' })
  }
  function stopPolling() { ++jobSequence; clearTimeout(pollTimer) }
  function resetScope() {
    stopPolling(); ++itemSequence; ++projectSequence; ++querySequence; ++detailSequence; ++documentSequence
    w.stats = {}; w.projects = []; w.projectTotal = 0; w.items = []; w.itemTotal = 0
    w.purchasers = []; w.suppliers = []; w.brands = []; w.categories = []
    w.projectPage = 1; w.projectSearch = ''; w.projectPurchaser = ''; w.itemPage = 1; w.itemSearch = ''
    w.filters = emptyFilters(); w.itemApplied = {}; w.result = null; w.resultError = ''; w.renderMs = null; w.renderState = ''; pendingRender = null
    w.selectedSuppliers = []; w.purchaser = ''; w.supplier = ''; w.evidence = null
    w.projectPanelOpen = false; w.projectDetail = null; w.networkOpen = false
    w.job = null; w.jobHistory = []; w.jobDocuments = []; w.jobDocumentTotal = 0; w.jobDocumentPage = 1
    w.ingesting = false; w.error = ''; w.itemError = ''; w.projectError = ''; w.resultLoading = false
    w.itemLoading = false; w.projectLoading = false; w.projectDetailLoading = false; w.projectDetailError = ''; w.jobProgressError = ''
  }
  async function loadDataset(reset = false) {
    const dataset = w.dataset, sequence = ++scopeSequence
    if (reset) resetScope()
    const itemsToken = ++itemSequence, projectsToken = ++projectSequence
    const appliedItems = { ...w.itemApplied }
    w.datasetLoading = true
    try {
      const params = { dataset_id: dataset }
      const [stats, projects, items, purchasers, suppliers, brands, categories, history, datasets] = await Promise.all([
        api.stats(params), api.projects({ ...params, q: w.projectSearch, purchaser: w.projectPurchaser, sort: w.projectSort, page: w.projectPage, page_size: w.projectPageSize }),
        api.items({ ...params, ...appliedItems, page: w.itemPage, page_size: w.itemPageSize }),
        api.entities('purchaser', params), api.entities('supplier', params), api.entities('brand', params), api.entities('category', params), api.jobs(params), api.datasets()
      ])
      if (!current(sequence, dataset)) return
      w.stats = stats; w.datasets = datasets; w.jobHistory = history
      w.purchasers = purchasers; w.suppliers = suppliers; w.brands = brands; w.categories = categories
      if (projectsToken === projectSequence) { w.projects = projects.data; w.projectTotal = projects.total }
      if (itemsToken === itemSequence) { w.items = items.data; w.itemTotal = items.total; w.itemApplied = appliedItems }
      if (!purchasers.includes(w.purchaser)) w.purchaser = purchasers.includes('香港城市大学(东莞)') ? '香港城市大学(东莞)' : purchasers[0] || ''
      if (!suppliers.includes(w.supplier)) w.supplier = suppliers.includes('中国移动通信集团广东有限公司') ? '中国移动通信集团广东有限公司' : suppliers[0] || ''
      w.selectedSuppliers = w.selectedSuppliers.filter(name => suppliers.includes(name))
      w.error = ''
    } catch (error) { if (current(sequence, dataset)) w.error = fail(error) }
    finally { if (current(sequence, dataset)) w.datasetLoading = false }
  }
  async function initialize() {
    w.booting = true; w.error = ''; w.connection = 'connecting'
    try {
      w.health = await api.health(); w.connection = 'online'
      if (!w.health.model_network_enabled) w.processingMode = 'parse-only'
      if (w.health.auth_required && !readToken()) { w.authorized = false; w.settingsOpen = true; return }
      const [datasets, samples] = await Promise.all([api.datasets(), api.samples()])
      if (stopped) return
      w.datasets = datasets; w.samples = samples; w.authorized = true
      w.dataset = preferredDataset(datasets, stored('procurement.dataset'))
      await loadDataset()
      const remembered = stored(`procurement.job.${w.dataset}`)
      const candidate = w.jobHistory.find(j => j.id === remembered) || w.jobHistory[0]
      if (candidate) watchJob(candidate.id)
    } catch (error) { w.error = fail(error); if (error.status === 401) { w.authorized = false; w.settingsOpen = true }; if (!w.health.status) w.connection = 'offline' }
    finally { if (!stopped) w.booting = false }
  }
  async function applyToken() {
    w.settingsBusy = true
    try { saveToken(w.accessToken.trim()); await initialize(); if (!w.error) w.settingsOpen = false }
    catch (error) { w.error = fail(error) }
    finally { w.settingsBusy = false }
  }
  async function loadProjects(reset = true) {
    if (reset) w.projectPage = 1
    const token = ++projectSequence, sequence = scopeSequence, dataset = w.dataset
    w.projectLoading = true; w.projectError = ''
    try {
      const response = await api.projects({ dataset_id: dataset, q: w.projectSearch, purchaser: w.projectPurchaser, sort: w.projectSort, page: w.projectPage, page_size: w.projectPageSize })
      if (token === projectSequence && current(sequence, dataset)) { w.projects = response.data; w.projectTotal = response.total }
    } catch (error) { if (token === projectSequence && current(sequence, dataset)) w.projectError = fail(error) }
    finally { if (token === projectSequence && current(sequence, dataset)) w.projectLoading = false }
  }
  function validateFilters() {
    for (const [low, high] of [['min_price', 'max_price'], ['min_quantity', 'max_quantity'], ['min_total', 'max_total']]) {
      for (const key of [low, high]) if (w.filters[key] !== '' && (!Number.isFinite(Number(w.filters[key])) || Number(w.filters[key]) < 0)) throw new Error('筛选值必须是大于或等于零的数字。')
      if (w.filters[low] !== '' && w.filters[high] !== '' && Number(w.filters[low]) > Number(w.filters[high])) throw new Error('筛选下限不能大于上限。')
    }
  }
  async function loadItems(reset = true) {
    try { validateFilters() } catch (error) { w.itemError = fail(error); return }
    const model = w.filters.model.trim().replace(/^`+|`+$/g, '').trim()
    if (model !== w.filters.model) {
      w.filters.model = model
      notify('已清理型号首尾的空格或反引号。')
    }
    if (reset) w.itemPage = 1
    const token = ++itemSequence, sequence = scopeSequence, dataset = w.dataset
    const filters = { ...w.filters, q: w.itemSearch }
    w.itemLoading = true; w.itemError = ''
    try {
      const response = await api.items({ dataset_id: dataset, ...filters, page: w.itemPage, page_size: w.itemPageSize })
      if (token === itemSequence && current(sequence, dataset)) { w.items = response.data; w.itemTotal = response.total; w.itemApplied = filters }
    } catch (error) { if (token === itemSequence && current(sequence, dataset)) w.itemError = fail(error) }
    finally { if (token === itemSequence && current(sequence, dataset)) w.itemLoading = false }
  }
  async function clearFilters() { w.filters = emptyFilters(); w.itemSearch = ''; await loadItems() }
  async function openProject(id, tab = 'items') {
    const token = ++detailSequence, sequence = scopeSequence, dataset = w.dataset
    w.projectPanelOpen = true; w.projectDetail = null; w.projectDetailError = ''; w.projectDetailLoading = true; w.projectDetailTab = tab
    try {
      const detail = await api.project(id, { dataset_id: dataset })
      if (token === detailSequence && current(sequence, dataset)) w.projectDetail = detail
    } catch (error) { if (token === detailSequence && current(sequence, dataset)) w.projectDetailError = fail(error) }
    finally { if (token === detailSequence && current(sequence, dataset)) w.projectDetailLoading = false }
  }
  function closeProject() { ++detailSequence; w.projectPanelOpen = false }
  function useExample() {
    const example = scenarioExamples[w.scenario]
    if (example.purchaser && w.purchasers.includes(example.purchaser)) w.purchaser = example.purchaser
    if (example.supplier && w.suppliers.includes(example.supplier)) w.supplier = example.supplier
    if (example.suppliers?.every(x => w.suppliers.includes(x))) w.selectedSuppliers = [...example.suppliers]
  }
  async function runScenario() {
    const definition = scenarios.find(s => s.id === w.scenario)
    const parameters = definition.kind === 'purchaser' ? { purchaser: w.purchaser }
      : definition.kind === 'supplier' ? { supplier: w.supplier } : { suppliers: w.selectedSuppliers.join(',') }
    if ((definition.kind === 'multiple' && w.selectedSuppliers.length < 2) || Object.values(parameters).some(v => !v)) return
    const token = ++querySequence, sequence = scopeSequence, dataset = w.dataset, scenario = w.scenario
    w.resultLoading = true; w.resultError = ''; w.result = null; w.renderMs = null; w.renderState = ''; pendingRender = null
    const started = performance.now()
    try {
      const data = await api.analytics(scenario, { dataset_id: dataset, ...parameters })
      if (token !== querySequence || !current(sequence, dataset)) return
      w.result = data; w.resultParameters = parameters
      pendingRender = { token, sequence, dataset, result: w.result, started }
      w.renderState = 'rendering'
    } catch (error) { if (token === querySequence && current(sequence, dataset)) w.resultError = fail(error) }
    finally { if (token === querySequence && current(sequence, dataset)) w.resultLoading = false }
  }
  async function finishScenarioRender(result, failed = false) {
    const pending = pendingRender
    if (!pending || pending.result !== result || pending.token !== querySequence || !current(pending.sequence, pending.dataset)) return
    if (failed) { pendingRender = null; w.renderState = 'error'; return }
    // The network emits only after ECharts finishes. Include the DOM tables and the following paint.
    await nextTick(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    if (pendingRender !== pending || pending.token !== querySequence || !current(pending.sequence, pending.dataset)) return
    w.renderMs = Math.round((performance.now() - pending.started) * 10) / 10
    w.renderState = 'complete'; pendingRender = null
  }
  async function loadJobDocuments(page = w.jobDocumentPage) {
    if (!w.job) return
    const token = ++documentSequence, jobId = w.job.id, sequence = scopeSequence, dataset = w.dataset
    w.jobDocumentPage = page
    try {
      const response = await api.jobDocuments(jobId, page, 20)
      if (token === documentSequence && current(sequence, dataset) && w.job?.id === jobId) { w.jobDocuments = response.data; w.jobDocumentTotal = response.total }
    } catch (error) { if (token === documentSequence && current(sequence, dataset)) w.jobProgressError = fail(error) }
  }
  async function watchJob(id) {
    stopPolling(); const token = jobSequence, sequence = scopeSequence, dataset = w.dataset
    w.jobProgressError = ''; w.jobDocumentPage = 1
    async function tick() {
      if (token !== jobSequence || !current(sequence, dataset)) return
      try {
        const job = await api.job(id)
        if (token !== jobSequence || !current(sequence, dataset)) return
        if (job.dataset_id !== dataset) return
        w.job = job; w.jobProgressError = ''; remember(`procurement.job.${dataset}`, id)
        const known = w.jobHistory.findIndex(entry => entry.id === job.id)
        if (known < 0) w.jobHistory.unshift(job)
        else w.jobHistory[known] = job
        await loadJobDocuments()
        if (job.finished) {
          const wasIngesting = w.ingesting; w.ingesting = false
          if (wasIngesting) await loadDataset()
          return
        }
      } catch (error) {
        if (token === jobSequence && current(sequence, dataset)) w.jobProgressError = fail(error)
      }
      if (token === jobSequence && current(sequence, dataset)) pollTimer = setTimeout(tick, 1500)
    }
    await tick()
  }
  async function startJob(action) {
    stopPolling(); w.error = ''; w.job = null; w.jobDocuments = []; w.jobDocumentTotal = 0; w.ingesting = true
    const sequence = scopeSequence, dataset = w.dataset
    try {
      const job = await action()
      if (current(sequence, dataset)) await watchJob(job.id)
    } catch (error) { if (current(sequence, dataset)) { w.error = fail(error); w.ingesting = false } }
  }
  function captureFiles(files) {
    w.error = ''
    const next = [...files]
    if (next.some(f => f.size > w.health.max_upload_bytes)) { w.error = '有文件超过上传上限，请拆分或使用本地目录接入。'; return }
    w.files = next
  }
  async function upload() {
    if (!w.files.length) return
    const files = [...w.files], mode = w.processingMode, dataset = w.dataset
    await startJob(() => api.ingest(files, dataset, mode))
  }
  async function importLocal() {
    if (!Number.isInteger(Number(w.localLimit)) || w.localLimit < 1 || w.localLimit > 2000) { w.error = '请输入1至2000之间的整数公告数量。'; return }
    const body = { dataset_id: w.dataset, limit: Number(w.localLimit), mode: w.processingMode }
    await startJob(() => api.local(body))
  }
  async function replaySamples() { const dataset = w.dataset; await startJob(() => api.replaySamples(dataset)) }
  async function retryJob() {
    if (!w.job) return
    const id = w.job.id; await startJob(() => api.retry(id))
  }
  async function download(kind = 'standard', jobId = null) {
    if (w.exporting) return
    const parameters = { ...scope(), ...(jobId ? { job_id: jobId } : {}) }
    if (kind === 'detail') parameters.item_view = 'detail'
    w.exporting = true
    try {
      if (kind === 'samples') await api.downloadSamples()
      else if (kind === 'quality') await api.downloadQuality(parameters)
      else await api.download(parameters)
      notify('下载已开始。')
    } catch (error) { w.error = fail(error) }
    finally { w.exporting = false }
  }
  const jobActive = computed(() => w.ingesting || (w.job && !w.job.finished))
  const definition = computed(() => scenarios.find(s => s.id === w.scenario))
  const canQuery = computed(() => !w.resultLoading && !w.datasetLoading && (definition.value.kind === 'multiple'
    ? w.selectedSuppliers.length >= 2 : definition.value.kind === 'purchaser' ? !!w.purchaser : !!w.supplier))
  const canUseExample = computed(() => {
    const e = scenarioExamples[w.scenario]
    return e.purchaser ? w.purchasers.includes(e.purchaser) : e.supplier ? w.suppliers.includes(e.supplier) : e.suppliers.every(s => w.suppliers.includes(s))
  })
  const visibleDatasets = computed(() => w.datasets.filter(dataset => w.showEmptyDatasets || dataset.packages > 0 || dataset.id === w.dataset))
  Object.assign(w, { visibleDatasets, navigate, initialize, loadDataset, applyToken, loadProjects, loadItems, clearFilters, openProject, closeProject, finishScenarioRender,
    useExample, runScenario, captureFiles, upload, importLocal, replaySamples, retryJob, watchJob, loadJobDocuments, download,
    jobActive, canQuery, canUseExample, scenarioDefinition: definition })
  watch(() => w.projectSort, value => {
    remember('procurement.projectSort', value)
    loadProjects()
  })
  watch(() => w.dataset, async value => {
    remember('procurement.dataset', value)
    await loadDataset(true)
    const remembered = stored(`procurement.job.${value}`)
    const job = w.jobHistory.find(j => j.id === remembered) || w.jobHistory[0]
    if (value === w.dataset && job) watchJob(job.id)
  })
  watch(() => [w.scenario, w.purchaser, w.supplier, JSON.stringify(w.selectedSuppliers)], () => {
    ++querySequence; w.resultLoading = false; w.result = null; w.resultError = ''; w.renderMs = null; w.renderState = ''; pendingRender = null
  })
  watch(() => w.page, value => { if (value !== 'relations' && pendingRender) { pendingRender = null; w.renderState = 'interrupted' } })
  const onHash = () => { w.page = pageFromHash(); window.scrollTo({ top: 0, behavior: 'instant' }) }
  onMounted(() => { window.addEventListener('hashchange', onHash); initialize() })
  onBeforeUnmount(() => { stopped = true; ++scopeSequence; stopPolling(); clearTimeout(toastTimer); window.removeEventListener('hashchange', onHash) })
  provide(workspaceKey, w)
  return w
}

export function useWorkspace() {
  const workspace = inject(workspaceKey)
  if (!workspace) throw new Error('Workspace provider is missing')
  return workspace
}
