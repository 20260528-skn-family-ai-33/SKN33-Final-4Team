// 고정지출 확인 · 고정지출 관리 — 한 화면을 두 곳에서 쓴다 (docs/api/fixed.md)
//   첫 설정 흐름: 월급 다음, 저축 확인 전. 자동으로 찾은 후보(suggested)를 확인한다 (아래 OnboardingView)
//   마이 > 고정지출 · 홈 '꼭 나갈 돈' (location.state.from === 'my' | 'home'): 확정된 고정지출 + [수정] · 중지 · 미납 [고르기] (ManageView)
// 목록 = GET /api/budgets/fixed/. [확인하고 다음] = 남은 후보를 모두 확정(confirm), [고정지출 아니에요] = 무시(ignore)
// 감지 조건 (요구사항 TXN-008): 같은 받는 곳으로 한 달에 1건 · 매달 같은 날 ±3일 · 같은 금액(±10%) · 3달 연속 — 자동 감지는 아직 없어 후보가 없으면 직접 추가만 보여 준다
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
import { won } from '../utils/money.js'
import { apiSend, newRequestKey, useApi } from '../utils/api.js'
import { pickEvidence, shortDate } from '../utils/fixed.js'
import { PATHS } from '../routes.js'

// 저장 뒤 같은 목록을 다시 받는다 — useApi 는 주소가 바뀔 때만 다시 받아서 서버가 무시하는 ?v= 를 바꾼다
function useFixedList() {
  const [version, setVersion] = useState(0)
  const { data, error } = useApi(`/api/budgets/fixed/?v=${version}`)
  return { data, error, reload: () => setVersion((v) => v + 1) }
}

// 목록 한 줄에 보일 결제 수단. 연결 해제면 다시 고르게 안내
function payLabel(f) {
  const method = f.paymentAccount ?? f.paymentCard
  if (!method) return '출금 계좌 확인 필요'
  return method.status === 'disconnected' ? `${method.displayName} (연결 해제됨 · 다시 고르기)` : method.displayName
}

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
            <span><strong style={{ fontSize: 15 }}>{f.name}</strong><br /><span className="mc-muted">{dayLabel(f, '출금일 모름')} · {payLabel(f)} · {f.categoryName}</span></span>
            <strong style={{ whiteSpace: 'nowrap' }}>{won(f.amount)}</strong>
          </div>
          <div className="mc-row" style={{ marginTop: 8 }}>
            <span className="mc-badge">{f.sourceType === 'manual' ? '직접 추가' : '자동으로 찾음'}</span>
            <button type="button" className="mc-link" onClick={() => openPopup(<FixedPopup item={f} onSaved={reload} />)}>{f.paymentDay ? '수정' : '출금일 정하기'}</button>
          </div>
          {lateDue(f) && <LateDue f={f} due={lateDue(f)} onSaved={reload} />}
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

