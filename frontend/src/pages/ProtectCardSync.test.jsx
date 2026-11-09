import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PopupProvider from '../popups/PopupProvider.jsx'
import ProtectPage from './ProtectPage.jsx'

const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
const connection = { connectionId: 'hyundai', businessType: 'card', organizationCode: '0302', organizationName: '현대카드', status: 'connected', cardIds: ['card-1', 'card-2'] }
const review = { reviewId: 'review-1', merchantName: '합성 해외상점', occurredOn: '2026-10-27', occurredTime: '10:27:00',
  amount: 6694, isCanceled: false, canceledAmount: null, canResolve: true,
  candidates: [{ cardId: 'card-1', displayName: '첫 카드', isSelected: true, nameMatches: true }] }
let host, root, connections, reason, result, requests, protectReads, run, connectionError, notes
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  connections = [structuredClone(connection), { ...connection, connectionId: 'unrelated', organizationName: '다른카드', cardIds: ['other-card'] },
    { connectionId: 'bank', businessType: 'bank', cardIds: [] }]
  reason = 'spending_history_incomplete'
  notes = []
  result = { items: [{ connectionId: 'hyundai', organizationName: '현대카드', status: 'succeeded', cardReviewCount: 1, approvalsComplete: false }] }
  requests = []; protectReads = 0; connectionError = null
  run = async () => {
    if (result.items[0].cardReviewCount > 0) reason = 'card_assignment_required'
    return reply({ taskId: 'sync-1', status: 'succeeded' })
  }
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    if (options.method) requests.push({ url, body: JSON.parse(options.body) })
    if (url === '/api/budgets/protect/') {
      protectReads++
      return reply({ missing: [], base: null, saved: null, recommended: null, cushion: { target: null }, cardNotes: notes,
        blocked: reason ? { reason: 'card_bill_pending' } : null, cardPending: reason ? [{ cardId: 'card-1', reason }] : [] })
    }
    if (url === '/api/finance/connections/' && !options.method) return connectionError || reply({ items: connections })
    if (url === '/api/finance/connections/' && options.method === 'POST') {
      connections[0].status = 'connected'
      return reply({ results: [{ status: 'connected', syncStatus: 'done' }] })
    }
    if (url === '/api/finance/sync/') return run()
    if (url === '/api/tasks/sync-1/') return reply({ status: 'succeeded', result })
    if (url === '/api/finance/card-reviews/') return reply({ items: [review] })
    if (url === '/api/finance/card-reviews/review-1/') { reason = null; return reply({ status: 'resolved' }) }
    if (url.startsWith('/api/budgets/fixed/')) return reply({ items: [], summary: {}, cardBills: [{ issuerCode: '0302', issuerName: '현대카드', totalAmount: 511515,
      bills: [{ billId: 'bill-1', issuerCode: '0302', billingMonth: '2026-11', dueDate: '2026-11-05', totalAmount: 511515, status: 'unchecked',
        remainingAmount: null, transactionPaidAmount: 0, recheckReason: null, userCheck: null, needsCheck: true }] }] })
    if (url === '/api/finance/card-bills/bill-1/payment-check/') { reason = null; return reply({ status: 'user_confirmed' }) }
    if (url === '/api/csrf/') return reply({ ok: true })
    throw new Error(`Unexpected URL: ${url}`)
  }))
  document.cookie = 'csrftoken=test; path=/'
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })
function Location() { return <output data-path>{useLocation().pathname}</output> }
const render = () => act(async () => root.render(<MemoryRouter initialEntries={['/budgets/protect']}><PopupProvider><ProtectPage /><Location /></PopupProvider></MemoryRouter>))
const button = (label) => [...host.querySelectorAll('button')].find((element) => element.textContent.includes(label))
const click = (label) => act(async () => button(label).click())
const fill = (name, value) => act(() => {
  const element = host.querySelector(`input[name="${name}"]`)
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(element, value)
  element.dispatchEvent(new Event('input', { bubbles: true }))
})
const submitSync = () => act(async () => host.querySelector('[aria-label="현대카드 내역 조회"] form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
const credentials = async () => { await fill('cardNo', '1234567890123456'); await fill('cardPassword', '1234') }

it('차단된 카드사만 조회하고 같은 화면에서 카드 확인 후 예산을 다시 불러온다', async () => {
  await render()
  expect(host.textContent).not.toContain('다른카드')
  await credentials()
  await submitSync()
  expect(requests[0]).toEqual({ url: '/api/finance/sync/', body: { connectionIds: ['hyundai'], cardCredentials: [{ connectionId: 'hyundai', cardNo: '1234567890123456', cardPassword: '1234' }] } })
  expect(host.querySelector('input[name=cardPassword]')).toBeNull()
  expect(host.querySelector('input[name=cardNo]')).toBeNull()
  expect(protectReads).toBeGreaterThan(1)
  await click('결제 카드 확인하기')
  await act(() => { const select = host.querySelector('select'); select.value = 'card-1'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  await act(async () => host.querySelector('[aria-label="결제 카드 확인 거래"] form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(host.textContent).not.toContain('결제 카드를 확인할 거래가 있어요')
  expect(host.querySelector('[data-path]').textContent).toBe('/budgets/protect')
  expect(requests.at(-1).body).toEqual({ cardId: 'card-1' })
})

it('추가 인증이 필요 없는 카드사는 번호·비밀번호 입력 없이 해당 연결만 조회한다', async () => {
  connections = [{ ...connection, organizationCode: '0306', organizationName: '신한카드' }]
  await render()
  expect(host.querySelector('input[name=cardPassword]')).toBeNull()
  await click('신한카드 내역 다시 불러오기')
  expect(requests[0].body).toEqual({ connectionIds: ['hyundai'] })
})

it('카드 확인 목록을 펼친 채로 재조회해도 목록을 새로 불러온다', async () => {
  reason = 'card_assignment_required'
  await render()
  await click('결제 카드 확인하기')
  const reads = () => fetch.mock.calls.filter(([url]) => url === '/api/finance/card-reviews/').length
  expect(reads()).toBe(1)
  expect(host.querySelector('input[name=cardPassword]')).toBeNull()
  await click('카드 내역도 다시 불러오기')
  await credentials()
  await submitSync()
  expect(reads()).toBe(2)
  expect(host.querySelector('[aria-label="결제 카드 확인 거래"]')).not.toBeNull()
})

it('결제 카드 확인만 필요하면 인증 입력이나 재조회 없이 거래를 확인한다', async () => {
  reason = 'card_assignment_required'
  await render()
  expect(host.querySelector('input[name=cardPassword]')).toBeNull()
  expect(host.querySelector('input[name=cardNo]')).toBeNull()
  await click('결제 카드 확인하기')
  await act(() => { const select = host.querySelector('select'); select.value = 'card-1'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  await act(async () => host.querySelector('[aria-label="결제 카드 확인 거래"] form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(requests).toEqual([{ url: '/api/finance/card-reviews/review-1/', body: { cardId: 'card-1' } }])
  expect(host.textContent).not.toContain('결제 카드를 확인할 거래가 있어요')
})

it('현대카드 비밀번호 길이를 검사하고 조회 중 중복 요청을 막는다', async () => {
  let finish
  run = () => new Promise((resolve) => { finish = resolve })
  await render()
  await credentials()
  await fill('cardPassword', '12')
  await submitSync()
  expect(requests).toHaveLength(0)
  await fill('cardPassword', '1234')
  await submitSync()
  await submitSync()
  expect(requests).toHaveLength(1)
  expect(host.querySelector('input[name=cardPassword]').disabled).toBe(true)
  await act(() => finish(reply({ taskId: 'sync-1' })))
})

it('연결 목록 오류를 표시하고 무관한 기관에 조회를 보내지 않는다', async () => {
  connectionError = reply({ error: { code: 'server_error', message: '연결 정보를 가져오지 못했어요.' } }, 500)
  await render()
  expect(host.textContent).toContain('연결 정보를 가져오지 못했어요.')
  expect(requests).toHaveLength(0)
})

it('인증 실패 시 입력값을 비우고 지킬 돈에서 재로그인 팝업을 연다', async () => {
  result = { items: [{ connectionId: 'hyundai', status: 'failed', error: { code: 'relogin_required', message: '다시 연결해 주세요.' } }] }
  await render()
  await credentials()
  await submitSync()
  expect(host.textContent).toContain('현대카드 다시 연결하기')
  await click('현대카드 다시 연결하기')
  expect(host.querySelector('[role=dialog]').textContent).toContain('현대카드 로그인')
  expect(host.querySelector('[data-path]').textContent).toBe('/budgets/protect')
})

it('재로그인이 필요한 연결도 지킬 돈에서 로그인 후 다시 조회할 수 있다', async () => {
  connections[0].status = 'relogin'
  await render()
  await click('현대카드 다시 연결하기')
  await fill('instId', 'synthetic-user')
  await fill('instPw', 'synthetic-password')
  // 추가 인증 칸은 기관 로그인 팝업에만 존재한다.
  await act(() => {
    const inputs = [...host.querySelectorAll('[role=dialog] input')].slice(2)
    for (const [index, element] of inputs.entries()) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(element, index === 0 ? '1234567890123456' : '1234')
      element.dispatchEvent(new Event('input', { bubbles: true }))
    }
  })
  await act(async () => host.querySelector('[role=dialog] form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(requests[0].url).toBe('/api/finance/connections/')
  expect(requests[0].body.institutions[0].organizationCode).toBe('0302')
  expect(host.querySelector('[role=dialog]')).toBeNull()
  expect(button('현대카드 내역 다시 불러오기')).toBeDefined()
  expect(host.querySelector('[data-path]').textContent).toBe('/budgets/protect')
})

it('11/06: 중복을 확인 못 한 금액은 넉넉하게 잡았다고 안내하고, 상세 일부 누락은 건수 · 금액 없이 알린다', async () => {
  reason = null
  notes = [{ cardId: 'card-1', issuerCode: '0302', reason: 'card_overlap_uncertain', amount: 100000, count: 1 },
    { cardId: 'card-1', issuerCode: '0302', reason: 'bill_items_incomplete', amount: null, count: null }]
  await render()
  expect(host.textContent).toContain('일부 카드 사용의 중복 여부를 확인하지 못해 카드값을 넉넉하게 잡았어요. 실제 사용 가능한 예산은 표시보다 많을 수 있어요.')
  expect(host.textContent).toContain('확인하지 못한 이번 주기 사용 1건 · 100,000원')
  expect(host.textContent).toContain('빠진 항목은 확인하지 못했어요')
  expect(host.querySelector('input[name=cardNo]')).toBeNull()   // 짝을 못 찾았다고 재조회 · 인증을 요구하지 않음
})

it('11/06: 청구 총액 불일치는 0원이나 상세 합계로 넘기지 않고 원래 청구액 확인이 필요하다고 안내한다', async () => {
  reason = 'bill_total_mismatch'
  await render()
  expect(host.textContent).toContain('카드 청구서의 원래 청구액을 확인해야 해요')
  expect(host.querySelector('input[name=cardNo]')).toBeNull()
})

it('청구서가 아직 없으면 미반영을 안내하고 재조회는 고를 때만', async () => {
  reason = null
  notes = [{ cardId: 'card-1', reason: 'first_bill_unavailable' }]
  await render()
  expect(host.textContent).toContain('지난 카드값이 아직 반영 안 됐어요')
  await click('지난 카드값이 아직 반영 안 됐어요')
  expect(host.textContent).toContain('이 예산으로 먼저 시작해도 돼요')
  expect(button('청구서 확인하러 가기')).toBeUndefined()
  await click('닫기 ×')
  expect(host.textContent).not.toContain('카드 내역을 다시 불러와야 해요')
  expect(host.querySelector('input[name=cardNo]')).toBeNull()
  await click('다시 불러오기')
  expect(host.querySelector('input[name=cardNo]')).not.toBeNull()
})

it('11/06: 조회 기간 누락만 재조회 입력을 바로 보여 준다', async () => {
  reason = 'bill_history_incomplete'
  await render()
  expect(host.textContent).toContain('카드 내역을 다시 불러와야 해요')
  expect(host.querySelector('input[name=cardNo]')).not.toBeNull()
})

