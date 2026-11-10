// 챗봇 답변 한 개 (pages/AssistantPage.jsx). 옛 개발용 로컬 채팅(local_chat.html, 11/10 삭제)의 bot() · chart() 를 옮겼다 (MEMO 25절 2단계)
// content = 서버 봇 메시지 content: { text, lead, lines, sections, notices, choices, chart?, view?, turn, agent, inputs, result } (MEMO 22-3 · 22-12 · 22-13)
// 디자인은 기존 클래스만: 분석 결과(result) = 소비 분석 카드(mc-spend*), 그 밖의 답 = 채팅 말풍선(mc-bubble)
// - 핵심 안내(lead)는 서버가 나눈 칸 그대로 맨 위에. '안내:' 글자 + 굵게 (색만으로 구분하지 않음)
// - 그래프: 상위 5줄은 바로, 나머지는 접기. 막대 길이 = 서버가 준 비율(share), 금액 · 비율은 글자로도. 화면에서 다시 계산하지 않는다
// - 옛 메시지 · 꺼짐(legacy) 엔진 답(text · choices 만 있는 것)도 말풍선으로 그린다
import { won } from '../utils/money.js'

const Lead = ({ items }) => items.map((l, i) => (
  // span 을 쓰지 않는다: mc-spend-unc 의 마지막 span 은 금액 칸용 줄바꿈 금지라 긴 문장이 카드 밖으로 나간다
  <p key={`lead-${i}`} className="mc-spend-unc mc-fx-lead" role="note"><strong>안내:</strong> {l}</p>
))

// 카테고리 막대: 금액이 큰 상위 SHOWN 줄은 바로, 나머지는 '나머지 N개 더 보기'로 접는다 (순서 · 금액 · 비율은 서버 그대로, 화면은 자르기만)
const SHOWN = 5

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
  const top = chart.rows.slice(0, SHOWN)
  const rest = chart.rows.slice(SHOWN)
  return (
    <div className="mc-fx-chart">
      <p className="mc-note" style={{ padding: 0 }}>{chart.basis}</p>
      {chart.limited && <p className="mc-note" style={{ padding: 0 }}>{chart.limited}</p>}
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

function Rows({ block }) {
  return (
    <div className="mc-spend-cats">
      {block.title && <strong>{block.title}</strong>}
      {block.rows.map((r) => (
        <div key={r.name} className="mc-spend-cat">
          <span className="mc-spend-cname">{r.name}</span>
          {r.badge
            ? <span className={`mc-spend-diff${BADGE[r.badge.direction]}`}>{r.badge.text}</span>
            : r.ratio != null
              ? <span className="mc-spend-cbar" role="img" aria-label={`${r.name} ${won(r.amount)}`}><span style={{ width: `${Number(r.ratio)}%` }}></span></span>
              : <span></span>}
          <span className={`mc-spend-camt${WAY[r.direction] ?? ''}`}>{r.value ?? won(r.amount)}{r.note && <small>{r.note}</small>}</span>
        </div>
      ))}
    </div>
  )
}

function Block({ block }) {
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
  if (block.kind === 'text') return <ul className="mc-spend-lines">{block.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
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

function ViewCard({ content, title }) {
  const [head, ...blocks] = content.view[0].kind === 'head' ? content.view : [null, ...content.view]
  const rows = blocks.filter((b) => b.kind === 'rows')
  const fold = rows.length >= FOLD_ROWS
  const first = blocks.indexOf(rows[0])
  return (
    <div className="mc-spend" aria-label={`${title} 결과`}>
      <div className="mc-spend-head"><span>{head ? head.title : title}</span>{head && <small>{title}</small>}</div>
      <Lead items={content.lead ?? []} />
      {blocks.map((b, i) => {
        if (!fold || b.kind !== 'rows') return <Block key={i} block={b} />
        if (i !== first) return null
        return (
          <details key={i} className="mc-fx-fold">
            <summary>근거 자세히 보기 ({rows.map((r) => r.title).filter(Boolean).length}가지)</summary>
            {rows.map((r, j) => <Block key={j} block={r} />)}
          </details>
        )
      })}
      {content.chart && <Chart chart={content.chart} />}
      {(content.notices ?? []).map((n, i) => <p key={`notice-${i}`} className="mc-note" style={{ padding: 0 }}>{n}</p>)}
    </div>
  )
}

// 분석 결과 카드 (SpendingCard 와 같은 틀 · 클래스). 카드 보기가 있으면 그것으로, 없으면(설명 답변 등) 문장 목록
function ResultCard({ content, title }) {
  if (content.view?.length) return <ViewCard content={content} title={title} />
  const notices = content.notices ?? []
  return (
    <div className="mc-spend" aria-label={`${title} 결과`}>
      <div className="mc-spend-head"><span>{title}</span></div>
      <Lead items={content.lead ?? []} />
      {content.lines.length > 0 && <ul className="mc-spend-lines">{content.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>}
      {(content.sections ?? []).length > 0 && (
        <div className="mc-spend-cats">
          {content.sections.map((s, i) => (
            <div key={`sec-${i}`}>
              <strong>{s.title}</strong>
              {s.lines.length > 0 && <ul className="mc-spend-lines">{s.lines.map((l, j) => <li key={j}>{l}</li>)}</ul>}
            </div>
          ))}
        </div>
      )}
      {content.chart && <Chart chart={content.chart} />}
      {notices.map((n, i) => <p key={`notice-${i}`} className="mc-note" style={{ padding: 0 }}>{n}</p>)}
    </div>
  )
}

// 짧은 답 · 질문 (채팅 말풍선)
function Bubble({ content }) {
  const lines = content.lines ?? (content.text ? [content.text] : [])
  const notices = content.notices ?? []
  // 선택지가 붙은 여러 줄 질문(예: '…은 아직 어려워요.' + '대신 … 확인할까요?'): 한 문단으로 잇고 마지막 줄(묻는 말)만 굵게
  const asking = (content.choices ?? []).length > 0 && lines.length > 1 && lines.at(-1).endsWith('?')
  return (
    <div className="mc-bubble">
      {(content.lead ?? []).map((l, i) => <span key={`lead-${i}`}><strong>안내: {l}</strong><br /></span>)}
      {asking
        ? <>{lines.slice(0, -1).join(' ')} <strong>{lines.at(-1)}</strong></>
        : lines.map((l, i) => <span key={`line-${i}`}>{i > 0 && <br />}{l}</span>)}
      {!lines.length && !(content.lead ?? []).length && !notices.length && '(보일 답변이 없어요)'}
      {notices.map((n, i) => <span key={`notice-${i}`}><br /><small>{n}</small></span>)}
    </div>
  )
}

export default function ChatReply({ content, title }) {
  return content.result ? <ResultCard content={content} title={title} /> : <Bubble content={content} />
}

// 답변 선택지 (기존 칩 모양). active = 마지막 답변의 선택지만 누를 수 있다 (서버도 지난 선택지는 409)
export function Choices({ choices, active, onPick }) {
  if (!choices?.length) return null
  return (
    <div className="mc-chipbar mc-fx-choices" role="group" aria-label="선택지">
      {choices.map((c) => (
        <button key={c.value} type="button" disabled={!active} aria-disabled={!active || undefined} onClick={() => onPick(c)}>
          {c.label}
        </button>
      ))}
    </div>
  )
}
