// 지킬 돈 설정 — GET · PUT /api/budgets/protect/ (docs/api/budgets.md '지킬 돈')
// 생활비 · 저축 목표액 · 월 쿠션 적립금을 정한다. 추천 금액 · 배분 기준 · 부족 판정은 서버가 계산해서 준다
// 생활비 = 한 달 생활비 전체 (별도 '최소 생활비'는 2026-10-15 기획에서 뺐다. 다음 화면에서 카테고리별로 나눈다)
// 쿠션은 '보호 하한' 대신 매달 적립할 금액으로 정한다 (2026-10-21)
// 항목 옆 % = 배분 가능한 금액(월급 - 고정지출) 중 비율. 금액을 고치면 같이 바뀐다
// 남는 돈은 여윳돈. 저축이에요 합계 > 추천 저축이면 서버가 저축을 먼저 떼고 다시 나눈 추천을 준다 (recommended.rebalanced)
// [추천 예산 보기] = 저장(PUT) 후 추천 예산 확인으로 가서 새 추천을 바로 만든다
// 마이에서 들어오면(location.state.from === 'my') 단계 표시줄 없이 버튼만 [저장]. 저장 뒤는 같다 (생활비가 바뀌면 카테고리를 다시 나눠야 해서)
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import CardSyncPanel from '../components/CardSyncPanel.jsx'
import CardReviewList from '../components/CardReviewList.jsx'
import Popup from '../popups/Popup.jsx'
import { usePopup } from '../popups/context.js'
import UnreflectedCardNote from '../components/UnreflectedCardNote.jsx'
import HelpTip from '../components/HelpTip.jsx'
import { won } from '../utils/money.js'
import { toast } from '../utils/toast.js'
import { apiSend, useApi } from '../utils/api.js'
import { waitTask } from '../utils/financeSync.js'
import { HOUSING } from '../utils/direction.js'
import { PATHS } from '../routes.js'

const RETRY_MS = 3000   // 카드 작업 상태 조회가 실패했을 때 다시 읽기까지 쉬는 시간

const ROWS = [['livingBudget', '생활비'], ['savingFloor', '저축 목표액'], ['cushionMonthly', '월 쿠션 적립금']]
const RATE_LABELS = [['living', '생활비'], ['saving', '저축'], ['spare', '여윳돈'], ['cushion', '쿠션']]
// 빠진 값 → 어디서 채우는지
const MISSING = {
  housingType: ['자취 여부', PATHS.profile, '내 정보 입력하기'],
  takeHomeIncome: ['월 실수령액', PATHS.salary, '급여 입력하기'],
  payday: ['급여일', PATHS.salary, '급여 입력하기'],
}

// 칸 아래 한 줄 안내
function hint(key, value, data) {
  if (!Number.isFinite(value) || value < 0) return '0 이상의 금액을 입력해 주세요.'
  // 11/12: 한 줄에 다 넣으면 줄바꿈이 어색해서, 기준(괄호)은 아래 작은 줄로
  if (key === 'livingBudget') return <>하루 약 <b>{won(Math.floor(value / 30 / 100) * 100)}</b> 쓸 수 있어요<small>30일 기준</small></>
  if (key === 'savingFloor' && value < data.savingsMin) return `저축 확인에서 고른 ${won(data.savingsMin)}보다 적게 정할 수 없어요.`
  if (key === 'savingFloor') return <>1년이면 <b>{won(value * 12)}</b> 모여요<small>매달 같은 금액 · 이자 제외</small></>
  if (value === 0) return data.cushion.required ? '잔액이 쿠션 목표보다 적어서 쿠션을 꼭 적립해야 해요.' : '쿠션을 적립하지 않아요.'
  const need = data.cushion.target - (data.cushion.balanceTotal ?? 0)  // 지금 잔액에서 더 모아야 할 돈
  if (need <= 0) return <>지금 잔액으로 쿠션 목표 {won(data.cushion.target)}를 이미 채웠어요.</>
  return <>약 <b>{Math.ceil(need / value)}개월</b>이면 쿠션 목표에 닿아요<small>목표 {won(data.cushion.target)} · 지금 잔액 기준</small></>
}

