import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AssistantPage from './AssistantPage.jsx'
import BudgetPage from './BudgetPage.jsx'
import RecommendPage from './RecommendPage.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'
import { usePopup } from '../popups/context.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'

vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), newRequestKey: vi.fn(), useApi: vi.fn() }))
vi.mock('../components/Layout.jsx', () => ({ default: function MockLayout({ children, foot }) {
  const { popup } = usePopup()
  return <main><section inert={Boolean(popup)}>{children}{foot}</section><aside>{popup}</aside></main>
} }))
vi.mock('../components/Icon.jsx', () => ({ default: () => <span /> }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root, keyNumber
const info = { engine: 'router', features: [{ value: 'budget', label: '예산 조정' }], testMode: false }
const change = (category, before, after, direct = false) => ({ category, before, after, diff: after - before, direct })
const card = (extra = {}) => ({
  proposalId: 'proposal-1', taskRef: 'task-1', mode: 'NORMAL_ADJUSTMENT', state: 'awaiting_approval', canApprove: true,
  changes: [change('FOOD', 400000, 415000, true), change('SPARE', 400000, 385000)],
  shortfalls: [], persistenceItems: [{ category: 'FOOD', amount: 415000 }], targetItems: [], unlockCategories: [], keepForNextCycle: false,
  ...extra,
})
const talk = (adjustment, choices = [], turn = 7) => ({ ...info, conversationId: 'conversation-1', messages: [
  { messageId: `user-${turn}`, role: 'user', content: { text: '식비를 조정하고 싶어' } },
  { messageId: `bot-${turn}`, role: 'bot', content: { agent: 'budget', turn, lines: ['변경안을 확인해 주세요.'], choices, budgetAdjustment: adjustment } },
] })
const proposalTalk = (proposal = card(), turn = 7) => talk({ status: 'replied', canApprove: true, proposal }, [
  { value: 'apply', label: '이 안으로 적용' }, { value: 'cancel', label: '취소' },
], turn)
const saved = (proposal) => ({ adjustment: { ...proposal, state: 'saved', canApprove: false, savedRef: proposal.proposalId }, budget: {} })
const journal = () => ({ conversationId: 'conversation-1', proposalId: 'proposal-1',
  path: '/api/budgets/proposals/proposal-1/approve/', key: 'restored-request-key', body: { keepForNextCycle: true } })
const comparison = () => ({
  basisAmount: 200000, amountWasGiven: true, recommendation: { planId: 'spare', reasons: ['중요한 예산을 유지해요.'] },
  plans: [
    { id: 'living', label: '생활비 내 재배분', status: 'partial', amount: 175000, recommended: false,
      assessment: { label: '비추천', reasons: ['요청액을 모두 채울 수 없어요.'], cautions: ['2순위 교통·차량이 75,000원으로 줄어요.'], facts: [{ category: 'ETC', remaining: 0 }] },
      result: { changes: [change('FOOD', 400000, 575000, true), change('TRANSPORT_CAR', 100000, 75000)],
        shortfalls: [{ category: 'FOOD', requestedAmount: 200000, appliedAmount: 175000, missingAmount: 25000 }] } },
    { id: 'spare', label: '여윳돈 활용', status: 'ready', amount: 200000, recommended: true,
      assessment: { label: '추천', reasons: ['저축과 우선순위 예산을 유지해요.'], cautions: [], facts: [] },
      result: { changes: [change('FOOD', 400000, 600000, true), change('SPARE', 400000, 200000)], shortfalls: [] } },
    { id: 'extended', label: '저축·월 쿠션 배분 조정', status: 'ready', amount: 200000, recommended: false,
      assessment: { label: '비추천', reasons: ['저축 목표 미달이 늘어요.'], cautions: [], facts: [] },
      result: { changes: [change('FOOD', 400000, 600000, true), change('SAVINGS', 600000, 480000), change('CUSHION_MONTHLY', 200000, 120000)], shortfalls: [] } },
  ],
})

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  keyNumber = 0
  newRequestKey.mockImplementation(() => `request-key-${++keyNumber}`)
  useApi.mockReturnValue({ data: info, error: null })
  Element.prototype.scrollIntoView = vi.fn()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.restoreAllMocks() })
const mount = async (entry = '/chat') => act(() => root.render(
  <MemoryRouter initialEntries={[entry]}><PopupProvider><AssistantPage /></PopupProvider></MemoryRouter>,
))
const button = (label, within = host) => [...within.querySelectorAll('button')].find((item) => item.textContent.trim() === label)
const click = async (element) => act(() => element.click())
const input = () => host.querySelector('input[aria-label="챗봇에게 보낼 메시지"]')
async function submit(text) {
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input(), text)
    input().dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(() => input().form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}

