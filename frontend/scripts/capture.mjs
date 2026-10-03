// Screenshots of the running app for visual QA: node scripts/capture.mjs <base-url> <out-dir> [route-file]
// Uses the Chromium that Playwright already downloaded (no browser is installed by this repository).
import { chromium } from 'playwright-core'
import { mkdirSync, readdirSync, existsSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const [base, out, routeFile] = process.argv.slice(2)
if (!base || !out) { console.error('usage: capture.mjs <base-url> <out-dir> [routes.json]'); process.exit(2) }

function findChromium() {
  const root = join(homedir(), '.cache', 'ms-playwright')
  const dirs = existsSync(root) ? readdirSync(root).filter(d => d.startsWith('chromium_headless_shell-')).sort().reverse() : []
  for (const d of dirs) {
    const exe = join(root, d, 'chrome-headless-shell-linux64', 'chrome-headless-shell')
    if (existsSync(exe)) return exe
  }
  throw new Error('no Playwright Chromium found under ~/.cache/ms-playwright')
}

const routes = routeFile ? (await import('node:fs')).readFileSync(routeFile, 'utf8') : null
const list = routes ? JSON.parse(routes) : [['home', '#/'], ['runs', '#/runs'], ['workflows', '#/workflows'], ['metrics', '#/metrics'], ['engines', '#/engines'], ['settings', '#/settings']]
const viewports = [['desktop', 1440, 900], ['laptop', 1100, 760], ['phone', 390, 844]]
const themes = ['light', 'dark']

mkdirSync(out, { recursive: true })
const browser = await chromium.launch({ executablePath: findChromium() })
const problems = []
for (const theme of themes) {
  for (const [vname, width, height] of viewports) {
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme, deviceScaleFactor: 1 })
    const page = await context.newPage()
    page.on('console', m => { if (['error', 'warning'].includes(m.type())) problems.push(`${theme}/${vname} console ${m.type()}: ${m.text().slice(0, 200)}`) })
    page.on('pageerror', e => problems.push(`${theme}/${vname} pageerror: ${String(e).slice(0, 200)}`))
    page.on('requestfailed', r => problems.push(`${theme}/${vname} request failed: ${r.url().slice(0, 120)}`))
    for (const [name, hash] of list) {
      await page.goto(base + '/' + hash, { waitUntil: 'networkidle' })
      await page.waitForTimeout(400)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      if (overflow > 1) problems.push(`${theme}/${vname}/${name}: horizontal overflow of ${overflow}px`)
      await page.screenshot({ path: join(out, `${name}-${vname}-${theme}.png`), fullPage: true })
    }
    await context.close()
  }
}
await browser.close()
writeFileSync(join(out, 'problems.txt'), problems.join('\n') + '\n')
console.log(problems.length ? problems.join('\n') : 'no console errors, failed requests or horizontal overflow')
