// 챗봇 답변 한 개 (pages/AssistantPage.jsx). 옛 개발용 로컬 채팅(local_chat.html, 11/10 삭제)의 bot() · chart() 를 옮겼다 (MEMO 25절 2단계)
// content = 서버 봇 메시지 content: { text, lead, lines, sections, notices, choices, chart?, view?, turn, agent, inputs, result } (MEMO 22-3 · 22-12 · 22-13)
// 디자인은 기존 클래스만: 분석 결과(result) = 소비 분석 카드(mc-spend*), 그 밖의 답 = 채팅 말풍선(mc-bubble)
// - 분석 안내·계산 기준은 카드 맨 아래에서 펼쳐 본다. 금액 옆의 자료 제한 표시는 유지한다
// - 그래프: 상위 5줄은 바로, 나머지는 접기. 막대 길이 = 서버가 준 비율(share), 금액 · 비율은 글자로도. 화면에서 다시 계산하지 않는다
// - 옛 메시지 · 꺼짐(legacy) 엔진 답(text · choices 만 있는 것)도 말풍선으로 그린다
import { useEffect, useRef, useState } from 'react'
import { useInRouterContext, useNavigate } from 'react-router'
import { PATHS } from '../routes.js'
import { won } from '../utils/money.js'
import { ENDED_ADJUSTMENTS } from '../utils/adjust.js'
import Mascot from './Mascot.jsx'
import { apiSend, useApi } from '../utils/api.js'
import { DETAIL, policyNumber, policyTitle } from '../utils/policyCards.js'
import BudgetAdjustment from './BudgetAdjustment.jsx'
import Icon from './Icon.jsx'
import Popup from '../popups/Popup.jsx'
import { usePopup } from '../popups/context.js'
import '../styles/policies.css'

const Lead = ({ items }) => items.map((l, i) => (
  // span 을 쓰지 않는다: mc-spend-unc 의 마지막 span 은 금액 칸용 줄바꿈 금지라 긴 문장이 카드 밖으로 나간다
  <p key={`lead-${i}`} className="mc-spend-unc mc-fx-lead" role="note"><strong>안내:</strong> {l}</p>
))

// 11/18 검수: 아직 카테고리를 정하지 않은 거래 때문에 금액이 늘 수 있다는 안내는 결과를 바꾸는 정보라 접지 않고 보이게 + [확인하기]
const CAVEAT = '카테고리를 정하지 않은 거래'
function Notes({ content }) {
  const inRouter = useInRouterContext()
  const oldUnclassified = content.chart?.rows?.find(isUnclassified) ?? content.view?.flatMap((b) => b.rows ?? []).find(isUnclassified)
  const unclassified = content.chart?.unclassified ?? (oldUnclassified && `확인 필요 ${won(oldUnclassified.amount)}은 총액에 포함하고 주요 카테고리에서 제외했어요.`)
  const all = [...new Set([...(content.lead ?? []), content.chart?.basis, content.chart?.limited, ...(content.notices ?? []), unclassified].filter(Boolean))]
  const caveats = all.filter((t) => t.includes(CAVEAT))
  const items = all.filter((t) => !t.includes(CAVEAT))
  if (!all.length) return null
  return <>
    {caveats.map((text) => <p key={text} className="mc-fx-caveat" role="note">{text}{inRouter && <PendingLink />}</p>)}
    {items.length > 0 && <details className="mc-fx-notes">
      <summary>분석 기준·안내</summary>
      {items.map((text, i) => <p key={i} className="mc-note">{text}</p>)}
    </details>}
  </>
}
function PendingLink() {
  const navigate = useNavigate()
  return <button type="button" className="mc-link" onClick={() => navigate(`${PATHS.txs}?category=UNCLASSIFIED`)}>확인하기 ›</button>
}

// 서버가 한 문장을 두 줄로 나눠 보내기도 해서('10월 … 소비' + '9월 …과 비교했어요.') 마침표 · 물음표로 끝나는 줄까지 이어 붙인다
function firstSentence(lines) {
  const end = lines.findIndex((l) => /[.?!]$/.test(l.trim()))
  return lines.slice(0, end < 0 ? 1 : end + 1).join(' ')
}

// 머리줄 (11/18 검수): 서버 제목 '10월 1일~31일 · 식비' → 달 전체면 '10월 식비' 한 줄(질문과 같은 순서), 아니면 주제를 크게 · 기간을 작게.
// 날짜로 시작하지 않는 제목은 그대로 + 오른쪽 작은 글자는 답 종류
const FULL_MONTH = /^(\d{1,2})월 1일~(2[89]|3[01])일$/
function headParts(text, kind) {
  const cut = text.indexOf(' · ')
  const period = cut < 0 ? '' : text.slice(0, cut)
  if (!/^\d{1,2}월/.test(period)) return [text, kind]
  const topic = text.slice(cut + 3)
  const month = period.match(FULL_MONTH)
  return month ? [`${month[1]}월 ${topic}`, null] : [topic, period]
}

// 카테고리 막대: 금액이 큰 상위 SHOWN 줄은 바로, 나머지는 '나머지 N개 더 보기'로 접는다 (순서 · 금액 · 비율은 서버 그대로, 화면은 자르기만)
const SHOWN = 5
const isUnclassified = (row) => row.code === 'UNCLASSIFIED' || row.name === '확인 필요' || row.name === '미분류'

function CatRows({ rows }) {
  return (
    <div className="mc-spend-cats">
      {rows.map((r) => (
        <div key={r.code} className="mc-spend-cat" role="img" aria-label={r.text}>
          <span className="mc-spend-cname">{r.name}</span>
          <span className="mc-spend-cbar"><span style={{ width: `${Math.max(Number(r.share), 0)}%` }}></span></span>
          <span className="mc-spend-camt">{won(r.amount)}<small>{r.share}%</small></span>
        </div>
      ))}
    </div>
  )
}

function Chart({ chart }) {
  const rows = chart.rows.filter((r) => !isUnclassified(r))
  const top = rows.slice(0, SHOWN)
  const rest = rows.slice(SHOWN)
  return (
    <div className="mc-fx-chart">
      {chart.empty && <p className="mc-spend-empty">{chart.empty}</p>}
      {top.length > 0 && <CatRows rows={top} />}
      {rest.length > 0 && (
        <details>
          <summary>나머지 {rest.length}개 더 보기</summary>
          <CatRows rows={rest} />
        </details>
      )}
    </div>
  )
}

// 카드 보기(content.view) 블록 — 서버가 준 금액 · 비율 · 글자 그대로. SpendingCard 와 같은 클래스
//   head = 머리줄(기간 · 범위, 둘째부터는 소제목) / total = 큰 금액(건수처럼 돈이 아니면 value 글자) / rows = 이름 · 막대 · 금액(또는 value 글자) 줄 (badge = 증감 글자 + 색) / text = 짧은 문장 / meter = 생활비 예산 막대
const BADGE = { up: ' is-up', down: ' is-down', same: '' }
const WAY = { up: ' mc-fx-up', down: ' mc-fx-down' }  // 늘었어요 = 붉은 글자 · 줄었어요 = 파란 글자 (배지와 같은 색, 글자로도 적혀 있음)
const ANALYSIS_TITLES = new Set(['핵심 진단', '분석 근거', '앞으로의 의미', '주요 카테고리', '큰 결제와 반복 소비', '생활 패턴', '월말 전망', '지출이 달라진 형태', '소비 속도', '해볼 행동', '확인할 질문',
  '예산 속도', '지난달과 비교', '또래 기준·저축 여력', '소비 추이', '소비 습관', '앞으로'])

