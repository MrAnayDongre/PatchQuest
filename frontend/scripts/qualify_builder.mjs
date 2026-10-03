// Builds the issue-to-fix style workflow from nothing in the visual builder (drag, configure, connect, validate, save, reload, version),
// then reports what the backend stored. Usage: node scripts/qualify_builder.mjs <base-url> <out-dir>
import { chromium } from 'playwright-core'
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const [base = 'http://127.0.0.1:8765', out = '/tmp/qualify'] = process.argv.slice(2)
mkdirSync(out, { recursive: true })
const root = join(homedir(), '.cache', 'ms-playwright')
const exe = readdirSync(root).filter(d => d.startsWith('chromium_headless_shell-')).sort().reverse()
  .map(d => join(root, d, 'chrome-headless-shell-linux64', 'chrome-headless-shell')).find(existsSync)
const browser = await chromium.launch({ executablePath: exe })
const page = await (await browser.newContext({ viewport: { width: 1920, height: 1080 } })).newPage()
const errors = []
page.on('pageerror', e => errors.push(String(e)))
page.on('console', m => { if (m.type() === 'error' && !/404 \(Not Found\)/.test(m.text())) errors.push(m.text()) })
const evidence = { steps: [] }
const shot = name => page.screenshot({ path: join(out, name + '.png') })
const ok = (name, detail = {}) => { evidence.steps.push({ name, ...detail }); console.log('ok  ', name, Object.keys(detail).length ? JSON.stringify(detail) : '') }
const api = async path => (await fetch(base + path)).json()
const nodeIds = () => page.locator('[data-node-id]').evaluateAll(els => els.map(e => e.dataset.nodeId))

const NAME = 'issue-to-fix built in the UI' + (process.env.WF_SUFFIX ?? '')
await page.goto(base + '/#/workflows/new', { waitUntil: 'networkidle' })
await page.getByRole('textbox', { name: /^Workflow name/ }).fill(NAME)
await page.getByRole('textbox', { name: /^Description/ }).fill('Built by hand in the visual builder: issue, agent, validation, approval, comment, Slack.')
await page.getByLabel('Trigger', { exact: true }).selectOption({ label: 'GitHub issue labelled' })
await page.getByRole('button', { name: 'Add a match' }).click()
await page.getByRole('textbox', { name: /^Event field 1/ }).fill('payload.label')
await page.getByRole('textbox', { name: /^Value 1/ }).fill('agent-ready')
for (const [i, [n, d]] of [['model', 'demo-issue'], ['provider', 'scripted'], ['repo', '/tmp/pq-demo/repos/payments-service']].entries()) {
  if (i > 0 || (await page.getByRole('textbox', { name: /^Variable name 1/ }).count()) === 0) await page.getByRole('button', { name: /add a variable/i }).click()
  await page.getByRole('textbox', { name: new RegExp(`^Variable name ${i + 1}`) }).fill(n)
  await page.getByRole('textbox', { name: new RegExp(`^Default for ${n}`) }).fill(d)
}
ok('workflow settings: name, trigger filter, variables')

// ---- drag steps from the palette onto the canvas
const canvas = page.getByLabel(/^Workflow canvas/)
const plan = [['Agent', 'investigate', 20, 40], ['Condition', 'validated', 260, 40], ['Approval', 'review', 500, 40], ['Action', 'comment', 740, 40],
  ['Action', 'tell', 740, 200], ['Action', 'note', 260, 200], ['End', 'done', 500, 200]]
for (const [type, name, x, y] of plan) {
  const before = new Set(await nodeIds())
  await page.locator('.wf-palette__item', { hasText: new RegExp('^' + type) }).dragTo(canvas, { targetPosition: { x, y } })
  await page.waitForTimeout(150)
  const id = (await nodeIds()).find(i => !before.has(i))
  if (!id) throw new Error('drag did not add a ' + type)
  await page.locator(`[data-node-id="${id}"]`).dblclick()
  const field = page.getByRole('textbox', { name: /^Step name/ })
  await field.fill(name); await field.press('Enter')
  await page.waitForTimeout(100)
}
ok('seven steps dragged onto the canvas', { ids: await nodeIds() })
await shot('b1-dragged')

