// 챗봇 소비 분석 결과 카드 — POST /api/chat/messages/ 응답의 bot 메시지 content (contentType: card, kind: spending)
// 칸 이름은 docs/api/chat.md '소비 분석 카드' 그대로 (결정 J · S · N · Z②)
//   total · fixedAmount · unclassifiedAmount 는 항상 전체 소비 기준. scope: category 면 categories 에 그 카테고리 1개
//   comparison.available = false 면 비교 칸(previousTotal · previousAmount · prevDiff · prevDiffRate)이 모두 null
//   topSpending 은 '확인 필요'를 뺀 순위, scope: category 이거나 소비 0원이면 null
//   소비가 0원이면 0원 카드 + "이 기간엔 소비가 없어요." (Z②)
import { useNavigate } from 'react-router'
import { won } from '../utils/money.js'
import { PATHS } from '../routes.js'

const NO_COMPARE = {
  not_requested: null,  // 비교를 묻지 않았으면 아무 말도 안 함
  previous_incomplete: '지난 기간 자료가 다 모이지 않아 비교하지 않았어요.',
}

// "2026-10-01" → "10월 1일"
const md = (d) => { const [, m, day] = d.split('-').map(Number); return `${m}월 ${day}일` }
// "2026-10-01"~"2026-10-21" → "10월 1일 ~ 21일" (같은 달이면 뒤쪽 월 생략)
function range(p) {
  const [a, b] = [p.from.split('-'), p.to.split('-')]
  return a[1] === b[1] ? `${md(p.from)} ~ ${Number(b[2])}일` : `${md(p.from)} ~ ${md(p.to)}`
}
const signed = (n) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${won(Math.abs(n))}`

export default function SpendingCard({ card }) {
  const navigate = useNavigate()
  const { scope, period, compare, comparison, total, fixedAmount, previousTotal, categories, topSpending, unclassifiedAmount, lines, updatedAt } = card
  const one = scope === 'category' ? categories[0] : null
  const empty = total === 0
  const living = total - fixedAmount
  const diff = comparison.available && previousTotal != null ? total - previousTotal : null
  const rate = diff != null && previousTotal > 0 ? Math.round((diff / previousTotal) * 100) : null
  const max = Math.max(1, ...categories.map((c) => c.amount))
  const time = updatedAt.slice(5, 16).replace('-', '.').replace('T', ' ')
  const vs = compare ? `${Number(compare.to.split('-')[1])}월 같은 기간보다 ` : '지난 기간보다 '

  return (
    <div className="mc-spend" aria-label="소비 분석 결과">
      <div className="mc-spend-head">
        <span>{range(period)} {one ? `· ${one.categoryName}` : '소비'}</span>
        <small>{time} 기준</small>
      </div>

      <div className="mc-spend-total">
        <strong>{won(one ? one.amount : total)}</strong>
        {one && !empty && <span className="mc-muted">전체 소비 {won(total)} 중 {one.ratio}%</span>}
        {!one && diff != null && (
          <span className={`mc-spend-diff ${diff > 0 ? 'is-up' : 'is-down'}`}>
            {vs}{signed(diff)}{rate != null && ` (${rate > 0 ? '+' : ''}${rate}%)`}
          </span>
        )}
        {one && one.prevDiff != null && (
          <span className={`mc-spend-diff ${one.prevDiff > 0 ? 'is-up' : 'is-down'}`}>
            {vs}{signed(one.prevDiff)}{one.prevDiffRate != null ? ` (${one.prevDiffRate > 0 ? '+' : ''}${one.prevDiffRate}%)` : ''}
          </span>
        )}
      </div>

      {empty && <p className="mc-spend-empty">이 기간엔 소비가 없어요.</p>}

      {/* 고정지출 · 생활비 나눠 보기 (결정 S) — 전체 기준이라 카테고리 질문에는 안 보여 줌 */}
      {!empty && !one && fixedAmount > 0 && (
        <div className="mc-spend-split">
          <div className="mc-spend-bar" aria-hidden="true">
            <span className="is-fixed" style={{ width: `${(fixedAmount / total) * 100}%` }}></span>
            <span className="is-living" style={{ width: `${(living / total) * 100}%` }}></span>
          </div>
          <div className="mc-spend-legend">
            <span><i className="is-fixed"></i>고정지출 {won(fixedAmount)}</span>
            <span><i className="is-living"></i>생활비 {won(living)}</span>
          </div>
        </div>
      )}

      {lines.length > 0 && <ul className="mc-spend-lines">{lines.map((l) => <li key={l}>{l}</li>)}</ul>}
      {!comparison.available && NO_COMPARE[comparison.reason] && <p className="mc-note" style={{ padding: 0 }}>{NO_COMPARE[comparison.reason]}</p>}

      {/* 카테고리별 (금액 큰 순) */}
      {!empty && !one && (
        <div className="mc-spend-cats">
          {categories.map((c) => (
            <div className="mc-spend-cat" key={c.category}>
              <span className="mc-spend-cname">{c.categoryName}{topSpending?.items.some((t) => t.category === c.category) && <em className="mc-spend-top">가장 많이</em>}</span>
              <span className="mc-spend-cbar" aria-hidden="true"><span style={{ width: `${(c.amount / max) * 100}%` }}></span></span>
              <span className="mc-spend-camt">{won(c.amount)}<small>{c.ratio}%{c.prevDiff != null && c.prevDiff !== 0 && ` · ${c.prevDiff > 0 ? '▲' : '▼'}${won(Math.abs(c.prevDiff))}`}</small></span>
            </div>
          ))}
        </div>
      )}

      {/* '확인 필요'는 한 줄로 (N④). total 에 들어 있다 */}
      {unclassifiedAmount > 0 && (
        <button type="button" className="mc-spend-unc" onClick={() => navigate(`${PATHS.txs}?category=확인 필요`)}>
          <span>카테고리 확인이 필요한 {won(unclassifiedAmount)}도 합계에 들어 있어요</span><span aria-hidden="true">분류하기 ›</span>
        </button>
      )}
    </div>
  )
}
