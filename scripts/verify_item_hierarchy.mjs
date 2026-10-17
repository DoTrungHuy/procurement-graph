/** Source-grounded hierarchy UI regression; isolated fixtures, no model HTTP. */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { startBrowserFixture } from './browser_fixture.mjs'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const root = process.cwd()
const runtime = path.join(root, 'results/ui-hierarchy', new Date().toISOString().replace(/[:.]/g, '-'))
const fixture = await startBrowserFixture({ runtime, production: true, apiPort: 8011, webPort: 5181 })
const env = { ...process.env, PYTHONPATH: path.join(root, 'demo/procurement/backend'),
  DATABASE_PATH: fixture.database, UPLOAD_DIR: path.join(runtime, 'uploads'),
  MODEL_SETTINGS_PATH: fixture.modelSettingsPath,
  DEEPSEEK_API_KEY: '', MODEL_NETWORK_ENABLED: 'false', NEO4J_URI: '',
  APP_API_TOKEN: '', REFERENCE_SEED: 'false', DEMO_SEED: 'false' }
const report = { scope: 'Synthetic source-grounded cases in isolated services; no official accuracy or model-speed claim', checks: [], modelRequests: 0 }
let browser, page
async function check(name, action) {
  await action(); report.checks.push({ name, passed: true }); console.log(JSON.stringify({ name, passed: true }))
}
async function openProject(name) {
  await page.getByRole('searchbox', { name: '搜索项目', exact: true }).fill(name)
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await page.getByText('1–1 / 1 条', { exact: true }).waitFor()
  await page.locator('.project-table').getByRole('button', { name, exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '采购包详情', exact: true })
  await dialog.getByRole('button', { name: '层级核验', exact: true }).click()
  return dialog
}
try {
  execFileSync(fixture.python, ['-X', 'utf8', '-c', `
import sys
sys.path.insert(0,'demo/procurement/backend/tests')
from test_item_hierarchy import grounded_notice
from app.database import upsert_notice
for status in ['confirmed','pending']:
    result=grounded_notice()
    result.notice_uuid='synthetic-hierarchy-'+status
    result.project_name='层级核验确认样例' if status=='confirmed' else '层级核验待查样例'
    if status=='pending': result.lots[0].items[1].supplier='供应商乙'
    upsert_notice(result,['notice.html','报价明细.pdf'],'hierarchy-check')
`], { cwd: root, env, stdio: 'pipe' })
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
  const errors = [], externalRequests = []
  await context.route('**/*', route => {
    if (!['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)) {
      externalRequests.push(route.request().url()); return route.abort()
    }
    return route.continue()
  })
  page = await context.newPage(); page.on('pageerror', error => errors.push(error.message))
  await page.goto(fixture.uiOrigin)
  await page.getByRole('combobox', { name: '当前数据集', exact: true }).selectOption('hierarchy-check')
  await page.getByRole('heading', { name: '采购项目 2', exact: true }).waitFor()
  await check('confirmed group shows summary, two details and all evidence checks', async () => {
    const dialog = await openProject('层级核验确认样例')
    const group = dialog.locator('[data-hierarchy-status="confirmed"]')
    await group.waitFor(); assert.equal(await group.locator('.hierarchy-children li').count(), 2)
    assert.match(await dialog.locator('.hierarchy-counts').innerText(), /原文记录\s*3/)
    assert.match(await dialog.locator('.hierarchy-counts').innerText(), /明细视图\s*2/)
    await group.locator('summary').click(); assert.equal(await group.locator('.check-pending').count(), 0)
    await page.screenshot({ path: path.join(runtime, 'hierarchy-confirmed.png') })
  })
  await check('detail evidence opens the program-grounded source row', async () => {
    await page.getByRole('button', { name: '查看明细原文', exact: true }).first().click()
    const evidence = page.getByRole('dialog', { name: '标的来源', exact: true })
    await evidence.getByRole('heading', { name: '程序定位的原文行', exact: true }).waitFor()
    assert.match(await evidence.locator('.grounded-quote').innerText(), /1-1-1.*台式电脑.*总价60元/)
    await page.keyboard.press('Escape'); await evidence.waitFor({ state: 'hidden' })
  })
  await check('detail workbook keeps three official sheets and all uncertain rows', async () => {
    const downloadEvent = page.waitForEvent('download')
    await page.getByRole('button', { name: '下载当前数据集的明细三表', exact: true }).click()
    const download = await downloadEvent
    assert.equal(download.suggestedFilename(), 'procurement-detail-tables.xlsx')
    const filename = path.join(runtime, download.suggestedFilename()); await download.saveAs(filename)
    const details = JSON.parse(execFileSync(fixture.python, ['-X', 'utf8', '-c',
      'import sys,json; from openpyxl import load_workbook; w=load_workbook(sys.argv[1],read_only=True); print(json.dumps({"sheets":w.sheetnames,"items":w["标的表"].max_row-1},ensure_ascii=False)); w.close()', filename],
      { cwd: root, env, encoding: 'utf8' }))
    assert.deepEqual(details.sheets, ['总表', '标的表', '投标人表']); assert.equal(details.items, 5)
  })
  await page.keyboard.press('Escape')
  await page.getByRole('dialog', { name: '采购包详情', exact: true }).waitFor({ state: 'hidden' })
  await check('supplier conflict keeps all rows and explains the failed check', async () => {
    const dialog = await openProject('层级核验待查样例')
    const group = dialog.locator('[data-hierarchy-status="needs_review"]')
    await group.waitFor(); assert.match(await dialog.locator('.hierarchy-counts').innerText(), /明细视图\s*3/)
    await group.locator('summary').click(); assert.match(await group.locator('details').innerText(), /待核验[\s\S]*摘要与所有明细的已披露供应商一致/)
  })
  await check('mobile hierarchy and source controls remain within the panel', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
    const panel = page.getByRole('dialog', { name: '采购包详情', exact: true })
    assert(await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1))
    await panel.getByRole('button', { name: '下载当前数据集的明细三表', exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(runtime, 'hierarchy-mobile.png') })
  })
  assert.deepEqual(errors, []); assert.deepEqual(externalRequests, [])
  report.version = (await (await fetch(fixture.apiOrigin + '/api/health')).json()).version
  report.passed = true
} catch (error) {
  report.passed = false; report.error = error.stack; console.error(error)
  if (page) await page.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {})
} finally {
  await browser?.close(); await fixture.stop()
  report.finished = new Date().toISOString(); report.runtime = path.relative(root, runtime).replaceAll('\\', '/')
  await fs.writeFile(path.join(runtime, 'verification.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, runtime: report.runtime }))
}
if (!report.passed) process.exitCode = 1
