// useApi(R29) · ApiStatus(R30) · apiSend — fetch 대역으로 응답 순서 · 지연 · 오류를 직접 정한다
import { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { apiSend, useApi } from './api.js'
import ApiStatus from '../components/ApiStatus.jsx'

const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
const htmlReply = (status) => ({ ok: false, status, json: async () => { throw new SyntaxError('<html>Traceback…') } })
let host, root, pending, calls
// 주소마다 응답을 나중에 손으로 보낸다: answer('/a', reply(...))
const answer = (url, res) => act(async () => { pending.get(url).shift()(res) })
const fail = (url, err) => act(async () => { pending.get(url).shift()(Promise.reject(err)) })
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  pending = new Map()
  calls = []
  vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
    calls.push([url, options.method ?? 'GET'])
    if (url === '/api/csrf/') return Promise.resolve(reply({}))
    return new Promise((resolve) => { pending.set(url, [...(pending.get(url) ?? []), resolve]) })
  }))
  document.cookie = 'csrftoken=test; path=/'
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })

function Probe({ path, keepData }) {
  const [memo, setMemo] = useState('')  // 화면의 입력 상태 — 다시 받기로 지워지면 안 된다
  const { data, error, loading, refreshing, reload } = useApi(path, { keepData })
  return <div>
    <button type="button" id="reload" onClick={reload}>reload</button>
    <input value={memo} onChange={(e) => setMemo(e.target.value)} />
    <p id="data">{data ? data.label : '-'}</p>
    <p id="flags">{`${loading ? 'loading' : ''}${refreshing ? 'refreshing' : ''}`}</p>
    {(!data || error) && <ApiStatus error={refreshing ? null : error} onRetry={reload} />}
  </div>
}
const show = (path, keepData) => act(async () => root.render(<MemoryRouter><Probe path={path} keepData={keepData} /></MemoryRouter>))
const text = (id) => host.querySelector(`#${id}`).textContent

it('필터를 바꾸면 이전 주소의 결과를 바로 숨기고, 늦게 온 이전 응답 · 오류는 버린다', async () => {
  await show('/a')
  await answer('/a', reply({ label: 'A' }))
  expect(text('data')).toBe('A')

  await show('/b')
  expect(text('data')).toBe('-')            // A 를 B 결과처럼 보이지 않는다
  expect(text('flags')).toBe('loading')
  await show('/c')
  await show('/d')
  await answer('/d', reply({ label: 'D' }))
  await answer('/c', reply({ label: 'C' }))  // 역순으로 늦게 도착
  await fail('/b', new Error('늦은 오류'))
  expect(text('data')).toBe('D')
  expect(host.textContent).not.toContain('못 불러왔어요')
})

it('A 성공 → B 대기 → A 복귀면 예전 A 결과를 다시 쓰지 않고 새로 받는다', async () => {
  await show('/a')
  await answer('/a', reply({ label: 'A-old' }))
  await show('/b')
  await show('/a')
  expect(text('data')).toBe('-')
  expect(text('flags')).toBe('loading')
  await answer('/b', reply({ label: 'B' }))   // 늦은 B 는 버린다
  expect(text('data')).toBe('-')
  await answer('/a', reply({ label: 'A-new' }))
  expect(text('data')).toBe('A-new')
})

it('취소된 요청은 오류로 보이지 않는다', async () => {
  await show('/a')
  await show('/b')
  await fail('/a', Object.assign(new Error('aborted'), { name: 'AbortError' }))
  expect(host.textContent).toContain('불러오는 중')
  await answer('/b', reply({ label: 'B' }))
  expect(text('data')).toBe('B')
})

