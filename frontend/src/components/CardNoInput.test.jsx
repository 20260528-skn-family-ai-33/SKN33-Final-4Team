// 카드번호 칸 — 가린 가운데 자리를 지워도 그 자리 숫자만 지워지고, 커서가 그 자리에 남는다 (PR #101 리뷰)
import { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import CardNoInput from './CardNoInput.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root, latest
function Box({ start }) {
  const [value, setValue] = useState(start)
  return <CardNoInput value={value} onChange={(next) => { latest = next; setValue(next) }} />
}
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })
// 브라우저처럼: 글자를 바꾸고 커서를 바뀐 자리 뒤에 둔 채 input 이벤트
const typeAt = (el, value, caret, inputType) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value)
  el.setSelectionRange(caret, caret)
  el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType }))
})

it('다섯 번째 자리를 연달아 두 번 지우면 5 · 6 이 지워진다', async () => {
  await act(() => root.render(<Box start="1234567890123456" />))
  const el = host.querySelector('input')
  el.focus()
  expect(el.value).toBe('1234 - •••• - •••• - ••••')
  // 다섯 번째 자리(첫 •) 뒤 커서 = 8 → Backspace
  await typeAt(el, '1234 - ••• - •••• - ••••', 7, 'deleteContentBackward')
  expect(latest).toBe('123467890123456')
  expect(el.value).toBe('1234 - •••• - •••• - •••')
  expect(el.selectionStart).toBe(4)  // 지운 자리('1234|') — 칸 끝으로 가지 않는다
  // 같은 자리('1234|')에서 Delete → 띄어쓰기만 지워지지만 뒤 숫자(6)가 지워진다
  await typeAt(el, '1234- •••• - •••• - •••', 4, 'deleteContentForward')
  expect(latest).toBe('12347890123456')
  expect(el.selectionStart).toBe(4)
})
