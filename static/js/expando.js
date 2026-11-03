// Expandos: a self post's text, or a linked image, video or site player, opened in place under
// the link. Without JS the button is a link to the post or what it links to.

// Media and players load on first opening, not with the listing.
function load(expando) {
  for (const media of expando.querySelectorAll('[data-src]')) {
    media.src = media.dataset.src
    media.removeAttribute('data-src')
  }
}

// Closing stops what was playing: a video pauses, and a site's player is unloaded, since the
// page cannot reach inside it. Opening again loads it afresh.
function unload(expando) {
  for (const video of expando.querySelectorAll('video')) {
    video.pause()
  }
  for (const frame of expando.querySelectorAll('iframe[src]')) {
    const empty = frame.cloneNode()
    empty.removeAttribute('src')
    empty.removeAttribute('style')
    empty.dataset.src = frame.src
    frame.replaceWith(empty)
  }
}

function toggle(button) {
  const expando = button.closest('.entry').querySelector(':scope > .expando')
  const opening = button.classList.contains('collapsed')
  button.classList.toggle('collapsed', !opening)
  button.classList.toggle('expanded', opening)
  expando.hidden = !opening
  if (opening) {
    load(expando)
  } else {
    unload(expando)
  }
}

// The height a post's frame reports, in whichever form its site sends it: X wraps it in a
// JSON-RPC call, Instagram sends a JSON string, Threads sends the number, Bluesky an object.
function reportedHeight(data) {
  let message = data
  if (typeof message === 'string') {
    try {
      message = JSON.parse(message)
    } catch {
      return null
    }
  }
  if (typeof message === 'number') {
    return message
  }
  const call = message?.['twttr.embed']
  if (call?.method === 'twttr.private.resize') {
    return call.params?.[0]?.height
  }
  if (message?.type === 'MEASURE') {
    return message.details?.height
  }
  return message?.height
}

// A message counts only from a resizing frame on this page, sent from that frame's own site, and
// only its height is used.
function resize(event) {
  const frames = document.querySelectorAll('iframe.embed-resizes[src]')
  const frame = [...frames].find((f) => f.contentWindow === event.source)
  if (!frame || new URL(frame.src).origin !== event.origin) {
    return
  }
  const height = Number(reportedHeight(event.data))
  if (height > 0 && height < 10000) {
    frame.style.height = `${Math.ceil(height)}px`
  }
}

export function enableExpandos(root) {
  root.addEventListener('click', (event) => {
    const button = event.target.closest('.expando-button')
    if (button?.closest('.entry')?.querySelector(':scope > .expando')) {
      event.preventDefault()
      toggle(button)
    }
  })
  window.addEventListener('message', resize)
}
