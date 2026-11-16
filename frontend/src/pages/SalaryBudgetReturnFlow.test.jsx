import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssistantPage from './AssistantPage.jsx'
import SalaryPage from './SalaryPage.jsx'
import ProtectPage from './ProtectPage.jsx'
import RecommendPage from './RecommendPage.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const CONVERSATION = 'salary-return-conversation'
const RETURN_QUERY = `budgetConversation=${CONVERSATION}`
const info = { engine: 'router', features: [{ value: 'budget', label: '예산 조정' }], testMode: false }
const handoff = () => ({
  ...info,
  conversationId: CONVERSATION,
  messages: [
    { messageId: 'salary-request', role: 'user', content: { text: '교육비 10만원 늘리고 싶은데 월급부터 수정할게' } },
    { messageId: 'salary-handoff', role: 'bot', content: {
      agent: 'budget', turn: 4,
      lines: ['월급은 마이페이지에서 수정해 주세요. 교육비 10만원 조정 요청은 보관했어요.'],
      choices: [{ value: 'salary_resume', label: '이전 예산 조정 이어하기' }],
      budgetAdjustment: { status: 'awaiting_input', phase: 'salary_handoff', canApprove: false },
    } },
  ],
})
const budget = (id = 'approved-budget', status = 'approved') => ({
  cycle: { startsOn: '2026-11-05', endsBefore: '2026-12-05' },
  budget: { budgetId: id, status, versionNo: status === 'approved' ? 1 : 2, salaryConfirmed: true },
  split: { funding: 3000000, fixed: 1000000, saving: 500000, cushion: 200000, living: 1000000, spare: 300000 },
  categories: [{ category: 'FOOD', categoryName: '식비', allocated: 1000000, minAmount: 0, locked: false, reason: '새 조건으로 나눈 예산이에요.' }],
})
const protect = () => ({
  missing: [], housingType: 'family', blocked: null, savingsMin: 0, budgetApproved: true,
  base: { takeHomeIncome: 3000000, fixedTotal: 1000000, available: 2000000 },
  recommended: { livingBudget: 1000000, savingFloor: 500000, cushionMonthly: 200000 },
  saved: { livingBudget: 800000, savingFloor: 400000, cushionMonthly: 100000, spare: 200000 },
  cushion: { required: true, target: 9000000, balanceTotal: 0 },
})

let host, root, api, mountNumber

function LocationProbe() {
  const { pathname, search, state } = useLocation()
  return <output data-testid="route">{JSON.stringify({ pathname, search, state })}</output>
}

