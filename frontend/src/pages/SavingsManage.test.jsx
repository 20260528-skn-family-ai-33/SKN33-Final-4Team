// 정기 저축 관리(마이에서 들어옴) — 무엇이든 바꾸면 [저장]. 저축 최소 금액이 처음과 달라졌고
// 지킬 돈에 정해 둔 저축보다 커졌을 때만 이번 / 다음 주기를 묻는다 (11/12)
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SavingsPage from './SavingsPage.jsx'

const { navigate, openPopup, closePopup, toast } = vi.hoisted(() => ({ navigate: vi.fn(), openPopup: vi.fn(), closePopup: vi.fn(), toast: vi.fn() }))
vi.mock('react-router', () => ({ useLocation: () => ({ state: { from: 'my' } }), useNavigate: () => navigate }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup, closePopup }) }))
vi.mock('../utils/toast.js', () => ({ toast }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <main>{children}{foot}</main> }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const reply = (data) => ({ ok: true, status: 200, json: async () => structuredClone(data) })
let host, root, savings, my
const button = (text, box = host) => [...box.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(text))
beforeEach(() => {
  vi.clearAllMocks()
  savings = { items: [{ savingId: 's1', name: '청년적금', amount: 300000, status: 'confirmed', sourceType: 'manual', transferDay: 25 }],
    summary: { minimumMonthly: 300000 } }
  my = { protect: { livingBudget: 1000000, savingsFloor: 740000, cushionMonthly: 100000 } }
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const u = String(url)
    return reply(u.includes('/api/auth/me/') ? { userId: 'u1', onboardingCompleted: true } : u.includes('/api/my/') ? my : savings)
  }))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })
const open = () => act(async () => root.render(<SavingsPage />))
const saveInPopup = (min) => act(async () => {
  if (min != null) savings.summary.minimumMonthly = min
  openPopup.mock.calls.at(-1)[0].props.onSaved()
})
const pressSave = async () => {
  const asked = openPopup.mock.calls.length
  await act(async () => button('저장').click())
  return openPopup.mock.calls.length > asked
}

it('지킬 돈 저축(74만)보다 최소 금액이 작으면 저축을 추가해도 묻지 않는다', async () => {
  await open()
  expect(button('저장')).toBeUndefined()
  await act(async () => button('+ 저축 직접 추가').click())
  await saveInPopup(330000)  // 3만 원 추가 → 최소 33만 원 < 74만 원
  expect(await pressSave()).toBe(false)
  expect(toast).toHaveBeenCalledWith('저장했어요')
  expect(navigate).toHaveBeenCalledWith('/my', { replace: true })
})

it('최소 금액이 지킬 돈 저축보다 커지면 묻고, 다음 주기부터 → 마이로', async () => {
  await open()
  await act(async () => button('+ 저축 직접 추가').click())
  await saveInPopup(800000)  // 50만 원 추가 → 최소 80만 원 > 74만 원
  expect(await pressSave()).toBe(true)
  const box = document.createElement('div'); document.body.append(box)
  const popupRoot = createRoot(box)
  await act(() => popupRoot.render(openPopup.mock.calls.at(-1)[0]))
  await act(async () => button('다음 주기부터', box).click())
  await act(() => popupRoot.unmount()); box.remove()
  expect(toast).toHaveBeenCalledWith('다음 주기부터 반영돼요')
  expect(navigate).toHaveBeenCalledWith('/my', { replace: true })
})

it('최소 금액이 줄어도 사용자가 저축을 더 크게 정해 뒀으면 묻지 않는다', async () => {
  await open()  // 지킬 돈 저축 74만 · 최소 30만
  await act(async () => button('수정').click())
  await saveInPopup(200000)
  expect(await pressSave()).toBe(false)
})

it('최소 금액에 맞춰 정해진 저축(지킬 돈 = 최소액)이면, 최소 금액이 줄 때 묻는다', async () => {
  savings.summary.minimumMonthly = 820000
  my.protect.savingsFloor = 820000
  await open()
  await act(async () => button('수정').click())
  await saveInPopup(320000)  // 50만 원 적금을 그만둠 → 최소 32만
  expect(await pressSave()).toBe(true)
})
