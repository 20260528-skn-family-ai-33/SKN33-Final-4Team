import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
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

it('금액과 카테고리를 먼저 보여주고 설명은 카드 하단의 닫힌 안내로 옮긴다', async () => {
  await render({ ...month, lead: ['먼저 이번 달 전체 소비를 볼게요.'],
    view: [{ kind: 'head', title: '11월 소비' }, { kind: 'total', amount: 437369, note: '일부 내역 기준' }],
    chart: { basis: '카테고리 금액 ÷ 전체 소비', limited: '빠진 내역에 따라 비율이 달라질 수 있어요.', rows: [
      { code: 'FOOD', name: '식비', amount: 172600, share: 39.46, text: '식비 172,600원' },
    ] }, notices: ['먼저 이번 달 전체 소비를 볼게요.', '수집된 내역만 반영했어요.'] })
  const card = host.querySelector('.mc-spend')
  const notes = card.querySelector('.mc-fx-notes')
  expect(card.lastElementChild).toBe(notes)
  expect(notes.open).toBe(false)
  expect(notes.querySelector('summary').textContent).toBe('분석 기준·안내')
  expect(notes.textContent).toContain('카테고리 금액 ÷ 전체 소비')
  expect(notes.textContent).toContain('빠진 내역에 따라 비율이 달라질 수 있어요.')
  expect(notes.textContent.match(/먼저 이번 달 전체 소비를 볼게요./g)).toHaveLength(1)
  expect(card.querySelector('.mc-spend-total').textContent).toContain('일부 내역 기준')
  expect(card.querySelector('.mc-fx-chart').textContent).toContain('식비')
  expect(card.querySelector('.mc-fx-chart').textContent).not.toContain('카테고리 금액 ÷ 전체 소비')
  expect(card.querySelector('.mc-fx-lead')).toBeNull()
})

it('카드 보기 없는 분석도 본문 아래에 안내를 모으고 질문 말풍선은 그대로 둔다', async () => {
  const content = { result: true, lines: ['식비는 30,000원이에요.'], lead: ['비교 범위 안내'], notices: [] }
  await render(content)
  expect(host.querySelector('.mc-spend').lastElementChild.className).toBe('mc-fx-notes')
  expect(host.querySelector('.mc-spend-lines').textContent).toBe('식비는 30,000원이에요.')
  await render({ ...content, result: false })
  expect(host.querySelector('.mc-bubble').textContent).toContain('비교 범위 안내')
  expect(host.querySelector('.mc-fx-notes')).toBeNull()
})

it('확인 필요는 새 응답과 이전 저장 응답 모두 주요 카테고리에서 빼고 하단에 표시한다', async () => {
  const row = { code: 'FOOD', name: '식비', amount: 111500, share: '27.33' }
  for (const chart of [
    { rows: [row], unclassified: '확인 필요 172,600원은 총액에 포함하고 주요 카테고리에서 제외했어요.' },
    { rows: [{ code: 'UNCLASSIFIED', name: '확인 필요', amount: 172600, share: '42.31' }, row] },
  ]) {
    await render({ ...month, chart })
    expect(host.querySelector('.mc-fx-chart').textContent).not.toContain('확인 필요')
    expect(host.querySelector('.mc-fx-chart').textContent).toContain('27.33%')
    expect(host.querySelector('.mc-fx-notes').textContent).toContain('확인 필요 172,600원은 총액에 포함')
    expect(host.querySelector('.mc-fx-notes').hasAttribute('open')).toBe(false)
  }
})

it('분석 소제목은 목록 항목 대신 제목으로 표시하고 중복하지 않는다', async () => {
  await render({ ...month, view: [{ kind: 'head', title: '소비 분석' },
    { kind: 'text', title: '큰 결제와 반복 소비', lines: ['큰 결제와 반복 소비', '식비 결제 금액이 늘었어요.'] }] })
  expect(host.querySelector('h3').textContent).toBe('큰 결제와 반복 소비')
  expect([...host.querySelectorAll('li')].map((el) => el.textContent)).toEqual(['식비 결제 금액이 늘었어요.'])
  expect(host.textContent.match(/큰 결제와 반복 소비/g)).toHaveLength(1)
})

it('이전에 저장된 첫 줄 소제목도 강조하고 일반 본문은 유지한다', async () => {
  await render({ ...month, view: [{ kind: 'head', title: '소비 분석' },
    { kind: 'text', lines: ['생활 패턴', '점심 외식의 건당 부담이 커졌어요.'] },
    { kind: 'text', lines: ['내역을 더 확인할 수 있어요.'] }] })
  expect(host.querySelector('h3').textContent).toBe('생활 패턴')
  expect(host.querySelectorAll('h3')).toHaveLength(1)
  expect(host.textContent).toContain('내역을 더 확인할 수 있어요.')
})

it('관측된 주별 금액을 선 그래프와 접근 가능한 표로 표시한다', async () => {
  const points = [84000, 98000, 112000, 126000].map((amount, i) => ({ label: `10/${2 + 7 * i}~10/${8 + 7 * i}`, amount }))
  await render({ ...month, view: [{ kind: 'head', title: '소비 분석' },
    { kind: 'trend', title: '최근 4주 소비 추이', basis: '관측된 결제 금액', series: [
      { code: 'FOOD', name: '식비', points },
      { code: 'TRANSPORT_CAR', name: '교통·차량', points: points.map((p) => ({ ...p, amount: 10000 })) },
    ] }] })
  expect(host.querySelector('svg[role="img"]').getAttribute('aria-label')).toBe('최근 4주 소비 추이')
  expect(host.querySelectorAll('polyline')).toHaveLength(2)
  expect(host.querySelectorAll('circle')).toHaveLength(8)
  expect(host.querySelectorAll('polyline')[1].getAttribute('stroke-dasharray')).toBe('6 4')
  expect(host.querySelector('desc').textContent).toContain('10/2~10/8 84,000원')
  expect(host.querySelector('table').textContent).toContain('126,000원')
  expect(host.querySelectorAll('tbody tr')).toHaveLength(4)
  expect(host.querySelector('summary').textContent).toBe('주별 금액 보기')
  expect(host.innerHTML).not.toMatch(/NaN|Infinity/)
  expect(host.textContent).not.toContain('월말 예상')
})

it.each([true, false])('분석 결과와 안내 답변에서 예시를 누르면 질문을 전송한다 (결과=%s)', async (result) => {
  const onSend = vi.fn()
  const question = '지난달보다 많이 썼어?'
  await act(() => root.render(<ChatReply content={{ ...month, result, examples: [question] }} title="소비 분석" active onSend={onSend} />))
  expect(host.textContent).toContain('이런 질문으로 이어서 볼 수 있어요.')
  await act(() => button(question).click())
  expect(onSend).toHaveBeenCalledExactlyOnceWith(question)
})

it('이전 답변의 예시와 전송할 수 없는 예시는 비활성화한다', async () => {
  const onSend = vi.fn()
  const question = '이번 달 전체 소비 분석해줘'
  for (const props of [{ active: false, onSend }, { active: true }]) {
    await act(() => root.render(<ChatReply content={{ ...month, examples: [question] }} title="소비 분석" {...props} />))
    expect(button(question).disabled).toBe(true)
    await act(() => button(question).click())
  }
  expect(onSend).not.toHaveBeenCalled()
})

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
