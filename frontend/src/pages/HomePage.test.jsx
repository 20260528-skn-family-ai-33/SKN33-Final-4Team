import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import HomePage from './HomePage.jsx'
import { useApi } from '../utils/api.js'
import { runFinanceSync } from '../utils/financeSync.js'
import { PATHS } from '../routes.js'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('react-router', () => ({ useNavigate: () => navigate, Navigate: () => null }))
vi.mock('../utils/api.js', () => ({ useApi: vi.fn() }))
vi.mock('../utils/financeSync.js', async (original) => ({ ...await original(), runFinanceSync: vi.fn() }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, headAction }) => <main>{headAction}{children}</main> }))
vi.mock('../popups/context.js', () => ({ usePopup: () => ({ openPopup: vi.fn() }) }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const reload = vi.fn()
const refresh = () => act(async () => host.querySelector('[aria-label="새로고침"]').click())

beforeEach(async () => {
  vi.clearAllMocks()
  useApi.mockReturnValue({ reload, data: { period: null, balance: { accountCount: 0, total: null }, pendingCount: 0, needsPaydayCount: 0, mustPay: [], budget: null } })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(() => root.render(<HomePage />))
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

it('연결 전부 불러온 뒤 홈 데이터를 다시 받고 실패한 기관은 연결 관리로 안내한다', async () => {
  runFinanceSync.mockResolvedValue({ items: [
    { organizationName: '신한은행', status: 'succeeded', newTransactionCount: 2 },
    { organizationName: '현대카드', status: 'failed', error: { message: '카드 비밀번호가 필요해요.' } },
  ] })
  await refresh()
  expect(runFinanceSync).toHaveBeenCalledWith()
  expect(reload).toHaveBeenCalledOnce()
  expect(host.textContent).toContain('신한은행 · 새 거래 2건을 불러왔어요')
  expect(host.textContent).toContain('현대카드 · 카드 비밀번호가 필요해요.')

  await act(async () => [...host.querySelectorAll('button')].find((b) => b.textContent.includes('연결 관리에서 확인')).click())
  expect(navigate).toHaveBeenCalledWith(PATHS.connections)
})

it('불러오기를 못 하면 이유를 보여 주고 다시 받지 않는다', async () => {
  runFinanceSync.mockRejectedValue(new Error('테스트 환경에서는 금융 연결 · 다시 불러오기를 쓸 수 없어요.'))
  await refresh()
  expect(reload).not.toHaveBeenCalled()
  expect(host.querySelector('[role="alert"]').textContent).toContain('테스트 환경에서는')
  expect(host.querySelector('[aria-label="새로고침"]').disabled).toBe(false)
})
