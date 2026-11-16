import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssistantPage from './AssistantPage.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import { runFinanceSync } from '../utils/financeSync.js'

vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), newRequestKey: vi.fn(), useApi: vi.fn() }))
vi.mock('../components/Layout.jsx', async () => {
  const { usePopup } = await import('../popups/context.js')
  return { default: function Layout({ children, foot }) { return <main>{children}{foot}{usePopup().popup}</main> } }  // 팝업은 Layout 이 그린다
})
vi.mock('../components/Icon.jsx', () => ({ default: () => <span /> }))
vi.mock('../utils/financeSync.js', async (original) => ({ ...await original(), runFinanceSync: vi.fn() }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root, keyNumber
const info = (engine) => ({ engine, features: [{ value: 'spending', label: '소비 분석' }], testMode: false })
const reply = (engine, number, text, content = {}) => ({
  ...info(engine), conversationId: 'conversation-1',
  messages: [
    { messageId: `user-${number}`, role: 'user', content: { text } },
    { messageId: `bot-${number}`, role: 'bot', content: { text: `답변 ${number}`, ...content } },
  ],
})

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  keyNumber = 0
  newRequestKey.mockImplementation(() => `request-key-${++keyNumber}`)
  useApi.mockReturnValue({ data: info('router'), error: null })
  Element.prototype.scrollIntoView = vi.fn()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
})
const mount = async () => act(() => root.render(<MemoryRouter><PopupProvider><AssistantPage /></PopupProvider></MemoryRouter>))
const button = (label) => [...host.querySelectorAll('button')].find((b) => b.textContent === label)
const click = async (element) => act(() => element.click())
const input = () => host.querySelector('input[aria-label="챗봇에게 보낼 메시지"]')
const salaryHandoff = () => reply('router', 1, '월급 수정하고 싶어', {
  agent: 'budget', turn: 4, lines: ['월급은 마이페이지에서 수정해 주세요. 이전 조정은 보관했어요.'],
  choices: [{ value: 'salary_resume', label: '이전 예산 조정 이어하기' }],
  budgetAdjustment: { status: 'awaiting_input', phase: 'salary_handoff', canApprove: false },
})

function SalaryHandoffPage() {
  const { state, search } = useLocation()
  return <div><output>{JSON.stringify({ state, search })}</output>
    <Link to={state.budgetReturn.returnTo}>이전 예산 조정 이어하기</Link></div>
}

async function submit(text) {
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input(), text)
    input().dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(() => input().form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}

