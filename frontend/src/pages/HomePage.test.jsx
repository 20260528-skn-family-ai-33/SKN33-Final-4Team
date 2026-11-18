import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import HomePage from './HomePage.jsx'
import { useApi } from '../utils/api.js'
import { runFinanceSync } from '../utils/financeSync.js'
import { PATHS } from '../routes.js'
import BillCheckDialog from '../popups/BillCheckDialog.jsx'
import SyncResultPopup from '../popups/SyncResultPopup.jsx'
import SalaryGapPopup from '../popups/SalaryGapPopup.jsx'
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
// 11/17 홈 재설계: 예산 응답 공통 모양 — 각 테스트는 바꿀 칸만 덮어쓴다
const BUDGET = { livingBudget: 1100000, livingLeft: 620000, livingSpent: 480000, livingPct: 44, spare: 50000, daily: 30000, daysLeft: 20,
  outOfBudgetAllocated: 0, outOfBudget: { total: 0, categories: [] }, overCategories: [], bigSpendings: [], nag: null, cardNotes: [], cardBillsReady: [],
  week: { budget: 178750, spent: 95000, left: 83750 }, forecast: { total: 1240000, gap: -140000 },
  topCategories: [
    { category: 'FOOD', categoryName: '식비', spent: 210000, allocated: 250000, over: false },
    { category: 'CULTURE_LEISURE', categoryName: '문화·여가', spent: 120000, allocated: 100000, over: true },
    { category: 'TRANSPORT_CAR', categoryName: '교통·차량', spent: 72000, allocated: 150000, over: false },
  ],
  vsPrev: { diff: 60000 } }
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
  const budget = { ...BUDGET, livingBudget: 300000, livingLeft: 0, livingPct: 104, spare: 0, daily: 0, daysLeft: 10, outOfBudgetAllocated: 0,
    outOfBudget: { total: 0, categories: [] },
    overCategories: [{ category: 'FOOD', categoryName: '식비', allocated: 300000, spent: 312000, over: 12000 }] }
  useApi.mockReturnValue({ reload, data: { period: null, balance: { accountCount: 0, total: null }, pendingCount: 0, needsPaydayCount: 0, mustPay: [], budget } })
  await act(() => root.render(<HomePage />))
  await act(async () => host.querySelector('[aria-label="알림 1개"]').click())
  const [item] = openPopup.mock.calls.at(-1)[0].props.items
  expect(item).toMatchObject({ key: 'over-FOOD', title: '예산 초과', icon: 'Utensils', text: '식비 예산을 12,000원 넘었어요' })
  item.go()
  expect(navigate).toHaveBeenCalledWith(PATHS.budget)
})

// 11/13: 예산을 자주 바꿨다는 문구(nag)는 예산 화면 대신 종 알림 한 줄
it('예산을 자주 바꾸면 종 알림에 한 줄이 생기고 누르면 예산 화면으로 간다', async () => {
  const nag = '이번 달 예산을 여러 번 바꿨어요'
  const budget = { ...BUDGET, livingBudget: 300000, livingLeft: 100000, livingPct: 67, spare: 0, daily: 0, daysLeft: 10, outOfBudgetAllocated: 0,
    outOfBudget: { total: 0, categories: [] }, overCategories: [], nag }
  useApi.mockReturnValue({ reload, data: { period: null, balance: { accountCount: 0, total: null }, pendingCount: 0, needsPaydayCount: 0, mustPay: [], budget } })
  await act(() => root.render(<HomePage />))
  await act(async () => host.querySelector('[aria-label="알림 1개"]').click())
  const [item] = openPopup.mock.calls.at(-1)[0].props.items
  expect(item).toMatchObject({ key: 'nag', title: '예산 변경', mark: '!', text: nag })
  item.go()
  expect(navigate).toHaveBeenCalledWith(PATHS.budget)
})

