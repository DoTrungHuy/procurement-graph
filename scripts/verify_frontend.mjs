/** Browser regression using fresh bundled fixtures. Model HTTP is forbidden. */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { startBrowserFixture } from './browser_fixture.mjs'
import assert from 'node:assert/strict'
const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const root = process.cwd(), frontend = path.join(root, 'demo/procurement/frontend')
const output = path.join(root, 'results/ui-checks/latest'), runtime = path.join(root, 'results/ui-checks', new Date().toISOString().replace(/[:.]/g, '-'))
await fs.mkdir(output, { recursive: true }); await fs.mkdir(runtime, { recursive: true })
const production = process.argv.includes('--production')
const report = { started: new Date().toISOString(), buildMode: production ? 'production-preview' : 'development', scope: 'Local Chromium UI verification; isolated database; no official score claim', checks: [], screenshots: [], consoleErrors: [], externalRequests: [], expectedFailures: [], downloads: [] }
const fixture = await startBrowserFixture({ runtime, production })
const { apiOrigin, uiOrigin, python } = fixture
report.fixtures = { database: 'fresh', officialInput: 'bundled 20 notices', syntheticDemoPackages: 6 }
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
async function ready(url) { for (let i = 0; i < 100; ++i) { try { if ((await fetch(url)).ok) return } catch {} await pause(200) } throw new Error('Local service did not start: ' + url) }
const json = async suffix => { const response = await fetch(apiOrigin + '/api/' + suffix); if (!response.ok) throw new Error(`API ${response.status}: ${suffix}`); return response.json() }
let browser, context, page
async function check(name, action) { await action(); report.checks.push({ name, passed: true }); console.log(JSON.stringify({ check: name, passed: true })) }
async function navigate(view, title) { await page.locator(`.primary-nav a[href="#/${view}"]`).click(); await page.getByRole('heading', { name: title, exact: true, level: 1 }).waitFor(); await page.evaluate(() => window.scrollTo(0, 0)) }
async function screenshot(name, options = {}) {
  await page.waitForFunction(() => !document.querySelector('.table-scroll[aria-busy=true]') && !document.querySelector('.export-button')?.disabled)
  await page.locator('.toast').waitFor({ state: 'hidden', timeout: 6000 })
  await page.evaluate(() => window.scrollTo(0, 0))
  const file = name + '.png'; await page.screenshot({ path: path.join(output, file), animations: 'disabled', ...options }); report.screenshots.push(file)
}
async function noOverflow(label) { const values = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth })); assert(values.document <= values.width + 1 && values.body <= values.width + 1, `${label}: ${JSON.stringify(values)}`) }
async function download(button, name) { const pending = page.waitForEvent('download'); await button.click(); const file = await pending; assert.equal(await file.failure(), null); if (name.endsWith('.xlsx')) assert.equal(file.suggestedFilename(), name); await file.saveAs(path.join(runtime, name)); const bytes = (await fs.stat(path.join(runtime, name))).size; assert(bytes > 100); report.downloads.push({ name, bytes }); return path.join(runtime, name) }
async function waitNewJob(action, expected) {
  const response = page.waitForResponse(r => r.request().method() === 'POST' && (r.url().includes('/api/ingest') || r.url().includes('/api/samples/replay') || r.url().includes('/retry')))
  await action(); const created = await (await response).json(); let job
  for (let attempt = 0; attempt < 120; ++attempt) { job = await json('jobs/' + created.id); if (job.finished) break; await pause(250) }
  assert(job?.finished, 'Job did not finish'); assert.equal(job.mode, expected); assert.equal(job.metrics.model_calls || 0, 0)
  await page.getByRole('button', { name: '重新处理此批', exact: true }).waitFor({ state: 'visible' })
  await page.waitForFunction(id => document.querySelector('.job-heading')?.textContent.includes(id) && !document.querySelector('.job-surface select')?.disabled, job.id)
  assert((await page.getByRole('combobox', { name: '查看历史任务', exact: true }).evaluate(element => element.selectedOptions[0]?.textContent || '')).includes(job.id.slice(0, 8)))
  return job
}
try {
  await Promise.all([ready(apiOrigin + '/api/health'), ready(uiOrigin)])
  report.health = await json('health'); assert.equal(report.health.model_network_enabled, false); assert.equal(report.health.deepseek_configured, false)
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1, acceptDownloads: true })
  await context.route('**/*', route => { const address = new URL(route.request().url()); if (!['localhost', '127.0.0.1'].includes(address.hostname)) { report.externalRequests.push(address.origin); return route.abort() } return route.continue() })
  page = await context.newPage(); page.on('pageerror', error => report.consoleErrors.push(error.message))
  await page.goto(uiOrigin); await page.getByRole('heading', { name: '采购项目 26', exact: true }).waitFor()
  await check('overview: 20 source notices, 26 packages, 138 items; no initial graph download', async () => {
    assert.match(await page.locator('.stat-strip').innerText(), /20/); assert.equal(await page.locator('.project-table tbody tr').count(), 10)
    assert(!await page.evaluate(() => performance.getEntriesByType('resource').some(r => /\/src\/graph\.js|echarts/.test(r.name))))
    await noOverflow('desktop overview'); await screenshot('overview-desktop')
  })
  await check('overview relationship preview uses real packages and supports keyboard navigation', async () => {
    await page.locator('.map-node.project').first().waitFor()
    const graph = await json('graph?dataset_id=official-reference&limit=3')
    assert.equal(await page.locator('.map-node.project').count(), graph.nodes.filter(node => node.type === 'project').length)
    const node = page.locator('.map-node.project').first(), label = await node.getAttribute('aria-label')
    await node.focus(); await page.keyboard.press('Enter')
    const panel = page.getByRole('dialog', { name: '采购包详情', exact: true }); await panel.locator('.detail-title').waitFor()
    assert(label.includes(await panel.locator('.detail-title').innerText())); await page.keyboard.press('Escape')
  })
  await check('empty dataset has a usable import entry', async () => {
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('official'); await page.getByRole('heading', { name: '这个数据集还没有项目', exact: true }).waitFor(); assert(await page.getByRole('button', { name: '前往数据接入', exact: true }).isVisible())
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('official-reference'); await page.getByRole('heading', { name: '采购项目 26', exact: true }).waitFor()
  })
  await check('project search, pagination and source evidence drawer', async () => {
    const first = await page.locator('.project-link').first().innerText()
    await page.getByRole('button', { name: '项目下一页', exact: true }).click(); await page.getByText('11–20 / 26 条', { exact: true }).waitFor(); await page.locator('.table-scroll[aria-busy="false"]').waitFor(); assert.notEqual(await page.locator('.project-link').first().innerText(), first)
    await page.getByRole('searchbox', { name: '搜索项目', exact: true }).fill('肇庆学院'); await page.getByRole('button', { name: '查询', exact: true }).click(); await page.getByText('1–1 / 1 条', { exact: true }).waitFor()
    await page.locator('.project-link').first().click(); const project = page.getByRole('dialog', { name: '采购包详情', exact: true }); await project.locator('.detail-title').waitFor()
    assert.match(await project.locator('.detail-fields').innerText(), /广东南方通信建设有限公司/)
    await project.getByRole('button', { name: '竞标结果', exact: false }).click(); assert.equal(await project.locator('tbody tr').count(), 20); await screenshot('project-bids-desktop')
    await project.getByRole('button', { name: '标的明细', exact: false }).click(); await project.getByRole('button', { name: '来源', exact: true }).first().click()
    const source = page.getByRole('dialog', { name: '标的来源', exact: true }); await source.locator('.source-quote').waitFor(); assert((await source.locator('.source-quote').innerText()).length > 30)
    await page.keyboard.press('Escape'); await source.waitFor({ state: 'hidden' }); assert(await project.isVisible()); await page.keyboard.press('Escape'); await project.waitFor({ state: 'hidden' })
    assert.equal(await page.evaluate(() => document.activeElement.className), 'project-link')
  })
  const records = await json('items?dataset_id=official-reference&page_size=200')
  const item = records.data.find(row => row.category && row.brand && row.model && row.unit_price > 0 && row.quantity > 0 && row.total_price > 0)
  assert(item, 'Need an actual fully specified item fixture')
  await check('seven-field filters, price/quantity ranges, reset, pagination, evidence', async () => {
    await navigate('items', '标的检索'); await page.getByRole('searchbox', { name: '搜索标的', exact: true }).fill(item.product_name)
    await page.getByLabel('品目名称', { exact: true }).selectOption(item.category); await page.getByLabel('品牌', { exact: true }).selectOption(item.brand); await page.getByLabel('规格型号', { exact: true }).fill(item.model)
    await page.getByRole('button', { name: '金额与数量筛选', exact: false }).click()
    for (const [label, value] of [['单价（元）', item.unit_price], ['数量', item.quantity], ['总价（元）', item.total_price]]) for (const edge of ['下限', '上限']) await page.getByRole('spinbutton', { name: label + edge, exact: true }).fill(String(value))
    await page.getByRole('button', { name: '检索', exact: true }).click(); await page.waitForFunction(() => document.querySelector('.result-summary')?.textContent.includes('10 项条件已应用'))
    assert((await page.locator('.items-table tbody tr').count()) >= 1); await page.getByRole('button', { name: '查看原文', exact: true }).first().click(); await page.getByRole('dialog', { name: '标的来源', exact: true }).locator('.source-quote').waitFor(); const evidence = page.getByRole('dialog', { name: '标的来源', exact: true })
    assert.equal(await evidence.locator('.evidence-fields button').count(), 7)
    assert.equal(await evidence.locator('mark').first().innerText(), item.model)
    await evidence.locator('.evidence-fields button').filter({ has: page.getByText('品牌', { exact: true }) }).click()
    assert.equal(await evidence.locator('mark').first().innerText(), item.brand)
    await evidence.locator('.evidence-fields button').filter({ has: page.getByText('规格型号', { exact: true }) }).click()
    await screenshot('source-evidence-desktop'); await page.keyboard.press('Escape')
    await page.getByRole('spinbutton', { name: '单价（元）下限', exact: true }).fill('999999999'); await page.getByRole('button', { name: '检索', exact: true }).click(); await page.getByRole('alert').filter({ hasText: '筛选下限不能大于上限' }).waitFor()
    await page.getByRole('button', { name: '重置', exact: true }).click(); await page.getByText('1–20 / 138 条', { exact: true }).waitFor(); await page.getByRole('button', { name: '标的下一页', exact: true }).click(); await page.getByText('21–40 / 138 条', { exact: true }).waitFor()
    await page.getByRole('combobox', { name: '标的每页条数', exact: true }).selectOption('50'); await page.getByText('1–50 / 138 条', { exact: true }).waitFor(); await screenshot('items-desktop')
    await page.getByRole('searchbox', { name: '搜索标的', exact: true }).fill('nonexistent-product-20261016'); await page.getByRole('button', { name: '检索', exact: true }).click(); await page.getByRole('heading', { name: '没有匹配的标的', exact: true }).waitFor(); await page.getByRole('button', { name: '清除筛选', exact: true }).click(); await page.getByText('1–50 / 138 条', { exact: true }).waitFor()
    for (const raw of ['`MES3200', '`MES3200`', '  MES3200  ']) {
      await page.getByLabel('规格型号', { exact: true }).fill(raw)
      const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/items' && new URL(response.url()).searchParams.get('model') === 'MES3200')
      await page.getByRole('button', { name: '检索', exact: true }).click()
      assert.equal((await (await pending).json()).total, 1)
      await page.getByText('1–1 / 1 条', { exact: true }).waitFor()
      assert.equal(await page.getByLabel('规格型号', { exact: true }).inputValue(), 'MES3200')
      await page.getByText('智慧工厂管理系统', { exact: true }).waitFor()
    }
    await page.getByLabel('规格型号', { exact: true }).fill('MES32'); await page.getByRole('button', { name: '检索', exact: true }).click()
    await page.getByRole('heading', { name: '没有匹配的标的', exact: true }).waitFor()
    assert.match(await page.locator('.empty-state').innerText(), /公告核验样本.*型号需完整匹配/)
    await page.getByRole('button', { name: '重置', exact: true }).click(); await page.getByText('1–50 / 138 条', { exact: true }).waitFor()
    await page.getByRole('searchbox', { name: '搜索标的', exact: true }).fill('广东南方通信建设有限公司'); await page.getByRole('button', { name: '检索', exact: true }).click()
    await page.locator('.table-scroll[aria-busy="false"]').waitFor(); assert(await page.locator('.items-table tbody tr').count() > 0)
    await page.getByRole('button', { name: '重置', exact: true }).click(); await page.getByText('1–50 / 138 条', { exact: true }).waitFor()
  })
  await navigate('relations', '关系分析')
  const scenarios = ['purchaser-partners', 'purchaser-bidders', 'supplier-competitors', 'common-purchasers', 'common-projects']
  for (const scenario of scenarios) await check('live scenario: ' + scenario, async () => {
    await page.locator(`[data-scenario-tab="${scenario}"]`).click(); await page.getByRole('button', { name: '填入样本示例', exact: true }).click(); await page.getByRole('button', { name: '查询关系', exact: true }).click()
    const result = page.locator(`[data-testid="scenario-result"][data-scenario="${scenario}"]`); await result.waitFor(); await page.waitForFunction(() => document.querySelector('[data-testid=scenario-result]')?.getAttribute('data-render-ms') != null)
    assert(await result.locator('tbody tr').count() > 0); assert(Number(await result.getAttribute('data-render-ms')) < 10000)
    assert.equal(await result.locator('.analysis-summary > div').count(), 3)
    await result.getByRole('button', { name: '图表', exact: true }).click(); assert(await result.locator('.bar-row').count() > 0)
    if (scenario !== 'purchaser-bidders') { await result.getByRole('combobox', { name: '图表比较指标', exact: true }).selectOption('amount'); assert((await result.locator('.bar-number').first().innerText()).length > 0) }
    await result.getByRole('button', { name: '表格', exact: true }).click()
    if (scenario === 'purchaser-partners') { await screenshot('relations-desktop'); await result.getByRole('button', { name: '产品品牌', exact: true }).click(); assert(await result.locator('tbody tr').count() > 0) }
    if (scenario === 'purchaser-bidders') { await result.getByRole('button', { name: '共同投标组合', exact: true }).click(); assert(await result.locator('tbody tr').count() > 0) }
    if (scenario === 'supplier-competitors') { await result.getByRole('button', { name: '全部参与方', exact: true }).click(); const params = new URLSearchParams({ dataset_id: 'official-reference', supplier: '中国移动通信集团广东有限公司' }); const expected = await json('analytics/supplier-competitors?' + params); assert.equal(await result.locator('tbody tr').count(), Math.min(15, expected.all_participants.length)); report.allParticipants = expected.all_participants.length; if (expected.all_participants.length > 15) { await result.getByRole('button', { name: '关系结果下一页', exact: true }).click(); assert.equal(await result.locator('tbody tr').count(), Math.min(15, expected.all_participants.length - 15)) } }
    if (scenario === 'common-purchasers') { await result.locator('summary').first().click(); assert(await result.locator('.supplier-details dd').count() >= 2) }
    if (scenario === 'common-projects') {
      const expected = await json('analytics/common-projects?' + new URLSearchParams({ dataset_id: 'official-reference', suppliers: '中国电信股份有限公司广东分公司,中国移动通信集团广东有限公司' }))
      assert((await result.locator('.analysis-summary').innerText()).includes(new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', maximumFractionDigits: 2 }).format(expected.summary.total_amount)))
      const expansion = result.locator('.bid-expansion').first()
      assert.equal(await expansion.getAttribute('open'), null)
      await expansion.locator('summary').click(); assert(await expansion.locator('li').count() > 1)
      await expansion.locator('summary').click()
    }
    if (scenario === 'common-projects') { await result.getByRole('button', { name: '查看竞标详情', exact: true }).first().click(); const panel = page.getByRole('dialog', { name: '采购包详情', exact: true }); await panel.locator('tbody tr').first().waitFor(); assert.match(await panel.innerText(), /未中标|中标/); await page.keyboard.press('Escape'); await screenshot('common-projects-desktop') }
  })
  await check('supplier search picker, keyboard close and input invalidation', async () => {
    await page.getByRole('button', { name: '移除中国移动通信集团广东有限公司', exact: true }).click(); assert(await page.getByRole('button', { name: '查询关系', exact: true }).isDisabled()); assert.equal(await page.locator('[data-testid=scenario-result]').count(), 0)
    await page.locator('.select-trigger').click(); await page.getByRole('searchbox', { name: '搜索供应商', exact: true }).fill('中国移动'); const checkbox = page.getByRole('checkbox', { name: '中国移动通信集团广东有限公司', exact: true }); await checkbox.check(); await page.keyboard.press('Escape'); assert.equal(await page.locator('.picker-popover').count(), 0); assert.equal(await page.locator('.select-trigger').getAttribute('aria-expanded'), 'false'); assert(await page.getByRole('button', { name: '查询关系', exact: true }).isEnabled())
  })
  await check('optional graph loads, resizes and closes without page errors', async () => {
    await page.getByRole('button', { name: '查看关系网络', exact: true }).click(); await page.locator('.network-canvas canvas').waitFor(); assert.match(await page.getByTestId('network-summary').innerText(), /个主体/)
    await page.getByRole('combobox', { name: '关系网络采购包数', exact: true }).selectOption('16'); await page.locator('.network-canvas canvas').waitFor(); await screenshot('network-desktop', { fullPage: true }); await page.getByRole('button', { name: '收起关系网络', exact: true }).click(); assert.equal(await page.locator('.network-canvas').count(), 0)
  })
  await check('standard workbook and quality downloads', async () => {
    await download(page.getByRole('button', { name: '导出三表', exact: false }), 'procurement-tables.xlsx'); await download(page.getByRole('button', { name: '处理说明', exact: true }), 'quality-report.json')
  })
  await navigate('import', '数据接入')
  await check('model option stays disabled; sample download and local-only upload parsing', async () => {
    assert.equal(await page.locator('option[value=extract]').evaluate(option => option.disabled), true); await page.getByRole('button', { name: '核验样本', exact: true }).click(); const sampleZip = await download(page.getByRole('button', { name: '下载样本公告', exact: true }), 'sample-announcements.zip')
    await page.getByRole('button', { name: '上传文件', exact: true }).click(); assert(await page.getByRole('button', { name: '开始处理', exact: true }).isDisabled()); await page.getByLabel('选择公告与附件', { exact: true }).setInputFiles(sampleZip)
    await page.getByRole('button', { name: '开始处理', exact: true }).hover(); assert.equal(await page.getByRole('button', { name: '开始处理', exact: true }).evaluate(element => getComputedStyle(element).color), 'rgb(255, 255, 255)')
    report.parseJob = await waitNewJob(() => page.getByRole('button', { name: '开始处理', exact: true }).click(), 'parse-only'); assert.equal(report.parseJob.total, 20); assert.equal(report.parseJob.failed, 0); await screenshot('import-desktop')
  })
  await check('old-rule cache replay and retry fail explicitly without overwriting source data', async () => {
    const before = await json('stats?dataset_id=official-reference')
    await page.getByLabel('处理方式', { exact: true }).selectOption('replay'); report.replayJob = await waitNewJob(() => page.getByRole('button', { name: '开始处理', exact: true }).click(), 'replay'); assert.equal(report.replayJob.total, 20); assert.equal(report.replayJob.failed, 20)
    assert.equal(report.replayJob.metrics.response_cache_hits, 0); assert(report.replayJob.errors.every(error => error.includes('未命中当前规则')))
    report.retryJob = await waitNewJob(() => page.getByRole('button', { name: '重新处理此批', exact: true }).click(), 'replay'); assert.equal(report.retryJob.failed, 20)
    assert.deepEqual(await json('stats?dataset_id=official-reference'), before)
    report.expectedFailures.push('Old-rule response caches rejected in replay and retry; zero model HTTP calls')
    await download(page.getByRole('button', { name: '此批处理说明', exact: true }), 'batch-quality.json')
  })
  await check('bundled old-cache replay requests new extraction; local directory parse stays available', async () => {
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('official'); await page.getByRole('button', { name: '核验样本', exact: true }).click(); report.sampleJob = await waitNewJob(() => page.getByRole('button', { name: '回放核验样本', exact: true }).click(), 'replay'); assert.equal(report.sampleJob.failed, 20); assert.equal(report.sampleJob.metrics.response_cache_hits, 0); assert.equal(report.sampleJob.skipped, 0)
    assert(report.sampleJob.errors.every(error => error.includes('未命中当前规则'))); assert.equal((await json('stats?dataset_id=official')).projects, 0)
    await page.getByRole('button', { name: '本地基准数据', exact: true }).click(); await page.getByLabel('处理方式', { exact: true }).selectOption('parse-only'); await page.getByLabel('本次处理公告数', { exact: true }).fill('1'); report.localJob = await waitNewJob(() => page.getByRole('button', { name: '开始处理', exact: true }).click(), 'parse-only'); assert.equal(report.localJob.total, 1)
  })
  await check('dataset switch clears results and restores correct dataset', async () => {
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('demo'); await navigate('overview', '工作台'); await page.getByRole('heading', { name: '采购项目 6', exact: true }).waitFor()
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('official-reference'); await page.getByRole('heading', { name: '采购项目 26', exact: true }).waitFor(); await page.getByRole('searchbox', { name: '搜索项目', exact: true }).fill(''); await page.getByRole('button', { name: '查询', exact: true }).click(); await page.getByText('1–10 / 26 条', { exact: true }).waitFor()
  })
  await check('responsive desktop/tablet/mobile, all four pages have no document overflow', async () => {
    for (const [width, height, label] of [[1920, 1080, 'large'], [1024, 900, 'tablet'], [390, 844, 'mobile']]) {
      await page.setViewportSize({ width, height })
      for (const [view, title] of [['overview', '工作台'], ['items', '标的检索'], ['import', '数据接入'], ['relations', '关系分析']]) {
        await navigate(view, title); await noOverflow(`${label}-${view}`)
        if (label === 'mobile' && view === 'overview') {
          const [labelBox, valueBox, noteBox] = await Promise.all(['span', 'strong', 'small'].map(tag => page.locator('.stat-amount > ' + tag).boundingBox()))
          assert(labelBox.y + labelBox.height <= valueBox.y && valueBox.y + valueBox.height <= noteBox.y, 'Mobile amount label, value and note must not overlap')
        }
        if (label === 'mobile' || (label === 'tablet' && view === 'overview') || (label === 'large' && view === 'overview')) await screenshot(`${view}-${label}`)
      }
      if (label === 'mobile') { await page.locator('[data-scenario-tab=common-projects]').click(); await page.getByRole('button', { name: '填入样本示例', exact: true }).click(); await page.locator('.select-trigger').click(); await noOverflow('mobile picker'); await screenshot('supplier-picker-mobile'); await page.keyboard.press('Escape') }
    }
  })
  await check('settings accessible and error recovery preserves data', async () => {
    await page.getByRole('button', { name: '连接设置', exact: true }).click(); const panel = page.getByRole('dialog', { name: '连接设置', exact: true }); await panel.waitFor(); assert.match(await panel.innerText(), /模型调用已暂停/); await panel.getByRole('button', { name: '保存并连接', exact: true }).click(); await panel.waitFor({ state: 'hidden' })
    await navigate('items', '标的检索'); let injected = false
    await page.route('**/api/items?**', async route => { if (!injected) { injected = true; return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Verification: temporary search error"}' }) } return route.continue() })
    await page.getByRole('button', { name: '检索', exact: true }).click(); await page.getByRole('alert').filter({ hasText: 'temporary search error' }).waitFor(); report.expectedFailures.push('One injected local 503 search error'); await page.getByRole('button', { name: '重试', exact: true }).click(); await page.waitForFunction(() => !document.querySelector('.filter-form')?.parentElement.querySelector('[role=alert]')); assert(await page.locator('.items-table tbody tr').count() > 0)
  })
  assert.deepEqual(report.consoleErrors, []); assert.deepEqual(report.externalRequests, [])
  report.healthAfter = await json('health'); assert.equal(report.healthAfter.model_network_enabled, false)
  report.passed = true; report.finished = new Date().toISOString(); report.runtime = path.relative(root, runtime).replaceAll('\\', '/')
} catch (error) {
  report.passed = false; report.error = error.stack; if (page) await page.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {}); console.error(error)
} finally {
  await context?.close(); await browser?.close(); await fixture.stop()
  await fs.writeFile(path.join(runtime, 'browser-verification.json'), JSON.stringify(report, null, 2))
  if (report.passed) await fs.writeFile(path.join(output, 'browser-verification.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.join(runtime, 'browser-verification.json') }))
}
if (!report.passed) process.exitCode = 1
