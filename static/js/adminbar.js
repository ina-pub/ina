// The admin bar: where this page's time went, on the server and in the browser.
// The server keeps each page's timings for a minute; this fetches them once the page has loaded.

import { h } from './h.js'

// Hidden, open or neither, remembered per browser.
const STATE = 'ina-admin-bar'

function remembered() {
  try {
    return localStorage.getItem(STATE) || ''
  } catch {
    return ''
  }
}

function remember(state) {
  try {
    if (state) {
      localStorage.setItem(STATE, state)
    } else {
      localStorage.removeItem(STATE)
    }
  } catch {
    // Private windows may refuse; the bar just forgets.
  }
}

function ms(value) {
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ms`
}

function totalsText(recording) {
  const parts = [`server ${ms(recording.total)}`]
  for (const [kind, { name, count, ms: spent }] of Object.entries(recording.totals)) {
    parts.push(kind === 'render' ? `${name} ${ms(spent)}` : `${name} ${count} in ${ms(spent)}`)
  }
  return parts.join(' · ')
}

// One row of a timeline: a label, then a bar placed by its start and length in ``scale`` ms.
function row(label, start, length, scale, kind, title = label) {
  const bar = h('span', { class: `admin-bar-span admin-bar-${kind}` })
  bar.style.setProperty('margin-left', `${(100 * start) / scale}%`)
  bar.style.setProperty('width', `${Math.max((100 * length) / scale, 0.3)}%`)
  return h(
    'li',
    { title: `${title}: ${ms(length)} at ${ms(start)}` },
    h('span', { class: 'admin-bar-label' }, label),
    h('span', { class: 'admin-bar-track' }, bar),
    h('span', { class: 'admin-bar-ms' }, ms(length))
  )
}

function serverTimeline(recording) {
  const scale = Math.max(recording.total, 1)
  const rows = [row('whole request', 0, recording.total, scale, 'total')]
  for (const span of recording.spans) {
    const name = recording.totals[span.kind]?.name || span.kind
    rows.push(
      row(`${name}: ${span.label}`, span.start, span.duration, scale, span.kind, span.label)
    )
  }
  return h('section', {}, h('h3', {}, 'server'), h('ol', { class: 'admin-bar-timeline' }, ...rows))
}

function browserTimeline() {
  const [nav] = performance.getEntriesByType('navigation')
  if (!nav) {
    return h('section', {}, h('h3', {}, 'browser'), h('p', {}, 'no navigation timing here'))
  }
  const scale = Math.max(nav.loadEventEnd || nav.duration, 1)
  const steps = [
    ['before the request', 0, nav.requestStart],
    ['waiting for the server', nav.requestStart, nav.responseStart],
    ['receiving the page', nav.responseStart, nav.responseEnd],
    ['building the page', nav.responseEnd, nav.domContentLoadedEventEnd],
    ['loading the rest', nav.domContentLoadedEventEnd, nav.loadEventEnd]
  ]
  const rows = steps.map(([label, from, to]) =>
    row(label, from, Math.max(to - from, 0), scale, 'browser')
  )
  return h('section', {}, h('h3', {}, 'browser'), h('ol', { class: 'admin-bar-timeline' }, ...rows))
}

function statements(recording) {
  const found = recording.spans.filter((span) => span.kind === 'pg')
  const items = found.map((span) =>
    h('li', {}, h('span', { class: 'admin-bar-ms' }, ms(span.duration)), h('code', {}, span.label))
  )
  return h(
    'section',
    { class: 'admin-bar-sql' },
    h('h3', {}, `SQL, without the values bound to it (${found.length})`),
    h('ol', {}, ...items)
  )
}

function drawDetails(details, recording) {
  details.replaceChildren(
    h('div', { class: 'admin-bar-timelines' }, serverTimeline(recording), browserTimeline()),
    statements(recording)
  )
}

export function enableAdminBar(root) {
  const bar = root.getElementById('admin-bar')
  if (!bar) {
    return
  }
  const show = root.querySelector('.admin-bar-show')
  const expand = bar.querySelector('.admin-bar-expand')
  const hide = bar.querySelector('.admin-bar-hide')
  const details = bar.querySelector('.admin-bar-details')
  const totals = bar.querySelector('.admin-bar-totals')
  let recording = null

  const apply = (state) => {
    bar.hidden = state === 'hidden'
    show.hidden = state !== 'hidden'
    details.hidden = state !== 'open'
    expand.setAttribute('aria-expanded', String(state === 'open'))
    expand.textContent = state === 'open' ? 'less' : 'details'
    root.body.classList.toggle('with-admin-bar', state !== 'hidden')
    if (state === 'open' && recording) {
      drawDetails(details, recording)
    }
  }
  const choose = (state) => {
    remember(state)
    apply(state)
  }
  expand.hidden = false
  hide.hidden = false
  expand.addEventListener('click', () => choose(details.hidden ? 'open' : ''))
  hide.addEventListener('click', () => choose('hidden'))
  show.addEventListener('click', () => choose(''))
  apply(remembered())

  const load = async () => {
    const response = await fetch(`/admin/timings/${bar.dataset.request}`, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin'
    })
    if (!response.ok) {
      totals.textContent = 'these timings have expired'
      return
    }
    recording = await response.json()
    totals.textContent = totalsText(recording)
    if (!details.hidden) {
      drawDetails(details, recording)
    }
  }
  // After the load event has finished, so the browser's timing is complete.
  if (root.readyState === 'complete') {
    setTimeout(load)
  } else {
    window.addEventListener('load', () => setTimeout(load), { once: true })
  }
}
