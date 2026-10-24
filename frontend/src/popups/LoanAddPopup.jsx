// 팝업: 대출 직접 등록 — 대출 확인의 [+ 조회되지 않은 대출 직접 등록] (docs/api/loans.md 11번, 결정 P · Y)
// 학자금 · 캐피탈처럼 은행 연결로 안 잡히는 대출을 직접 넣는다
//   POST /api/finance/loans/  — 요청 번호(Idempotency-Key)는 팝업을 열 때 만든다
//   필수: lenderName(1~50자) · productName(1~50자) · manualBalance(1원 ~ 10억)
//   선택(비우면 '모름'): manualRate(0~30%) · maturityDate · monthlyPayment(1원 ~ 1억) · paymentDay(1~31) · repaymentMethod
//   월 상환액 · 납부일이 둘 다 있으면 서버가 납부 예정을 만들어 홈 '꼭 나갈 돈'에 나온다
import { useState } from 'react'
import Popup from './Popup.jsx'
import MoneyInput from '../components/MoneyInput.jsx'
import { usePopup } from './context.js'
import { MONTH_DAYS } from '../utils/days.js'
import { newRequestKey } from '../utils/api.js'

// 상환 방식 코드 (결정 P) — 화면 이름
const METHODS = [['equal_payment', '원리금균등'], ['equal_principal', '원금균등'], ['bullet', '만기일시'], ['unknown', '잘 모르겠어요']]
const MAX_BALANCE = 1_000_000_000
const MAX_MONTHLY = 100_000_000

export default function LoanAddPopup() {
  const { closePopup } = usePopup()
  const [method, setMethod] = useState(null)   // null = 고르지 않음(모름)
  const [errors, setErrors] = useState({})
  const [requestKey] = useState(newRequestKey)  // 이 팝업의 요청 번호 (다시 눌러도 같은 번호)
  const [saving, setSaving] = useState(false)

  const onSubmit = (e) => {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const lender = String(f.get('lnLender') ?? '').trim()
    const product = String(f.get('lnProduct') ?? '').trim()
    const balance = Number(f.get('lnBalance') || 0)
    const rateText = String(f.get('lnRate') ?? '').trim()
    const monthly = Number(f.get('lnMonthly') || 0)
    const next = {}
    if (!lender) next.lender = '기관 이름을 적어 주세요.'
    else if (lender.length > 50) next.lender = '50자까지 적을 수 있어요.'
    if (!product) next.product = '대출 이름을 적어 주세요.'
    else if (product.length > 50) next.product = '50자까지 적을 수 있어요.'
    if (balance < 1) next.balance = '남은 금액을 적어 주세요.'
    else if (balance > MAX_BALANCE) next.balance = '10억 원까지 적을 수 있어요.'
    if (rateText) {
      const rate = Number(rateText)
      if (Number.isNaN(rate) || rate < 0 || rate > 30) next.rate = '금리는 0 ~ 30% 사이로 적어 주세요.'
    }
    if (monthly > MAX_MONTHLY) next.monthly = '월 상환액은 1억 원까지 적을 수 있어요.'
    setErrors(next)
    if (Object.keys(next).length > 0 || saving) return
    setSaving(true)
    // 저장은 API 연결 후:
    // await apiSend('/api/finance/loans/', 'POST', { lenderName, productName, manualBalance, manualRate, maturityDate,
    //   monthlyPayment, paymentDay, repaymentMethod: method }, { requestKey })  → 201 대출 한 건
    void requestKey
    closePopup()
  }

  return (
    <Popup label="대출 직접 등록" sub>
      <div className="mc-eyebrow">대출 직접 등록</div>
      <h1>조회되지 않은 대출 추가</h1>
      <p className="mc-muted">학자금대출처럼 은행 연결로 불러오지 못한 대출을 적어 주세요. 모르는 칸은 비워 둬도 돼요.</p>
      <form className="mc-form" id="mc-pop-form" onSubmit={onSubmit} noValidate>
        <label className="mc-field">기관 이름 <span className="mc-hint">필수</span>
          <input name="lnLender" placeholder="예: 한국장학재단" type="text" maxLength={50} aria-invalid={!!errors.lender} />
        </label>
        {errors.lender && <p className="mc-field-error">{errors.lender}</p>}
        <label className="mc-field">대출 이름 <span className="mc-hint">필수</span>
          <input name="lnProduct" placeholder="예: 학자금대출" type="text" maxLength={50} aria-invalid={!!errors.product} />
        </label>
        {errors.product && <p className="mc-field-error">{errors.product}</p>}
        <label className="mc-field">남은 금액 <span className="mc-hint">필수</span>
          <MoneyInput name="lnBalance" placeholder="예: 3,150,000" />
        </label>
        {errors.balance && <p className="mc-field-error">{errors.balance}</p>}

        <div className="mc-loan-2">
          <label className="mc-field">연 이율 (%)
            <input name="lnRate" placeholder="예: 1.7" type="text" inputMode="decimal" aria-invalid={!!errors.rate} />
          </label>
          <label className="mc-field">만기일
            <input name="lnMaturity" type="date" />
          </label>
        </div>
        {errors.rate && <p className="mc-field-error">{errors.rate}</p>}

        <h3 className="mc-loan-h">앞으로 낼 돈</h3>
        <div className="mc-field">상환 방식
          <div className="mc-chipbar" style={{ marginTop: 6 }}>
            {METHODS.map(([code, label]) => (
              <button key={code} type="button" aria-pressed={method === code} onClick={() => setMethod(method === code ? null : code)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="mc-loan-2">
          <label className="mc-field">월 상환액
            <MoneyInput name="lnMonthly" placeholder="금액 입력" />
          </label>
          <label className="mc-field">납부일
            <select name="lnDay" defaultValue="">
              <option value="">모름</option>
              {MONTH_DAYS.map((d) => <option key={d} value={String(d)}>매월 {d}일</option>)}
            </select>
          </label>
        </div>
        {errors.monthly && <p className="mc-field-error">{errors.monthly}</p>}
        <p className="mc-note" style={{ padding: 0 }}>월 상환액과 납부일을 둘 다 적으면 홈 '꼭 나갈 돈'에 나와요. 잔액 · 금리는 등록한 뒤에 고칠 수 없어요.</p>
        <div className="mc-foot"><button className="mc-primary" form="mc-pop-form" type="submit" disabled={saving}>{saving ? '등록 중…' : '등록하기'}</button></div>
      </form>
    </Popup>
  )
}