// 화면·라우터·팝업·API 유틸은 실제 코드를 사용하고 서버 응답만 대체한다.
function mockServer() {
  api = {
    calls: [],
    salary: { takeHomeIncome: 2500000, payday: 5, pendingPaydayChange: null, pendingIncomeChange: null, incomeBasis: null },
    talk: handoff(),
    currentBudget: budget(),
    approvedId: 'approved-budget',
    recommendFails: false,
    salaryFails: false,
    approveFails: false,
    recommendRecovery: false,
    salaryReview: null,
  }
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const method = options.method ?? 'GET'
    const body = options.body ? JSON.parse(options.body) : undefined
    api.calls.push({ url, method, body })
    let data
    if (url === '/api/auth/me/') data = { userId: 'salary-test-user', onboardingCompleted: true }
    else if (url === '/api/chat/messages/' && method === 'GET') data = info
    else if (url === `/api/chat/conversations/${CONVERSATION}/`) data = api.talk
    else if (url === '/api/chat/messages/' && method === 'POST') {
      if (body.choice?.value === 'salary_resume') {
        api.talk = { ...info, conversationId: CONVERSATION, messages: [
          ...api.talk.messages,
          { messageId: 'resumed-request', role: 'user', content: { text: '이전 예산 조정 이어하기' } },
          { messageId: 'resumed-answer', role: 'bot', content: {
            agent: 'budget', turn: 5,
            lines: ['최신 월급으로 교육비 10만원 조정을 이어갈게요.'],
            budgetAdjustment: { status: 'awaiting_input', phase: 'comparison', canApprove: false },
          } },
        ] }
      }
      data = api.talk
    } else if (url === '/api/budgets/salary/') {
      if (method === 'PUT' && api.salaryFails) return {
        ok: false, status: 400, json: async () => ({ error: { message: '월급을 저장하지 못했어요. 다시 확인해 주세요.' } }),
      }
      if (method === 'PUT') api.salary = { ...api.salary, takeHomeIncome: body.takeHomeIncome, payday: body.payday }
      data = api.salary
    } else if (String(url).startsWith('/api/budgets/salary-review/')) {
      data = api.salaryReview ?? { status: 'no_increase' }
      if (api.salaryReview && method === 'POST') {
        const saving = data.remainingAmount * body.savingPercent / 100
        api.salaryReview = { ...data, savingPercent: body.savingPercent, proposal: {
          proposalId: 'salary-review-draft', state: 'awaiting_approval', mode: 'NORMAL_ADJUSTMENT',
          canApprove: true, changes: [
            ...data.requestChanges,
            { category: 'SAVINGS', before: 500000, after: 500000 + saving, diff: saving },
            { category: 'FOOD', before: 400000, after: 500000, diff: 100000 },
          ], shortfalls: [], persistenceItems: [],
        }, summary: { allocationReview: { status: 'reviewed', plan: 'b', allocatedAmount: 100000,
          unallocatedAmount: data.remainingAmount - saving - 100000,
          message: '소비 내역과 이전 예산 변경을 검토해 식비에 100,000원을 더 배정했어요.' } } }
        data = api.salaryReview
      }
    } else if (url === '/api/budgets/proposals/salary-review-draft/approve/' && method === 'POST') {
      api.approvedId = 'salary-review-draft'
      api.salaryReview.proposal = { ...api.salaryReview.proposal, state: 'saved', savedRef: 'salary-review-draft' }
      data = { adjustment: api.salaryReview.proposal }
    } else if (url === '/api/budgets/protect/') {
      data = method === 'PUT' ? { locksReleased: [], approvedBudgetUpdated: false } : protect()
    } else if (url === '/api/budgets/') data = api.currentBudget
    else if (url === '/api/budgets/ai-consent/') data = { available: false, status: 'not_granted' }
    else if (url === '/api/budgets/recommend/' && method === 'POST') data = { task: { taskId: 'salary-review-task' } }
    else if (url === '/api/tasks/salary-review-task/') {
      if (api.recommendFails) data = { status: 'failed', error: { message: '추천 계산에 실패했어요. 잠시 후 다시 시도해 주세요.' } }
      else if (api.recommendRecovery) data = { status: 'succeeded', result: {
        outcome: 'needs_upper_allocation_review', summary: '지킬 돈 배분을 다시 확인해 주세요.',
        handoff: { mode: 'DESIGN_RECOVERY', sourceTaskId: 'salary-review-task' },
      } }
      else {
        api.currentBudget = budget('review-draft', 'validated')
        data = { status: 'succeeded', result: { outcome: 'proposed', budgetId: 'review-draft' } }
      }
    } else if (url === '/api/budgets/review-draft/approve/' && method === 'POST') {
      if (api.approveFails) return {
        ok: false, status: 409, json: async () => ({ error: { message: '예산 조건이 바뀌어 승인하지 못했어요.' } }),
      }
      api.approvedId = 'review-draft'
      api.currentBudget = budget('review-draft', 'approved')
      data = api.currentBudget
    } else throw new Error(`Unexpected request: ${method} ${url}`)
    return { ok: true, status: 200, json: async () => data }
  }))
}

