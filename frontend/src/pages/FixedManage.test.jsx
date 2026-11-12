// 고정지출 관리(마이 · 홈에서 들어옴) — 추가 · 수정 · 그만두기를 했을 때만 [저장]이 생기고,
// 누르면 가운데 창에서 이번 주기부터(→ 지킬 돈) / 다음 주기부터(→ 돌아가기 + 토스트)를 고른다 (11/12)
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import FixedPage from './FixedPage.jsx'

const { navigate, openPopup, closePopup, toast, location } = vi.hoisted(() => ({
  navigate: vi.fn(), openPopup: vi.fn(), closePopup: vi.fn(), toast: vi.fn(), location: { state: { from: 'my' } },
}))
vi.mock('react-router', () => ({ useLocation: () => location, useNavigate: () => navigate }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup, closePopup }) }))
vi.mock('../utils/toast.js', () => ({ toast }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <main>{children}{foot}</main> }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const reply = (data) => ({ ok: true, status: 200, json: async () => data })
let host, root, me
const fixed = { items: [{ recurringId: 'f1', name: '월세', amount: 500000, status: 'confirmed', sourceType: 'manual', paymentDay: 25, category: 'HOUSING_TELECOM', categoryName: '주거·통신' }],
  summary: { confirmedMonthlyTotal: 500000, suggestedCount: 0 }, cardBills: [], cardLoading: null }
const button = (text, box = host) => [...box.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(text))

beforeEach(() => {
  vi.clearAllMocks()
  location.state = { from: 'my' }
  me = { userId: 'u1', onboardingCompleted: true }
  vi.stubGlobal('fetch', vi.fn(async (url) => reply(String(url).includes('/api/auth/me/') ? me : fixed)))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })

const open = () => act(async () => root.render(<FixedPage />))
// 팝업에 넘긴 고정지출 팝업의 onSaved 를 불러 '저장했다'를 흉내 낸다
const saveInPopup = (affectsBudget = true) => act(async () => openPopup.mock.calls.at(-1)[0].props.onSaved(affectsBudget))
const pick = async (label) => {
  const box = document.createElement('div'); document.body.append(box)
  const popupRoot = createRoot(box)
  await act(() => popupRoot.render(openPopup.mock.calls.at(-1)[0]))
  await act(async () => button(label, box).click())
  await act(() => popupRoot.unmount()); box.remove()
}

it('바꾼 게 없으면 [저장]이 없고, 추가하면 생긴다', async () => {
  await open()
  expect(button('저장')).toBeUndefined()
  await act(async () => button('+ 고정지출 직접 추가').click())
  await saveInPopup()
  expect(button('저장')).toBeDefined()
})

it('이번 주기부터 → 지킬 돈 화면(새 추천값부터)으로 간다', async () => {
  await open()
  await act(async () => button('수정').click())
  await saveInPopup()
  await act(async () => button('저장').click())
  await pick('이번 주기부터')
  expect(closePopup).toHaveBeenCalled()
  expect(navigate).toHaveBeenCalledWith('/budgets/protect', { replace: true, state: { from: 'my', refreshRecommendation: true } })
})

it('다음 주기부터 → 들어온 곳(홈)으로 돌아가고 토스트를 띄운다', async () => {
  location.state = { from: 'home' }
  await open()
  await act(async () => button('수정').click())
  await saveInPopup()
  await act(async () => button('저장').click())
  await pick('다음 주기부터')
  expect(toast).toHaveBeenCalledWith('다음 주기부터 반영돼요')
  expect(navigate).toHaveBeenCalledWith('/', { replace: true })
})

it('첫 예산 승인 전이면 [저장]을 눌러도 묻지 않고 바로 돌아간다', async () => {
  me = { userId: 'u1', onboardingCompleted: false }
  await open()
  await act(async () => button('+ 고정지출 직접 추가').click())
  await saveInPopup()
  const asked = openPopup.mock.calls.length
  await act(async () => button('저장').click())
  expect(openPopup.mock.calls.length).toBe(asked)  // 적용 시점 창을 열지 않는다
  expect(navigate).toHaveBeenCalledWith('/my', { replace: true })
})

it('이름 · 출금일 · 카테고리만 바꾸면 [저장]은 생기지만 묻지 않고 바로 돌아간다', async () => {
  await open()
  await act(async () => button('수정').click())
  await saveInPopup(false)
  const asked = openPopup.mock.calls.length
  await act(async () => button('저장').click())
  expect(openPopup.mock.calls.length).toBe(asked)
  expect(toast).toHaveBeenCalledWith('저장했어요')
  expect(navigate).toHaveBeenCalledWith('/my', { replace: true })
})
