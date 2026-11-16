import { expect, it } from 'vitest'
import { adjustmentChoices } from './adjust.js'

it('월급을 고치기 전 안내 단계에는 [이전 예산 조정 이어하기]를 보이지 않는다', () => {
  const choices = [{ value: 'salary_resume', label: '이전 예산 조정 이어하기' }, { value: 'cancel', label: '그만하기' }]
  const handoff = { choices, budgetAdjustment: { status: 'awaiting_input', phase: 'salary_handoff' } }
  expect(adjustmentChoices(handoff)).toEqual([{ value: 'cancel', label: '그만하기' }])
  expect(handoff.choices).toHaveLength(2)  // 월급 화면에서 돌아올 때 자동으로 이어 가는 데 쓰므로 원본은 그대로
  expect(adjustmentChoices({ choices, budgetAdjustment: { status: 'awaiting_input', phase: 'salary_allocation_review' } })).toEqual([])
})
