// 홈 — 실제 useApi · ApiStatus 로: 성공 → [새로고침] 뒤 다시 받기 실패 → 오류 카드 → [다시 시도] 성공 (R29 · R30 회귀)
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import HomePage from './HomePage.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'

vi.mock('../utils/financeSync.js', async (original) => ({ ...await original(), runFinanceSync: vi.fn(async () => ({ items: [] })) }))

const home = { period: null, balance: { accountCount: 0, total: null }, pendingCount: 0, needsPaydayCount: 0, mustPay: [], budget: null }
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
let host, root, answers
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  answers = [reply(home), reply({ error: { code: 'server_error', message: '내부 오류' } }, 500), reply(home)]
  vi.stubGlobal('fetch', vi.fn(async () => answers.shift()))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })
const click = (selector) => act(async () => host.querySelector(selector).click())

it('다시 받기가 실패하면 오류 카드로 알리고, 다시 시도로 홈을 되찾는다', async () => {
  await act(async () => root.render(<MemoryRouter><PopupProvider><HomePage /></PopupProvider></MemoryRouter>))
  expect(host.querySelector('[aria-label="새로고침"]')).not.toBeNull()

  await click('[aria-label="새로고침"]')
  expect(host.textContent).toContain('일시적인 문제일 수 있어요')
  expect(host.textContent).not.toContain('runserver')

  await click('[role=alert] button')
  expect(host.querySelector('[role=alert]')).toBeNull()
  expect(host.querySelector('[aria-label="새로고침"]')).not.toBeNull()
  expect(fetch).toHaveBeenCalledTimes(3)
})
