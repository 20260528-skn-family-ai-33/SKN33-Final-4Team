// 대출 조회·상환 정보 보완. 각 대출을 저장한 뒤 다음 단계로 이동한다.
import { useRef, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import StepBar from '../components/StepBar.jsx'
import { MONTH_DAYS } from '../utils/days.js'
import { won } from '../utils/money.js'
import { apiSend, useApi } from '../utils/api.js'
import { PATHS } from '../routes.js'
import { usePopup } from '../popups/context.js'
import LoanAddPopup from '../popups/LoanAddPopup.jsx'

const METHODS = [['equal_payment', '원리금균등'], ['equal_principal', '원금균등'], ['bullet', '만기일시'], ['unknown', '잘 모르겠어요']]
const money = (value) => value == null ? '확인 필요' : won(value)
const SOURCE = { provider: '기관 조회', manual: '직접 입력', estimated: '서버 추정' }
const when = (iso) => iso ? new Date(iso).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '조회 기록 없음'
const draftOf = (loan) => ({ monthlyPayment: String(loan.monthlyPayment ?? ''), paymentDay: String(loan.paymentDay ?? ''), repaymentMethod: loan.repaymentMethod ?? '' })

function LoanCard({ loan, onEditing }) {
  const navigate = useNavigate()
  const [saved, setSaved] = useState(loan)
  const [draft, setDraft] = useState(() => draftOf(loan))
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const [error, setError] = useState('')
  const [fields, setFields] = useState({})
  const [success, setSuccess] = useState(false)
  const change = (field, value) => {
    const next = { ...draft, [field]: value }
    setDraft(next)
    setSuccess(false)
    const original = draftOf(saved)
    onEditing(loan.loanId, Object.keys(next).some((key) => next[key] !== original[key]))
  }
  const save = async (e) => {
    e.preventDefault()
    if (pending.current) return
    setError('')
    setFields({})
    setSuccess(false)
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
      setSuccess(true)
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
      {success && <p className="mc-note" role="status">상환 정보를 저장했어요.</p>}
    </section>
  )
}

export default function LoanCheckPage() {
  const navigate = useNavigate()
  const { state } = useLocation()
  const { openPopup } = usePopup()
  const { data, error, reload } = useApi('/api/finance/loans/')
  const [editing, setEditing] = useState({})
  const [registered, setRegistered] = useState('')
  const manage = ['my', 'home', 'manage'].includes(state?.from)
  const unfinished = Object.values(editing).some(Boolean)
  const onEditing = (id, value) => setEditing((old) => ({ ...old, [id]: value }))
  if (error?.code === 'not_authenticated') return <Navigate to={PATHS.login} replace />
  const done = () => navigate(manage ? (state?.from === 'home' ? PATHS.home : PATHS.connections) : PATHS.salary)
  const rows = [...(data?.items ?? [])].sort((a, b) => Number(b.loanId === state?.loanId) - Number(a.loanId === state?.loanId))
  return (
    <Layout title="대출 확인" showBack foot={<button type="button" className="mc-primary" disabled={unfinished} onClick={done}>{manage ? '확인 마침' : '다음'}</button>}>
      {!manage && <StepBar step={2} />}
      <h1>상환 중인 대출</h1>
      <p className="mc-muted">대출별로 상환 정보를 확인하고 저장해 주세요.</p>
      {error ? <div className="mc-card">
        <p className="mc-error" role="alert">{error.code === 'consent_required' ? '대출 목록을 보려면 대출 조회 동의가 필요해요.' : error.status ? error.message : '대출 목록을 불러오지 못했어요.'}</p>
        {error.code === 'consent_required' ? <button type="button" className="mc-secondary" onClick={() => navigate(PATHS.connect)}>연결 화면에서 동의하기</button> : <button type="button" className="mc-secondary" onClick={reload}>다시 시도</button>}
      </div> : !data ? <p role="status">대출을 불러오는 중…</p> : <>
        {rows.length === 0 && <p className="mc-note">저장된 상환 중 대출이 없어요. 조회되지 않는 대출은 직접 등록할 수 있어요.</p>}
        {rows.map((loan) => <LoanCard key={loan.loanId} loan={loan} onEditing={onEditing} />)}
      </>}
      {(data || error?.code === 'consent_required') && <button type="button" className="mc-link" onClick={() => openPopup(<LoanAddPopup onSaved={(created) => { setRegistered(created.productName); reload() }} />)}>+ 조회되지 않은 대출 직접 등록</button>}
      {registered && <p className="mc-note" role="status">{registered} 대출을 등록했어요.{error?.code === 'consent_required' && ' 등록한 대출의 목록 조회는 대출 조회 동의 후 이용할 수 있어요.'}</p>}
      {unfinished && <p className="mc-note" role="status">수정한 대출의 상환 정보를 저장한 뒤 이동해 주세요.</p>}
    </Layout>
  )
}
