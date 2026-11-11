// 대출 조회·상환 정보 보완. 각 대출을 저장한 뒤 다음 단계로 이동한다.
import { useRef, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import { MONTH_DAYS } from '../utils/days.js'
import { won } from '../utils/money.js'
import { apiSend, useApi } from '../utils/api.js'
import { toast } from '../utils/toast.js'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import LoanAddPopup from '../popups/LoanAddPopup.jsx'
import Icon from '../components/Icon.jsx'
import LoanDueRow from '../components/LoanDueRow.jsx'
import LoanPaymentPopup from '../popups/LoanPaymentPopup.jsx'
import LoanEditPopup from '../popups/LoanEditPopup.jsx'
import LoanPayoffPopup from '../popups/LoanPayoffPopup.jsx'

const METHODS = [['equal_payment', '원리금균등'], ['equal_principal', '원금균등'], ['bullet', '만기일시'], ['unknown', '잘 모르겠어요']]
const monthDay = (iso) => { const d = new Date(iso); return `${d.getMonth() + 1}월 ${d.getDate()}일` }
const money = (value) => value == null ? '확인 필요' : won(value)
const SOURCE = { provider: '기관 조회', manual: '직접 입력', estimated: '서버 추정' }
const when = (iso) => iso ? new Date(iso).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '조회 기록 없음'
const draftOf = (loan) => ({ monthlyPayment: String(loan.monthlyPayment ?? ''), paymentDay: String(loan.paymentDay ?? ''), repaymentMethod: loan.repaymentMethod ?? '' })

function LoanCard({ loan, onEditing, onPaymentSaved, onPaidOff }) {
  const navigate = useNavigate()
  const { openPopup } = usePopup()
  const [saved, setSaved] = useState(loan)
  const [draft, setDraft] = useState(() => draftOf(loan))
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const [error, setError] = useState('')
  const [fields, setFields] = useState({})
  const change = (field, value) => {
    const next = { ...draft, [field]: value }
    setDraft(next)
    const original = draftOf(saved)
    onEditing(loan.loanId, Object.keys(next).some((key) => next[key] !== original[key]))
  }
  const save = async (e) => {
    e.preventDefault()
    if (pending.current) return
    setError('')
    setFields({})
    const amount = draft.monthlyPayment === '' ? null : Number(draft.monthlyPayment)
    if (amount !== null && (!Number.isInteger(amount) || amount < 1 || amount > 100_000_000)) {
      setFields({ monthlyPayment: ['월 상환액은 1원부터 1억 원까지 입력해 주세요.'] })
      return
    }
    pending.current = true
    setBusy(true)
    onEditing(loan.loanId, true)
    try {
      const result = await apiSend(`/api/finance/loans/${loan.loanId}/`, 'PATCH', {
        monthlyPayment: amount,
        paymentDay: draft.paymentDay === '' ? null : Number(draft.paymentDay),
        repaymentMethod: draft.repaymentMethod || null,
      })
      setSaved(result)
      setDraft(draftOf(result))
      toast('상환 정보를 저장했어요')
      onEditing(loan.loanId, false)
    } catch (err) {
      if (err.code === 'not_authenticated') navigate(PATHS.login, { replace: true })
      setFields(err.fields ?? {})
      setError(err.status ? err.message : '서버에 연결하지 못했어요. 입력한 내용은 유지돼요. 다시 시도해 주세요.')
    } finally {
      pending.current = false
      setBusy(false)
    }
  }
  const fieldError = (key) => fields[key] && <p className="mc-field-error" role="alert">{fields[key].join(' ')}</p>
  const stateNote = (key) => {
    const state = saved.fieldStates?.[key]
    if (!state?.source) return null
    return <small className="mc-loan-sub">저장된 값: {SOURCE[state.source] ?? '출처 확인 필요'} · {state.confirmationStatus === 'confirmed' ? '확인 완료' : '확인 필요'}{state.confirmedAt ? ` · ${when(state.confirmedAt)}` : ''}</small>
  }
  const manual = saved.origin === 'manual'
  return (
    <section className="mc-card mc-loan" aria-label={`${saved.productName ?? '대출'} 상환 정보`}>
      <div className="mc-row">
        <h2>{saved.productName ?? '대출'} <span className="mc-hint">{saved.lenderName ?? '기관 확인 필요'}</span></h2>
        <span className={`mc-loan-tag ${manual ? 'plan' : 'auto'}`}>{manual ? '직접 등록' : '기관 조회'}</span>
      </div>
      {saved.overdue === true && <p className="mc-loan-overdue">연체 중으로 조회됐어요. 기관에서 확인해 주세요.</p>}
      {saved.overdue === false && <p className="mc-loan-ok">연체 없음 · 조회됨</p>}
      <h3 className="mc-loan-h">대출 정보 <small>{when(saved.fetchedAt)}</small></h3>
      <div className="mc-loan-grid">
        <div><span>약정금액 · 기관 조회</span><strong>{money(saved.reportedPrincipal)}</strong></div>
        <div><span>잔액 · {manual ? '직접 입력' : '기관 조회'}</span><strong>{money(manual ? saved.manualBalance : saved.reportedBalance)}</strong></div>
        <div><span>금리 · {manual ? '직접 입력' : '기관 조회'}</span><strong>{(manual ? saved.manualRate : saved.reportedRate) == null ? '확인 필요' : `연 ${manual ? saved.manualRate : saved.reportedRate}%`}</strong></div>
        <div><span>만기일</span><strong>{saved.maturityDate ?? '확인 필요'}</strong></div>
      </div>
      {saved.paymentHistory?.length > 0 && <>
        <h3 className="mc-loan-h">지난 납부 내역</h3>
        <table className="mc-loan-table">
          <thead><tr><th>납부일</th><th>원금</th><th>이자</th></tr></thead>
          <tbody>{saved.paymentHistory.map((h, index) => <tr key={`${h.date}-${index}`}><td>{h.date}</td><td>{money(h.principal)}</td><td>{money(h.interest)}</td></tr>)}</tbody>
        </table>
      </>}
      {loan.recentDues?.length > 0 && <>
        <h3 className="mc-loan-h">이번 · 지난 주기 납부</h3>
        {/* 회차 한 장: 납부일 · 금액 / 상태 칩 · [납부 거래 확인 ›] — 줄 전체를 누르면 확인 팝업 (11/9: 한 줄에 다 넣어 줄바꿈이 깨져서) */}
        <div className="mc-card mc-loan-dues">{loan.recentDues.map((d) => (  /* 목록 새로고침 값 (입력 중인 상환 정보는 그대로 둠) */
          <LoanDueRow key={d.dueId} d={d} onOpen={() => openPopup(<LoanPaymentPopup key={d.dueId} dueId={d.dueId} onSaved={onPaymentSaved} />)} />))}
        </div>
      </>}
      <form onSubmit={save} noValidate>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <legend className="mc-loan-h">앞으로 낼 돈 · 직접 확인</legend>
          <label className="mc-field">상환 방식
            <select value={draft.repaymentMethod} onChange={(e) => change('repaymentMethod', e.target.value)}>
              <option value="">미입력</option>{METHODS.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
            </select>
            {stateNote('repaymentMethod')}
          </label>
          {fieldError('repaymentMethod')}
          <div className="mc-loan-2">
            <label className="mc-field">월 상환 예정액
              <MoneyInput value={draft.monthlyPayment} onChange={(value) => change('monthlyPayment', value)} aria-invalid={!!fields.monthlyPayment} placeholder="모르면 비워 두세요" />
              {stateNote('monthlyPayment')}
            </label>
            <label className="mc-field">매월 납부일
              <select value={draft.paymentDay} onChange={(e) => change('paymentDay', e.target.value)}>
                <option value="">모름</option>{MONTH_DAYS.map((day) => <option key={day} value={String(day)}>매월 {day}일</option>)}
              </select>
              {stateNote('paymentDay')}
            </label>
          </div>
          {fieldError('monthlyPayment')}{fieldError('paymentDay')}
          {saved.estimatedMonthlyPayment != null && <button type="button" className="mc-link" onClick={() => change('monthlyPayment', String(saved.estimatedMonthlyPayment))}>추정액 {won(saved.estimatedMonthlyPayment)} 쓰기</button>}
          {saved.reportedPaymentDate && <p className="mc-note">기관 제공 납부일: {saved.reportedPaymentDate}. 매월 납부일은 직접 확인해 주세요.</p>}
          <p className="mc-note">빈 칸으로 저장하면 해당 값을 비워요. 월 상환액과 납부일을 모두 저장하면 홈 ‘꼭 나갈 돈’에 반영돼요.</p>
          {error && <p className="mc-error" role="alert">{error}</p>}{fieldError('nonFieldErrors')}
          <button type="submit" className="mc-primary">{busy ? '저장 중…' : '상환 예정액 저장'}</button>
        </fieldset>
      </form>
      {manual && <div className="mc-keyvalue">
        <button type="button" className="mc-link" onClick={() => openPopup(<LoanEditPopup loan={saved} onSaved={setSaved} />)}>대출 정보 고치기</button>
        <button type="button" className="mc-link" onClick={() => openPopup(<LoanPayoffPopup loan={{ ...saved, recentDues: loan.recentDues }} onDone={onPaidOff}
          onCheckDue={(dueId) => openPopup(<LoanPaymentPopup key={dueId} dueId={dueId} onSaved={onPaymentSaved} />)} />)}>대출 전체 완납 처리</button>
      </div>}
    </section>
  )
}

export default function LoanCheckPage() {
  const navigate = useNavigate()
  const { state } = useLocation()
  const { openPopup } = usePopup()
  const { data, error, reload } = useApi('/api/finance/loans/')
  const repaid = useApi('/api/finance/loans/?status=repaid')  // 완납 되돌리기용 (직접 등록만)
  const [notice, setNotice] = useState('')
  const undoing = useRef(false)
  const reloadAll = () => { reload(); repaid.reload() }
  const undo = async (loan) => {
    if (undoing.current) return
    undoing.current = true
    try {
      await apiSend(`/api/finance/loans/${loan.loanId}/payoff/cancel/`, 'POST', { reportedAt: loan.payoff.reportedAt })
      setNotice(''); toast('완납을 취소했어요')
    } catch (err) {
      if (err.code === 'not_authenticated') navigate(PATHS.login, { replace: true })
      setNotice(err.status ? err.message : '서버에 연결하지 못했어요. 다시 시도해 주세요.')
    } finally {
      undoing.current = false
      reloadAll()
    }
  }
  const [editing, setEditing] = useState({})
  const manage = ['my', 'home', 'manage'].includes(state?.from)
  const unfinished = Object.values(editing).some(Boolean)
  const onEditing = (id, value) => setEditing((old) => ({ ...old, [id]: value }))
  if (error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  // 11/9: 관리 화면에서 왔으면 새로 쌓지 않고 온 곳으로 돌아간다 (확인 마침 → 연결 관리 → 뒤로 가 다시 대출 확인이 뜨던 문제)
  //   주소를 직접 열어 앱 안 이전 화면이 없으면 그 화면으로 바꿔 이동
  const done = () => {
    if (!manage) return navigate(PATHS.protect)
    if (window.history.state?.idx > 0) return navigate(-1)
    navigate(state?.from === 'home' ? PATHS.home : PATHS.connections, { replace: true })
  }
  const rows = [...(data?.items ?? [])].sort((a, b) => Number(b.loanId === state?.loanId) - Number(a.loanId === state?.loanId))
  return (
    <Layout title="대출 확인" showBack foot={<button type="button" className="mc-primary" disabled={unfinished} onClick={done}>{manage ? '확인 마침' : '다음'}</button>}>
      {!manage && <StepBar step={4} />}
      <h1>대출 확인</h1>
      <p className="mc-muted mc-loan-msg">대출별로 상환 정보를 확인하고 저장해 주세요.</p>
      {error ? <div className="mc-card">
        <p className="mc-error" role="alert">{error.code === 'consent_required' ? '기관 대출을 보려면 대출 조회 동의가 필요해요.' : error.status ? error.message : '대출 목록을 불러오지 못했어요.'}</p>
        {error.code === 'consent_required' ? <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connect)}>연결 화면에서 동의하기</button> : <button type="button" className="mc-secondary" onClick={reload}>다시 시도</button>}
      </div> : !data ? <p role="status">대출을 불러오는 중…</p> : <>
        {data.hasInquiryConsent === false && <div className="mc-card">
          <p className="mc-note">직접 등록한 대출은 조회 동의 없이 확인하고 수정할 수 있어요. 금융기관에서 대출을 불러오려면 대출 조회에 동의해 주세요.</p>
          <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connect)}>기관 대출 조회에 동의하기</button>
        </div>}
        {/* 11/9: '상환 종료한 대출'과 같은 카드. 없으면 카드 안에 안내, 있으면 제목 아래 대출 카드들 */}
        {rows.length === 0
          ? <section className="mc-card" aria-label="상환 중인 대출">
            <h2 className="mc-loan-h">상환 중인 대출</h2>
            <p className="mc-muted" style={{ margin: 0 }}>{data.hasInquiryConsent === false ? '직접 등록한 상환 중 대출이 없어요.' : '저장된 상환 중 대출이 없어요.'}</p>
          </section>
          : <h2 className="mc-loan-h">상환 중인 대출</h2>}
        {rows.map((loan) => <LoanCard key={loan.loanId} loan={loan} onEditing={onEditing} onPaymentSaved={reload}
          onPaidOff={() => { onEditing(loan.loanId, false); setNotice(''); reloadAll() }} />)}{/* 11/9: 완납하면 아래 '상환 종료한 대출'에 바로 보여서 따로 안내하지 않음 */}
      </>}
      {notice && <p className="mc-loan-notice" role="status">{notice}</p>}
      {/* 11/9: 한 줄 문장 → 목록 줄(상품명 / 기관 · 완납일) + 작은 [완납 취소] (연결 관리 기관 줄과 같은 버튼) */}
      {repaid.data?.items?.length > 0 && <section className="mc-card" aria-label="상환 종료한 직접 등록 대출">
        <h2 className="mc-loan-h">상환 종료한 대출</h2>
        {repaid.data.items.map((loan) => (
          <div className="mc-listrow mc-repaid-row" key={loan.loanId}>
            <span className="mc-tile"><Icon name="CircleCheck" /></span>
            <span className="mc-grow">
              <strong className="mc-tx-name"><span>{loan.productName ?? '대출'}</span></strong>
              <span className="mc-muted">{[loan.lenderName, loan.payoff && `${monthDay(loan.payoff.reportedAt)} 직접 완납`].filter(Boolean).join(' · ')}</span>
            </span>
            {loan.payoff && <button type="button" className="mc-conn-act" onClick={() => undo(loan)}>완납 취소</button>}
          </div>))}
      </section>}
      {/* 등록 뒤엔 위 목록에 새 대출 카드가 생기므로 따로 안내하지 않는다 (연결 관리와 같음) */}
      {(data || error?.code === 'consent_required') && <button type="button" className="mc-secondary" onClick={() => openPopup(<LoanAddPopup onSaved={() => reload()} />)}>+ 대출 직접 등록</button>}
      {unfinished && <p className="mc-note" role="status">수정한 대출의 상환 정보를 저장한 뒤 이동해 주세요.</p>}
    </Layout>
  )
}