it('분배 실패 해결안의 카테고리 배정액을 확인한 뒤 최초 예산으로 승인한다', async () => {
  const proposal = card({ mode: 'INITIAL_ADJUSTMENT', baseBudgetId: null, sourceTaskId: 'failed-design',
    allocations: { FOOD: 1115000, EDUCATION: 12345 }, changes: [change('LIVING', 1120000, 1217345, true)],
    persistenceItems: [{ category: 'LIVING', amount: 1217345 }] })
  apiSend.mockResolvedValueOnce(talk({ status: 'awaiting_input', phase: 'design_recovery', canApprove: false },
    [{ value: 'recovery_cycle', label: '이번 주기만 필요해요' }])).mockResolvedValueOnce(proposalTalk(proposal, 8))
    .mockResolvedValueOnce(saved(proposal))
  await mount({ pathname: '/chat', state: { budgetContext: { mode: 'DESIGN_RECOVERY', sourceTaskId: 'failed-design' } } })
  await click(button('이번 주기만 필요해요'))
  await click(button('예산 확정하기'))
  const popup = host.querySelector('aside')
  expect(popup.textContent).toContain('12,345원')
  expect(popup.textContent).toContain('카테고리별 배정액')
  await click(button('예산 확정', popup))
  expect(apiSend.mock.calls[2].slice(0, 3)).toEqual(['/api/budgets/proposal-1/approve/', 'POST', { keepForNextCycle: false }])
  expect(host.textContent).toContain('예산에 반영했어요')
})

it('비교안 여러 개를 펼쳐도 요청하지 않고 선택할 때만 최신 turn을 전송한다', async () => {
  apiSend.mockResolvedValueOnce(talk({ status: 'awaiting_input', comparison: comparison(), canApprove: false }, [
    { value: 'plan_living', label: '생활비 내 재배분' }, { value: 'plan_spare', label: '여윳돈 활용' },
    { value: 'plan_extended', label: '저축·월 쿠션 배분 조정' },
  ])).mockResolvedValueOnce(proposalTalk(card(), 8))
  await mount()
  await submit('비교하고 싶어')
  const panels = [...host.querySelectorAll('.mc-budget-plan')]
  expect(panels).toHaveLength(3)
  expect(host.textContent).toContain('부족 25,000원')
  expect(host.textContent).toContain('기타 남은 예산 0원')
  await click(panels[0].querySelector('summary'))
  await click(panels[1].querySelector('summary'))
  expect(panels[0].querySelector('details').open).toBe(true)
  expect(panels[1].querySelector('details').open).toBe(true)
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(button('여윳돈 활용')).toBeUndefined()
  await click(button('이 안 선택', panels[1]))
  expect(apiSend.mock.calls[1][2]).toEqual({ conversationId: 'conversation-1', choice: { value: 'plan_spare', turn: 7 } })
  expect(button('이 안 선택', panels[0]).disabled).toBe(true)
})

