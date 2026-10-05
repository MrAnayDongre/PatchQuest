// The live part of the demo qualification, in a real browser: node scripts/qualify_live.mjs <base> <out-dir> wait|approve|tour
//   wait     every workflow run is at its approval gate; capture the run, its agent run, the changes and the reasons
//   approve  approve each gate in the UI and wait for the workflow runs to finish
//   tour     the pages an investor sees (home, runs, metrics, evaluation, integrations, settings); report console errors
import { chromium } from 'playwright-core'
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const [base = 'http://127.0.0.1:8765', out = '/tmp/qualify', phase = 'wait'] = process.argv.slice(2)
mkdirSync(out, { recursive: true })
const root = join(homedir(), '.cache', 'ms-playwright')
const exe = readdirSync(root).filter(d => d.startsWith('chromium_headless_shell-')).sort().reverse()
  .map(d => join(root, d, 'chrome-headless-shell-linux64', 'chrome-headless-shell')).find(existsSync)
const browser = await chromium.launch({ executablePath: exe })
const page = await (await browser.newContext({ viewport: { width: 1920, height: 1080 } })).newPage()
const errors = []
page.on('pageerror', e => errors.push(String(e)))
page.on('console', m => { if (m.type() === 'error' && !/404 \(Not Found\)/.test(m.text())) errors.push(m.text()) })
const shot = name => page.screenshot({ path: join(out, name + '.png') })
const api = async path => (await fetch(base + path)).json()
const go = async hash => { await page.goto(base + '/' + hash, { waitUntil: 'networkidle' }); await page.waitForTimeout(500) }
const tag = process.env.TAG ? process.env.TAG + '-' : ''
const ev = {}
const log = (k, v) => { ev[k] = v; console.log(k, JSON.stringify(v)) }

if (phase === 'wait') {
  const deadline = Date.now() + 120000
  let runs = []
  while (Date.now() < deadline) {
    runs = (await api('/api/workflows/runs')).filter(r => ['pending', 'running', 'waiting'].includes(r.status))
    const detail = await Promise.all(runs.map(r => api('/api/workflows/runs/' + r.id)))
    if (runs.length && detail.every(d => d.steps?.some(s => s.node_id && s.status === 'waiting' && s.wait_kind === 'approval'))) break
    await new Promise(r => setTimeout(r, 1000))
  }
  log('workflow_runs_waiting', runs.map(r => ({ id: r.id, status: r.status })))
  let i = 0
  for (const r of runs) {
    i += 1
    await go(`#/workflows/runs/${r.id}`)
    await page.getByRole('button', { name: 'Approve' }).first().waitFor({ timeout: 30000 })
    await shot(`l${tag}${i}-workflow-run-waiting`)
    const d = await api('/api/workflows/runs/' + r.id)
    const child = d.steps.find(s => s.child_run_id)?.child_run_id
    if (child && i === 1) {
      await go(`#/runs/${child}`)
      await shot(`l${tag}-run-activity`)
      await page.getByRole('tab', { name: /changes/i }).click(); await page.waitForTimeout(600); await shot(`l${tag}-run-changes`)
      await page.getByRole('tab', { name: /why/i }).click(); await page.waitForTimeout(600); await shot(`l${tag}-run-why`)
      const run = await api('/api/runs/' + child)
      log('agent_run', { id: child, status: run.status, outcome: run.outcome ?? null, verdict: run.verdict ?? null })
    }
  }
}

if (phase === 'approve') {
  const runs = (await api('/api/workflows/runs')).filter(r => r.status === 'waiting')
  let i = 0
  for (const r of runs) {
    i += 1
    await go(`#/workflows/runs/${r.id}`)
    await page.getByRole('button', { name: 'Approve' }).first().click()
    await page.getByText('Approved').first().waitFor({ timeout: 15000 }).catch(() => {})
  }
  const deadline = Date.now() + 90000
  let states = []
  while (Date.now() < deadline) {
    states = (await api('/api/workflows/runs')).map(r => r.status)
    if (states.every(s => s === 'completed' || s === 'succeeded')) break
    await new Promise(r => setTimeout(r, 1000))
  }
  log('workflow_run_states_after_approval', states)
  if (runs[0]) { await go(`#/workflows/runs/${runs[0].id}`); await shot(`l${tag}-workflow-run-done`) }
}

if (phase === 'tour') {
  for (const [name, hash] of [['home', '#/'], ['runs', '#/runs'], ['workflows', '#/workflows'], ['integrations', '#/integrations'], ['metrics', '#/metrics'],
    ['repositories', '#/repositories'], ['engines', '#/engines'], ['settings', '#/settings'], ['extras', '#/extras']]) {
    await go(hash)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    await shot('t-' + name)
    log('page_' + name, { overflow, text_chars: (await page.locator('main').innerText()).length })
  }
}
log('console_errors', errors)
writeFileSync(join(out, `live-${tag}${phase}.json`), JSON.stringify(ev, null, 2))
await browser.close()
process.exit(errors.length ? 1 : 0)
