/** Browser + HTTP acceptance on an isolated copy. External model HTTP is disabled by the fixture. */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { startBrowserFixture } from './browser_fixture.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const root = process.cwd(), source = path.resolve(process.argv[2]), runtime = path.resolve(process.argv[3])
const hash = async file => createHash('sha256').update(await fs.readFile(file)).digest('hex')
const before = await hash(source), token = 'quality-browser-fixture-only', dataset = 'official-full-20261018'
const report = { checks: [], consoleErrors: [], model_requests: 0, official_score: false,
  scope: 'Local production UI and HTTP on a private corrected database copy; no public deployment or official accuracy score.' }
const fixture = await startBrowserFixture({ runtime, databaseSource: source, production: true, apiPort: 8029, webPort: 5199, apiToken: token })
let browser, page
const headers = { Authorization: 'Bearer ' + token }
async function get(endpoint, params = {}) {
  const response = await fetch(fixture.apiOrigin + '/api/' + endpoint + '?' + new URLSearchParams({ dataset_id: dataset, ...params }), { headers })
  assert.equal(response.status, 200, endpoint)
  return response.json()
}
async function check(name, work) { await work(); report.checks.push({ name, passed: true }); console.log(JSON.stringify({ name, passed: true })) }
async function waitForNetwork() {
  await page.mouse.move(0, 0)
  await page.waitForFunction(() => [...document.querySelectorAll('.network-canvas canvas')].some(canvas => {
    const rgba = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
    let painted = 0
    for (let i = 3; i < rgba.length; i += 4) if (rgba[i] && ++painted > 100) return true
    return false
  }), null, { timeout: 15000 })
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const paintedShapes = await page.waitForFunction(() => {
    const canvases = [...document.querySelectorAll('.network-canvas canvas')]
    const colors = [[91, 98, 201], [170, 135, 82]]
    const shapes = colors.map(color => {
      let left = Infinity, top = Infinity, right = -1, bottom = -1
      for (const canvas of canvases) {
        const rgba = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
        for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
          const i = (y * canvas.width + x) * 4
          if (rgba[i + 3] > 240 && color.every((value, channel) => Math.abs(rgba[i + channel] - value) < 3)) {
            left = Math.min(left, x); top = Math.min(top, y); right = Math.max(right, x); bottom = Math.max(bottom, y)
          }
        }
      }
      return { width: right - left + 1, height: bottom - top + 1 }
    })
    return shapes.every(shape => shape.width > 8 && shape.height > 8 && shape.height < 90 &&
      Math.abs(shape.width / shape.height - 1) < .2) ? shapes : false
  }, null, { timeout: 10000 })
  report.network_shapes ??= []
  report.network_shapes.push(await paintedShapes.jsonValue())
}
const oracle = String.raw`
import sqlite3,json,sys
from contextlib import closing
with closing(sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)) as c:
 c.row_factory=sqlite3.Row;d='official-full-20261018'
 buyer=c.execute("SELECT p.purchaser FROM projects p JOIN items i ON i.project_id=p.id WHERE p.dataset_id=? AND i.aggregation_eligible=0 AND i.brand<>'' GROUP BY p.purchaser ORDER BY (SELECT COUNT(DISTINCT j.brand) FROM items j JOIN projects q ON q.id=j.project_id WHERE q.dataset_id=p.dataset_id AND q.purchaser=p.purchaser) LIMIT 1",(d,)).fetchone()[0]
 ids=c.execute('SELECT COUNT(*) FROM bids b JOIN projects p ON p.id=b.project_id WHERE p.dataset_id=? AND b.identity_confirmed=0',(d,)).fetchone()[0]
 print(json.dumps({'buyer':buyer,'anonymous_rows':ids},ensure_ascii=False))
`
try {
  const expected = JSON.parse(execFileSync(fixture.python, ['-X', 'utf8', '-c', oracle, fixture.database], { encoding: 'utf8' }))
  await check('five relationship HTTP APIs and corrected single-package award', async () => {
    const partner = await get('analytics/purchaser-partners', { purchaser: '武城县第二中学' })
    const supplier = partner.winning_suppliers.find(row => row.supplier === '山东扬天信息科技有限公司')
    assert.equal(supplier.cooperation_count, 1); assert.equal(supplier.total_amount, 1443000)
    await get('analytics/purchaser-bidders', { purchaser: '武城县第二中学' })
    await get('analytics/supplier-competitors', { supplier: '山东扬天信息科技有限公司' })
    await get('analytics/common-purchasers', { suppliers: '共道网络科技有限公司,北京瑞和云图科技有限公司' })
    const projects = await get('analytics/common-projects', { suppliers: '山东扬天信息科技有限公司,德州万创电子科技有限公司' })
    assert.equal(projects.projects.filter(row => row.notice_uuid === 't20260812_27123559').length, 1)
    const separate = await get('analytics/common-projects', { suppliers: '共道网络科技有限公司,北京瑞和云图科技有限公司' })
    assert(!separate.projects.some(row => row.notice_uuid === 't20260408_26372711'))
  })
  await check('anonymous score IDs stay out of named supplier inventories', async () => {
    assert(expected.anonymous_rows > 0)
    const entities = await get('entities', { type: 'supplier' })
    assert(!entities.some(name => name.startsWith('GC-')))
    const overview = await get('graph/overview')
    assert(!overview.nodes.some(node => node.type === 'supplier' && node.name.startsWith('GC-')))
    report.anonymous_rows = expected.anonymous_rows
  })
  await check('uncertain source prices do not become a numeric brand total', async () => {
    const data = await get('analytics/purchaser-partners', { purchaser: expected.buyer })
    const pending = data.product_suppliers.filter(row => row.amount_status === 'pending_review')
    assert(pending.length > 0 && pending.every(row => row.total_amount === null && row.unconfirmed_amount_item_count > 0))
    report.pending_buyer = expected.buyer
  })
  browser = await chromium.launch({ headless: true, channel: 'msedge', args: ['--no-proxy-server'] })
  page = await browser.newPage({ viewport: { width: 1440, height: 1040 } })
  page.on('pageerror', error => report.consoleErrors.push(error.message))
  await check('production UI explains pending amounts in both table and chart', async () => {
    await page.goto(fixture.uiOrigin)
    const panel = page.getByRole('dialog', { name: '连接设置', exact: true })
    await panel.getByLabel('服务访问令牌', { exact: true }).fill(token)
    await panel.getByRole('button', { name: '保存并连接', exact: true }).click()
    await panel.waitFor({ state: 'hidden' })
    await page.getByRole('link', { name: '关系分析', exact: true }).click()
    await page.getByRole('button', { name: '合作画像', exact: true }).click()
    await page.getByRole('combobox', { name: '采购单位', exact: true }).selectOption(expected.buyer)
    await page.getByRole('button', { name: '查询关系', exact: true }).click()
    await page.locator('[data-testid="scenario-result"][data-render-state="complete"]').waitFor({ timeout: 30000 })
    await page.getByRole('button', { name: '产品品牌', exact: true }).click()
    await page.getByText('部分品牌的跨来源明细尚待核验，金额暂不汇总；原文金额仍保留供核对。', { exact: true }).waitFor()
    assert.match(await page.locator('.relation-table').innerText(), /待核对/)
    await page.getByRole('button', { name: '已披露金额', exact: true }).click()
    assert.match(await page.locator('.bar-chart').innerText(), /待核对/)
    await waitForNetwork()
    await page.screenshot({ path: path.join(runtime, 'pending-amount-desktop.png'), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await waitForNetwork()
    assert.match(await page.locator('.relation-table').innerText(), /待核对/)
    await page.screenshot({ path: path.join(runtime, 'pending-amount-mobile.png'), fullPage: true })
  })
  await check('HTTP full workbook equals the corrected CLI workbook cells', async () => {
    const response = await fetch(fixture.apiOrigin + '/api/export/standard.xlsx?' + new URLSearchParams({ dataset_id: dataset }), { headers })
    assert.equal(response.status, 200)
    const downloaded = path.join(runtime, 'http-tables.xlsx')
    await fs.writeFile(downloaded, Buffer.from(await response.arrayBuffer()))
    const code = String.raw`import sys,json;from pathlib import Path;from openpyxl import load_workbook;sys.path.insert(0,sys.argv[3]+'/demo/procurement/backend');from app.acceptance import check_workbook;a=load_workbook(sys.argv[1],read_only=True);b=load_workbook(sys.argv[2],read_only=True);assert a.sheetnames==b.sheetnames;assert all(list(a[n].values)==list(b[n].values) for n in a.sheetnames);a.close();b.close();r=check_workbook(Path(sys.argv[1]).read_bytes(),Path(sys.argv[3]+'/data/official-benchmark/indexes/output-schema.json'));assert r['valid'];print(json.dumps(r['row_counts']))`
    report.row_counts = JSON.parse(execFileSync(fixture.python, ['-X', 'utf8', '-c', code, downloaded, path.join(path.dirname(source), 'procurement-tables.xlsx'), root], { encoding: 'utf8' }))
  })
  assert.deepEqual(report.consoleErrors, [])
  report.passed = true
} catch (error) {
  report.passed = false; report.error = error.stack; console.error(error)
  await page?.screenshot({ path: path.join(runtime, 'failure.png'), fullPage: true }).catch(() => {})
} finally {
  await browser?.close(); await fixture.stop()
  report.source_unchanged = (await hash(source)) === before
  await fs.writeFile(path.join(runtime, 'verification.json'), JSON.stringify(report, null, 2))
  // These are isolated test copies, not delivery databases or original files.
  for (const name of ['ui-verification.db', 'ui-verification.db-wal', 'ui-verification.db-shm']) {
    await fs.unlink(path.join(runtime, name)).catch(error => { if (error.code !== 'ENOENT') throw error })
  }
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, source_unchanged: report.source_unchanged }))
}
if (!report.passed || !report.source_unchanged) process.exitCode = 1
