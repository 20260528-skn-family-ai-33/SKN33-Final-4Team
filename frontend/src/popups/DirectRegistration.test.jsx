import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import FixedPopup from './FixedPopup.jsx'
import SavingPopup from './SavingPopup.jsx'
import { apiSend, useApi } from '../utils/api.js'

const { closePopup } = vi.hoisted(() => ({ closePopup: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), useApi: vi.fn(), newRequestKey: () => 'popup-key' }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))

const accounts = [
  { accountId: 'salary-id', displayName: '합성 월급통장 · ****1001', isSelected: true, status: 'connected' },
  { accountId: 'deposit-id', displayName: '합성 첫목돈적금 · ****2001', isSelected: false, status: 'connected' },
]
const cards = [{ cardId: 'credit-id', displayName: '합성 일상 신용 · ****3002', isSelected: true, status: 'connected' }]
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => {
  vi.clearAllMocks()
  useApi.mockImplementation((url) => ({ data: { items: url.includes('/accounts/') ? accounts : cards } }))
  apiSend.mockResolvedValue({})
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
})

async function choose(select, value) {
  await act(() => {
    select.value = value
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

it.each(['account', 'card'])('목록에서 고른 %s로 고정지출을 저장한다', async (kind) => {
  await act(() => root.render(<FixedPopup />))
  if (kind === 'card') {
    await act(() => [...host.querySelectorAll('button')].find((b) => b.textContent === '카드').click())
  }
  const selected = kind === 'account' ? accounts[0] : cards[0]
  const option = [...host.querySelectorAll('option')].find((o) => o.textContent === selected.displayName)
  expect(option).toBeTruthy()
  expect(host.textContent).not.toContain(accounts[1].displayName)
  await choose(option.parentElement, option.value)
  await choose([...host.querySelectorAll('select')].find((s) => s.textContent.includes('카테고리 선택')), '문화·여가')
  host.querySelector('[name=fxName]').value = '직접 등록 구독'
  host.querySelector('[name=fxAmount]').value = '12000'
  await act(() => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(useApi).toHaveBeenCalledWith('/api/finance/connect/accounts/?v=0')
  expect(useApi).toHaveBeenCalledWith('/api/finance/connect/cards/?v=0')
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/fixed/', 'POST', expect.objectContaining({
    paymentAccountId: kind === 'account' ? accounts[0].accountId : null,
    paymentCardId: kind === 'card' ? cards[0].cardId : null,
  }), { requestKey: 'popup-key' })
  expect(closePopup).toHaveBeenCalledOnce()
})

it('목록의 월급통장에서 미선택 적금계좌로 저축을 등록한다', async () => {
  await act(() => root.render(<SavingPopup />))
  const from = host.querySelector('[name=svFrom]')
  const to = host.querySelector('[name=svTo]')
  expect([...from.options].some((o) => o.value === accounts[1].accountId)).toBe(false)
  expect([...to.options].some((o) => o.textContent === accounts[1].displayName)).toBe(true)
  await choose(from, accounts[0].accountId)
  await choose(to, accounts[1].accountId)
  host.querySelector('[name=svName]').value = '직접 등록 적금'
  host.querySelector('[name=svAmount]').value = '900000'
  await act(() => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(useApi).toHaveBeenCalledWith('/api/finance/connect/accounts/?v=0')
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/savings/', 'POST', expect.objectContaining({
    fromAccountId: accounts[0].accountId, toAccountId: accounts[1].accountId,
  }), { requestKey: 'popup-key' })
  expect(closePopup).toHaveBeenCalledOnce()
})