it('감지된 큰 지출 중 가장 큰 한 건만 알리고 승인 예산의 조정 대화로 이동한다', async () => {
  await show({ budget: { budgetId: 'approved-budget', bigSpendings: [
    { transactionId: 't1', occurredOn: '2026-11-10', amount: 200000, kind: 'spending', categoryName: '식비' },
    { transactionId: 't2', occurredOn: '2026-11-09', merchantName: '무신사', amount: 300000, kind: 'spending', categoryName: '패션·쇼핑' },
  ] } })
  await act(async () => host.querySelector('[aria-label="알림 1개"]').click())
  const [item] = openPopup.mock.calls.at(-1)[0].props.items
  expect(item).toMatchObject({
    key: 'big-spending',
    title: '무신사 · 300,000원',
    text: '생활비 예산을 넘길 수 있어요. 지금 조정할까요?',
  })
  item.go()
  expect(navigate).toHaveBeenCalledWith(PATHS.assistant, {
    state: { budgetContext: { baseBudgetId: 'approved-budget', mode: 'NORMAL_ADJUSTMENT' } },
  })
})

it('머니쿠션 적립일과 잔액 확인을 종 알림에 표시하고 머니쿠션 화면으로 이동한다', async () => {
  await show({ budget: null, cushionNotices: { depositDue: true, balanceLow: true } })
  await act(async () => host.querySelector('[aria-label="알림 2개"]').click())
  const [deposit, balance] = openPopup.mock.calls.at(-1)[0].props.items
  expect(deposit.text).toBe('쿠션 모으기 잊지 마세요!')
  expect(balance.text).toContain('머니쿠션을 잘 모으고 있는지 확인해볼까요?')
  expect([deposit.group, deposit.act, balance.group]).toEqual(['act', '쿠션 보기', 'check'])  // 11/18 종 팝업 묶음
  deposit.go()
  balance.go()
  expect(navigate.mock.calls).toEqual([[PATHS.fund], [PATHS.fund]])
})

it.each([
  ['2027-02-01', 9, null],
  ['2027-02-01', 8, '설날'],
  ['2026-09-25', 14, '추석'],
  ['2026-10-01', 6, null],
  ['2026-12-25', 8, null],
  ['2026-12-25', 7, '연말'],
  ['2027-01-25', 24, null],
])('급여일 %s에 %d일 남았을 때 명절·연말 예산 조정 안내를 표시한다', async (nextPayday, dday, season) => {
  await show({ period: { ...period, nextPayday, dday }, budget: { budgetId: 'approved-budget' } })
  await act(async () => host.querySelector(season ? '[aria-label="알림 1개"]' : '[aria-label="알림"]').click())
  const items = openPopup.mock.calls.at(-1)[0].props.items
  if (season) {
    expect(items[0].text).toBe(`${season}이 다가오고 있어요. 지출에 대비해 같이 예산을 조정해볼까요?`)
    items[0].go()
    expect(navigate).toHaveBeenCalledWith(PATHS.assistant, {
      state: { budgetContext: { baseBudgetId: 'approved-budget', mode: 'NORMAL_ADJUSTMENT' } },
    })
  } else {
    expect(items).toEqual([])
  }
})

