// Keyboard check: Tab through each page and report any focused control that shows no visible focus indicator.
// Usage: node scripts/qualify_focus.mjs <base-url> [tabs-per-page]
import { chromium } from 'playwright-core'
import { existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const [base = 'http://127.0.0.1:8765', tabs = '18'] = process.argv.slice(2)
const root = join(homedir(), '.cache', 'ms-playwright')
const exe = readdirSync(root).filter(d => d.startsWith('chromium_headless_shell-')).sort().reverse()
  .map(d => join(root, d, 'chrome-headless-shell-linux64', 'chrome-headless-shell')).find(existsSync)
const browser = await chromium.launch({ executablePath: exe })
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage()
const bad = []
let checked = 0
for (const hash of ['#/', '#/runs', '#/workflows', '#/integrations', '#/metrics', '#/repositories', '#/settings']) {
  await page.goto(base + '/' + hash, { waitUntil: 'networkidle' })
  await page.waitForTimeout(300)
  for (let i = 0; i < Number(tabs); i++) {
    await page.keyboard.press('Tab')
    const info = await page.evaluate(() => {
      const e = document.activeElement
      if (!e || e === document.body) return null
      const s = getComputedStyle(e)
      const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== 'none')
      return { ring: !!ring, what: `${e.tagName.toLowerCase()}${e.getAttribute('aria-label') ? `[${e.getAttribute('aria-label')}]` : ''} ${(e.textContent || '').trim().slice(0, 30)}` }
    })
    if (!info) continue
    checked += 1
    if (!info.ring) bad.push(`${hash}: ${info.what}`)
  }
}
await browser.close()
console.log(JSON.stringify({ focused_controls_checked: checked, without_visible_focus: bad }, null, 1))
process.exit(bad.length ? 1 : 0)
