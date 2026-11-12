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

it('여러 받을 항목과 금액을 직접 정하고 합계가 맞을 때만 미리보기를 보낸다', async () => {
  const send = vi.fn()
  const content = { turn: 2, budgetAdjustment: { status: 'awaiting_input', distributionInput: {
    source: 'EDUCATION', amount: 50000, categories: ['FOOD', 'TRANSPORT_CAR', 'SPARE'] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  expect(host.querySelectorAll('select')).toHaveLength(0)
  expect(host.querySelectorAll('[aria-pressed="true"]')).toHaveLength(0)
  expect(button('나눈 예산 미리보기').disabled).toBe(true)
  await click(button('식비'))
  await click(button('교통·차량'))
  expect(host.querySelectorAll('[aria-pressed="true"]')).toHaveLength(2)
  for (let i = 0; i < 2; i++) await click(host.querySelector('[aria-label="1번 금액 10,000원 늘리기"]'))
  expect(button('나눈 예산 미리보기').disabled).toBe(true)
  for (let i = 0; i < 3; i++) await click(host.querySelector('[aria-label="2번 금액 10,000원 늘리기"]'))
  expect(host.querySelector('[role="status"]').textContent).toContain('모두 나눴어요')
  expect(host.querySelector('[aria-label="1번 금액 10,000원 늘리기"]').disabled).toBe(true)
  expect(host.querySelector('[aria-label="2번 금액 10,000원 늘리기"]').disabled).toBe(true)
  const input = host.querySelector('input')
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '130000')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  expect(input.value).toBe('20,000')
  await click(button('나눈 예산 미리보기'))
  expect(send).toHaveBeenCalledWith('기존 배분 대신 교육 예산을 50000원 줄여 식비에 20000원, 교통·차량에 30000원 나눠줘.')
  await click(button('여윳돈'))
  expect(host.querySelectorAll('.mc-budget-distribution-row')).toHaveLength(3)
  expect(button('나눈 예산 미리보기').disabled).toBe(true)
  await click(button('✓ 여윳돈'))
  expect(button('나눈 예산 미리보기').disabled).toBe(false)
  await act(() => root.render(<BudgetAdjustment content={content} active={false} onSend={send} />))
  expect(host.querySelector('fieldset').disabled).toBe(true)
})

it('감액 입력과 더하기 버튼은 서버가 확인한 최대 금액을 넘지 않는다', async () => {
  const send = vi.fn()
  const content = { budgetAdjustment: { status: 'awaiting_input', amountInput: { category: 'EDUCATION', direction: 'down', maxAmount: 50000 } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  for (let i = 0; i < 5; i++) await click(button('+10,000원'))
  const input = host.querySelector('input')
  expect(input.value).toBe('50,000')
  expect(button('+10,000원').disabled).toBe(true)
  await click(button('+10,000원'))
  expect(input.value).toBe('50,000')
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '130000')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  expect(input.value).toBe('50,000')
  expect(host.textContent).toContain('최대 50,000원')
  await click(button('미리보기'))
  expect(send).toHaveBeenCalledWith('교육을 5만원 줄이고 싶어요')
  await click(button('−10,000원'))
  expect(input.value).toBe('40,000')
  expect(button('+10,000원').disabled).toBe(false)
})

it.each([0, undefined])('감액 상한이 %s이면 금액을 임의로 늘리거나 전송하지 않는다', async (maxAmount) => {
  const send = vi.fn()
  const content = { budgetAdjustment: { status: 'awaiting_input', amountInput: { category: 'EDUCATION', direction: 'down', maxAmount } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  expect(button('+10,000원').disabled).toBe(true)
  expect(button('미리보기').disabled).toBe(true)
  expect(send).not.toHaveBeenCalled()
})

it('추천 질문의 답은 추천하는 방법과 이유를 카드 위에 바로 보여 준다', async () => {
  const content = { budgetAdjustment: { status: 'awaiting_input', recommendationAdvice: {
    title: '다른 생활비에 배분을 추천해요.', reasons: ['중요하게 고른 다른 예산을 줄이지 않아도 돼요.'] },
    comparison: { plans: [{ id: 'living', label: '다른 생활비에 배분', status: 'ready', amount: 50000,
      result: { changes: [{ category: 'FOOD', before: 400000, after: 450000, diff: 50000 }] } }] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={vi.fn()} />))
  const advice = host.querySelector('[aria-label="추천하는 이유"]')
  expect(advice.textContent).toContain('추천해요')
  expect(advice.textContent).toContain('다른 예산을 줄이지')
  expect(advice.closest('details')).toBeNull()
})

it('확정 전 변경안은 받는 항목까지 모두 펼쳐 보여 준다', async () => {
  const content = { budgetAdjustment: { status: 'replied', canApprove: true, proposal: { state: 'awaiting_approval',
    changes: [{ category: 'MEDICAL_HEALTH', before: 60000, after: 10000, diff: -50000, direct: true },
      { category: 'FOOD', before: 400000, after: 450000, diff: 50000 }] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onApprove={vi.fn()} />))
  const proposal = host.querySelector('.mc-budget-proposal')
  expect(proposal.textContent).toContain('식비')
  expect(proposal.textContent).toContain('450,000원')
  expect(proposal.querySelector('details')).toBeNull()
})

it('배분 수정은 선택할 때만 열리고 기존 배분액 안에서 다시 나눈다', async () => {
  const send = vi.fn(), approve = vi.fn()
  const content = { budgetAdjustment: { status: 'replied', canApprove: true,
    distributionEdit: { source: 'EDUCATION', amount: 50000, categories: ['FOOD', 'TRANSPORT_CAR', 'SPARE'],
      values: [{ category: 'FOOD', amount: 50000 }] },
    proposal: { proposalId: 'p1', state: 'awaiting_approval', allocations: { FOOD: 450000, EDUCATION: 0 },
      changes: [{ category: 'EDUCATION', before: 50000, after: 0, diff: -50000 },
        { category: 'FOOD', before: 400000, after: 450000, diff: 50000 }] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} onApprove={approve} />))
  expect(host.querySelector('input')).toBeNull()
  expect(button('예산 확정하기').disabled).toBe(false)
  await click(button('배분 수정'))
  expect(host.querySelector('select')).toBeNull()
  expect(button('예산 확정하기')).toBeUndefined()
  expect(host.querySelector('input').value).toBe('50,000')
  expect(button('나눈 예산 미리보기').disabled).toBe(true)
  expect(host.querySelector('[aria-label="1번 금액 10,000원 늘리기"]').disabled).toBe(true)
  await click(button('교통·차량'))
  for (let i = 0; i < 2; i++) await click(host.querySelector('[aria-label="1번 금액 10,000원 줄이기"]'))
  expect(host.querySelector('[role="status"]').textContent).toContain('20,000원 남음')
  expect(button('나눈 예산 미리보기').disabled).toBe(true)
  for (let i = 0; i < 2; i++) await click(host.querySelector('[aria-label="2번 금액 10,000원 늘리기"]'))
  expect(send).not.toHaveBeenCalled()
  await click(button('수정 취소'))
  expect(host.querySelector('input')).toBeNull()
  expect(button('예산 확정하기').disabled).toBe(false)
  expect(send).not.toHaveBeenCalled()
  await click(button('배분 수정'))
  expect(host.querySelector('input').value).toBe('50,000')
  await click(button('교통·차량'))
  await click(host.querySelector('[aria-label="1번 금액 10,000원 줄이기"]'))
  await click(host.querySelector('[aria-label="2번 금액 10,000원 늘리기"]'))
  await click(button('나눈 예산 미리보기'))
  expect(send).toHaveBeenCalledWith('기존 배분 대신 교육 예산을 50000원 줄여 식비에 40000원, 교통·차량에 10000원 나눠줘.')
  expect(approve).not.toHaveBeenCalled()
})

it('금액 버튼은 입력만 바꾸고 미리보기에서만 감액 요청을 보낸다', async () => {
  const send = vi.fn(), approve = vi.fn()
  const content = { turn: 1, lines: ['긴 설명'], choices: [], budgetAdjustment: { status: 'awaiting_input',
    amountInput: { category: 'MEDICAL_HEALTH', direction: 'down', maxAmount: 50000 },
    explanation: { brief: '긴 안내', why: '최소 금액의 상세 근거' } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} onApprove={approve} />))
  const input = host.querySelector('input')
  expect(host.querySelector('legend').textContent).toBe('의료·건강 예산을 얼마 줄일까요?')
  expect(host.textContent).not.toContain('긴 설명')
  expect(host.textContent).not.toContain('긴 안내')
  expect(host.querySelector('details').open).toBe(false)
  expect(button('미리보기').disabled).toBe(true)
  await click(button('+10,000원'))
  await click(button('+10,000원'))
  expect(input.value).toBe('20,000')
  await click(button('−10,000원'))
  expect(input.value).toBe('10,000')
  expect(send).not.toHaveBeenCalled()
  await click(button('미리보기'))
  expect(send).toHaveBeenCalledWith('의료·건강을 1만원 줄이고 싶어요')
  expect(approve).not.toHaveBeenCalled()
  await click(button('−10,000원'))
  expect(input.value).toBe('0')
  expect(button('미리보기').disabled).toBe(true)
  expect(button('−10,000원').disabled).toBe(true)
})

it('직접 입력은 5천원 단위를 확인하고 이전 답변에서는 전송하지 않는다', async () => {
  const send = vi.fn()
  const content = { turn: 1, budgetAdjustment: { status: 'awaiting_input', amountInput: { category: 'FOOD', direction: 'up' } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  const input = host.querySelector('input')
  const enter = async (text) => act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await enter('1234')
  expect(button('미리보기').disabled).toBe(true)
  expect(host.querySelector('[role="status"]').textContent).toContain('5,000원 단위')
  await enter('15000')
  await act(() => input.form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(send).toHaveBeenCalledWith('식비를 15,000원 늘리고 싶어요')
  await act(() => root.render(<BudgetAdjustment content={content} active={false} onSend={send} />))
  expect(host.querySelector('fieldset').disabled).toBe(true)
  await act(() => input.form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(send).toHaveBeenCalledTimes(1)
})

it('복합안의 예산 펼침은 저장·선택을 하지 않고 선택 버튼은 정확한 서버 안 번호를 보낸다', async () => {
  const pick = vi.fn(), approve = vi.fn()
  const choice = { value: 'financial_0', label: '생활비 + 여윳돈' }
  const content = { choices: [choice], budgetAdjustment: { status: 'awaiting_input', canApprove: false,
    requestSummary: '식비 늘리기 · 금액은 함께 정하기 / 유지할 항목: 저축',
    comparison: { basisKind: 'forecast_shortfall', basisAmount: 120000, amountWasGiven: false,
      plans: [{ id: '0', label: '생활비 + 여윳돈', status: 'ready', amount: 120000, maximum: 250000, recommended: true,
        assessment: { label: '추천', reasons: ['저축과 월 쿠션을 유지해요.'], cautions: [], facts: [] },
        result: { changes: [{ category: 'FOOD', before: 400000, after: 520000, diff: 120000, direct: true }], shortfalls: [] } }],
      recommendation: { planId: '0' } } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={pick} onApprove={approve} />))
  expect(host.textContent).toContain('최근 지출을 참고해 제안하는 금액')
  expect(host.querySelector('[aria-label="이해한 요청"]').textContent).toContain('유지할 항목: 저축')
  expect(host.firstElementChild.firstElementChild.getAttribute('aria-label')).toBe('이해한 요청')
  expect(host.textContent).toContain('옮길 수 있는 최대 금액')
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

it('초기 질문에는 예전 응답에 남은 추천·비교 선택지도 표시하지 않는다', async () => {
  const content = { lines: ['의료·건강 예산을 얼마나 줄일까요?'],
    choices: [{ value: 'recommend', label: '추천안 보기' }, { value: 'compare', label: '비교하기' }],
    budgetAdjustment: { status: 'awaiting_input', phase: 'amount', canApprove: false } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={vi.fn()} />))
  expect(host.textContent).toContain('얼마나 줄일까요?')
  expect(host.querySelector('.mc-budget-navigation')).toBeNull()
})

it('검수한 비교 카드의 추천 버튼은 무엇을 보여주는지 설명하는 서버 문구를 쓴다', async () => {
  const pick = vi.fn()
  const choice = { value: 'recommend', label: '추천한 조정안 미리보기' }
  const content = { choices: [choice], budgetAdjustment: { status: 'awaiting_input', canApprove: false,
    comparison: { plans: [{ id: '0', label: '여윳돈에서 가져오기', status: 'ready', amount: 10000,
      result: { changes: [{ category: 'FOOD', before: 400000, after: 410000, diff: 10000 }] } }],
      recommendation: { planId: '0' } } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={pick} />))
  await click(button(choice.label))
  expect(pick).toHaveBeenCalledWith(choice)
  expect(button('추천안 보기')).toBeUndefined()
})

it('계산한 예산 변화가 없는 안은 펼침과 선택 버튼도 보여 주지 않는다', async () => {
  const content = { lines: ['의료·건강에 50,000원을 남기도록 계산해요. 원하는 금액을 알려 주세요.'],
    choices: [{ value: 'plan_living', label: '다른 생활비에 배분' }],
    budgetAdjustment: { status: 'awaiting_input', canApprove: false,
      comparison: { plans: [{ id: 'living', label: '다른 생활비에 배분', status: 'needs_destination', result: null }] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={vi.fn()} />))
  expect(host.querySelector('.mc-budget-plan')).toBeNull()
  expect(button('이 안 선택')).toBeUndefined()
  expect(host.textContent).not.toContain('필요한 항목')
  expect(host.textContent).toContain('의료·건강에 50,000원을 남기도록')
  expect(host.textContent).not.toContain('지금 확인된 정보로 보여 드릴 조정안이 없어요')
})