describe('대화 스크롤', () => {
  function geometry(replyHeight = 120) {
    const main = host.querySelector('main')
    const spacer = host.querySelector('.mc-asst-log > [aria-hidden="true"]')
    const questions = () => [...host.querySelectorAll('.mc-msg.me')]
    const lastTop = () => 400 + Math.max(0, questions().length - 1) * 900
    let scrollTop = 0
    Object.defineProperties(main, {
      clientHeight: { get: () => 600 },
      scrollHeight: { get: () => Math.max(600, lastTop() + replyHeight + (parseFloat(spacer.style.height) || 0) + 100) },
      scrollTop: { get: () => scrollTop, set: (value) => { scrollTop = Math.max(0, Math.min(value, main.scrollHeight - 600)) } },
    })
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function () {
      const index = questions().indexOf(this)
      return { top: this === main ? 80 : 80 - scrollTop + (this === spacer ? lastTop() + replyHeight : 400 + index * 900) }
    })
    return { main, spacer }
  }

  it.each([120, 1100])('전송하면 하단, %i px 답변이 오면 방금 보낸 질문을 상단에 놓는다', async (height) => {
    let resolve
    apiSend.mockImplementation(() => new Promise((done) => { resolve = done }))
    await mount()
    const { main, spacer } = geometry(height)
    await submit('교육비를 늘려줘')
    expect(main.scrollTop).toBe(main.scrollHeight - main.clientHeight)
    await act(() => resolve(reply('router', 1, '교육비를 늘려줘')))
    expect(main.scrollTop).toBe(400)
    expect(parseFloat(spacer.style.height) > 0).toBe(height < 600)

    main.scrollTop = 100 // 이전 답변을 읽는 중에도 새 메시지를 보내면 끝으로 이동한다.
    await submit('다른 방법도 보여줘')
    expect(spacer.style.height).toBe('0px')
    expect(main.scrollTop).toBe(main.scrollHeight - main.clientHeight)
    await act(() => resolve(reply('router', 2, '다른 방법도 보여줘')))
    expect(main.scrollTop).toBe(1300)

    main.scrollTop = 100
    await act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input(), '작성 중')
      input().dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(main.scrollTop).toBe(100)
    await click(button('새 대화'))
    expect(spacer.style.height).toBe('0px')
  })

  it('추천 선택지 전송도 같은 규칙을 사용한다', async () => {
    let resolve
    apiSend.mockImplementation(() => new Promise((done) => { resolve = done }))
    await mount()
    const { main } = geometry()
    await click(button('소비를 분석해주세요'))
    expect(main.scrollTop).toBe(main.scrollHeight - main.clientHeight)
    await act(() => resolve(reply('router', 1, '소비를 분석해주세요', {
      choices: [{ value: 'this_month', label: '이번 달로 보기' }], turn: 1,
    })))
    expect(main.scrollTop).toBe(400)
    await click(button('이번 달로 보기'))
    expect(main.scrollTop).toBe(main.scrollHeight - main.clientHeight)
    await act(() => resolve(reply('router', 2, '이번 달로 보기')))
    expect(main.scrollTop).toBe(1300)
  })

  it('전송 오류는 아래에서 보여주고 재시도 성공 때 해당 질문으로 이동한다', async () => {
    let resolve, reject
    apiSend.mockImplementation(() => new Promise((done, fail) => { resolve = done; reject = fail }))
    await mount()
    const { main, spacer } = geometry()
    await submit('첫 질문')
    await act(() => resolve(reply('router', 1, '첫 질문')))
    await submit('두 번째 질문')
    await act(() => reject(new TypeError('연결 끊김')))
    expect(spacer.style.height).toBe('0px')
    expect(main.scrollTop).toBe(main.scrollHeight - main.clientHeight)
    await click(button('다시 보내기'))
    await act(() => resolve(reply('router', 2, '두 번째 질문')))
    expect(main.scrollTop).toBe(1300)
  })
})

it('월급 수정 버튼에 복귀 대화를 전달하고 돌아오면 같은 대화를 한 번만 재검토한다', async () => {
  const handoff = salaryHandoff()
  apiSend.mockResolvedValueOnce(handoff).mockResolvedValueOnce(handoff)
    .mockResolvedValueOnce(reply('router', 2, '이전 예산 조정 이어하기', {
      agent: 'budget', turn: 5, lines: ['최신 월급으로 이전 조정을 이어갈게요.'],
      budgetAdjustment: { status: 'awaiting_input', phase: 'comparison' },
    }))
  await act(() => root.render(<MemoryRouter initialEntries={['/chat']}><PopupProvider><Routes>
    <Route path="/chat" element={<AssistantPage />} />
    <Route path="/budgets/salary" element={<SalaryHandoffPage />} />
  </Routes></PopupProvider></MemoryRouter>))
  await submit('월급 수정하고 싶어')
  await click(button('마이페이지에서 월급 수정하기'))
  expect(JSON.parse(host.querySelector('output').textContent)).toEqual({
    search: '?budgetConversation=conversation-1',
    state: { from: 'my', budgetReturn: { conversationId: 'conversation-1', returnTo: '/chat?budgetConversation=conversation-1' } },
  })
  expect(apiSend).toHaveBeenCalledTimes(1)
  await click(host.querySelector('a'))
  expect(apiSend.mock.calls[1]).toEqual(['/api/chat/conversations/conversation-1/', 'GET'])
  expect(apiSend.mock.calls[2][2]).toEqual({ conversationId: 'conversation-1', choice: { value: 'salary_resume', turn: 4 } })
  expect(apiSend).toHaveBeenCalledTimes(3)
  expect(apiSend.mock.calls.every(([path]) => path.startsWith('/api/chat/'))).toBe(true)
  expect(host.textContent).toContain('월급 수정하고 싶어')
  expect(host.textContent).toContain('최신 월급으로 이전 조정을 이어갈게요.')
})

