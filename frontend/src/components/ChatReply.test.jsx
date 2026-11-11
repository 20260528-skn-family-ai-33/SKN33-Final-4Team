import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import ChatReply from './ChatReply.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
let host, root
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(async () => { await act(() => root.unmount()); host.remove() })
const button = (text) => [...host.querySelectorAll('button')].find((el) => el.textContent === text)
const view = (title, amount) => [{ kind: 'head', title }, { kind: 'total', amount, note: null }]
const month = { result: true, lines: [], lead: [], notices: ['이번 달 안내'], view: view('11월 1일~11일 · 소비', 30000) }
const alt = { label: '예산 주기', baseLabel: '이번 달', lines: [], notices: ['주기 안내'], view: view('10월 25일~11월 11일 · 소비', 100000) }
const render = (content) => act(() => root.render(<ChatReply content={content} title="소비 분석" active onPick={() => {}} />))

it('예산 주기 판이 없으면 기간 전환 버튼을 그리지 않는다', async () => {
  await render(month)
  expect(host.querySelector('[aria-label="기간 기준"]')).toBeNull()
  expect(host.textContent).toContain('30,000원')
})

it('[이번 달 | 예산 주기]로 서버가 준 두 판을 바꿔 보여 준다', async () => {
  await render({ ...month, alt })
  expect(button('이번 달').getAttribute('aria-pressed')).toBe('true')
  expect(host.textContent).toContain('11월 1일~11일 · 소비')
  expect(host.textContent).toContain('30,000원')
  expect(host.textContent).toContain('이번 달 안내')
  await act(() => button('예산 주기').click())
  expect(button('예산 주기').getAttribute('aria-pressed')).toBe('true')
  expect(host.textContent).toContain('10월 25일~11월 11일 · 소비')
  expect(host.textContent).toContain('100,000원')
  expect(host.textContent).toContain('주기 안내')
  expect(host.textContent).not.toContain('30,000원')
  expect(host.textContent).not.toContain('이번 달 안내')
  await act(() => button('이번 달').click())
  expect(host.textContent).toContain('30,000원')
})

it('예산 조정 중에 물은 답(alt.first)은 예산 주기를 먼저 보여 준다', async () => {
  await render({ ...month, alt: { ...alt, first: true } })
  expect(button('예산 주기').getAttribute('aria-pressed')).toBe('true')
  expect(host.textContent).toContain('100,000원')
})
