import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import MyPage from './MyPage.jsx'
import SavedPoliciesPage from './SavedPoliciesPage.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root, items, listError, logoutFails, removed, restore
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body })
const summary = {
  salary: { takeHomeIncome: null, payday: null }, fixed: { count: 0, monthlyTotal: 0 }, savings: { count: 0, monthlyTotal: 0 },
  protect: { livingBudget: null, savingsFloor: null }, connections: { bankCount: 0, accountCount: 0, cardCount: 0 },
}
beforeEach(() => {
  items = []; listError = false; logoutFails = false; removed = []; restore = null
  sessionStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (url, opts) => {
    if (url === '/api/auth/me/') return reply({ nickname: '테스터', email: 'tester@example.invalid' })
    if (url === '/api/my/') return reply(summary)
    if (url === '/api/policies/saved/') return listError ? reply({ error: { code: 'server_error' } }, 500) : reply({ items })
    if (url.startsWith('/api/policies/') && url.endsWith('/saved/')) {  // PUT · DELETE
      const no = decodeURIComponent(url.split('/')[3])
      if (opts?.method === 'DELETE') { removed.push(no); items = items.filter((p) => p.policyNo !== no); return { ok: true, status: 204, json: async () => { throw new Error('no body') } } }
      if (opts?.method === 'PUT') { items = [restore, ...items]; return reply(restore) }
    }
    if (url === '/api/auth/logout/') return logoutFails ? reply({ error: { code: 'server_error', message: '로그아웃 실패' } }, 500) : reply({ ok: true })
    if (url === '/api/csrf/') return reply({})
    throw new Error(`Unexpected URL ${url}`)
  }))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); sessionStorage.clear() })
function Location() { const loc = useLocation(); return <output>{loc.pathname}{loc.search}</output> }
const render = (url = '/my') => act(async () => root.render(<MemoryRouter initialEntries={[url]}><PopupProvider>
  <Routes><Route path="/my" element={<MyPage />} /><Route path="/my/policies" element={<SavedPoliciesPage />} /><Route path="/login" element={<p>로그인</p>} /></Routes>
  <Location /></PopupProvider></MemoryRouter>))
const button = (name) => [...host.querySelectorAll('button')].find((b) => b.textContent.trim() === name)
const click = (name) => act(async () => button(name).click())

it('마이에서는 메뉴만 보이고 들어가면 목록이 나오며 뒤로 돌아올 수 있다', async () => {
  items = [
    { policyNo: 'P2', name: '두 번째 정책', applyState: 'always', active: true, savedAt: '2026-11-12T12:00:00+09:00', link: 'https://example.org/policy' },
    { policyNo: 'P1', name: '마감된 정책', applyState: 'closed', applyEnd: '2026-10-31', active: false, savedAt: '2026-11-11T12:00:00+09:00', link: null },
  ]
  await render()
  expect(host.querySelector('[role="tablist"]')).toBeNull()
  expect([...host.querySelectorAll('h2')].map((node) => node.textContent)).toEqual(['테스터', '내 돈 관리', '청년 정책', '앱 설정'])
  expect(host.querySelector('output').textContent).toBe('/my')
  expect(host.querySelector('article')).toBeNull()
  expect(fetch.mock.calls.some(([url]) => url === '/api/policies/saved/')).toBe(false)
  await click('저장한 정책관심 있는 청년 정책 모아보기›')
  expect(host.querySelector('output').textContent).toBe('/my/policies')
  expect([...host.querySelectorAll('article h3')].map((node) => node.textContent)).toEqual(['두 번째 정책', '마감된 정책'])
  expect(host.textContent).toContain('상시 신청')
  expect(host.textContent).toContain('신청 마감 · 마감일 2026-10-31')
  expect(host.textContent).toContain('현재 정책 목록에서 빠진 정책')
  expect(host.textContent).toContain('저장일2026-11-12')  // 표: 저장일 | 2026-11-12
  expect(host.querySelector('details.mc-saved-past summary').textContent).toContain('지난 정책 1개')  // 마감 · 빠진 정책은 아래로
  expect(host.querySelector('article a').href).toBe('https://example.org/policy')
  expect(host.querySelector('article a').target).toBe('_blank')
  await act(() => host.querySelector('button[aria-label="뒤로"]').click())
  expect(host.querySelector('output').textContent).toBe('/my')
  expect(host.textContent).toContain('내 돈 관리')
  expect(host.querySelector('article')).toBeNull()
})

it('지역 · 지원 표, 상태 배지 · 마감 D-day 를 보이고 해제한 정책은 되돌릴 수 있다', async () => {
  const end = new Date(); end.setDate(end.getDate() + 3)
  const ymd = `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, '0')}-${String(end.getDate()).padStart(2, '0')}`
  items = [{ policyNo: 'P/1', name: '월세 지원', applyState: 'open', applyEnd: ymd, active: true, savedAt: '2026-11-12T12:00:00+09:00',
    link: 'https://example.org/p', region: '서울특별시', supportType: null, support: '월 20만 원' }]
  restore = items[0]
  await render('/my/policies')
  const rows = [...host.querySelectorAll('.mc-policy-table > div')].map((d) => d.querySelector('dt').textContent)
  expect(rows).toEqual(['지역', '지원', '신청', '저장일'])  // 지원 형태가 없으면 줄을 뺀다
  expect([...host.querySelectorAll('.mc-saved-badge')].map((b) => [b.textContent, b.classList.contains('is-urgent')]))
    .toEqual([['신청 중', false], ['마감 D-3', true]])
  await click('저장 해제')
  expect(removed).toEqual(['P/1'])
  expect(host.querySelector('article')).toBeNull()
  expect(host.querySelector('.mc-saved-undo').textContent).toContain('‘월세 지원’ 저장을 해제했어요.')
  await click('되돌리기')
  expect([...host.querySelectorAll('article h3')].map((h) => h.textContent)).toEqual(['월세 지원'])
  expect(host.querySelector('.mc-saved-undo')).toBeNull()
})

it('저장한 정책이 없으면 빈 상태와 챗봇 진입을 안내한다', async () => {
  await render('/my/policies')
  expect(host.textContent).toContain('아직 저장한 정책이 없어요')
  expect(host.querySelector('a').getAttribute('href')).toBe('/chat')
})

it('조회 실패를 빈 목록으로 표시하지 않으며 재시도할 수 있다', async () => {
  listError = true
  await render('/my/policies')
  expect(host.querySelector('[role="alert"]')).toBeTruthy()
  expect(host.textContent).not.toContain('아직 저장한 정책이 없어요')
  listError = false
  await click('다시 시도')
  expect(host.textContent).toContain('아직 저장한 정책이 없어요')
})

it('실행 가능한 주소를 공고 링크로 만들지 않는다', async () => {
  items = [{ policyNo: 'P1', name: '정책', applyState: 'unknown', link: 'javascript:alert(1)' }]
  await render('/my/policies')
  expect(host.querySelector('article a')).toBeNull()
  expect(host.textContent).toContain('등록된 링크가 없어요')
})

it('로그아웃 실패 때는 대화를 유지하고 성공한 뒤 번호를 지운다', async () => {
  sessionStorage.setItem('mc-chat-conversation', 'conversation-1')
  logoutFails = true
  await render()
  await click('로그아웃　›')
  expect(sessionStorage.getItem('mc-chat-conversation')).toBe('conversation-1')
  logoutFails = false
  await click('로그아웃　›')
  expect(sessionStorage.getItem('mc-chat-conversation')).toBeNull()
  expect(host.querySelector('output').textContent).toBe('/login')
})
