// Comment threads and conversations: inline reply and edit, collapsing, and "load more comments".

import { h } from './h.js'
import { fragment, nodes, post } from './http.js'

function showErrors(form, errors) {
  const holder = form.querySelector('.error-holder')
  holder.replaceChildren(
    ...errors.map(([code, message, field]) =>
      h('span', { class: `error ${code} field-${field}` }, message)
    )
  )
}

function childList(thing) {
  const child = thing.querySelector(':scope > .child')
  let list = child.querySelector(':scope > .sitetable')
  if (!list) {
    list = h('div', { class: 'sitetable listing' })
    child.append(list)
  }
  return list
}

// Where a new thing goes: under its parent comment or message, else at the top of the thread.
function placeFor(parentName) {
  const parent = document.querySelector(`.thing[data-fullname="${CSS.escape(parentName)}"]`)
  if (parent?.querySelector(':scope > .child')) {
    return childList(parent)
  }
  return (
    document.querySelector('.commentarea > .nestedlisting') ||
    document.querySelector('.conversation')
  )
}

function openReply(button) {
  const thing = button.closest('.thing')
  const child = thing.querySelector(':scope > .child')
  const open = child.querySelector(':scope > form.usertext')
  if (open) {
    open.querySelector('textarea').focus()
    return
  }
  const template = document.getElementById('reply-template')
  const form = template.content.firstElementChild.cloneNode(true)
  form.querySelector('input[name="thing_id"]').value = thing.dataset.fullname
  child.prepend(form)
  form.querySelector('textarea').focus()
}

async function submitReply(form) {
  const answer = await post(form.action, new FormData(form))
  if (answer.errors.length > 0) {
    showErrors(form, answer.errors)
    return
  }
  for (const thing of answer.data.things) {
    placeFor(thing.data.parent).prepend(fragment(thing.data.content))
  }
  if (form.classList.contains('top-level')) {
    form.reset()
    showErrors(form, [])
  } else {
    form.remove()
  }
}

// The author's edit form sits hidden under the text it edits; "edit" swaps the two.
function editForm(thing) {
  return thing.querySelector(':scope > .entry form.edit-usertext')
}

function showEditing(form, editing) {
  form.hidden = !editing
  form.parentElement.querySelector(':scope > .usertext-body').hidden = editing
  if (editing) {
    form.querySelector('textarea').focus()
  } else {
    form.reset()
    showErrors(form, [])
  }
}

async function submitEdit(form) {
  const answer = await post(form.action, new FormData(form))
  if (answer.errors.length > 0) {
    showErrors(form, answer.errors)
    return
  }
  const edited = answer.data.things[0].data
  const body = form.parentElement.querySelector(':scope > .usertext-body')
  body.querySelector('.md').replaceChildren(nodes(edited.contentHTML))
  form.querySelector('textarea').defaultValue = edited.contentText
  showEditing(form, false)
}

async function loadMore(link) {
  const area = link.closest('.commentarea')
  const more = link.closest('.morechildren')
  const label = link.textContent
  link.textContent = 'loading…'
  const answer = await post('/api/morechildren', {
    link_id: link.dataset.linkId || area.dataset.linkId,
    children: link.dataset.children,
    sort: link.dataset.sort || area.dataset.sort
  })
  if (answer.errors.length > 0) {
    link.textContent = label
    return
  }
  more.remove()
  for (const thing of answer.data.things) {
    placeFor(thing.parent || thing.data.parent).append(fragment(thing.data.content))
  }
}

export function enableComments(root) {
  root.addEventListener('click', (event) => {
    const target = event.target
    if (target.matches('.buttons .edit-usertext') && editForm(target.closest('.thing'))) {
      event.preventDefault()
      showEditing(editForm(target.closest('.thing')), true)
    } else if (target.matches('form.edit-usertext .cancel')) {
      event.preventDefault()
      showEditing(target.closest('form'), false)
    } else if (target.matches('.reply-button') && document.getElementById('reply-template')) {
      event.preventDefault()
      openReply(target)
    } else if (target.matches('form.usertext .cancel')) {
      target.closest('form').remove()
    } else if (target.matches('.thing.comment > .entry .expand')) {
      const thing = target.closest('.thing')
      const collapsed = thing.classList.toggle('collapsed')
      thing.classList.toggle('noncollapsed', !collapsed)
      target.textContent = collapsed ? '+' : '−'
    } else if (target.matches('.morecomments a.button')) {
      event.preventDefault()
      loadMore(target)
    }
  })
  root.addEventListener('submit', (event) => {
    const form = event.target.closest('form.usertext')
    if (form?.classList.contains('edit-usertext')) {
      event.preventDefault()
      submitEdit(form)
    } else if (form) {
      event.preventDefault()
      submitReply(form)
    }
  })
}
