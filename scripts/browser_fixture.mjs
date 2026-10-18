/** Deterministic local services for browser tests; never read the business DB. */
import fs from 'node:fs/promises'
import { appendFileSync } from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'

export async function startBrowserFixture({ runtime, production = false, apiPort = 8007, webPort = 5177, modelSettingsPath, databaseSource, apiToken = '' }) {
  const root = process.cwd(), frontend = path.join(root, 'demo/procurement/frontend')
  await fs.mkdir(runtime, { recursive: true })
  const database = path.join(runtime, 'ui-verification.db')
  try { await fs.access(database); throw new Error('Browser fixture requires a new database path') }
  catch (error) { if (error.code !== 'ENOENT') throw error }
  if (databaseSource) await fs.copyFile(databaseSource, database)
  const apiOrigin = `http://127.0.0.1:${apiPort}`, uiOrigin = `http://127.0.0.1:${webPort}`
  const python = process.env.PYTHON_EXECUTABLE || path.join(root, 'demo/procurement/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const env = { ...process.env, PYTHONPATH: path.join(root, 'demo/procurement/backend'), PYTHONIOENCODING: 'utf-8',
    DATABASE_PATH: database, UPLOAD_DIR: path.join(runtime, 'uploads'), PARSE_CACHE_DIR: path.join(runtime, 'parsed'),
    MODEL_SETTINGS_PATH: modelSettingsPath || path.join(runtime, 'model-settings.local.json'),
    MODEL_RESPONSE_CACHE_DIR: path.join(root, 'demo/procurement/data/demo/cache'),
    OFFICIAL_DATA_DIR: path.join(root, 'demo/procurement/data/demo'),
    MODEL_NETWORK_ENABLED: 'false', DEEPSEEK_API_KEY: '', DEEPSEEK_BASE_URL: 'https://api.deepseek.com',
    DEEPSEEK_MODEL: 'deepseek-flash', MODEL_MAX_TOKENS: '16384', MODEL_THINKING: 'false', EXTRACTION_STRATEGY: 'schema',
    APP_API_TOKEN: apiToken, NEO4J_URI: '', REFERENCE_SEED: databaseSource ? 'false' : 'true', DEMO_SEED: databaseSource ? 'false' : 'true', VITE_API_TARGET: apiOrigin }
  const bootstrap = `import httpx,uvicorn\nprint('fixture: importing application',flush=True)\nasync def forbidden(*args, **kwargs):\n    raise RuntimeError('External model HTTP is forbidden in browser tests')\nhttpx.AsyncClient.request=forbidden\nfrom app.main import app\nprint('fixture: application imported; starting isolated database and sample seed',flush=True)\nuvicorn.run(app,host='127.0.0.1',port=${apiPort},log_level='warning')`
  const children = []
  const start = (name, executable, args) => {
    const child = spawn(executable, args, { cwd: root, env, windowsHide: true })
    for (const stream of [child.stdout, child.stderr]) stream.on('data', data => appendFileSync(path.join(runtime, name + '.log'), data))
    child.on('error', error => appendFileSync(path.join(runtime, name + '.log'), error.message))
    children.push(child)
  }
  const stop = async () => {
    await Promise.all(children.map(child => new Promise(resolve => {
      if (child.exitCode !== null || child.signalCode !== null) return resolve()
      child.once('exit', resolve); child.kill()
      const timeout = setTimeout(resolve, 5000); timeout.unref()
    })))
  }
  try {
    start('backend', python, ['-X', 'utf8', '-c', bootstrap])
    start('frontend', process.execPath, [path.join(frontend, 'node_modules/vite/bin/vite.js'), ...(production ? ['preview'] : []), frontend, '--config', path.join(frontend, 'vite.config.js'), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'])
    for (const url of [apiOrigin + '/api/health', uiOrigin]) {
      let ready = false
      for (let attempt = 0; attempt < 180; attempt++) {
        if (children.some(child => child.exitCode !== null)) throw new Error('Fixture service exited; inspect logs in ' + runtime)
        try { ready = (await fetch(url, { signal: AbortSignal.timeout(3000) })).ok } catch {}
        if (ready) break
        await new Promise(resolve => setTimeout(resolve, 500))
      }
      if (!ready) throw new Error('Fixture service did not start: ' + url)
    }
    return { root, runtime, database, apiOrigin, uiOrigin, python, modelSettingsPath: env.MODEL_SETTINGS_PATH, stop }
  } catch (error) { await stop(); throw error }
}
