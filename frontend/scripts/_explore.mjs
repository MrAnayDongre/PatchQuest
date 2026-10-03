import { chromium } from 'playwright-core'
import { existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
const root = join(homedir(), '.cache', 'ms-playwright')
const exe = readdirSync(root).filter(d => d.startsWith('chromium_headless_shell-')).sort().reverse().map(d => join(root, d, 'chrome-headless-shell-linux64', 'chrome-headless-shell')).find(existsSync)
const browser = await chromium.launch({ executablePath: exe })
const page = await (await browser.newContext({ viewport: { width: 1920, height: 1080 } })).newPage()
const out = '/tmp/claude-1000/qual/shots/'
await page.goto('http://127.0.0.1:8765/#/workflows', { waitUntil: 'networkidle' })
await page.getByText('New workflow').first().click()
await page.waitForTimeout(500)
console.log(page.url())
const canvas = page.getByLabel('Workflow editor')
const box = await canvas.boundingBox(); console.log(box)
for (const [t, x, y] of [['Agent', 120, 120], ['Approval', 420, 120]]) {
  await page.locator('.wf-palette__item', { hasText: new RegExp('^' + t) }).dragTo(page.getByLabel(/^Workflow canvas/), { targetPosition: { x, y } }).catch(e => console.log('drag fail', String(e).slice(0, 150)))
}
await page.waitForTimeout(500)
console.log(await page.locator('[data-node-id]').count())
await page.screenshot({ path: out + 'b1-new.png' })
await browser.close()
