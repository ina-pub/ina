// Expandos: a self post's text or a linked image, opened in place under the link.
// Without JS the button is a link to the post or the image.

function toggle(button) {
  const expando = button.closest('.entry').querySelector(':scope > .expando')
  const opening = button.classList.contains('collapsed')
  button.classList.toggle('collapsed', !opening)
  button.classList.toggle('expanded', opening)
  expando.hidden = !opening
  // Images load on first opening, not with the listing.
  for (const image of expando.querySelectorAll('img[data-src]')) {
    image.src = image.dataset.src
    image.removeAttribute('data-src')
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
}
