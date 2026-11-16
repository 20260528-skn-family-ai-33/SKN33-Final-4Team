// 거래 상세 — 카테고리 수정은 [카테고리 수정하기]를 눌러야 격자가 열리고, 칸은 고르기만 · [확인]을 눌러야 저장 (11/12)
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import TransactionPopup from './TransactionPopup.jsx'
import { apiSend } from '../utils/api.js'
import { CHIPS } from '../utils/transactions.js'

const { closePopup, detail } = vi.hoisted(() => ({ closePopup: vi.fn(), detail: { current: { canApplyToFuture: true } } }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: () => ({ data: detail.current }) }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))
vi.mock('../utils/toast.js', () => ({ toast: vi.fn() }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const tx = { id: 'tx-1', kind: 'normal', name: '합성 식당', cat: '식비', amount: 12_000, net: 12_000 }
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.trim() === text)
const click = (el) => act(async () => el.click())
const open = (props = {}) => act(() => root.render(<TransactionPopup tx={tx} date="11월 8일" categories={CHIPS} {...props} />))
beforeEach(() => {
  vi.resetAllMocks()
  detail.current = { canApplyToFuture: true }
  apiSend.mockResolvedValue({})
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('[카테고리 수정하기]를 눌러야 격자가 열리고, 칸을 골라도 [확인] 전에는 저장하지 않는다', async () => {
  const onSaved = vi.fn()
  await open({ onSaved })
  expect(host.querySelector('.mc-catgrid')).toBeNull()
  await click(button('카테고리 수정하기'))
  expect(host.querySelector('[aria-pressed="true"]').textContent).toBe('식비')  // 지금 카테고리가 골라져 있다
  expect(button('확인').disabled).toBe(true)  // 그대로면 저장할 게 없다
  await click(button('교통·차량'))
  expect(apiSend).not.toHaveBeenCalled()
  expect(host.querySelector('[aria-pressed="true"]').textContent).toBe('교통·차량')
  await click(button('확인'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/transactions/tx-1/', 'PATCH', { category: 'TRANSPORT_CAR', applyToFuture: false })
  expect(onSaved).toHaveBeenCalledOnce()
  expect(closePopup).toHaveBeenCalledOnce()
})

it('[취소]를 누르면 고른 것을 버리고 격자를 닫는다', async () => {
  await open()
  await click(button('카테고리 수정하기'))
  await click(button('교통·차량'))
  await click(button('취소'))
  expect(host.querySelector('.mc-catgrid')).toBeNull()
  expect(apiSend).not.toHaveBeenCalled()
  await click(button('카테고리 수정하기'))
  expect(host.querySelector('[aria-pressed="true"]').textContent).toBe('식비')
})

it('같은 카테고리라도 앞으로도 적용을 체크하면 저장할 수 있고, 체크 안 함 안내는 없다', async () => {
  await open()
  await click(button('카테고리 수정하기'))
  expect(host.textContent).not.toContain('체크하지 않으면')
  await click(host.querySelector('input[type="checkbox"]'))
  expect(button('확인').disabled).toBe(false)
  await click(button('확인'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/transactions/tx-1/', 'PATCH', { category: 'FOOD', applyToFuture: true })
})

it('남에게 보낸 이체도 [소비로 분류하기] → 칸 고르기 → [확인] 순서로 저장한다', async () => {
  detail.current = {}
  await open({ tx: { ...tx, kind: 'transfer', cat: null } })
  await click(button('소비로 분류하기'))
  expect(button('확인').disabled).toBe(true)
  await click(button('식비'))
  expect(apiSend).not.toHaveBeenCalled()
  await click(button('확인'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/transactions/tx-1/', 'PATCH', { category: 'FOOD', applyToFuture: false })
})

it('소비 거래는 [소비가 아니에요]로 소비에서 빼고, 소비 아님 거래는 [소비로 되돌리기]만 보여 준다 (11/13)', async () => {
  await open()
  await click(button('소비가 아니에요'))
  expect(apiSend).toHaveBeenCalledWith('/api/finance/transactions/tx-1/', 'PATCH', { notSpending: true })
  expect(closePopup).toHaveBeenCalledOnce()
  await act(() => root.render(<TransactionPopup key="excluded" tx={{ ...tx, kind: 'excluded', cat: null }} date="11월 8일" categories={CHIPS} />))  // 새 창 (저장 중 표시가 남지 않게)
  expect(host.querySelector('.mc-eyebrow').textContent).toBe('소비 아님')
  expect(button('카테고리 수정하기')).toBeUndefined()
  expect(button('소비가 아니에요')).toBeUndefined()
  await click(button('소비로 되돌리기'))
  expect(apiSend).toHaveBeenLastCalledWith('/api/finance/transactions/tx-1/', 'PATCH', { notSpending: false })
})

it('고정지출에 연결된 거래 · 전액 취소 거래에는 [소비가 아니에요]가 없다 (11/13)', async () => {
  await open({ tx: { ...tx, funding: 'fixed', fixedName: '넷플릭스' } })
  expect(button('소비가 아니에요')).toBeUndefined()
  await open({ tx: { ...tx, cancel: 'full', cancelAmount: 12_000 } })
  expect(button('소비가 아니에요')).toBeUndefined()
})
