// 머니쿠션 목표 바꾸기 창 (11/13): 목표 금액을 직접 입력하면 PATCH { goalAmount }, 되돌리기는 { goalAmount: null }
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CushionPopup from './CushionPopup.jsx'
import { apiSend } from '../utils/api.js'

const { closePopup, toast } = vi.hoisted(() => ({ closePopup: vi.fn(), toast: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn() }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))
vi.mock('../utils/toast.js', () => ({ toast }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => { vi.resetAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })
const show = (cushion, onSaved) => act(() => root.render(<CushionPopup cushion={cushion} onSaved={onSaved} />))
const input = () => host.querySelector('input[aria-label="목표 금액 (원)"]')
const type = (value) => act(() => {
  const el = input()
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
})
const submit = () => act(async () => host.querySelector('button[type="submit"]').click())
const reset = () => [...host.querySelectorAll('button')].find((b) => b.textContent.includes('되돌리기'))
const DEFAULT = { goal: 8_400_000, goalCustom: false, defaultGoal: 8_400_000 }

it('지금 목표가 입력칸에 들어 있고 기본 목표(월급의 3배)를 안내한다', async () => {
  await show(DEFAULT)
  expect(input().value).toBe('8,400,000')
  expect(host.querySelector('.mc-cushion-hint').textContent).toBe('추천 8,400,000원 · 월급의 3배')
  expect(reset()).toBeUndefined()  // 기본 목표일 때는 되돌리기 없음
})

it('금액을 입력해 저장하면 PATCH 하고 쿠션 화면을 다시 불러온다', async () => {
  apiSend.mockResolvedValue({})
  const onSaved = vi.fn()
  await show(DEFAULT, onSaved)
  await type('5,000,000')
  await submit()
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/cushion/', 'PATCH', { goalAmount: 5_000_000 })
  expect(onSaved).toHaveBeenCalledOnce()
  expect(toast).toHaveBeenCalledWith('목표를 5,000,000원으로 바꿨어요')
})

it('빈 칸 · 범위 밖은 저장하지 않고 안내한다', async () => {
  await show(DEFAULT)
  await type('')
  await submit()
  expect(host.querySelector('.mc-error').textContent).toBe('목표 금액을 입력해 주세요.')
  await type('9999')
  await submit()
  expect(host.querySelector('.mc-error').textContent).toBe('10,000원부터 1,000,000,000원까지 정할 수 있어요.')
  expect(apiSend).not.toHaveBeenCalled()
})

it('직접 정한 목표면 기본으로 되돌리기가 있고, 같은 금액 저장은 그냥 닫는다', async () => {
  apiSend.mockResolvedValue({})
  await show({ goal: 5_000_000, goalCustom: true, defaultGoal: 8_400_000 })
  await submit()
  expect(apiSend).not.toHaveBeenCalled()
  expect(closePopup).toHaveBeenCalledOnce()
  await act(async () => reset().click())
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/cushion/', 'PATCH', { goalAmount: null })
  expect(toast).toHaveBeenCalledWith('목표를 추천 금액으로 되돌렸어요')
})

it('급여가 없으면 빈 칸으로 열고 추천 금액을 숨긴다', async () => {
  await show({ goal: null, goalCustom: false, defaultGoal: null })
  expect(input().value).toBe('')
  expect(host.querySelector('.mc-cushion-hint')).toBeNull()
})
