// 추천 예산 확인 — 실제 초안 · 승인 (docs/api/budgets.md)
//   초안 보기: GET /api/budgets/ (이번 주기 승인 전 초안 budget.status = validated)
//   초안 만들기: POST /api/budgets/recommend/ → GET /api/tasks/<taskId>/ (작업 결과 outcome: proposed · infeasible)
//   금액 고정: PATCH /api/budgets/<budgetId>/categories/<code>/ { locked }
//   승인: POST /api/budgets/<budgetId>/approve/ (요청 번호 필수)
import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import Icon from '../components/Icon.jsx'
import LockIcon from '../components/LockIcon.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import AdjustChatPopup from '../popups/AdjustChatPopup.jsx'
import { won } from '../utils/money.js'
import { apiSend, newRequestKey } from '../utils/api.js'
import { PATHS } from '../routes.js'
import { catIcon, catName } from '../utils/categories.js'
import { manwon } from '../utils/adjust.js'

// [CSS 이름(색), 이름, split 칸]
const SPLIT_ROWS = [
  ['fix', '고정지출', 'fixed'], ['save', '저축', 'saving'], ['live', '생활비', 'living'],
  ['cushion', '머니 쿠션 적립', 'cushion'], ['left', '여윳돈', 'spare'],   // 비정기 준비금은 10/24 결정 AB로 2차
]
// 카테고리 막대 색 진하기: 첫 칸 100% → 마지막 칸 18%
const shade = (i, n) => `color-mix(in srgb,var(--mc-brand) ${Math.round(100 - (82 * i) / Math.max(n - 1, 1))}%,var(--mc-paper))`
const pct = (part, whole) => (whole > 0 ? (part / whole) * 100 : 0)
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// 추천을 못 만든 이유 (작업 결과 conflicts) → 화면 문장
function conflictText(c) {
  if (c.type === 'minimums_over_living') return `카테고리 최소 금액 합계가 생활비보다 ${won(c.shortfall)} 많아요. 내 정보에서 최소 금액을 줄여 주세요.`
  if (c.type === 'lock_below_minimum') return `${catName(c.category)}의 고정 금액(${won(c.lockedAmount)})이 최소 금액(${won(c.minAmount)})보다 작아요.`
  if (c.type === 'locks_mismatch_living') return `모든 카테고리를 고정했는데 합계가 생활비와 ${won(Math.abs(c.difference))} 달라요. 자물쇠를 하나 풀어 주세요.`
  return '카테고리 최소 금액과 자물쇠 금액을 확인해 주세요.'
}

