import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PopupProvider from '../popups/PopupProvider.jsx'
import ProtectPage from './ProtectPage.jsx'
import { PATHS } from '../routes.js'

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
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    if (url !== '/api/budgets/protect/') throw new Error(`Unexpected URL: ${url}`)
    return { ok: true, json: async () => options.method === 'PUT' ? { locksReleased: [] } : data }
  }))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })
const render = () => act(async () => root.render(<MemoryRouter initialEntries={[PATHS.protect]}><PopupProvider><Routes>
  <Route path={PATHS.protect} element={<ProtectPage />} />
  <Route path={PATHS.recommend} element={<p>추천 화면</p>} />
</Routes></PopupProvider></MemoryRouter>))

it('승인 예산 충당액과 배분 후 여윳돈을 기본 설정과 구분해서 표시한다', async () => {
  data.approvedBudget = { spareTransfer: 100000, spare: 620000 }
  await render()
  const notice = host.querySelector('[aria-label="이번 승인 예산 여윳돈"]')
  expect(notice.textContent).toContain('이번 승인 예산에는 여윳돈 100,000원을 생활비로 포함')
  expect(notice.textContent).toContain('이번 예산 배분 기준 여윳돈은 620,000원')
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

it('PUT 응답에 충당 안내가 없어도 지킬 돈 화면을 교체하지 않고 추천 화면으로 이동한다', async () => {
  data.approvedBudget = { spareTransfer: 100000, spare: 620000 }
  document.cookie = 'csrftoken=test; path=/'
  try {
    await render()
    await act(async () => host.querySelector('#mc-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(host.textContent).toContain('추천 화면')
    const put = fetch.mock.calls.find(([, options]) => options?.method === 'PUT')
    expect(JSON.parse(put[1].body)).toEqual({ livingBudget: 500000, savingFloor: 380000, cushionMonthly: 320000 })
  } finally {
    document.cookie = 'csrftoken=; max-age=0; path=/'
  }
})

it('금액을 고치면 칸 아래에 저장해 둔 금액보다 얼마나 늘었는지 · 줄었는지 보여 준다 (11/12)', async () => {
  await render()
  const shown = () => [...host.querySelectorAll('.mc-protect-delta')].map((d) => d.textContent).filter(Boolean)
  expect(shown()).toEqual([])  // 안 고쳤으면 글자 없이 자리만 (칸 높이를 맞추려고)
  expect(host.querySelectorAll('.mc-cat-rec').length).toBe(3)  // 추천 칸은 고치지 않아도 세 칸 모두
  const type = (id, value) => act(() => {
    const el = host.querySelector(id)
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await type('#mc-p-livingBudget', '450000')
  await type('#mc-p-savingFloor', '430000')
  expect(shown()).toEqual(['기존보다 −50,000원', '기존보다 +50,000원'])
  const cls = [...host.querySelectorAll('.mc-protect-delta')].filter((d) => d.textContent).map((d) => d.classList.contains('is-up') ? 'up' : d.classList.contains('is-down') ? 'down' : '')
  expect(cls).toEqual(['down', 'up'])  // 줄면 파랑(is-down) · 늘면 빨강(is-up)
})

it('배분 가능한 금액보다 많이 넣고 저장하면 서버에 보내지 않고 토스트만 띄운다 (11/12)', async () => {
  await render()
  await act(() => {
    const el = host.querySelector('#mc-p-livingBudget')
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, '99000000')
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => host.querySelector('#mc-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
  expect(document.getElementById('mc-toast').textContent).toBe('금액을 줄여 주세요')
  expect(host.querySelector('.mc-form > .mc-error[role=alert]')).toBeNull()  // 아래에 같은 문장을 또 붙이지 않는다
})

it('나눈 기준은 맨 아래 글 대신 제목 옆 [?] 가운데 창으로 본다 (11/12)', async () => {
  data.rates = { living: 45, saving: 40, spare: 5, cushion: 10 }
  await render()
  expect(host.textContent).not.toContain('뺀 금액을')
  await act(async () => host.querySelector('[aria-label="추천안을 나눈 기준 보기"]').click())
  expect(document.body.textContent).toContain('뺀 금액을 나눴어요')
  expect(document.body.textContent).toContain('자취 여부는 내 정보에서 바꿀 수 있어요')
  expect(document.querySelector('.mc-help-box')?.textContent).toBe('생활비 45% · 저축 40% · 여윳돈 5% · 쿠션 10%')  // 비율은 목록 말고 제목 밑 칸에
})

it('마이에서 들어와 저장하면 가운데 창으로 다시 추천을 알리고, [예산 확인하러 가기]를 눌러야 저장한다 (11/13)', async () => {
  document.cookie = 'csrftoken=test; path=/'
  try {
    await act(async () => root.render(<MemoryRouter initialEntries={[{ pathname: PATHS.protect, state: { from: 'my' } }]}><PopupProvider><Routes>
      <Route path={PATHS.protect} element={<ProtectPage />} />
      <Route path={PATHS.recommend} element={<p>추천 화면</p>} />
    </Routes></PopupProvider></MemoryRouter>))
    const submit = () => act(async () => host.querySelector('#mc-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    await submit()
    const dialog = () => document.querySelector('[role=dialog][aria-label="예산 다시 추천"]')
    expect(dialog().textContent).toContain('새 추천을 승인해야 지금 예산이 바뀌어요.')
    expect(fetch.mock.calls.some(([, o]) => o?.method === 'PUT')).toBe(false)  // 창만 띄우고 아직 저장 안 함
    await act(async () => [...dialog().querySelectorAll('button')].find((b) => b.textContent === '예산 확인하러 가기').click())
    expect(fetch.mock.calls.some(([, o]) => o?.method === 'PUT')).toBe(true)
    expect(host.textContent).toContain('추천 화면')
  } finally {
    document.cookie = 'csrftoken=; max-age=0; path=/'
  }
})

it('온보딩에서는 창 없이 바로 저장하고 추천으로 간다 (11/13)', async () => {
  document.cookie = 'csrftoken=test; path=/'
  try {
    await render()
    await act(async () => host.querySelector('#mc-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(document.querySelector('[aria-label="예산 다시 추천"]')).toBeNull()
    expect(host.textContent).toContain('추천 화면')
  } finally {
    document.cookie = 'csrftoken=; max-age=0; path=/'
  }
})