it('새 필터 조회가 실패하면 안내하고, 다시 시도로 받는다', async () => {
  await show('/a')
  await answer('/a', reply({ error: { code: 'server_error', message: '내부 오류' } }, 500))
  expect(host.textContent).toContain('일시적인 문제일 수 있어요')
  await act(async () => host.querySelector('[role=alert] button').click())
  expect(host.textContent).toContain('불러오는 중')
  await answer('/a', reply({ label: 'A' }))
  expect(text('data')).toBe('A')
})

it('기본: reload 실패면 data 를 비워 if (!data) 화면이 오류 카드를 띄운다', async () => {
  await show('/a')
  await answer('/a', reply({ label: 'A' }))
  await act(async () => host.querySelector('#reload').click())
  expect(text('data')).toBe('A')            // 받는 동안은 그대로
  await answer('/a', htmlReply(502))
  expect(text('data')).toBe('-')
  expect(host.textContent).toContain('데이터를 못 불러왔어요')
})

it('keepData: 같은 주소 reload 는 data · 입력을 그대로 두고, 실패도 숨기지 않는다', async () => {
  await show('/a', true)
  await answer('/a', reply({ label: 'A' }))
  const input = host.querySelector('input')
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '메모')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => host.querySelector('#reload').click())
  expect(text('data')).toBe('A')
  expect(text('flags')).toBe('refreshing')
  await answer('/a', htmlReply(502))
  expect(text('data')).toBe('A')
  expect(host.querySelector('input').value).toBe('메모')
  expect(host.textContent).toContain('데이터를 못 불러왔어요')
  expect(host.textContent).not.toContain('Traceback')
})

it.each([
  [reply({ error: { code: 'not_authenticated', message: '로그인이 필요해요.' } }, 403), '로그인이 필요해요', '로그인하기'],
  [reply({ error: { code: 'permission_denied', message: 'CSRF 확인에 실패했어요.' } }, 403), 'CSRF 확인에 실패했어요.', null],
  [reply({ error: { code: 'consent_required', message: '동의 필요' } }, 403), '조회 동의가 필요해요', '금융 연결로 가기'],
  [reply({ error: { code: 'not_found', message: '없음' } }, 404), '찾을 수 없어요', null],
  [reply({ error: { code: 'not_ready', message: '급여를 먼저 입력해 주세요.' } }, 409), '급여를 먼저 입력해 주세요.', '다시 시도'],
  [reply(null, 429), '요청이 많아요', '다시 시도'],
  [htmlReply(500), '일시적인 문제일 수 있어요', '다시 시도'],
])('오류 안내: %#', async (res, words, action) => {
  await show('/a')
  await answer('/a', res)
  expect(host.textContent).toContain(words)
  expect(host.textContent).not.toContain('runserver')
  if (action) expect(host.querySelector('[role=alert] a, [role=alert] button').textContent).toBe(action)
  else expect(host.querySelector('[role=alert] a, [role=alert] button')).toBeNull()
  // 일반 403 은 로그인 안내가 아니다
  if (res.status === 403 && !words.includes('로그인')) expect(host.textContent).not.toContain('로그인')
})

it('네트워크 오류는 일시 실패로 안내한다', async () => {
  await show('/a')
  await fail('/a', new TypeError('Failed to fetch'))
  expect(host.textContent).toContain('일시적인 문제일 수 있어요')
  expect(host.textContent).not.toContain('Failed to fetch')
})

it('apiSend 는 칸 오류 · 코드를 그대로 던진다', async () => {
  const sent = apiSend('/api/x/', 'POST', { a: 1 }).catch((e) => e)
  await vi.waitFor(() => expect(pending.get('/api/x/')).toBeTruthy())
  await answer('/api/x/', reply({ error: { code: 'invalid', message: '입력값을 확인해 주세요.', fields: { a: ['틀림'] } } }, 400))
  const err = await sent
  expect([err.status, err.code, err.message, err.fields]).toEqual([400, 'invalid', '입력값을 확인해 주세요.', { a: ['틀림'] }])
  expect(calls.filter(([, m]) => m === 'POST')).toHaveLength(1)
})
