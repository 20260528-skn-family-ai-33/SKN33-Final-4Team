import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CardReviewsPage from './CardReviewsPage.jsx'
import ProtectPage from './ProtectPage.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'
import { syncLines } from '../utils/financeSync.js'

const review = {
  reviewId: 'review-1', merchantName: '합성 해외 상점', occurredOn: '2026-10-27', occurredTime: '10:27:00',
  issuerName: '합성카드', amount: 6694, isCanceled: false, canceledAmount: null, canResolve: true,
  candidates: [{ cardId: 'card-1', displayName: '첫 카드', isSelected: true, nameMatches: true }],
}
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
let host, root, rows, write, requests, getError
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  rows = [structuredClone(review)]
  requests = []
  getError = null
  write = async () => { rows = []; return reply({ status: 'resolved' }) }
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    if (options.method === 'PUT') { requests.push(JSON.parse(options.body)); return write() }
    if (url === '/api/csrf/') return reply({ ok: true })
    return getError ?? reply({ items: structuredClone(rows) })
  }))
  document.cookie = 'csrftoken=test; path=/'
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })
const render = () => act(async () => root.render(<MemoryRouter initialEntries={['/finance/card-reviews']}>
  <PopupProvider><Routes><Route path="/finance/card-reviews" element={<CardReviewsPage />} /><Route path="/budgets/protect" element={<p>예산 다시 계산</p>} /></Routes></PopupProvider>
</MemoryRouter>))
const select = () => act(() => {
  const element = host.querySelector('select')
  element.value = 'card-1'
  element.dispatchEvent(new Event('change', { bubbles: true }))
})
const submit = () => act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))

it('카드명 일치는 후보로 표시하고 사용자가 선택한 거래만 확인한 뒤 예산으로 돌아간다', async () => {
  await render()
  expect(host.textContent).toContain('카드명 일치')
  expect(host.querySelector('select').value).toBe('')
  expect(host.querySelector('button[type=submit]').disabled).toBe(true)
  await select()
  await submit()
  expect(requests).toEqual([{ cardId: 'card-1' }])
  expect(host.textContent).toContain('결제 카드를 확인할 거래가 없어요')
  await act(() => [...host.querySelectorAll('button')].find((button) => button.textContent.includes('지킬 돈')).click())
  expect(host.textContent).toContain('예산 다시 계산')
})

it('금액이 불명확해진 거래는 카드를 확정할 수 없다', async () => {
  rows[0].canResolve = false
  await render()
  expect(host.textContent).toContain('금액이나 취소 정보를 다시 확인')
  expect(host.querySelector('select')).toBeNull()
  await submit()
  expect(requests).toEqual([])
})

it('저장 실패를 보여 주고 선택값을 유지한다', async () => {
  write = async () => reply({ error: { code: 'invalid', message: '실패', fields: { cardId: ['선택할 수 없는 카드예요.'] } } }, 400)
  await render()
  await select()
  await submit()
  expect(host.textContent).toContain('선택할 수 없는 카드예요.')
  expect(host.querySelector('select').value).toBe('card-1')
})

it('저장 중 중복 요청을 막는다', async () => {
  let finish
  write = () => new Promise((resolve) => { finish = resolve })
  await render()
  await select()
  await submit()
  await submit()
  expect(requests).toHaveLength(1)
  expect(host.querySelector('select').disabled).toBe(true)
  await act(() => finish(reply({ status: 'resolved' })))
})

it('조회 실패를 미확인 거래 0건으로 표시하지 않는다', async () => {
  getError = reply({ error: { code: 'consent_required', message: '카드 조회 동의가 필요해요.' } }, 403)
  await render()
  expect(host.textContent).toContain('카드 조회 동의가 필요해요')
  expect(host.textContent).not.toContain('결제 카드를 확인할 거래가 없어요')
})

it('다시 불러오기 결과에서 미확인 거래와 사용 내역 미완료를 안내한다', () => {
  expect(syncLines({ items: [{ status: 'succeeded', cardReviewCount: 1 }] })[0].text).toContain('확인할 거래 1건')
  expect(syncLines({ items: [{ status: 'succeeded', approvalsComplete: false }] })[0].ok).toBe(false)
})

it('지킬 돈 안에서 결제 카드 확인 거래를 펼친다', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url) => url === '/api/budgets/protect/' ? reply({
    base: null, blocked: { reason: 'card_bill_pending' }, recommended: null, saved: null,
    missing: [], cushion: { target: null }, cardPending: [{ cardId: 'card-1', reason: 'card_assignment_required' }],
  }) : url === '/api/finance/card-reviews/' ? reply({ items: [review] }) : reply({ items: [] })))
  await act(async () => root.render(<MemoryRouter initialEntries={['/budgets/protect']}><PopupProvider><Routes>
    <Route path="/budgets/protect" element={<ProtectPage />} />
  </Routes></PopupProvider></MemoryRouter>))
  expect(host.textContent).toContain('결제 카드를 확인할 거래가 있어요')
  await act(() => [...host.querySelectorAll('button')].find((button) => button.textContent === '결제 카드 확인하기').click())
  expect(host.textContent).toContain('합성 해외 상점')
  expect(host.querySelector('select')).not.toBeNull()
})
