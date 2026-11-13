import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import ChatReply, { Choices } from './ChatReply.jsx'

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

const rows = () => [...host.querySelectorAll('.mc-policy-table > div')].map((row) => [row.querySelector('dt').textContent, row.querySelector('dd').textContent])
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
    expect(link.textContent).toContain('공고 보기')
    expect(link.target).toBe('_blank')
    expect(link.rel.split(' ')).toEqual(expect.arrayContaining(['noopener', 'noreferrer']))
    expect(link.getAttribute('aria-label')).toContain('새 탭에서 열기')
    expect(rows()).toEqual([['지원', '테스트 안내']])
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
  expect(rows()).toEqual([['링크', value]])
})

it('라벨 없는 경고 줄은 표 밖 안내로, 값 속 쌍점은 값에 남긴다', async () => {
  await render(policyCard(['곧 나이 상한을 넘어요. 신청 기간을 서둘러 확인해 보세요.', '신청: 상시: 예산 소진 시 마감']))
  expect(host.querySelector('.mc-policy-note').textContent).toContain('곧 나이 상한')
  expect(rows()).toEqual([['신청', '상시: 예산 소진 시 마감']])
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

it('질문 아래 예시 문장(examples)을 회색 한 줄로 보인다', async () => {
  await render({ agent: 'policy', lines: ['어떤 고민이 있으세요?'], examples: ['월세가 부담돼요', '돈을 모으고 싶어요'] })
  expect(host.querySelector('.mc-examples').textContent).toBe('예) 월세가 부담돼요 · 돈을 모으고 싶어요')
})

it('결과 카드(분야 안내)도 안내 문장 아래 회색 예시 줄을 보인다', async () => {
  await render({ agent: 'policy', result: true, lines: ['이런 분야를 찾을 수 있어요.'], examples: ['월세 지원 있을까요?'],
    sections: [{ title: '찾을 수 있는 분야', lines: ['주거: 월세 지원'] }] })
  expect(host.querySelector('.mc-examples').textContent).toBe('예) 월세 지원 있을까요?')
  expect(host.querySelector('.mc-policy-table dd').textContent).toBe('월세 지원')
})

it('청년 정책 확인 질문은 줄을 나누고, 찾을 정책 · 지역을 작은 상자로 보인다', async () => {
  await render({ agent: 'policy', lines: ['`월세 지원` 정책을 찾아볼게요.', '가입 지역인 `경기도 화성시 동탄구`로 찾으시는 거 맞나요?'],
    choices: [{ value: 'yes', label: '예' }, { value: 'no', label: '아니요' }] })
  expect(host.querySelector('.mc-bubble strong')).toBeNull()
  expect(host.querySelectorAll('.mc-bubble br')).toHaveLength(1)
  expect([...host.querySelectorAll('.mc-pill')].map((p) => p.textContent)).toEqual(['월세 지원', '경기도 화성시 동탄구'])
  expect(host.querySelector('.mc-bubble').textContent).not.toContain('`')
})

it('청년 정책 결과 카드 위 문장의 지역도 파란 강조로 보인다 (다른 담당은 그대로)', async () => {
  const card = { result: true, lines: ['`경기도 화성시` 기준으로 찾은 청년 정책이에요.'], sections: [{ title: '1. 정책', lines: ['지원: 월 20만 원'] }] }
  await render({ ...card, agent: 'policy' })
  expect(host.querySelector('.mc-pill').textContent).toBe('경기도 화성시')
  expect(host.textContent).not.toContain('`')
  await render({ ...card, agent: 'spending' })
  expect(host.querySelector('.mc-pill')).toBeNull()
})

it('scroll 이면 선택지를 한 줄 가로 스크롤로 보인다 (기본은 줄바꿈)', async () => {
  const choices = [{ value: 'more', label: '다른 정책 더 보기' }, { value: 'region', label: '지역 변경하기' }]
  await act(() => root.render(<Choices choices={choices} active onPick={() => {}} scroll />))
  expect(host.querySelector('.mc-fx-choices').classList.contains('is-scroll')).toBe(true)
  await act(() => root.render(<Choices choices={choices} active onPick={() => {}} />))
  expect(host.querySelector('.mc-fx-choices').classList.contains('is-scroll')).toBe(false)
})
