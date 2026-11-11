// 홈 새로고침의 카드 값 팝업 — 그 연결만 카드번호 · 비밀번호를 넣어 다시 불러오고, 또 틀리면 팝업 안에서 알린다
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CardCredentialPopup from './CardCredentialPopup.jsx'
import { needsCardValues, runFinanceSync } from '../utils/financeSync.js'
import { toast } from '../utils/toast.js'

const { closePopup } = vi.hoisted(() => ({ closePopup: vi.fn() }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))
vi.mock('../utils/financeSync.js', async (orig) => ({ ...(await orig()), runFinanceSync: vi.fn() }))
vi.mock('../utils/toast.js', () => ({ toast: vi.fn() }))

const item = { connectionId: 'conn-1', organizationCode: '0302', organizationName: '현대카드' }
const failed = { ...item, status: 'failed', error: { code: 'invalid', message: '카드 값', fields: { cardNo: ['카드번호를 확인해 주세요.'] } } }
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => { vi.clearAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })
const type = (el, v) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v)
  el.dispatchEvent(new Event('input', { bubbles: true }))
})
const fillAndSend = async (pw = '1234') => {
  const [no, pass] = host.querySelectorAll('input')
  await type(no, '1234567890123456'); await type(pass, pw)
  await act(async () => host.querySelector('form').requestSubmit())
}

it('카드 값이 필요한 결과만 고른다', () => {
  expect(needsCardValues(failed)).toBe(true)
  expect(needsCardValues({ ...item, status: 'failed', error: { code: 'unavailable' } })).toBe(false)
  expect(needsCardValues({ ...item, status: 'succeeded' })).toBe(false)
})

it('그 연결만 카드 값과 함께 다시 불러오고, 성공하면 닫은 뒤 onDone', async () => {
  const onDone = vi.fn()
  runFinanceSync.mockResolvedValue({ items: [{ ...item, status: 'succeeded', newTransactionCount: 2 }] })
  await act(() => root.render(<CardCredentialPopup item={item} onDone={onDone} />))
  await fillAndSend()
  expect(runFinanceSync).toHaveBeenCalledWith({ connectionIds: ['conn-1'], cardCredentials: [{ connectionId: 'conn-1', cardNo: '1234567890123456', cardPassword: '1234' }] })
  expect(closePopup).toHaveBeenCalled()
  expect(onDone).toHaveBeenCalled()
  expect(toast).toHaveBeenCalledWith('카드 내역을 불러왔어요')
  expect(host.querySelectorAll('input')[0].value).toBe('')  // 보낸 값은 바로 지운다
})

it('현대카드는 비밀번호 4자리만 받고, 서버가 또 카드 값을 달라면 팝업 안에서 알린다', async () => {
  await act(() => root.render(<CardCredentialPopup item={item} />))
  await fillAndSend('12')
  expect(runFinanceSync).not.toHaveBeenCalled()
  expect(host.textContent).toContain('카드번호와 카드 비밀번호를 확인해 주세요.')
  runFinanceSync.mockResolvedValue({ items: [failed] })
  await fillAndSend()
  expect(closePopup).not.toHaveBeenCalled()
  expect(host.textContent).toContain('카드번호를 확인해 주세요.')
})

// PR #101 리뷰: 작업은 성공해도 그 카드사가 실패(카드사 장애 등)면 성공 안내 없이 팝업 안에서 알린다
it('카드 값 말고 다른 이유로 실패하면 닫지 않고 그 실패를 보여 준다', async () => {
  const onDone = vi.fn()
  runFinanceSync.mockResolvedValue({ items: [{ ...item, status: 'failed', error: { code: 'unavailable', message: '현대카드에서 잠시 응답하지 않아요.' } }] })
  await act(() => root.render(<CardCredentialPopup item={item} onDone={onDone} />))
  await fillAndSend()
  expect(closePopup).not.toHaveBeenCalled()
  expect(toast).not.toHaveBeenCalled()
  expect(onDone).not.toHaveBeenCalled()
  expect(host.querySelector('[role="alert"]').textContent).toBe('현대카드에서 잠시 응답하지 않아요.')
})

it('그 카드사 결과가 없거나 건너뛰었으면 성공으로 안내하지 않는다', async () => {
  await act(() => root.render(<CardCredentialPopup item={item} />))
  runFinanceSync.mockResolvedValue({ items: [{ ...item, connectionId: 'other', status: 'succeeded' }] })
  await fillAndSend()
  expect(host.querySelector('[role="alert"]').textContent).toBe('해당 카드사의 조회 결과를 확인하지 못했어요. 다시 시도해 주세요.')
  runFinanceSync.mockResolvedValue({ items: [{ ...item, status: 'skipped' }] })
  await fillAndSend()
  expect(host.querySelector('[role="alert"]').textContent).toContain('불러올 항목이 없어요')
  expect(closePopup).not.toHaveBeenCalled()
  expect(toast).not.toHaveBeenCalled()
})