// 분석 문장 읽기 쉽게 (11/16): 글자는 서버 문장 그대로 두고(textContent 같음) 모양만 나눈다.
// 금액 · 비율 · 배수는 굵게(+ 붉은 · - 파란), 괄호 속 보조 설명은 옅게, '항목 금액 · 항목 금액' 나열은 칩으로, 'A = B + C' 계산식은 결과와 식을 두 줄로
// 11/17: 숫자 바로 뒤에 붙은 한글 조사(77.3만 원'이' · 4.9배'를')까지 한 덩어리로 묶어 줄이 그 사이에서 끊기지 않게 한다
const NUM = /([+\-−]?\d[\d,]*(?:\.\d+)?(?:\s?만\s?원|만|원|%|배))([가-힣]*)/
function Nums({ text }) {
  const parts = text.split(NUM)  // [글, 숫자, 붙은 조사, 글, 숫자, 붙은 조사, …]
  const out = []
  for (let i = 0; i < parts.length; i += 3) {
    if (parts[i]) out.push(parts[i])
    if (i + 1 >= parts.length) break
    const num = parts[i + 1]
    const sign = /^[+]/.test(num) ? ' mc-fx-up' : /^[-−]/.test(num) ? ' mc-fx-down' : ''
    out.push(<span key={i} className="mc-fx-nb"><b className={`mc-fx-num${sign}`}>{num}</b>{parts[i + 2]}</span>)
  }
  return out
}
// 달마다 흐름 ('9월 47만 원 → 10월 64.5만 원'): 달 · 금액 칸을 화살표로 잇는다. 마지막 달(가장 최근)만 진하게. 글자는 그대로
const FLOW = /^\d{1,2}월 [^→]+(?: → \d{1,2}월 [^→]+)+$/
function Flow({ text }) {
  const steps = text.split(' → ')
  return <span className="mc-fx-flow">{steps.map((step, i) => {
    const [, month, amount] = step.match(/^(\d{1,2}월) (.+)$/)
    return <span key={i} className="mc-fx-flow-item">
      {i > 0 && <span className="mc-fx-flow-arrow"> → </span>}
      <span className={`mc-fx-flow-step${i === steps.length - 1 ? ' is-last' : ''}`}><small>{month}</small> <b>{amount}</b></span>
    </span>
  })}</span>
}
const digits = (t) => Number(t.replace(/\D/g, ''))  // '40,400원' → 40400
// 생활 패턴 (11/17): 주말 · 평일 하루 평균 문장은 두 칸 나란히(큰 쪽 강조) + 기준은 작은 글씨. 금액은 문장 속 숫자 그대로
const WEEKDAYS = /^주말 하루 평균 ([\d,]+원), 평일 하루 평균 ([\d,]+원)이에요 \((.+)\)\.$/
function Weekdays({ text }) {
  const [, end, day, basis] = text.match(WEEKDAYS)
  const big = digits(end) >= digits(day)
  return <span className="mc-fx-pair" role="img" aria-label={text}>
    <span className="mc-fx-flow">
      <span className={`mc-fx-flow-step${big ? ' is-last' : ''}`}><small>주말 하루 평균</small> <b>{end}</b></span>
      <span className={`mc-fx-flow-step${big ? '' : ' is-last'}`}><small>평일 하루 평균</small> <b>{day}</b></span>
    </span>
    <small className="mc-fx-pair-basis">{basis}</small>
  </span>
}
function Rich({ text }) {
  if (typeof text !== 'string') return text
  if (FLOW.test(text)) return <Flow text={text} />
  if (WEEKDAYS.test(text)) return <Weekdays text={text} />
  const parts = text.split(' · ')
  if (parts.length >= 2 && parts.every((p) => /\d/.test(p) && !/[요다]\.?$/.test(p.trim()))) {
    return <span className="mc-fx-chips">{parts.map((p, i) => <span key={i}>
      {i > 0 && <span className="mc-fx-sr"> · </span>}<span className="mc-fx-chip"><Nums text={p} /></span></span>)}</span>
  }
  return text.split(/( \([^()]*\))/).map((part, i) => i % 2  // 띄어 쓴 괄호만 (예: '다음 달(11월)'은 그대로)
    ? <span key={i} className="mc-fx-aside">{part}</span>
    : <Nums key={i} text={part} />)
}
function KeyLine({ text }) {
  const eq = typeof text === 'string' && text.match(/^(.+?) = (.+)$/)
  if (eq) {  // 11/17: 계산식은 '+ 항목'마다 새 줄로 (항목 중간에서 줄이 끊기지 않게). 글자는 그대로 — 줄 앞 공백은 화면에서 안 보인다
    const [first, ...more] = eq[2].split(' + ')
    return <p className="mc-fx-eq"><span className="mc-fx-eq-result"><Nums text={eq[1]} /></span> <span className="mc-fx-eq-calc">= <Nums text={first} />
      {more.map((term, i) => <span key={i} className="mc-fx-eq-term"> + <Nums text={term} /></span>)}</span></p>
  }
  return <p><Rich text={text} /></p>
}

// 월별 생활비 막대 (11/13): 완료된 달 = 실제(진한 막대), 이번 달 = 쓴 돈 + 남은 기간 예상(빗금), 다음 달 = 예상(빗금).
// 예산선은 서버가 예산과 같은 범위로 비교할 수 있을 때만 준다. 금액은 서버 값 그대로, 화면은 높이만 계산한다
const man = (n) => (n >= 1000000 ? `${Math.round(n / 10000).toLocaleString()}만` : n >= 10000 ? `${Math.round(n / 1000) / 10}만` : won(n))

