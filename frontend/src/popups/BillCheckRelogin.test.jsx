// 청구서 확인 창의 다시 로그인 (PR #116 리뷰) — 가짜 없이 실제 PopupProvider · CardSyncPanel · ConnectLoginPopup 으로
// 로그인 팝업이 확인 창을 대신해도(팝업은 하나) 로그인 결과가 새 확인 창으로 이어져 청구서 확인까지 가는지 본다. 서버 응답만 합성
import { act, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PopupProvider from './PopupProvider.jsx'
import { usePopup } from './context.js'
import BillCheckDialog from './BillCheckDialog.jsx'

const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
let host, root, connStatus, synced, loginResult, posted, org, syncStatus
const onFound = vi.fn()

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  vi.clearAllMocks()
  connStatus = 'relogin'; synced = false; posted = null
  org = { organizationCode: '0306', organizationName: '신한카드' }; syncStatus = 'succeeded'
  loginResult = { status: 'connected', syncStatus: 'done' }
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const method = options.method ?? 'GET'
    if (url === '/api/csrf/') return reply({ ok: true })
    if (url === '/api/budgets/protect/') return reply({ cardNotes: synced ? [] : [{ cardId: 'card-1', reason: 'first_bill_unavailable' }] })
    if (url === '/api/finance/connections/' && method === 'GET') return reply({ items: [{ connectionId: 'conn-1', businessType: 'card',
      ...org, status: connStatus, cardIds: ['card-1'] }] })
    if (url === '/api/finance/connections/' && method === 'POST') {
      posted = JSON.parse(options.body)
      if (loginResult.status === 'connected') connStatus = 'connected'
      return reply({ results: [loginResult] })
    }
    if (url === '/api/finance/sync/') return reply({ taskId: 'sync-1', status: 'running' }, 202)
    if (url === '/api/tasks/sync-1/') {
      synced = syncStatus === 'succeeded'  // 건너뜀이면 청구서도 그대로
      return reply({ status: 'succeeded', result: { items: [{ connectionId: 'conn-1', organizationName: org.organizationName, status: syncStatus }] } })
    }
    throw new Error(`Unexpected URL: ${method} ${url}`)
  }))
  document.cookie = 'csrftoken=test; path=/'
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })

// 화면처럼 지금 떠 있는 팝업을 그린다 (Layout 의 팝업 자리 대신)
function Host() {
  const { popup, openPopup } = usePopup()
  useEffect(() => { openPopup(<BillCheckDialog cardIds={['card-1']} onFound={onFound} />) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return popup
}
const settle = () => act(() => new Promise((r) => setTimeout(r, 30)))
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.includes(text))
const type = (name, value) => {
  const el = host.querySelector(`input[name="${name}"]`)
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}
const login = async () => {
  await act(async () => root.render(<MemoryRouter><PopupProvider><Host /></PopupProvider></MemoryRouter>))
  for (let i = 0; i < 5 && !button('신한카드 다시 연결하기'); i++) await settle()
  await act(async () => button('신한카드 다시 연결하기').click())
  expect(host.textContent).toContain('신한카드 로그인')                // 로그인 팝업이 확인 창을 대신함
  await act(async () => { type('instId', 'hong'); type('instPw', 'pw') })
  await act(async () => host.querySelector('form').requestSubmit())
  for (let i = 0; i < 10; i++) await settle()
}

it('다시 로그인에 성공하면 새 확인 창이 이어서 불러오고 청구서가 나오면 재추천으로 간다', async () => {
  await login()
  expect(posted.institutions[0]).toMatchObject({ businessType: 'card', organizationCode: '0306', loginId: 'hong' })
  expect(onFound).toHaveBeenCalledOnce()
})

it('다시 로그인에 실패하면 새 확인 창에 이유와 다시 시도를 보여 준다', async () => {
  loginResult = { status: 'failed', error: { message: '아이디 또는 비밀번호가 맞지 않아요.' } }
  await login()
  expect(onFound).not.toHaveBeenCalled()
  expect(host.textContent).toContain('청구서를 불러오지 못했어요')
  expect(host.textContent).toContain('신한카드 · 아이디 또는 비밀번호가 맞지 않아요.')
  expect(button('다시 시도')).toBeDefined()
})

it('추가 인증이 필요하면 금융 연결 화면으로 안내한다', async () => {
  loginResult = { status: 'sms_required' }
  await login()
  expect(host.textContent).toContain('카드사 추가 인증이 필요해요')
  expect(button('추가 인증하러 가기')).toBeDefined()
})

// PR #116 2차 리뷰: 실제 조회 칸에서 건너뜀(skipped)은 불러온 게 아니라서 청구서 확인으로 넘어가지 않는다
it('현대카드 직접 조회가 건너뜀이면 아직 없음으로 끝내지 않는다', async () => {
  connStatus = 'connected'
  org = { organizationCode: '0302', organizationName: '현대카드' }
  syncStatus = 'skipped'
  await act(async () => root.render(<MemoryRouter><PopupProvider><Host /></PopupProvider></MemoryRouter>))
  for (let i = 0; i < 5 && !host.querySelector('input[name="cardNo"]'); i++) await settle()
  await act(async () => { type('cardNo', '4111222233334444'); type('cardPassword', '1234') })
  await act(async () => host.querySelector('.mc-bill-manual form').requestSubmit())
  for (let i = 0; i < 5; i++) await settle()
  expect(host.textContent).not.toContain('아직 청구서가 조회되지 않았어요')
  expect(host.textContent).toContain('카드사 확인이 필요해요')
  expect(onFound).not.toHaveBeenCalled()
})
