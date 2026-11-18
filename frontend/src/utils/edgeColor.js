// 휴대폰 Safari: 화면 위(시계 줄) · 아래(주소창) 여백 색을 지금 화면의 맨 위 · 맨 아래 색에 맞춘다.
// - 위: html · body 배경색을 보고 칠한다 → 맨 위 색을 넣는다
// - 아래: 화면 아래에 붙은 고정(fixed) 요소의 색을 보고 칠한다 → 맨 아래와 같은 색의 얇은 띠(8px)를 맨 아래에 겹쳐 둔다
//   (같은 색이라 눈에는 안 보이고 누를 수도 없다. 반투명 덮개가 덮이면 띠를 숨긴다)
// PC(가운데 휴대폰 틀)에서는 아무것도 하지 않는다 — shell.css 의 회색 바탕 그대로.
const MOBILE = '(max-width: 599px), (hover: none), (pointer: coarse)'

// 그 자리에서 위로 올라가며 처음 만나는 '꽉 찬' 배경색 (투명 · 띠 자신은 건너뜀)
// stopAtTranslucent: 반투명 덮개(팝업 뒤 어두운 막 등)를 만나면 색을 정하지 않는다
function solidBg(el, stopAtTranslucent = false) {
  for (; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    if (el.dataset?.edge) continue
    const m = getComputedStyle(el).backgroundColor.match(/rgba?\(([^)]+)\)/)
    if (!m) continue
    const parts = m[1].split(/[\s,/]+/).filter(Boolean)
    const alpha = parts.length < 4 ? 1 : parseFloat(parts[3])
    if (alpha >= 1) return `rgb(${parts.slice(0, 3).join(', ')})`
    if (alpha > 0 && stopAtTranslucent) return null
  }
  return null
}

let strip = null
function bottomStrip() {
  if (!strip) {
    strip = document.createElement('div')
    strip.dataset.edge = 'bottom'
    strip.setAttribute('aria-hidden', 'true')
    Object.assign(strip.style, { position: 'fixed', left: '0', right: '0', bottom: '0', height: '8px', zIndex: '2147483647', pointerEvents: 'none' })
    document.body.appendChild(strip)
  }
  return strip
}

// 그 높이 왼쪽 끝에 있는 화면 요소 (띠 자신은 빼고)
function hit(y) {
  return document.elementsFromPoint(2, y).find((el) => !el.dataset?.edge) || null
}

function paint() {
  const mobile = window.matchMedia(MOBILE).matches
  const top = mobile ? solidBg(hit(0)) : null
  const bottom = mobile ? solidBg(hit(window.innerHeight - 1), true) : null
  for (const el of [document.documentElement, document.body]) {
    if (top) el.style.backgroundColor = top
    else el.style.removeProperty('background-color')
  }
  if (bottom) { bottomStrip().style.background = bottom; strip.style.display = '' }
  else if (strip) strip.style.display = 'none'
}

let queued = false
function schedule() {
  if (queued) return
  queued = true
  requestAnimationFrame(() => { queued = false; paint() })
}

export function startEdgeColor() {
  if (typeof window === 'undefined' || !document.elementsFromPoint) return
  const app = document.getElementById('root')
  if (app) new MutationObserver(schedule).observe(app, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'data-tab'] })
  window.addEventListener('resize', schedule)
  schedule()
}
