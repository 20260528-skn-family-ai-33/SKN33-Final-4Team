import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AdjustForm from './AdjustForm.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })
const submit = async () => act(() => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
const typeAmount = async (value) => act(() => {
  const input = host.querySelector('input[aria-label="직접 입력 (원)"]')
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
})

it('5천 원 초기값과 1만 5천 원 직접 입력을 원 단위 그대로 보낸다', async () => {
  const send = vi.fn()
  await act(() => root.render(<AdjustForm initial={{ cat: '식비', amount: 5000, dir: 'up' }} onSubmit={send} />))
  expect(host.querySelector('input').value).toBe('5000')
  await submit()
  expect(send).toHaveBeenLastCalledWith({ cat: '식비', amount: 5000, dir: 'up' })
  await typeAmount('15000')
  await submit()
  expect(send).toHaveBeenLastCalledWith({ cat: '식비', amount: 15000, dir: 'up' })
})

it('직접 입력을 임의 반올림하거나 10배로 바꾸지 않고 허용 단위 수정을 안내한다', async () => {
  const send = vi.fn()
  await act(() => root.render(<AdjustForm initial={{ cat: '식비', dir: 'up' }} onSubmit={send} />))
  await typeAmount('35555')
  await submit()
  expect(send).not.toHaveBeenCalled()
  expect(host.querySelector('[role=alert]').textContent).toContain('5,000원 단위')
  expect(host.querySelector('input').value).toBe('35555')
})
