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

it('큰 증액 경고는 한 번 표시하고 확정 버튼을 막지 않으며 완료 후에는 숨긴다', async () => {
  const warning = '월 실수령액의 30% 이상을 늘리는 요청이에요. 저축이나 다른 예산이 크게 줄 수 있어요.'
  const approve = vi.fn()
  const content = { budgetAdjustment: { status: 'replied', increaseWarning: warning, canApprove: true,
    proposal: { proposalId: 'large', state: 'awaiting_approval', changes: [] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onApprove={approve} />))
  expect(host.textContent.split(warning)).toHaveLength(2)
  expect(button('예산 확정하기').disabled).toBe(false)
  await click(button('예산 확정하기'))
  expect(approve).toHaveBeenCalledOnce()
  content.budgetAdjustment.proposal.state = 'saved'
  await act(() => root.render(<BudgetAdjustment content={content} active onApprove={approve} />))
  expect(host.textContent).not.toContain(warning)
})

it.each([
  ['FOOD', 'up', 400000, 420000], ['EDUCATION', 'down', 50000, 30000],
  ['LIVING', 'up', 950000, 970000], ['SAVINGS', 'down', 600000, 580000],
])('%s 금액 입력과 버튼 조작마다 변경 전후 예산을 표시한다', async (category, direction, beforeAmount, expected) => {
  const send = vi.fn()
  const content = { budgetAdjustment: { status: 'awaiting_input', amountInput: {
    category, direction, beforeAmount, maxAmount: 50000 } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  const preview = () => host.querySelector('[aria-label="변경 전후 예산"]')
  const won = (value) => `${value.toLocaleString('ko-KR')}원`
  expect(preview().textContent).toBe(`변경 전 ${won(beforeAmount)} → 변경 후 ${won(beforeAmount)}`)
  const input = host.querySelector('input')
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '20000')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  expect(preview().textContent).toBe(`변경 전 ${won(beforeAmount)} → 변경 후 ${won(expected)}`)
  await click(button('+10,000원'))
  expect(preview().querySelector('strong').textContent).toBe(won(expected + (direction === 'up' ? 10000 : -10000)))
  await click(button('−10,000원'))
  expect(preview().querySelector('strong').textContent).toBe(won(expected))
  expect(send).not.toHaveBeenCalled()
})

it('이전 대화에 기준 예산이 없으면 0원으로 추측하지 않는다', async () => {
  await act(() => root.render(<BudgetAdjustment content={{ budgetAdjustment: {
    status: 'awaiting_input', amountInput: { category: 'FOOD', direction: 'up' } } }} active onSend={vi.fn()} />))
  expect(host.querySelector('[aria-label="변경 전후 예산"]').textContent).toBe('변경 전 확인 필요 → 변경 후 계산 대기')
})

it('증액 상한을 넘는 직접 입력은 임의로 바꾸지 않고 안내 후 전송을 막는다', async () => {
  const send = vi.fn()
  const content = { budgetAdjustment: { status: 'awaiting_input', amountInput: {
    category: 'LIVING', direction: 'up', maxAmount: 1200000 } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  const input = host.querySelector('input')
  const type = async (amount) => act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, amount)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await type('10000000')
  expect(input.value).toBe('10,000,000')
  expect(host.querySelector('[role="alert"]').textContent).toContain('1,200,000원')
  expect(button('미리보기').disabled).toBe(true)
  await act(() => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(send).not.toHaveBeenCalled()
  await type('50000')
  expect(host.querySelector('[role="alert"]')).toBeNull()
  await click(button('미리보기'))
  expect(send).toHaveBeenCalledWith('생활비를 5만원 늘리고 싶어요')
})

it('가져올 수 있는 금액과 미확인 사유를 항목별로 한 줄씩 보여준다', async () => {
  const lines = ['월 머니쿠션 · 최대 185,000원', '저축 · 사용 가능한 잔액 미확인', '여윳돈 · 남길 금액을 정하면 계산 가능']
  const content = { budgetAdjustment: { status: 'awaiting_input', guidance: {
    kind: 'explanation', message: lines.join('\n'), details: [] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={vi.fn()} />))
  expect([...host.querySelectorAll('p')].map((row) => row.textContent)).toEqual(lines)
  expect(host.querySelector('details')).toBeNull()
})

it('공통 안내 형식으로 질문과 예외 사유를 표시하고 예전 설명은 중복 표시하지 않는다', async () => {
  const content = { lines: ['옛 안내'], budgetAdjustment: { status: 'awaiting_input',
    explanation: { title: '옛 설명', why: '옛 사유' },
    guidance: { kind: 'question', context: '교통·차량 늘리기', message: '어디에서 가져올까요?',
      reason: '최근 지출을 다 확인하지 못해 자동 추천은 제외했어요.', next: '줄여도 괜찮은 항목을 직접 고를 수 있어요.',
      details: ['계좌·카드 내역을 새로 조회하면 자동 추천도 다시 확인할 수 있어요.'] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={vi.fn()} />))
  expect(host.textContent).not.toContain('옛')
  expect(host.querySelector('[aria-label="이해한 요청"]').textContent).toBe('교통·차량 늘리기')
  expect(host.textContent.split('어디에서 가져올까요?')).toHaveLength(2)
  expect(host.querySelector('[role="note"]').textContent).toContain('자동 추천은 제외')
  expect(host.querySelector('details').open).toBe(false)
  content.budgetAdjustment.guidance.kind = 'explanation'
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={vi.fn()} />))
  expect(host.querySelector('details').open).toBe(true)
})

it('금액 입력에서는 질문을 한 번만 보여주고 오류가 나면 입력창 위에 실제 사유를 보여준다', async () => {
  const content = { budgetAdjustment: { status: 'awaiting_input',
    amountInput: { category: 'TRANSPORT_CAR', direction: 'up' },
    guidance: { kind: 'question', context: '교통·차량 늘리기', message: '교통·차량 예산을 얼마 늘릴까요?',
      reason: '', next: '', details: [] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={vi.fn()} />))
  expect(host.textContent.split('교통·차량 예산을 얼마 늘릴까요?')).toHaveLength(2)
  expect(host.querySelector('details')).toBeNull()
  content.budgetAdjustment.guidance = { kind: 'error', message: '계산하는 중 문제가 생겼어요.',
    reason: '', next: '잠시 후 다시 시도해 주세요.', details: [] }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={vi.fn()} />))
  expect(host.textContent).toContain('계산하는 중 문제가 생겼어요.')
  expect(host.textContent).toContain('잠시 후 다시 시도해 주세요.')
})

it('확정 후 다시 불러온 카드에는 과거 질문이나 미승인 안내를 다시 표시하지 않는다', async () => {
  const content = { budgetAdjustment: { status: 'saved', proposal: { state: 'saved', changes: [] },
    guidance: { kind: 'explanation', message: '아직 저장하지 않았어요.', reason: '잠금을 풀까요?',
      next: '확정해 주세요.', details: ['확정 전에 확인할 내용이에요.'] } } }
  await act(() => root.render(<BudgetAdjustment content={content} onSend={vi.fn()} />))
  expect(host.textContent).toContain('예산에 반영했어요')
  expect(host.textContent).not.toContain('아직 저장')
  expect(host.textContent).not.toContain('잠금을 풀까요')
  expect(host.querySelector('details')).toBeNull()
})

it('선택한 교통·차량을 금액 입력 제목과 전송에 사용하고 반복 안내를 숨긴다', async () => {
  const send = vi.fn()
  const content = { turn: 1, budgetAdjustment: { status: 'awaiting_input',
    amountInput: { category: 'TRANSPORT_CAR', direction: 'up' },
    explanation: { title: '금액 입력 방법', why: '원하는 금액으로 예산을 미리 계산해요.',
      effect: '미리보기만으로 예산이 바뀌지는 않아요.', next: '금액 칸에 직접 입력하거나 버튼으로 바꿔 주세요.' } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  expect(host.querySelector('legend').textContent).toBe('교통·차량 예산을 얼마 늘릴까요?')
  expect(host.querySelector('details')).toBeNull()
  expect(host.textContent).not.toContain('원하는 금액으로 예산을 미리 계산해요.')
  await click(button('+10,000원'))
  await click(button('미리보기'))
  expect(send).toHaveBeenCalledWith('교통·차량을 1만원 늘리고 싶어요')
})

it('본문과 상세 설명의 문장 배치가 달라도 같은 설명은 반복하지 않는다', async () => {
  const reason = '식비는 금액을 고정해 둔 항목이에요.'
  const next = '잠금을 풀지 다른 항목을 고를지 알려 주세요.'
  const effect = '최종 확정 전까지 기존 예산은 그대로예요.'
  const content = { lines: [`${reason}  ${next}`], budgetAdjustment: { status: 'awaiting_input',
    explanation: { brief: reason, why: `${reason}\n${effect}`, effect, next } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={vi.fn()} />))
  expect(host.textContent.split(reason)).toHaveLength(2)
  expect(host.textContent.split(next)).toHaveLength(2)
  expect(host.textContent.split(effect)).toHaveLength(2)
  expect(host.querySelector('dl').textContent).toContain(effect)
})

it('숫자가 다른 설명은 중복으로 숨기지 않는다', async () => {
  const content = { lines: ['옮길 수 있는 돈은 50,000원이에요.'], budgetAdjustment: { status: 'awaiting_input',
    explanation: { why: '남겨 둘 돈은 150,000원이에요.', next: '원하는 금액을 직접 입력해 주세요.' } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={vi.fn()} />))
  expect(host.querySelector('dl').textContent).toContain('150,000원')
})

it('직접 조정에서 여러 감액 항목을 고르고 항목별 한도를 지킨다', async () => {
  const send = vi.fn()
  const content = { turn: 2, budgetAdjustment: { status: 'awaiting_input', distributionInput: {
    target: 'TRANSPORT_CAR', amount: 80000, categories: ['FASHION_SHOPPING', 'EDUCATION'],
    maximums: { FASHION_SHOPPING: 30000, EDUCATION: 50000 },
    before: { TRANSPORT_CAR: 100000, FASHION_SHOPPING: 150000, EDUCATION: 50000 } } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  expect(host.textContent).toContain('어느 항목을 줄일까요?')
  await click(button('패션·쇼핑'))
  await click(button('교육'))
  const inputs = [...host.querySelectorAll('input')]
  await act(() => {
    for (const input of inputs) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '90000')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    }
  })
  // 각각 한 번씩 편집해 React 상태가 반영된 뒤 합계를 검증한다.
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[0], '30000')
    inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[1], '50000')
    inputs[1].dispatchEvent(new Event('input', { bubbles: true }))
  })
  expect(inputs.map((input) => input.value)).toEqual(['30,000', '50,000'])
  expect(host.querySelector('[aria-label="현재 예산과 조정 후 예산"]').textContent).toContain('180,000원')
  expect([...host.querySelectorAll('.mc-budget-distribution-change')].map((row) => row.textContent)).toEqual([
    '현재 150,000원 → 조정 후 120,000원', '현재 50,000원 → 조정 후 0원',
  ])
  await click(button('나눈 예산 미리보기'))
  expect(send).toHaveBeenCalledWith('기존 요청 대신 패션·쇼핑 예산을 30000원, 교육 예산을 50000원 줄여 교통·차량 예산을 80000원 늘려줘.')
})

it('최신 내역이 없으면 실패할 미리보기 버튼 대신 조회 경로를 보여준다', async () => {
  const send = vi.fn()
  const content = { budgetAdjustment: { distributionInput: { target: 'TRANSPORT_CAR', amount: 50000,
    categories: ['EDUCATION'], maximums: { EDUCATION: 50000 }, needsRefresh: true,
    values: [{ category: 'EDUCATION', amount: 50000 }] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  expect(button('나눈 예산 미리보기')).toBeUndefined()
  expect(host.querySelector('a').getAttribute('href')).toBe('/finance/connections')
  await act(() => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(send).not.toHaveBeenCalled()
})

it('조회 완료 후에는 보존된 직접 조정 금액을 바꾸지 않아도 미리보기를 요청한다', async () => {
  const send = vi.fn()
  const content = { budgetAdjustment: { distributionInput: { target: 'TRANSPORT_CAR', amount: 50000,
    categories: ['EDUCATION'], maximums: { EDUCATION: 50000 }, needsRefresh: false,
    before: { TRANSPORT_CAR: 130000, EDUCATION: 50000 }, values: [{ category: 'EDUCATION', amount: 50000 }] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  expect(button('나눈 예산 미리보기').disabled).toBe(false)
  await click(button('나눈 예산 미리보기'))
  expect(send).toHaveBeenCalledWith('기존 요청 대신 교육 예산을 50000원 줄여 교통·차량 예산을 50000원 늘려줘.')
})

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
  expect(host.textContent).toContain('직접 입력')
  expect(document.getElementById(input.getAttribute('aria-describedby'))).not.toBeNull()
  await click(button('나눈 예산 미리보기'))
  expect(send).toHaveBeenCalledWith('기존 배분 대신 교육 예산을 50000원 줄여 식비에 20000원, 교통·차량에 30000원 나눠줘.')
  await click(button('여윳돈'))
  expect(host.querySelectorAll('.mc-budget-distribution-row')).toHaveLength(3)
  expect(button('나눈 예산 미리보기').disabled).toBe(false)
  await click(button('나눈 예산 미리보기'))
  expect(send).toHaveBeenLastCalledWith('기존 배분 대신 교육 예산을 50000원 줄여 식비에 20000원, 교통·차량에 30000원 나눠줘.')
  await click(button('✓ 여윳돈'))
  expect(button('나눈 예산 미리보기').disabled).toBe(false)
  await act(() => root.render(<BudgetAdjustment content={content} active={false} onSend={send} />))
  expect(host.querySelector('fieldset').disabled).toBe(true)
})

it.each([false, true])('선택만 한 빈칸과 0원 항목을 제외하고 합계가 맞으면 진행한다 (직접 감액: %s)', async (funding) => {
  const send = vi.fn()
  const content = { budgetAdjustment: { distributionInput: { amount: 95000,
    categories: ['HOUSEHOLD', 'FASHION_SHOPPING', 'BEAUTY', 'CULTURE_LEISURE', 'ETC'],
    ...(funding ? { target: 'TRANSPORT_CAR', maximums: { HOUSEHOLD: 75000, FASHION_SHOPPING: 20000, BEAUTY: 30000, CULTURE_LEISURE: 50000 } }
      : { source: 'SPARE' }) } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={send} />))
  expect(button('기타')).toBeUndefined()
  for (const name of ['생활용품', '패션·쇼핑', '뷰티·미용', '문화·여가']) await click(button(name))
  const inputs = [...host.querySelectorAll('input')]
  for (const [index, amount] of [[0, '75000'], [1, '20000'], [2, '0']]) {
    await act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inputs[index], amount)
      inputs[index].dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  expect(button('나눈 예산 미리보기').disabled).toBe(false)
  await click(button('나눈 예산 미리보기'))
  expect(send).toHaveBeenCalledWith(funding
    ? '기존 요청 대신 생활용품 예산을 75000원, 패션·쇼핑 예산을 20000원 줄여 교통·차량 예산을 95000원 늘려줘.'
    : '기존 배분 대신 여윳돈 예산을 95000원 줄여 생활용품에 75000원, 패션·쇼핑에 20000원 나눠줘.')
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
      before: { EDUCATION: 50000, FOOD: 400000, TRANSPORT_CAR: 100000, SPARE: 400000 },
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
  expect(host.querySelector('.mc-budget-distribution-change').textContent).toBe('현재 400,000원 → 조정 후 450,000원')
  expect(button('나눈 예산 미리보기').disabled).toBe(true)
  expect(host.querySelector('[aria-label="1번 금액 10,000원 늘리기"]').disabled).toBe(true)
  await click(button('교통·차량'))
  for (let i = 0; i < 2; i++) await click(host.querySelector('[aria-label="1번 금액 10,000원 줄이기"]'))
  expect(host.querySelector('[role="status"]').textContent).toContain('20,000원 남음')
  expect(button('나눈 예산 미리보기').disabled).toBe(true)
  for (let i = 0; i < 2; i++) await click(host.querySelector('[aria-label="2번 금액 10,000원 늘리기"]'))
  expect([...host.querySelectorAll('.mc-budget-distribution-change')].map((row) => row.textContent)).toEqual([
    '현재 400,000원 → 조정 후 430,000원', '현재 100,000원 → 조정 후 120,000원',
  ])
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
  expect(host.textContent).toContain('직접 입력')
  expect(document.getElementById(input.getAttribute('aria-describedby'))).not.toBeNull()
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

it.each([['0', 'financial_0'], ['combined', 'plan_combined']])('복합안 %s 펼침은 저장하지 않고 선택 버튼은 정확한 서버 안 번호를 보낸다', async (id, value) => {
  const pick = vi.fn(), approve = vi.fn()
  const choice = { value, label: '생활비 + 여윳돈' }
  const content = { choices: [choice], budgetAdjustment: { status: 'awaiting_input', canApprove: false,
    requestSummary: '식비 늘리기 · 금액은 함께 정하기 / 유지할 항목: 저축',
    comparison: { basisKind: 'forecast_shortfall', basisAmount: 120000, amountWasGiven: false,
      plans: [{ id, label: '생활비 + 여윳돈', status: 'ready', amount: 120000, maximum: 250000, recommended: true,
        assessment: { label: '추천', reasons: ['저축과 월 쿠션을 유지해요.'], cautions: [], facts: [] },
        result: { changes: [{ category: 'FOOD', before: 400000, after: 520000, diff: 120000, direct: true }], shortfalls: [] } }],
      recommendation: { planId: id } } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={pick} onApprove={approve} />))
  expect(host.textContent).toContain('최근 지출을 참고해 제안하는 금액')
  expect(host.querySelector('[aria-label="이해한 요청"]').textContent).toContain('유지할 항목: 저축')
  expect(host.firstElementChild.firstElementChild.getAttribute('aria-label')).toBe('이해한 요청')
  expect(host.textContent).toContain('옮길 수 있는 최대 금액')
  expect(host.textContent.split('저축과 월 쿠션을 유지해요.')).toHaveLength(2)
  await click(host.querySelector('details summary'))
  expect(pick).not.toHaveBeenCalled()
  expect(approve).not.toHaveBeenCalled()
  await click(button('이 안 선택하기'))
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
  expect(button('이 안 선택하기')).toBeUndefined()
  expect(host.textContent).not.toContain('필요한 항목')
  expect(host.textContent).toContain('의료·건강에 50,000원을 남기도록')
  expect(host.textContent).not.toContain('지금 확인된 정보로 보여 드릴 조정안이 없어요')
})


it('추천 배지는 선택 표시와 다르고 선택은 확정 요청을 보내지 않는다', async () => {
  const pick = vi.fn(), approve = vi.fn()
  const plans = ['living', 'spare'].map((id, index) => ({ id, label: id === 'living' ? '생활비에서 가져오기' : '여윳돈에서 가져오기',
    recommended: index === 0, status: 'ready', amount: 10000,
    result: { changes: [{ category: 'FOOD', before: 100000, after: 110000, direct: true }] } }))
  const content = { choices: plans.map((p) => ({ value: `plan_${p.id}`, label: p.label })),
    budgetAdjustment: { status: 'awaiting_input', comparison: { plans, basisAmount: 10000, amountWasGiven: true } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onPick={pick} onApprove={approve} />))
  const cards = host.querySelectorAll('article')
  expect(cards[0].querySelector('.mc-budget-recommended').textContent).toBe('추천')
  expect(host.querySelector('.is-selected')).toBeNull()
  const select = cards[1].querySelector('button')
  expect(host.querySelector('h2').textContent).toBe('예산 조정 예시')
  expect(select.textContent).toBe('이 안 선택하기')
  expect(document.getElementById(select.getAttribute('aria-describedby')).textContent)
    .toBe('선택해도 바로 적용되지 않아요. 다음 화면에서 확인 후 확정해 주세요.')
  expect(select.classList.contains('mc-budget-select')).toBe(true)
  expect(select.getAttribute('aria-pressed')).toBe('false')
  await click(select)
  expect(cards[1].classList.contains('is-selected')).toBe(true)
  expect(cards[0].classList.contains('is-selected')).toBe(false)
  expect(select.textContent).toBe('✓ 선택한 안')
  expect(select.getAttribute('aria-pressed')).toBe('true')
  expect(pick).toHaveBeenCalledWith(content.choices[1])
  expect(approve).not.toHaveBeenCalled()
})

it('설명 제목은 내용을 밝히고 이미 답한 문장은 반복하지 않는다', async () => {
  const why = '교통비는 이미 쓴 돈 때문에 더 줄일 수 없어요.'
  const content = { lines: [why], budgetAdjustment: { status: 'awaiting_input', explanation: {
    title: '지금 조정할 수 없는 이유', why, brief: why, effect: '기존 예산은 그대로예요.', next: '다른 항목을 골라 주세요.' } } }
  await act(() => root.render(<BudgetAdjustment content={content} active />))
  expect(host.textContent.split(why)).toHaveLength(2)
  expect(host.querySelector('summary').textContent).toBe('지금 조정할 수 없는 이유')
  expect(host.textContent).not.toContain('이유 자세히 보기')
  expect(host.querySelector('dt').textContent).toBe('변경되는 내용')
})

it('계산 방식이 바뀌면 조정 예시 위에 이유를 표시한다', async () => {
  const notice = '지출 기록이 부족해 현재 예산으로 만든 예시를 보여 드려요.'
  const content = { budgetAdjustment: { status: 'awaiting_input', comparison: { notice, basisAmount: 10000, plans: [
    { id: 'spare', label: '여윳돈 활용', status: 'ready', amount: 10000,
      result: { changes: [{ category: 'FOOD', before: 100000, after: 110000 }] } } ] } } }
  await act(() => root.render(<BudgetAdjustment content={content} active />))
  expect(host.querySelector('[role="note"]').textContent).toBe(notice)
  expect(host.querySelector('[role="note"]').closest('details')).toBeNull()
})

it('추천이 없어 금액 입력으로 넘어가도 숨긴 이유를 먼저 알린다', async () => {
  const brief = '지금은 생활비 자동 추천을 제외했어요.'
  const content = { budgetAdjustment: { status: 'awaiting_input',
    amountInput: { category: 'MEDICAL_HEALTH', direction: 'down', maxAmount: 50000 },
    explanation: { title: '추천에서 제외한 방법과 이유', brief, why: '자동 추천에서는 의료·건강 예산을 유지해요.' } } }
  await act(() => root.render(<BudgetAdjustment content={content} active onSend={vi.fn()} />))
  const note = host.querySelector('[role="note"]')
  expect(note.textContent).toBe(brief)
  expect(note.compareDocumentPosition(host.querySelector('form')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(host.textContent.split(brief)).toHaveLength(2)
})
