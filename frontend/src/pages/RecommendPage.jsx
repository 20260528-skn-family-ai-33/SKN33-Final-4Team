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
import BudgetAIConsent from '../components/BudgetAIConsent.jsx'
import CardReservedHelp from '../components/CardReservedHelp.jsx'
import { won } from '../utils/money.js'
import { apiSend, newRequestKey } from '../utils/api.js'
import { PATHS } from '../routes.js'
import { catIcon, catName, OUT_OF_BUDGET } from '../utils/categories.js'
import { manwon } from '../utils/adjust.js'

// [CSS 이름(색), 이름, split 칸]
const SPLIT_ROWS = [
  ['fix', '고정지출', 'fixed'], ['save', '저축', 'saving'], ['live', '생활비', 'living'],
  ['cushion', '머니 쿠션 적립', 'cushion'], ['left', '여윳돈', 'spare'],   // 비정기 준비금은 10/24 결정 AB로 2차
]
// 고정지출 중 '지난 카드값'(가입 전에 쓴 카드값 · 할부 회차)은 따로 칸 (11/12). 0원이면 칸 없음. [CSS, 이름, 금액]
const splitParts = (split) => {
  const card = split.cardReserved ?? 0
  return SPLIT_ROWS.flatMap(([css, name, key]) => key !== 'fixed' ? [[css, name, split[key]]]
    : [[css, name, split.fixed - card], ...(card > 0 ? [['card', '지난 카드값', card]] : [])])
}
// 카테고리 막대 색 진하기: 첫 칸 100% → 마지막 칸 18%
const shade = (i, n) => `color-mix(in srgb,var(--mc-brand) ${Math.round(100 - (82 * i) / Math.max(n - 1, 1))}%,var(--mc-paper))`
const pct = (part, whole) => (whole > 0 ? (part / whole) * 100 : 0)
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const WAIT_TRIES = 90  // 추천 작업을 기다리는 최대 횟수 (1초씩). AI 를 쓰면 30초를 넘길 때가 있어 11/12 에 30 → 90

// 추천을 만드는 동안 보여 줄 화면 (11/12): 오래 걸릴 수 있어 버튼 글자만 바뀌던 것을 화면 전체 안내로.
// 단계 문구는 실제 진행률이 아니라 기다리는 동안의 안내라서, 몇 초마다 다음 문구로 넘어가고 마지막 문구에서 멈춘다 (몇 % 같은 숫자는 보여 주지 않음)
const MAKING_STEPS = ['지난 소비를 살펴보고 있어요', '꼭 나갈 돈과 지킬 돈을 맞춰 보고 있어요', '카테고리별로 예산을 나누고 있어요', '마지막으로 금액을 검사하고 있어요']
function MakingView() {
  const [step, setStep] = useState(0)
  useEffect(() => {
    if (step >= MAKING_STEPS.length - 1) return undefined
    const timer = setTimeout(() => setStep((s) => s + 1), 3000)
    return () => clearTimeout(timer)
  }, [step])
  return (
    <Layout title="추천 예산 확인">
      <StepBar step={6} />
      <div className="mc-making" role="status" aria-live="polite">
        <span className="mc-conn-busy"><Icon name="Loader" /></span>
        <h1>예산을 짜고 있어요</h1>
        <p className="mc-muted">{MAKING_STEPS[step]}</p>
        <p className="mc-note">조금 걸릴 수 있어요. 다 되면 이 화면에서 바로 보여 드려요.</p>
      </div>
    </Layout>
  )
}

