/** Persist model settings across browser/service restarts without provider calls. */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { startBrowserFixture } from './browser_fixture.mjs'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const root = process.cwd(), runtime = path.join(root, 'results/model-settings-ui', new Date().toISOString().replace(/[:.]/g, '-'))
const settingsPath = path.join(runtime, 'model-settings.local.json')
const fakeKey = 'fixture-browser-credential'
const report = { scope: 'Isolated fake credentials; provider HTTP forbidden', checks: [], modelRequests: 0 }
let fixture, browser, page
const errors = [], unexpectedRequests = []
async function check(name, action) { await action(); report.checks.push({ name, passed: true }); console.log(JSON.stringify({ name, passed: true })) }
async function start(name) {
  fixture = await startBrowserFixture({ runtime: path.join(runtime, name), production: true, apiPort: 8013, webPort: 5183, modelSettingsPath: settingsPath })
  await page.goto(fixture.uiOrigin)
  await page.getByRole('button', { name: '连接设置', exact: true }).click()
  const panel = page.getByRole('dialog', { name: '连接设置', exact: true })
  await panel.getByRole('button', { name: '模型服务', exact: true }).click()
  await panel.getByRole('textbox', { name: '模型名称', exact: true }).waitFor()
  await page.waitForFunction(() => {
    const input = document.querySelector('input[aria-label="模型名称"]')
    return input && !input.matches(':disabled') && input.value
  })
  return panel
}
async function save(panel) {
  const response = page.waitForResponse(value => value.url().includes('/api/settings/model') && value.request().method() === 'POST')
  await panel.getByRole('button', { name: '保存模型配置', exact: true }).click()
  const result = await response
  assert.equal(result.status(), 200)
  assert(!(await result.text()).includes(fakeKey))
  await panel.getByText('配置已保存，新任务立即生效。', { exact: true }).waitFor()
}
try {
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.route('**/*', route => {
    const request = route.request(), address = new URL(request.url())
    if (!['127.0.0.1', 'localhost'].includes(address.hostname) ||
        (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && address.pathname !== '/api/settings/model')) {
      unexpectedRequests.push(request.method() + ' ' + address.pathname); return route.abort()
    }
    return route.continue()
  })
  page = await context.newPage(); page.on('pageerror', error => errors.push(error.message))
  let panel = await start('before-restart')
  await check('first save persists settings and enables manual extraction without a model call', async () => {
    await panel.getByRole('textbox', { name: '模型服务地址', exact: true }).fill('https://configuration-fixture.invalid/v1/chat/completions')
    await panel.getByRole('textbox', { name: '模型名称', exact: true }).fill('fixture-model-a')
    await panel.getByLabel('模型API Key', { exact: true }).fill(fakeKey)
    await panel.getByRole('checkbox', { name: '启用模型抽取', exact: true }).check()
    await save(panel)
    assert.equal(await panel.getByLabel('模型API Key', { exact: true }).inputValue(), '')
    assert.equal(await panel.getByRole('textbox', { name: '模型服务地址', exact: true }).inputValue(), 'https://configuration-fixture.invalid/v1')
    assert.match(await panel.locator('[data-testid="model-key-status"]').innerText(), /已配置/)
  })
  await check('reopening and reloading keep metadata without exposing saved key', async () => {
    await page.reload()
    await page.getByRole('button', { name: '连接设置', exact: true }).click()
    panel = page.getByRole('dialog', { name: '连接设置', exact: true })
    await panel.getByRole('button', { name: '模型服务', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('input[aria-label="模型名称"]')?.value === 'fixture-model-a')
    assert.equal(await panel.getByLabel('模型API Key', { exact: true }).inputValue(), '')
    assert(!(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).includes(fakeKey))
  })
  await check('blank key preserves stored credential while changing model', async () => {
    await panel.getByRole('textbox', { name: '模型名称', exact: true }).fill('fixture-model-b')
    await save(panel)
    const stored = JSON.parse(await fs.readFile(settingsPath, 'utf8'))
    assert.equal(stored.api_key, fakeKey); assert.equal(stored.model, 'fixture-model-b')
  })
  await fixture.stop(); fixture = null
  panel = await start('after-restart')
  await check('a fresh backend loads saved address model and credential status', async () => {
    assert.equal(await panel.getByRole('textbox', { name: '模型名称', exact: true }).inputValue(), 'fixture-model-b')
    assert.equal(await panel.getByRole('textbox', { name: '模型服务地址', exact: true }).inputValue(), 'https://configuration-fixture.invalid/v1')
    assert.equal(await panel.getByLabel('模型API Key', { exact: true }).inputValue(), '')
    assert.match(await panel.locator('[data-testid="model-key-status"]').innerText(), /已配置/)
    assert(await panel.getByRole('checkbox', { name: '启用模型抽取', exact: true }).isChecked())
    const health = await (await fetch(fixture.apiOrigin + '/api/health')).json()
    assert.equal(health.model, 'fixture-model-b'); assert.equal(health.model_network_enabled, true)
    report.version = health.version
    await page.screenshot({ path: path.join(runtime, 'settings-desktop.png') })
  })
  await check('mobile configuration fields and save control remain usable', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    assert(await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1))
    await panel.getByRole('button', { name: '保存模型配置', exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(runtime, 'settings-mobile.png') })
  })
  assert.deepEqual(errors, []); assert.deepEqual(unexpectedRequests, [])
  report.passed = true
} catch (error) {
  report.passed = false; report.error = error.stack; console.error(error)
  if (page) await page.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {})
} finally {
  await browser?.close(); await fixture?.stop()
  await fs.mkdir(runtime, { recursive: true })
  report.finished = new Date().toISOString(); report.runtime = path.relative(root, runtime).replaceAll('\\', '/')
  await fs.writeFile(path.join(runtime, 'verification.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, runtime: report.runtime }))
}
if (!report.passed) process.exitCode = 1