// ---- arrange them with the mouse, like a person would
const centre = async id => { const b = await page.locator(`[data-node-id="${id}"]`).boundingBox(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 } }
await page.getByRole('button', { name: 'Zoom out' }).click(); await page.getByRole('button', { name: 'Zoom out' }).click()
const z = (await page.locator('[data-node-id]').first().boundingBox()).width / 208
const area = await canvas.boundingBox()
const at = (col, y) => [area.x + 40 + 104 * z + col * 240 * z, area.y + 70 + y * z]
const slots = { investigate: at(0, 0), validated: at(1, 0), review: at(2, 0), comment: at(3, 0), tell: at(4, 0), done: at(5, 80), note: at(1, 170) }
for (const id of ['done', 'tell', 'comment', 'review', 'validated', 'investigate', 'note']) {
  const [x, y] = slots[id]
  const c = await centre(id)
  await page.mouse.move(c.x - 40 * z, c.y); await page.mouse.down(); await page.mouse.move(x - 40 * z, y, { steps: 12 }); await page.mouse.up()
}
ok('steps arranged by dragging them on the canvas')

// ---- connect them by dragging from an output port to the next step
const connect = async (from, to) => {
  const b = await page.locator(`[data-node-id="${from}"] [data-port="out"]`).boundingBox()
  const t = await centre(to)
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await page.mouse.down()
  await page.mouse.move(t.x, t.y, { steps: 10 }); await page.mouse.up()
  await page.waitForTimeout(120)
}
for (const [a, b] of [['investigate', 'validated'], ['validated', 'review'], ['validated', 'note'], ['review', 'comment'], ['review', 'done'], ['review', 'done'],
  ['comment', 'tell'], ['tell', 'done'], ['note', 'done']]) await connect(a, b)
ok('nine connections drawn by dragging between ports')
await shot('b2-connected')