export default function RecommendPage() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)  // { message, conflicts?, code? }
  // 서버가 푼 자물쇠 (P-13): 지킬 돈에서 생활비를 바꿔 풀렸거나(이전 화면에서 넘겨받음), 추천할 때 막혀서 풀린 것
  const { state: navState } = useLocation()
  const [released, setReleased] = useState(() => navState?.locksReleased ?? [])
  const approveKeys = useRef(new Map())  // 초안 하나에 요청 번호 하나 (같은 초안을 다시 눌러도 한 번만 승인)

  const load = useCallback(async () => {
    try {
      const next = await apiSend('/api/budgets/', 'GET')
      setData(next)
      setLoadError(null)
      return next
    } catch (err) {
      setLoadError(err)
      return null
    }
  }, [])


  const draft = data?.budget?.status === 'validated' ? data.budget : null
  const approveKey = (budgetId) => {
    if (!approveKeys.current.has(budgetId)) approveKeys.current.set(budgetId, newRequestKey())
    return approveKeys.current.get(budgetId)
  }

  // 새 추천 만들기: 작업을 만들고(응답은 taskId · status만) 작업 확인 API로 결과를 읽은 뒤 초안을 다시 불러온다
  const creating = useRef(false)  // 빠르게 두 번 눌러도 한 번만 (state는 다음 그림까지 안 바뀜)
  const makeDraft = useCallback(async () => {
    if (creating.current) return
    creating.current = true
    setBusy(true)
    setProblem(null)
    try {
      const { task } = await apiSend('/api/budgets/recommend/', 'POST', {}, { requestKey: newRequestKey() })
      let now = await apiSend(`/api/tasks/${task.taskId}/`, 'GET')
      for (let i = 0; ['queued', 'running'].includes(now.status) && i < 30; i += 1) {
        await wait(1000)
        now = await apiSend(`/api/tasks/${task.taskId}/`, 'GET')
      }
      if (['queued', 'running'].includes(now.status)) {
        // 아직 만드는 중: 다시 누르면 서버가 진행 중인 같은 작업을 돌려준다
        setProblem({ message: '아직 추천을 만드는 중이에요. 잠시 후 다시 눌러 주세요.' })
      } else if (now.status !== 'succeeded') {
        setProblem({ message: now.error?.message ?? '추천을 만들지 못했어요. 잠시 후 다시 시도해 주세요.' })
      } else {
        if (now.result?.locksReleased?.length) setReleased(now.result.locksReleased)  // 막혀서 풀린 자물쇠 (P-13 ②)
        if (now.result?.outcome === 'infeasible') setProblem({ message: now.result.summary, conflicts: now.result.conflicts })
      }
      await load()
    } catch (err) {
      setProblem({ message: err.message, code: err.code })
    } finally {
      creating.current = false
      setBusy(false)
    }
  }, [load])

  const toggleLock = async (c) => {
    setProblem(null)
    try {
      const saved = await apiSend(`/api/budgets/${draft.budgetId}/categories/${c.category}/`, 'PATCH', { locked: !c.locked })
      setData((now) => ({
        ...now,
        nag: saved.nag,
        categories: now.categories.map((x) => (x.category === c.category ? { ...x, locked: saved.locked, lockedAmount: saved.lockedAmount } : x)),
      }))
    } catch (err) {
      setProblem({ message: err.message, code: err.code })
    }
  }

  const onSubmit = async (e) => {
    e.preventDefault()
    if (!draft || busy) return
    setBusy(true)
    setProblem(null)
    try {
      await apiSend(`/api/budgets/${draft.budgetId}/approve/`, 'POST', {}, { requestKey: approveKey(draft.budgetId) })
      navigate(PATHS.home)
    } catch (err) {
      setProblem({ message: err.message, code: err.code })
      if (err.status === 409) await load()  // 만료 · 지난 예산은 다시 불러와 화면에서 내린다 (같은 에러 버튼이 남지 않게)
    } finally {
      setBusy(false)
    }
  }

  // 처음 불러오기. 지킬 돈을 저장하고 넘어왔으면(state.autoRecommend) 처음 불러온 데이터에 초안이 없을 때만 새 추천을 한 번 만든다.
  // 표시는 들어오자마자 지워서, 승인 뒤 뒤로 가기로 다시 와도 새 초안을 만들지 않는다
  const autoRecommend = useRef(Boolean(navState?.autoRecommend))
  useEffect(() => {
    if (navState?.autoRecommend) navigate(PATHS.recommend, { replace: true, state: null })
  }, [navState, navigate])
  useEffect(() => {
    let alive = true  // 화면을 떠나면 늦게 온 응답은 버린다
    apiSend('/api/budgets/', 'GET')
      .then((first) => {
        if (!alive) return
        setData(first)
        if (!autoRecommend.current) return
        autoRecommend.current = false  // 첫 데이터로만 판단
        if (first.budget?.status !== 'validated') makeDraft()
      })
      .catch((err) => { if (alive) setLoadError(err) })
    return () => { alive = false }
  }, [makeDraft])  // makeDraft는 useCallback이라 화면을 열 때 한 번만 돈다

  if (!data) return <Layout title="추천 예산 확인" showBack><ApiStatus error={loadError} /></Layout>

  const foot = draft
    ? <button className="mc-primary" form="mc-form" type="submit" disabled={busy}>{busy ? '처리하는 중…' : '이 예산으로 시작하기'}</button>
    : null
  const problemBox = problem && (
    <div className="mc-min-warn" role="alert">
      <p style={{ margin: 0 }}>{problem.message}</p>
      {problem.conflicts?.map((c, i) => <p key={i} style={{ margin: '4px 0 0' }}>{conflictText(c)}</p>)}
      {problem.code === 'not_ready' && !draft && (
        <button type="button" className="mc-link" onClick={() => navigate(PATHS.protect)}>지킬 돈 확인하러 가기 ›</button>
      )}
    </div>
  )

  // 승인 전 초안이 없을 때: 새로 만들기 (이미 승인한 예산이 있으면 다시 추천받기)
  if (!draft) {
    return (
      <Layout title="추천 예산 확인" showBack>
        <StepBar step={6} />
        <div className="mc-eyebrow">추천 예산 확인</div>
        <h1>{data.budget ? '이번 달 예산을 이미 쓰고 있어요' : '카테고리별 예산을 추천해 드릴게요'}</h1>
        <p className="mc-muted">{data.budget ? '다시 추천받으면 새 초안을 확인한 뒤 바꿀 수 있어요. 승인하기 전까지 지금 예산은 그대로예요.' : '지킬 돈에서 정한 생활비를 카테고리별로 나눠요.'}</p>
        {released.length > 0 && <p className="mc-min-warn" role="status">생활비가 바뀌어서 이번 달 자물쇠({released.map(catName).join(' · ')})를 풀었어요. 필요하면 다시 걸어 주세요.</p>}
        {problemBox}
        <button type="button" className="mc-primary" onClick={makeDraft} disabled={busy || !data.cycle}>
          {busy ? '추천 만드는 중…' : data.budget ? '다시 추천받기' : '추천 예산 만들기'}
        </button>
        {!data.cycle && <p className="mc-note">급여일을 먼저 정해 주세요.</p>}
      </Layout>
    )
  }

  const { split, categories } = data
  const livingTotal = split.living
  const cats = categories.map((c, i) => ({ ...c, color: shade(i, categories.length) }))

  return (
    <Layout title="추천 예산 확인" showBack foot={foot}>
      <StepBar step={6} />
      <div className="mc-eyebrow">추천 예산 확인 · 예산 {draft.versionNo}</div>
      <h1>카테고리별 예산을<br />확인하고 고쳐 주세요</h1>
      <p className="mc-muted">지킬 돈에서 정한 생활비를 카테고리별로 나눴어요.</p>
      {!draft.salaryConfirmed && <p className="mc-note">이번 주기 월급이 아직 확인되지 않았어요.</p>}
      {released.length > 0 && <p className="mc-min-warn" role="status">생활비가 바뀌어서 이번 달 자물쇠({released.map(catName).join(' · ')})를 풀었어요. 필요하면 다시 걸어 주세요.</p>}

      <div className="mc-card">
        <div className="mc-row"><h2>한 달 나눠보기</h2><span className="mc-muted">월 {won(split.funding)}</span></div>
        <div className="mc-split" role="img" aria-label={SPLIT_ROWS.map(([, n, k]) => `${n} ${won(split[k])}`).join(', ')}>
          {SPLIT_ROWS.map(([css, n, k]) => <span key={n} className={`mc-split-${css}`} style={{ width: `${pct(split[k], split.funding)}%` }}></span>)}
        </div>
        <div className="mc-split-legend">
          {SPLIT_ROWS.map(([css, n, k]) => <div key={n}><i className={`mc-split-${css}`}></i><span>{n}</span><strong>{won(split[k])}</strong></div>)}
        </div>
      </div>

      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <div className="mc-card" style={{ marginBottom: 0 }}>
          <div className="mc-row"><h2>생활비 카테고리</h2><span className="mc-muted">{won(livingTotal)}</span></div>
          <div className="mc-split" role="img" aria-label={cats.map((c) => `${c.categoryName} ${Math.round(pct(c.allocated, livingTotal))}%`).join(', ')}>
            {cats.map((c) => <span key={c.category} style={{ width: `${pct(c.allocated, livingTotal)}%`, background: c.color }}></span>)}
          </div>
          <div className="mc-cat-legend">
            {cats.map((c) => <div key={c.category}><i style={{ background: c.color }}></i><span>{c.categoryName}</span><b>{Math.round(pct(c.allocated, livingTotal))}%</b></div>)}
          </div>
          <div className="mc-row mc-cat-head"><span>카테고리별 예산</span><span>고정</span></div>
          <div className="mc-cat-list">
            {cats.map((c) => (
              <div className="mc-catrow" key={c.category}>
                <span className="mc-catname"><span className="mc-cat-ic"><Icon name={catIcon(c.categoryName)} /></span>{c.categoryName}{c.minAmount > 0 && <em className="mc-min-tag">최소 {manwon(c.minAmount)}</em>}</span>
                <strong>{won(c.allocated)}</strong>
                <button type="button" className="mc-lockt" aria-label={`${c.categoryName} 금액 고정`} aria-pressed={c.locked} onClick={() => toggleLock(c)}>
                  <LockIcon />
                </button>
              </div>
            ))}
          </div>
          {data.nag && <p className="mc-note" style={{ padding: '10px 0 0' }}>{data.nag}</p>}
          <div className="mc-chat-alt">
            <span>꼭 바꾸고 싶은 부분이 있나요?</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<AdjustChatPopup />)}>챗봇과 조정하기 ›</button>
          </div>
        </div>
        {problemBox}
        <p className="mc-note">최소 표시는 내 정보에서 정한 카테고리 최소 금액이에요. 그 밑으로는 줄지 않고, 자물쇠를 걸면 다음에 다시 추천받을 때 그 금액 그대로 고정돼요.</p>
      </form>
    </Layout>
  )
}
