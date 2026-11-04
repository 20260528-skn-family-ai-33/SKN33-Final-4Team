import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import RefreshRow from './RefreshRow.jsx'
import { apiSend } from '../utils/api.js'

vi.mock('../utils/api.js', () => ({ apiSend: vi.fn() }))

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
const task = (status, error = null) => ({ status, result: { items: [{ organizationName: '신한은행', status, error }] } })
const answer = (body) => vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => body })))
const click = () => act(async () => host.querySelector('button').click())

beforeEach(() => {
  vi.clearAllMocks()
  apiSend.mockResolvedValue({ taskId: 't1' })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

it('그 연결만 불러오고 10분 안에 다시 누르면 요청 없이 안내한다', async () => {
  answer(task('succeeded'))
  await act(() => root.render(<RefreshRow connectionId="c1" />))
  await click()
  expect(apiSend).toHaveBeenCalledWith('/api/finance/sync/', 'POST', { connectionIds: ['c1'] })
  expect(host.textContent).toContain('방금 새로고침했어요')

  await click()
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('10분 뒤에 다시 할 수 있어요')
})

it('기관이 실패하면 이유를 보여 주고 제한을 걸지 않는다', async () => {
  answer({ status: 'succeeded', result: { items: [{ organizationName: '신한은행', status: 'failed', error: { message: '기관 점검 중이에요.' } }] } })
  await act(() => root.render(<RefreshRow connectionId="c2" />))
  await click()
  expect(host.textContent).toContain('기관 점검 중이에요.')

  await click()
  expect(apiSend).toHaveBeenCalledTimes(2)
})

it('요청이 막히면 서버 문장을 그대로 보여 준다', async () => {
  apiSend.mockRejectedValue(Object.assign(new Error('테스트 환경에서는 쓸 수 없어요.'), { status: 503 }))
  await act(() => root.render(<RefreshRow connectionId="c3" />))
  await click()
  expect(host.textContent).toContain('테스트 환경에서는 쓸 수 없어요.')
})

it('입력 오류는 칸 오류 문장을, 서버에 못 닿으면 안내 문장을 보여 준다', async () => {
  apiSend.mockRejectedValueOnce(Object.assign(new Error('입력값을 확인해 주세요.'), { status: 400, fields: { nonFieldErrors: ['연결된 기관이 없어요.'] } }))
  await act(() => root.render(<RefreshRow connectionId="c4" />))
  await click()
  expect(host.textContent).toContain('연결된 기관이 없어요.')

  apiSend.mockRejectedValueOnce(new TypeError('Failed to fetch'))
  await click()
  expect(host.textContent).toContain('서버에 연결하지 못했어요.')
})