// 첫 설정 흐름 · 고정지출 확인
function OnboardingView() {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const { data, error, reload } = useFixedList()
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')

  // 후보 확정 · 무시. 누를 때 요청 번호를 새로 만든다 (버튼을 잠가서 응답 전 재전송은 없다)
  const decide = async (path, ids) => {
    setBusy(true)
    setActionError('')
    try {
      await apiSend(path, 'POST', { recurringIds: ids }, { requestKey: newRequestKey() })
      return true
    } catch (err) {
      setActionError(err.fields?.recurringIds?.[0] ?? err.message)
      return false
    } finally {
      setBusy(false)
    }
  }

  if (!data) return <Layout title="고정지출 확인" showBack><StepBar step={4} /><ApiStatus error={error} /></Layout>
  const suggested = data.items.filter((f) => f.status === 'suggested')

  const onNext = async () => {
    if (busy) return
    if (suggested.length && !(await decide('/api/budgets/fixed/confirm/', suggested.map((f) => f.recurringId)))) return
    navigate(PATHS.savings)
  }
  const ignore = async (f) => { if (!busy && await decide('/api/budgets/fixed/ignore/', [f.recurringId])) reload() }

  const foot = <button type="button" className="mc-primary" disabled={busy} onClick={onNext}>{busy ? '저장 중…' : suggested.length ? '확인하고 다음' : '계속하기'}</button>
  return (
    <Layout title="고정지출 확인" showBack foot={foot}>
      <StepBar step={4} />
      <div className="mc-eyebrow">고정지출 확인</div>
      <h1>매달 이 돈이 나가나요?</h1>
      {suggested.length > 0
        ? <p className="mc-muted">매달 비슷한 날, 같은 금액으로 3달 연속 나간 항목을 찾았어요. 맞으면 그대로, 다르면 고치거나 '고정지출 아니에요'를 눌러 주세요.</p>
        : <p className="mc-muted">자동으로 찾은 고정지출이 없어요. 월세 · 통신비처럼 매달 나가는 돈이 있으면 직접 추가하고, 없으면 계속하기를 눌러 주세요.</p>}
      <p className="mc-note" style={{ paddingTop: 0 }}>대출 상환은 대출 확인에서, 내 계좌로 보내는 저축은 다음 화면에서 따로 챙겨요.</p>

      {data.items.map((f) => (
        <div className="mc-card" key={f.recurringId}>
          <div className="mc-row">
            <span><strong style={{ fontSize: 15 }}>{f.name}</strong><br /><span className="mc-muted">{dayLabel(f, '출금일 확인 필요')} · {payLabel(f)} · {f.categoryName}</span></span>
            <strong>{won(f.amount)}</strong>
          </div>
          <span className="mc-badge" style={{ marginTop: 8 }}>{f.status === 'suggested' ? '3달 연속 감지' : f.sourceType === 'manual' ? '직접 추가' : '확인함'}</span>
          {f.evidenceSuggestion && <Suggestion f={f} onSaved={reload} />}
          <div className="mc-inline">
            <button type="button" className="mc-secondary" onClick={() => openPopup(<FixedPopup item={f} onSaved={reload} />)}>수정</button>
            {/* 고정지출 아니에요 = 이 후보를 무시(ignored). 같은 후보를 다시 묻지 않는다. 확정된 건 후보가 아니라 없음 */}
            {f.status === 'suggested' && <button type="button" className="mc-secondary" disabled={busy} onClick={() => ignore(f)}>고정지출 아니에요</button>}
          </div>
        </div>
      ))}
      <button type="button" className="mc-secondary" onClick={() => openPopup(<FixedPopup onSaved={reload} />)}>+ 고정지출 직접 추가</button>

      <div className="mc-card" style={{ marginTop: 14 }}>
        <div className="mc-keyvalue"><span>확정한 고정지출 합계</span><strong>{won(data.summary.confirmedMonthlyTotal)}</strong></div>
        {suggested.length > 0 && <p className="mc-muted" style={{ margin: '4px 0 0', fontSize: 12 }}>찾은 후보 {suggested.length}건은 확인하고 다음을 누르면 더해져요.</p>}
      </div>
      {actionError && <p className="mc-error" role="alert">{actionError}</p>}
      <div className="mc-card">
        <div className="mc-row"><h2>카드 청구서</h2><button type="button" className="mc-link" onClick={() => navigate(PATHS.connect)}>카드 관리</button></div>
        <p className="mc-note" style={{ paddingBottom: 0 }}>카드값은 청구서 금액 기준으로 출금예정일에 챙겨요. 카드로 내는 고정지출(구독료 등)은 이 안에 들어 있어서 따로 빼지 않아요.</p>
      </div>
    </Layout>
  )
}

export default function FixedPage() {
  const location = useLocation()
  const from = location.state?.from
  return from === 'my' || from === 'home' ? <ManageView /> : <OnboardingView />
}