// 추천을 못 만든 이유 (작업 결과 conflicts) → 화면 문장
function conflictText(c) {
  if (c.type === 'target_conflict') {
    const reasons = { locked_amount: '잠금액과 유지 목표가 달라요.', minimum_amount: '유지 목표가 최소 보장액보다 작아요.',
      minimum_or_floor: '유지 목표가 최소금액이나 시스템 하한보다 작아요.', amount_unit: '유지 목표를 5천 원 단위로 확인해 주세요.',
      living_budget: '유지 목표를 현재 생활비 안에 모두 반영할 수 없어요.', invalid_target: '저장된 유지 목표를 확인해 주세요.' }
    const basis = { locked_amount: '잠금액', minimum_amount: '최소액', minimum_or_floor: '하한액', amount_unit: '기준금액', living_budget: '반영 가능액' }
    const names = (c.categories ?? (c.category ? [c.category] : [])).map(catName).join(' · ')
    const amounts = Number.isSafeInteger(c.requestedAmount) && Number.isSafeInteger(c.possibleAmount)
      ? ` 목표 ${won(c.requestedAmount)} · ${basis[c.reason] ?? '기준금액'} ${won(c.possibleAmount)}${Number.isSafeInteger(c.diff) ? ` · 차이 ${won(Math.abs(c.diff))}` : ''}` : ''
    return `${names ? `${names}: ` : ''}${reasons[c.reason] ?? '유지 목표와 현재 예산 조건을 확인해 주세요.'}${amounts}`
  }
  if (c.type === 'minimums_over_living') return `최소금액과 고정 금액을 모두 지키려면 생활비가 ${won(c.shortfall)} 부족해요. 설정 금액이나 생활비 배분을 확인해 주세요.`
  if (c.type === 'lock_below_minimum') return `${catName(c.category)}의 고정 금액(${won(c.lockedAmount)})이 최소 금액(${won(c.minAmount)})보다 작아요.`
  if (c.type === 'locks_mismatch_living') return `모든 카테고리를 고정했는데 합계가 생활비와 ${won(Math.abs(c.difference))} 달라요. 자물쇠를 하나 풀어 주세요.`
  if (c.type === 'target_conflict') {
    const reasons = { locked_amount: '잠금액과 유지 목표가 달라요.', minimum_amount: '유지 목표가 최소 보장액보다 작아요.',
      amount_unit: '유지 목표를 5천 원 단위로 확인해 주세요.', living_budget: '유지 목표를 현재 생활비 안에 모두 반영할 수 없어요.', invalid_target: '저장된 유지 목표를 확인해 주세요.' }
    const basis = { locked_amount: '잠금액', minimum_amount: '최소액', amount_unit: '기준금액', living_budget: '반영 가능액' }
    const names = (c.categories ?? []).map(catName).join(' · ')
    const amounts = Number.isSafeInteger(c.requestedAmount) && Number.isSafeInteger(c.possibleAmount)
      ? ` 목표 ${won(c.requestedAmount)} · ${basis[c.reason] ?? '기준금액'} ${won(c.possibleAmount)}${Number.isSafeInteger(c.diff) ? ` · 차이 ${won(Math.abs(c.diff))}` : ''}` : ''
    return `${names ? `${names}: ` : ''}${reasons[c.reason] ?? '유지 목표와 현재 예산 조건을 확인해 주세요.'}${amounts}`
  }
  return '카테고리 최소 금액과 자물쇠 금액을 확인해 주세요.'
}

