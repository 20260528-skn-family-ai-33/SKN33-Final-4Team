import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PopupProvider from '../popups/PopupProvider.jsx'
import MyPage from './MyPage.jsx'
import LoginPage from './LoginPage.jsx'

const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
let host, root, summary, consents, requests, send, summaryError, consentError
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  summary = { salary: { takeHomeIncome: 3120000, payday: 15, pendingPaydayChange: { payday: 20, effectiveOn: '2026-12-20' } },
    fixed: { count: 3, monthlyTotal: 714000 }, savings: { count: 0, monthlyTotal: 0 },
    protect: { livingBudget: 1230000, savingsFloor: null, cushionMonthly: 0 }, connections: { bankCount: 1, accountCount: 4, cardCount: 2 } }
  consents = { items: ['bank', 'card', 'loan'].map((productScope) => ({ productScope, required: productScope === 'bank',
    status: productScope === 'loan' ? 'not_granted' : 'granted', documentVersion: 'v2', grantedAt: '2026-11-04T10:00:00+09:00', revokedAt: null })) }
  requests = []; summaryError = consentError = null
  send = async (url) => {
    if (url === '/api/auth/me/') return reply({ ok: true })
    consents.items[1] = { ...consents.items[1], status: 'revoked', revokedAt: '2026-11-05T10:00:00+09:00' }
    return reply(consents)
  }
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    if (options.method) { requests.push({ url, method: options.method, body: JSON.parse(options.body) }); return send(url) }
    if (url === '/api/auth/me/') return reply({ nickname: '테스트', email: 'synthetic@example.com' })
    if (url === '/api/my/') return summaryError || reply(summary)
    if (url === '/api/finance/consents/') return consentError || reply(consents)
    if (url === '/api/csrf/') return reply({ ok: true })
    throw new Error(`Unexpected URL ${url}`)
  }))
  document.cookie = 'csrftoken=test; path=/'
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); localStorage.clear() })
function Location() { return <output data-path>{useLocation().pathname}</output> }
const render = (page = <MyPage />) => act(async () => root.render(<MemoryRouter initialEntries={['/my']}><PopupProvider>{page}<Location /></PopupProvider></MemoryRouter>))
const click = (text) => act(async () => [...host.querySelectorAll('button')].find((button) => button.textContent.trim() === text).click())
const open = async () => { await render(); await click('동의·데이터 관리　›') }
const input = (name, value) => act(() => {
  const element = host.querySelector(`input[name="${name}"]`)
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(element, value)
  element.dispatchEvent(new Event('input', { bubbles: true }))
})

it('실제 요약·급여일 예약을 표시하고 미설정과 0원을 구분한다', async () => {
  await render()
  for (const value of ['3,120,000원', '매월 15일', '2026-12-20부터 20일', '3건 · 714,000원', '0건 · 0원', '저축 미설정', '쿠션 0원', '은행 1곳 · 계좌 4개 · 카드 2장']) expect(host.textContent).toContain(value)
  expect(host.textContent).not.toContain('962,000원')
})
it('요약 실패를 0원으로 표시하지 않고 재시도한다', async () => {
  summaryError = reply({ error: { code: 'server_error' } }, 500)
  await render()
  expect(host.textContent).toContain('요약을 불러오지 못했어요')
  expect(host.textContent).not.toContain('0건 · 0원')
  summaryError = null
  await click('요약 다시 불러오기')
  expect(host.textContent).toContain('3건 · 714,000원')
})
it('비밀번호 재설정 진입점을 제거했다', async () => {
  await render(<LoginPage />)
  expect(host.textContent).not.toContain('비밀번호를 잊으셨나요')
})
it('실제 동의를 표시하고 철회 취소는 삭제 요청을 보내지 않는다', async () => {
  await open()
  expect(host.querySelector('[role=dialog]').textContent).toContain('v2')
  expect(host.querySelector('[role=dialog]').textContent).toContain('동의 안 함')
  await click('카드 조회 철회')
  expect(host.querySelector('[role=dialog]').textContent).toContain('예산·쿠션 기록')
  await click('취소')
  expect(requests).toHaveLength(0)
})
it('확인 후 해당 범위만 삭제하고 상태·마이 요약을 갱신한다', async () => {
  await open()
  const before = fetch.mock.calls.filter(([url]) => url === '/api/my/').length
  await click('카드 조회 철회'); await click('철회하고 데이터 삭제')
  expect(requests).toEqual([{ url: '/api/finance/consents/card/', method: 'DELETE', body: { confirm: true } }])
  expect(host.querySelector('[role=dialog]').textContent).toContain('철회함')
  expect(fetch.mock.calls.filter(([url]) => url === '/api/my/').length).toBe(before + 1)
})
it('삭제 중 중복 요청을 막고 실패를 성공으로 표시하지 않는다', async () => {
  let finish
  send = () => new Promise((resolve) => { finish = resolve })
  await open(); await click('카드 조회 철회'); await click('철회하고 데이터 삭제')
  await act(() => host.querySelector('[role=dialog] form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(requests).toHaveLength(1)
  await act(() => finish(reply({ error: { code: 'provider_unavailable', message: '연결 삭제를 완료하지 못했어요.' } }, 502)))
  expect(host.querySelector('[role=dialog]').textContent).toContain('연결 삭제를 완료하지 못했어요.')
  expect(host.querySelector('[role=dialog] form')).not.toBeNull()
})
it('탈퇴는 로그인 비밀번호와 삭제 확인 후 실행하고 로그인으로 이동한다', async () => {
  localStorage.setItem('moneycushion.savedEmail', 'synthetic@example.com')
  await open(); await click('회원 탈퇴 · 데이터 삭제')
  const submit = () => host.querySelector('[role=dialog] button[type=submit]')
  expect(submit().disabled).toBe(true)
  await input('withdrawPassword', 'Synthetic123!')
  expect(submit().disabled).toBe(true)
  await act(() => host.querySelector('[role=dialog] input[type=checkbox]').click())
  await click('탈퇴하고 전체 데이터 삭제')
  expect(requests[0]).toEqual({ url: '/api/auth/me/', method: 'DELETE', body: { password: 'Synthetic123!', confirm: true } })
  expect(host.querySelector('[data-path]').textContent).toBe('/login')
  expect(localStorage.getItem('moneycushion.savedEmail')).toBeNull()
})
it('동의 조회 실패 시 임의 상태나 철회 버튼을 표시하지 않는다', async () => {
  consentError = reply({ error: { code: 'server_error' } }, 500)
  await open()
  expect(host.querySelector('[role=dialog]').textContent).toContain('동의 상태를 불러오지 못했어요')
  expect(host.querySelector('[role=dialog]').textContent).not.toContain('카드 조회 철회')
})
