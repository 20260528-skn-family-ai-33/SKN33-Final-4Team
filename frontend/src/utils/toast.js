// 화면 위에 잠깐 떴다가 사라지는 안내 (토스트, 11/9) — Layout 의 #mc-toast 에 글자만 넣고 2초 뒤 지운다
// 팝업이 떠 있어도 그 위에 보인다 (CSS z-index). 같은 안내를 연달아 부르면 시간을 다시 잰다
let timer
export function toast(text, ms = 2000) {
  const el = document.getElementById('mc-toast')
  if (!el) return
  el.textContent = ''
  void el.offsetWidth  // 같은 글자여도 나타나는 효과를 다시 시작
  el.textContent = text
  clearTimeout(timer)
  timer = setTimeout(() => { el.textContent = '' }, ms)
}
