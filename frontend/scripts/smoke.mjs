// Browser smoke test against a running demo: node scripts/smoke.mjs http://127.0.0.1:8765
// Opens the app in real Chromium and walks the main path; exits non-zero on the first failed expectation or any console error.
// It APPROVES the waiting patch (that is part of the path), so run `patchquest demo reset` afterwards to get a fresh demo.
import { chromium } from 'playwright-core'
import { existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const base = process.argv[2] ?? 'http://127.0.0.1:8765'
const root = join(homedir(), '.cache', 'ms-playwright')
const exe = readdirSync(root).filter(d => d.startsWith('chromium_headless_shell-')).sort().reverse()
  .map(d => join(root, d, 'chrome-headless-shell-linux64', 'chrome-headless-shell')).find(existsSync)
if (!exe) { console.error('no Playwright Chromium found'); process.exit(2) }

const errors = []
const browser = await chromium.launch({ executablePath: exe })
const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage()
page.on('pageerror', e => errors.push(String(e)))
page.on('console', m => { if (m.type() === 'error' && !/404 \(Not Found\)/.test(m.text())) errors.push(m.text()) })

let step = 0
async function check(name, fn) {
  step += 1
  try { await fn(); console.log(`ok   ${step}. ${name}`) }
  catch (e) { console.error(`FAIL ${step}. ${name}: ${String(e).split('\n')[0]}`); await browser.close(); process.exit(1) }
}
const text = sel => page.locator(sel).first().innerText()
const api = async path => (await fetch(base + path)).json()

await check('home shows what needs a person', async () => {
  await page.goto(base + '/#/', { waitUntil: 'networkidle' })
  await page.getByText('Needs you').first().waitFor({ timeout: 8000 })
  await page.getByRole('button', { name: /approve once/i }).first().waitFor()
})
await check('runs list has the six seeded runs', async () => {
  await page.goto(base + '/#/runs', { waitUntil: 'networkidle' })
  await page.getByText('Decline refunds above the refund limit').first().waitFor()
})
await check('a run shows its activity, changes and why', async () => {
  await page.getByText('Decline refunds above the refund limit').first().click()
  await page.getByRole('tab', { name: /changes/i }).click()
  await page.getByText(/refunds\.py/).first().waitFor()
  await page.getByRole('tab', { name: /why/i }).click()
  await page.getByText(/Why this happened/i).first().waitFor()
})
await check('integrations are listed as simulators', async () => {
  await page.goto(base + '/#/integrations', { waitUntil: 'networkidle' })
  await page.getByText('GitHub (simulator)').first().waitFor()
  await page.getByText('Simulated').first().waitFor()
})
await check('the workflow opens in the builder', async () => {
  await page.goto(base + '/#/workflows', { waitUntil: 'networkidle' })
  await page.getByText('issue-to-fix').first().click()
  await page.getByText('Add a step').first().waitFor()
})
await check('metrics render real numbers', async () => {
  await page.goto(base + '/#/metrics', { waitUntil: 'networkidle' })
  await page.getByText(/success/i).first().waitFor()
})
await check('approving the waiting patch from the home page completes the decision', async () => {
  await page.goto(base + '/#/', { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: /approve once/i }).first().click()
  for (let i = 0; i < 40; i++) {
    const runs = await api('/api/runs')
    if (runs.every(r => r.status !== 'waiting_approval')) return
    await page.waitForTimeout(250)
  }
  throw new Error('the run is still waiting after approval')
})
await check('no console errors during the walk', async () => { if (errors.length) throw new Error(errors.join(' | ').slice(0, 300)) })
await browser.close()
console.log('SMOKE OK')