export default function RecommendPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [making, setMaking] = useState(false)  // 새 추천을 만드는 중 (11/12: 이때는 화면 전체를 기다림 안내로)
  const [problem, setProblem] = useState(null)  // { message, conflicts?, code? }
  const [draftBlocked, setDraftBlocked] = useState(false)
  // 서버가 푼 자물쇠 (P-13): 지킬 돈에서 생활비를 바꿔 풀렸거나(이전 화면에서 넘겨받음), 추천할 때 막혀서 풀린 것
  const { state: navState } = useLocation()
  const returnedRecovery = navState?.recoveryReturn
  const [resumeRecovery, setResumeRecovery] = useState(() => returnedRecovery ?? null)
  const [released, setReleased] = useState(() => navState?.locksReleased ?? [])
  const approveKeys = useRef(new Map())  // 초안 하나에 요청 번호 하나 (같은 초안을 다시 눌러도 한 번만 승인)
  const mounted = useRef(false)

  const openRecovery = useCallback((sourceTaskId) => navigate(PATHS.assistant, { replace: true, state: {
    budgetContext: { mode: 'DESIGN_RECOVERY', sourceTaskId },
    designRecovery: { sourceTaskId, requestKey: newRequestKey() },
  } }), [navigate])

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


  const draft = data?.budget?.status === 'validated' && !draftBlocked ? data.budget : null
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
    setMaking(true)
    setProblem(null)
    setResumeRecovery(null)
    setDraftBlocked(true)
    try {
      const { task } = await apiSend('/api/budgets/recommend/', 'POST', {}, { requestKey: newRequestKey() })
      let now = await apiSend(`/api/tasks/${task.taskId}/`, 'GET')
      for (let i = 0; ['queued', 'running'].includes(now.status) && i < WAIT_TRIES; i += 1) {
        await wait(1000)
        now = await apiSend(`/api/tasks/${task.taskId}/`, 'GET')
      }
      if (['queued', 'running'].includes(now.status)) {
        // 아직 만드는 중: 다시 누르면 서버가 진행 중인 같은 작업을 돌려준다
        setProblem({ message: '아직 추천을 만드는 중이에요. 잠시 후 다시 눌러 주세요.' })
      } else if (now.status !== 'succeeded') {
        setProblem({ message: now.error?.message ?? '추천을 만들지 못했어요. 잠시 후 다시 시도해 주세요.' })
      } else {
        const handoff = now.result?.handoff
        if (['infeasible', 'target_conflict', 'needs_upper_allocation_review'].includes(now.result?.outcome) && handoff?.mode === 'DESIGN_RECOVERY'
            && handoff.sourceTaskId === task.taskId && !now.result.budgetId) {
          if (mounted.current) openRecovery(handoff.sourceTaskId)
          return
        }
        if (now.result?.locksReleased?.length) setReleased(now.result.locksReleased)  // 막혀서 풀린 자물쇠 (P-13 ②)
        if (now.result?.outcome === 'proposed' && now.result.budgetId) {
          const next = await load()
          if (next?.budget?.status === 'validated' && next.budget.budgetId === now.result.budgetId) {
            setDraftBlocked(false)
          } else {
            setProblem({ message: '새 추천 예산을 확인하지 못했어요. 다시 시도해 주세요.' })
          }
        } else {
          setProblem({ message: now.result?.summary || '추천 예산을 만들지 못했어요. 조건을 확인한 뒤 다시 시도해 주세요.', conflicts: now.result?.conflicts, code: now.result?.outcome })
        }
      }
    } catch (err) {
      setProblem({ message: err.message, code: err.code })
    } finally {
      creating.current = false
      setBusy(false)
      setMaking(false)
    }
  }, [load, openRecovery])

  const toggleLock = async (c) => {
    if (!draft || busy || OUT_OF_BUDGET.includes(c.category)) return
    setBusy(true)
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
    } finally {
      setBusy(false)
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
      if (err.status === 409) {
        setDraftBlocked(true)
        await load()
      }
    } finally {
      setBusy(false)
    }
  }

  // 처음 불러오기. 지킬 돈을 저장하고 넘어왔으면(state.autoRecommend) 처음 불러온 데이터에 초안이 없을 때만 새 추천을 한 번 만든다.
  // 표시는 들어오자마자 지워서, 승인 뒤 뒤로 가기로 다시 와도 새 초안을 만들지 않는다
  const autoRecommend = useRef(Boolean(navState?.autoRecommend))
  const refreshRecommendation = useRef(Boolean(navState?.refreshRecommendation))
  useEffect(() => {
    if (navState?.autoRecommend) navigate(PATHS.recommend, { replace: true, state: null })
  }, [navState, navigate])
  useEffect(() => {
    let alive = true  // 화면을 떠나면 늦게 온 응답은 버린다
    mounted.current = true
    apiSend('/api/budgets/', 'GET')
      .then((first) => {
        if (!alive) return
        setData(first)
        if (!autoRecommend.current) return
        autoRecommend.current = false  // 첫 데이터로만 판단
        if (refreshRecommendation.current || first.budget?.status !== 'validated') makeDraft()
      })
      .catch((err) => { if (alive) setLoadError(err) })
    return () => { alive = false; mounted.current = false }
  }, [makeDraft])  // makeDraft는 useCallback이라 화면을 열 때 한 번만 돈다

  if (making) return <MakingView />
  if (!data) return <Layout title="추천 예산 확인" showBack><ApiStatus error={loadError} /></Layout>

  const foot = draft
    ? <button className="mc-primary" form="mc-form" type="submit" disabled={busy}>{busy ? '처리하는 중…' : '이 예산으로 시작하기'}</button>
    : null
  const settingsConflict = problem?.conflicts?.some(c => ['minimums_over_living', 'lock_below_minimum', 'locks_mismatch_living'].includes(c.type))
  const problemBox = problem && (
    <div className="mc-min-warn" role="alert">
      {!settingsConflict && <p style={{ margin: 0 }}>{problem.message}</p>}
      {problem.conflicts?.map((c, i) => <p key={i} style={{ margin: '4px 0 0' }}>{conflictText(c)}</p>)}
      {settingsConflict && (
        <div className="mc-row" style={{ marginTop: 8, flexWrap: 'wrap', gap: 12 }}>
          <button type="button" className="mc-link" onClick={() => navigate(PATHS.profile, { state: { from: 'my' } })}>최소금액 확인하기</button>
          {data.budget && <button type="button" className="mc-link" onClick={() => navigate(PATHS.budget)}>자물쇠 확인하기</button>}
        </div>
      )}
      {problem.code === 'not_ready' && !draft && (
        <button type="button" className="mc-link" onClick={() => navigate(PATHS.protect)}>지킬 돈 확인하러 가기 ›</button>
      )}
      {problem.code === 'needs_upper_allocation_review' && !draft && (
        <button type="button" className="mc-link" onClick={() => navigate(PATHS.protect)}>지킬 돈 배분 확인하기 ›</button>
      )}
    </div>
  )
  const recoveryBox = resumeRecovery && <div className="mc-note">
    <p>{resumeRecovery.expired ? '예산 조건이 바뀌었거나 조정 대화가 만료됐어요. 다시 추천받아 주세요.'
      : resumeRecovery.canceled ? '예산 조정을 취소했어요. 다시 추천받거나 조정을 다시 열 수 있어요.' : '추천을 다시 시도하거나 조정을 다시 열 수 있어요.'}</p>
    {!resumeRecovery.expired && resumeRecovery.sourceTaskId && <button type="button" className="mc-link" disabled={busy}
      onClick={() => openRecovery(resumeRecovery.sourceTaskId)}>예산 조정 다시 열기</button>}
  </div>

  // 승인 전 초안이 없을 때: 새로 만들기 (이미 승인한 예산이 있으면 다시 추천받기)
  if (!draft) {
    return (
      <Layout title="추천 예산 확인" showBack>
        <StepBar step={6} />
        <div className="mc-eyebrow">추천 예산 확인</div>
        <h1>{data.budget?.status === 'approved' ? '이번 달 예산을 이미 쓰고 있어요' : '카테고리별 예산을 추천해 드릴게요'}</h1>
        <p className="mc-muted">추천안을 확인하고 승인해야 예산에 반영돼요. 새 추천을 만드는 동안에는 승인한 예산을 계속 사용해요.</p>
        {released.length > 0 && <p className="mc-min-warn" role="status">생활비가 바뀌어서 이번 달 자물쇠({released.map(catName).join(' · ')})를 풀었어요. 필요하면 다시 걸어 주세요.</p>}
        {problemBox}
        {recoveryBox}
        <BudgetAIConsent />
        <button type="button" className="mc-primary" onClick={makeDraft} disabled={busy || !data.cycle}>
          {busy ? '추천 만드는 중…' : data.budget ? '다시 추천받기' : '추천 예산 만들기'}
        </button>
        {!data.cycle && <p className="mc-note">급여일을 먼저 정해 주세요.</p>}
      </Layout>
    )
  }

  const { split, categories } = data
  const livingTotal = split.living
  const cats = categories.filter((c) => !OUT_OF_BUDGET.includes(c.category)).map((c, i) => ({ ...c, color: shade(i, categories.length) }))

  return (
    <Layout title="추천 예산 확인" showBack foot={foot}>
      <StepBar step={6} />
      {/* 임시 엔진(temp-…)일 때만 — ML 엔진으로 바뀌면 서버 값이 바뀌어 저절로 사라진다 */}
      <div className="mc-eyebrow">추천 예산 확인{draft.engineVersion?.startsWith('temp-') && ' · 테스트 추천'}</div>
      <h1>카테고리별 예산을<br />확인하고 고쳐 주세요</h1>
      <p className="mc-muted">지킬 돈에서 정한 생활비를 카테고리별로 나눴어요.</p>
      {data.recommendation?.spareTransfer > 0 && <p className="mc-note" role="status">여윳돈 {won(data.recommendation.spareTransfer)}을 이번 예산 생활비에만 포함해요. 기존 생활비 설정은 유지돼요.</p>}
      {data.recommendation?.targetConflicts?.map((c) => <p key={c.category} className="mc-note" role="status">{catName(c.category)} 유지 목표 {won(c.requestedAmount)} → 이번 예산 {won(c.possibleAmount)} (차이 {won(c.diff)}). {c.reason === 'amount_unit' ? '최종 금액 단위를 맞췄어요.' : '생활비와 보호 조건을 함께 반영했어요.'} 저장된 유지 목표는 그대로예요.</p>)}
      {released.length > 0 && <p className="mc-min-warn" role="status">생활비가 바뀌어서 이번 달 자물쇠({released.map(catName).join(' · ')})를 풀었어요. 필요하면 다시 걸어 주세요.</p>}

      <div className="mc-card">
        <div className="mc-row"><h2>한 달 나눠보기</h2><span className="mc-muted">월 {won(split.funding)}</span></div>
        <div className="mc-split" role="img" aria-label={splitParts(split).map(([, n, v]) => `${n} ${won(v)}`).join(', ')}>
          {splitParts(split).map(([css, n, v]) => <span key={n} className={`mc-split-${css}`} style={{ width: `${pct(v, split.funding)}%` }}></span>)}
        </div>
        <div className="mc-split-legend">
          {splitParts(split).map(([css, n, v]) => <div key={n}><i className={`mc-split-${css}`}></i><span>{n}{css === 'card' && <CardReservedHelp />}</span><strong>{won(v)}</strong></div>)}
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
                <button type="button" className="mc-lockt" aria-label={`${c.categoryName} 금액 고정`} aria-pressed={c.locked} disabled={busy} onClick={() => toggleLock(c)}>
                  <LockIcon />
                </button>
                {typeof c.reason === 'string' && c.reason.trim() && <p className="mc-note" style={{ gridColumn: '1 / -1', margin: 0, padding: 0 }}>{c.reason}</p>}
              </div>
            ))}
          </div>
          {data.nag && <p className="mc-note" style={{ padding: '10px 0 0' }}>{data.nag}</p>}
          <div className="mc-chat-alt">
            <span>꼭 바꾸고 싶은 부분이 있나요?</span>
            <button type="button" className="mc-link" disabled={busy} onClick={() => navigate(PATHS.assistant, {
              state: { budgetContext: { baseBudgetId: draft.budgetId, mode: 'INITIAL_ADJUSTMENT' } },
            })}>챗봇과 조정하기 ›</button>
          </div>
        </div>
        {problemBox}
        {recoveryBox}
        <p className="mc-note">최소 표시는 내 정보에서 정한 카테고리 최소 금액이에요. 그 밑으로는 줄지 않고, 자물쇠를 걸면 다음에 다시 추천받을 때 그 금액 그대로 고정돼요.</p>
      </form>
      <BudgetAIConsent />
    </Layout>
  )
}
