import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import BudgetAdjustment from './BudgetAdjustment.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const button = (text) => [...host.querySelectorAll('button')].find((el) => el.textContent.trim() === text)
const click = async (el) => act(() => el.click())
beforeEach(() => { vi.resetAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })

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

it('답변 전체를 다른 답변과 같은 말풍선 안에 그린다', async () => {
  const content = { lines: ['어디에서 가져올까요?'], budgetAdjustment: { status: 'awaiting_input', canApprove: false } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={vi.fn()} onApprove={vi.fn()} />))
  const bubble = host.querySelector('.mc-bubble')
  expect(bubble.textContent).toBe('어디에서 가져올까요?')
  expect(host.children).toHaveLength(1)
  expect(host.firstElementChild).toBe(bubble)
})
