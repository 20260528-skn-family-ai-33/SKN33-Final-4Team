import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import BudgetAdjustConsent from './BudgetAdjustConsent.jsx'
import BudgetAdjustment from './BudgetAdjustment.jsx'
import { apiSend } from '../utils/api.js'

vi.mock('../utils/api.js', () => ({ apiSend: vi.fn() }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const doc = (title) => ({ title, label: title, sections: [{ h: '전송 항목', items: ['합성 카테고리 집계'] }] })
const state = { available: true, canRevoke: false, status: 'not_granted', documentVersion: 'v1', documentHash: 'hash', document: doc('외부 전송 동의'),
  healthDocumentVersion: 'v1', healthDocumentHash: 'health-hash', healthDocument: doc('건강 요약 별도 동의') }
const button = (text) => [...host.querySelectorAll('button')].find((el) => el.textContent.trim() === text)
const click = async (el) => act(() => el.click())
beforeEach(() => { vi.resetAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('동의는 자동 조회·자동 선택하지 않고 각 문서와 두 체크박스를 확인한다', async () => {
  apiSend.mockResolvedValueOnce(state).mockResolvedValueOnce({ ...state, status: 'granted' })
  await act(() => root.render(<BudgetAdjustConsent />))
  expect(apiSend).not.toHaveBeenCalled()
  await click(button('예산 조정 AI 동의 관리 열기'))
  const boxes = [...host.querySelectorAll('input[type="checkbox"]')]
  expect(boxes).toHaveLength(2)
  expect(boxes.every((box) => !box.checked)).toBe(true)
  expect(button('이 기능의 AI 전송에 동의').disabled).toBe(true)
  await click(boxes[0])
  expect(button('이 기능의 AI 전송에 동의').disabled).toBe(true)
  await click(boxes[1])
  await click(button('이 기능의 AI 전송에 동의'))
  expect(apiSend).toHaveBeenLastCalledWith('/api/budgets/adjustment-ai-consent/', 'PUT', {
    agreed: true, healthAgreed: true, documentVersion: 'v1', documentHash: 'hash',
    healthDocumentVersion: 'v1', healthDocumentHash: 'health-hash',
  })
})

it('분배 동의와 조정 동의는 서로 다른 경로를 사용하고 철회는 해당 기능만 처리한다', async () => {
  apiSend.mockResolvedValueOnce({ ...state, status: 'granted' }).mockResolvedValueOnce(state)
  await act(() => root.render(<BudgetAdjustConsent scope="design" />))
  await click(button('예산 분배 AI 동의 관리 열기'))
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/ai-consent/', 'GET')
  await click(button('이 기능의 AI 전송 동의 철회'))
  expect(apiSend).toHaveBeenLastCalledWith('/api/budgets/ai-consent/', 'DELETE', { confirm: true })
})

it('동의 조회 실패는 미동의 확정으로 바꾸거나 전송 동의를 저장하지 않는다', async () => {
  apiSend.mockRejectedValueOnce(new Error('자료를 불러오지 못했어요.'))
  await act(() => root.render(<BudgetAdjustConsent />))
  await click(button('예산 조정 AI 동의 관리 열기'))
  expect(host.querySelector('[role="alert"]').textContent).toContain('불러오지 못했어요')
  expect(host.querySelectorAll('input')).toHaveLength(0)
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('기능이 꺼져 있으면 신규 동의 없이 갱신이 필요한 과거 동의도 철회할 수 있다', async () => {
  apiSend.mockResolvedValueOnce({ ...state, available: false, canRevoke: true })
    .mockResolvedValueOnce({ ...state, available: false })
  await act(() => root.render(<BudgetAdjustConsent scope="design" />))
  await click(button('예산 분배 AI 동의 관리 열기'))
  expect(host.textContent).toContain('새 동의는 받지 않아요')
  expect(host.querySelectorAll('input[type="checkbox"]')).toHaveLength(0)
  expect(button('이 기능의 AI 전송에 동의')).toBeUndefined()
  await click(button('이 기능의 AI 전송 동의 철회'))
  expect(apiSend).toHaveBeenLastCalledWith('/api/budgets/ai-consent/', 'DELETE', { confirm: true })
})

it('문서를 연 뒤 기능이 꺼져 저장이 거절돼도 신규 동의 UI를 닫는다', async () => {
  apiSend.mockResolvedValueOnce(state)
    .mockRejectedValueOnce(Object.assign(new Error('이 AI 기능이 꺼져 있어요.'), { code: 'budget_ai_unavailable' }))
  await act(() => root.render(<BudgetAdjustConsent />))
  await click(button('예산 조정 AI 동의 관리 열기'))
  for (const box of host.querySelectorAll('input')) await click(box)
  await click(button('이 기능의 AI 전송에 동의'))
  expect(host.querySelectorAll('input[type="checkbox"]')).toHaveLength(0)
  expect(button('이 기능의 AI 전송에 동의')).toBeUndefined()
  expect(host.querySelector('[role="alert"]').textContent).toContain('꺼져')
})

it('문서가 바뀌면 이전 체크 상태를 지우고 새 문서에 다시 선택하게 한다', async () => {
  apiSend.mockResolvedValueOnce(state)
    .mockRejectedValueOnce(Object.assign(new Error('동의 문서가 바뀌었어요.'), { code: 'document_outdated' }))
    .mockResolvedValueOnce({ ...state, documentVersion: 'v2', documentHash: 'new-hash' })
  await act(() => root.render(<BudgetAdjustConsent />))
  await click(button('예산 조정 AI 동의 관리 열기'))
  for (const box of host.querySelectorAll('input')) await click(box)
  await click(button('이 기능의 AI 전송에 동의'))
  await click(button('동의 문서 다시 불러오기'))
  expect([...host.querySelectorAll('input')].every((box) => !box.checked)).toBe(true)
  expect(button('이 기능의 AI 전송에 동의').disabled).toBe(true)
})

it('복합안의 예산 펼침은 저장·선택을 하지 않고 선택 버튼은 정확한 서버 안 번호를 보낸다', async () => {
  const pick = vi.fn(), approve = vi.fn()
  const choice = { value: 'financial_0', label: '생활비 + 여윳돈' }
  const content = { choices: [choice], budgetAdjustment: { status: 'awaiting_input', canApprove: false,
    comparison: { basisKind: 'forecast_shortfall', basisAmount: 120000, amountWasGiven: false,
      plans: [{ id: '0', label: '생활비 + 여윳돈', status: 'ready', amount: 120000, maximum: 250000, recommended: true,
        assessment: { label: '추천', reasons: ['저축과 월 쿠션을 유지해요.'], cautions: [], facts: [] },
        result: { changes: [{ category: 'FOOD', before: 400000, after: 520000, diff: 120000, direct: true }], shortfalls: [] } }],
      recommendation: { planId: '0' } } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={pick} onApprove={approve} />))
  expect(host.textContent).toContain('지출 추정에 따른 증액 제안')
  expect(host.textContent).toContain('최대 조정 가능액')
  await click(host.querySelector('details summary'))
  expect(pick).not.toHaveBeenCalled()
  expect(approve).not.toHaveBeenCalled()
  await click(button('이 안 선택'))
  expect(pick).toHaveBeenCalledWith(choice)
  expect(approve).not.toHaveBeenCalled()
})
