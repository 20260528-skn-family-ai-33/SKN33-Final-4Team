import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PopupProvider from '../popups/PopupProvider.jsx'
import ProtectPage from './ProtectPage.jsx'

let host, root, data
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  data = {
    missing: [], housingType: 'family', blocked: null, savingsMin: 0,
    base: { takeHomeIncome: 3000000, fixedTotal: 1080000, available: 1920000 },
    recommended: { livingBudget: 500000, savingFloor: 380000, cushionMonthly: 320000 },
    saved: { livingBudget: 500000, savingFloor: 380000, cushionMonthly: 320000, spare: 720000 },
    cushion: { required: true, target: 9000000, balanceTotal: 0 },
  }
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (url !== '/api/budgets/protect/') throw new Error(`Unexpected URL: ${url}`)
    return { ok: true, json: async () => data }
  }))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })
const render = () => act(async () => root.render(<MemoryRouter><PopupProvider><ProtectPage /></PopupProvider></MemoryRouter>))

it('승인 예산 충당액과 배분 후 여윳돈을 기본 설정과 구분해서 표시한다', async () => {
  data.approvedBudget = { spareTransfer: 100000, spare: 620000 }
  await render()
  const notice = host.querySelector('[aria-label="이번 승인 예산 여윳돈"]')
  expect(notice.textContent).toContain('이번 승인 예산에는 여윳돈 100,000원을 생활비로 포함')
  expect(notice.textContent).toContain('배분 후 여윳돈은 620,000원')
  expect(notice.textContent).toContain('지킬 돈 설정은 그대로')
  expect(host.querySelector('input[name="livingBudget"]').value.replace(/,/g, '')).toBe('500000')
  expect(host.querySelector('#mc-protectsum').textContent).toContain('720,000원')
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('승인된 충당이 없으면 기본 여윳돈만 표시한다', async () => {
  await render()
  expect(host.querySelector('[aria-label="이번 승인 예산 여윳돈"]')).toBeNull()
  expect(host.querySelector('#mc-protectsum').textContent).toContain('720,000원')
})

it('충당액이 0원이면 충당 안내를 표시하지 않는다', async () => {
  data.approvedBudget = { spareTransfer: 0, spare: 720000 }
  await render()
  expect(host.querySelector('[aria-label="이번 승인 예산 여윳돈"]')).toBeNull()
})