it('조정안의 자동 감액 항목도 상세를 펼치기 전에 보여 준다', async () => {
  apiSend.mockResolvedValueOnce(proposalTalk(card({ changes: [
    change('FOOD', 400000, 425000, true), change('TRANSPORT_CAR', 100000, 75000),
  ] })))
  await mount()
  await submit('식비 5만 원 늘려줘')
  const proposal = host.querySelector('.mc-budget-proposal')
  const visible = proposal.querySelector(':scope > .mc-budget-changes')
  expect(visible.textContent).toContain('교통·차량')
  expect(visible.textContent).toContain('75,000원')
  expect(proposal.querySelector('details').open).toBe(false)
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('대화 답변의 작은 보기 옵션은 과거 답변에서도 현재 조건과 최신 turn으로 요청한다', async () => {
  const navigation = [{ value: 'recommend', label: '추천안 보기' }, { value: 'compare', label: '비교하기' }]
  apiSend.mockResolvedValueOnce(talk({ status: 'awaiting_input', phase: 'funding_scope', canApprove: false }, [
    { value: 'living', label: '생활비 내에서' }, ...navigation,
  ], 7)).mockResolvedValueOnce(talk({ status: 'awaiting_input', phase: 'discussion', canApprove: false }, navigation, 8))
    .mockResolvedValueOnce({ ...proposalTalk(card(), 9), messages: proposalTalk(card(), 9).messages.map((m) =>
      m.role === 'bot' ? { ...m, content: { ...m.content, choices: navigation } } : m) })
  await mount()
  await submit('식비가 부족해')
  expect(button('생활비 내에서')).toBeUndefined()
  expect(host.querySelectorAll('.mc-budget-navigation')).toHaveLength(1)
  expect(host.querySelector('.mc-budget-comparison')).toBeNull()
  await submit('저축은 그대로 두고 싶어')
  const options = [...host.querySelectorAll('.mc-budget-navigation')]
  expect(options).toHaveLength(2)
  await click(button('추천안 보기', options[0]))
  expect(apiSend.mock.calls[2][2].choice).toEqual({ value: 'recommend', turn: 8 })
  expect(host.querySelector('.mc-budget-proposal')).not.toBeNull()
  expect(host.querySelector('.mc-budget-comparison')).toBeNull()
  expect(apiSend.mock.calls.every(([path]) => path === '/api/chat/messages/')).toBe(true)
})

it('부분 반영안과 다른 재원은 비교만 하며 선택 전에는 확정할 수 없다', async () => {
  apiSend.mockResolvedValue(talk({ status: 'awaiting_input', canApprove: false, phase: 'partial',
    proposal: card({ canApprove: false, shortfalls: comparison().plans[0].result.shortfalls }), alternatives: comparison() }, [
    { value: 'partial_accept', label: '가능한 금액만 반영' }, { value: 'partial_compare', label: '다른 재원 비교' },
  ]))
  await mount()
  await submit('생활비에서만 조정할래')
  expect(host.textContent).toContain('여윳돈')
  expect(host.textContent).toContain('200,000원')
  expect(button('이 안 선택')).toBeUndefined()
  expect(button('예산 확정하기')).toBeUndefined()
  await click(button('가능한 금액만 반영'))
  expect(apiSend.mock.calls[1][2].choice).toEqual({ value: 'partial_accept', turn: 7 })
})

it('자세히 요청한 답변은 변경안·비교안·설명 상세를 펼친 채 보여 준다', async () => {
  apiSend.mockResolvedValue(talk({ status: 'awaiting_input', canApprove: false, detail: true,
    proposal: card({ canApprove: false }), alternatives: comparison(),
    explanation: { why: '하한과 금액 단위를 함께 확인했어요.', effect: '예산은 아직 그대로예요.', next: '가능한 금액을 선택해 주세요.' } }))
  await mount()
  await submit('자세히')
  const details = [...host.querySelectorAll('.mc-budget-details')]
  expect(details.length).toBe(5)
  expect(details.every((element) => element.open)).toBe(true)
  expect(host.textContent).toContain('하한과 금액 단위를 함께 확인했어요.')
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it.each([
  ['INITIAL_ADJUSTMENT', true, '/api/budgets/proposal-1/approve/'],
  ['NORMAL_ADJUSTMENT', false, '/api/budgets/proposals/proposal-1/approve/'],
])('%s 확인 팝업에서 유지 기본값을 바꿔 정확한 승인 경로로 보낸다', async (mode, initialKeep, path) => {
  const proposal = card({ mode, keepForNextCycle: initialKeep,
    targetItems: [{ category: 'FASHION_SHOPPING', amount: null }], unlockCategories: ['FOOD'] })
  apiSend.mockResolvedValueOnce(proposalTalk(proposal)).mockResolvedValueOnce(saved(proposal))
  await mount()
  await submit('식비를 조정해줘')
  await click(button('예산 확정하기'))
  const dialog = host.querySelector('[role=dialog]')
  expect(dialog.textContent).toContain('385,000원')
  expect(dialog.textContent).toContain('식비 잠금 해제')
  expect(dialog.textContent).toContain('패션·쇼핑 유지 목표 해제')
  const checkbox = dialog.querySelector('input[type=checkbox]')
  expect(checkbox.checked).toBe(initialKeep)
  await click(checkbox)
  await click(button('예산 확정', dialog))
  expect(apiSend.mock.calls[1]).toEqual([path, 'POST', { keepForNextCycle: !initialKeep }, { requestKey: 'request-key-2' }])
  expect(host.querySelector('[role=dialog]')).toBeNull()
  expect(host.textContent).toContain('예산에 반영했어요')
  expect(button('예산 확정하기')).toBeUndefined()
})

it('승인 응답이 유실되면 팝업을 닫아도 본문·키를 보존하고 다른 입력을 막는다', async () => {
  const proposal = card()
  apiSend.mockResolvedValueOnce(proposalTalk(proposal)).mockRejectedValueOnce(new TypeError('연결 끊김'))
    .mockResolvedValueOnce(saved(proposal))
  await mount()
  await submit('식비 조정')
  await click(button('예산 확정하기'))
  await click(host.querySelector('input[type=checkbox]'))
  await click(button('예산 확정'))
  expect(host.querySelector('input[type=checkbox]').disabled).toBe(true)
  expect(input().disabled).toBe(true)
  expect(JSON.parse(sessionStorage.getItem('mc-budget-approval-pending'))).toEqual({
    conversationId: 'conversation-1', proposalId: 'proposal-1', path: '/api/budgets/proposals/proposal-1/approve/',
    key: 'request-key-2', body: { keepForNextCycle: true },
  })
  await click(button('닫기'))
  expect(input().disabled).toBe(true)
  expect(button('예산을 조정하고 싶어요').disabled).toBe(true)
  await click(button('확정 결과 다시 확인'))
  expect(host.querySelector('input[type=checkbox]').checked).toBe(true)
  await click(button('같은 요청으로 다시 확인'))
  expect(apiSend.mock.calls[2]).toEqual(apiSend.mock.calls[1])
  expect(newRequestKey).toHaveBeenCalledTimes(2)
  expect(input().disabled).toBe(false)
  expect(sessionStorage.getItem('mc-budget-approval-pending')).toBeNull()
})

it('승인 처리 중 새로고침해도 서버의 대기 카드를 확인한 뒤 원래 키와 체크 값으로만 재시도한다', async () => {
  const proposal = card()
  let finishOriginal
  apiSend.mockResolvedValueOnce(proposalTalk(proposal))
    .mockImplementationOnce(() => new Promise((resolve) => { finishOriginal = resolve }))
  await mount()
  await submit('식비 조정')
  await click(button('예산 확정하기'))
  await click(host.querySelector('input[type=checkbox]'))
  await click(button('예산 확정'))
  const originalRequest = apiSend.mock.calls[1]
  expect(input().disabled).toBe(true)
  await act(() => root.render(null))
  apiSend.mockResolvedValueOnce(proposalTalk(card({ keepForNextCycle: false }))).mockResolvedValueOnce(saved(proposal))
  await mount()
  expect(apiSend.mock.calls[2]).toEqual(['/api/chat/conversations/conversation-1/', 'GET'])
  expect(input().disabled).toBe(true)
  await click(button('확정 결과 다시 확인'))
  const checkbox = host.querySelector('input[type=checkbox]')
  expect(checkbox.checked).toBe(true)
  expect(checkbox.disabled).toBe(true)
  await click(button('같은 요청으로 다시 확인'))
  expect(apiSend.mock.calls[3]).toEqual(originalRequest)
  expect(newRequestKey).toHaveBeenCalledTimes(2)
  expect(sessionStorage.getItem('mc-budget-approval-pending')).toBeNull()
  expect(input().disabled).toBe(false)
  await act(() => finishOriginal(saved(proposal)))
})

it.each(['saved', 'expired'])('새로고침 조회로 %s가 확인되면 승인 기록과 입력 차단을 해제한다', async (state) => {
  sessionStorage.setItem('mc-budget-approval-pending', JSON.stringify(journal()))
  apiSend.mockResolvedValue(proposalTalk(card({ state, ...(state === 'saved' ? { savedRef: 'proposal-1' } : {}) })))
  await mount()
  expect(sessionStorage.getItem('mc-budget-approval-pending')).toBeNull()
  expect(input().disabled).toBe(false)
  expect(button('확정 결과 다시 확인')).toBeUndefined()
  expect(apiSend.mock.calls[0]).toEqual(['/api/chat/conversations/conversation-1/', 'GET'])
  expect(apiSend.mock.calls.every(([, method]) => method === 'GET')).toBe(true)
})

it('승인 기록이 가리키는 대화가 404면 기록을 지우고 새 대화로 시작한다', async () => {
  sessionStorage.setItem('mc-budget-approval-pending', JSON.stringify(journal()))
  apiSend.mockRejectedValueOnce(Object.assign(new Error('없는 대화'), { status: 404 })).mockResolvedValueOnce(proposalTalk())
  await mount()
  expect(sessionStorage.getItem('mc-budget-approval-pending')).toBeNull()
  expect(input().disabled).toBe(false)
  await submit('새로 조정할래')
  expect(apiSend.mock.calls[1][2].conversationId).toBeNull()
})

it('승인 상태 조회 실패는 기록과 입력 차단을 유지하고 같은 대화를 다시 조회한다', async () => {
  sessionStorage.setItem('mc-budget-approval-pending', JSON.stringify(journal()))
  apiSend.mockRejectedValueOnce(new TypeError('조회 연결 끊김')).mockResolvedValueOnce(proposalTalk())
  await mount()
  expect(input().disabled).toBe(true)
  expect(sessionStorage.getItem('mc-budget-approval-pending')).not.toBeNull()
  await click(button('확정 상태 다시 불러오기'))
  expect(apiSend.mock.calls[1]).toEqual(apiSend.mock.calls[0])
  expect(input().disabled).toBe(true)
  expect(button('확정 결과 다시 확인')).toBeTruthy()
})

it.each([
  '{broken',
  JSON.stringify({ ...journal(), path: 'https://example.invalid/approve/' }),
  JSON.stringify({ ...journal(), conversationId: '../../other-endpoint' }),
  JSON.stringify({ ...journal(), body: { keepForNextCycle: 'true' } }),
  JSON.stringify({ ...journal(), key: '' }),
  JSON.stringify({ ...journal(), proposal: { allocations: { FOOD: 999999 } } }),
  JSON.stringify({ ...journal(), path: '/api/budgets/proposal-1/approve/' }),
])('변조되거나 실제 카드 모드와 다른 승인 기록은 전송하지 않는다: %s', async (record) => {
  sessionStorage.setItem('mc-chat-conversation', 'conversation-1')
  sessionStorage.setItem('mc-budget-approval-pending', record)
  apiSend.mockResolvedValue(proposalTalk())
  await mount()
  expect(sessionStorage.getItem('mc-budget-approval-pending')).toBeNull()
  expect(input().disabled).toBe(false)
  expect(apiSend.mock.calls).toEqual([['/api/chat/conversations/conversation-1/', 'GET']])
})

it('확정 기록을 저장할 수 없으면 승인 API를 보내기 전에 중단한다', async () => {
  apiSend.mockResolvedValue(proposalTalk())
  await mount()
  await submit('식비 조정')
  await click(button('예산 확정하기'))
  const original = Storage.prototype.setItem
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (key, value) {
    if (key === 'mc-budget-approval-pending') throw new Error('storage unavailable')
    return original.call(this, key, value)
  })
  await click(button('예산 확정'))
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('확정 정보를 보관하지 못했어요.')
  expect(host.querySelector('input[type=checkbox]').disabled).toBe(false)
})

it('409 expired는 카드를 만료시키고 재승인 대신 새 조정을 안내한다', async () => {
  apiSend.mockResolvedValueOnce(proposalTalk()).mockRejectedValueOnce(Object.assign(new Error('조건 변경'), { status: 409, code: 'expired' }))
  await mount()
  await submit('식비 조정')
  await click(button('예산 확정하기'))
  await click(button('예산 확정'))
  expect(button('예산 확정')).toBeUndefined()
  await click(button('닫고 다시 조정하기'))
  expect(host.textContent).toContain('만료된 변경안')
  expect(button('예산 확정하기')).toBeUndefined()
  expect(input().disabled).toBe(false)
  expect(apiSend).toHaveBeenCalledTimes(2)
})

it('유지 설정의 명시적 400 거절은 체크를 바꾸고 새 키로 다시 확정할 수 있다', async () => {
  const proposal = card({ keepForNextCycle: true })
  apiSend.mockResolvedValueOnce(proposalTalk(proposal))
    .mockRejectedValueOnce(Object.assign(new Error('다음 달 목표 합계를 확인해 주세요.'), { status: 400, code: 'invalid' }))
    .mockResolvedValueOnce(saved(proposal))
  await mount()
  await submit('식비 조정')
  await click(button('예산 확정하기'))
  await click(button('예산 확정'))
  const checkbox = host.querySelector('input[type=checkbox]')
  expect(checkbox.disabled).toBe(false)
  expect(host.textContent).toContain('다음 달 목표 합계를 확인해 주세요.')
  await click(checkbox)
  await click(button('예산 확정'))
  expect(apiSend.mock.calls[1][2]).toEqual({ keepForNextCycle: true })
  expect(apiSend.mock.calls[2][2]).toEqual({ keepForNextCycle: false })
  expect(apiSend.mock.calls[2][3].requestKey).not.toBe(apiSend.mock.calls[1][3].requestKey)
  expect(host.textContent).toContain('예산에 반영했어요')
})

it('새로고침으로 복원한 저장 완료 카드는 캐시 선택지가 있어도 다시 승인하지 않는다', async () => {
  sessionStorage.setItem('mc-chat-conversation', 'conversation-1')
  apiSend.mockResolvedValue(proposalTalk({ ...card(), state: 'saved', savedRef: 'proposal-1' }))
  await mount()
  expect(host.textContent).toContain('예산에 반영했어요')
  expect(button('예산 확정하기')).toBeUndefined()
  expect(button('이 안으로 적용')).toBeUndefined()
  expect(button('취소')).toBeUndefined()
})

it('기능 목록을 못 읽으면 예산 기준을 임의 전송하지 않고 다시 시작하도록 안내한다', async () => {
  useApi.mockReturnValue({ data: null, error: new Error('목록 응답 유실') })
  apiSend.mockResolvedValue(talk({ status: 'awaiting_input', canApprove: false }))
  await mount({ pathname: '/chat', state: { budgetContext: { baseBudgetId: 'basis-1', mode: 'INITIAL_ADJUSTMENT' } } })
  expect(apiSend).not.toHaveBeenCalled()
  expect(host.textContent).toContain('예산 조정 서비스를 아직 사용할 수 없어요.')
  expect(input().disabled).toBe(false)
})

it('legacy에서는 지원하지 않는 예산 기준 메시지를 보내지 않는다', async () => {
  useApi.mockReturnValue({ data: { ...info, engine: 'legacy' }, error: null })
  await mount({ pathname: '/chat', state: { budgetContext: { baseBudgetId: 'basis-1', mode: 'INITIAL_ADJUSTMENT' } } })
  expect(apiSend).not.toHaveBeenCalled()
  expect(host.textContent).toContain('예산 조정 서비스를 아직 사용할 수 없어요.')
  expect(input().disabled).toBe(false)
})

const budgetData = (status) => ({
  budget: { budgetId: 'basis-1', status, versionNo: 1, salaryConfirmed: true },
  cycle: { startsOn: '2026-10-25', endsBefore: '2026-11-25' }, split: { funding: 1000000, fixed: 0, saving: 100000, cushion: 100000, living: 600000, spare: 200000 },
  living: { budget: 600000, spent: 0 }, overspend: { over: 0 }, categories: [], unclassifiedCount: 0,
})

const sourceTaskId = 'test-design-task'
const recoveryResult = { outcome: 'infeasible', budgetId: null, summary: '생활비를 다시 검토해 주세요.',
  handoff: { mode: 'DESIGN_RECOVERY', sourceTaskId } }
const recoveryTalk = (status = 'awaiting_input', proposal) => talk({ status, phase: 'design_recovery', sourceTaskId,
  canApprove: Boolean(proposal), proposal }, status === 'awaiting_input' ? [{ value: 'recovery_cycle', label: '이번 주기만 필요해요' }] : [],
  status === 'awaiting_input' ? 7 : 8)

function mockRecovery(chat, task = { status: 'succeeded', result: recoveryResult }) {
  useApi.mockImplementation((url) => ({ data: url === '/api/budgets/' ? budgetData('approved') : info, error: null }))
  apiSend.mockImplementation(async (url, method, body) => {
    if (url === '/api/budgets/') return { ...budgetData('approved'), budget: null }
    if (url === '/api/budgets/recommend/') return { task: { taskId: sourceTaskId } }
    if (url === `/api/tasks/${sourceTaskId}/`) return task
    if (url === '/api/chat/messages/' && method === 'POST') return chat(body)
    if (url === '/api/chat/conversations/conversation-1/') return recoveryTalk()
    throw new Error(`unexpected endpoint: ${url}`)
  })
}

async function mountRecovery(entry = '/budgets/recommend') {
  let location
  function LocationProbe() { location = useLocation(); return null }
  await act(() => root.render(<MemoryRouter initialEntries={[entry]}><PopupProvider>
    <LocationProbe /><Routes>
      <Route path="/budgets/recommend" element={<RecommendPage />} /><Route path="/chat" element={<AssistantPage />} />
      <Route path="/budgets" element={<BudgetPage />} />
    </Routes>
  </PopupProvider></MemoryRouter>))
  return () => location
}

const sentChat = () => apiSend.mock.calls.filter(([url, method]) => url === '/api/chat/messages/' && method === 'POST')

it('추천 실패에서 자동 연결하고 승인 후 반영된 예산을 볼 수 있다', async () => {
  const proposal = card({ mode: 'INITIAL_ADJUSTMENT', baseBudgetId: null, sourceTaskId, allocations: { EDUCATION: 12345 } })
  mockRecovery((body) => body.budgetContext ? recoveryTalk() : recoveryTalk('awaiting_approval', proposal))
  const send = apiSend.getMockImplementation()
  let approved = false
  useApi.mockImplementation((url) => ({ data: url === '/api/budgets/' ? {
    ...budgetData('approved'), budget: approved ? { ...budgetData('approved').budget, versionNo: 2 } : null,
  } : info, error: null }))
  apiSend.mockImplementation(async (...args) => {
    if (args[0] !== '/api/budgets/proposal-1/approve/') return send(...args)
    approved = true
    return saved(proposal)
  })
  const location = await mountRecovery()
  await click(button('추천 예산 만들기'))
  expect(location().pathname).toBe('/chat')
  expect(sentChat()).toHaveLength(1)
  expect(sentChat()[0][2]).toEqual({ conversationId: null, choice: 'budget', budgetContext: { mode: 'DESIGN_RECOVERY', sourceTaskId } })
  expect(button('예산 확정하기')).toBeUndefined()
  await click(button('이번 주기만 필요해요'))
  await click(button('예산 확정하기'))
  expect(host.querySelector('aside').textContent).toContain('12,345원')
  await click(button('예산 확정', host.querySelector('aside')))
  await click(button('반영된 예산 보기'))
  expect(location().pathname).toBe('/budgets')
  expect(host.textContent).toContain('예산 2')
})

it('조정 대화에서 재원이나 보호 조건을 맞추지 못하면 승인할 수 없다', async () => {
  mockRecovery((body) => body.budgetContext ? recoveryTalk() : talk({ status: 'awaiting_input',
    phase: 'design_recovery', sourceTaskId, canApprove: false }, [], 8))
  await mountRecovery()
  await click(button('추천 예산 만들기'))
  await click(button('이번 주기만 필요해요'))
  expect(button('예산 확정하기')).toBeUndefined()
  expect(apiSend.mock.calls.some(([url]) => url.endsWith('/approve/'))).toBe(false)
})

it('새로고침하면 연결된 대화를 복원하고 취소 후에는 자동으로 다시 연결하지 않는다', async () => {
  mockRecovery((body) => recoveryTalk(body.text === '취소' ? 'ended' : 'awaiting_input'))
  const location = await mountRecovery()
  await click(button('추천 예산 만들기'))
  const entry = location()
  await act(() => root.unmount())
  root = createRoot(host)
  const restored = await mountRecovery(entry)
  expect(sentChat()).toHaveLength(1)
  expect(apiSend).toHaveBeenCalledWith('/api/chat/conversations/conversation-1/', 'GET')
  await click(button('조정 취소하고 돌아가기'))
  expect(restored().pathname).toBe('/budgets/recommend')
  expect(sentChat()[1][2]).toEqual({ conversationId: 'conversation-1', text: '취소' })
  expect(host.textContent).toContain('예산 조정을 취소했어요.')
  expect(sentChat()).toHaveLength(2)
  await click(button('예산 조정 다시 열기'))
  expect(restored().state.designRecovery.conversationId).toBe('conversation-1')
  expect(sentChat()[2][3].requestKey).not.toBe(sentChat()[0][3].requestKey)
})

it('첫 연결 응답이 끊긴 뒤 새로고침해도 같은 요청 번호를 쓴다', async () => {
  const chat = vi.fn().mockRejectedValueOnce(new Error('연결 끊김')).mockResolvedValueOnce(recoveryTalk())
  mockRecovery(chat)
  const location = await mountRecovery()
  await click(button('추천 예산 만들기'))
  expect(button('다시 보내기')).toBeDefined()
  const entry = location()
  await act(() => root.unmount())
  root = createRoot(host)
  const restored = await mountRecovery(entry)
  expect(sentChat()).toHaveLength(2)
  expect(sentChat()[1][3].requestKey).toBe(sentChat()[0][3].requestKey)
  expect(restored().state.designRecovery.conversationId).toBe('conversation-1')
})

it('만료된 연결은 추천 화면에서 새 추천으로 이어진다', async () => {
  mockRecovery(() => { throw Object.assign(new Error('expired'), { status: 409, code: 'expired' }) })
  const location = await mountRecovery()
  await click(button('추천 예산 만들기'))
  expect(host.textContent).toContain('조정 대화가 만료됐어요.')
  await click(button('추천 화면으로 돌아가기'))
  expect(location().pathname).toBe('/budgets/recommend')
  expect(button('추천 예산 만들기')).toBeDefined()
  expect(button('예산 조정 다시 열기')).toBeUndefined()
})

it.each([
  { status: 'failed', error: { message: '계산 서비스 오류' } },
  { status: 'succeeded', result: { outcome: 'needs_data', summary: '입력 정보를 확인해 주세요.' } },
  { status: 'succeeded', result: { ...recoveryResult, outcome: 'needs_data', summary: '입력 정보를 확인해 주세요.' } },
  { status: 'succeeded', result: { ...recoveryResult, handoff: { ...recoveryResult.handoff, sourceTaskId: 'other' } } },
])('연결 근거가 없는 추천 실패는 화면에서 재시도를 안내한다', async (task) => {
  mockRecovery(vi.fn(), task)
  const location = await mountRecovery()
  await click(button('추천 예산 만들기'))
  expect(location().pathname).toBe('/budgets/recommend')
  expect(host.querySelector('[role=alert]').textContent).toContain(task.error?.message ?? task.result.summary)
  expect(sentChat()).toHaveLength(0)
})

it.each([
  ['/budgets', 'approved', 'NORMAL_ADJUSTMENT', '챗봇으로 예산 조정'],
  ['/budgets', 'validated', 'INITIAL_ADJUSTMENT', '챗봇으로 예산 조정'],
  ['/budgets/recommend', 'validated', 'INITIAL_ADJUSTMENT', '챗봇과 조정하기 ›'],
])('%s의 %s 예산에서 실제 기준 ID와 모드로 새 대화를 시작한다', async (path, status, mode, label) => {
  useApi.mockImplementation((url) => ({ data: url === '/api/budgets/' ? budgetData(status) : info, error: null }))
  apiSend.mockImplementation(async (url) => url === '/api/budgets/' ? budgetData(status) : talk({ status: 'awaiting_input', canApprove: false }))
  await act(() => root.render(<MemoryRouter initialEntries={[path]}><PopupProvider><Routes>
    <Route path="/budgets" element={<BudgetPage />} /><Route path="/budgets/recommend" element={<RecommendPage />} />
    <Route path="/chat" element={<AssistantPage />} />
  </Routes></PopupProvider></MemoryRouter>))
  await click(button(label))
  const sent = apiSend.mock.calls.find(([url, method]) => url === '/api/chat/messages/' && method === 'POST')
  expect(sent[2]).toEqual({ conversationId: null, choice: 'budget', budgetContext: { baseBudgetId: 'basis-1', mode } })
  expect(apiSend.mock.calls.filter(([url]) => url === '/api/chat/messages/')).toHaveLength(1)
})

const consentState = { available: true, canRevoke: false, status: 'not_granted', documentVersion: 'v1', documentHash: 'hash',
  document: { title: '외부 전송 동의', label: '[선택] 외부 전송 동의', sections: [] },
  healthDocumentVersion: 'v1', healthDocumentHash: 'health-hash',
  healthDocument: { title: '건강 요약 처리 동의', label: '[선택] 건강 요약 처리 동의', sections: [] } }
const needConsent = () => Object.assign(new Error('예산 조정 AI 전송 동의를 먼저 확인해 주세요.'), { status: 403, code: 'budget_adjust_consent_required' })
const consentCalls = () => apiSend.mock.calls.filter(([url]) => url === '/api/budgets/adjustment-ai-consent/')

it('예산 조정 AI 동의 이력이 없으면 동의 창을 띄우고, 동의하면 거절됐던 요청을 그대로 다시 보낸다', async () => {
  let agreed = false
  apiSend.mockImplementation(async (url, method) => {
    if (url === '/api/budgets/adjustment-ai-consent/') {
      if (method === 'PUT') agreed = true
      return { ...consentState, status: agreed ? 'granted' : 'not_granted' }
    }
    if (!agreed) throw needConsent()
    return talk({ status: 'awaiting_input', canApprove: false })
  })
  await mount()
  expect(host.textContent).not.toContain('AI 동의 관리')  // 화면 위의 동의 카드는 없다
  await submit('식비를 5만 원 늘려줘')
  const dialog = () => host.querySelector('[role="dialog"]')
  expect(dialog().getAttribute('aria-label')).toBe('예산 조정 AI 전송 동의')
  expect(consentCalls().map(([, method]) => method)).toEqual(['GET'])
  expect(sentChat()).toHaveLength(1)
  for (const box of dialog().querySelectorAll('input[type="checkbox"]')) await click(box)
  await click(button('동의하고 계속', dialog()))
  expect(dialog()).toBeNull()
  expect(consentCalls().map(([, method]) => method)).toEqual(['GET', 'PUT'])
  expect(sentChat()).toHaveLength(2)
  expect(sentChat()[1][2]).toEqual(sentChat()[0][2])  // 같은 요청을 다시
  expect(sentChat()[1][2]).toEqual({ conversationId: null, text: '식비를 5만 원 늘려줘' })
  expect(host.textContent).toContain('변경안을 확인해 주세요.')
  expect(host.querySelector('[role="alert"]')).toBeNull()
  expect(input().value).toBe('')
})

it('동의 창을 닫으면 다시 보내지 않고 안내만 남기며 입력한 문장은 그대로 둔다', async () => {
  apiSend.mockImplementation(async (url) => {
    if (url === '/api/budgets/adjustment-ai-consent/') return consentState
    throw needConsent()
  })
  await mount()
  await submit('식비를 5만 원 늘려줘')
  await click(button('취소', host.querySelector('[role="dialog"]')))
  expect(host.querySelector('[role="dialog"]')).toBeNull()
  expect(sentChat()).toHaveLength(1)
  expect(consentCalls().map(([, method]) => method)).toEqual(['GET'])
  expect(host.querySelector('[role="alert"]').textContent).toContain('AI 전송 동의가 필요해요')
  expect(input().value).toBe('식비를 5만 원 늘려줘')
})

it('초과한 카테고리의 [예산 조정하기]는 그 카테고리와 초과액을 담아 새 대화를 시작한다', async () => {
  const data = { ...budgetData('approved'), categories: [
    { category: 'FOOD', categoryName: '식비', allocated: 300000, spent: 312000, minAmount: null, locked: false, lockedAmount: null },
    { category: 'BEAUTY', categoryName: '뷰티·미용', allocated: 50000, spent: 20000, minAmount: null, locked: false, lockedAmount: null },
    { category: 'CULTURE_LEISURE', categoryName: '문화·여가', allocated: 0, spent: 7000, minAmount: null, locked: false, lockedAmount: null },
    { category: 'ETC', categoryName: '기타', allocated: 10000, spent: 30000, minAmount: null, locked: false, lockedAmount: null },
  ] }
  useApi.mockImplementation((url) => ({ data: url === '/api/budgets/' ? data : info, error: null }))
  apiSend.mockImplementation(async () => talk({ status: 'awaiting_input', canApprove: false }))
  await act(() => root.render(<MemoryRouter initialEntries={['/budgets']}><PopupProvider><Routes>
    <Route path="/budgets" element={<BudgetPage />} /><Route path="/chat" element={<AssistantPage />} />
  </Routes></PopupProvider></MemoryRouter>))
  // 초과한 카테고리(식비)에만 버튼이 있다. 예산 0원 칸(문화·여가)은 초과로 보지 않고, 기타는 초과여도 조정으로 넘기지 않는다
  expect([...host.querySelectorAll('button')].filter((b) => b.textContent.trim() === '예산 조정하기 ›')).toHaveLength(1)
  await click(host.querySelector('[aria-label="식비 예산 조정하기"]'))
  expect(sentChat()).toHaveLength(1)
  expect(sentChat()[0][2]).toEqual({ conversationId: null, choice: 'budget', budgetContext: {
    baseBudgetId: 'basis-1', mode: 'NORMAL_ADJUSTMENT', hints: { reason: 'over', categories: ['FOOD'], overAmount: 12000 },
  } })
})

it('승인 전 초안 예산에서는 초과한 카테고리에 [예산 조정하기]를 보이지 않는다', async () => {
  const data = { ...budgetData('validated'), categories: [
    { category: 'FOOD', categoryName: '식비', allocated: 300000, spent: 312000, minAmount: null, locked: false, lockedAmount: null },
  ] }
  useApi.mockImplementation((url) => ({ data: url === '/api/budgets/' ? data : info, error: null }))
  await act(() => root.render(<MemoryRouter initialEntries={['/budgets']}><PopupProvider><Routes>
    <Route path="/budgets" element={<BudgetPage />} /><Route path="/chat" element={<AssistantPage />} />
  </Routes></PopupProvider></MemoryRouter>))
  expect(host.textContent).toContain('12,000원 초과')
  expect(host.querySelector('[aria-label="식비 예산 조정하기"]')).toBeNull()
})

it.each([
  ['locked_amount', 400000, 350000, -50000, '잠금액과 유지 목표가 달라요.'],
  ['minimum_amount', 300000, 350000, 50000, '유지 목표가 최소 보장액보다 작아요.'],
  ['amount_unit', 55555, 60000, 4445, '유지 목표를 5천 원 단위로 확인해 주세요.'],
  ['living_budget', 700000, 600000, -100000, '유지 목표를 현재 생활비 안에 모두 반영할 수 없어요.'],
  ['invalid_target', null, null, null, '저장된 유지 목표를 확인해 주세요.'],
])('다음 예산의 %s 목표 충돌은 서버 금액과 구체적인 이유를 보여 준다', async (reason, requestedAmount, possibleAmount, diff, expected) => {
  apiSend.mockImplementation(async (url) => {
    if (url === '/api/budgets/') return budgetData('approved')
    if (url === '/api/budgets/recommend/') return { task: { taskId: 'design-task', status: 'queued' } }
    if (url === '/api/tasks/design-task/') return { status: 'succeeded', result: {
      outcome: 'infeasible', summary: '저장한 유지 목표와 현재 예산 조건을 확인해 주세요.',
      conflicts: [{ type: 'target_conflict', reason, categories: ['FOOD'], requestedAmount, possibleAmount, diff }],
    } }
    throw new Error(`unexpected endpoint: ${url}`)
  })
  await act(() => root.render(<MemoryRouter initialEntries={['/budgets/recommend']}><PopupProvider><RecommendPage /></PopupProvider></MemoryRouter>))
  await click(button('다시 추천받기'))
  const alert = host.querySelector('[role=alert]')
  expect(alert.textContent).toContain(expected)
  if (requestedAmount !== null) expect(alert.textContent).toContain(`목표 ${requestedAmount.toLocaleString('ko-KR')}원`)
  if (possibleAmount !== null) expect(alert.textContent).toContain(`${possibleAmount.toLocaleString('ko-KR')}원`)
  if (diff !== null) expect(alert.textContent).toContain(`차이 ${Math.abs(diff).toLocaleString('ko-KR')}원`)
  expect(alert.textContent).not.toContain('NaN')
})