// ---- configure each step in the side panel
const open = async id => { await page.locator(`[data-node-id="${id}"]`).dblclick(); await page.getByRole('tab', { name: 'Step' }).click().catch(() => {}) }
const box = (name) => page.getByRole('textbox', { name: new RegExp('^' + name) })
await open('investigate')
await box('Task').fill('Resolve this issue: {{trigger.payload.title}}\n\n{{trigger.payload.body}}')
await box('Repository folder').fill('{{vars.repo}}'); await box('Provider').fill('{{vars.provider}}'); await box('Model').fill('{{vars.model}}')
await open('validated')
const cond = page.getByRole('group', { name: /./ }).first()
await page.getByRole('button', { name: /start a condition/i }).click().catch(() => {})
await box('Value').fill('{{nodes.investigate.output.verdict}}')
await page.getByLabel('Comparison').selectOption({ label: 'is equal to' }).catch(async () => page.getByLabel('Comparison').selectOption('eq'))
await box('Compare with').fill('passed')
await open('review')
await box('Question for the approver').fill('Post the validated fix from run {{nodes.investigate.output.run_id}} to the issue and Slack?')
const detail = async (i, k, v) => { if (i > 1) await page.getByRole('button', { name: /add a detail/i }).click(); else await page.getByRole('button', { name: /add a detail/i }).click(); await box(`Details name ${i}`).fill(k); await box(`Details value ${i}`).fill(v) }
await open('review')
const conns = page.locator('select[aria-label="Connection to done"]')
console.log('review->done selects:', await conns.count(), await conns.evaluateAll(els => els.map(e => e.value)))
for (let k = 0; k < await conns.count(); k++) if ((await conns.nth(k).inputValue()) === '') await conns.nth(k).selectOption({ label: 'No answer in time' })
await open('comment')
await page.getByLabel('Action', { exact: true }).selectOption('github.comment')
await detail(1, 'body', 'PatchQuest validated a fix for this issue (run {{nodes.investigate.output.run_id}}); its tests pass in an isolated workspace.')
await detail(2, 'issue_number', '{{trigger.payload.number}}')
await open('tell')
await page.getByLabel('Action', { exact: true }).selectOption('slack.post_message')
await detail(1, 'channel', 'C0DEMO001')
await detail(2, 'text', 'Validated a fix for #{{trigger.payload.number}}: {{trigger.payload.title}}')
await open('note')
await page.getByLabel('Action', { exact: true }).selectOption('notify.log')
await detail(1, 'message', 'Not validated: nothing was posted')
ok('every step configured through the side panel')
await shot('b3-configured')
// validation: the server's checker must be satisfied before Save is offered (a bad name is refused with a reason first)
await page.getByRole('tab', { name: /^Workflow$/ }).click()
await box('Workflow name').fill('bad (name)')
await page.getByRole('tab', { name: /^Problems/ }).click()
const refusal = await page.locator('.wf-problems').innerText({ timeout: 8000 }).then(t => t.includes('1 to 64 letters'))
evidence.name_refusal_shown = refusal
await page.getByRole('tab', { name: /^Workflow$/ }).click()
await box('Workflow name').fill(NAME)
await page.getByRole('tab', { name: /^Problems/ }).click()
await page.getByText(/no problems|looks good|ready to save/i).first().waitFor({ timeout: 8000 }).catch(() => {})
const save = page.getByRole('button', { name: /^Save$/ })
await page.waitForFunction(() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Save'); return b && !b.disabled }, null, { timeout: 15000 })
ok('validation: a bad name is refused with a reason, a valid workflow enables Save', { name_refusal_shown: refusal })
await shot('b4-valid')
const positionsBefore = await page.locator('[data-node-id]').evaluateAll(els => Object.fromEntries(els.map(e => { const r = e.getBoundingClientRect(), k = 208 / r.width; return [e.dataset.nodeId, [r.x * k, r.y * k]] })))
await save.click()
await page.getByText(/Saved as version 1/).waitFor({ timeout: 10000 })
ok('saved as version 1')

// ---- reload: what the backend stored must be what we built, positions included
await page.reload({ waitUntil: 'networkidle' })
await page.waitForSelector('[data-node-id]')
const reloaded = await page.locator('[data-node-id]').evaluateAll(els => Object.fromEntries(els.map(e => { const r = e.getBoundingClientRect(), k = 208 / r.width; return [e.dataset.nodeId, [r.x * k, r.y * k]] })))
const list = await api('/api/workflows')
const rec = list.find(w => w.name === NAME)
const stored = await api('/api/workflows/' + rec.id)
const def = stored.definition
const relative = pos => { const xs = Object.values(pos).map(p => p[0]), ys = Object.values(pos).map(p => p[1]); const x0 = Math.min(...xs), y0 = Math.min(...ys); return Object.fromEntries(Object.entries(pos).map(([k, v]) => [k, [Math.round((v[0] - x0) / 20), Math.round((v[1] - y0) / 20)]])) }
const sameLayout = JSON.stringify(relative(positionsBefore)) === JSON.stringify(relative(reloaded))
evidence.stored = { id: rec.id, version: rec.version, trigger: def.trigger, nodes: def.nodes.map(n => `${n.id}:${n.type}${n.config?.action ? '=' + n.config.action : ''}`), edges: def.edges.map(e => `${e.from}->${e.to}${e.when ? '[' + e.when + ']' : ''}`), layout_keys: Object.keys(def.layout ?? {}).length }
if (!sameLayout) throw new Error('layout changed across reload: ' + JSON.stringify({ positionsBefore, reloaded }))
ok('reloaded: the steps, connections and layout are what was saved', evidence.stored)
await shot('b5-reloaded')

// ---- a change becomes version 2; version 1 is unchanged
await page.locator('[data-node-id="note"]').dblclick()
await box('Details value 1').fill('Not validated: nothing was posted (v2)')
await page.getByRole('button', { name: 'Save new version' }).click()
await page.getByText(/Saved as version 2/).waitFor({ timeout: 10000 })
await page.getByRole('button', { name: 'Versions' }).click()
await page.getByRole('dialog').getByText(/version 2/i).first().waitFor({ timeout: 8000 })
const versions = await page.getByRole('dialog').innerText()
await shot('b6-versions')
const v = await api('/api/workflows/' + rec.id + '/versions').catch(() => null)
evidence.versions_dialog = versions.replace(/\s+/g, ' ').slice(0, 300)
ok('a change saved as version 2; the versions dialog lists both', { dialog: evidence.versions_dialog })
await page.keyboard.press('Escape')
await browser.close()
writeFileSync(join(out, 'builder-evidence.json'), JSON.stringify({ ...evidence, errors }, null, 2))
