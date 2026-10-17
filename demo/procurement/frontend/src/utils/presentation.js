export const money = value => value == null ? '未披露' : new Intl.NumberFormat('zh-CN', {
  style: 'currency', currency: 'CNY', maximumFractionDigits: 2
}).format(value)
export const number = value => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value ?? 0)
export const lotLabel = value => String(value || '').split(/[（(]/)[0].trim() || value || '采购包未标明'
export const jsonValue = (value, fallback = []) => {
  if (typeof value !== 'string') return value ?? fallback
  try { return JSON.parse(value) } catch { return fallback }
}
export const statusName = value => ({
  queued: '等待处理', processing: '处理中', complete: '完成', completed: '全部完成',
  completed_with_warnings: '完成，有处理说明', completed_with_errors: '部分处理失败',
  fallback: '规则兜底', partial: '待核对', failed: '失败', interrupted: '处理已中断',
  parsed: '预检通过', cached: '已复用结果'
}[value] || value || '未记录')
export const statusTone = value => ['failed', 'completed_with_errors'].includes(value) ? 'danger'
  : ['partial', 'fallback', 'interrupted', 'completed_with_warnings'].includes(value) ? 'warning'
  : ['processing', 'queued'].includes(value) ? 'active'
  : ['complete', 'completed', 'parsed', 'cached'].includes(value) ? 'success' : 'neutral'
export const modeName = value => ({ 'parse-only': '解析预检', replay: '历史响应回放', extract: '模型抽取' }[value] || value)
export const datasetName = id => ({
  'official-reference': '公告核验样本', official: '官方基准数据', default: '工作数据', demo: '合成演示数据'
}[id] || id)
export const formatBytes = value => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB`
  : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1024))} KB`
export const scenarios = [
  { id: 'purchaser-partners', title: '合作画像', heading: '采购单位与谁合作最多', description: '查看中标供应商和产品品牌的合作次数、金额与时间。', kind: 'purchaser' },
  { id: 'purchaser-bidders', title: '投标活跃度', heading: '谁经常参与这家单位的采购', description: '查看高频投标主体，以及在同一采购包中共同出现的组合。', kind: 'purchaser' },
  { id: 'supplier-competitors', title: '竞争关系', heading: '供应商经常与谁共同竞标', description: '查看共同竞标的高频主体与全部参与方。', kind: 'supplier' },
  { id: 'common-purchasers', title: '共同采购单位', heading: '这些供应商都与哪些单位合作过', description: '采购单位必须分别与每一家选定供应商有中标合作。', kind: 'multiple' },
  { id: 'common-projects', title: '共同竞标项目', heading: '这些供应商共同参与过哪些采购包', description: '全部选定供应商必须在同一采购包中有效参与。', kind: 'multiple' }
]
export const scenarioExamples = {
  'purchaser-partners': { purchaser: '香港城市大学(东莞)' },
  'purchaser-bidders': { purchaser: '香港城市大学(东莞)' },
  'supplier-competitors': { supplier: '中国移动通信集团广东有限公司' },
  'common-purchasers': { suppliers: ['北京纳凯诺科技发展有限公司', '广东写川技术有限公司'] },
  'common-projects': { suppliers: ['中国电信股份有限公司广东分公司', '中国移动通信集团广东有限公司'] }
}
