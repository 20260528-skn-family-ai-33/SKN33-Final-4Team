// 월급 설정 — 만원 단위 입력 · 칸 안 [만원|원] 전환 · 급여일 숫자 입력 (11/12 피드백)
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SalaryPage from './SalaryPage.jsx'
import { apiSend, useApi } from '../utils/api.js'

const { navigate, openPopup, closePopup, toast } = vi.hoisted(() => ({ navigate: vi.fn(), openPopup: vi.fn(), closePopup: vi.fn(), toast: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn() }))
vi.mock('react-router', () => ({ useNavigate: () => navigate, useLocation: () => ({ state: null }) }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <div>{children}{foot}</div> }))
vi.mock('../components/StepBar.jsx', () => ({ default: () => null }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup, closePopup }) }))
vi.mock('../utils/toast.js', () => ({ toast }))

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
  const salary = { takeHomeIncome: null, payday: null, pendingPaydayChange: null, pendingIncomeChange: null, incomeBasis: null, ...saved }
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

// 가운데 창(openPopup 에 넘긴 것)을 따로 그려서 버튼을 누른다
let popupHost, popupRoot
const openedPopup = async () => {
  popupHost = document.createElement('div'); document.body.append(popupHost); popupRoot = createRoot(popupHost)
  await act(() => popupRoot.render(openPopup.mock.calls.at(-1)[0]))
  return popupHost
}
const pickApply = async (label) => {
  const box = await openedPopup()
  await act(async () => [...box.querySelectorAll('button')].find((b) => b.textContent.startsWith(label)).click())
  await act(() => popupRoot.unmount()); popupHost.remove()
}
const STARTED = { userId: 'u1', onboardingCompleted: true }

it('첫 예산 승인 뒤 급여일을 바꾸고 저장하면 가운데 창으로 적용 시점을 묻는다 (11/12)', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25 }, STARTED)
  expect(day().value).toBe('25')
  await pickDay('10')
  expect(host.textContent).not.toContain('언제부터 적용할까요')  // 화면에는 묻는 칸이 없다
  await submit()
  expect(apiSend).not.toHaveBeenCalled()
  expect((await openedPopup()).textContent).toContain('언제부터 적용할까요?')
  await act(() => popupRoot.unmount()); popupHost.remove()
})

it('실수령액만 바꿔도 묻고, 다음 주기부터를 고르면 저장 뒤 토스트와 함께 돌아간다 (11/12)', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25 }, STARTED)
  await type('300')
  await submit()
  await pickApply('다음 주기부터')
  expect(closePopup).toHaveBeenCalled()
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 3000000, payday: 25, applyFrom: 'next_cycle' })
  expect(toast).toHaveBeenCalledWith('다음 주기부터 바뀌어요')
  expect(navigate).toHaveBeenCalledWith('/', { replace: true })
})

it('이번 주기부터를 고르면 지킬 돈 화면으로 간다 (11/12)', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25 }, STARTED)
  await type('300')
  await submit()
  await pickApply('이번 주기부터')
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 3000000, payday: 25, applyFrom: 'current_cycle' })
  expect(navigate).toHaveBeenCalledWith('/budgets/protect', { replace: true, state: { from: 'my', refreshRecommendation: true } })
})

it('받은 월급 기준으로 짠 주기에서 이번 주기부터 바꾸면 기준을 한 번 더 묻고, 새 월급으로 다시 짜면 지킬 돈 화면으로 간다 (11/13 검수)', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25, incomeBasis: 2200000 }, STARTED)
  await type('300')
  await submit()
  await pickApply('이번 주기부터')
  expect(apiSend).not.toHaveBeenCalled()
  const box = await openedPopup()
  expect(box.textContent).toContain('이번 주기는 실제 받은 2,200,000원 기준으로 짜여 있어요')
  await act(() => popupRoot.unmount()); popupHost.remove()
  await pickApply('3,000,000원으로 다시 짜기')
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 3000000, payday: 25, applyFrom: 'current_cycle', keepIncomeBasis: false })
  expect(navigate).toHaveBeenCalledWith('/budgets/protect', { replace: true, state: { from: 'my', refreshRecommendation: true } })
})

it('받은 월급 기준을 유지하면 이번 주기 예산은 그대로 두고 돌아간다 (11/13 검수)', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25, incomeBasis: 2200000 }, STARTED)
  await type('300')
  await submit()
  await pickApply('이번 주기부터')
  await pickApply('받은 2,200,000원 유지')
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 3000000, payday: 25, applyFrom: 'current_cycle', keepIncomeBasis: true })
  expect(toast).toHaveBeenCalledWith('월급은 바꾸고 이번 주기 예산은 그대로 둘게요')
  expect(navigate).toHaveBeenCalledWith('/', { replace: true })
})

it('첫 예산 승인 전에도 받은 월급 기준이 있으면 기준을 묻고 시점 없이 보낸다 (11/13 재검수)', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25, incomeBasis: 2200000 })
  await type('300')
  await submit()
  expect(apiSend).not.toHaveBeenCalled()
  await pickApply('받은 2,200,000원 유지')
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 3000000, payday: 25, keepIncomeBasis: true })
})

it('급여일도 이번 주기부터 바꾸면 받은 월급 기준은 묻지 않는다 (11/13 재검수)', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25, incomeBasis: 2200000 }, STARTED)
  await type('300')
  await pickDay('10')
  await submit()
  await pickApply('이번 주기부터')
  expect(openPopup).toHaveBeenCalledTimes(1)
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 3000000, payday: 10, applyFrom: 'current_cycle' })
})

it('받은 월급 기준이 있어도 다음 주기부터면 더 묻지 않는다', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25, incomeBasis: 2200000 }, STARTED)
  await type('300')
  await submit()
  await pickApply('다음 주기부터')
  expect(openPopup).toHaveBeenCalledTimes(1)
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 3000000, payday: 25, applyFrom: 'next_cycle' })
})

it('바꾼 게 없으면 묻지 않고, 예약이 있으면 예약 값을 보여 주고 그대로 둔다 (11/12)', async () => {
  await render({ takeHomeIncome: 2500000, payday: 25, pendingIncomeChange: { takeHomeIncome: 3000000, effectiveOn: '2026-11-25' } }, STARTED)
  expect(field().value).toBe('300')
  expect(host.textContent).toContain('11월 25일부터 월 실수령액이 3,000,000원으로 바뀌어요')
  await submit()
  expect(openPopup).not.toHaveBeenCalled()
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/', 'PUT', { takeHomeIncome: 3000000, payday: 25, applyFrom: 'next_cycle' })
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