it('명시한 복귀 대화가 저장된 다른 대화보다 우선하고 이미 이어간 대화는 다시 전송하지 않는다', async () => {
  sessionStorage.setItem('mc-chat-conversation', 'another-conversation')
  apiSend.mockResolvedValueOnce(reply('router', 2, '이어서 조정', {
    agent: 'budget', turn: 5, budgetAdjustment: { status: 'awaiting_input', phase: 'comparison' },
  }))
  await act(() => root.render(<MemoryRouter initialEntries={['/chat?budgetConversation=conversation-1']}>
    <PopupProvider><AssistantPage /></PopupProvider></MemoryRouter>))
  expect(apiSend.mock.calls).toEqual([['/api/chat/conversations/conversation-1/', 'GET']])
  expect(sessionStorage.getItem('mc-chat-conversation')).toBe('conversation-1')
})

it('월급 증가분 검토는 같은 대화를 마이페이지와 공통인 배분 화면으로 연결한다', async () => {
  const handoff = salaryHandoff()
  handoff.messages[1].content.budgetAdjustment.phase = 'salary_allocation_review'
  apiSend.mockResolvedValueOnce(handoff).mockResolvedValueOnce(handoff)
    .mockResolvedValueOnce(reply('router', 2, '이전 예산 조정 이어하기', {
      agent: 'budget', turn: 5, lines: ['확정한 변경을 반영했어요. 같은 요청을 다시 적용하지 않을게요.'],
      budgetAdjustment: { status: 'replied', phase: null },
    }))
  await act(() => root.render(<MemoryRouter initialEntries={['/chat']}><PopupProvider><Routes>
    <Route path="/chat" element={<AssistantPage />} />
    <Route path="/budgets/protect" element={<SalaryHandoffPage />} />
  </Routes></PopupProvider></MemoryRouter>))
  await submit('이전 예산 조정 이어하기')
  await click(button('월급 증가분 배분 검토'))
  expect(JSON.parse(host.querySelector('output').textContent)).toEqual({
    search: '?budgetConversation=conversation-1&salaryReview=1',
    state: { from: 'my', salaryReview: true,
      budgetReturn: { conversationId: 'conversation-1', returnTo: '/chat?budgetConversation=conversation-1' } },
  })
  expect(apiSend).toHaveBeenCalledTimes(1)
  await click(host.querySelector('a'))
  expect(apiSend.mock.calls[2][2]).toEqual({ conversationId: 'conversation-1', choice: { value: 'salary_resume', turn: 4 } })
  expect(host.textContent).toContain('같은 요청을 다시 적용하지 않을게요.')
  expect(apiSend).toHaveBeenCalledTimes(3)
})

it('복귀 요청의 응답이 유실돼도 자동 반복하지 않고 같은 키로 재시도한다', async () => {
  apiSend.mockResolvedValueOnce(salaryHandoff()).mockRejectedValueOnce(new TypeError('응답 유실'))
    .mockResolvedValueOnce(reply('router', 2, '이전 예산 조정 이어하기'))
  await act(() => root.render(<MemoryRouter initialEntries={['/chat?budgetConversation=conversation-1']}>
    <PopupProvider><AssistantPage /></PopupProvider></MemoryRouter>))
  expect(apiSend).toHaveBeenCalledTimes(2)
  await click(button('다시 보내기'))
  expect(apiSend.mock.calls[2]).toEqual(apiSend.mock.calls[1])
})

it('복귀 대화가 없거나 다른 사용자 것이면 이어가기 요청을 보내지 않는다', async () => {
  apiSend.mockRejectedValueOnce(Object.assign(new Error('없는 대화'), { status: 404 }))
  await act(() => root.render(<MemoryRouter initialEntries={['/chat?budgetConversation=missing']}>
    <PopupProvider><AssistantPage /></PopupProvider></MemoryRouter>))
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(sessionStorage.getItem('mc-chat-conversation')).toBeNull()
  expect(button('이전 예산 조정 이어하기')).toBeUndefined()
})

it('전체 분석 뒤 예시를 누르면 같은 대화에서 새 질문으로 전송한다', async () => {
  const question = '지난달보다 많이 썼어?'
  apiSend.mockResolvedValueOnce(reply('router', 1, '아무거나 분석해줘', {
    result: true, lines: ['이번 달 소비예요.'], examples: [question], agent: 'spending', turn: 1,
  })).mockResolvedValueOnce(reply('router', 2, question, { result: true, lines: ['지난달 비교예요.'], agent: 'spending', turn: 2 }))
  await mount()
  await submit('아무거나 분석해줘')
  expect(host.querySelector('.mc-asst-route')).toBeNull()
  await click(button(question))
  expect(apiSend.mock.calls[1][2]).toEqual({ conversationId: 'conversation-1', text: question })
  expect(button(question).disabled).toBe(true)
  expect(host.textContent).toContain('지난달 비교예요.')
})

