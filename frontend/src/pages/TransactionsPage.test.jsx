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
  await act(async () => btn('확인 필요 0').click())
  await act(async () => btn('식비').click())
  expect(btn('확인 필요 0').getAttribute('aria-pressed')).toBe('false')  // 확인 필요는 다른 카테고리와 섞지 않는다
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
