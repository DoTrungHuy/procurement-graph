const tokenKey = 'procurement.accessToken'
let sessionToken = ''
export const readToken = () => { try { return sessionStorage.getItem(tokenKey) || sessionToken } catch { return sessionToken } }
export const saveToken = value => {
  sessionToken = value
  try { value ? sessionStorage.setItem(tokenKey, value) : sessionStorage.removeItem(tokenKey) } catch { /* Session storage is optional. */ }
}
const url = (path, params = {}) => `/api${path}?${new URLSearchParams(Object.entries(params).filter(([,v]) => v !== '' && v != null))}`
const response = async (path, options = {}) => {
  const headers = new Headers(options.headers)
  if (readToken()) headers.set('Authorization', `Bearer ${readToken()}`)
  const res = await fetch(path, {...options, headers})
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try { const body = await res.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail) } catch (_) {}
    const error = new Error(detail); error.status = res.status; throw error
  }
  return res
}
const request = async (path, params, options) => (await response(url(path, params), options)).json()
export const api = {
  samples: () => request('/samples'),
  replaySamples: dataset_id => request('/samples/replay', {}, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset_id})}),
  downloadSamples: async () => {
    const res=await response(url('/samples/input.zip')); const href=URL.createObjectURL(await res.blob())
    const link=document.createElement('a'); link.href=href; link.download='sample-announcements.zip'; document.body.appendChild(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(href),30000)
  },
  health: () => request('/health'), datasets: () => request('/datasets'),
  modelSettings: () => request('/settings/model'),
  saveModelSettings: body => request('/settings/model', {}, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}),
  stats: params => request('/stats', params), projects: params => request('/projects', params),
  graphOverview: params => request('/graph/overview', params),
  project: (id, params) => request(`/projects/${id}`, params), items: params => request('/items', params),
  entities: (type, params) => request('/entities', {...params, type}), graph: params => request('/graph', params),
  ingest: (files, dataset_id, mode = 'extract') => {
    const body = new FormData(); body.append('dataset_id', dataset_id); body.append('mode', mode)
    files.forEach(file => body.append('files', file))
    return request('/ingest', {}, {method: 'POST', body})
  },
  local: body => request('/ingest/local', {}, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}),
  job: id => request(`/jobs/${id}`), jobs: params => request('/jobs', params),
  jobDocuments: (id, page = 1, page_size = 20) => request(`/jobs/${id}/documents`, {page, page_size}),
  retry: id => request(`/jobs/${id}/retry`, {}, {method: 'POST'}),
  analytics: (scenario, params) => request(`/analytics/${scenario}`, params),
  downloadQuality: async params => {
    const res=await response(url('/export/quality.json', params)); const href=URL.createObjectURL(await res.blob())
    const link=document.createElement('a'); link.href=href; link.download='quality-report.json'; document.body.appendChild(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(href), 30000)
  },
  download: async params => {
    const res = await response(url('/export/standard.xlsx', params)); const blob = await res.blob()
    const href = URL.createObjectURL(blob); const link = document.createElement('a')
    let filename = params?.item_view === 'detail' ? 'procurement-detail-tables.xlsx' : 'procurement-tables.xlsx'
    if (params?.job_id) filename = filename.replace('procurement-', 'procurement-batch-')
    link.href = href; link.download = filename; document.body.appendChild(link)
    link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(href), 30000)
  }
}