describe.each(['legacy', 'router'])('%s 엔진 메시지 복구', (engine) => {
  it('첫 메시지를 저장한 뒤 응답만 유실돼도 같은 요청으로 복구하고 다음 메시지만 새 키를 쓴다', async () => {
    useApi.mockReturnValue({ data: info(engine), error: null })
    const stored = new Map()
    apiSend.mockImplementation(async (_path, _method, body, { requestKey }) => {
      if (stored.has(requestKey)) return stored.get(requestKey)
      const response = reply(engine, stored.size + 1, body.text)
      stored.set(requestKey, response)
      if (stored.size === 1) throw new TypeError('Failed to fetch') // 서버 저장 성공, 응답 유실
      return response
    })
    await mount()
    await submit('이번 달 얼마 썼어?')
    expect(stored.size).toBe(1)
    expect(input().disabled).toBe(true)
    expect(host.querySelector('[aria-label="보내기"]').disabled).toBe(true)
    expect(button('소비를 분석해주세요').disabled).toBe(true)
    await click(button('다시 보내기'))
    expect(apiSend.mock.calls[1]).toEqual(apiSend.mock.calls[0])
    expect(apiSend.mock.calls[1][2].conversationId).toBeNull()
    expect(stored.size).toBe(1)
    expect(host.querySelectorAll('.mc-msg.me')).toHaveLength(1)
    expect(host.textContent).toContain('답변 1')
    expect(button('다시 보내기')).toBeUndefined()
    expect(input().disabled).toBe(false)
    await submit('그럼 교통비는?')
    expect(apiSend.mock.calls[2][2]).toEqual({ conversationId: 'conversation-1', text: '그럼 교통비는?' })
    expect(apiSend.mock.calls[2][3].requestKey).not.toBe(apiSend.mock.calls[0][3].requestKey)
    expect(stored.size).toBe(2)
    expect(host.querySelectorAll('.mc-msg.me')).toHaveLength(2)
  })

  it('기존 대화의 실패 메시지도 반복 재시도에서 대화 번호와 키를 유지한다', async () => {
    useApi.mockReturnValue({ data: info(engine), error: null })
    apiSend.mockResolvedValueOnce(reply(engine, 1, '첫 질문'))
      .mockRejectedValueOnce(new TypeError('연결 끊김'))
      .mockRejectedValueOnce(Object.assign(new Error('서버 오류'), { status: 502 }))
      .mockResolvedValueOnce(reply(engine, 2, '다음 질문'))
    await mount()
    await submit('첫 질문')
    await submit('다음 질문')
    await click(button('다시 보내기'))
    await click(button('다시 보내기'))
    expect(apiSend.mock.calls[2]).toEqual(apiSend.mock.calls[1])
    expect(apiSend.mock.calls[3]).toEqual(apiSend.mock.calls[1])
    expect(apiSend.mock.calls[3][2].conversationId).toBe('conversation-1')
    expect(newRequestKey).toHaveBeenCalledTimes(2)
    expect(host.querySelectorAll('.mc-msg.me')).toHaveLength(2)
  })
})

it('선택지 재시도도 value와 turn을 보존하고 복구 전에는 다른 선택지를 막는다', async () => {
  apiSend.mockResolvedValueOnce(reply('router', 1, '질문', {
    turn: 3, choices: [{ value: 'this_month', label: '이번 달로 보기' }],
  })).mockRejectedValueOnce(new TypeError('연결 끊김')).mockResolvedValueOnce(reply('router', 2, '이번 달로 보기'))
  await mount()
  await submit('질문')
  await click(button('이번 달로 보기'))
  expect(button('이번 달로 보기').disabled).toBe(true)
  await click(button('다시 보내기'))
  expect(apiSend.mock.calls[2]).toEqual(apiSend.mock.calls[1])
  expect(apiSend.mock.calls[2][2]).toEqual({ conversationId: 'conversation-1', choice: { value: 'this_month', turn: 3 } })
})

