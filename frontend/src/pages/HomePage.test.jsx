import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import HomePage from './HomePage.jsx'
import { useApi } from '../utils/api.js'
import { runFinanceSync } from '../utils/financeSync.js'
import { PATHS } from '../routes.js'
import BillCheckDialog from '../popups/BillCheckDialog.jsx'
import SyncResultPopup from '../popups/SyncResultPopup.jsx'
import CardCredentialPopup from '../popups/CardCredentialPopup.jsx'
import { toast } from '../utils/toast.js'

const { navigate, openPopup, shown } = vi.hoisted(() => ({ navigate: vi.fn(), openPopup: vi.fn(), shown: { popup: null } }))
vi.mock('react-router', () => ({ useNavigate: () => navigate, Navigate: () => null }))
vi.mock('../utils/api.js', () => ({ useApi: vi.fn() }))
vi.mock('../utils/financeSync.js', async (original) => ({ ...await original(), runFinanceSync: vi.fn() }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, headAction }) => <main>{headAction}{children}</main> }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup, closePopup: vi.fn(), popup: shown.popup }) }))  // shown.popup = 지금 떠 있는 팝업
vi.mock('../utils/toast.js', () => ({ toast: vi.fn() }))  // 11/12: 새로고침 성공은 토스트로

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const reload = vi.fn()
const refresh = () => act(async () => host.querySelector('[aria-label="새로고침"]').click())

beforeEach(async () => {
  vi.clearAllMocks()
  shown.popup = null
  useApi.mockReturnValue({ reload, data: { period: null, balance: { accountCount: 0, total: null }, pendingCount: 0, needsPaydayCount: 0, mustPay: [], budget: null } })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(() => root.render(<HomePage />))
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

// 11/12: 불러온 기관은 홈에 줄로 남기지 않고 토스트로 잠깐, 실패한 기관은 가운데 창으로
it('연결 전부 불러온 뒤 홈 데이터를 다시 받고, 불러온 곳은 토스트 · 실패한 곳은 창으로 연결 관리를 안내한다', async () => {
  runFinanceSync.mockResolvedValue({ items: [
    { organizationName: '신한은행', status: 'succeeded', newTransactionCount: 2 },
    { organizationName: '롯데카드', status: 'succeeded' },
    { organizationName: '현대카드', status: 'failed', error: { message: '다시 연결이 필요해요.' } },
  ] })
  await refresh()
  expect(runFinanceSync).toHaveBeenCalledWith()
  expect(reload).toHaveBeenCalledOnce()
  expect(toast).toHaveBeenCalledExactlyOnceWith('신한은행 · 새 거래 2건을 불러왔어요\n롯데카드 · 불러왔어요', 2800)
  expect(host.textContent).not.toContain('신한은행')           // 홈 화면에는 남지 않는다

  const popup = openPopup.mock.calls.at(-1)[0]
  expect(popup.type).toBe(SyncResultPopup)
  expect(popup.props.lines).toEqual([{ name: '현대카드', ok: false, text: '다시 연결이 필요해요.' }])
  await act(() => root.render(popup))
  expect(host.textContent).toContain('현대카드 · 다시 연결이 필요해요.')
  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent.includes('연결 관리에서 확인')).click())
  expect(navigate).toHaveBeenCalledWith(PATHS.connections)
})

// 리뷰: 카드번호 입력 팝업을 그냥 닫아도 앞에서 실패한 곳 안내가 사라지지 않는다 (예전에는 홈에 줄로 남았음)
it('카드번호 입력 팝업을 닫으면 남은 카드사는 묻지 않고 실패한 곳을 보여 준다', async () => {
  runFinanceSync.mockResolvedValue({ items: [
    { connectionId: 'h', organizationCode: '0302', organizationName: '현대카드', status: 'failed', error: { fields: { cardNo: ['카드번호를 넣어 주세요.'] } } },
    { connectionId: 's', organizationName: '신한카드', status: 'failed', error: { message: '카드사 점검 중이에요.' } },
  ] })
  await refresh()
  const ask = openPopup.mock.calls.at(-1)[0]
  expect(ask.type).toBe(CardCredentialPopup)              // 카드번호 입력부터
  shown.popup = ask
  await act(() => root.render(<HomePage />))
  expect(openPopup).toHaveBeenCalledTimes(1)
  shown.popup = null                                       // ×로 닫음 (onDone 없이)
  await act(() => root.render(<HomePage />))
  const popup = openPopup.mock.calls.at(-1)[0]
  expect(popup.type).toBe(SyncResultPopup)
  expect(popup.props.lines.map((l) => l.name)).toEqual(['신한카드'])
})

it('모두 불러오면 토스트만 띄우고 창은 열지 않는다', async () => {
  runFinanceSync.mockResolvedValue({ items: [{ organizationName: '농협은행', status: 'succeeded', newTransactionCount: 2 }] })
  await refresh()
  expect(toast).toHaveBeenCalledExactlyOnceWith('농협은행 · 새 거래 2건을 불러왔어요', 2000)
  expect(openPopup).not.toHaveBeenCalled()
})

