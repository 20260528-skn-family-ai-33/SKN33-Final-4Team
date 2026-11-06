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
const chip = (name) => [...host.querySelectorAll('.mc-cat-filter button')].find((b) => b.textContent === name)

it('카테고리를 바꾸면 새 결과가 올 때까지 이전 합계를 숨기고, 실패하면 다시 시도로 받는다', async () => {
  await act(async () => root.render(<MemoryRouter><PopupProvider><TransactionsPage /></PopupProvider></MemoryRouter>))
  await respond('transactions/?from', list([tx], 12_345))
  expect(host.textContent).toContain('12,345원')

  await act(async () => chip('식비').click())
  expect(host.textContent).not.toContain('12,345원')       // 전체 목록 합계를 식비 결과처럼 보이지 않는다
  expect(host.textContent).toContain('불러오는 중')
  expect(chip('식비').getAttribute('aria-pressed')).toBe('true')  // 칩은 그대로 — 계속 바꿀 수 있다

  await respond('category=FOOD', reply('<html>', 503))
  expect(host.textContent).toContain('일시적인 문제일 수 있어요')
  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent === '다시 시도').click())
  await respond('category=FOOD', list([], 0))
  expect(host.textContent).toContain('이 기간에 거래가 없어요')
})