it('다시 그리기 전 연속 클릭도 요청을 하나만 보낸다', async () => {
  let finish
  apiSend.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  await mount()
  await act(() => {
    button('소비를 분석해주세요').click()
    button('소비를 분석해주세요').click()
  })
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(newRequestKey).toHaveBeenCalledTimes(1)
  await act(() => finish(reply('router', 1, '소비 분석')))
})

it.each([400, 403, 409])('명시적인 %i 거절은 입력을 복원해 고칠 수 있게 한다', async (status) => {
  apiSend.mockRejectedValueOnce(Object.assign(new Error('요청 거절'), { status }))
    .mockResolvedValueOnce(reply('router', 1, '수정한 질문'))
  await mount()
  await submit('원래 질문')
  expect(input().disabled).toBe(false)
  expect(input().value).toBe('원래 질문')
  expect(button('다시 보내기')).toBeUndefined()
  await submit('수정한 질문')
  expect(apiSend.mock.calls[1][3].requestKey).not.toBe(apiSend.mock.calls[0][3].requestKey)
})

it.each(['첫 화면', '대화 복원', '메시지 전송'].flatMap((stage) => [
  [stage, 403, 'not_authenticated', '인증 정보가 없습니다.', '로그인해 주세요.'],
  [stage, 401, undefined, '인증이 필요합니다.', '로그인해 주세요.'],
  [stage, 403, 'permission_denied', 'CSRF 확인에 실패했어요.', 'CSRF 확인에 실패했어요.'],
]))('%s의 %i %s 오류는 로그인 오류와 권한 오류를 구분한다', async (stage, status, code, message, expected) => {
  const error = Object.assign(new Error(message), { status, code })
  if (stage === '첫 화면') useApi.mockReturnValue({ data: null, error })
  else {
    apiSend.mockRejectedValueOnce(error)
    if (stage === '대화 복원') sessionStorage.setItem('mc-chat-conversation', 'conversation-1')
  }
  await mount()
  if (stage === '메시지 전송') await submit('내 소비 습관 어때?')
  expect(host.querySelector('[role="alert"]').textContent).toBe(expected)
  if (code === 'permission_denied') expect(host.textContent).not.toContain('로그인해 주세요.')
})

it.each([408, 500])('%i 응답은 성공 여부가 불명확해 같은 요청을 유지한다', async (status) => {
  apiSend.mockRejectedValueOnce(Object.assign(new Error('응답 실패'), { status }))
    .mockResolvedValueOnce(reply('router', 1, '질문'))
  await mount()
  await submit('질문')
  await click(button('다시 보내기'))
  expect(apiSend.mock.calls[1]).toEqual(apiSend.mock.calls[0])
})

it('성공 상태여도 응답 본문이 유실되면 같은 요청으로 복구한다', async () => {
  apiSend.mockResolvedValueOnce(null).mockResolvedValueOnce(reply('router', 1, '질문'))
  await mount()
  await submit('질문')
  await click(button('다시 보내기'))
  expect(apiSend.mock.calls[1]).toEqual(apiSend.mock.calls[0])
  expect(host.querySelectorAll('.mc-msg.me')).toHaveLength(1)
})


