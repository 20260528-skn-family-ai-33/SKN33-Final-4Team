// 월급 확인 팝업 (R12) — 합성 데이터로 단일 확인 + 자동 인식 · 분할 합계 · 이유 안내 · 자동 인식 끄기
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SalaryCheckPopup from './SalaryCheckPopup.jsx'
import { apiSend, useApi } from '../utils/api.js'

const { reload } = vi.hoisted(() => ({ reload: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn() }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup: vi.fn() }) }))

const account = { accountId: 'acc', displayName: '합성은행 월급통장 · ****1001', status: 'connected' }
const item = (id, extra = {}) => ({ transactionId: id, payCycleOn: '2026-09-25', date: '2026-09-25', amount: 2_500_000, description: '합성회사',
  account, decision: null, isPattern: false, isCandidate: true, patternUnusableReason: null, ...extra })
const body = (extra = {}) => ({ windows: [{ payCycleOn: '2026-09-25', from: '2026-09-18', to: '2026-10-17', items: [
  item('tx-1'), item('tx-2', { amount: 1_000_000, description: '입금', isCandidate: false, patternUnusableReason: '‘입금’ · ‘급여’ 같은 일반 문구라 자동 인식 기준으로 쓸 수 없어요' }),
] }], pattern: null, ...extra })

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const onSaved = vi.fn()
beforeEach(() => {
  vi.clearAllMocks()
  useApi.mockReturnValue({ data: body(), reload })
  apiSend.mockResolvedValue({})
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.includes(text))
const click = (el) => act(async () => el.click())
const render = () => act(() => root.render(<SalaryCheckPopup onSaved={onSaved} />))

it('후보 한 건을 골라 월급으로 확인하고 자동 인식을 명시적으로 켠다', async () => {
  await render()
  expect(host.textContent).toContain('월급일 수 있어요 · 확인 필요')
  expect(button('월급이 맞아요')).toBeUndefined()
  await click(button('합성회사'))
  const box = host.querySelector('input[type=checkbox]')
  expect(box.checked).toBe(false)   // 기본은 꺼짐 — 사용자가 직접 고른다
  await act(async () => box.click())
  await click(button('월급이 맞아요'))
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/decisions/', 'POST', { transactionIds: ['tx-1'], decision: 'confirmed', usePattern: true })
  expect(onSaved).toHaveBeenCalled()
})

it('여러 건은 합계로 확인하고 자동 인식 칸이 없다 · 일반 문구는 이유를 보여 준다', async () => {
  await render()
  await click(button('입금'))
  expect(host.textContent).toContain('일반 문구라 자동 인식 기준으로 쓸 수 없어요')
  await click(button('합성회사'))
  expect(host.textContent).toContain('3,500,000')
  expect(host.querySelector('input[type=checkbox]')).toBeNull()
  await click(button('월급이 아니에요'))
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/decisions/', 'POST', { transactionIds: ['tx-2', 'tx-1'], decision: 'rejected' })
})

it('자동 인식 기준을 끈다', async () => {
  useApi.mockReturnValue({ data: body({ pattern: item('tx-0', { decision: 'confirmed', isPattern: true }) }), reload })
  await render()
  await click(button('자동 인식 끄기'))
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/pattern/', 'DELETE', undefined)
})

it('과거 회차 재확인은 정상 분할 급여를 함께 유지하고 사용자가 다시 확정한다', async () => {
  useApi.mockReturnValue({ data: body({ windows: [
    { payCycleOn: '2026-11-25', from: '2026-11-18', to: '2026-12-17', items: [] },
    { payCycleOn: '2026-09-25', from: '2026-09-25', to: '2026-09-25', items: [
      item('changed', { description: '변경된 입금', decision: 'recheck', amount: 50_000 }),
      item('unchanged', { description: '정상 분할 급여', decision: 'confirmed' }),
    ] },
  ] }), reload })
  await render()
  expect(host.textContent).toContain('09.25 회차 월급')
  expect(host.textContent).toContain('다시 확인 필요')
  expect(button('월급이 맞아요')).toBeUndefined()
  await click(button('변경된 입금'))
  expect(host.querySelector('input[type=checkbox]')).toBeNull()
  await click(button('월급이 맞아요'))
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary/decisions/', 'POST',
    { transactionIds: ['unchanged', 'changed'], decision: 'confirmed', usePattern: false })
})
