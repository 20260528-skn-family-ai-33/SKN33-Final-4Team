// 월급 설정 — 만원 단위 입력 · 칸 안 [만원|원] 전환 · 급여일 숫자 입력 (11/12 피드백)
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SalaryPage from './SalaryPage.jsx'
import { apiSend, useApi } from '../utils/api.js'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn() }))
vi.mock('react-router', () => ({ useNavigate: () => navigate, useLocation: () => ({ state: null }) }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <div>{children}{foot}</div> }))
vi.mock('../components/StepBar.jsx', () => ({ default: () => null }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => {
  vi.clearAllMocks()
  apiSend.mockResolvedValue({})
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

const render = (saved, me = { userId: 'u1', onboardingCompleted: false }) => {
  const salary = { takeHomeIncome: null, payday: null, pendingPaydayChange: null, ...saved }
  useApi.mockImplementation((path) => ({ data: path === '/api/auth/me/' ? me : salary, error: null }))
  return act(() => root.render(<SalaryPage />))
}
const field = () => host.querySelector('#mc-salary')
const type = (value) => act(() => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field(), value)
  field().dispatchEvent(new Event('input', { bubbles: true }))
})
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.trim() === text)
const submit = () => act(async () => host.querySelector('form').requestSubmit())
const day = () => host.querySelector('#mc-payday')
const pickDay = (d) => act(() => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(day(), String(d))
  day().dispatchEvent(new Event('input', { bubbles: true }))
})

it('처음엔 만원 단위로 받고, 서버에는 원 단위로 보낸다', async () => {
  await render()
  expect(host.textContent).toContain('만원')
  await type('285')
  await pickDay(25)
  await submit()
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 2850000, payday: 25 })
})

it('[원]을 누르면 원 단위로 정확히 받는다 · 단위만 바꾸면 금액은 그대로', async () => {
  await render()
  await type('285')
  await act(async () => button('원').click())
  expect(field().value).toBe('2,850,000')
  await type('2,853,420')
  await act(async () => button('만원').click())
  expect(field().value).toBe('285')
  await act(async () => button('원').click())
  expect(field().value).toBe('2,853,420')  // 만원으로 봤다가 돌아와도 원 단위 금액이 남는다
})

it('저장된 금액이 만원으로 안 떨어지면 원 단위로 연다', async () => {
  await render({ takeHomeIncome: 2853420, payday: 25 })
  expect(field().value).toBe('2,853,420')
  expect(button('원').getAttribute('aria-pressed')).toBe('true')
})

it('급여일은 숫자로 입력 · 1~31 이 아니면 칸 아래에 알려 주고 저장하지 않는다', async () => {
  await render()
  await type('250')
  await pickDay('3a2')
  expect(day().value).toBe('32')
  expect(host.textContent).toContain('급여일은 1~31 중에서 입력해 주세요.')
  await submit()
  expect(apiSend).not.toHaveBeenCalled()
  await pickDay('0')
  expect(day().value).toBe('')
  await pickDay('30')
  expect(host.textContent).not.toContain('1~31 중에서 입력해 주세요.')
  expect(host.textContent).toContain('30일이 없는 달은 말일을 급여일로 계산해요.')
  await pickDay('31')  // 직접 31 을 치면 숫자 그대로 + 안내
  expect(day().value).toBe('31')
  expect(host.textContent).toContain('31일이 없는 달은 말일을 급여일로 계산해요.')
  await submit()
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 2500000, payday: 31 })
})

it('첫 예산 승인 뒤 저장된 급여일을 바꾸면 적용 시점을 묻는다', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25 }, { userId: 'u1', onboardingCompleted: true })
  expect(day().value).toBe('25')
  expect(host.textContent).not.toContain('언제부터 적용할까요')
  await pickDay('10')
  expect(host.textContent).toContain('언제부터 적용할까요')
})

it('온보딩(첫 예산 승인 전)에는 급여일을 바꿔도 적용 시점을 묻지 않고 그대로 보낸다', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25 })
  await pickDay('10')
  expect(host.textContent).not.toContain('언제부터 적용할까요')
  await submit()
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 2500000, payday: 10 })
})

it('[말일]을 누르면 급여일이 말일(31)이 되고 칸은 말일로 잠긴다 · [직접 입력]으로 돌아가면 비운다', async () => {
  await render()
  await type('250')
  await act(async () => button('말일').click())
  expect(day().value).toBe('말일')
  expect(day().disabled).toBe(true)
  await submit()
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 2500000, payday: 31 })
  await act(async () => button('직접 입력').click())
  expect(day().value).toBe('')
  expect(day().disabled).toBe(false)
})

it('저장된 급여일이 31이면 말일로 보인다', async () => {
  await render({ takeHomeIncome: 2500000, payday: 31 })
  expect(day().value).toBe('말일')
  expect(button('말일').getAttribute('aria-pressed')).toBe('true')
})