beforeEach(() => {
  sessionStorage.clear()
  document.cookie = 'csrftoken=salary-flow-test; path=/'
  Element.prototype.scrollIntoView = vi.fn()
  mountNumber = 0
  mockServer()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

async function mount(entry = '/chat') {
  await act(() => root.render(
    <MemoryRouter key={++mountNumber} initialEntries={[entry]}>
      <PopupProvider>
        <LocationProbe />
        <Routes>
          <Route path="/chat" element={<AssistantPage />} />
          <Route path="/budgets/salary" element={<SalaryPage />} />
          <Route path="/budgets/protect" element={<ProtectPage />} />
          <Route path="/budgets/recommend" element={<RecommendPage />} />
        </Routes>
      </PopupProvider>
    </MemoryRouter>,
  ))
}
const route = () => JSON.parse(host.querySelector('[data-testid="route"]').textContent)
const calls = (url, method) => api.calls.filter((call) => call.url === url && call.method === method)
const findAction = (label) => [...host.querySelectorAll('button, a')].find((item) => item.textContent === label)
async function click(label) {
  const item = findAction(label)
  expect(item, `화면에 '${label}' 동작이 있어야 해요`).toBeTruthy()
  expect(item.disabled).not.toBe(true)
  await act(() => item.click())
}
async function type(selector, value) {
  const input = host.querySelector(selector)
  expect(input).toBeTruthy()
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function submit(selector = '#mc-form') {
  await act(() => host.querySelector(selector).dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}
async function enterFromChat(text = '교육비 10만원 늘리고 싶은데 월급부터 수정할게') {
  await mount()
  await type('input[aria-label="챗봇에게 보낼 메시지"]', text)
  const form = host.querySelector('input[aria-label="챗봇에게 보낼 메시지"]').form
  await act(() => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await click('마이페이지에서 월급 수정하기')
  expect(route().pathname).toBe('/budgets/salary')
  expect(new URLSearchParams(route().search).get('budgetConversation')).toBe(CONVERSATION)
}
async function saveSalary() {
  await type('#mc-salary', '300')
  await submit()
  expect(host.textContent).toContain('월급은 저장됐어요.')
  expect(calls('/api/budgets/salary/', 'PUT')).toHaveLength(1)
  expect(calls('/api/budgets/salary/', 'PUT')[0].body).toEqual({
    takeHomeIncome: 3000000, payday: 5, applyFrom: 'current_cycle', preserveUnchangedSchedules: true,
  })
}
async function review() {
  await click('새 조건으로 예산 검토')
  expect(route().pathname).toBe('/budgets/protect')
  expect(host.textContent).toContain('먼저 지킬 돈을 정해요')
  expect(host.querySelector('#mc-p-livingBudget').value).toBe('1,000,000')
  expect(findAction('추천 예산 보기')).toBeTruthy()
  await submit()
  expect(route().pathname).toBe('/budgets/recommend')
}
async function returnToChat() {
  await click('이전 예산 조정 이어하기')
  expect(route().pathname).toBe('/chat')
  expect(host.textContent).toContain('최신 월급으로 교육비 10만원 조정을 이어갈게요.')
  const resume = calls('/api/chat/messages/', 'POST').filter(({ body }) => body.choice?.value === 'salary_resume')
  expect(resume).toHaveLength(1)
  expect(resume[0].body).toEqual({ conversationId: CONVERSATION, choice: { value: 'salary_resume', turn: 4 } })
  expect(calls(`/api/chat/conversations/${CONVERSATION}/`, 'GET')).toHaveLength(1)
  expect(calls('/api/budgets/salary/', 'PUT')).toHaveLength(1)
}

describe('실제 월급·지킬 돈·추천 화면과 예산 조정 대화 왕복', () => {
  it.each(['chat', 'mypage'])('%s에서 월급을 저장하면 공통 검토 결과를 보여 주고 별도 승인한다', async (entry) => {
    api.salary.takeHomeIncome = 3000000
    api.salaryReview = { status: 'ready', basisHash: 'latest-approved', incomeBefore: 3000000, incomeAfter: 4000000,
      increase: 1000000, requestedAmount: entry === 'chat' ? 500000 : 0,
      remainingAmount: entry === 'chat' ? 500000 : 1000000, savingPercent: 30,
      requestChanges: entry === 'chat' ? [{ category: 'EDUCATION', before: 50000, after: 550000, diff: 500000 }] : [] }
    if (entry === 'chat') {
      const text = '교육비 50만원 늘리고 싶은데 월급부터 수정할게'
      api.talk.messages[0].content.text = text
      await enterFromChat(text)
    } else await mount({ pathname: '/budgets/salary', state: { from: 'my' } })
    await type('#mc-salary', '400')
    await submit()
    if (entry === 'mypage') await click('이번 주기부터지킬 돈과 이번 예산을 바로 다시 정해요')
    expect(host.textContent).toContain('월급은 저장됐어요.')
    expect(calls('/api/budgets/salary-review/', 'POST')).toHaveLength(0)
    await click('새 조건으로 예산 검토')
    expect(route().pathname).toBe('/budgets/protect')
    if (entry === 'chat') expect(host.textContent).toContain('교육50,000원 → 550,000원')
    await click('이 비율로 예산 미리보기')
    expect(calls('/api/budgets/salary-review/', 'POST')[0].body).toEqual({ basisHash: 'latest-approved', savingPercent: 30,
      ...(entry === 'chat' ? { conversationId: CONVERSATION } : {}) })
    expect(host.textContent).toContain('식비400,000원 → 500,000원')
    expect(api.approvedId).toBe('approved-budget')
    expect(calls('/api/budgets/recommend/', 'POST')).toHaveLength(0)
    await click('예산 확정하기')
    await click('예산 확정')
    expect(api.approvedId).toBe('salary-review-draft')
    expect(host.textContent).toContain('새 예산을 반영했어요')
    expect(calls('/api/budgets/proposals/salary-review-draft/approve/', 'POST')).toHaveLength(1)
    if (entry === 'chat') {
      await click('예산 조정 대화로 돌아가기')
      expect(route().pathname).toBe('/chat')
      expect(sessionStorage.getItem('mc-chat-conversation')).toBe(CONVERSATION)
      expect(calls('/api/budgets/proposals/salary-review-draft/approve/', 'POST')).toHaveLength(1)
    }
  })

  it('월급 저장만으로 추천하거나 승인하지 않고 같은 대화를 한 번 이어간다', async () => {
    await enterFromChat()
    await saveSalary()
    expect(findAction('새 조건으로 예산 검토')).toBeTruthy()
    expect(calls('/api/budgets/protect/', 'PUT')).toHaveLength(0)
    expect(calls('/api/budgets/recommend/', 'POST')).toHaveLength(0)
    expect(calls('/api/budgets/review-draft/approve/', 'POST')).toHaveLength(0)
    expect(api.approvedId).toBe('approved-budget')
    await returnToChat()
  })

  it('실제 지킬 돈 화면에서 검토한 초안을 승인한 뒤 이전 대화로 돌아온다', async () => {
    await enterFromChat()
    await saveSalary()
    await review()
    expect(calls('/api/budgets/protect/', 'PUT')).toHaveLength(1)
    expect(calls('/api/budgets/protect/', 'PUT')[0].body).toEqual({
      livingBudget: 1000000, savingFloor: 500000, cushionMonthly: 200000, updateApprovedBudget: false,
    })
    expect(calls('/api/budgets/recommend/', 'POST')).toHaveLength(1)
    expect(calls('/api/budgets/review-draft/approve/', 'POST')).toHaveLength(0)
    expect(api.approvedId).toBe('approved-budget')
    expect(findAction('이전 예산 조정 이어하기')).toBeTruthy()
    await click('이 예산으로 시작하기')
    expect(host.textContent).toContain('새 예산을 승인했어요.')
    expect(api.approvedId).toBe('review-draft')
    expect(calls('/api/budgets/review-draft/approve/', 'POST')).toHaveLength(1)
    await returnToChat()
  })

  it('검토 도중 돌아가도 저장한 월급과 기존 승인 예산을 유지한다', async () => {
    await enterFromChat()
    await saveSalary()
    await click('새 조건으로 예산 검토')
    expect(route().pathname).toBe('/budgets/protect')
    expect(host.textContent).toContain('새 안을 승인하기 전까지 기존 승인 예산은 유지돼요.')
    await returnToChat()
    expect(api.salary.takeHomeIncome).toBe(3000000)
    expect(api.approvedId).toBe('approved-budget')
    expect(calls('/api/budgets/protect/', 'PUT')).toHaveLength(0)
    expect(calls('/api/budgets/recommend/', 'POST')).toHaveLength(0)
  })

  it('추천 실패 후에도 월급 저장 상태와 이전 대화 복귀 동작이 남는다', async () => {
    api.recommendFails = true
    await enterFromChat()
    await saveSalary()
    await review()
    expect(host.textContent).toContain('추천 계산에 실패했어요')
    expect(host.textContent).toContain('월급은 저장됐어요')
    expect(findAction('이 예산으로 시작하기')).toBeUndefined()
    await returnToChat()
    expect(api.approvedId).toBe('approved-budget')
    expect(calls('/api/budgets/review-draft/approve/', 'POST')).toHaveLength(0)
  })

  it('서버가 추천 복구 대화를 제안해도 새 대화로 자동 이동하지 않고 이전 대화로 복귀한다', async () => {
    api.recommendRecovery = true
    await enterFromChat()
    await saveSalary()
    await review()
    expect(route().pathname).toBe('/budgets/recommend')
    expect(host.textContent).toContain('지킬 돈 배분을 다시 확인해 주세요.')
    expect(host.textContent).toContain('월급은 저장됐어요')
    expect(calls('/api/chat/messages/', 'POST')).toHaveLength(1)
    await returnToChat()
    expect(api.approvedId).toBe('approved-budget')
  })

  it('월급 저장 실패를 저장 완료로 표시하지 않고 기존 대화로 돌아갈 수 있다', async () => {
    api.salaryFails = true
    await enterFromChat()
    await type('#mc-salary', '300')
    await submit()
    expect(host.textContent).toContain('월급을 저장하지 못했어요.')
    expect(host.textContent).not.toContain('월급은 저장됐어요')
    expect(findAction('새 조건으로 예산 검토')).toBeUndefined()
    expect(host.querySelector('#mc-salary').value).toBe('300')
    expect(api.salary.takeHomeIncome).toBe(2500000)
    expect(calls('/api/budgets/recommend/', 'POST')).toHaveLength(0)
    await returnToChat()
  })

  it('승인 재검산이 실패해도 승인 완료로 표시하지 않고 기존 대화로 복귀한다', async () => {
    api.approveFails = true
    await enterFromChat()
    await saveSalary()
    await review()
    await click('이 예산으로 시작하기')
    expect(host.textContent).toContain('예산 조건이 바뀌어 승인하지 못했어요.')
    expect(host.textContent).not.toContain('새 예산을 승인했어요.')
    expect(host.textContent).toContain('월급은 저장됐어요')
    expect(api.approvedId).toBe('approved-budget')
    expect(calls('/api/budgets/review-draft/approve/', 'POST')).toHaveLength(1)
    await returnToChat()
  })

  it('예약 월급과 급여일이 있어도 현재값을 편집하고 변경하지 않은 예약 보존을 요청한다', async () => {
    api.salary.pendingIncomeChange = { takeHomeIncome: 3200000, effectiveOn: '2026-12-05' }
    api.salary.pendingPaydayChange = { payday: 10, effectiveOn: '2026-12-10' }
    await enterFromChat()
    expect(host.querySelector('#mc-salary').value).toBe('250')
    expect(host.querySelector('#mc-payday').value).toBe('5')
    await saveSalary()
    expect(calls('/api/budgets/salary/', 'PUT')[0].body.preserveUnchangedSchedules).toBe(true)
    await returnToChat()
  })

  it('라우터 state 없이 다시 열어도 월급·검토·초안 화면에서 복귀 대화를 유지한다', async () => {
    sessionStorage.setItem('mc-chat-conversation', 'other-conversation')
    await mount(`/budgets/salary?${RETURN_QUERY}`)
    await saveSalary()
    await mount(`${route().pathname}${route().search}`)
    expect(route().state).toBeNull()
    expect(host.textContent).toContain('월급은 저장됐어요.')
    await click('새 조건으로 예산 검토')
    await mount(`${route().pathname}${route().search}`)
    expect(route().state).toBeNull()
    expect(host.querySelector('#mc-p-livingBudget').value).toBe('1,000,000')
    await submit()
    expect(route().pathname).toBe('/budgets/recommend')
    expect(new URLSearchParams(route().search).get('budgetConversation')).toBe(CONVERSATION)
    await mount(`${route().pathname}${route().search}`)
    expect(route().state).toBeNull()
    expect(findAction('이 예산으로 시작하기')).toBeTruthy()
    expect(calls('/api/budgets/recommend/', 'POST')).toHaveLength(1)
    await returnToChat()
    expect(sessionStorage.getItem('mc-chat-conversation')).toBe(CONVERSATION)
    expect(api.approvedId).toBe('approved-budget')
  })
})
