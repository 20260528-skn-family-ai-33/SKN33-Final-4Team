// 매월 며칠 입력칸 (11/12) — 숫자 입력 · [직접 입력|말일] · 비워도 되는 칸은 '모름'
import { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import DayInput from './DayInput.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const sent = []  // DayInput 이 onChange 로 보낸 값
const last = () => sent.at(-1)
function Box({ start = '', optional }) {
  const [v, setV] = useState(start)
  return <DayInput id="d" what="출금일" optional={optional} value={v} onChange={(d) => { sent.push(d); setV(d) }} />
}
beforeEach(() => { sent.length = 0; host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })
const box = () => host.querySelector('#d')
const type = (text) => act(() => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(box(), text)
  box().dispatchEvent(new Event('input', { bubbles: true }))
})
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent === text)

it('비워도 되는 칸은 모름으로 보이고, 숫자만 2자리까지 받는다', async () => {
  await act(() => root.render(<Box optional />))
  expect(box().placeholder).toBe('모름')
  expect(host.querySelector('.mc-day-suffix')).toBeNull()  // 빈 칸엔 '일'을 붙이지 않는다
  await type('0a5x9')
  expect(last()).toBe('59')
  expect(host.textContent).toContain('출금일은 1~31 중에서 입력해 주세요.')
  await type('30')
  expect(host.textContent).toContain('30일이 없는 달은 말일을 출금일로 계산해요.')
})

it('[말일]은 31 로 잠그고, [직접 입력]으로 돌아가면 비운다', async () => {
  await act(() => root.render(<Box start="10" />))
  await act(async () => button('말일').click())
  expect(last()).toBe('31')
  expect(box().value).toBe('말일')
  expect(box().disabled).toBe(true)
  await act(async () => button('직접 입력').click())
  expect(last()).toBe('')
  expect(box().disabled).toBe(false)
})
