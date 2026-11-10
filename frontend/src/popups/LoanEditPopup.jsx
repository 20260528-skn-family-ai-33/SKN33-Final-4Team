// 팝업: 직접 등록 대출 정보 고치기 (docs/api/loans.md 13번, R19)
//   PATCH /api/finance/loans/<id>/ — 바꾼 칸만 보낸다. 기관명 · 상품명 · 잔액은 비울 수 없고, 금리 · 만기일은 비우면 null
//   잔액 0원으로 고쳐도 상환 종료가 아니다 → [대출 전체 완납 처리]는 따로
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { PATHS } from '../routes.js'
import Popup from './Popup.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import { usePopup } from './context.js'
import { apiSend } from '../utils/api.js'

const formOf = (loan) => ({
  lenderName: loan.lenderName ?? '', productName: loan.productName ?? '',
  manualBalance: String(loan.manualBalance ?? ''), manualRate: loan.manualRate == null ? '' : String(loan.manualRate),
  maturityDate: loan.maturityDate ?? '',
})

export default function LoanEditPopup({ loan, onSaved }) {
  const { closePopup } = usePopup()
  const navigate = useNavigate()
  const original = formOf(loan)
  const [form, setForm] = useState(original)
  const [errors, setErrors] = useState({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const pending = useRef(false)
  const set = (key, value) => setForm((old) => ({ ...old, [key]: value }))

  const onSubmit = async (e) => {
    e.preventDefault()
    if (pending.current) return
    setError('')
    const next = {}
    const lender = form.lenderName.trim()
    const product = form.productName.trim()
    const balance = Number(form.manualBalance)
    if (!lender || lender.length > 50) next.lenderName = '기관 이름을 1~50자로 적어 주세요.'
    if (!product || product.length > 50) next.productName = '대출 이름을 1~50자로 적어 주세요.'
    if (form.manualBalance === '' || !Number.isInteger(balance) || balance < 0 || balance > 1_000_000_000) next.manualBalance = '남은 금액을 0원 ~ 10억 원으로 적어 주세요.'
    const rate = form.manualRate.trim() === '' ? null : Number(form.manualRate)
    if (rate !== null && (Number.isNaN(rate) || rate < 0 || rate > 30)) next.manualRate = '금리는 0 ~ 30% 사이로 적어 주세요.'
    setErrors(next)
    if (Object.keys(next).length > 0) return
    const values = { lenderName: lender, productName: product, manualBalance: balance, manualRate: rate, maturityDate: form.maturityDate || null }
    const body = Object.fromEntries(Object.entries(values).filter(([key]) => form[key] !== original[key]))
    if (Object.keys(body).length === 0) { closePopup(); return }
    pending.current = true
    setSaving(true)
    try {
      const saved = await apiSend(`/api/finance/loans/${loan.loanId}/`, 'PATCH', body)
      onSaved?.(saved)
      closePopup()
    } catch (err) {
      if (err.code === 'not_authenticated') navigate(PATHS.login, { replace: true })
      setErrors(Object.fromEntries(Object.entries(err.fields ?? {}).map(([key, messages]) => [key, [].concat(messages).join(' ')])))
      setError(err.status === 404 ? '이미 상환 종료했거나 찾을 수 없는 대출이에요. 목록을 새로 불러와 주세요.'
        : err.status ? err.message : '서버에 연결하지 못했어요. 입력한 내용은 유지돼요. 다시 시도해 주세요.')
    } finally {
      pending.current = false
      setSaving(false)
    }
  }
  const fieldError = (key) => errors[key] && <p className="mc-field-error" role="alert">{errors[key]}</p>

  return (
    <Popup label="대출 정보 고치기" sub>
      <div className="mc-eyebrow">직접 등록 대출</div>
      <h1>대출 정보 고치기</h1>
      <p className="mc-muted">잘못 적은 정보를 바로잡아요. 지난 납부 기록과 연결한 출금은 그대로 남아요.</p>
      <form className="mc-form" onSubmit={onSubmit} noValidate>
        <fieldset className="mc-form" disabled={saving} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <label className="mc-field">기관 이름
            <input value={form.lenderName} onChange={(e) => set('lenderName', e.target.value)} maxLength={50} aria-invalid={!!errors.lenderName} />
          </label>
          {fieldError('lenderName')}
          <label className="mc-field">대출 이름
            <input value={form.productName} onChange={(e) => set('productName', e.target.value)} maxLength={50} aria-invalid={!!errors.productName} />
          </label>
          {fieldError('productName')}
          <label className="mc-field">남은 금액
            <MoneyInput value={form.manualBalance} onChange={(value) => set('manualBalance', value)} aria-invalid={!!errors.manualBalance} />
          </label>
          {fieldError('manualBalance')}
          <div className="mc-loan-2">
            <label className="mc-field">연 이율 (%)
              <input value={form.manualRate} onChange={(e) => set('manualRate', e.target.value)} inputMode="decimal" placeholder="모르면 비워 두세요" aria-invalid={!!errors.manualRate} />
            </label>
            <label className="mc-field">만기일
              <input type="date" value={form.maturityDate} onChange={(e) => set('maturityDate', e.target.value)} aria-invalid={!!errors.maturityDate} />
            </label>
          </div>
          {fieldError('manualRate')}{fieldError('maturityDate')}{fieldError('nonFieldErrors')}
          <p className="mc-note" style={{ padding: 0 }}>남은 금액을 0원으로 고쳐도 상환이 끝난 것으로 바뀌지 않아요. 다 갚았다면 [대출 전체 완납 처리]를 눌러 주세요.</p>
          {error && <p className="mc-error" role="alert">{error}</p>}
          <div className="mc-foot"><button className="mc-primary" type="submit">{saving ? '저장 중…' : '고친 정보 저장'}</button></div>
        </fieldset>
      </form>
    </Popup>
  )
}
