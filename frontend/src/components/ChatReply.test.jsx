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

const policyCard = (lines, extra = {}) => ({
  agent: 'policy', result: true, lines: [],
  sections: [{ title: '테스트 정책', lines }], ...extra,
})

it.each(['청년 정책 추천', '공고 원문에 적힌 내용이에요.', '저장한 정책이에요.'])(
  '%s 카드의 원문 URL을 새 탭 링크로 표시한다', async (heading) => {
    const url = 'https://example.org/policy?id=123&from=youth#apply'
    await render(policyCard(['지원: 테스트 안내', `링크: ${url}`], { lines: [heading] }))
    const link = host.querySelector('a')
    expect(link.getAttribute('href')).toBe(url)
    expect(link.textContent).toBe(url)
    expect(link.target).toBe('_blank')
    expect(link.rel.split(' ')).toEqual(expect.arrayContaining(['noopener', 'noreferrer']))
    expect(link.getAttribute('aria-label')).toContain('새 탭에서 열기')
    expect(host.textContent).toContain('지원: 테스트 안내')
    expect(host.textContent).toContain(`링크: ${url}`)
  },
)

it('HTTP 주소와 괄호가 있는 경로도 원문 그대로 연결한다', async () => {
  const url = 'http://example.org/policy/(youth)?name=%EC%B2%AD%EB%85%84'
  await render(policyCard([`링크: ${url}`]))
  expect(host.querySelector('a').getAttribute('href')).toBe(url)
})

it.each([
  '정보 없음 (온통청년에서 정책명으로 찾아 주세요)',
  '', 'javascript:alert(1)', 'data:text/html,<script>alert(1)</script>',
  '//example.org/policy', 'https://', 'https://[invalid',
  'https://example.org/ 공고 안내', 'https://example.org/<img src=x onerror=alert(1)>',
])('유효한 HTTP(S) 주소가 아닌 링크 줄은 글자로 남긴다: %s', async (value) => {
  await render(policyCard([`링크: ${value}`]))
  expect(host.querySelector('a')).toBeNull()
  expect(host.querySelector('img, script')).toBeNull()
  expect(host.textContent).toContain(`링크: ${value}`)
})

it('정책의 일반 문장과 다른 담당·예전 담당 없는 카드는 링크로 해석하지 않는다', async () => {
  const line = '링크: https://example.org/policy'
  await render(policyCard(['설명: https://example.org/policy']))
  expect(host.querySelector('a')).toBeNull()
  for (const agent of ['spending', 'budget', undefined]) {
    await render(policyCard([line], { agent }))
    expect(host.querySelector('a')).toBeNull()
    expect(host.textContent).toContain(line)
  }
})
