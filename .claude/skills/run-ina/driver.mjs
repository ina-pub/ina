// Drive ina in headless Chromium from a script of commands on stdin, one per line.
//
//   node .claude/skills/run-ina/driver.mjs <<'EOF'
//   goto /in/pics/
//   open YouTube
//   shot pics
//   frames
//   report
//   EOF
//
// Commands:
//   goto <path|url>          load a page (paths are relative to $INA_RUN_URL)
//   login <user> <password>  sign in; the session cookie is kept for later commands
//   click <css selector>     click the first match
//   open <link title>        click the expando button of the link with exactly that title,
//                            then wait for its player to load
//   wait <ms>                pause
//   text <css selector>      print the first match's innerText
//   eval <js expression>     print the result of evaluating it in the page
//   shot <name> [selector]   screenshot the page (or one element) to $INA_RUN_SHOTS/<name>.png
//   frames                   list the open expandos' players: size, src, and the text inside
//   report                   CSP violations on ina's own page, and uncaught page errors
//
// Lines starting with # are ignored. Needs playwright-core installed under $INA_RUN_DEPS.

import { mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { createInterface } from 'node:readline'

const deps = process.env.INA_RUN_DEPS ?? `${tmpdir()}/ina-run`
const { chromium } = createRequire(`${deps}/`)('playwright-core')
const base = process.env.INA_RUN_URL ?? 'http://localhost:8765'
const shots = process.env.INA_RUN_SHOTS ?? `${deps}/shots`
mkdirSync(shots, { recursive: true })

// The full Chromium build (Chrome for Testing), not the default headless shell: the shell has no
// H.264, so linked .mp4 files fail with MEDIA_ERR_SRC_NOT_SUPPORTED.
const browser = await chromium.launch({ channel: 'chromium' })
const context = await browser.newContext({ viewport: { width: 1100, height: 900 } })
const page = await context.newPage()
const violations = []
const errors = []
// Only ina's own policy: each embedded site's frame enforces its own, which isn't ours to judge.
await page.exposeFunction('reportCsp', (v) => violations.push(v))
await page.addInitScript(() => {
  if (window.top !== window) return
  document.addEventListener('securitypolicyviolation', (e) =>
    window.reportCsp(`${e.effectiveDirective} blocked ${e.blockedURI}`)
  )
})
page.on('pageerror', (e) => errors.push(String(e)))

const url = (target) => (target.startsWith('http') ? target : `${base}${target}`)

async function frames() {
  const players = page.locator('.expando:not([hidden]) iframe, .expando:not([hidden]) video')
  for (const player of await players.all()) {
    const info = await player.evaluate((el) => ({
      tag: el.tagName.toLowerCase(),
      height: Math.round(el.getBoundingClientRect().height),
      resized: el.style.height || null,
      src: (el.getAttribute('src') ?? `(not loaded) ${el.dataset.src}`).slice(0, 80)
    }))
    let inside
    if (info.tag === 'iframe') {
      const frame = await (await player.elementHandle()).contentFrame()
      inside = frame
        ? await frame
            .evaluate(() => document.body?.innerText.slice(0, 80).replace(/\s+/g, ' '))
            .catch((e) => `unreadable: ${e.message.slice(0, 60)}`)
        : 'no frame'
    } else {
      inside = await player.evaluate(
        (v) => `readyState=${v.readyState} error=${v.error?.code ?? 'none'}`
      )
    }
    console.log(JSON.stringify(info), '|', inside)
  }
}

const commands = {
  goto: async (target) => page.goto(url(target)),
  login: async (rest) => {
    const [user, passwd] = rest.split(' ')
    const form = { user, passwd, api_type: 'json' }
    const body = await (await context.request.post(url('/api/login'), { form })).json()
    console.log('login errors:', body.json.errors)
  },
  click: async (selector) => page.locator(selector).first().click(),
  open: async (title) => {
    const entry = page.locator('.thing').filter({
      has: page.locator('a.title', {
        hasText: new RegExp(`^${title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`)
      })
    })
    await entry.locator('.expando-button').click()
    // Players load from other sites, and posts resize once rendered.
    await page.waitForTimeout(6000)
  },
  wait: async (ms) => page.waitForTimeout(Number(ms)),
  text: async (selector) => console.log(await page.locator(selector).first().innerText()),
  eval: async (js) => console.log(await page.evaluate(js)),
  shot: async (rest) => {
    const [name, ...selector] = rest.split(' ')
    const path = `${shots}/${name}.png`
    const target = selector.length ? page.locator(selector.join(' ')).first() : page
    await target.screenshot({ path, ...(selector.length ? {} : { fullPage: true }) })
    console.log('screenshot:', path)
  },
  frames,
  report: async () => {
    console.log('CSP violations on ina:', violations.length ? violations : 'none')
    console.log('page errors:', errors.length ? errors : 'none')
  }
}

let failed = false
for await (const raw of createInterface({ input: process.stdin })) {
  const line = raw.trim()
  if (!line || line.startsWith('#')) continue
  const [name, ...rest] = line.split(' ')
  const command = commands[name]
  console.log(`> ${line}`)
  if (!command) {
    console.log(`unknown command: ${name}`)
    failed = true
    continue
  }
  try {
    await command(rest.join(' '))
  } catch (error) {
    console.log(`failed: ${error.message.split('\n')[0]}`)
    failed = true
  }
}
await browser.close()
process.exit(failed ? 1 : 0)
