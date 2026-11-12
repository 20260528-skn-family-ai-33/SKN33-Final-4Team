// 거래 내역 — 느린 응답 중 카테고리를 바꾸면 이전 목록 · 합계를 새 조건 값처럼 보이지 않는다 (R29). 실패 → 다시 시도 (R30)
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import TransactionsPage from './TransactionsPage.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'

const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
const tx = { transactionId: 'tx-1', occurredOn: '2026-11-03', transactionType: 'spending', netAmount: 12_345, amount: 12_345,
  merchantName: '합성 식당', category: 'FOOD', categoryName: '식비', classifyState: 'confirmed', cancel: { state: 'none', amount: 0 } }
const list = (items, spentTotal) => reply({ items, summary: { spentTotal, needsReviewCount: 0 } })
let host, root, waiting
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  waiting = []
  vi.stubGlobal('fetch', vi.fn((url) => {
    if (url.includes('category=UNCLASSIFIED')) return Promise.resolve(list([], 0))
    return new Promise((resolve) => waiting.push({ url, resolve }))
  }))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })
const respond = (match, res) => act(async () => waiting.find((w) => w.url.includes(match) && !w.done && (w.done = true)).resolve(res))
const btn = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent === text)

it('카테고리를 바꾸면 새 결과가 올 때까지 이전 합계를 숨기고, 실패하면 다시 시도로 받는다', async () => {
  await act(async () => root.render(<MemoryRouter><PopupProvider><TransactionsPage /></PopupProvider></MemoryRouter>))
  await respond('transactions/?from', list([tx], 12_345))
  expect(host.textContent).toContain('12,345원')

  expect(btn('식비')).toBeUndefined()                       // 카테고리는 위에 늘 떠 있지 않고 [필터] 안에서 고른다
  await act(async () => btn('필터').click())
  await act(async () => btn('확인 필요').click())
  await act(async () => btn('식비').click())
  expect(btn('확인 필요').getAttribute('aria-pressed')).toBe('false')  // 확인 필요는 다른 카테고리와 섞지 않는다
  await act(async () => btn('소비').click())
  await act(async () => btn('적용').click())
  expect(waiting.at(-1).url).toContain('transactionType=spending')
  expect(host.textContent).not.toContain('12,345원')       // 전체 목록 합계를 식비 결과처럼 보이지 않는다
  expect(host.textContent).toContain('불러오는 중')
  expect(btn('필터 2').getAttribute('aria-pressed')).toBe('true')  // 고른 조건 수(유형 1 + 카테고리 1)가 버튼에 남는다

  await respond('transactions/?category=FOOD', reply('<html>', 503))
  expect(host.textContent).toContain('일시적인 문제일 수 있어요')
  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent === '다시 시도').click())
  await respond('transactions/?category=FOOD', list([], 0))
  expect(host.textContent).toContain('이 기간에 거래가 없어요')
})

// PR #101 리뷰: 입금 · 이체만 골라 둔 채 '확인 필요' 배너를 누르면 유형 조건이 남아 늘 비던 문제
it('확인 필요 배너는 거래 유형 · 계좌 · 카드 조건을 풀고 배너 숫자 그대로의 목록을 연다', async () => {
  const pendingTx = { ...tx, transactionId: 'tx-p', category: 'UNCLASSIFIED', categoryName: '미분류', classifyState: 'needs_review' }
  fetch.mockImplementation((url) => {
    if (url.includes('category=UNCLASSIFIED')) return Promise.resolve(reply({ items: [pendingTx], summary: { spentTotal: 12_345, needsReviewCount: 1 } }))
    return new Promise((resolve) => waiting.push({ url, resolve }))
  })
  await act(async () => root.render(<MemoryRouter initialEntries={['/txs?type=income&account=acc-1']}><PopupProvider><TransactionsPage /></PopupProvider></MemoryRouter>))
  expect(waiting.at(-1).url).toContain('transactionType=income')
  const banner = host.querySelector('.mc-pend-banner')
  expect(banner.textContent).toContain('1')
  const before = fetch.mock.calls.length
  await act(async () => banner.click())
  const urls = fetch.mock.calls.slice(before).map(([url]) => url)
  const listUrl = urls.find((url) => url.includes('category=UNCLASSIFIED'))
  expect(listUrl).toBe('/api/finance/transactions/?category=UNCLASSIFIED')  // 유형 · 계좌 조건 없이, 기간 없이(전체 기간)
  expect(host.querySelector('.mc-pend-banner').getAttribute('aria-pressed')).toBe('true')
  expect(btn('필터 1')).toBeDefined()  // 남은 조건은 '확인 필요' 하나
  expect(host.textContent).toContain('카테고리 확인 필요')  // 배너 숫자 1건이 목록에 보인다
})

// 11/11: 이름 검색 — 입력하는 대로 받은 목록 안에서 바로 거르고(서버에 다시 묻지 않음), 소비 합계도 걸러진 거래로 센다
it('이름 검색은 입력하는 대로 목록 · 합계를 거르고, 비우면 원래대로 돌아온다', async () => {
  const other = { ...tx, transactionId: 'tx-2', merchantName: 'Starbucks 역삼', netAmount: 5_000, amount: 5_000 }
  await act(async () => root.render(<MemoryRouter><PopupProvider><TransactionsPage /></PopupProvider></MemoryRouter>))
  await respond('transactions/?from', list([tx, other], 17_345))
  const calls = fetch.mock.calls.length
  const input = host.querySelector('input[name="q"]')
  const type = (v) => act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, v)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })

  await type('star')                                       // 영어는 대소문자 무시
  expect(host.textContent).toContain('거래 1건 · 소비 5,000원')
  expect(host.textContent).not.toContain('합성 식당')
  await type('없는 가게')
  expect(host.textContent).toContain("'없는 가게' 이름의 거래가 이 기간에 없어요.")
  await type('')
  expect(host.textContent).toContain('거래 2건 · 소비 17,345원')
  expect(fetch.mock.calls.length).toBe(calls)              // 글자마다 서버에 다시 묻지 않는다
})
