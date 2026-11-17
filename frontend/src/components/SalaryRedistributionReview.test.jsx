import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SalaryRedistributionReview from './SalaryRedistributionReview.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'

vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), newRequestKey: vi.fn(), useApi: vi.fn() }))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const context = { salaryReview: true, budgetReturn: { conversationId: 'saved-request', returnTo: '/chat?budgetConversation=saved-request' } }
const ready = () => ({ status: 'ready', basisHash: 'basis-1', incomeBefore: 3000000, incomeAfter: 4000000,
  increase: 1000000, requestedAmount: 500000, remainingAmount: 500000, savingPercent: 30, scope: 'cycle',
  requestChanges: [{ category: 'EDUCATION', before: 50000, after: 550000, diff: 500000 }] })
const proposal = () => ({ proposalId: 'salary-review-1', state: 'awaiting_approval', mode: 'NORMAL_ADJUSTMENT',
  canApprove: true, changes: [
    { category: 'EDUCATION', before: 50000, after: 550000, diff: 500000 },
    { category: 'SAVINGS', before: 600000, after: 750000, diff: 150000 },
    { category: 'CUSHION_MONTHLY', before: 200000, after: 250000, diff: 50000 },
  ], shortfalls: [], persistenceItems: [] })
let host, root, data, reload
function Location() { return <output data-testid="location">{useLocation().pathname}</output> }
const button = (text) => [...host.querySelectorAll('button')].find((item) => item.textContent === text)
const click = async (element) => act(() => element.click())
const input = () => host.querySelector('#salary-review-saving')
const enter = async (value) => act(() => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input(), value)
  input().dispatchEvent(new Event('input', { bubbles: true }))
})
const submit = async () => act(() => input().form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
async function render(reviewContext = context) {
  await act(() => root.render(<MemoryRouter><PopupProvider><Location />
    <SalaryRedistributionReview context={reviewContext} fallback={<p>기존 지킬 돈 설정</p>} />
  </PopupProvider></MemoryRouter>))
}
beforeEach(() => {
  vi.resetAllMocks()
  data = ready()
  reload = vi.fn()
  useApi.mockImplementation(() => ({ data, reload }))
  newRequestKey.mockReturnValue('review-request-key')
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove() })

it('기존 요청을 먼저 표시하고 저축 비율 확인 전에는 계산과 승인을 하지 않는다', async () => {
  await render()
  expect(useApi).toHaveBeenCalledWith('/api/budgets/salary-review/?conversationId=saved-request')
  expect(host.textContent).toContain('기존 요청에 먼저 배정500,000원')
  expect(host.textContent).toContain('추가로 나눌 금액500,000원')
  expect(host.textContent).toContain('교육50,000원 → 550,000원')
  expect(input().value).toBe('30')
  expect(apiSend).not.toHaveBeenCalled()
  expect(button('예산 확정하기')).toBeUndefined()
})

it('새 비율로 미리보기만 만들고 최종 승인 팝업에서 확정한다', async () => {
  apiSend.mockResolvedValueOnce({ ...ready(), proposal: { ...proposal(),
    salaryReview: { incomeBefore: 3000000, incomeAfter: 4000000, increase: 1000000 },
    transfers: [{ from: 'SPARE', to: 'EDUCATION', amount: 500000 }],
    persistenceItems: [{ category: 'SAVINGS', amount: 750000 }],
  } })
  await render()
  await enter('40')
  await submit()
  expect(apiSend).toHaveBeenCalledWith('/api/budgets/salary-review/', 'POST', {
    conversationId: 'saved-request', basisHash: 'basis-1', savingPercent: 40,
  }, { requestKey: 'review-request-key' })
  expect(host.textContent).toContain('600,000원 → 750,000원')
  await click(button('예산 확정하기'))
  const confirmation = host.querySelector('[role="dialog"]')
  expect(confirmation.textContent).toContain('월급 증가분 → 교육 500,000원')
  expect(confirmation.textContent).not.toContain('여윳돈 → 교육')
  expect(confirmation.textContent).toContain('선택하면 아래 생활비·저축·월 쿠션 금액을 다음 예산의 설정에도 반영해요.')
  expect(apiSend).toHaveBeenCalledTimes(1)
  apiSend.mockResolvedValueOnce({ adjustment: { ...proposal(), state: 'saved', savedRef: 'salary-review-1' } })
  await click(button('예산 확정'))
  expect(apiSend).toHaveBeenLastCalledWith('/api/budgets/proposals/salary-review-1/approve/', 'POST',
    { keepForNextCycle: false }, { requestKey: 'review-request-key' })
  expect(host.textContent).toContain('새 예산을 반영했어요')
  await click(button('예산 조정 대화로 돌아가기'))
  expect(host.querySelector('[data-testid="location"]').textContent).toBe('/chat')
})

it('마이페이지에서 시작해도 같은 계산을 사용하며 대화 ID를 만들지 않는다', async () => {
  apiSend.mockResolvedValueOnce({ ...ready(), proposal: proposal() })
  await render({ salaryReview: true, budgetReturn: null })
  expect(useApi).toHaveBeenCalledWith('/api/budgets/salary-review/')
  await submit()
  expect(apiSend.mock.calls[0][2]).toEqual({ basisHash: 'basis-1', savingPercent: 30 })
  expect(button('예산 검토 나중에 하기')).toBeDefined()
})

it.each([context, { salaryReview: true, budgetReturn: null }])('추가 배분 근거가 부족해도 검증된 기존 요청과 저축 미리보기를 유지한다', async (reviewContext) => {
  const message = '추가 배분 근거를 확인하지 못해 남은 350,000원은 여윳돈으로 남겨 뒀어요.'
  apiSend.mockResolvedValueOnce({ ...ready(), proposal: proposal(), summary: {
    allocationReview: { status: 'unavailable', reason: 'ml_unavailable', message,
      allocatedAmount: 0, unallocatedAmount: 350000 },
  } })
  await render(reviewContext)
  await submit()
  expect(host.textContent).toContain('교육50,000원 → 550,000원')
  expect(host.textContent).toContain('600,000원 → 750,000원')
  expect(button('예산 확정하기')).toBeDefined()
  await enter('40')
  expect(button('예산 확정하기')).toBeUndefined()
})

it('비율을 바꾸면 이전 미리보기의 승인 버튼을 숨기고 재계산을 요구한다', async () => {
  apiSend.mockResolvedValueOnce({ ...ready(), proposal: proposal() })
  await render(); await submit()
  expect(button('예산 확정하기')).toBeDefined()
  await enter('35')
  expect(button('예산 확정하기')).toBeUndefined()
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('잘못된 비율을 전송하지 않고 계산 실패 시 입력과 기존 요청을 유지한다', async () => {
  apiSend.mockRejectedValueOnce(new Error('현재 요청을 먼저 반영하면 배분할 금액이 부족해요.'))
  await render()
  await enter('101'); await submit()
  expect(apiSend).not.toHaveBeenCalled()
  await enter('30.5'); await submit()
  expect(apiSend).not.toHaveBeenCalled()
  await enter('25'); await submit()
  expect(input().value).toBe('25')
  expect(host.textContent).toContain('현재 요청을 먼저 반영하면 배분할 금액이 부족해요.')
  expect(host.textContent).toContain('기존 요청에 먼저 배정500,000원')
})

it('다음 주기 요청은 예약 날짜와 별도의 예약 확정 단계를 유지한다', async () => {
  data = { ...ready(), scope: 'next_cycle', effectiveOn: '2026-12-05' }
  apiSend.mockResolvedValueOnce({ ...data, proposal: { ...proposal(), effectiveOn: data.effectiveOn } })
  await render(); await submit()
  expect(host.textContent).toContain('2026-12-05부터의 예산을 검토해요. 이번 예산은 그대로예요.')
  await click(button('다음 주기 변경 예약하기'))
  expect(button('예약 확정')).toBeDefined()
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it.each(['no_increase', 'no_budget'])('%s일 때 기존 검토 화면으로 연결한다', async (status) => {
  data = { status }
  await render()
  expect(host.textContent).toContain('기존 지킬 돈 설정')
  expect(apiSend).not.toHaveBeenCalled()
})

it('요청 정보가 부족하면 원인과 복귀 경로를 보여 주고 임의로 금액을 만들지 않는다', async () => {
  data = { status: 'needs_input', incomeBefore: 3000000, incomeAfter: 4000000, increase: 1000000,
    message: '교육비를 얼마나 늘릴지 알려 주세요.' }
  await render()
  expect(host.textContent).toContain(data.message)
  expect(host.textContent).not.toContain('NaN')
  expect(host.textContent).toContain('요청 확인 후 계산')
  expect(input()).toBeNull()
  expect(button('이전 예산 조정 이어하기')).toBeDefined()
})

it.each(['awaiting_approval', 'saved'])('새로고침 시 서버의 %s 검토안을 복원하고 다시 계산하지 않는다', async (state) => {
  data = { ...ready(), proposal: { ...proposal(), state, ...(state === 'saved' ? { savedRef: 'salary-review-1' } : {}) } }
  await render()
  expect(host.textContent).toContain('600,000원 → 750,000원')
  expect(apiSend).not.toHaveBeenCalled()
  if (state === 'saved') {
    expect(host.textContent).toContain('새 예산을 반영했어요')
    expect(button('예산 확정하기')).toBeUndefined()
    expect(input()).toBeNull()
  } else expect(button('예산 확정하기')).toBeDefined()
})

it('복원한 선택 비율과 기존 승인 예산의 저축 비율을 구분한다', async () => {
  data = { ...ready(), previousSavingPercent: 27, savingPercent: 45, proposal: proposal() }
  await render()
  expect(input().value).toBe('45')
  expect(host.querySelector('#salary-review-saving-help').textContent).toContain('저축 비율은 27%')
  expect(host.querySelector('#salary-review-saving-help').textContent).not.toContain('45%')
})

it('돌아가기 버튼은 지금 단계에서 무엇이 이어지는지 글자로 알려 준다', async () => {
  await render()
  const back = button('저축·쿠션은 그대로 두고 요청만 이어가기')
  expect(document.getElementById(back.getAttribute('aria-describedby')).textContent).toContain('원래 요청한 생활비 조정만 이어가요')
  apiSend.mockResolvedValueOnce({ ...ready(), proposal: proposal() })
  await submit()
  expect(button('이 배분안으로 대화 이어가기')).toBeDefined()
  expect(button('저축·쿠션은 그대로 두고 요청만 이어가기')).toBeUndefined()
})
