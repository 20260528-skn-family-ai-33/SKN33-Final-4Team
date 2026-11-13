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
