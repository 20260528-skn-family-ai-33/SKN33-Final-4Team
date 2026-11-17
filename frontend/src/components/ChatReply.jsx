// 챗봇 답변 한 개 (pages/AssistantPage.jsx). 옛 개발용 로컬 채팅(local_chat.html, 11/10 삭제)의 bot() · chart() 를 옮겼다 (MEMO 25절 2단계)
// content = 서버 봇 메시지 content: { text, lead, lines, sections, notices, choices, chart?, view?, turn, agent, inputs, result } (MEMO 22-3 · 22-12 · 22-13)
// 디자인은 기존 클래스만: 분석 결과(result) = 소비 분석 카드(mc-spend*), 그 밖의 답 = 채팅 말풍선(mc-bubble)
// - 분석 안내·계산 기준은 카드 맨 아래에서 펼쳐 본다. 금액 옆의 자료 제한 표시는 유지한다
// - 그래프: 상위 5줄은 바로, 나머지는 접기. 막대 길이 = 서버가 준 비율(share), 금액 · 비율은 글자로도. 화면에서 다시 계산하지 않는다
// - 옛 메시지 · 꺼짐(legacy) 엔진 답(text · choices 만 있는 것)도 말풍선으로 그린다
import { useEffect, useRef, useState } from 'react'
import { won } from '../utils/money.js'
import { ENDED_ADJUSTMENTS } from '../utils/adjust.js'
import Mascot from './Mascot.jsx'
import { apiSend, useApi } from '../utils/api.js'
import { DETAIL, policyNumber, policyTitle } from '../utils/policyCards.js'
import BudgetAdjustment from './BudgetAdjustment.jsx'
import Icon from './Icon.jsx'
import '../styles/policies.css'

const Lead = ({ items }) => items.map((l, i) => (
  // span 을 쓰지 않는다: mc-spend-unc 의 마지막 span 은 금액 칸용 줄바꿈 금지라 긴 문장이 카드 밖으로 나간다
  <p key={`lead-${i}`} className="mc-spend-unc mc-fx-lead" role="note"><strong>안내:</strong> {l}</p>
))

