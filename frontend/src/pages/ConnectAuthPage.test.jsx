// 기관 로그인 화면 — 이미 연결된 기관은 '연결 완료'로 (11/12: 다음 화면에 갔다가 뒤로 오면 다시 로그인해야 했다)
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ConnectAuthPage from './ConnectAuthPage.jsx'
import { useApi } from '../utils/api.js'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn() }))
vi.mock('react-router', () => ({
  useNavigate: () => navigate,
  useLocation: () => ({ pathname: '/finance/connect/auth', state: { banks: ['신한은행', '국민은행'], cards: ['삼성카드'] } }),
  Navigate: () => null,
}))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <div>{children}{foot}</div> }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup: vi.fn() }) }))

const conn = (businessType, organizationCode, status = 'connected') => ({ businessType, organizationCode, status })

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => {
  vi.clearAllMocks()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

const row = (name) => [...host.querySelectorAll('.mc-inst')].find((r) => r.querySelector('strong').textContent === name)
const render = (items) => {
  useApi.mockReturnValue({ data: items ? { items } : null, error: null })
  return act(() => root.render(<ConnectAuthPage />))
}

it('이미 연결된 기관은 연결 완료로 보이고 바로 다음 단계로 갈 수 있다', async () => {
  await render([conn('bank', '0088'), conn('card', '0303')])
  expect(row('신한은행').textContent).toContain('이미 연결된 기관이에요')
  expect(row('신한은행').querySelector('button')).toBeNull()
  expect(row('삼성카드').textContent).toContain('이미 연결된 기관이에요')
  expect(row('국민은행').textContent).toContain('로그인이 필요해요')
  const next = [...host.querySelectorAll('button')].find((b) => b.textContent.includes('보유 계좌 보기'))
  expect(next.textContent).toContain('은행 1곳')
  await act(async () => next.click())
  expect(navigate).toHaveBeenCalledWith('/finance/connect/accounts', { state: { cards: true } })
})

it('다시 로그인이 필요한 연결 · 목록을 못 받은 경우는 예전처럼 로그인부터', async () => {
  await render([conn('bank', '0088', 'relogin')])
  expect(row('신한은행').textContent).toContain('로그인이 필요해요')
  await render(null)
  expect(row('신한은행').textContent).toContain('로그인이 필요해요')
  expect(row('국민은행').textContent).toContain('로그인이 필요해요')
})
