/** Competition UI acceptance against a private copy of the full result DB. No model requests. */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { startBrowserFixture } from './browser_fixture.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const root = process.cwd(), source = path.resolve(process.argv[2] || 'results/competition-platform-20261018/platform.db')
const runtime = path.join(root, 'results/competition-site', new Date().toISOString().replace(/[:.]/g, '-'))
const token = 'isolated-competition-site-token', dataset = 'official-full-20261018'
const digest = async file => createHash('sha256').update(await fs.readFile(file)).digest('hex')
const sourceHash = await digest(source)
const report = { scope: 'Local production UI, full-result DB copy, fake application token; no model HTTP; not official accuracy or remote latency', official_score: false, checks: [], timings: [], source_sha256: sourceHash, consoleErrors: [] }
const fixture = await startBrowserFixture({ runtime, databaseSource: source, production: true, apiPort: 8019, webPort: 5189, apiToken: token })
let browser, page
const headers = { Authorization: 'Bearer ' + token }
async function get(suffix, params = {}) {
  const response = await fetch(fixture.apiOrigin + '/api/' + suffix + '?' + new URLSearchParams({ dataset_id: dataset, ...params }), { headers })
  assert.equal(response.status, 200, suffix)
  return response.json()
}
async function check(name, action) { await action(); report.checks.push({ name, passed: true }); console.log(JSON.stringify({ name, passed: true })) }
async function login(target) {
  await target.goto(fixture.uiOrigin)
  const panel = target.getByRole('dialog', { name: '连接设置', exact: true })
  await panel.getByLabel('服务访问令牌', { exact: true }).fill(token)
  await panel.getByRole('button', { name: '保存并连接', exact: true }).click()
  await panel.waitFor({ state: 'hidden' })
  await target.getByRole('heading', { name: '采购项目 2236', exact: true }).waitFor()
}
const oracleCode = String.raw`
import sqlite3,json,sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
c.row_factory=sqlite3.Row
d='official-full-20261018'; buyer='香港城市大学(东莞)'; supplier='中国移动通信集团广东有限公司'
def rows(sql,args=()): return [dict(r) for r in c.execute(sql,args)]
base=' FROM bids b JOIN projects p ON p.id=b.project_id WHERE p.dataset_id=? AND p.processing_status<>\'fallback\' AND b.eligible=1 '
partners=rows('SELECT b.bidder name,COUNT(DISTINCT p.id) count'+base+'AND b.is_winner=1 AND p.purchaser=? GROUP BY b.bidder',(d,buyer))
bidders=rows('SELECT b.bidder name,COUNT(DISTINCT p.id) count'+base+'AND p.purchaser=? GROUP BY b.bidder',(d,buyer))
competitors=rows('SELECT b.bidder name,COUNT(DISTINCT p.id) count'+base+'AND p.lot_name<>\'\' AND b.bidder<>? AND p.id IN (SELECT project_id FROM bids WHERE bidder=? AND eligible=1) GROUP BY b.bidder',(d,supplier,supplier))
common_buyers=rows('SELECT p.purchaser name,COUNT(DISTINCT p.id) count'+base+'AND b.is_winner=1 AND b.bidder IN (?,?) GROUP BY p.purchaser HAVING COUNT(DISTINCT b.bidder)=2',(d,'北京纳凯诺科技发展有限公司','广东写川技术有限公司'))
common_projects=rows('SELECT p.id'+base+'AND p.lot_name<>\'\' AND b.bidder IN (?,?) GROUP BY p.id HAVING COUNT(DISTINCT b.bidder)=2',(d,'中国电信股份有限公司广东分公司',supplier))
print(json.dumps(dict(partners=partners,bidders=bidders,competitors=competitors,common_buyers=common_buyers,common_projects=common_projects)))
`
try {
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
  page = await context.newPage(); page.on('pageerror', error => report.consoleErrors.push(error.message))
  await check('access screen precedes model settings and avoids unauthenticated model requests', async () => {
    const modelReads = []; page.on('request', request => { if (request.url().includes('/api/settings/model')) modelReads.push(request.url()) })
    await page.goto(fixture.uiOrigin)
    const panel = page.getByRole('dialog', { name: '连接设置', exact: true })
    await panel.waitFor(); assert(await panel.getByRole('button', { name: '模型服务', exact: true }).isDisabled())
    assert.equal(modelReads.length, 0)
    assert(await panel.getByLabel('服务访问令牌', { exact: true }).isVisible())
    const rejected = await fetch(fixture.apiOrigin + '/api/datasets'); assert.equal(rejected.status, 401)
    await login(page)
  })
  await check('fresh login selects named full data and hides empty presets while retaining samples', async () => {
    const select = page.getByRole('combobox', { name: '当前数据集', exact: true })
    assert.equal(await select.inputValue(), dataset)
    assert.match(await select.locator('option:checked').innerText(), /官方全量数据/)
    assert.deepEqual(await select.locator('option').evaluateAll(options => options.map(option => option.value).sort()), [dataset, 'official-reference'].sort())
    const stats = await get('stats'); assert.equal(stats.notices, 1038); assert.equal(stats.projects, 2236); assert.equal(stats.items, 21728)
    report.counts = stats
  })
  await check('all seven field filters work on full data and numeric bounds are enforced', async () => {
    const sample = (await get('items', { q: '遥测心电监护系统', brand: '迈瑞' })).data[0]
    assert.equal(sample.model, 'BeneVision TMS30A'); assert.equal(sample.total_price, 440000)
    const categories = await get('entities', { type: 'category' })
    const probes = { product_name: { q: sample.product_name }, category: { category: categories[0] }, brand: { brand: sample.brand }, model: { model: ' BeneVision  TMS30A ' }, unit_price: { min_price: 440000, max_price: 440000 }, quantity: { min_quantity: 1, max_quantity: 1 }, total_price: { min_total: 440000, max_total: 440000 } }
    report.field_matches = {}
    for (const [field, params] of Object.entries(probes)) {
      const response = await get('items', params); assert(response.total > 0, field)
      report.field_matches[field] = response.total
    }
    const invalid = await fetch(fixture.apiOrigin + '/api/items?' + new URLSearchParams({ dataset_id: dataset, min_price: 20, max_price: 10 }), { headers }); assert.equal(invalid.status, 400)
    await page.getByRole('link', { name: '标的检索', exact: true }).click()
    await page.getByRole('searchbox', { name: '搜索标的', exact: true }).fill('遥测心电监护系统')
    await page.getByRole('button', { name: '检索', exact: true }).click()
    await page.getByText('1–2 / 2 条', { exact: true }).waitFor()
    const table = await page.locator('.items-table').innerText(); assert.match(table, /采购附件/); assert.match(table, /公告正文/)
    await page.getByRole('button', { name: '查看原文', exact: true }).first().click()
    const evidence = page.getByRole('dialog', { name: '标的来源', exact: true }); await evidence.waitFor()
    assert.match(await evidence.innerText(), /BeneVision TMS30A/)
    assert.match(await evidence.innerText(), /包1报价明细表.pdf/)
    await evidence.getByRole('button', { name: '关闭侧栏', exact: true }).click()
  })
  await check('five scenario results agree with independent SQL counts and shared package identities', async () => {
    const oracle = JSON.parse(execFileSync(fixture.python, ['-X', 'utf8', '-c', oracleCode, fixture.database], { encoding: 'utf8' }))
    const map = (rows, name, count) => Object.fromEntries(rows.map(row => [row[name], row[count]]))
    const partner = await get('analytics/purchaser-partners', { purchaser: '香港城市大学(东莞)' })
    assert.deepEqual(map(partner.winning_suppliers, 'supplier', 'cooperation_count'), map(oracle.partners, 'name', 'count'))
    const bidders = await get('analytics/purchaser-bidders', { purchaser: '香港城市大学(东莞)' })
    const frequencies = map(oracle.bidders, 'name', 'count'); assert.equal(bidders.top_bidders.length, Math.min(5, oracle.bidders.length))
    const cutoff = Object.values(frequencies).sort((a, b) => b - a)[Math.min(4, oracle.bidders.length - 1)]
    assert(bidders.top_bidders.every(row => row.frequency === frequencies[row.bidder] && row.frequency >= cutoff))
    const competitors = await get('analytics/supplier-competitors', { supplier: '中国移动通信集团广东有限公司' })
    assert.deepEqual(map(competitors.all_participants, 'bidder', 'frequency'), map(oracle.competitors, 'name', 'count'))
    const buyers = await get('analytics/common-purchasers', { suppliers: '北京纳凯诺科技发展有限公司,广东写川技术有限公司' })
    assert.deepEqual(map(buyers.purchasers, 'purchaser', 'cooperation_count'), map(oracle.common_buyers, 'name', 'count'))
    const projects = await get('analytics/common-projects', { suppliers: '中国电信股份有限公司广东分公司,中国移动通信集团广东有限公司' })
    assert.deepEqual(projects.projects.map(row => row.id).sort((a, b) => a - b), oracle.common_projects.map(row => row.id).sort((a, b) => a - b))
    report.relationship_checks = { partners: partner.winning_suppliers.length, top_bidders: bidders.top_bidders.length, competitors: competitors.all_participants.length, common_purchasers: buyers.purchasers.length, common_projects: projects.projects.length, scope: 'Stored data consistency, not independent source accuracy' }
  })
  await check('five queries include completed graph rendering in their displayed timings', async () => {
    await page.getByRole('link', { name: '关系分析', exact: true }).click()
    for (const name of ['合作画像', '投标活跃度', '竞争关系', '共同采购单位', '共同竞标项目']) {
      await page.getByRole('button', { name, exact: true }).click()
      await page.getByRole('button', { name: '填入样本示例', exact: true }).click()
      await page.getByRole('button', { name: '查询关系', exact: true }).click()
      const result = page.locator('[data-testid="scenario-result"][data-render-state="complete"]'); await result.waitFor({ timeout: 20000 })
      assert(await result.locator('canvas').first().isVisible()); assert.equal(await result.getByText('正在绘制关系网络', { exact: true }).count(), 0)
      const ms = Number(await result.getAttribute('data-render-ms')); assert(ms > 0)
      report.timings.push({ scenario: name, full_render_ms: ms })
    }
    report.local_average_ms = report.timings.reduce((sum, row) => sum + row.full_render_ms, 0) / 5
    await page.screenshot({ path: path.join(runtime, 'full-data-relations.png') })
  })
  await check('full-data workbook download retains the official schema and all three row counts', async () => {
    const started = performance.now()
    const pending = page.waitForEvent('download', { timeout: 60000 }); await page.getByRole('button', { name: /导出三表/ }).click()
    const file = await pending; assert.equal(await file.failure(), null)
    const target = path.join(runtime, 'procurement-tables.xlsx'); await file.saveAs(target)
    report.export_ms = Math.round(performance.now() - started)
    const code = "import sys,json;from openpyxl import load_workbook;w=load_workbook(sys.argv[1],read_only=True);print(json.dumps([s.max_row-1 for s in w]));w.close()"
    assert.deepEqual(JSON.parse(execFileSync(fixture.python, ['-c', code, target], { encoding: 'utf8' })), [2236, 21728, 6542])
    const validation = "import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[2]+'/demo/procurement/backend');from app.acceptance import check_workbook;print(json.dumps(check_workbook(Path(sys.argv[1]).read_bytes(),Path(sys.argv[2]+'/data/official-benchmark/indexes/output-schema.json'))))"
    report.workbook_acceptance = JSON.parse(execFileSync(fixture.python, ['-c', validation, target, root], { encoding: 'utf8' }))
    report.submission_ready = report.workbook_acceptance.valid
    report.export_bytes = (await fs.stat(target)).size
  })
  await check('switches and mobile access settings remain usable with full data', async () => {
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('official-reference')
    await page.reload(); assert.equal(await page.getByRole('combobox', { name: '当前数据集', exact: true }).inputValue(), 'official-reference')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: '连接设置', exact: true }).click()
    const panel = page.getByRole('dialog', { name: '连接设置', exact: true }); await panel.waitFor()
    assert(await panel.getByLabel('服务访问令牌', { exact: true }).isVisible())
    assert(await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1))
    await page.screenshot({ path: path.join(runtime, 'mobile-access.png'), animations: 'disabled' })
    await panel.getByRole('button', { name: '关闭侧栏', exact: true }).click()
  })
  assert.deepEqual(report.consoleErrors, []); assert.equal(await digest(source), sourceHash)
  report.passed = true
} catch (error) {
  report.passed = false; report.error = error.stack; console.error(error)
  await page?.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {})
} finally {
  await browser?.close(); await fixture.stop()
  report.finished = new Date().toISOString(); report.source_unchanged = await digest(source) === sourceHash
  await fs.writeFile(path.join(runtime, 'verification.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, runtime: path.relative(root, runtime) }))
}
if (!report.passed) process.exitCode = 1
