// 고정지출 확인 · 고정지출 관리 — 한 화면을 두 곳에서 쓴다 (docs/api/fixed.md)
//   첫 설정 흐름: 월급 다음, 저축 확인 전. 자동으로 찾은 후보(suggested)를 확인한다 (아래 OnboardingView)
//   마이 > 고정지출 · 홈 '꼭 나갈 돈' (location.state.from === 'my' | 'home'): 확정된 고정지출 + [수정] · 중지 · 미납 [고르기] (ManageView)
// 목록 = GET /api/budgets/fixed/. [고정지출 맞아요] = 개별 확정, [확인하고 다음] = 남은 후보 모두 확정, [고정지출 아니에요] = 무시
// 감지 조건 (요구사항 TXN-008): 같은 받는 곳으로 한 달에 1건 · 매달 같은 날 ±3일 · 같은 금액(±10%) · 3달 연속
// 결제 확인 (10/21 결정, C-14 · C-16): 서버가 찾은 비슷한 결제를 evidenceSuggestion 으로 준다 (있을 때만 묻는다)
//   1건 = "이 결제가 맞나요? [맞아요] [아니에요]" / 여러 건 = "비슷한 결제가 N건 있어요 [고르기]" → EvidencePopup
//   [맞아요] · 고른 결제 = PATCH /api/budgets/fixed/<id>/ { evidenceTransactionId }. [아니에요] = 요청 없음 (다음 달 다시 물어봄)
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePopup } from '../popups/context.js'
import FixedPopup from '../popups/FixedPopup.jsx'
import EvidencePopup from '../popups/EvidencePopup.jsx'
import Popup from '../popups/Popup.jsx'
import TransactionPopup from '../popups/TransactionPopup.jsx'
import CardBillCheck from '../components/CardBillCheck.jsx'
import { CHIPS, toView } from '../utils/transactions.js'
import { won } from '../utils/money.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import { pickEvidence, shortDate } from '../utils/fixed.js'
import { PATHS } from '../routes.js'

// 저장 뒤 같은 목록을 다시 받는다 (같은 주소 reload — 받는 동안 지난 목록 유지, 실패하면 오류 카드)
const useFixedList = () => useApi('/api/budgets/fixed/')

// 목록 한 줄에 보일 결제 수단. 연결 해제면 다시 고르게 안내
function payLabel(f) {
  const method = f.paymentAccount ?? f.paymentCard
  if (!method) return '출금 계좌 확인 필요'
  return method.status === 'disconnected' ? `${method.displayName} (연결 해제됨 · 다시 고르기)` : method.displayName
}

// 카테고리는 이름 옆에 작게 — 아래 줄(출금일 · 결제 수단)이 길어지지 않게
const CatTag = ({ f }) => <span className="mc-muted" style={{ fontSize: 12, marginLeft: 6 }}>{f.categoryName}</span>
const dayLabel = (f, unknown) => (f.paymentDay ? `매월 ${f.paymentDay}일` : unknown)
const lateDue = (f) => f.recentDues?.find((d) => d.paymentState === 'needs_review')

// 결제 확인 한 줄: 1건이면 맞아요/아니에요, 여러 건이면 고르기
function Suggestion({ f, onSaved }) {
  const { openPopup } = usePopup()
  const [state, setState] = useState({ busy: false, error: '', dismissed: false })
  const { count, candidates } = f.evidenceSuggestion
  if (state.dismissed) return null
  if (count > 1) {
    return (
      <div className="mc-row mc-note" style={{ paddingBottom: 0 }}>
        <span>비슷한 결제가 {count}건 있어요</span>
        <button type="button" className="mc-link" onClick={() => openPopup(<EvidencePopup item={f} onSaved={onSaved} />)}>고르기</button>
      </div>
    )
  }
  const t = candidates[0]
  const yes = async () => {
    if (state.busy) return
    setState({ busy: true, error: '', dismissed: false })
    try {
      await pickEvidence(f, t.transactionId)
      onSaved()
    } catch (err) {
      setState({ busy: false, error: err.fields?.evidenceTransactionId?.[0] ?? err.message, dismissed: false })
    }
  }
  return (
    <div className="mc-note" style={{ paddingBottom: 0 }}>
      <p style={{ margin: 0 }}>이 결제가 {f.name} 맞나요? {shortDate(t.date)} {t.merchantName} {won(t.amount)}</p>
      {/* 아니에요 = 요청 없음 (다음 달 다시 물어봄) */}
      <div className="mc-inline">
        <button type="button" className="mc-secondary" disabled={state.busy} onClick={yes}>{state.busy ? '저장 중…' : '맞아요'}</button>
        <button type="button" className="mc-secondary" disabled={state.busy} onClick={() => setState({ ...state, dismissed: true })}>아니에요</button>
      </div>
      {state.error && <p className="mc-error" role="alert">{state.error}</p>}
    </div>
  )
}