describe.each(['legacy', 'router'])('%s 엔진 페이지 이동·새로고침 복원과 재전송 통합', (engine) => {
  it('다른 페이지를 다녀와도 서버에서 이전 메시지를 복원하고 같은 대화를 이어간다', async () => {
    useApi.mockReturnValue({ data: info(engine), error: null })
    const saved = reply(engine, 1, '이전 질문')
    apiSend.mockResolvedValueOnce(saved).mockResolvedValueOnce(saved)
      .mockResolvedValueOnce(reply(engine, 2, '이어서 질문'))
    await act(() => root.render(<MemoryRouter initialEntries={['/chat']}><PopupProvider>
      <Link to="/other">다른 페이지로</Link><Link to="/chat">챗봇으로</Link>
      <Routes><Route path="/chat" element={<AssistantPage />} /><Route path="/other" element={<p>다른 화면</p>} /></Routes>
    </PopupProvider></MemoryRouter>))
    await submit('이전 질문')
    await click(host.querySelector('a[href="/other"]'))
    expect(host.textContent).toContain('다른 화면')
    expect(host.textContent).not.toContain('이전 질문')
    expect(sessionStorage.getItem('mc-chat-conversation')).toBe('conversation-1')
    await click(host.querySelector('a[href="/chat"]'))
    expect(apiSend.mock.calls[1]).toEqual(['/api/chat/conversations/conversation-1/', 'GET'])
    expect(host.textContent).toContain('이전 질문')
    expect(host.textContent).toContain('답변 1')
    await submit('이어서 질문')
    expect(apiSend.mock.calls[2][2]).toEqual({ conversationId: 'conversation-1', text: '이어서 질문' })
    expect(host.querySelectorAll('.mc-msg.me')).toHaveLength(2)
  })

  it('복원 중 전송을 막고 복원된 대화에서 실패한 요청은 같은 키로 재전송한다', async () => {
    sessionStorage.setItem('mc-chat-conversation', 'conversation-1')
    useApi.mockReturnValue({ data: info(engine), error: null })
    let finishRestore
    apiSend.mockImplementationOnce(() => new Promise((resolve) => { finishRestore = resolve }))
      .mockRejectedValueOnce(new TypeError('응답 유실'))
      .mockResolvedValueOnce(reply(engine, 2, '이어서 질문'))
    await mount()
    expect(apiSend.mock.calls[0]).toEqual(['/api/chat/conversations/conversation-1/', 'GET'])
    expect(input().disabled).toBe(true)
    expect(button('소비를 분석해주세요').disabled).toBe(true)
    await act(() => finishRestore(reply(engine, 1, '이전 질문')))
    expect(host.textContent).toContain('이전 질문')
    expect(input().disabled).toBe(false)
    await submit('이어서 질문')
    expect(apiSend.mock.calls[1][2].conversationId).toBe('conversation-1')
    await click(button('다시 보내기'))
    expect(apiSend.mock.calls[2]).toEqual(apiSend.mock.calls[1])
    expect(host.querySelectorAll('.mc-msg.me')).toHaveLength(2)
    expect(sessionStorage.getItem('mc-chat-conversation')).toBe('conversation-1')
    await act(() => root.render(null))
    expect(sessionStorage.getItem('mc-chat-conversation')).toBe('conversation-1')
  })

  it('복원 대상이 없으면 저장된 번호를 버리고 새 대화로 보낸다', async () => {
    sessionStorage.setItem('mc-chat-conversation', 'missing-conversation')
    useApi.mockReturnValue({ data: info(engine), error: null })
    apiSend.mockRejectedValueOnce(Object.assign(new Error('없는 대화'), { status: 404 }))
      .mockResolvedValueOnce(reply(engine, 1, '새 질문'))
    await mount()
    expect(sessionStorage.getItem('mc-chat-conversation')).toBeNull()
    await submit('새 질문')
    expect(apiSend.mock.calls[1][2]).toEqual({ conversationId: null, text: '새 질문' })
  })
})

it('메뉴로 이동했다 뒤로 돌아와도 서버에서 같은 대화와 답변을 복원한다', async () => {
  const previous = reply('router', 1, '추천 정책 알려줘')
  apiSend.mockResolvedValueOnce(previous).mockResolvedValueOnce(previous)
  function Navigation() {
    const navigate = useNavigate()
    return <><button onClick={() => navigate('/my')}>메뉴로 이동</button><button onClick={() => navigate(-1)}>뒤로 돌아가기</button></>
  }
  await act(() => root.render(<MemoryRouter initialEntries={['/chat']}><PopupProvider>
    <Navigation /><Routes><Route path="/chat" element={<AssistantPage />} /><Route path="/my" element={<p>마이페이지</p>} /></Routes>
  </PopupProvider></MemoryRouter>))
  await submit('추천 정책 알려줘')
  await click(button('메뉴로 이동'))
  expect(host.textContent).not.toContain('답변 1')
  expect(sessionStorage.getItem('mc-chat-conversation')).toBe('conversation-1')
  await click(button('뒤로 돌아가기'))
  expect(apiSend.mock.calls[1]).toEqual(['/api/chat/conversations/conversation-1/', 'GET'])
  expect(host.textContent).toContain('추천 정책 알려줘')
  expect(host.textContent).toContain('답변 1')
  expect(host.querySelectorAll('.mc-msg.me')).toHaveLength(1)
})

