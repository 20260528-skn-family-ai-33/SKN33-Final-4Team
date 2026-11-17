import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
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

it('저장된 옛 rows 응답도 확인 필요를 순위에서 빼고 하단에 보존한다', async () => {
  await render({ ...month, view: [
    { kind: 'head', title: '소비 분석' },
    { kind: 'rows', rows: [{ name: '확인 필요', amount: 172600, ratio: 39.46 }, { name: '식비', amount: 111500, ratio: 25.49 }] },
  ] })
  expect(host.querySelector('.mc-spend-cats').textContent).not.toContain('확인 필요')
  expect(host.querySelector('.mc-spend-cats').textContent).toContain('식비')
  const notes = host.querySelector('.mc-fx-notes')
  expect(notes.open).toBe(false)
  expect(notes.textContent).toContain('172,600원')
})

it('습관 해석과 제안은 소제목으로 구분하고 금액 합계 카드로 바꾸지 않는다', async () => {
  await render({ result: true, lines: [], notices: ['일부 내역만 수집했어요.'], view: [
    { kind: 'head', title: '소비 분석 · 일부 내역 기준' },
    { kind: 'text', title: '핵심 진단', lines: ['핵심 진단', '평일 점심에 외식 결제가 반복되는 패턴이 보여요.'] },
    { kind: 'text', title: '줄이고 싶다면', lines: ['줄이고 싶다면', '주 1회는 도시락으로 바꿔보세요.'] },
  ] })
  expect([...host.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(['핵심 진단', '줄이고 싶다면'])
  expect(host.querySelector('.mc-spend-total')).toBeNull()
  expect(host.querySelector('.mc-spend').lastElementChild.className).toBe('mc-fx-notes')
})

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
  expect(host.querySelector('.mc-examples')).toBeNull()
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

it('소비 분석 답은 핵심 진단을 먼저 강조하고 예산 속도는 막대와 글자로 함께 보여 준다', async () => {
  await render({ result: true, lines: [], notices: ['분석 범위: 생활비'], view: [
    { kind: 'head', title: '11월 1일~20일 · 소비 분석' },
    { kind: 'text', title: '핵심 진단', tone: 'key', lines: ['핵심 진단', '식비는 주기의 67%가 지났는데 예산의 90%를 썼어요.'] },
    { kind: 'text', title: '예산 속도', lines: ['예산 속도', '식비 예산 520,000원 중 470,000원을 썼어요.'] },
    { kind: 'pace', title: '예산 사용', elapsed: 67, caption: '예산 주기 20일째 · 67% 지남', rows: [
      { code: 'FOOD', name: '식비', spent: 470000, allocated: 520000, ratio: 90, state: 'fast' },
      { code: 'BEAUTY', name: '뷰티·미용', spent: 65000, allocated: 50000, ratio: 130, state: 'over' },
      { code: 'TRANSPORT_CAR', name: '교통·차량', spent: 10000, allocated: 100000, ratio: 10, state: 'slack' },
      { code: 'HOUSEHOLD', name: '생활용품', spent: 30000, allocated: 70000, ratio: 43, state: 'ok' },
      { code: 'EDUCATION', name: '교육', spent: 10000, allocated: 50000, ratio: 20, state: 'ok' },
    ] },
  ] })
  const key = host.querySelector('.mc-fx-key')
  expect(key.querySelector('h3').textContent).toBe('핵심 진단')
  expect(key.querySelector('p').textContent).toContain('예산의 90%')
  expect(key.querySelector('li')).toBeNull()
  const rows = [...host.querySelectorAll('.mc-fx-pace > .mc-fx-pace-row')]
  expect(rows.map((r) => r.querySelector('.mc-fx-pace-name').textContent)).toEqual(['식비빠름', '뷰티·미용넘음', '교통·차량여유', '생활용품'])
  expect(rows[0].getAttribute('aria-label')).toBe('식비 예산 520,000원 중 470,000원 사용 (90%), 빠름')
  expect(rows[1].querySelector('.mc-fx-pace-bar > span').style.width).toBe('100%')  // 넘어도 막대는 100%까지, 글자는 130%
  expect(rows[1].querySelector('.mc-fx-pace-amt').textContent).toContain('130%')
  expect(rows[0].querySelector('.mc-fx-pace-bar > i').style.left).toBe('67%')
  expect(host.querySelector('.mc-fx-pace details summary').textContent).toBe('나머지 1개 더 보기')
  expect(host.querySelector('.mc-spend').lastElementChild.className).toBe('mc-fx-notes')
})

it('월별 생활비는 실제 · 이번 달(쓴 돈+예상) · 다음 달 예상을 구분하고 예산선과 이어 물을 말을 함께 보여 준다', async () => {
  const onSend = vi.fn()
  await act(() => root.render(<ChatReply content={{ result: true, lines: [], notices: [], examples: ['지난달이랑 비교해줘'], view: [
    { kind: 'head', title: '11월 1일~5일 · 소비 분석' },
    { kind: 'text', title: '핵심 진단', tone: 'key', lines: ['핵심 진단', '지난 4개월 한 달 생활비는 93만 원~106만 원으로 꾸준했어요.'] },
    { kind: 'text', title: '소비 추이', lines: ['소비 추이', '7월 106만 원 → 10월 93만 원'] },
    { kind: 'months', title: '월별 생활비', budget: 1050000, basis: '생활비 카테고리 결제 기준', bars: [
      { label: '9월', actual: 1038300, projected: 0, kind: 'actual' },
      { label: '10월', actual: 930000, projected: 0, kind: 'actual' },
      { label: '11월', actual: 246000, projected: 731000, kind: 'current' },
      { label: '12월', actual: 0, projected: 994000, kind: 'next' },
    ] },
  ] }} title="소비 분석" active onPick={() => {}} onSend={onSend} />))
  const svg = host.querySelector('.mc-fx-months svg')
  expect(svg.getAttribute('aria-label')).toContain('11월 쓴 돈 246,000원 + 예상 731,000원')
  expect(svg.getAttribute('aria-label')).toContain('12월 예상 994,000원')
  expect(svg.getAttribute('aria-label')).toContain('예산 1,050,000원')
  expect(svg.querySelectorAll('.mc-fx-bar-actual')).toHaveLength(3)     // 9월 · 10월 · 11월의 쓴 돈
  expect(svg.querySelectorAll('.mc-fx-bar-projected')).toHaveLength(2)  // 11월 남은 기간 · 12월
  expect(svg.querySelector('.mc-fx-months-budget')).not.toBeNull()
  expect([...svg.querySelectorAll('text.is-future')].map((t) => t.textContent)).toEqual(['11월', '12월'])
  expect(host.querySelector('.mc-fx-months tbody').textContent).toContain('977,000원')
  expect(host.querySelector('.mc-fx-months-legend').textContent).toContain('예산 105만')
})

it('분석 메뉴는 분석마다 설명을 보여 주고, 누르면 그 질문을 보낸다 (지난 답은 설명만)', async () => {
  const onSend = vi.fn()
  const content = { result: true, lines: [], notices: [], examples: [], view: [
    { kind: 'head', title: '소비 분석 · 볼 수 있는 분석' },
    { kind: 'options', items: [
      { title: '소비 습관', desc: '매달 반복되는 소비를 짚어요.', ask: '내 소비 습관 어때?' },
      { title: '지난달과 비교', desc: '더 쓴 이유를 찾아요.', ask: '이번 달 생활비 왜 이렇게 많이 나왔지?' },
    ] },
  ] }
  await act(() => root.render(<ChatReply content={content} title="소비 분석" active onPick={() => {}} onSend={onSend} />))
  const cards = host.querySelectorAll('.mc-fx-options button')
  expect(cards).toHaveLength(2)
  expect(cards[0].textContent).toContain('매달 반복되는 소비를 짚어요.')
  await act(() => cards[1].click())
  expect(onSend).toHaveBeenCalledWith('이번 달 생활비 왜 이렇게 많이 나왔지?')
  await act(() => root.render(<ChatReply content={content} title="소비 분석" active={false} onPick={() => {}} onSend={onSend} />))
  expect(host.querySelectorAll('.mc-fx-options button')).toHaveLength(0)
  expect(host.querySelector('.mc-fx-options').textContent).toContain('소비 습관')
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
  expect(host.querySelector('.mc-fx-choices')).toBeNull()
})

it('결과 카드(분야 안내)도 안내 문장 아래 회색 예시 줄을 보인다', async () => {
  await render({ agent: 'policy', result: true, lines: ['이런 분야를 찾을 수 있어요.'], examples: ['월세 지원 있을까요?'],
    sections: [{ title: '찾을 수 있는 분야', lines: ['주거: 월세 지원'] }] })
  expect(host.querySelector('.mc-examples').textContent).toBe('예) 월세 지원 있을까요?')
  expect(host.querySelector('.mc-policy-table dd').textContent).toBe('월세 지원')
  expect(host.querySelector('.mc-fx-choices')).toBeNull()
})

it('정책 카드의 기존 안내는 접지 않고, 질문 말풍선의 입력도 유지한다', async () => {
  await render({ agent: 'policy', result: true, lines: ['정책을 찾았어요.'], lead: ['지역 기준'], notices: ['신청 기간 확인'] })
  expect(host.querySelector('.mc-fx-lead').textContent).toContain('지역 기준')
  expect(host.querySelector('.mc-fx-notes')).toBeNull()
  expect(host.querySelector('.mc-spend').lastElementChild.textContent).toBe('신청 기간 확인')
  await act(() => root.render(<ChatReply content={{ agent: 'policy', lines: ['지역을 골라 주세요.'] }} title="청년 정책">
    <select aria-label="지역 선택"><option>서울</option></select>
  </ChatReply>))
  expect(host.querySelector('.mc-bubble select').getAttribute('aria-label')).toBe('지역 선택')
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

it('소비 비중 분석에는 카테고리별 금액과 비율 막대를 보여 준다', async () => {
  await render({ ...month, view: [
    { kind: 'head', title: '소비 분석' },
    { kind: 'rows', title: '분류된 소비의 비중', rows: [
      { code: 'FOOD', name: '식비', amount: 60000, ratio: 60, note: '60%' },
      { code: 'FASHION_SHOPPING', name: '패션·쇼핑', amount: 40000, ratio: 40, note: '40%' },
      { code: 'UNCLASSIFIED', name: '미분류', amount: 10000, ratio: 10, note: '10%' },
    ] },
  ] })
  const chart = host.querySelector('.mc-spend-cats')
  expect(chart.querySelector('strong').textContent).toBe('분류된 소비의 비중')
  const rows = [...chart.querySelectorAll('.mc-spend-cat')]
  expect(rows.map((row) => row.querySelector('.mc-spend-cname').textContent)).toEqual(['식비', '패션·쇼핑'])
  expect(rows.map((row) => row.querySelector('.mc-spend-cbar > span').style.width)).toEqual(['60%', '40%'])
  expect(rows.map((row) => row.querySelector('.mc-spend-camt').textContent)).toEqual(['60,000원60%', '40,000원40%'])
  expect(chart.textContent).not.toContain('미분류')
  expect(host.querySelector('.mc-fx-months')).toBeNull()
})

it('항목별 전망 막대의 읽기 이름은 화면 금액과 같고 NaN원이 나오지 않는다', async () => {
  await render({ ...month, view: [
    { kind: 'head', title: '소비 분석' },
    { kind: 'rows', title: '항목별 이번 달 말 예상', rows: [
      { name: '식비', value: '41.7만', note: '예산 40만의 104% · 넘을 전망', ratio: 100, direction: 'up' },
      { name: '교통·차량', value: '9만', note: '예산 10만의 90%', ratio: 22 },
    ] },
  ] })
  const bars = [...host.querySelectorAll('.mc-spend-cbar')]
  expect(bars.map((bar) => bar.getAttribute('aria-label'))).toEqual(['식비 41.7만', '교통·차량 9만'])
  expect(host.textContent).not.toContain('NaN')
  const amounts = [...host.querySelectorAll('.mc-spend-camt')].map((el) => el.firstChild.textContent)
  expect(amounts).toEqual(['41.7만', '9만'])
})


it('소비 유형 아래 자세히 보기는 처음에 접고 분석 문장과 그래프를 한 번씩 보존한다', async () => {
  const summary = '쇼핑 예산을 여러 번 늘렸어요.'
  const evidence = '쇼핑 예산을 세 번 올렸어요.'
  await render({ ...month, view: [
    { kind: 'head', title: '이번 달 소비 성향' },
    { kind: 'text', title: '이번만 조금 더형 🐣', tone: 'key', lines: [summary] },
    { kind: 'details', title: '자세히 보기', blocks: [
      { kind: 'text', title: '예산 관리 습관', lines: [evidence] },
      { kind: 'trend', title: '최근 소비 추이', series: [
        { code: 'FASHION_SHOPPING', name: '패션·쇼핑', points: [{ label: '첫째 주', amount: 50000 }, { label: '둘째 주', amount: 90000 }] },
      ] },
      { kind: 'rows', title: '월말 전망', rows: [{ name: '패션·쇼핑', value: '24만 원' }] },
    ] },
  ] })
  const fold = host.querySelector('.mc-fx-fold')
  expect(fold.open).toBe(false)
  expect(host.querySelector('.mc-fx-key').closest('details')).toBeNull()
  expect(host.querySelector('.mc-fx-key').textContent).toContain(summary)
  await act(() => fold.querySelector('summary').click())
  expect(fold.open).toBe(true)
  expect(fold.textContent).toContain(evidence)
  expect(fold.textContent).toContain('24만 원')
  expect(fold.querySelectorAll('svg[role="img"]')).toHaveLength(1)
  expect(fold.querySelector('table').textContent).toContain('90,000원')
  expect(host.textContent.match(/쇼핑 예산을 세 번 올렸어요./g)).toHaveLength(1)
  await act(() => fold.querySelector('summary').click())
  expect(fold.open).toBe(false)
  expect(fold.querySelector('svg[role="img"]')).not.toBeNull()
})

it('자세히 보기 안의 분석 선택도 마지막 답변에서만 원래 질문을 전송한다', async () => {
  const onSend = vi.fn()
  const question = '쇼핑 소비 추이를 보여줘'
  const content = { ...month, view: [
    { kind: 'head', title: '소비 분석' },
    { kind: 'details', blocks: [{ kind: 'options', items: [{ title: '쇼핑 추이', desc: '최근 흐름', ask: question }] }] },
  ] }
  await act(() => root.render(<ChatReply content={content} title="소비 분석" active onSend={onSend} />))
  await act(() => host.querySelector('.mc-fx-fold > summary').click())
  await act(() => host.querySelector('.mc-fx-options button').click())
  expect(onSend).toHaveBeenCalledExactlyOnceWith(question)
  await act(() => root.render(<ChatReply content={content} title="소비 분석" active={false} onSend={onSend} />))
  expect(host.querySelector('.mc-fx-options button')).toBeNull()
  expect(host.querySelector('.mc-fx-options').textContent).toContain('쇼핑 추이')
})


it('소비 유형 카드는 마스코트 · 유형 이름 · 소개 · 근거 · 해볼 한 가지를 접지 않고 맨 위에 보여 준다', async () => {
  await render({ ...month, view: [
    { kind: 'head', title: '이번 달 소비 성향' },
    { kind: 'persona', mascot: 'planner', name: '계획 척척형', desc: '정한 예산 안에서 꾸준히 소비를 관리해요.',
      reasons: ['생활비는 예산 속도에 맞게 쓰고 있어요.'], action: '이번 주도 남은 예산을 한 번 확인해 보세요.' },
    { kind: 'details', title: '자세한 소비 분석 보기', blocks: [{ kind: 'text', title: '소비 추이', lines: ['소비 추이', '지난달과 비슷해요.'] }] },
  ] })
  const card = host.querySelector('.mc-fx-persona')
  expect(card.closest('details')).toBeNull()
  expect(card.getAttribute('aria-label')).toBe('나의 소비 유형')
  expect(card.querySelector('small').textContent).toBe('나의 소비 유형')
  expect(card.querySelector('.mc-fx-persona-tags')).toBeNull()
  expect(card.querySelector('.mc-mascot').dataset.mood).toBe('planner')
  expect(card.querySelector('h3').textContent).toBe('계획 척척형')
  expect(card.textContent).toContain('정한 예산 안에서 꾸준히 소비를 관리해요.')
  expect(card.textContent).toContain('생활비는 예산 속도에 맞게 쓰고 있어요.')
  expect(card.textContent).toContain('이번에 해볼 한 가지')
  expect(host.querySelector('.mc-fx-fold').open).toBe(false)
})

it('소비 유형 카드는 예산 관리 태그를 칩으로 보여 주고 판정 기록(traits)은 그리지 않는다', async () => {
  await render({ ...month, view: [{ kind: 'head', title: '소비 분석' },
    { kind: 'persona', mascot: 'beauty', name: '나를 가꾸는 게 좋아형', desc: '나를 위한 관리에 투자하는 편이에요.',
      tags: [{ code: 'saver', label: '차곡차곡 모아요' }, { code: 'stretch', label: '이번만 조금 더' }],
      traits: [{ code: 'BEAUTY', axis: 'taste', basis: 'own_history', status: 'met', strength: 0.87 }],
      reasons: ['최근 완료된 3개월 생활비의 평균 10%가 뷰티·미용이에요.'], action: null }] })
  const card = host.querySelector('.mc-fx-persona')
  const chips = [...card.querySelectorAll('.mc-fx-persona-tags li')]
  expect(chips.map((li) => li.textContent)).toEqual(['차곡차곡 모아요', '이번만 조금 더'])
  expect(chips.map((li) => li.dataset.tag)).toEqual(['saver', 'stretch'])
  expect(card.textContent).not.toContain('own_history')
  expect(card.textContent).not.toContain('0.87')
})

it('유형을 정하지 않아도 확인된 소비 특징을 분석 카드로 보여 준다', async () => {
  await render({ ...month, view: [{ kind: 'head', title: '소비 분석' },
    { kind: 'persona', mascot: 'basic', label: '지금까지의 소비 분석', name: '확인된 소비 특징', desc: '',
      reasons: ['점심 무렵에는 식비 결제가 가장 많아요.'], action: null }] })
  const card = host.querySelector('.mc-fx-persona')
  expect(card.getAttribute('aria-label')).toBe('지금까지의 소비 분석')
  expect(card.querySelector('small').textContent).toBe('지금까지의 소비 분석')
  expect(card.querySelector('h3').textContent).toBe('확인된 소비 특징')
  expect(card.textContent).toContain('점심 무렵에는 식비 결제가 가장 많아요.')
  expect(card.querySelector('.mc-mascot').dataset.mood).toBe('basic')
  expect(card.closest('details')).toBeNull()
  expect(card.querySelector('.mc-fx-persona-top p')).toBeNull()
  expect(card.querySelector('.mc-fx-persona-act')).toBeNull()
})

it('모르는 마스코트 코드는 기본 표정으로 그린다', async () => {
  await render({ ...month, view: [{ kind: 'head', title: '이번 달 소비 성향' },
    { kind: 'persona', mascot: 'unknown', name: '아직 탐색 중이에요', desc: '아직 뚜렷한 습관이 보이지 않아요.', reasons: [] }] })
  expect(host.querySelector('.mc-mascot').dataset.mood).toBe('basic')
  expect(host.querySelector('.mc-fx-persona-why')).toBeNull()
})

it('예산 조정의 추천 질문은 미리보기와 확정 버튼을 유지하고 표시 문장만 보낸다', async () => {
  const onSend = vi.fn(), onPick = vi.fn(), onApprove = vi.fn()
  const content = { lines: [], examples: ['이 조정안의 변경 내역을 설명해줘'], budgetAdjustment: {
    status: 'replied', canApprove: true, proposal: { state: 'awaiting_approval', changes: [
      { category: 'FOOD', before: 400000, after: 420000, diff: 20000 },
    ] },
  } }
  await act(() => root.render(<ChatReply content={content} active onPick={onPick} onApprove={onApprove} onSend={onSend} />))
  const before = host.querySelector('.mc-budget-changes').textContent
  expect(before).toContain('400,000원')
  expect(before).toContain('420,000원')
  expect(button('예산 확정하기').disabled).toBe(false)
  await act(() => button(content.examples[0]).click())
  expect(onSend).toHaveBeenCalledExactlyOnceWith(content.examples[0])
  expect(onPick).not.toHaveBeenCalled()
  expect(onApprove).not.toHaveBeenCalled()
  expect(host.querySelector('.mc-budget-changes').textContent).toBe(before)
  expect(button('예산 확정하기').disabled).toBe(false)
})

it('지난 예산 답변이나 전송 기능이 없는 답변의 추천 질문은 실행할 수 없다', async () => {
  const onSend = vi.fn()
  const content = { lines: ['금액을 알려 주세요.'], examples: ['금액을 어떻게 정하면 좋을까?'],
    budgetAdjustment: { status: 'awaiting_input', phase: 'amount' } }
  for (const props of [{ active: false, onSend }, { active: true }]) {
    await act(() => root.render(<ChatReply content={content} {...props} />))
    expect(button(content.examples[0]).disabled).toBe(true)
    await act(() => button(content.examples[0]).click())
    expect(onSend).not.toHaveBeenCalled()
  }
})

it('종료된 예산 응답에는 저장된 추천 질문이 남아 있어도 표시하지 않는다', async () => {
  for (const state of ['saved', 'expired', 'canceled', 'rejected', 'failed', 'ended']) {
    for (const adjustment of [{ status: state }, { status: 'replied', proposal: { state } }]) {
      if (state === 'ended' && adjustment.proposal) continue
      const content = { lines: ['조정을 마쳤어요.'], examples: ['변경 내역을 설명해줘'], budgetAdjustment: adjustment }
      await act(() => root.render(<ChatReply content={content} active onSend={vi.fn()} />))
      expect(button(content.examples[0])).toBeUndefined()
    }
  }
})

it('분석 문장은 글자를 그대로 두고 숫자 · 괄호 · 나열 · 계산식만 읽기 쉽게 나눈다', async () => {
  const eq = '이번 달 말 약 52.5만 원 = 지금까지 쓴 23.5만 원 + 남은 18일 예상 29만 원'
  const list = '패션·쇼핑 +70,660원 · 문화·여가 -54,800원 · 기타 -29,748원'
  const aside = '식비 630,000원 (지난달 같은 기간 406,000원)'
  await render({ ...month, view: [
    { kind: 'head', title: '소비 분석' },
    { kind: 'text', tone: 'key', title: '계산 요약', lines: ['계산 요약', eq] },
    { kind: 'text', title: '지난달과 달라진 점', lines: ['지난달과 달라진 점', list, aside, '다음 달(11월)도 지금처럼 쓰면 약 141만 원이에요.'] },
  ] })
  const key = host.querySelector('.mc-fx-key p')
  expect(key.textContent).toBe(eq)
  expect(key.querySelector('.mc-fx-eq-result').textContent).toBe('이번 달 말 약 52.5만 원')
  const items = [...host.querySelectorAll('.mc-fx-lines li')]
  expect(items.map((li) => li.textContent)).toEqual([list, aside, '다음 달(11월)도 지금처럼 쓰면 약 141만 원이에요.'])
  expect([...items[0].querySelectorAll('.mc-fx-chip')].map((c) => c.textContent)).toEqual(['패션·쇼핑 +70,660원', '문화·여가 -54,800원', '기타 -29,748원'])
  expect(items[0].querySelector('.mc-fx-num.mc-fx-up').textContent).toBe('+70,660원')
  expect(items[1].querySelector('.mc-fx-aside').textContent).toBe(' (지난달 같은 기간 406,000원)')
  expect(items[2].querySelector('.mc-fx-aside')).toBeNull()  // 붙여 쓴 괄호(11월)는 그대로
})