function Months({ block }) {
  const bars = block.bars
  const top = Math.max(1, block.budget ?? 0, ...bars.map((b) => b.actual + b.projected))
  const W = 360, H = 190, base = 150, left = 12, gap = 10
  const width = (W - left * 2 - gap * (bars.length - 1)) / bars.length
  const y = (v) => base - (v / top) * 118
  const describe = bars.map((b) => `${b.label} ${b.kind === 'actual' ? won(b.actual) : b.kind === 'current' ? `쓴 돈 ${won(b.actual)} + 예상 ${won(b.projected)}` : `예상 ${won(b.projected)}`}`).join(', ')
  return (
    <figure className="mc-fx-months mc-fx-section">
      <figcaption><h3>{block.title}</h3></figcaption>
      <ul className="mc-fx-months-legend">
        <li><i className="is-actual" />실제</li>
        <li><i className="is-projected" />지금 흐름이면</li>
        {block.budget && <li><i className="is-budget" />예산 {man(block.budget)}</li>}
      </ul>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${block.title}: ${describe}${block.budget ? `, 예산 ${won(block.budget)}` : ''}`}>
        <defs>
          <pattern id="mc-fx-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" className="mc-fx-hatch-bg" /><line x1="0" y1="0" x2="0" y2="6" className="mc-fx-hatch-line" />
          </pattern>
        </defs>
        <line x1={left} x2={W - left} y1={base} y2={base} className="mc-fx-months-axis" />
        {block.budget && <line x1={left} x2={W - left} y1={y(block.budget)} y2={y(block.budget)} className="mc-fx-months-budget" />}
        {bars.map((b, i) => {
          const x = left + i * (width + gap)
          const total = b.actual + b.projected
          return <g key={b.label}>
            {b.actual > 0 && <rect x={x} y={y(b.actual)} width={width} height={base - y(b.actual)} rx="4" className="mc-fx-bar-actual" />}
            {b.projected > 0 && <rect x={x} y={y(total)} width={width} height={y(b.actual) - y(total)} rx="4" fill="url(#mc-fx-hatch)" className="mc-fx-bar-projected" />}
            {/* 금액은 막대 안쪽 위에 (예산선과 겹치지 않게). 막대가 너무 낮으면 위에 */}
            <text x={x + width / 2} y={base - y(total) > 26 ? y(total) + 15 : y(total) - 6} textAnchor="middle"
              className={`mc-fx-bar-value${base - y(total) > 26 ? (b.kind === 'actual' ? ' is-in' : ' is-in-light') : ''}`}>{man(total)}</text>
            <text x={x + width / 2} y={base + 16} textAnchor="middle" className={b.kind === 'actual' ? '' : 'is-future'}>{b.label}</text>
          </g>
        })}
      </svg>
      <details><summary>월별 금액 보기</summary>
        <table><caption>{block.basis}</caption><thead><tr><th scope="col">달</th><th scope="col">실제</th><th scope="col">예상</th></tr></thead>
          <tbody>{bars.map((b) => <tr key={b.label}><th scope="row">{b.label}</th><td>{b.actual ? won(b.actual) : '-'}</td><td>{b.projected ? won(b.actual + b.projected) : '-'}</td></tr>)}</tbody>
        </table>
      </details>
    </figure>
  )
}

// 예산 속도 막대 (11/13): 카테고리마다 예산 사용률 막대 + 주기가 지난 지점 표시선. 금액 · 비율 · 상태는 서버 값 그대로
//   상태 over = 예산 넘음 · fast = 주기보다 빨리 씀 · slack = 여유 · ok = 보통. 색만이 아니라 글자(넘음 · 빠름 · 여유)로도 적는다
const PACE_STATE = { over: ['is-over', '넘음'], fast: ['is-fast', '빠름'], slack: ['is-slack', '여유'], ok: ['', ''] }
const PACE_SHOWN = 4

function PaceRow({ row, elapsed }) {
  const [cls, word] = PACE_STATE[row.state] ?? PACE_STATE.ok
  const label = `${row.name} 예산 ${won(row.allocated)} 중 ${won(row.spent)} 사용 (${row.ratio}%)${word ? `, ${word}` : ''}`
  return (
    <div className={`mc-fx-pace-row ${cls}`.trim()} role="img" aria-label={label}>
      <span className="mc-fx-pace-name">{row.name}{word && <em>{word}</em>}</span>
      <span className="mc-fx-pace-bar">
        <span style={{ width: `${Math.min(Math.max(Number(row.ratio), 0), 100)}%` }}></span>
        <i style={{ left: `${Math.min(Math.max(Number(elapsed), 0), 100)}%` }} aria-hidden="true"></i>
      </span>
      <span className="mc-fx-pace-amt">{row.ratio}%</span>
      <small className="mc-fx-pace-sub">{won(row.spent)} / {won(row.allocated)}</small>
    </div>
  )
}

function Pace({ block }) {
  const top = block.rows.slice(0, PACE_SHOWN)
  const rest = block.rows.slice(PACE_SHOWN)
  return (
    <figure className="mc-fx-pace">
      <figcaption><strong>{block.title}</strong><small>{block.caption}</small></figcaption>
      {top.map((r) => <PaceRow key={r.code} row={r} elapsed={block.elapsed} />)}
      {rest.length > 0 && (
        <details>
          <summary>나머지 {rest.length}개 더 보기</summary>
          {rest.map((r) => <PaceRow key={r.code} row={r} elapsed={block.elapsed} />)}
        </details>
      )}
      <p className="mc-fx-pace-legend"><i aria-hidden="true"></i>세로선 = 지금까지 지난 예산 주기</p>
    </figure>
  )
}

function Trend({ block }) {
  const series = block.series
  const maximum = Math.max(0, ...series.flatMap((s) => s.points.map((p) => p.amount)))
  const x = (i, count) => 68 + (count > 1 ? i * 256 / (count - 1) : 128)
  const y = (amount) => 130 - (maximum ? amount / maximum * 104 : 0)
  const colors = ['var(--mc-brand)', 'var(--mc-olive)']
  return (
    <figure className="mc-fx-trend mc-fx-section">
      <figcaption><h3>{block.title}</h3></figcaption>
      <ul className="mc-fx-trend-legend">
        {series.map((s, i) => <li key={s.code}><i style={{ borderColor: colors[i % 2], borderTopStyle: i % 2 ? 'dashed' : 'solid' }} />{s.name}<strong>{won(s.points.at(-1).amount)}</strong><small>최근 7일</small></li>)}
      </ul>
      <svg viewBox="0 0 360 172" role="img" aria-label={block.title}>
        <desc>{block.basis}. {series.map((s) => `${s.name}: ${s.points.map((p) => `${p.label} ${won(p.amount)}`).join(', ')}`).join('. ')}</desc>
        {[0, 0.5, 1].map((ratio) => <g key={ratio}>
          <line x1="68" x2="324" y1={y(maximum * ratio)} y2={y(maximum * ratio)} className="mc-fx-trend-grid" />
          <text x="58" y={y(maximum * ratio) + 3} textAnchor="end">{won(Math.round(maximum * ratio))}</text>
        </g>)}
        {series.map((s, index) => <g key={s.code} style={{ color: colors[index % 2] }}>
          <polyline fill="none" stroke="currentColor" strokeWidth="2.5" strokeDasharray={index % 2 ? '6 4' : undefined}
            points={s.points.map((p, i) => `${x(i, s.points.length)},${y(p.amount)}`).join(' ')} />
          {s.points.map((p, i) => <circle key={p.label} cx={x(i, s.points.length)} cy={y(p.amount)} r="4" fill="currentColor">
            <title>{`${s.name} ${p.label}: ${won(p.amount)}`}</title>
          </circle>)}
        </g>)}
        {series[0].points.map((p, i) => <text key={p.label} x={x(i, series[0].points.length)} y="154" textAnchor="middle">{p.label.split('~')[0]}</text>)}
      </svg>
      <details><summary>주별 금액 보기</summary>
        {block.basis && <p className="mc-note">{block.basis}</p>}
        <table><caption>주별 관측 금액</caption><thead><tr><th scope="col">기간</th>{series.map((s) => <th key={s.code} scope="col">{s.name}</th>)}</tr></thead>
          <tbody>{series[0].points.map((p, i) => <tr key={p.label}><th scope="row">{p.label}</th>{series.map((s) => <td key={s.code}>{won(s.points[i].amount)}</td>)}</tr>)}</tbody>
        </table>
      </details>
    </figure>
  )
}

// 막대의 읽기 이름은 화면에 보이는 금액과 같은 값을 쓴다 (전망 행은 amount 없이 value 문자열만 온다 → NaN원 방지)
const rowAmount = (r) => r.value ?? (r.amount != null && Number.isFinite(Number(r.amount)) ? won(r.amount) : '')

function Rows({ block }) {
  const comparison = block.presentation === 'period_comparison'
  return (
    <div className="mc-spend-cats mc-fx-rows">
      {block.title && !comparison && <strong>{block.title}</strong>}
      {block.rows.filter((r) => !isUnclassified(r)).map((r) => (
        <div key={r.name} className="mc-spend-cat">
          <span className="mc-spend-cname">{r.name}</span>
          {r.badge
            ? <span className={`mc-spend-diff${BADGE[r.badge.direction]}`}>{r.badge.text}</span>
            : r.ratio != null
              ? <span className="mc-spend-cbar" role="img" aria-label={`${r.name} ${rowAmount(r)}`.trim()}><span style={{ width: `${Number(r.ratio)}%` }}></span></span>
              : <span></span>}
          <span className={`mc-spend-camt${WAY[r.direction] ?? ''}`}>{rowAmount(r)}{r.note && <small>{r.note}</small>}</span>
        </div>
      ))}
    </div>
  )
}

function PeriodComparison({ block }) {
  return <figure className="mc-fx-period-bars" aria-label={block.title}>
    <figcaption>{block.title}</figcaption>
    <Rows block={block} />
  </figure>
}

// 분석 메뉴 (11/14): 볼 수 있는 분석 + 한 줄 설명. 누르면 그 질문을 보낸다 (마지막 답변일 때만 — 지난 답은 설명만)
function Options({ block, onSend }) {
  return (
    <ul className="mc-fx-options" aria-label="볼 수 있는 분석">
      {block.items.map((o) => (
        <li key={o.title}>
          {onSend
            ? <button type="button" onClick={() => onSend(o.ask)}><strong>{o.title}</strong><span>{o.desc}</span></button>
            : <div><strong>{o.title}</strong><span>{o.desc}</span></div>}
        </li>
      ))}
    </ul>
  )
}

// 소비 유형 카드 (11/16 v2): 마스코트(유형별 표정) · 유형 이름 · 한 줄 소개 · 예산 관리 태그 → 이렇게 본 이유(사용자 자료) → 해볼 한 가지.
// 이름 · 문장 · 태그는 서버 그대로. 마스코트는 꾸밈이라 유형 이름을 글자로도 적는다. traits(판정 기록)는 그리지 않는다
function Persona({ block }) {
  const reasons = block.reasons ?? []
  const tags = block.tags ?? []
  const label = block.label ?? '나의 소비 유형'
  return (
    <section className="mc-fx-section mc-fx-persona" aria-label={label}>
      <div className="mc-fx-persona-top">
        <Mascot mood={block.mascot} />
        <div>
          <small>{label}</small>
          <h3>{block.name}</h3>
        </div>
      </div>
      {/* 11/17: 마스코트 옆 좁은 칸 대신 소개 · 태그는 카드 폭 전체로 */}
      {block.desc && <p className="mc-fx-persona-desc">{block.desc}</p>}
      {tags.length > 0 && <ul className="mc-fx-persona-tags" aria-label="예산 관리 태그">
        {tags.map((t) => <li key={t.code} data-tag={t.code}>{t.label}</li>)}
      </ul>}
      {reasons.length > 0 && <div className="mc-fx-persona-why">
        <strong>이렇게 봤어요</strong>
        <ul className="mc-spend-lines mc-fx-lines">{reasons.map((l, i) => <li key={i}><Rich text={l} /></li>)}</ul>
      </div>}
      {block.action && <p className="mc-fx-persona-act"><strong>이번에 해볼 한 가지</strong>{block.action}</p>}
    </section>
  )
}

// 지난달과 비교 (11/17): '이름 금액 (지난달 같은 기간 금액)' 줄이 있으면 카테고리마다 묶는다 — 이름 · 이번 금액 한 줄, 지난달 금액은 그 아래, 이유 문장은 그 밑에.
// 이유 문장은 카테고리 이름으로 시작하면 그 카테고리에(예: '교통·차량 지출이 달라진 주된 이유는…'은 뒤 교통·차량 줄에), 아니면 바로 앞 카테고리에 붙인다. 글자는 서버 그대로(괄호만 화면에서 뺌)
const COMPARE_ROW = /^(.+?) ([\d,]+원) \((지난달 같은 기간 [\d,]+원)\)$/
const compareRows = (lines) => lines.some((l) => COMPARE_ROW.test(l))
// 이번 금액 옆 증감 화살표: 서버가 준 두 금액(이번 · 지난달 같은 기간)의 크기만 비교한다. 같으면 없음. 색 · 기호만으로 뜻을 전하지 않게 읽기 글자도
function Arrow({ now, before }) {
  const diff = digits(now) - digits(before)
  if (!diff) return null
  return <span className={`mc-fx-arrow ${diff > 0 ? 'mc-fx-up' : 'mc-fx-down'}`}>
    <span aria-hidden="true">{diff > 0 ? '▲' : '▼'}</span><span className="mc-fx-sr">{diff > 0 ? ' 늘었어요' : ' 줄었어요'}</span>
  </span>
}
// 예산을 관리하는 습관 (11/17): '이름: N번 늘림(+금액), M번 줄임(-금액)' 줄이 둘 이상이면 표로 (카테고리 | 늘림 | 줄임). 앞 문장은 머리말, 뒤 문장은 목록.
// 금액 · 횟수는 문장 속 글자 그대로, 늘림은 붉게 · 줄임은 파랗게 (글자로도 '늘림 · 줄임' 머리줄이 있다)
const HABIT_PART = /^(\d+)번 (늘림|줄임)\(([+\-−][\d,]+원)\)$/
const habitRow = (l) => {
  const m = l.match(/^([^:]{1,12}): (.+)$/)
  const parts = m?.[2].split(', ').map((p) => p.match(HABIT_PART))
  return parts?.every(Boolean) ? { name: m[1], parts } : null
}
const habitRows = (lines) => lines.filter(habitRow).length >= 2
function HabitLines({ lines }) {
  const first = lines.findIndex(habitRow)
  const last = lines.findLastIndex(habitRow)
  const cell = (row, way) => {
    const p = row.parts.find((x) => x[2] === way)
    return <td>{p ? <><b className={way === '늘림' ? 'mc-fx-up' : 'mc-fx-down'}>{p[3]}</b><small>{p[1]}번</small></> : <span className="mc-muted">-</span>}</td>
  }
  return <>
    {lines.slice(0, first).map((l, i) => <p key={`h${i}`} className="mc-fx-money-lead"><Rich text={l} /></p>)}
    <table className="mc-fx-habit">
      <thead><tr><th scope="col"><span className="mc-fx-sr">카테고리</span></th><th scope="col">늘림</th><th scope="col">줄임</th></tr></thead>
      <tbody>{lines.slice(first, last + 1).map(habitRow).filter(Boolean).map((row) => (
        <tr key={row.name}><th scope="row">{row.name}</th>{cell(row, '늘림')}{cell(row, '줄임')}</tr>
      ))}</tbody>
    </table>
    {last + 1 < lines.length && <ul className="mc-spend-lines mc-fx-lines">{lines.slice(last + 1).map((l, i) => <li key={i}><Rich text={l} /></li>)}</ul>}
  </>
}

// 카테고리 줄 앞의 전체 요약 (11/17): 회색 판 하나에 모으고, '이번 달 N원, 지난달 같은 기간 M원이에요.'는 지난달 → 이번 달 칸으로. 아래 카테고리 묶음과 나뉘어 보이게
const TOTALS = /^이번 달 ([\d,]+원), 지난달 같은 기간 ([\d,]+원)이에요\.$/
function CompareSummary({ lines }) {
  const totals = lines.map((l) => l.match(TOTALS)).find(Boolean)
  const rest = lines.filter((l) => !TOTALS.test(l))
  return <div className="mc-fx-compare-sum">
    {totals && <p className="mc-fx-flow" role="img" aria-label={totals[0]}>
      <span className="mc-fx-flow-step"><small>지난달 같은 기간</small> <b>{totals[2]}</b></span>
      <span className="mc-fx-flow-arrow" aria-hidden="true"> → </span>
      <span className="mc-fx-flow-step is-last"><small>이번 달</small> <b>{totals[1]}</b></span>
    </p>}
    {rest.length > 0 && <ul className="mc-spend-lines mc-fx-lines">{rest.map((l, i) => <li key={i}><Rich text={l} /></li>)}</ul>}
  </div>
}

function CompareLines({ lines }) {
  const groups = []
  const extra = []  // 첫 카테고리 줄보다 앞에 있으면서 어느 카테고리 이름으로도 시작하지 않는 문장
  const names = lines.map((l) => l.match(COMPARE_ROW)?.[1]).filter(Boolean)
  let waiting = []  // 다음 카테고리 줄을 기다리는 이유 문장
  for (const l of lines) {
    const row = l.match(COMPARE_ROW)
    if (row) { groups.push({ name: row[1], amount: row[2], before: row[3], notes: [...waiting] }); waiting = []; continue }
    const ahead = names.find((n) => l.startsWith(n) && !groups.some((g) => g.name === n))
    if (ahead) waiting.push(l)
    else if (groups.length) groups.at(-1).notes.push(l)
    else extra.push(l)
  }
  if (waiting.length) groups.at(-1).notes.push(...waiting)
  return <>
    {extra.length > 0 && <CompareSummary lines={extra} />}
    {extra.length > 0 && <p className="mc-fx-compare-label">카테고리별</p>}
    <dl className="mc-fx-money mc-fx-compare">
      {groups.map((g) => <div key={g.name}>
        <dt>{g.name}</dt>
        <dd className="mc-fx-money-amt">{g.amount}<Arrow now={g.amount} before={g.before} /></dd>
        <dd className="mc-fx-money-desc">{g.before}</dd>
        {g.notes.map((n, i) => <dd key={i} className="mc-fx-compare-why"><Rich text={n} /></dd>)}
      </div>)}
    </dl>
  </>
}

function Block({ block, onSend }) {
  if (block.kind === 'persona') return <Persona block={block} />
  if (block.kind === 'details') return <Details block={block} onSend={onSend} />
  if (block.kind === 'total') {
    return (
      <div className="mc-spend-total">
        {block.caption && <span className="mc-muted">{block.caption}</span>}
        <strong>{block.value ?? won(block.amount)}</strong>
        {block.note && <span className={`mc-muted${WAY[block.direction] ?? ''}`}>{block.note}</span>}
      </div>
    )
  }
  if (block.kind === 'rows') return block.presentation === 'period_comparison' ? <PeriodComparison block={block} /> : <Rows block={block} />
  // 한 답에 카드가 여럿이면(예: 지난달 비교 + 남은 예산) 두 번째부터의 머리줄은 구분선 + 소제목 — 빠지면 어느 기간 · 무엇의 금액인지 모른다
  if (block.kind === 'head') return <div className="mc-spend-head mc-fx-subhead"><span>{block.title}</span></div>
  if (block.kind === 'text') {
    const title = block.title ?? (ANALYSIS_TITLES.has(block.lines[0]) ? block.lines[0] : null)
    const lines = title && block.lines[0] === title ? block.lines.slice(1) : block.lines
    if (block.tone === 'key') {  // 핵심 진단: 목록 점 없이 굵은 글자 한두 문장 (분석의 답을 먼저)
      return <section className="mc-fx-section mc-fx-key"><h3>{title}</h3>{lines.map((l, i) => <KeyLine key={i} text={l} />)}</section>
    }
    // 달마다 흐름 칸은 섹션 맨 위로 (그림으로 먼저 보고 설명 문장을 읽게, 11/17)
    const shown = [...lines.filter((l) => FLOW.test(l)), ...lines.filter((l) => !FLOW.test(l))]
    const body = compareRows(lines) ? <CompareLines lines={lines} /> : habitRows(lines) ? <HabitLines lines={lines} /> : <ul className="mc-spend-lines mc-fx-lines">{shown.map((l, i) => <li key={i}><Rich text={l} /></li>)}</ul>
    return title ? <section className="mc-fx-section"><h3>{title}</h3>{body}</section> : body
  }
  if (block.kind === 'trend') return <Trend block={block} />
  if (block.kind === 'pace') return <Pace block={block} />
  if (block.kind === 'months') return <Months block={block} />
  if (block.kind === 'options') return <Options block={block} onSend={onSend} />
  if (block.kind === 'meter') {  // 생활비 예산 사용률 (홈 화면과 같은 mc-bar). 기간은 title, 초과는 글자(lines)로도 · 줄마다 한 줄
    const lines = block.lines ?? [block.text]  // 줄 나누기 전에 저장된 옛 메시지는 text 한 줄
    return (
      <div className="mc-spend-cats">
        <strong>{block.title}</strong>
        <div className={`mc-bar${block.over ? ' is-hot' : ''}`} role="progressbar" aria-label={block.title}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(Number(block.ratio), 100)} aria-valuetext={lines.join(', ')}>
          <span style={{ width: `${Math.min(Number(block.ratio), 100)}%` }}></span>
        </div>
        {lines.map((l) => <span key={l} className="mc-muted" style={{ display: 'block' }}>{l}</span>)}
      </div>
    )
  }
  return null
}

// 줄 목록 블록이 이만큼 이상이면 한 토글로 접는다 (설명 답의 근거 줄처럼 길쭉한 카드). 큰 금액 · 문장은 그대로 보인다
const FOLD_ROWS = 3

// 쪽 넘기기 (11/17): 핵심 진단으로 시작하는 긴 분석 답만 쪽으로 나눈다. 블록 순서 · 글자는 서버 그대로, 화면은 묶기만.
// 첫 쪽 = 핵심 진단 + 뒤따르는 그래프 · 목록 하나. 제목 있는 글 블록마다 새 쪽, 그래프 · 목록은 앞 쪽에 붙이고(쪽마다 하나까지) '앞으로'는 앞 쪽(월별 그래프)에, 최근 4주 선그래프는 따로
const titleOf = (b) => b.title ?? (b.kind === 'text' && ANALYSIS_TITLES.has(b.lines[0]) ? b.lines[0] : null)
// loose = 자세히 보기 팝업: 핵심 진단 없이 시작해도 나눈다 (첫 쪽 이름 = 첫 블록 제목)
function paginate(blocks, loose = false) {
  const key = blocks[0]?.kind === 'text' && blocks[0].tone === 'key'
  if (!key && !loose) return null
  const pages = []
  for (const b of blocks) {
    const t = titleOf(b)
    // 제목 있는 그래프 · 목록은 이미 그래프가 있는 쪽이면 새 쪽으로 (한 쪽에 그래프 하나 — 예: 월별 그래프 뒤 항목별 예상, 4주 추이 뒤 카테고리 막대)
    const visual = b.kind !== 'text' && t && pages.at(-1)?.blocks.some((x) => x.kind !== 'text')
    if (!pages.length || b.kind === 'trend' || visual || (b.kind === 'text' && t && t !== '앞으로')) pages.push({ title: pages.length || !key ? t ?? '요약' : '요약', blocks: [b] })
    else pages.at(-1).blocks.push(b)
  }
  if (!loose) return pages.length >= 3 ? pages : null
  // 팝업 (11/17): 글만 있는 짧은 쪽끼리는 한 쪽으로 묶는다 (쪽마다 빈 칸이 크지 않게). 글자 수 기준 = 팝업 한 쪽에 스크롤 없이 들어가는 정도
  const textOnly = (p) => p.blocks.every((x) => x.kind === 'text')
  const size = (p) => p.blocks.reduce((n, x) => n + x.lines.join('').length, 0)
  const merged = []
  for (const p of pages) {
    const last = merged.at(-1)
    if (last && textOnly(last) && textOnly(p) && size(last) + size(p) <= MERGE_CHARS) merged[merged.length - 1] = { title: `${last.title} · ${p.title}`, blocks: [...last.blocks, ...p.blocks] }
    else merged.push(p)
  }
  return merged.length >= 2 ? merged : null
}
const MERGE_CHARS = 300

function Pager({ pages, onSend }) {
  const [at, setAt] = useState(0)
  const box = useRef(null)
  const touch = useRef(null)
  const i = Math.min(at, pages.length - 1)  // 기간 전환으로 쪽 수가 줄어도 범위 안
  const go = (n) => {
    if (n < 0 || n >= pages.length) return
    setAt(n)
    // 아래 버튼을 눌러 넘겼는데 새 쪽 머리가 화면 위로 지나가 있으면 쪽 머리로 올린다
    const scroller = box.current?.closest('.mc-sheet, main')
    if (scroller?.classList.contains('mc-sheet')) return void (scroller.scrollTop = 0)  // 팝업 안: 새 쪽은 맨 위부터
    const top = box.current?.getBoundingClientRect().top
    const log = scroller?.getBoundingClientRect().top ?? 0
    if (top < log) box.current.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  }
  const start = (e) => { touch.current = [e.touches[0].clientX, e.touches[0].clientY] }
  const end = (e) => {
    if (!touch.current) return
    const dx = e.changedTouches[0].clientX - touch.current[0], dy = e.changedTouches[0].clientY - touch.current[1]
    touch.current = null
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) go(i + (dx < 0 ? 1 : -1))  // 세로 스크롤과 헷갈리지 않게 확실한 가로 밀기만
  }
  return (
    <section className="mc-fx-pager" ref={box} aria-roledescription="쪽 넘기기" aria-label="분석 내용" onTouchStart={start} onTouchEnd={end}>
      <div className="mc-fx-page" key={i} role="group" aria-label={`${pages.length}쪽 중 ${i + 1}쪽: ${pages[i].title}`}>
        {pages[i].blocks.map((b, j) => <Block key={j} block={b} onSend={onSend} />)}
      </div>
      <div className="mc-fx-pager-nav">
        <button type="button" onClick={() => go(i - 1)} disabled={i === 0} aria-label="이전 쪽"><Icon name="ChevronLeft" /></button>
        <span aria-live="polite">
          <span className="mc-fx-pager-dots" aria-hidden="true">{pages.map((p, n) => <i key={n} className={n === i ? 'on' : undefined} />)}</span>
          <span>{i + 1}/{pages.length} · {pages[i].title}</span>
        </span>
        <button type="button" onClick={() => go(i + 1)} disabled={i === pages.length - 1} aria-label="다음 쪽"><Icon name="ChevronRight" /></button>
      </div>
    </section>
  )
}

// 자세히 보기 (11/17): 화면(팝업 틀) 안이고 3쪽 이상이면 버튼 → 가운데 팝업에서 쪽 넘기기. 아니면(테스트 · 짧은 내용) 예전처럼 접기
function Details({ block, onSend }) {
  const popups = usePopup()
  const title = block.title ?? '자세히 보기'
  const children = block.blocks ?? []
  const pages = popups && paginate(children, true)
  if (!pages) return (
    <details className="mc-fx-fold">
      <summary>{title}</summary>
      {children.map((child, i) => <Block key={i} block={child} onSend={onSend} />)}
    </details>
  )
  const send = onSend && ((ask) => { popups.closePopup(); onSend(ask) })
  const open = () => popups.openPopup(
    <Popup label={title} dialog>
      <div className="mc-fx-detail-pop"><h2>{title}</h2><Pager pages={pages} onSend={send} /></div>
    </Popup>)
  return <button type="button" className="mc-fx-detail-open" onClick={open} aria-haspopup="dialog">{title}<Icon name="ChevronRight" size={18} /></button>
}

function ViewCard({ content, title, onSend }) {
  // 기간 전환 (11/11): 이번 달 조회에 예산 주기 판(content.alt)이 함께 오면 [이번 달 | 예산 주기]로 바꿔 본다.
  // 두 판 모두 서버가 계산해 준 것 그대로 — 화면은 어느 판을 그릴지만 고른다. alt.first = 예산 조정 중에 물은 답이라 예산 주기를 먼저
  const alt = content.alt?.view?.length ? content.alt : null
  const [cycle, setCycle] = useState(Boolean(alt?.first))
  const shown = alt && cycle ? { ...content, view: alt.view, chart: alt.chart, lead: alt.lead ?? [], notices: alt.notices ?? [] } : content
  const [head, ...blocks] = shown.view[0].kind === 'head' ? shown.view : [null, ...shown.view]
  const rows = blocks.filter((b) => b.kind === 'rows' && b.presentation !== 'period_comparison')
  const fold = rows.length >= FOLD_ROWS
  const first = blocks.indexOf(rows[0])
  const pages = paginate(blocks)
  const [big, small] = head ? headParts(head.title, title) : [title, null]
  const answer = !cycle && blocks.some((b) => b.kind === 'total') && firstSentence(shown.lines ?? [])
  return (
    <div className="mc-spend" aria-label={`${title} 결과`}>
      <div className="mc-spend-head"><span>{big}</span>{small && <small>{small}</small>}</div>
      {/* 11/18 검수: 금액 한 장짜리 답이면 서버의 답 문장을 먼저 (숫자만 있으면 대답처럼 안 읽힌다) */}
      {answer && <p className="mc-fx-answer">{answer}</p>}
      {alt && (
        <div className="mc-tabs mc-fx-period" role="group" aria-label="기간 기준">
          <button type="button" aria-pressed={!cycle} onClick={() => setCycle(false)}>{alt.baseLabel}</button>
          <button type="button" aria-pressed={cycle} onClick={() => setCycle(true)}>{alt.label}</button>
        </div>
      )}
      {pages ? <Pager pages={pages} onSend={onSend} /> : blocks.map((b, i) => {
        if (!fold || !rows.includes(b)) return <Block key={i} block={b} onSend={onSend} />
        if (i !== first) return null
        return (
          <details key={i} className="mc-fx-fold">
            <summary>근거 자세히 보기 ({rows.map((r) => r.title).filter(Boolean).length}가지)</summary>
            {rows.map((r, j) => <Block key={j} block={r} />)}
          </details>
        )
      })}
      {shown.chart && <Chart chart={shown.chart} />}
      <Notes content={shown} />
    </div>
  )
}

// 긴 공고 원문은 접어 두되 끝까지 읽을 수 있게 한다
function PolicyText({ text }) {
  if (text.length <= 350) return text
  return <details className="mc-policy-detail"><summary>{text.slice(0, 180)}… <strong>전체 내용 보기</strong></summary><p>{text}</p></details>
}

// DB 링크 줄의 주소만 연결한다. 일반 문장·HTML은 해석하지 않는다.
function policyHref(href) {
  if (!/^https?:\/\/[^\s<>]+$/i.test(href)) return null
  try {
    const url = new URL(href)
    return ['http:', 'https:'].includes(url.protocol) && url.hostname ? href : null
  } catch {
    return null
  }
}

// "라벨: 값" 줄은 2열 표(dl), 라벨 없는 줄(나이 상한·목록 제외 경고)은 표 위 안내, 공고 주소는 버튼으로 나눈다
// head(번호 · 정책명)는 표와 같은 회색 영역 맨 위에 둔다
function PolicyRows({ lines, head }) {
  const notes = [], rows = []
  let href = null
  for (const line of lines) {
    const m = line.match(/^([^:\n]{1,12}): ([\s\S]*)$/)
    if (!m) notes.push(line)
    else if (m[1] === '링크' && policyHref(m[2])) href = m[2]
    else rows.push([m[1], m[2]])
  }
  return <>
    <div className="mc-policy-box">
      {head && <div className="mc-policy-head">{head}</div>}
      {notes.map((text, i) => <p key={`n-${i}`} className="mc-policy-note"><PolicyText text={text} /></p>)}
      {rows.length > 0 && <dl className="mc-policy-table">{rows.map(([label, value], i) => (
        <div key={i}><dt>{label}</dt><dd><PolicyText text={value} /></dd></div>
      ))}</dl>}
    </div>
    {href && <a className="mc-secondary mc-policy-link" href={href} target="_blank" rel="noopener noreferrer"
      aria-label="공고 보기 (새 탭에서 열기)">공고 보기 ↗</a>}
  </>
}

// 정책 저장은 대화의 turn 선택지가 아니라 정책 ID를 쓰는 멱등 북마크 API다.
// 지난 답변에서도 저장할 수 있고, 대화 상태나 현재 질문을 바꾸지 않는다.
function PolicySections({ sections, choices, onDetail }) {
  const [index, setIndex] = useState(0)
  const [changes, setChanges] = useState({})
  const pending = useRef(new Set())
  const bookmarks = useApi('/api/policies/saved/')
  const section = sections[Math.min(index, sections.length - 1)]
  const id = section.policyNo
  const title = policyTitle(section.title)
  const number = policyNumber(section.title)
  const state = changes[id] ?? {}
  const saved = state.saved || bookmarks.data?.items?.some((item) => item.policyNo === id)
  // [자세히 보기]는 그 답변의 선택지(detail:정책번호)를 고른다. 지난 답변의 카드도 서버가 받아 준다 (이름이 같은 정책과 섞이지 않게 번호로)
  const detail = choices?.find((c) => c.value === `${DETAIL}${id}`)
  const save = async () => {
    if (!id || saved || pending.current.has(id)) return
    pending.current.add(id)
    setChanges((prev) => ({ ...prev, [id]: { busy: true } }))
    try {
      await apiSend(`/api/policies/${encodeURIComponent(id)}/saved/`, 'PUT')
      setChanges((prev) => ({ ...prev, [id]: { saved: true } }))
      bookmarks.reload()
    } catch (error) {
      setChanges((prev) => ({ ...prev, [id]: { error: error.message || '저장하지 못했어요. 다시 시도해 주세요.' } }))
    } finally {
      pending.current.delete(id)
    }
  }
  return <div className="mc-spend-cats mc-policy-cards">
    <article aria-label={title}>
      <PolicyRows lines={section.lines} head={<>
        {number && <div className="mc-eyebrow">{number}번 정책</div>}
        <h3 className="mc-policy-title">{title}</h3>
      </>} />
      {sections.length > 1 && <div className="mc-policy-nav" role="group" aria-label="추천 정책 넘기기">
        <button type="button" className="mc-secondary" disabled={index === 0} onClick={() => setIndex((n) => n - 1)}>이전 정책</button>
        <span role="status">{index + 1} / {sections.length}</span>
        <button type="button" className="mc-secondary" disabled={index === sections.length - 1} onClick={() => setIndex((n) => n + 1)}>다음 정책</button>
      </div>}
      {id && <div className="mc-policy-actions">
        {detail && <button type="button" className="mc-secondary" disabled={!onDetail} onClick={() => onDetail(detail)}
          aria-label={`${title} 자세히 보기`}>자세히 보기</button>}
        <button type="button" className="mc-secondary mc-policy-save" onClick={save}
          disabled={Boolean(saved || state.busy || bookmarks.loading || bookmarks.error)}
          aria-label={`${title} ${saved ? '저장됨' : '저장'}`}>{saved ? '저장됨' : state.busy ? '저장 중…' : '저장'}</button>
      </div>}
      {state.error && <p role="alert">{state.error}</p>}
      {bookmarks.error && <p role="alert">저장 상태를 확인하지 못했어요. <button type="button" className="mc-link" onClick={bookmarks.reload}>다시 확인</button></p>}
    </article>
  </div>
}

// 분석 결과 카드 (SpendingCard 와 같은 틀 · 클래스). 카드 보기가 있으면 그것으로, 없으면(설명 답변 등) 문장 목록
// 돈 종류 나눠 보기 (11/17, 예: '여윳돈 얼마나 있어?'): '이름: 금액 — 설명' 줄이 둘 이상이면 이름 · 설명은 왼쪽, 금액은 오른쪽 줄로.
// 글자는 서버 문장 그대로(': ' · ' — ' 구분 기호만 화면에서 뺌), 읽는 순서도 이름 → 금액 → 설명 그대로. 금액 대신 안내 문장이 온 줄(확인 필요)은 설명 자리에. 앞 · 뒤 일반 문장은 머리말 · 안내로
const MONEY_ROW = /^([^:]{1,24}): (?:([\d,]+원) — (.+)|(.+))$/
const moneyRows = (lines = []) => lines.filter((l) => /^[^:]{1,24}: [\d,]+원 — /.test(l)).length >= 2
function MoneyLines({ lines }) {
  const first = lines.findIndex((l) => MONEY_ROW.test(l))
  const last = lines.findLastIndex((l) => MONEY_ROW.test(l))
  return <>
    {lines.slice(0, first).map((l, i) => <p key={`h${i}`} className="mc-fx-money-lead">{l}</p>)}
    <dl className="mc-fx-money">
      {lines.slice(first, last + 1).map((l, i) => {
        const [, name, amount, desc, other] = l.match(MONEY_ROW) ?? [null, null, null, null, l]
        return <div key={i} className={amount ? undefined : 'is-pending'}>
          {name && <dt>{name}</dt>}
          {amount && <dd className="mc-fx-money-amt">{amount}</dd>}
          <dd className="mc-fx-money-desc">{desc ?? other}</dd>
        </div>
      })}
    </dl>
    {lines.slice(last + 1).map((l, i) => <p key={`n${i}`} className="mc-note mc-note-flush">{l}</p>)}
  </>
}

function ResultCard({ content, title, onDetail, onSend }) {
  if (content.view?.length) return <ViewCard content={content} title={title} onSend={onSend} />
  const notices = content.notices ?? []
  return (
    <div className="mc-spend" aria-label={`${title} 결과`}>
      <div className="mc-spend-head"><span>{title}</span></div>
      {content.agent === 'policy' && <Lead items={content.lead ?? []} />}
      {content.agent !== 'policy' && moneyRows(content.lines)
        ? <MoneyLines lines={content.lines} />
        : content.lines.length > 0 && <ul className="mc-spend-lines">{content.lines.map((l, i) => <li key={i}><Boxed text={l} on={content.agent === 'policy'} /></li>)}</ul>}
      {content.agent === 'policy' && (content.examples ?? []).length > 0 && <p className="mc-examples">예) {content.examples.join(' · ')}</p>}
      {content.agent === 'policy' && content.sections?.some((s) => s.policyNo)
        ? <PolicySections sections={content.sections} choices={content.choices} onDetail={onDetail} />
        : (content.sections ?? []).length > 0 && (
        <div className="mc-spend-cats">
          {content.sections.map((s, i) => (
            <div key={`sec-${i}`}>
              {content.agent === 'policy'
                ? <PolicyRows lines={s.lines} head={<strong className="mc-policy-title">{policyTitle(s.title)}</strong>} />
                : <>
                  <strong>{s.title}</strong>
                  {s.lines.length > 0 && <ul className="mc-spend-lines">{s.lines.map((l, j) => <li key={j}>{l}</li>)}</ul>}
                </>}
            </div>
          ))}
        </div>
      )}
      {content.chart && <Chart chart={content.chart} />}
      {content.agent === 'policy' ? notices.map((n, i) => <p key={`notice-${i}`} className="mc-note mc-note-flush">{n}</p>) : <Notes content={content} />}
    </div>
  )
}

// 짧은 답 · 질문 (채팅 말풍선)
// 청년 정책 문장의 `…` 부분(찾을 정책 · 지역)을 파란 글자로. 다른 담당 문장은 그대로
function Boxed({ text, on }) {
  if (!on) return text
  return text.split(/`([^`]+)`/).map((part, i) => i % 2 ? <span key={i} className="mc-pill">{part}</span> : part)
}

// children = 질문과 같은 말풍선 안에 둘 입력 (청년 정책 지역 드롭다운)
function Bubble({ content, children }) {
  const lines = content.lines ?? (content.text ? [content.text] : [])
  const notices = content.notices ?? []
  // 선택지가 붙은 여러 줄 질문(예: '…은 아직 어려워요.' + '대신 … 확인할까요?'): 한 문단으로 잇고 마지막 줄(묻는 말)만 굵게
  // 청년 정책은 줄마다 나눠 굵게 하지 않는다 ('‘월세 지원’ 정책을 찾아볼게요.' / '가입 지역인 …로 찾으시는 거 맞나요?')
  const asking = content.agent !== 'policy' && (content.choices ?? []).length > 0 && lines.length > 1 && lines.at(-1).endsWith('?')
  return (
    <div className="mc-bubble">
      {(content.lead ?? []).map((l, i) => <span key={`lead-${i}`}><strong>안내: {l}</strong><br /></span>)}
      {asking
        ? <>{lines.slice(0, -1).join(' ')} <strong>{lines.at(-1)}</strong></>
        : lines.map((l, i) => <span key={`line-${i}`}>{i > 0 && <br />}<Boxed text={l} on={content.agent === 'policy'} /></span>)}
      {!lines.length && !(content.lead ?? []).length && !notices.length && '(보일 답변이 없어요)'}
      {notices.map((n, i) => <span key={`notice-${i}`}><br /><small>{n}</small></span>)}
      {content.agent === 'policy' && (content.examples ?? []).length > 0 && <p className="mc-examples">예) {content.examples.join(' · ')}</p>}
      {children}
    </div>
  )
}

export default function ChatReply({ content, title, active, onPick, onApprove, onSend, onSalaryEdit, onSalaryReview, onDetail, navigation, children }) {
  const adjustment = content.budgetAdjustment
  const ended = adjustment && (adjustment.status === 'ended' || ENDED_ADJUSTMENTS.has(adjustment.status) || ENDED_ADJUSTMENTS.has(adjustment.proposal?.state))
  return <>
    {adjustment
      ? <BudgetAdjustment content={content} active={active} onPick={onPick} onApprove={onApprove} onSend={onSend} onSalaryEdit={onSalaryEdit} onSalaryReview={onSalaryReview} navigation={navigation} />
      : content.result ? <ResultCard content={content} title={title} onDetail={onDetail} onSend={active && onSend ? onSend : null} /> : <Bubble content={content}>{children}</Bubble>}
    {content.agent !== 'policy' && !ended && content.examples?.length > 0 && <div className={adjustment ? "mc-fx-next" : undefined}>
      <p className="mc-note">{adjustment ? '이렇게 이어서 말해 보세요.' : '이런 질문으로 이어서 볼 수 있어요.'}</p>
      <Choices choices={content.examples.map((text) => ({ value: text, label: text }))} active={active && Boolean(onSend)}
        onPick={(choice) => onSend(choice.value)} />
    </div>}
  </>
}

// 답변 선택지 (기존 칩 모양). active = 마지막 답변의 선택지만 누를 수 있다 (서버도 지난 선택지는 409)
// scroll = 한 줄로 두고 옆으로 밀어서 보기 (청년 정책: 추천 뒤 버튼이 4~5개라 줄이 늘어지지 않게)
export function Choices({ choices, active, onPick, scroll = false }) {
  const bar = useRef(null)
  // 스크롤바를 숨겼으니 PC 마우스 세로 휠을 가로 이동으로 바꾼다. React onWheel 은 passive 라 preventDefault 가 안 돼 직접 단다
  useEffect(() => {
    const el = bar.current
    if (!scroll || !el) return
    const onWheel = (e) => {
      // Ctrl+휠 · 트랙패드 확대(ctrlKey) · 가로 휠 · 넘칠 게 없으면 브라우저 기본 동작 그대로
      if (e.ctrlKey || Math.abs(e.deltaY) <= Math.abs(e.deltaX) || el.scrollWidth <= el.clientWidth) return
      e.preventDefault()
      // 이동량 단위: 0 픽셀 · 1 줄(줄 높이, 'normal' 이면 16px) · 2 페이지(선택지 줄 너비)
      const unit = e.deltaMode === 1 ? parseFloat(getComputedStyle(el).lineHeight) || 16 : e.deltaMode === 2 ? el.clientWidth : 1
      el.scrollLeft += e.deltaY * unit
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [scroll, choices?.length])
  if (!choices?.length) return null
  return (
    <div ref={bar} className={`mc-chipbar mc-fx-choices${scroll ? ' is-scroll' : ''}`} role="group" aria-label="선택지">
      {choices.map((c) => (
        <button key={c.value} type="button" disabled={!active} aria-disabled={!active || undefined} onClick={() => onPick(c)}>
          {c.label}
        </button>
      ))}
    </div>
  )
}

// 끝부분만 빠진 소비 답(content.syncedUntil) 아래 [내역 다시 불러오기] (11/13). 불러오기 · 같은 질문 다시 보내기는 AssistantPage 가 한다
// state = null(버튼) · { failed: false }(불러오는 중) · { failed: true }(실패 + 다시 시도)
export function Resync({ state, disabled, onRun }) {
  if (state && !state.failed) return <div className="mc-asst-route is-busy" role="status"><Icon name="Loader" /> 내역 불러오는 중…</div>
  if (state?.failed) {
    return <div className="mc-msg bot"><div className="mc-bubble" role="alert">
      <p>불러오지 못했어요</p>
      <button type="button" className="mc-btn" disabled={disabled} onClick={onRun}>다시 시도</button>
    </div></div>
  }
  return (
    <div className="mc-chipbar mc-fx-choices" role="group" aria-label="내역 다시 불러오기">
      <button type="button" disabled={disabled} onClick={onRun}>내역 다시 불러오기</button>
    </div>
  )
}