// 미납 확인 필요 회차 한 줄 (결정 G) — 서버가 늦게 낸 출금 후보를 줬을 때만 [고르기]
function LateDue({ f, due, onSaved }) {
  const { openPopup } = usePopup()
  const canPick = f.evidenceSuggestion?.dueId === due.dueId
  return (
    <div className="mc-fixed-late" role="note">
      <p><b>{shortDate(due.dueDate)} {f.name} 출금을 아직 못 찾았어요.</b><br />{canPick ? '늦게 냈으면 그 출금을 골라 주세요. 고르면 납부 완료로 바뀌어요.' : '비슷한 출금이 들어오면 고를 수 있게 알려 드릴게요.'}</p>
      {canPick && <button type="button" className="mc-secondary" onClick={() => openPopup(<EvidencePopup late item={f} onSaved={onSaved} />)}>고르기</button>}
    </div>
  )
}

// 이번 달 낸 거래 → 거래 내역과 같은 상세 팝업 (거래 한 건을 불러와서 연다)
function PaidTxPopup({ id, onSaved }) {
  const { data, error } = useApi(`/api/finance/transactions/${id}/`)
  if (!data) return <Popup label="거래 상세" sub><ApiStatus error={error} /></Popup>
  return <TransactionPopup tx={toView(data)} date={shortDate(data.occurredOn)} categories={CHIPS} onSaved={onSaved} />
}

// 마이 > 고정지출 · 홈 '꼭 나갈 돈'에서 열기
function ManageView() {
  const { openPopup } = usePopup()
  const { data, error, reload } = useFixedList()
  if (!data) return <Layout title="고정지출" showBack><ApiStatus error={error} /></Layout>

  const confirmed = data.items.filter((f) => f.status === 'confirmed')
  const late = confirmed.filter(lateDue)
  const noDay = confirmed.filter((f) => !f.paymentDay)
  // 확인할 것이 있는 항목을 먼저 보여 준다
  const rows = [...late, ...noDay.filter((f) => !late.includes(f)), ...confirmed.filter((f) => !late.includes(f) && !noDay.includes(f))]

  return (
    <Layout title="고정지출" showBack>
      <div className="mc-eyebrow">고정지출</div>
      <h1>매달 나가는 돈</h1>
      <p className="mc-muted">여기 있는 돈은 예산에서 먼저 떼어 둬요. 금액을 바꾸거나 그만두면 다음 달 예산부터 반영돼요.</p>
      {(late.length > 0 || noDay.length > 0) && (
        <p className="mc-note" style={{ paddingTop: 0 }}>
          확인할 것 {late.length + noDay.length}건{late.length > 0 && ` · 미납 확인 ${late.length}건`}{noDay.length > 0 && ` · 출금일 모름 ${noDay.length}건`}
        </p>
      )}
      {confirmed.length === 0 && (
        <div className="mc-card"><p className="mc-muted" style={{ margin: 0 }}>등록된 고정지출이 없어요. 월세 · 통신비처럼 매달 나가는 돈이 있으면 추가해 주세요.</p></div>
      )}

      {rows.map((f) => (
        <div className="mc-card" key={f.recurringId}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{f.name}</strong><CatTag f={f} /><span className="mc-muted" style={{ display: 'block', marginTop: 4 }}>{dayLabel(f, '출금일 모름')} · {payLabel(f)}</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(f.amount)}</strong>
          </div>
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">{f.sourceType === 'manual' ? '직접 추가' : '자동으로 찾음'}</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<FixedPopup item={f} onSaved={reload} />)}>{f.paymentDay ? '수정' : '출금일 정하기'}</button>
          </div>
          {lateDue(f) && <LateDue f={f} due={lateDue(f)} onSaved={reload} />}
          {!lateDue(f) && f.paidThisCycle && (
            <div className="mc-row mc-note" style={{ paddingBottom: 0 }}>
              <span>이번 달 납부 완료 · {shortDate(f.paidThisCycle.date)} {f.paidThisCycle.merchantName} {won(f.paidThisCycle.amount)}</span>
              <button type="button" className="mc-link" onClick={() => openPopup(<PaidTxPopup id={f.paidThisCycle.transactionId} onSaved={reload} />)}>거래 보기</button>
            </div>
          )}
          {!lateDue(f) && f.evidenceSuggestion && <Suggestion f={f} onSaved={reload} />}
          {!f.paymentDay && <p className="mc-note" style={{ paddingBottom: 0 }}>출금일을 정하면 홈 '꼭 나갈 돈'에 나오고, 출금됐는지 확인해 드려요.</p>}
        </div>
      ))}
      <button type="button" className="mc-secondary" onClick={() => openPopup(<FixedPopup onSaved={reload} />)}>+ 고정지출 직접 추가</button>

      <div className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>매달 나가는 고정지출</span><strong>{won(data.summary.confirmedMonthlyTotal)}</strong></div>
        <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>카드로 내는 고정지출은 카드값 안에 들어 있어서 따로 빼지 않아요.</p>
      </div>
    </Layout>
  )
}