// 입력칸 아래 '기존보다 +50,000원' (11/12). 저장해 둔 값이 없거나(처음 설정) 같으면 빈 칸(격자 자리만 차지)
function Delta({ now, before }) {
  if (before == null || !Number.isFinite(now) || now === before) return <small className="mc-protect-delta" aria-hidden="true" />  // 자리만
  const up = now > before
  return <small className={`mc-protect-delta ${up ? 'is-up' : 'is-down'}`}>기존보다 {up ? '+' : '−'}{won(Math.abs(now - before))}</small>
}

// 지킬 돈 카드 보류 이유 묶음 (budgets.json cardPending[].reason)
const REVIEW = (r) => r === 'card_assignment_required'
const SYNC = (r) => ['bill_history_incomplete', 'spending_history_incomplete', 'installment_amount_unknown'].includes(r)
const NOT_YET = (r) => r === 'first_bill_unavailable'   // 조회는 됐지만 이번 주기 청구서가 아직 안 보임 (실패 아님)
const DATA = (r) => !REVIEW(r) && !SYNC(r) && !NOT_YET(r)

export default function ProtectPage() {
  const navigate = useNavigate()
  const { state: navState } = useLocation()
  const manage = navState?.from === 'my'
  const go = (to) => navigate(to, { state: navState })  // 고치러 가는 화면도 마이에서 왔으면 관리 모드로
  const { data, error: loadError, reload } = useApi('/api/budgets/protect/')
  const [edits, setValues] = useState(null)  // 고친 칸 글자 그대로 (빈 칸도 허용하려고 문자열로). 안 고쳤으면 null
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [busy, setBusy] = useState(false)
  const [showCardSync, setShowCardSync] = useState(false)
  const { openPopup, closePopup } = usePopup()
  const [reviewVersion, setReviewVersion] = useState(0)
  // 11/09: 카드 내역을 서버가 뒤에서 불러오는 중이면 재조회를 권하지 않고 기다렸다가 끝나면(성공 · 실패 모두) 다시 읽는다
  // 11/10: 상태 조회가 끊기거나 5분을 넘기면 잠깐 쉬고 다시 읽는다. 다시 읽을 때마다(data 가 바뀔 때마다) 조회를 새로 시작해서,
  //   서버가 아직 같은 작업 ID 를 줘도 멈추지 않는다. 작업이 끝나거나 기한이 지나면 ID 가 빠져 조회도 멈춘다
  const loadingTask = data?.cardLoadTaskId ?? null
  useEffect(() => {
    if (!loadingTask) return undefined
    const ctrl = new AbortController()
    waitTask(loadingTask, { signal: ctrl.signal })
      .then(() => null, () => new Promise((r) => setTimeout(r, RETRY_MS)))
      .then(() => { if (!ctrl.signal.aborted) reload() })
    return () => ctrl.abort()
  }, [loadingTask, data]) // eslint-disable-line react-hooks/exhaustive-deps -- reload 는 렌더마다 새로 만들어져 넣으면 계속 다시 돈다


  if (!data) return <Layout title="지킬 돈 설정" showBack><ApiStatus error={loadError} /></Layout>

  const { base, blocked, recommended: rec, cushion } = data
  // 보류 카드를 이유별로 (아래 REVIEW · SYNC · NOT_YET · DATA)
  const idsFor = (group) => [...new Set((data.cardPending ?? []).filter((item) => group(item.reason)).map((item) => item.cardId))]
  const needsCardReview = idsFor(REVIEW).length > 0
  // 11/06: 카드값 선차감 안내 — 중복을 확인하지 못한 금액은 빼지 않았음(넉넉하게). 확인 가능한 금액과 모르는 부분을 나눠 보여 준다
  const notes = data.cardNotes ?? []
  const uncertain = notes.filter((n) => n.reason === 'card_overlap_uncertain')
  const incomplete = notes.some((n) => n.reason === 'bill_items_incomplete')
  const foreignCount = notes.filter((n) => n.reason === 'foreign_amount_pending').reduce((sum, n) => sum + n.count, 0)
  const mismatch = idsFor((r) => r === 'bill_total_mismatch').length > 0
  // 카드값을 반영해 다시 추천받는 경로에서는 새 가용 금액으로 나눈 추천값부터 보여 준다.
  const start = navState?.refreshRecommendation ? rec : data.saved ?? rec
  const values = edits ?? (start ? Object.fromEntries(ROWS.map(([k]) => [k, String(start[k])])) : null)
  const housingName = HOUSING[data.housingType]?.name
  const rateLine = data.rates ? RATE_LABELS.map(([k, n]) => `${n} ${data.rates[k]}%`).join(' · ') : ''
  const num = (k) => (!values || values[k] === '' ? NaN : Number(values[k]))
  const available = base?.available ?? 0
  const total = ROWS.reduce((sum, [k]) => sum + num(k), 0)
  const left = available - total   // 남으면 여윳돈 (아직 어느 예산에도 배정 안 된 돈)
  const bad = !Number.isFinite(total) || ROWS.some(([k]) => num(k) < 0)
  const share = (v) => (Number.isFinite(v) && v >= 0 && available > 0 ? `${Math.round((v / available) * 100)}%` : '–')
  const ready = data.missing.length === 0 && !blocked && rec && values

  const onSubmit = async (e) => {
    e.preventDefault()
    if (!ready || busy) return
    if (bad) { setError('금액을 확인해 주세요.'); return }
    // 11/12: 배분 가능한 금액을 넘으면 서버에 보내지 않는다. 아래 합계 칸에 이미 빨간 안내가 있으니
    // 같은 문장을 또 붙이지 않고, 토스트 + 맨 위로 올려서 입력칸부터 다시 보게 한다
    if (left < 0) {
      toast('금액을 줄여 주세요')
      document.getElementById('mc-main')?.scrollTo?.({ top: 0, behavior: 'smooth' })
      return
    }
    // 11/13: 이미 승인한 예산이 있고 생활비는 그대로면(저축 목표액 · 월 쿠션 적립금만 고침) 카테고리 배분이 바뀌지 않으니
    // 다시 추천(LLM)하지 않고 저장만 한다(승인 예산의 저축 · 쿠션 줄은 서버가 같이 바꿈). 월급 차이 · 카드 청구서로 다시 짜러 온 경우(refreshRecommendation)는 제외
    if (manage && data.budgetApproved && !navState?.refreshRecommendation && num('livingBudget') === data.saved?.livingBudget) {
      saveOnly()
      return
    }
    // 11/13: 마이에서 들어왔으면 저장 전에 가운데 창으로 '예산을 다시 추천한다'를 알리고, [예산 확인하러 가기]를 눌러야 저장한다.
    // 닫으면 저장하지 않는다. 온보딩은 원래 다음 단계가 추천이라 바로 저장
    if (manage) {
      openPopup(
        <Popup label="예산 다시 추천" dialog>
          <h2>예산을 다시 추천해요</h2>
          <p className="mc-muted">저장하면 바뀐 금액으로 예산을 다시 추천해요.<br />새 추천을 승인해야 지금 예산이 바뀌어요.</p>
          <button type="button" className="mc-primary" onClick={() => { closePopup(); save() }}>예산 확인하러 가기</button>
        </Popup>,
      )
      return
    }
    save()
  }

  // 생활비를 안 바꾼 저장: 서버가 이번 주기 승인 예산의 저축 · 쿠션 줄도 바꾼다(카테고리 배분은 그대로, 여윳돈은 같이 맞춰짐, 검수 P1).
  // 바꿀 수 없으면(approvedBudgetUpdated=false, 합계가 승인 당시 배분 대상을 넘음 등) 다시 추천으로 보낸다
  const saveOnly = async () => {
    setBusy(true)
    setError('')
    setFieldErrors({})
    try {
      const saved = await apiSend('/api/budgets/protect/', 'PUT', Object.fromEntries(ROWS.map(([k]) => [k, num(k)])))
      if (saved.approvedBudgetUpdated === false) {
        navigate(PATHS.recommend, { state: { autoRecommend: true, refreshRecommendation: false, locksReleased: saved.locksReleased } })
        return
      }
      toast('저장했어요. 이번 예산에도 반영했어요')
      navigate(PATHS.my, { replace: true })
    } catch (err) {
      setError(err.fields?.nonFieldErrors?.[0] ?? err.message)
      setFieldErrors(err.fields ?? {})
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    setError('')
    setFieldErrors({})
    try {
      const saved = await apiSend('/api/budgets/protect/', 'PUT', Object.fromEntries(ROWS.map(([k]) => [k, num(k)])))
      // 저장 뒤 새 추천을 바로 만든다. 생활비가 바뀌어 풀린 자물쇠(P-13)는 다음 화면에서 알려 준다
      navigate(PATHS.recommend, { state: { autoRecommend: true, refreshRecommendation: Boolean(navState?.refreshRecommendation), locksReleased: saved.locksReleased } })
    } catch (err) {
      setError(err.fields?.nonFieldErrors?.[0] ?? err.message)
      setFieldErrors(err.fields ?? {})
    } finally {
      setBusy(false)
    }
  }

  const foot = <button className="mc-primary" form="mc-form" type="submit" disabled={!ready || busy}>{busy ? '저장하는 중…' : manage ? '저장' : '추천 예산 보기'}</button>

  return (
    <Layout title="지킬 돈 설정" showBack foot={foot}>
      {!manage && <StepBar step={5} />}
      <div className="mc-eyebrow">지킬 돈 설정</div>
      <h1>먼저 지킬 돈을 정해요</h1>
      {ready && <p className="mc-muted">{housingName} 비율로 나눈 추천 금액이에요. 바꾸고 싶은 칸만 고치세요.</p>}
      {/* 11/13: 이번 주기만 실제 받은 월급으로 나누는 경우 */}
      {ready && base.incomeSource === 'actual' && <p className="mc-note" role="note">이번 주기는 실제로 받은 월급 {won(base.takeHomeIncome)}으로 나눠요.</p>}
      {data.approvedBudget?.spareTransfer > 0 && <p className="mc-note" role="status" aria-label="이번 승인 예산 여윳돈">
        이번 승인 예산에는 여윳돈 {won(data.approvedBudget.spareTransfer)}을 생활비로 포함했어요.
        {' '}이번 예산 배분 기준 여윳돈은 {won(data.approvedBudget.spare)}이고, 아래 지킬 돈 설정은 그대로예요.
      </p>}

      {data.approvedBudget && <section className="mc-card" aria-label="이번 주기 승인 예산">
        <h2>이번 주기 승인 예산</h2>
        <p className="mc-muted">이번 주기에 승인한 금액이에요. 아래 입력칸은 저장한 지킬 돈 설정이에요.</p>
        {data.approvedBudget.spareTransfer > 0 && <p className="mc-note" role="status">
          여윳돈 {won(data.approvedBudget.spareTransfer)}을 생활비에 포함했어요. 이번 예산 배분 기준 여윳돈은 {won(data.approvedBudget.spare)}이에요.
        </p>}
        {[
          ['저축', data.approvedBudget.savingFloor, data.approvedBudget.savingTransfer, data.saved?.savingFloor],
          ['월 쿠션 적립금', data.approvedBudget.cushionMonthly, data.approvedBudget.cushionTransfer, data.saved?.cushionMonthly],
        ].map(([label, amount, transfer, savedAmount]) => <div className="mc-keyvalue" key={label}>
          <span>{label}{transfer > 0 && <small> · 생활비로 {won(transfer)} 이동</small>}
            {savedAmount != null && savedAmount !== amount && <small> · 저장한 설정과 달라요</small>}</span>
          <strong>{won(amount)}</strong>
        </div>)}
      </section>}

      {data.missing.length > 0 && (
        <div className="mc-card" role="alert">
          <h2>먼저 입력해야 할 정보가 있어요</h2>
          <p className="mc-muted" style={{ margin: '6px 0 0' }}>{data.missing.map((m) => MISSING[m]?.[0] ?? m).join(' · ')}이 있어야 지킬 돈을 나눌 수 있어요.</p>
          <button type="button" className="mc-secondary" onClick={() => go(MISSING[data.missing[0]]?.[1] ?? PATHS.salary)}>
            {MISSING[data.missing[0]]?.[2] ?? '입력하러 가기'}
          </button>
        </div>
      )}

      {blocked?.reason === 'card_bill_pending' && loadingTask && (
        <div className="mc-card" role="status">
          <h2>카드 내역을 불러오는 중이에요</h2>
          <p className="mc-muted" style={{ margin: '6px 0 0' }}>카드사 청구서와 사용 내역을 뒤에서 불러오고 있어요. 1~2분쯤 걸리고, 끝나면 이 화면이 저절로 새로 고쳐져요.</p>
        </div>
      )}
      {blocked?.reason === 'card_bill_pending' && !loadingTask && <>
        {/* 보류 이유별로 필요한 것만 (11/06): 카드 지정 → 결제 카드 선택 · 납부 미확인 → 앞으로 낼 금액 · 조회 기간 누락 → 재조회 · 자료 이상 → 안내 (+ 원하면 재조회) */}
        {needsCardReview && (
          <div className="mc-card" role="alert">
            <h2>결제 카드를 확인할 거래가 있어요</h2>
            <p className="mc-muted" style={{ margin: '6px 0 0' }}>어떤 카드로 결제했는지 확인하면 카드 내역을 반영해 지킬 돈을 다시 계산할 수 있어요.</p>
            {/* 11/9: 아래로 펼치기 → 가운데 창(다이얼로그)으로 */}
            <button type="button" className="mc-secondary" onClick={() => openPopup(
              <Popup label="결제 카드 확인" dialog>
                <h2>결제 카드 확인</h2>
                <CardReviewList key={reviewVersion} cardIds={idsFor(REVIEW)} onChanged={reload} />
              </Popup>,
            )}>결제 카드 확인하기</button>
            {/* 11/12: '카드 내역도 다시 불러오기'는 뺐다 — 결제 카드만 고르면 끝나는 일이라 재조회가 필요 없다 (불러오기가 필요하면 아래 카드가 따로 뜬다) */}
          </div>
        )}
        {idsFor(SYNC).length > 0 && (
          <div className="mc-card" role="alert">
            <h2>카드 내역을 다시 불러와야 해요</h2>
            <p className="mc-muted" style={{ margin: '6px 0 0' }}>이번 급여 주기의 청구서나 카드 사용 내역 중 아직 불러오지 못한 기간이 있어요. 해당 카드사의 내역을 다시 불러와 주세요.</p>
            <CardSyncPanel cardIds={idsFor(SYNC)} onChanged={() => { reload(); setReviewVersion((version) => version + 1) }} />
          </div>
        )}
        {idsFor(NOT_YET).length > 0 && (
          <div className="mc-card" role="alert">
            <h2>이번 급여 주기 카드 청구서를 아직 찾지 못했어요</h2>
            <p className="mc-muted" style={{ margin: '6px 0 0' }}>불러온 내역에는 이번 주기에 결제일인 청구서가 없어요. 카드사가 청구서를 내면 다시 불러와서 확인할 수 있어요.</p>
            <button type="button" className="mc-link" onClick={() => setShowCardSync((shown) => !shown)} aria-expanded={showCardSync}>
              {showCardSync ? '다시 불러오기 접기' : '카드 내역 다시 불러오기'}
            </button>
            {showCardSync && <CardSyncPanel cardIds={idsFor(NOT_YET)} onChanged={reload} />}
          </div>
        )}
        {idsFor(DATA).length > 0 && (
          <div className="mc-card" role="alert">
            <h2>{mismatch ? '카드 청구서의 원래 청구액을 확인해야 해요' : '카드 청구서 내용을 확인할 수 없어요'}</h2>
            <p className="mc-muted" style={{ margin: '6px 0 0' }}>{mismatch
              ? '카드사가 준 청구 총액이 청구서 상세 합계보다 작아서, 이번 청구서에서 원래 내기로 한 금액을 알 수 없어요. 0원이나 상세 합계로 정하지 않고 계산을 미뤘어요.'
              : '카드사가 준 청구서에 결제일 · 명세서 종류 · 금액 등이 빠져 있어 계산을 미뤘어요. 카드사 응답이 바뀌었을 수 있으면 다시 불러와 볼 수 있어요.'}</p>
            <button type="button" className="mc-link" onClick={() => setShowCardSync((shown) => !shown)} aria-expanded={showCardSync}>
              {showCardSync ? '다시 불러오기 접기' : '카드 내역 다시 불러오기'}
            </button>
            {showCardSync && <CardSyncPanel cardIds={idsFor(DATA)} onChanged={reload} />}
          </div>
        )}
      </>}

      {/* 청구서 없는 카드사 안내는 한 줄 띠로 위에 둔다. 11/11: 줄 안의 [다시 불러오기 · 접기] 대신 홈과 같은 흐름
          (띠 → 안내 시트 → 청구서 확인 창). 새 청구서가 나오면 이 화면에서 바로 새 추천값으로 다시 보여 준다 */}
      {!blocked && <UnreflectedCardNote notes={notes} compact budgetStarted={manage} onRecalculate={() => {
        setValues(null)
        reload()
        navigate(PATHS.protect, { replace: true, state: { ...navState, refreshRecommendation: true } })
      }} />}

      {/* 11/13: 고정지출 · 카드값 · 저축을 월급과 줄 맞춰 비교하고, 모자란 돈을 빨강으로. 고칠 수 있는 것만 버튼으로 */}
      {blocked && blocked.reason !== 'card_bill_pending' && (() => {
        const savingsCase = blocked.reason === 'savings_over_available'
        const card = base.cardReserved ?? 0
        return (
          <div className="mc-card" role="alert">
            <h2>{savingsCase ? '저축까지 하면 월급이 모자라요' : '고정지출이 월급보다 많아요'}</h2>
            <dl className="mc-compare">
              <div><dt>월급</dt><dd>{won(base.takeHomeIncome)}</dd></div>
              <div><dt>고정지출</dt><dd>−{won(base.fixedTotal - card)}</dd></div>
              {card > 0 && <div><dt>이번 주기 카드값</dt><dd>−{won(card)}</dd></div>}
              {savingsCase && <div><dt>꼭 할 저축</dt><dd>−{won(data.savingsMin)}</dd></div>}
              <div className="mc-compare-total">
                <dt>{blocked.shortfall > 0 ? '모자란 돈' : '남는 돈'}</dt>
                <dd className={blocked.shortfall > 0 ? 'is-short' : undefined}>{blocked.shortfall > 0 ? `−${won(blocked.shortfall)}` : won(0)}</dd>
              </div>
            </dl>
            <p className="mc-muted" style={{ margin: '10px 0 0' }}>
              {savingsCase ? '정기 저축이나 고정지출을 줄이면 생활비를 나눌 수 있어요.' : '고정지출을 줄이거나 월급을 다시 확인해 주세요.'}
              {card > 0 && <><br />카드값은 이번 주기에 결제되는 청구서 금액이에요.</>}
            </p>
            <div className="mc-two" style={{ marginTop: 12, gap: 8 }}>
              {savingsCase
                ? <button type="button" className="mc-secondary" onClick={() => go(PATHS.savings)}>정기 저축 고치기</button>
                : <button type="button" className="mc-secondary" onClick={() => go(PATHS.salary)}>월급 확인</button>}
              <button type="button" className="mc-secondary" onClick={() => go(PATHS.fixed)}>고정지출 확인</button>
            </div>
          </div>
        )
      })()}

      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        {ready && <>
        <div className="mc-card" style={{ marginBottom: 0 }}>
          <div className="mc-row">
            {/* 11/12: 맨 아래 '월급에서 고정지출을 뺀 금액을 … 비율로 나눴어요' 안내를 [?] 가운데 창으로 옮김 */}
            <h2>추천안 · {housingName} 기준<HelpTip label="추천안을 나눈 기준" title={`${housingName} 비율로 나눴어요`} box={rateLine}>
              <li>월급 {won(base.takeHomeIncome)}에서 고정지출 {won(base.fixedTotal - (base.cardReserved ?? 0))}{base.cardReserved > 0 && <>과 이번 주기 카드값 {won(base.cardReserved)}</>}을 뺀 금액을 나눴어요.</li>
              {rec.rebalanced && <li>꼭 할 저축이 더 크면 저축을 먼저 떼고 나머지를 같은 비율로 나눠요.</li>}
              <li>자취 여부는 내 정보에서 바꿀 수 있어요.</li>
            </HelpTip></h2>
            <button type="button" className="mc-link" onClick={() => { setValues(Object.fromEntries(ROWS.map(([k]) => [k, String(rec[k])]))); setError(''); setFieldErrors({}) }}>추천값으로</button>
          </div>
          {/* 11/12: '저축을 먼저 뗐어요' 카드는 위에 따로 있어 잘 안 보이고 글이 길어서, 추천안 안에 짧은 한 줄로 */}
          {rec.rebalanced && <p className="mc-protect-rebalanced" role="status">
            꼭 할 저축 {won(data.savingsMin)}이 추천보다 커서<br />저축을 먼저 떼고 나머지를 나눴어요.
          </p>}
          <div className="mc-protected-values">
            {ROWS.map(([k, label]) => {
              const edited = num(k) !== rec[k]  // 추천값과 다르면 칸을 '고침' 모양으로
              return (
                <div className="mc-protected-item" key={k}>
                  {/* 11/12: 두 줄 격자 — [이름 · 비율 | 입력칸] / [추천 ○원 | 기존보다 ±○원].
                      '기존보다' 칸은 바뀐 게 없어도 자리를 지켜서, 칸마다 추천 칸과 아래 설명 사이 간격이 같다 */}
                  <label className="mc-protect-line" htmlFor={`mc-p-${k}`}>
                    <span><span className="mc-protect-name">{label}</span><b className="mc-protect-pct" aria-label={`배분 가능한 금액의 ${share(num(k))}`}>{share(num(k))}</b></span>
                    <span className="mc-cat-input-wrap">
                      <MoneyInput aria-label={label} className={edited ? 'mc-cat-input mc-protect-input is-edited' : 'mc-cat-input mc-protect-input'}
                        id={`mc-p-${k}`} name={k}
                        value={values[k]} onChange={(digits) => { setValues({ ...values, [k]: digits }); setError(''); setFieldErrors({}) }} />
                      <span>원</span>
                    </span>
                    <small className="mc-cat-rec" id={`mc-p-rec-${k}`}>추천 {won(rec[k])}</small>
                    {/* 저장해 둔 금액에서 얼마나 바뀌었는지: 생활비를 줄인 만큼 저축 · 쿠션을 얼마나 올릴지 감을 잡게 */}
                    <Delta now={num(k)} before={data.saved?.[k]} />
                  </label>
                  <p aria-live="polite" className={fieldErrors[k] ? 'mc-projection mc-error' : 'mc-projection'} id={`mc-hint-${k}`}>{fieldErrors[k]?.[0] ?? hint(k, num(k), data)}</p>
                </div>
              )
            })}
          </div>
        </div>

        <div aria-live="polite" className="mc-card" id="mc-protectsum" style={{ marginTop: 14 }}>
          {bad ? <p className="mc-error">금액을 확인해 주세요.</p> : <>
            <strong>{won(total)}</strong> <span className="mc-muted">/ 배분 가능한 금액 {won(available)}</span>
            <p className={left < 0 ? 'mc-error' : 'mc-muted'} style={{ margin: '4px 0 0' }}>
              {left === 0 ? '배분 가능한 금액에 딱 맞게 나눴어요.'
                : left < 0 ? <>배분 가능한 금액보다 {won(-left)} 많아요.<br />입력 금액을 줄여 주세요.</>
                : `남는 ${won(left)}(${share(left)})은 여윳돈으로 둬요.`}
            </p>
          </>}
        </div>
        </>}

        {cushion.target !== null && (
          <div className="mc-card mc-cjudge">
            <div className="mc-row"><h2>머니 쿠션</h2><span className="mc-badge">{cushion.required ? '필수' : '선택'}</span></div>
            <div className="mc-row" style={{ margin: '10px 0 2px' }}><span className="mc-muted">목표액</span><strong>{won(cushion.target)}</strong></div>
            <p className="mc-note" style={{ padding: 0 }}>갑작스러운 지출에 대비해 모아 두는 돈이에요. 목표는 머니쿠션 화면에서 바꿀 수 있어요.</p>
          </div>
        )}
        {error && <p className="mc-error" role="alert">{error}</p>}
      </form>
      {/* 진행을 막지 않는 작은 카드 안내는 추천 금액을 먼저 보도록 맨 아래에 둔다 */}
      {notes.some((n) => n.reason === 'no_bill_in_cycle') && !blocked && (
        <p className="mc-note">이번 급여 주기에 결제일인 카드 청구서가 없어서 첫 카드값은 따로 떼지 않았어요.</p>
      )}
      {foreignCount > 0 && !blocked && (
        <p className="mc-note" role="note">이번 급여 주기 해외 결제 {foreignCount}건은 카드사가 원화 금액을 주지 않았고 청구서·계좌 출금에서도 찾지 못해 쓴 돈에 넣지 못했어요. 실제로 쓴 돈은 표시보다 많을 수 있어요.</p>
      )}
      {(uncertain.length > 0 || incomplete) && !blocked && (
        <div className="mc-note" role="note">
          <p style={{ margin: 0 }}>일부 카드 사용의 중복 여부를 확인하지 못해 카드값을 넉넉하게 잡았어요. 실제 사용 가능한 예산은 표시보다 많을 수 있어요.</p>
          {uncertain.map((n) => <p key={n.cardId} style={{ margin: '4px 0 0' }}>확인하지 못한 이번 주기 사용 {n.count}건 · {won(n.amount)}</p>)}
          {incomplete && <p style={{ margin: '4px 0 0' }}>카드사 청구서 상세가 일부만 와서, 빠진 항목은 확인하지 못했어요.</p>}
        </div>
      )}
    </Layout>
  )
}
