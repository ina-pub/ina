// Build DOM nodes without innerHTML: h('span', { class: 'error' }, 'text').

export function h(tag, attributes = {}, ...children) {
  const node = document.createElement(tag)
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, value)
  }
  for (const child of children) {
    node.append(child)
  }
  return node
}
