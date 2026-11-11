// 카테고리 예산 — GET /api/budgets/ (docs/api/budgets.md '카테고리 예산 보기')
// 자물쇠: PATCH /api/budgets/<budgetId>/categories/<code>/ { locked } (누르면 바로 저장)
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import Icon from '../components/Icon.jsx'
import LockIcon from '../components/LockIcon.jsx'
import { won } from '../utils/money.js'
import { apiSend, useApi } from '../utils/api.js'
import ApiStatus from '../components/ApiStatus.jsx'
import OutOfBudgetLine, { OldScopeNote } from '../components/OutOfBudgetLine.jsx'
import UnreflectedCardNote from '../components/UnreflectedCardNote.jsx'
import { PATHS } from '../routes.js'
import { catIcon, PENDING } from '../utils/categories.js'
import { manwon } from '../utils/adjust.js'
import HelpTip from '../components/HelpTip.jsx'

// 한 달 나눠보기 줄 (split 칸 이름 → 화면 이름)
const SPLIT_ROWS = [['fixed', '고정비'], ['saving', '저축'], ['cushion', '쿠션 적립'], ['living', '생활비'], ['spare', '여윳돈']]

// '2026-10-25' ~ 끝 경계 '2026-11-25' → '10.25 ~ 11.24' (끝 경계 전날까지가 이번 주기)
const shortDay = (iso, minusDays = 0) => {
  const d = new Date(`${iso}T00:00:00`)
  d.setDate(d.getDate() - minusDays)
  return `${d.getMonth() + 1}.${d.getDate()}`
}

const pct = (used, budget) => (budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : used > 0 ? 100 : 0)

