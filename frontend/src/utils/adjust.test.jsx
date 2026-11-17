import { expect, it } from 'vitest'
import { adjustmentChoices } from './adjust.js'

it('월급을 고치기 전 안내 단계에는 [이전 예산 조정 이어하기]를 보이지 않는다', () => {
  const choices = [{ value: 'salary_resume', label: '이전 예산 조정 이어하기' }, { value: 'cancel', label: '그만하기' }]
  const handoff = { choices, budgetAdjustment: { status: 'awaiting_input', phase: 'salary_handoff' } }
  expect(adjustmentChoices(handoff)).toEqual([{ value: 'cancel', label: '그만하기' }])
  expect(handoff.choices).toHaveLength(2)  // 월급 화면에서 돌아올 때 자동으로 이어 가는 데 쓰므로 원본은 그대로
  expect(adjustmentChoices({ choices, budgetAdjustment: { status: 'awaiting_input', phase: 'salary_allocation_review' } })).toEqual([])
})

it('비교 박스의 수정 버튼은 바깥에 중복하지 않고 다른 단계에서는 유지한다', () => {
  const edits = [{ value: 'manual', label: '금액 바꾸기' }, { value: 'manual_living', label: '줄일 항목 직접 고르기' }]
  const choices = [...edits, { value: 'cancel', label: '취소' }]
  const plans = [{ status: 'ready', amount: 10000, result: { changes: [{ category: 'FOOD', diff: 10000 }] } }]
  expect(adjustmentChoices({ choices, budgetAdjustment: { comparison: { plans } } })).toEqual([choices[2]])
  expect(adjustmentChoices({ choices, budgetAdjustment: { phase: 'funding_scope' } })).toEqual(choices)
  expect(adjustmentChoices({ choices, budgetAdjustment: { comparison: { plans: [] } } })).toEqual(choices)
  expect(adjustmentChoices({ choices, budgetAdjustment: { comparison: { plans: [{ status: 'needs_data' }] } } })).toEqual(choices)
  const destination = { id: 'living', status: 'needs_destination', amount: 0, result: null }
  const offered = [...choices, { value: 'plan_living', label: '다른 생활비에 분배' }]
  expect(adjustmentChoices({ choices: offered, budgetAdjustment: { comparison: { plans: [destination] } } })).toEqual([choices[2]])
  expect(adjustmentChoices({ choices, budgetAdjustment: { comparison: { plans: [destination] } } })).toEqual(choices)
})