// 11/12: 서버가 '지금은 청구서가 나온 카드'를 주면 띠 문구가 바뀌고, 시트 · 확인 창 없이 바로 재추천으로
it('청구서가 이미 나온 카드가 있으면 띠를 바꾸고 바로 재추천 경로로 간다', async () => {
  const initial = useApi.mock.results[0].value.data
  useApi.mockReturnValue({ reload, data: { ...initial, budget: { ...BUDGET,
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
  useApi.mockReturnValue({ reload, data: { ...initial, budget: { ...BUDGET,
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

it('받은 월급이 등록 금액과 다르면 주기 밑 띠와 알림으로 묻고, 누르면 월급 차이 창을 연다 (11/13)', async () => {
  const salaryGap = { diff: -300000, received: 2200000, registered: 2500000, basis: null }
  const period = { startDate: '2026-10-25', endDate: '2026-11-24', nextPayday: '2026-11-25', dday: 12, salaryState: 'received', salaryGap }
  useApi.mockReturnValue({ reload, data: { period, balance: { accountCount: 1, total: 0 }, pendingCount: 0, needsPaydayCount: 0, mustPay: [], budget: null } })
  await act(() => root.render(<HomePage />))
  const strip = host.querySelector('.mc-home3-gap')
  expect(strip.textContent).toContain('월급이 300,000원 덜 들어왔어요')
  await act(async () => strip.click())
  const popup = openPopup.mock.calls.at(-1)[0]
  expect(popup.type).toBe(SalaryGapPopup)
  expect(popup.props.gap).toEqual(salaryGap)
  expect(host.querySelector('[aria-label="알림 1개"]')).not.toBeNull()
})

// ---- 11/17 홈 재설계 (시안 B) ----
const period = { startDate: '2026-10-25', endDate: '2026-11-24', nextPayday: '2026-11-25', dday: 20, salaryState: 'received' }
const show = async (over = {}) => {
  const { budget = {}, ...rest } = over
  useApi.mockReturnValue({ reload, data: { period, balance: { accountCount: 2, total: 2180000, unknownCount: 0, reloginCount: 0, asOf: null },
    pendingCount: 0, needsPaydayCount: 0, mustPay: [], upcoming: { total: 0, shortfall: 0, items: [] }, budget: budget === null ? null : { ...BUDGET, ...budget }, ...rest } })
  await act(() => root.render(<HomePage />))
}
const due = (dueId, dueDate, amount) => ({ dueId, type: 'recurring', sourceId: 'r', name: `고정-${dueId}`, dueDate, amount, paymentState: 'unpaid', isEstimated: false, userChecked: false })

it('종을 열면 배지만 사라지고 목록과 새 알림은 계속 볼 수 있다', async () => {
  await show({ pendingCount: 2, needsPaydayCount: 1 })
  await act(async () => host.querySelector('[aria-label="알림 2개"]').click())
  expect(host.querySelector('.mc-bell-n')).toBeNull()
  expect(openPopup.mock.calls.at(-1)[0].props.items.map((item) => item.key)).toEqual(['pending', 'payday'])

  await act(async () => host.querySelector('[aria-label="알림"]').click())
  expect(openPopup.mock.calls.at(-1)[0].props.items).toHaveLength(2)

  await show({ pendingCount: 2, needsPaydayCount: 1, cushionNotices: { balanceLow: true } })
  expect(host.querySelector('.mc-bell-n').textContent).toBe('1')
  await act(async () => host.querySelector('[aria-label="알림 1개"]').click())
  expect(openPopup.mock.calls.at(-1)[0].props.items).toHaveLength(3)
})

it('같은 회원이 홈을 다시 열면 읽은 상태를 복원하고 다음 주기 적립일은 새 알림으로 센다', async () => {
  const userId = 'notice-reader'
  window.localStorage.removeItem(`moneycushion.homeNotices.${userId}`)
  const data = { userId, cushionNotices: { depositDue: true } }
  await show(data)
  await act(async () => host.querySelector('[aria-label="알림 1개"]').click())
  await act(() => root.render(<HomePage key="reopen" />))
  expect(host.querySelector('.mc-bell-n')).toBeNull()

  await show({ ...data, userId: 'another-notice-reader' })
  expect(host.querySelector('.mc-bell-n').textContent).toBe('1')

  await show({ ...data, period: { ...period, startDate: '2026-11-25', nextPayday: '2026-12-25' } })
  expect(host.querySelector('.mc-bell-n').textContent).toBe('1')
})

it('이번 주 쓸 수 있는 돈은 띠 제목의 큰 숫자, 사용률 · 이달 남은 생활비는 그 아래 흰 시트에 나온다 (11/18 시안 B)', async () => {
  await show()
  const head = host.querySelector('.mc-home3-hero')
  const big = host.querySelector('.mc-tabhero .mc-home3-big')
  expect(big.textContent).toBe('83,750원')
  expect(big.getAttribute('aria-label')).toBe('이번 주 쓸 수 있는 돈 8만 3,750원')
  expect(head.querySelector('[role=progressbar]').getAttribute('aria-valuenow')).toBe('53')   // 95,000 ÷ 178,750
  expect(head.textContent).toContain('이달 남은 생활비')
  expect(head.textContent).toContain('620,000원')
  expect(head.textContent).toContain('이대로 쓰면 월급날140,000원 부족')
})

it('forecast 가 null(주기 첫날)이면 전망 칸만 사라지고 나머지는 그대로다', async () => {
  await show({ budget: { forecast: null } })
  const head = host.querySelector('.mc-home3-hero')
  expect(head.textContent).not.toContain('이대로 쓰면')
  expect(head.textContent).toContain('이달 남은 생활비')
  expect(host.querySelector('.mc-home3-big').textContent).toBe('83,750원')
})

it('이전 홈 응답에서도 승인 예산을 표시하고 빠진 주간 금액이나 지출 전망을 만들지 않는다', async () => {
  await show({ budget: { budgetId: 'approved', week: undefined, topCategories: undefined, forecast: undefined,
    vsPrev: undefined, bigSpendings: [{ transactionId: 'legacy', amount: 110000 }] }, upcoming: undefined })
  expect(host.querySelector('.mc-tabhero').textContent).toContain('이번 주 쓸 수 있는 돈—')
  expect(host.textContent).toContain('620,000원')
  expect(host.textContent).not.toContain('아직 이번 주기 생활비 지출이 없어요')
  expect(host.querySelector('.mc-bell-n')).toBeNull()
})

it('남은 주간 예산이 0원으로 제한된 응답에서도 실제 초과액을 표시한다', async () => {
  await show({ budget: { week: { budget: 180000, spent: 320000, left: 0 } } })
  expect(host.querySelector('.mc-tabhero').textContent).toContain('140,000원넘게 썼어요')
})

it('흰 시트의 [예산 자세히 보기]를 누르면 예산 화면으로 간다 (11/18)', async () => {
  await show()
  await act(async () => [...host.querySelectorAll('.mc-home3-hero button')].find((b) => b.textContent === '예산 자세히 보기').click())
  expect(navigate).toHaveBeenCalledWith(PATHS.budget)
})

it('많이 쓴 카테고리 3개 타일 · 예산 넘긴 항목 표시 · 지난달 대비 · 가장 큰 지출을 보여 준다', async () => {
  await show({ budget: { bigSpendings: [
    { transactionId: 't1', occurredOn: '2026-11-03', merchantName: '무신사', amount: 198000, kind: 'spending', category: 'FASHION_SHOPPING', categoryName: '패션·쇼핑' },
    { transactionId: 't2', occurredOn: '2026-11-04', merchantName: '마트', amount: 175000, kind: 'spending', category: 'HOUSEHOLD', categoryName: '생활용품' },
  ] } })
  const rows = [...host.querySelectorAll('.mc-home3-tile')].map((r) => r.textContent)
  expect(rows).toEqual(['식비210,000원', '문화·여가120,000원20,000원 초과', '교통·차량72,000원'])  // 11/18: 비율 글은 넘었을 때만
  expect(host.querySelectorAll('.mc-home3-tile.is-over')).toHaveLength(1)
  expect(host.textContent).toContain('지난달 이맘때보다 60,000원 더 썼어요')
  const big = [...host.querySelectorAll('button')].find((b) => b.textContent.includes('가장 큰 지출'))
  expect(big.textContent).toContain('11.03 무신사 198,000원')
  await act(async () => big.click())
  expect(navigate).toHaveBeenCalledWith(PATHS.txs)
})

it('7일 안에 나갈 돈이 없으면 그 줄이 없고, 잔액이 모자라면 경고가 보인다', async () => {
  await show()
  expect(host.textContent).not.toContain('곧 나갈 돈')
  const items = [due('a', '2026-11-07', 65000), due('b', '2026-11-10', 412000)]
  await show({ mustPay: items, upcoming: { total: 477000, shortfall: 0, items } })
  expect(host.querySelector('.mc-home3-due summary').textContent).toContain('곧 나갈 돈7일 안 · 2건477,000원')
  expect(host.querySelector('.mc-home3-warn')).toBeNull()
  await show({ mustPay: items, upcoming: { total: 477000, shortfall: 30000, items } })
  expect(host.querySelector('.mc-home3-warn').textContent).toBe('입출금 계좌 잔액보다 30,000원 많아요')
})

it("홈에 '하루에 쓸 수 있는 돈' · '여윳돈' 문구가 없다", async () => {
  await show()
  expect(host.textContent).not.toContain('하루에 쓸 수 있는 돈')
  expect(host.textContent).not.toContain('여윳돈')
})

it('예산이 없으면 머리에 예산 만들기 안내만 있고 시트는 없다 · 계좌가 없으면 연결하기 줄', async () => {
  await show({ budget: null, balance: { accountCount: 0, total: null } })
  expect(host.querySelector('.mc-home3-sheet')).toBeNull()
  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent === '예산 만들기').click())
  expect(navigate).toHaveBeenCalledWith(PATHS.salary)
  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent.includes('연결하기')).click())
  expect(navigate).toHaveBeenCalledWith(PATHS.connect)
})
