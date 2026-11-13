import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import BudgetAdjustConsentPopup from './BudgetAdjustConsentPopup.jsx'
import { apiSend } from '../utils/api.js'

const { closePopup, toast } = vi.hoisted(() => ({ closePopup: vi.fn(), toast: vi.fn() }))
vi.mock('../utils/api.js', () => ({ apiSend: vi.fn() }))
vi.mock('./context.js', () => ({ usePopup: () => ({ closePopup }) }))
vi.mock('../utils/toast.js', () => ({ toast }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const URL = '/api/budgets/adjustment-ai-consent/'
const doc = (title) => ({ title, label: `[필수] ${title}`, sections: [{ h: '전송 항목', items: [`${title} 본문`] }] })
const state = { available: true, canRevoke: false, status: 'not_granted', documentVersion: 'v1', documentHash: 'hash', document: doc('외부 전송 동의'),
  healthDocumentVersion: 'v1', healthDocumentHash: 'health-hash', healthDocument: doc('건강 요약 처리 동의') }
const button = (text) => [...host.querySelectorAll('button')].find((el) => el.textContent.trim() === text)
const boxes = () => [...host.querySelectorAll('input[type="checkbox"]')]
const click = async (el) => act(() => el.click())
const open = async (onAgreed = vi.fn()) => { await act(() => root.render(<BudgetAdjustConsentPopup onAgreed={onAgreed} />)); return onAgreed }
beforeEach(() => { vi.resetAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('문서를 불러오되 자동으로 체크하지 않고, 두 항목을 모두 직접 체크해야 동의하고 계속할 수 있다', async () => {
  apiSend.mockResolvedValueOnce(state).mockResolvedValueOnce({ ...state, status: 'granted' })
  const onAgreed = await open()
  expect(apiSend).toHaveBeenCalledWith(URL, 'GET')
  expect(host.querySelector('[role="dialog"]').getAttribute('aria-label')).toBe('예산 조정 AI 전송 동의')
  expect(boxes()).toHaveLength(2)
  expect(boxes().every((box) => !box.checked)).toBe(true)
  expect(button('동의하고 계속').disabled).toBe(true)
  await click(boxes()[0])
  expect(button('동의하고 계속').disabled).toBe(true)
  await click(boxes()[1])
  await click(button('동의하고 계속'))
  expect(apiSend).toHaveBeenLastCalledWith(URL, 'PUT', {
    agreed: true, healthAgreed: true, documentVersion: 'v1', documentHash: 'hash',
    healthDocumentVersion: 'v1', healthDocumentHash: 'health-hash',
  })
  expect(closePopup).toHaveBeenCalledOnce()
  expect(onAgreed).toHaveBeenCalledOnce()
  expect(toast).toHaveBeenCalledWith('동의했어요. 철회는 마이에서 할 수 있어요')  // 철회 안내는 창 글자 대신 토스트로 (11/12)
  expect(host.textContent).not.toContain('동의 이력 확인과 철회')
})

it('문서 [보기]는 창 안에서 펼쳐 보여 주고 체크 상태를 바꾸지 않는다', async () => {
  apiSend.mockResolvedValueOnce(state)
  await open()
  expect(host.textContent).not.toContain('외부 전송 동의 본문')
  await click(host.querySelector('.mc-terms-open'))
  expect(host.textContent).toContain('외부 전송 동의 본문')
  expect(host.querySelector('.mc-terms-open').getAttribute('aria-expanded')).toBe('true')
  expect(boxes().every((box) => !box.checked)).toBe(true)
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('[닫기 ×]를 누르면 아무것도 저장하지 않고 창만 닫는다 ([취소]는 없다, 11/12)', async () => {
  apiSend.mockResolvedValueOnce(state)
  const onAgreed = await open()
  await click(boxes()[0])
  expect(button('취소')).toBeUndefined()
  await click(button('닫기 ×'))
  expect(closePopup).toHaveBeenCalledOnce()
  expect(onAgreed).not.toHaveBeenCalled()
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('문서를 못 불러오면 동의 칸을 보이지 않고 다시 불러오게 한다', async () => {
  apiSend.mockRejectedValueOnce(Object.assign(new Error('자료를 불러오지 못했어요.'), { status: 500 })).mockResolvedValueOnce(state)
  await open()
  expect(host.querySelector('[role="alert"]').textContent).toContain('불러오지 못했어요')
  expect(boxes()).toHaveLength(0)
  expect(button('동의하고 계속')).toBeUndefined()
  await click(button('동의 문서 다시 불러오기'))
  expect(boxes()).toHaveLength(2)
})

it('기능이 꺼져 있으면 새 동의를 받지 않는다', async () => {
  apiSend.mockResolvedValueOnce({ ...state, available: false })
  const onAgreed = await open()
  expect(host.textContent).toContain('새 동의는 받지 않아요')
  expect(boxes()).toHaveLength(0)
  expect(button('동의하고 계속')).toBeUndefined()
  expect(onAgreed).not.toHaveBeenCalled()
})

it('저장하는 사이 기능이 꺼지면 동의 칸을 닫고 계속하지 않는다', async () => {
  apiSend.mockResolvedValueOnce(state)
    .mockRejectedValueOnce(Object.assign(new Error('이 AI 기능이 꺼져 있어요.'), { status: 409, code: 'budget_ai_unavailable' }))
  const onAgreed = await open()
  for (const box of boxes()) await click(box)
  await click(button('동의하고 계속'))
  expect(boxes()).toHaveLength(0)
  expect(host.querySelector('[role="alert"]').textContent).toContain('꺼져')
  expect(onAgreed).not.toHaveBeenCalled()
  expect(closePopup).not.toHaveBeenCalled()
})

it('문서가 바뀌면 체크를 지우고 새 문서로 다시 확인하게 한다', async () => {
  apiSend.mockResolvedValueOnce(state)
    .mockRejectedValueOnce(Object.assign(new Error('동의 문서가 바뀌었어요.'), { status: 409, code: 'document_outdated' }))
    .mockResolvedValueOnce({ ...state, documentVersion: 'v2', documentHash: 'new-hash' })
    .mockResolvedValueOnce({ ...state, status: 'granted' })
  const onAgreed = await open()
  for (const box of boxes()) await click(box)
  await click(button('동의하고 계속'))
  expect(host.querySelector('[role="alert"]').textContent).toContain('동의 내용이 바뀌었어요')
  expect(boxes().every((box) => !box.checked)).toBe(true)
  expect(onAgreed).not.toHaveBeenCalled()
  for (const box of boxes()) await click(box)
  await click(button('동의하고 계속'))
  expect(apiSend).toHaveBeenLastCalledWith(URL, 'PUT', expect.objectContaining({ documentVersion: 'v2', documentHash: 'new-hash' }))
  expect(onAgreed).toHaveBeenCalledOnce()
})

it('이미 동의한 상태면 다시 받지 않고 바로 계속하게 한다', async () => {
  apiSend.mockResolvedValueOnce({ ...state, status: 'granted' })
  const onAgreed = await open()
  expect(boxes()).toHaveLength(0)
  await click(button('계속하기'))
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(onAgreed).toHaveBeenCalledOnce()
})
