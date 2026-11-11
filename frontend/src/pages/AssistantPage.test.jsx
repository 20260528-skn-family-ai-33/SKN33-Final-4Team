import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssistantPage from './AssistantPage.jsx'
import PopupProvider from '../popups/PopupProvider.jsx'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'

vi.mock('../utils/api.js', () => ({ apiSend: vi.fn(), newRequestKey: vi.fn(), useApi: vi.fn() }))
vi.mock('../components/Layout.jsx', () => ({ default: ({ children, foot }) => <main>{children}{foot}</main> }))
vi.mock('../components/Icon.jsx', () => ({ default: () => <span /> }))

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
})
const mount = async () => act(() => root.render(<MemoryRouter><PopupProvider><AssistantPage /></PopupProvider></MemoryRouter>))
const button = (label) => [...host.querySelectorAll('button')].find((b) => b.textContent === label)
const click = async (element) => act(() => element.click())
const input = () => host.querySelector('input[aria-label="챗봇에게 보낼 메시지"]')
async function submit(text) {
  await act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input(), text)
    input().dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(() => input().form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}

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


describe.each(['legacy', 'router'])('%s 엔진 새로고침 복원과 재전송 통합', (engine) => {
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
    expect(sessionStorage.getItem('mc-chat-conversation')).toBeNull()
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