function Notes({ content }) {
  const oldUnclassified = content.chart?.rows?.find(isUnclassified) ?? content.view?.flatMap((b) => b.rows ?? []).find(isUnclassified)
  const unclassified = content.chart?.unclassified ?? (oldUnclassified && `확인 필요 ${won(oldUnclassified.amount)}은 총액에 포함하고 주요 카테고리에서 제외했어요.`)
  const items = [...new Set([...(content.lead ?? []), content.chart?.basis, content.chart?.limited, ...(content.notices ?? []), unclassified].filter(Boolean))]
  if (!items.length) return null
  return <details className="mc-fx-notes">
    <summary>분석 기준·안내</summary>
    {items.map((text, i) => <p key={i} className="mc-note">{text}</p>)}
  </details>
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
function Rich({ text }) {
  if (typeof text !== 'string') return text
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
      <span className="mc-fx-pace-amt">{row.ratio}%<small>{won(row.spent)} / {won(row.allocated)}</small></span>
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
  return (
    <div className="mc-spend-cats mc-fx-rows">
      {block.title && <strong>{block.title}</strong>}
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
          {block.desc && <p>{block.desc}</p>}
          {tags.length > 0 && <ul className="mc-fx-persona-tags" aria-label="예산 관리 태그">
            {tags.map((t) => <li key={t.code} data-tag={t.code}>{t.label}</li>)}
          </ul>}
        </div>
      </div>
      {reasons.length > 0 && <div className="mc-fx-persona-why">
        <strong>이렇게 봤어요</strong>
        <ul className="mc-spend-lines mc-fx-lines">{reasons.map((l, i) => <li key={i}><Rich text={l} /></li>)}</ul>
      </div>}
      {block.action && <p className="mc-fx-persona-act"><strong>이번에 해볼 한 가지</strong>{block.action}</p>}
    </section>
  )
}

function Block({ block, onSend }) {
  if (block.kind === 'persona') return <Persona block={block} />
  if (block.kind === 'details') return (
    <details className="mc-fx-fold">
      <summary>{block.title ?? '자세히 보기'}</summary>
      {(block.blocks ?? []).map((child, i) => <Block key={i} block={child} onSend={onSend} />)}
    </details>
  )
  if (block.kind === 'total') {
    return (
      <div className="mc-spend-total">
        {block.caption && <span className="mc-muted">{block.caption}</span>}
        <strong>{block.value ?? won(block.amount)}</strong>
        {block.note && <span className={`mc-muted${WAY[block.direction] ?? ''}`}>{block.note}</span>}
      </div>
    )
  }
  if (block.kind === 'rows') return <Rows block={block} />
  // 한 답에 카드가 여럿이면(예: 지난달 비교 + 남은 예산) 두 번째부터의 머리줄은 구분선 + 소제목 — 빠지면 어느 기간 · 무엇의 금액인지 모른다
  if (block.kind === 'head') return <div className="mc-spend-head mc-fx-subhead"><span>{block.title}</span></div>
  if (block.kind === 'text') {
    const title = block.title ?? (ANALYSIS_TITLES.has(block.lines[0]) ? block.lines[0] : null)
    const lines = title && block.lines[0] === title ? block.lines.slice(1) : block.lines
    if (block.tone === 'key') {  // 핵심 진단: 목록 점 없이 굵은 글자 한두 문장 (분석의 답을 먼저)
      return <section className="mc-fx-section mc-fx-key"><h3>{title}</h3>{lines.map((l, i) => <KeyLine key={i} text={l} />)}</section>
    }
    const body = <ul className="mc-spend-lines mc-fx-lines">{lines.map((l, i) => <li key={i}><Rich text={l} /></li>)}</ul>
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

function ViewCard({ content, title, onSend }) {
  // 기간 전환 (11/11): 이번 달 조회에 예산 주기 판(content.alt)이 함께 오면 [이번 달 | 예산 주기]로 바꿔 본다.
  // 두 판 모두 서버가 계산해 준 것 그대로 — 화면은 어느 판을 그릴지만 고른다. alt.first = 예산 조정 중에 물은 답이라 예산 주기를 먼저
  const alt = content.alt?.view?.length ? content.alt : null
  const [cycle, setCycle] = useState(Boolean(alt?.first))
  const shown = alt && cycle ? { ...content, view: alt.view, chart: alt.chart, lead: alt.lead ?? [], notices: alt.notices ?? [] } : content
  const [head, ...blocks] = shown.view[0].kind === 'head' ? shown.view : [null, ...shown.view]
  const rows = blocks.filter((b) => b.kind === 'rows')
  const fold = rows.length >= FOLD_ROWS
  const first = blocks.indexOf(rows[0])
  return (
    <div className="mc-spend" aria-label={`${title} 결과`}>
      <div className="mc-spend-head"><span>{head ? head.title : title}</span>{head && <small>{title}</small>}</div>
      {alt && (
        <div className="mc-tabs mc-fx-period" role="group" aria-label="기간 기준">
          <button type="button" aria-pressed={!cycle} onClick={() => setCycle(false)}>{alt.baseLabel}</button>
          <button type="button" aria-pressed={cycle} onClick={() => setCycle(true)}>{alt.label}</button>
        </div>
      )}
      {blocks.map((b, i) => {
        if (!fold || b.kind !== 'rows') return <Block key={i} block={b} onSend={onSend} />
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
function ResultCard({ content, title, onDetail, onSend }) {
  if (content.view?.length) return <ViewCard content={content} title={title} onSend={onSend} />
  const notices = content.notices ?? []
  return (
    <div className="mc-spend" aria-label={`${title} 결과`}>
      <div className="mc-spend-head"><span>{title}</span></div>
      {content.agent === 'policy' && <Lead items={content.lead ?? []} />}
      {content.lines.length > 0 && <ul className="mc-spend-lines">{content.lines.map((l, i) => <li key={i}><Boxed text={l} on={content.agent === 'policy'} /></li>)}</ul>}
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
      {content.agent === 'policy' ? notices.map((n, i) => <p key={`notice-${i}`} className="mc-note" style={{ padding: 0 }}>{n}</p>) : <Notes content={content} />}
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
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX) || el.scrollWidth <= el.clientWidth) return  // 가로 휠·트랙패드 · 넘칠 게 없으면 그대로
      e.preventDefault()
      el.scrollLeft += e.deltaY
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