it('[새 대화]는 대화가 있을 때만 보이고, 누르면 화면을 비우고 다음 문장을 새 대화로 보낸다', async () => {
  apiSend.mockImplementation(async (_path, _method, body) => reply('router', apiSend.mock.calls.length, body.text))
  await mount()
  expect(button('새 대화')).toBeUndefined()
  expect(host.textContent).not.toContain('가짜 해석')
  await submit('이번 달 얼마 썼어?')
  expect(host.textContent).toContain('답변 1')
  expect(sessionStorage.getItem('mc-chat-conversation')).toBe('conversation-1')
  await click(button('새 대화'))
  expect(host.textContent).not.toContain('답변 1')
  expect(host.querySelectorAll('.mc-msg.me')).toHaveLength(0)
  expect(sessionStorage.getItem('mc-chat-conversation')).toBeNull()
  expect(button('새 대화')).toBeUndefined()
  await submit('그럼 교통비는?')
  expect(apiSend.mock.calls[0][2].conversationId).toBeNull()
  expect(apiSend.mock.calls[1][2].conversationId).toBeNull()
})

describe('일부 내역 답의 [내역 다시 불러오기] (11/13)', () => {
  const asked = '식비 얼마 남았어?'
  const partial = { result: true, agent: 'spending', lines: ['식비 예산 350,000원 중 60,000원을 썼고 290,000원 남았어요.'],
    notices: ['카드 1장은 11월 12일까지 불러온 내역이에요. 다시 불러오면 더 정확해져요.'],
    syncedUntil: [{ kind: 'card', name: '현대카드 · ****1234', until: '2026-11-12' }] }
  const fresh = { result: true, agent: 'spending', lines: ['새로 받은 답'] }
  const loaded = { items: [{ organizationName: '현대카드', status: 'succeeded', newTransactionCount: 2 }] }
  const resync = () => [...host.querySelectorAll('button')].find((b) => b.textContent.includes('내역 다시 불러오기'))
  const start = async () => {
    apiSend.mockResolvedValueOnce(reply('router', 1, asked, partial)).mockResolvedValueOnce(reply('router', 2, asked, fresh))
    await mount()
    await submit(asked)
  }

  it('누르면 불러오는 중을 보이고, 다 불러오면 같은 질문을 다시 보내 새 답을 보인다', async () => {
    let finish
    runFinanceSync.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    await start()
    expect(resync()).toBeDefined()
    await click(resync())
    expect(runFinanceSync).toHaveBeenCalledWith()  // 홈 새로고침과 같은 API (새 API 없음)
    expect(host.querySelector('[role="status"]').textContent).toContain('내역 불러오는 중…')
    expect(resync()).toBeUndefined()
    expect(input().disabled).toBe(true)
    await act(async () => finish(loaded))
    expect(apiSend.mock.calls[1][2]).toEqual({ conversationId: 'conversation-1', text: asked })
    expect(host.textContent).toContain('새로 받은 답')
    expect(resync()).toBeUndefined()  // 새 답엔 syncedUntil 이 없다
  })

  it('실패하면 그 자리에 안내 + [다시 시도], 다시 시도가 되면 같은 질문을 보낸다', async () => {
    runFinanceSync.mockRejectedValueOnce(new Error('서버 오류')).mockResolvedValueOnce(loaded)
    await start()
    await click(resync())
    expect(host.querySelector('[role="alert"]').textContent).toContain('불러오지 못했어요')
    expect(apiSend).toHaveBeenCalledTimes(1)
    expect(input().disabled).toBe(false)
    await click(button('다시 시도'))
    expect(runFinanceSync).toHaveBeenCalledTimes(2)
    expect(apiSend.mock.calls[1][2]).toEqual({ conversationId: 'conversation-1', text: asked })
    expect(host.textContent).toContain('새로 받은 답')
  })

  it('카드번호가 필요한 카드사는 입력 팝업부터, 그냥 닫으면 다시 묻지 않고 실패로', async () => {
    runFinanceSync.mockResolvedValueOnce({ items: [{ connectionId: 'conn-1', organizationCode: '0302', organizationName: '현대카드',
      status: 'failed', error: { code: 'invalid', message: '카드 값', fields: { cardNo: ['카드번호를 넣어 주세요.'] } } }] })
    await start()
    await act(async () => resync().click())
    expect(document.body.textContent).toContain('카드 정보를 넣어 주세요')
    await click(button('닫기 ×'))
    expect(host.querySelector('[role="alert"]').textContent).toContain('불러오지 못했어요')
    expect(apiSend).toHaveBeenCalledTimes(1)
  })
})
