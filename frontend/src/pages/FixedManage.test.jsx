// 고정지출 관리(마이 · 홈에서 들어옴) — 무엇이든 바꾸면 [저장]이 생기고, 고정지출 합계가 처음과 달라졌을 때만
// 가운데 창에서 이번 주기부터(→ 지킬 돈) / 다음 주기부터(→ 돌아가기 + 토스트)를 고른다 (11/12)
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
const reply = (data) => ({ ok: true, status: 200, json: async () => structuredClone(data) })
let host, root, me, fixed
const button = (text, box = host) => [...box.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(text))

beforeEach(() => {
  vi.clearAllMocks()
  location.state = { from: 'my' }
  me = { userId: 'u1', onboardingCompleted: true }
  fixed = { items: [{ recurringId: 'f1', name: '월세', amount: 500000, status: 'confirmed', sourceType: 'manual', paymentDay: 25, category: 'HOUSING_TELECOM', categoryName: '주거·통신' }],
    summary: { confirmedMonthlyTotal: 500000, suggestedCount: 0 }, cardBills: [], cardLoading: null }
  vi.stubGlobal('fetch', vi.fn(async (url) => reply(String(url).includes('/api/auth/me/') ? me : fixed)))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })

const open = () => act(async () => root.render(<FixedPage />))
// 팝업에서 저장한 것처럼: 서버 합계를 바꾸고(선택) 팝업이 부르는 onSaved 를 부른다
const saveInPopup = (total) => act(async () => {
  if (total != null) fixed.summary.confirmedMonthlyTotal = total
  openPopup.mock.calls.at(-1)[0].props.onSaved()
})
const pick = async (label) => {
  const box = document.createElement('div'); document.body.append(box)
  const popupRoot = createRoot(box)
  await act(() => popupRoot.render(openPopup.mock.calls.at(-1)[0]))
  await act(async () => button(label, box).click())
  await act(() => popupRoot.unmount()); box.remove()
}

it('바꾼 게 없으면 [저장]이 없고, 바꾸면 생긴다', async () => {
  await open()
  expect(button('저장')).toBeUndefined()
  await act(async () => button('+ 고정지출 직접 추가').click())
  await saveInPopup(512000)
  expect(button('저장')).toBeDefined()
})

it('합계가 달라졌으면 묻고, 이번 주기부터 → 지킬 돈 화면(새 추천값부터)으로 간다', async () => {
  await open()
  await act(async () => button('수정').click())
  await saveInPopup(550000)
  await act(async () => button('저장').click())
  await pick('이번 주기부터')
  expect(closePopup).toHaveBeenCalled()
  expect(navigate).toHaveBeenCalledWith('/budgets/protect', { replace: true, state: { from: 'my', refreshRecommendation: true } })
})

it('다음 주기부터 → 들어온 곳(홈)으로 돌아가고 토스트를 띄운다', async () => {
  location.state = { from: 'home' }
  await open()
  await act(async () => button('수정').click())
  await saveInPopup(450000)
  await act(async () => button('저장').click())
  await pick('다음 주기부터')
  expect(toast).toHaveBeenCalledWith('다음 주기부터 반영돼요')
  expect(navigate).toHaveBeenCalledWith('/', { replace: true })
})

it('합계가 그대로면(이름 · 카테고리만 바꿈, 올렸다 되돌림) [저장]을 눌러도 묻지 않고 돌아간다', async () => {
  await open()
  await act(async () => button('수정').click())
  await saveInPopup(600000)
  await act(async () => button('수정').click())
  await saveInPopup(500000)  // 다시 처음 합계로
  const asked = openPopup.mock.calls.length
  await act(async () => button('저장').click())
  expect(openPopup.mock.calls.length).toBe(asked)
  expect(toast).toHaveBeenCalledWith('저장했어요')
  expect(navigate).toHaveBeenCalledWith('/my', { replace: true })
})

it('첫 예산 승인 전이면 합계가 바뀌어도 묻지 않고 돌아간다', async () => {
  me = { userId: 'u1', onboardingCompleted: false }
  await open()
  await act(async () => button('+ 고정지출 직접 추가').click())
  await saveInPopup(512000)
  const asked = openPopup.mock.calls.length
  await act(async () => button('저장').click())
  expect(openPopup.mock.calls.length).toBe(asked)
  expect(navigate).toHaveBeenCalledWith('/my', { replace: true })
})
