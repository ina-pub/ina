// Voting without a page load. The arrows are forms; this sends the same form with fetch.

import { post } from './http.js'

const CLASSES = { 1: 'likes', 0: 'unvoted', '-1': 'dislikes' }

function show(thing, direction) {
  for (const holder of thing.querySelectorAll(':scope > .midcol, :scope > .entry')) {
    holder.classList.remove('likes', 'unvoted', 'dislikes')
    holder.classList.add(CLASSES[direction])
  }
  const up = thing.querySelector(':scope > .midcol .arrow[aria-label="upvote"]')
  const down = thing.querySelector(':scope > .midcol .arrow[aria-label="downvote"]')
  up.className = `arrow ${direction === 1 ? 'upmod' : 'up'}`
  down.className = `arrow ${direction === -1 ? 'downmod' : 'down'}`
  up.value = direction === 1 ? '0' : '1'
  down.value = direction === -1 ? '0' : '-1'
}

export function enableVoting(root) {
  root.addEventListener('submit', async (event) => {
    const form = event.target.closest('form.vote')
    if (!form || !event.submitter) {
      return
    }
    event.preventDefault()
    const fields = new FormData(form)
    fields.set('dir', event.submitter.value)
    const answer = await post(form.action, fields)
    if (answer.errors.length === 0) {
      show(form.closest('.thing'), Number(event.submitter.value))
    }
  })
}
