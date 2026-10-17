/** Read-only UI race tests. Delayed/injected responses are explicitly identified in the report. */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import assert from 'node:assert/strict'
import { startBrowserFixture } from './browser_fixture.mjs'
const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const root = process.cwd(), output = path.join(root, 'results/ui-checks/latest')
await fs.mkdir(output, { recursive: true })
const runtime = path.join(root, 'results/ui-checks', 'interactions-' + new Date().toISOString().replace(/[:.]/g, '-'))
const fixture = await startBrowserFixture({ runtime, apiPort: 8008, webPort: 5178 })
const { apiOrigin, uiOrigin } = fixture
const report = { scope: 'Read-only race and keyboard tests; fresh isolated fixtures with deliberately delayed responses', checks: [], consoleErrors: [], blockedRequests: [] }
const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) }).catch(async error => { await fixture.stop(); throw error })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await context.newPage(); page.on('pageerror', error => report.consoleErrors.push(error.message))
await context.route('**/*', route => {
  const request = route.request(), url = new URL(request.url())
  if (!['localhost', '127.0.0.1'].includes(url.hostname) || request.method() !== 'GET') { report.blockedRequests.push(url.origin + url.pathname); return route.abort() }
  return route.continue()
})
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
function latch() { let release; const promise = new Promise(resolve => { release = resolve }); return { promise, release } }
async function check(name, action) { await action(); report.checks.push({ name, passed: true }); console.log(JSON.stringify({ check: name, passed: true })) }
async function navigate(view) { await page.locator(`.primary-nav a[href="#/${view}"]`).click(); await page.waitForURL('**/#/' + view) }
try {
  const health = await (await context.request.get(apiOrigin + '/api/health')).json(); assert.equal(health.model_network_enabled, false)
  await page.goto(uiOrigin + '/#/items'); await page.getByText('1–20 / 138 条', { exact: true }).waitFor()
  await check('delayed item search cannot overwrite a different dataset', async () => {
    const entered = latch(), blocked = latch()
    await page.route('**/api/items?**', async route => {
      if (new URL(route.request().url()).searchParams.get('q') !== 'stale-probe-missing') return route.continue()
      const response = await route.fetch(); entered.release(); await blocked.promise; await route.fulfill({ response })
    })
    await page.getByRole('searchbox', { name: '搜索标的', exact: true }).fill('stale-probe-missing'); await page.getByRole('button', { name: '检索', exact: true }).click(); await entered.promise
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('demo'); await page.locator('.table-scroll[aria-busy="false"]').waitFor(); const before = await page.locator('.result-summary').innerText(); assert(!before.includes('138'))
    blocked.release(); await wait(300); assert.equal(await page.locator('.result-summary').innerText(), before); assert(await page.locator('.items-table tbody tr').count() > 0)
    await page.unroute('**/api/items?**'); await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('official-reference'); await page.getByText('1–20 / 138 条', { exact: true }).waitFor()
  })
  await check('changing the query subject invalidates an in-flight relationship result', async () => {
    await navigate('relations'); const entered = latch(), blocked = latch()
    await page.route('**/api/analytics/purchaser-partners?**', async route => {
      if (new URL(route.request().url()).searchParams.get('purchaser') !== '香港城市大学(东莞)') return route.continue()
      const response = await route.fetch(); entered.release(); await blocked.promise; await route.fulfill({ response })
    })
    await page.getByRole('button', { name: '填入样本示例', exact: true }).click(); await page.getByRole('button', { name: '查询关系', exact: true }).click(); await entered.promise
    await page.getByRole('combobox', { name: '采购单位', exact: true }).selectOption('肇庆学院'); blocked.release(); await wait(300); assert.equal(await page.locator('[data-testid=scenario-result]').count(), 0)
    await page.getByRole('button', { name: '查询关系', exact: true }).click(); await page.locator('[data-testid=scenario-result] tbody tr').waitFor(); assert.match(await page.locator('.scenario-results').innerText(), /广东南方通信建设有限公司/)
    await page.unroute('**/api/analytics/purchaser-partners?**')
  })
  await check('leaving a page while its default graph is loading does not render into a disposed view', async () => {
    await navigate('items')
    const entered = latch(), blocked = latch()
    await page.route('**/api/graph?**', async route => { const response = await route.fetch(); entered.release(); await blocked.promise; await route.fulfill({ response }) })
    await navigate('relations'); await entered.promise; await navigate('items'); blocked.release(); await wait(500)
    assert.equal(await page.locator('.network-canvas').count(), 0); assert.deepEqual(report.consoleErrors, []); await page.unroute('**/api/graph?**')
  })
  await check('default graph and recent projects reject delayed responses from the previous dataset', async () => {
    await navigate('overview'); await page.locator('.network-canvas canvas').waitFor(); await page.locator('.recent-project-link').first().waitFor()
    const graphEntered = latch(), recentEntered = latch(), blocked = latch()
    await page.route('**/api/graph?**', async route => {
      if (new URL(route.request().url()).searchParams.get('dataset_id') !== 'official-reference') return route.continue()
      const response = await route.fetch(); graphEntered.release(); await blocked.promise; await route.fulfill({ response })
    })
    await page.route('**/api/projects?**', async route => {
      const params = new URL(route.request().url()).searchParams
      if (params.get('dataset_id') !== 'official-reference' || params.get('page_size') !== '5') return route.continue()
      const response = await route.fetch(); recentEntered.release(); await blocked.promise; await route.fulfill({ response })
    })
    await page.getByRole('button', { name: '刷新数据', exact: true }).click(); await Promise.all([graphEntered.promise, recentEntered.promise])
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('demo')
    await page.getByRole('heading', { name: '采购项目 6', exact: true }).waitFor(); await page.locator('.network-surface[aria-busy="false"] .network-canvas canvas').waitFor(); await page.locator('.recent-project-link').first().waitFor()
    const before = { graph: await page.getByTestId('network-summary').innerText(), recent: await page.locator('.recent-project-link').allTextContents() }
    blocked.release(); await wait(400)
    assert.equal(await page.getByTestId('network-summary').innerText(), before.graph); assert.deepEqual(await page.locator('.recent-project-link').allTextContents(), before.recent)
    await page.unroute('**/api/graph?**'); await page.unroute('**/api/projects?**')
    await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('official-reference'); await page.getByRole('heading', { name: '采购项目 26', exact: true }).waitFor()
  })
  await check('graph and recent-project retries preserve the complete project list', async () => {
    await page.locator('.network-canvas canvas').waitFor(); let graphFailure = false, recentFailure = false
    await page.route('**/api/graph?**', route => {
      if (!graphFailure && new URL(route.request().url()).searchParams.get('limit') === '16') { graphFailure = true; return route.fulfill({ status: 503, json: { detail: 'Verification: temporary graph failure' } }) }
      return route.continue()
    })
    await page.getByRole('combobox', { name: '关系网络采购包数', exact: true }).selectOption('16')
    await page.getByRole('alert').filter({ hasText: 'temporary graph failure' }).waitFor(); assert.equal(await page.locator('.project-table tbody tr').count(), 10)
    await page.locator('.network-surface').getByRole('button', { name: '重新加载', exact: true }).click(); await page.locator('.network-canvas canvas').waitFor()
    await page.route('**/api/projects?**', route => {
      if (!recentFailure && new URL(route.request().url()).searchParams.get('page_size') === '5') { recentFailure = true; return route.fulfill({ status: 503, json: { detail: 'Verification: temporary recent-project failure' } }) }
      return route.continue()
    })
    await page.getByRole('button', { name: '刷新数据', exact: true }).click(); await page.getByRole('alert').filter({ hasText: 'temporary recent-project failure' }).waitFor()
    assert.equal(await page.locator('.project-table tbody tr').count(), 10)
    await page.getByRole('button', { name: '重新加载近期项目', exact: true }).click(); await page.locator('.recent-project-link').first().waitFor(); assert.equal(await page.locator('.recent-project-link').count(), 5)
    await page.unroute('**/api/graph?**'); await page.unroute('**/api/projects?**')
  })
  await check('skip link focuses the current page without changing the route', async () => {
    await navigate('items'); await page.locator('.skip-link').focus(); await page.keyboard.press('Enter'); assert.match(page.url(), /#\/items$/); assert.equal(await page.evaluate(() => document.activeElement.id), 'main-content')
  })
  await check('mobile keeps both exports accessible and supplier selection controls within the viewport', async () => {
    await page.setViewportSize({ width: 390, height: 844 }); assert(await page.getByRole('button', { name: '处理说明', exact: true }).isVisible()); assert(await page.getByRole('button', { name: '导出三表', exact: false }).isVisible())
    await navigate('relations'); await page.locator('[data-scenario-tab=common-projects]').click(); await page.locator('.select-trigger').click(); const box = await page.locator('.picker-popover').boundingBox(); assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= 390 && box.y + box.height <= 844)
    assert(await page.getByRole('button', { name: '完成选择', exact: true }).isVisible()); await page.keyboard.press('Escape'); assert.equal(await page.locator('.picker-popover').count(), 0)
  })
  await check('unapplied invalid filters do not break workspace refresh; navigation returns to the top', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 }); await navigate('items')
    await page.getByRole('button', { name: '金额与数量筛选', exact: false }).click(); await page.getByRole('spinbutton', { name: '单价（元）下限', exact: true }).fill('100'); await page.getByRole('spinbutton', { name: '单价（元）上限', exact: true }).fill('1'); await page.getByRole('button', { name: '检索', exact: true }).click(); await page.getByRole('alert').filter({ hasText: '下限不能大于上限' }).waitFor()
    await page.evaluate(() => window.scrollTo(0, 1500)); await navigate('overview'); await page.waitForFunction(() => scrollY === 0)
    await page.getByRole('button', { name: '刷新数据', exact: true }).click(); await page.locator('.table-scroll[aria-busy="false"]').waitFor(); assert.equal(await page.locator('.global-error').count(), 0); assert.equal(await page.locator('.project-table tbody tr').count(), 10)
  })
  await check('visual refinements keep tab markers, sticky controls and reduced motion working', async () => {
    assert.equal(await page.locator('.primary-nav .app-icon svg').count(), 4)
    assert.equal(await page.locator('h1').evaluate(element => getComputedStyle(element).fontSize), '22px')
    await page.waitForFunction(() => parseFloat(document.querySelector('.primary-nav')?.style.getPropertyValue('--tab-width')) > 0)
    assert.equal(await page.locator('.topbar').evaluate(element => getComputedStyle(element).position), 'sticky')
    await page.emulateMedia({ reducedMotion: 'reduce' }); await page.setViewportSize({ width: 1440, height: 560 }); await navigate('relations')
    await page.locator('.network-canvas canvas').waitFor(); await page.locator('[data-scenario-tab=common-projects]').click(); await page.getByRole('button', { name: '填入样本示例', exact: true }).click(); await page.getByRole('button', { name: '查询关系', exact: true }).click()
    await page.locator('[data-testid=scenario-result] tbody tr').first().waitFor(); await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await page.getByRole('button', { name: '回到顶部', exact: false }).waitFor(); await page.getByRole('button', { name: '回到顶部', exact: false }).click(); await page.waitForFunction(() => scrollY === 0)
    const durations = await page.locator('.primary-nav').evaluate(element => getComputedStyle(element, '::before').transitionDuration)
    assert(durations.split(',').every(value => parseFloat(value) === 0))
  })
  await check('project sorting resets pagination and survives refresh and reload', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 }); await navigate('overview')
    await page.getByRole('button', { name: '项目下一页', exact: true }).click(); await page.getByText('11–20 / 26 条', { exact: true }).waitFor()
    const sorted = page.waitForResponse(response => { const url = new URL(response.url()); return url.pathname === '/api/projects' && url.searchParams.get('sort') === 'imported_at' && url.searchParams.get('page') === '1' })
    await page.getByRole('combobox', { name: '项目排序', exact: true }).selectOption('imported_at')
    const result = await (await sorted).json(); await page.getByText('1–10 / 26 条', { exact: true }).waitFor()
    const titles = result.data.map(project => [project.name, project.lot_name, project.project_code].filter(Boolean).join(' · '))
    assert.deepEqual(await page.locator('.project-link').evaluateAll(elements => elements.map(element => element.title)), titles)
    const refreshed = page.waitForResponse(response => { const url = new URL(response.url()); return url.pathname === '/api/projects' && url.searchParams.get('sort') === 'imported_at' })
    await page.getByRole('button', { name: '刷新数据', exact: true }).click(); await refreshed
    await page.locator('.table-scroll[aria-busy="false"]').waitFor(); assert.equal(await page.getByRole('combobox', { name: '项目排序', exact: true }).inputValue(), 'imported_at')
    await page.reload(); await page.getByText('1–10 / 26 条', { exact: true }).waitFor()
    assert.equal(await page.getByRole('combobox', { name: '项目排序', exact: true }).inputValue(), 'imported_at')
    assert.deepEqual(await page.locator('.project-link').evaluateAll(elements => elements.map(element => element.title)), titles)
    await page.screenshot({ path: path.join(output, 'project-sort-desktop.png') })
    await page.setViewportSize({ width: 390, height: 844 }); await page.screenshot({ path: path.join(output, 'project-sort-mobile.png') })
  })
  assert.deepEqual(report.consoleErrors, []); assert.deepEqual(report.blockedRequests, []); report.passed = true
} catch (error) { report.passed = false; report.error = error.stack; console.error(error); await page.screenshot({ path: path.join(output, 'edgecase-failure.png') }).catch(() => {}) }
finally { await context.close(); await browser.close(); await fixture.stop(); report.finished = new Date().toISOString(); await fs.writeFile(path.join(output, 'edgecase-verification.json'), JSON.stringify(report, null, 2)) }
if (!report.passed) process.exitCode = 1