// 고정지출 확인 · 카드사 한 줄 (11/06 단순화): 청구서마다 '앞으로 얼마를 더 내야 하나요?'만 묻는다 (CardBillCheck)
//   확인하지 않아도 다음으로 갈 수 있다. 첫 예산에 필요한데 모르면 지킬 돈 단계에서 다시 묻는다
function CardBillRow({ bill, onSaved }) {
  if (bill.totalAmount === null) {
    return (
      <div style={{ marginTop: 14 }}>
        <strong>{bill.issuerName}</strong>
        <p className="mc-muted">{bill.reason === 'cycle_unavailable' ? '월급 정보를 설정하면 이번 주기 청구서를 확인할 수 있어요.' : '청구서 확인 필요 · 카드 관리에서 이번 주기 청구서를 불러와 주세요.'}</p>
      </div>
    )
  }
  return (
    <div style={{ marginTop: 14 }} aria-label={`${bill.issuerName} 청구서`}>
      {bill.bills.map((b) => <CardBillCheck key={b.billId} bill={b} issuerName={bill.issuerName} onSaved={onSaved} />)}
    </div>
  )
}

// 첫 설정 흐름 · 고정지출 확인
function OnboardingView() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const { data, error, reload } = useFixedList()
  const [saving, setSaving] = useState(false)
  const [refreshingData, setRefreshingData] = useState(null)
  const busy = saving || (refreshingData !== null && data === refreshingData)
  const [actionError, setActionError] = useState('')

  // 후보 확정 · 무시. 누를 때 요청 번호를 새로 만든다 (버튼을 잠가서 응답 전 재전송은 없다)
  const decide = async (path, ids, refresh = false) => {
    setSaving(true)
    setActionError('')
    try {
      await apiSend(path, 'POST', { recurringIds: ids }, { requestKey: newRequestKey() })
      if (refresh) {
        setRefreshingData(data)
        reload()
      }
      return true
    } catch (err) {
      setActionError(err.fields?.recurringIds?.[0] ?? err.message)
      return false
    } finally {
      setSaving(false)
    }
  }

  if (!data) return <Layout title="고정지출 확인" showBack><StepBar step={4} /><ApiStatus error={error} /></Layout>
  const suggested = data.items.filter((f) => f.status === 'suggested')

  const onNext = async () => {
    if (busy) return
    if (suggested.length && !(await decide('/api/budgets/fixed/confirm/', suggested.map((f) => f.recurringId)))) return
    navigate(PATHS.savings)
  }
  const confirm = (f) => { if (!busy) return decide('/api/budgets/fixed/confirm/', [f.recurringId], true) }
  const ignore = (f) => { if (!busy) return decide('/api/budgets/fixed/ignore/', [f.recurringId], true) }

  const foot = <button type="button" className="mc-primary" disabled={busy} onClick={onNext}>{busy ? '저장 중…' : suggested.length ? '확인하고 다음' : '계속하기'}</button>
  return (
    <Layout title="고정지출 확인" showBack foot={foot}>
      <StepBar step={4} />
      <div className="mc-eyebrow">고정지출 확인</div>
      <h1>매달 이 돈이 나가나요?</h1>
      {suggested.length > 0
        ? <p className="mc-muted">매달 비슷한 날, 같은 금액으로 3달 연속 나간 항목을 찾았어요. '고정지출 맞아요'를 누르면 아래 확정 합계에 바로 반영돼요. 다르면 수정하거나 '고정지출 아니에요'를 눌러 주세요.</p>
        : <p className="mc-muted">{data.items.length ? '고정지출을 모두 확인했어요. 빠진 항목이 있으면 직접 추가해 주세요.' : '자동으로 찾은 고정지출이 없어요. 월세 · 통신비처럼 매달 나가는 돈이 있으면 직접 추가하고, 없으면 계속하기를 눌러 주세요.'}</p>}
      <p className="mc-note" style={{ paddingTop: 0 }}>대출 상환은 대출 확인에서, 내 계좌로 보내는 저축은 다음 화면에서 따로 챙겨요.</p>

      {data.items.map((f) => (
        <div className="mc-card" key={f.recurringId}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{f.name}</strong><CatTag f={f} /><span className="mc-muted" style={{ display: 'block', marginTop: 4 }}>{dayLabel(f, '출금일 확인 필요')} · {payLabel(f)}</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(f.amount)}</strong>
          </div>
          {/* 배지 왼쪽 · [수정] 오른쪽 — 저축 확인 화면과 같은 모양 */}
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">{f.status === 'suggested' ? '3달 연속 감지' : f.sourceType === 'manual' ? '직접 추가' : '확인함'}</span>
            <button type="button" className="mc-link" disabled={busy} onClick={() => openPopup(<FixedPopup item={f} onSaved={reload} />)}>수정</button>
          </div>
          {f.evidenceSuggestion && <Suggestion f={f} onSaved={reload} />}
          {/* 고정지출 아니에요 = 이 후보를 무시(ignored). 같은 후보를 다시 묻지 않는다. 확정된 건 후보가 아니라 없음 */}
          {f.status === 'suggested' && (
            <div className="mc-inline">
              <button type="button" className="mc-primary" disabled={busy} onClick={() => confirm(f)}>고정지출 맞아요</button>
              <button type="button" className="mc-secondary" disabled={busy} onClick={() => ignore(f)}>고정지출 아니에요</button>
            </div>
          )}
        </div>
      ))}
      <button type="button" className="mc-secondary" disabled={busy} onClick={() => openPopup(<FixedPopup onSaved={reload} />)}>+ 고정지출 직접 추가</button>

      <div className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue" aria-live="polite"><span>확정한 고정지출 합계</span><strong>{won(data.summary.confirmedMonthlyTotal)}</strong></div>
        {suggested.length > 0 && <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>아직 확인하지 않은 {suggested.length}건은 합계에서 제외했어요. '확인하고 다음'을 누르면 남은 항목을 모두 확정해요.</p>}
      </div>
      {actionError && <p className="mc-error" role="alert">{actionError}</p>}
      <div className="mc-card">
        <div className="mc-row"><h2>카드 청구서</h2><button type="button" className="mc-link" onClick={() => navigate(PATHS.connect)}>카드 관리</button></div>
        {(data.cardBills ?? []).length === 0
          ? <p className="mc-muted">관리할 카드를 연결하고 선택하면 청구금액을 확인할 수 있어요.</p>
          : data.cardBills.map((bill) => <CardBillRow key={bill.issuerCode} bill={bill} onSaved={reload} />)}
      </div>
    </Layout>
  )
}

export default function FixedPage() {
  const location = useLocation()
  const from = location.state?.from
  return from === 'my' || from === 'home' ? <ManageView /> : <OnboardingView />
}