export default function BudgetPage() {
  const navigate = useNavigate()
  const { data, error } = useApi('/api/budgets/')
  // 자물쇠를 누른 결과 (서버 응답). 카테고리 코드 → { locked, lockedAmount }
  const [locks, setLocks] = useState({})
  const [nag, setNag] = useState(undefined)
  const [overOpen, setOverOpen] = useState(false)  // 생활비 초과 안내 펼침
  const [lockError, setLockError] = useState(null)

  if (!data) return <Layout title="카테고리 예산" activeTab="budget"><ApiStatus error={error} /></Layout>
  const { budget, split, living, overspend, categories, unclassifiedCount } = data
  const goPending = () => navigate(`${PATHS.txs}?category=${PENDING.code}`)

  // 예산이 아직 없을 때 (첫 가입 · 급여일 없음)
  if (!budget) {
    return (
      <Layout title="카테고리 예산" activeTab="budget">
        <h1>카테고리 예산</h1>
        <div className="mc-card">
          <h2>{data.cycle ? '추천 예산을 먼저 만들어 주세요' : '급여일을 먼저 정해 주세요'}</h2>
          <p className="mc-muted">{data.cycle ? '지킬 돈을 정하면 카테고리별 예산을 추천해 드려요.' : '급여일이 있어야 이번 달(급여 주기)을 정할 수 있어요.'}</p>
          <button type="button" className="mc-primary" onClick={() => navigate(data.cycle ? PATHS.protect : PATHS.salary)}>
            {data.cycle ? '지킬 돈 정하러 가기' : '급여 입력하러 가기'}
          </button>
        </div>
        {unclassifiedCount > 0 && (
          <button type="button" className="mc-catuse mc-catuse-pend" onClick={goPending}>
            <span>확인 필요 {unclassifiedCount}건</span><strong>고르러 가기 ›</strong>
          </button>
        )}
      </Layout>
    )
  }

  const shownNag = nag === undefined ? data.nag : nag
  const lockOf = (c) => locks[c.category] ?? { locked: c.locked, lockedAmount: c.lockedAmount }

  const toggleLock = async (c) => {
    setLockError(null)
    try {
      const saved = await apiSend(`/api/budgets/${budget.budgetId}/categories/${c.category}/`, 'PATCH', { locked: !lockOf(c).locked })
      setLocks((now) => ({ ...now, [c.category]: { locked: saved.locked, lockedAmount: saved.lockedAmount } }))
      setNag(saved.nag)
    } catch (err) {
      setLockError(err.message)
    }
  }

  return (
    <Layout title="카테고리 예산" activeTab="budget">
      <div className="mc-eyebrow">{shortDay(data.cycle.startsOn)} ~ {shortDay(data.cycle.endsBefore, 1)} · 예산 {budget.versionNo}</div>
      <h1>카테고리 예산</h1>

      {budget.status === 'validated' && (
        <div className="mc-card">
          <h2>아직 승인 전 예산이에요</h2>
          <p className="mc-muted">확인하고 승인하면 이번 달 예산으로 시작돼요.</p>
          <button type="button" className="mc-primary" onClick={() => navigate(PATHS.recommend)}>추천 예산 확인하러 가기</button>
        </div>
      )}
      {!budget.salaryConfirmed && <p className="mc-note">이번 주기 월급이 아직 확인되지 않았어요.</p>}
      <UnreflectedCardNote notes={data.cardNotes} onRecalculate={() => navigate(PATHS.protect, { state: { from: 'my', refreshRecommendation: true } })} />

      <div className="mc-card">
        <div className="mc-row"><h2>한 달 나눠보기</h2><span className="mc-muted">월 {won(split.funding)}</span></div>
        {SPLIT_ROWS.map(([key, name]) => <div className="mc-keyvalue" key={key}><span>{name}</span><strong>{won(split[key])}</strong></div>)}
      </div>

      <div className="mc-card">
        <div className="mc-row">
          <h2>생활비 카테고리
            {/* 11/9: 맨 아래 있던 조정 규칙 안내를 제목 옆 [?] 창으로 */}
            <HelpTip label="예산 조정 규칙" title="예산은 이렇게 조정돼요">
              <li><span className="mc-inline-ic"><LockIcon /></span> 자물쇠를 건 카테고리는 조정할 때 그대로 둬요.</li>
              <li>‘최소’ 표시가 있는 카테고리는 그 금액 밑으로 줄이지 않아요.</li>
              <li>예산 변경은 챗봇이 만든 조정안을 확인하고 승인해야 반영돼요.</li>
            </HelpTip>
          </h2>
          <span className="mc-muted">{won(living.spent)} / {won(living.budget)}</span>
        </div>
        {overspend.over > 0 && (
          /* 11/9: 예산 초과 안내 — 빨간 ! + 첫 줄만, [›]를 눌러 펼치면 여윳돈 · 모자란 돈 */
          <button type="button" className="mc-over" aria-expanded={overOpen} onClick={() => setOverOpen((v) => !v)}>
            <span className="mc-over-ic"><Icon name="CircleAlert" size={16} /></span>
            <span className="mc-grow">
              <strong>생활비를 <b>{won(overspend.over)}</b> 넘게 썼어요</strong>
              {overOpen && <small>여윳돈에서 <b>{won(overspend.coveredBySpare)}</b> 채웠어요{overspend.shortfall > 0 && <><br />그래도 <b>{won(overspend.shortfall)}</b> 모자라요</>}</small>}
            </span>
            <span className="mc-over-go"><Icon name="ChevronRight" size={16} /></span>
          </button>
        )}
        {categories.map((c) => {
          const lock = lockOf(c)
          // 예산을 넘긴 금액 (홈 알림과 같은 기준: 예산 0원 칸은 넘겼다고 보지 않는다). 넘기면 막대 색이 바뀐다
          const over = c.allocated > 0 && c.spent > c.allocated ? c.spent - c.allocated : 0
          return (
            <div className="mc-catuse" key={c.category}>
              <div className="mc-row">
                <span className="mc-catname"><span className="mc-cat-ic"><Icon name={catIcon(c.categoryName)} /></span>{c.categoryName}{c.minAmount > 0 && <em className="mc-min-tag">최소 {manwon(c.minAmount)}</em>}</span>
                <button type="button" className="mc-lockt" aria-pressed={lock.locked} aria-label={`${c.categoryName} 자물쇠`} onClick={() => toggleLock(c)}>
                  <LockIcon />
                </button>
              </div>
              <div className={`mc-bar${over ? ' is-over' : ''}`}><span style={{ width: `${pct(c.spent, c.allocated)}%` }}></span></div>
              <div className="mc-row mc-catuse-n">
                <span className="mc-muted">{won(c.spent)} / {won(c.allocated)} · {pct(c.spent, c.allocated)}%</span>
                {over ? <strong className="is-over">{won(over)} 초과</strong> : <strong>남은 {won(Math.max(c.allocated - c.spent, 0))}</strong>}
              </div>
              {/* 초과한 카테고리 → 그 카테고리를 담아 챗봇 예산 조정으로 (11/11, docs/api/chat.md budgetContext.hints).
                  overAmount 는 참고값이고 서버가 최신 초과액을 다시 확인한다 */}
              {over > 0 && (
                <button type="button" className="mc-link mc-catuse-adjust" aria-label={`${c.categoryName} 예산 조정하기`}
                  onClick={() => navigate(PATHS.assistant, { state: { budgetContext: {
                    baseBudgetId: budget.budgetId, mode: budget.status === 'approved' ? 'NORMAL_ADJUSTMENT' : 'INITIAL_ADJUSTMENT',
                    hints: { reason: 'over', categories: [c.category], overAmount: over },
                  } } })}>
                  예산 조정하기 ›
                </button>
              )}
            </div>
          )
        })}
        <OutOfBudgetLine value={data.outOfBudget} />
        <OldScopeNote amount={budget.outOfBudgetAllocated} />
        {lockError && <p className="mc-note" role="alert" style={{ padding: 0 }}>{lockError}</p>}
        {shownNag && <p className="mc-note" style={{ padding: 0 }}>{shownNag}</p>}
        {unclassifiedCount > 0 && (
          <button type="button" className="mc-catuse mc-catuse-pend" onClick={goPending}>
            <span>확인 필요 {unclassifiedCount}건</span><strong>고르러 가기 ›</strong>
          </button>
        )}
      </div>

      <button type="button" className="mc-primary" onClick={() => navigate(PATHS.assistant, {
        state: { budgetContext: { baseBudgetId: budget.budgetId, mode: budget.status === 'approved' ? 'NORMAL_ADJUSTMENT' : 'INITIAL_ADJUSTMENT' } },
      })}>
        <Icon name="MessageCircle" /> 챗봇으로 예산 조정
      </button>
    </Layout>
  )
}
