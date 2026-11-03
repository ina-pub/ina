// Posting forms to the API the way r2's JS did: api_type=json, with the CSRF token as X-Modhash.

export function modhash() {
  const meta = document.querySelector('meta[name="uh"]')
  return meta ? meta.content : ''
}

export async function post(path, fields) {
  const body = fields instanceof FormData ? fields : new FormData()
  if (!(fields instanceof FormData)) {
    for (const [name, value] of Object.entries(fields)) {
      body.append(name, value)
    }
  }
  body.set('api_type', 'json')
  const response = await fetch(path, {
    method: 'POST',
    body: new URLSearchParams(body),
    headers: { 'X-Modhash': modhash(), Accept: 'application/json' },
    credentials: 'same-origin'
  })
  const answer = await response.json()
  return answer.json || { errors: [[`HTTP_${response.status}`, response.statusText, null]] }
}

// Server-rendered HTML for one thing; Jinja escaped every user value in it.
export function fragment(html) {
  const template = document.createElement('template')
  template.innerHTML = html.trim()
  return template.content.firstElementChild
}

// Several nodes of server-rendered HTML, such as a comment body the sanitizer already cleaned.
export function nodes(html) {
  const template = document.createElement('template')
  template.innerHTML = html
  return template.content
}
