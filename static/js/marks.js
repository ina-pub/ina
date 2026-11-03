// Save and hide without a page load: the buttons are forms; this sends the same form with fetch.

import { post } from './http.js'

function flip(form) {
  const verb = form.dataset.verb
  const marking = form.action.endsWith(`/api/${verb}`)
  const thing = form.closest('.thing')
  if (verb === 'hide') {
    // A hidden link leaves the listing, and an unhidden one leaves the hidden page, as on r2.
    if (thing.nextElementSibling?.classList.contains('clearleft')) {
      thing.nextElementSibling.remove()
    }
    thing.remove()
    return
  }
  form.action = `/api/${marking ? 'un' : ''}${verb}`
  form.querySelector('button').textContent = `${marking ? 'un' : ''}${verb}`
  thing.classList.toggle('saved', marking)
}

export function enableMarks(root) {
  root.addEventListener('submit', async (event) => {
    const form = event.target.closest('form.toggle-mark')
    if (!form) {
      return
    }
    event.preventDefault()
    const answer = await post(form.action, new FormData(form))
    if (answer.errors.length === 0) {
      flip(form)
    }
  })
}