it('불러오기를 못 하면 이유를 보여 주고 다시 받지 않는다', async () => {
  runFinanceSync.mockRejectedValue(new Error('테스트 환경에서는 금융 연결 · 다시 불러오기를 쓸 수 없어요.'))
  await refresh()
  expect(reload).not.toHaveBeenCalled()
  expect(host.querySelector('[aria-label="새로고침"]').disabled).toBe(false)
  const popup = openPopup.mock.calls.at(-1)[0]                // 11/12: 이유는 홈 위 줄 대신 가운데 창으로
  expect(popup.type).toBe(SyncResultPopup)
  await act(() => root.render(popup))
  expect(host.querySelector('[role="alert"]').textContent).toContain('테스트 환경에서는')
})

it('예산을 넘긴 카테고리마다 종 알림에 한 줄이 생기고 누르면 예산 화면으로 간다', async () => {
  const budget = { livingBudget: 300000, livingLeft: 0, livingPct: 104, spare: 0, daily: 0, daysLeft: 10, outOfBudgetAllocated: 0,
    outOfBudget: { total: 0, categories: [] },
    overCategories: [{ category: 'FOOD', categoryName: '식비', allocated: 300000, spent: 312000, over: 12000 }] }
  useApi.mockReturnValue({ reload, data: { period: null, balance: { accountCount: 0, total: null }, pendingCount: 0, needsPaydayCount: 0, mustPay: [], budget } })
  await act(() => root.render(<HomePage />))
  await act(async () => host.querySelector('[aria-label="알림 1개"]').click())
  const [item] = openPopup.mock.calls.at(-1)[0].props.items
  expect(item).toMatchObject({ key: 'over-FOOD', icon: 'Utensils', text: '식비 예산을 12,000원 넘었어요' })
  item.go()
  expect(navigate).toHaveBeenCalledWith(PATHS.budget)
})

it('생활비 예산 대비 큰 지출 · 이체마다 종 알림에 한 줄이 생기고 누르면 거래 내역으로 간다', async () => {
  const budget = { livingBudget: 1000000, livingLeft: 500000, livingPct: 50, spare: 0, daily: 0, daysLeft: 10, outOfBudgetAllocated: 0,
    outOfBudget: { total: 0, categories: [] }, overCategories: [],
    bigSpendings: [
      { transactionId: 't1', occurredOn: '2026-11-10', merchantName: '홍길동', amount: 300000, kind: 'transfer', category: null, categoryName: null },
      { transactionId: 't2', occurredOn: '2026-11-08', merchantName: '', amount: 150000, kind: 'spending', category: 'FOOD', categoryName: '식비' },
    ] }
  useApi.mockReturnValue({ reload, data: { period: null, balance: { accountCount: 0, total: null }, pendingCount: 0, needsPaydayCount: 0, mustPay: [], budget } })
  await act(() => root.render(<HomePage />))
  await act(async () => host.querySelector('[aria-label="알림 2개"]').click())
  const items = openPopup.mock.calls.at(-1)[0].props.items
  expect(items.map((n) => [n.key, n.icon, n.text])).toEqual([
    ['big-t1', 'Send', '11.10 홍길동 300,000원 · 큰 이체가 있었어요'],
    ['big-t2', 'Utensils', '11.08 150,000원 · 큰 지출이 있었어요'],
  ])
  items[0].go()
  expect(navigate).toHaveBeenCalledWith(PATHS.txs)
})

// 11/12: 서버가 '지금은 청구서가 나온 카드'를 주면 띠 문구가 바뀌고, 시트 · 확인 창 없이 바로 재추천으로
it('청구서가 이미 나온 카드가 있으면 띠를 바꾸고 바로 재추천 경로로 간다', async () => {
  const initial = useApi.mock.results[0].value.data
  useApi.mockReturnValue({ reload, data: { ...initial, budget: {
    daily: 20000, livingBudget: 800000, livingLeft: 700000, livingPct: 12, spare: 100000,
    cardNotes: [{ cardId: 'card-1', reason: 'first_bill_unavailable' }], cardBillsReady: ['card-1'],
  } } })
  await act(() => root.render(<HomePage />))
  expect(host.textContent).not.toContain('지난 카드값이 아직 반영 안 됐어요')
  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent.includes('새 카드 청구서가 나왔어요')).click())
  expect(openPopup).not.toHaveBeenCalled()
  expect(navigate).toHaveBeenCalledWith(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })
})

it('시작한 예산의 카드값 미반영 안내와 같은 주기 재추천 경로를 보여 준다', async () => {
  const initial = useApi.mock.results[0].value.data
  useApi.mockReturnValue({ reload, data: { ...initial, budget: {
    daily: 20000, livingBudget: 800000, livingLeft: 700000, livingPct: 12, spare: 100000,
    cardNotes: [{ cardId: 'card-1', reason: 'first_bill_unavailable' }],
  } } })
  await act(() => root.render(<HomePage />))
  expect(host.textContent).toContain('지난 카드값이 아직 반영 안 됐어요')
  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent.includes('지난 카드값이 아직 반영 안 됐어요')).click())
  await act(() => root.render(openPopup.mock.calls.at(-1)[0]))
  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent.includes('청구서 확인하러 가기')).click())
  // 11/11: 바로 이동하지 않고 가운데 창에서 그 카드 청구서를 불러온다. 새 청구서가 있을 때만 재추천 경로로
  const dialog = openPopup.mock.calls.at(-1)[0]
  expect(dialog.type).toBe(BillCheckDialog)
  expect(dialog.props.cardIds).toEqual(['card-1'])
  expect(navigate).not.toHaveBeenCalled()
  dialog.props.onFound()
  expect(navigate).toHaveBeenCalledWith(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })
})
